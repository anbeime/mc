"""省钱策略引擎 —— 「麦麦省钱雷达」的大脑。

四件事：
  1. **薅**：一键把麦麦省里能领的券全领了（auto-bind-coupons）
  2. **盘**：把账户里的券按到期时间排成红黄绿三档，快过期的顶到最前
  3. **算**：枚举「餐品 × 可用券」，给出这一单最省的 3 个组合
  4. **哨**：活动日历 + 积分机会，提前告诉你哪天值得来

设计上做了「在线试算 + 本地估算」双通道：
  - 有门店上下文时优先调 `calculate-price` 拿官方口径的实付价；
  - 拿不到时（离线 / 无 Token / 门店未指定）自动降级到本地券-单匹配算法，
    保证任何环境下都能给出一份可用的建议，而不是直接报错。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from .models import (
    Account,
    Campaign,
    Coupon,
    Meal,
    Store,
    extract_json,
    iter_payloads,
    parse_available_coupons_md,
    parse_campaign_calendar_md,
    parse_meals_payload,
    parse_my_coupons_md,
    pick,
    to_float,
    today_cn,
    unwrap_envelope,
)
from .tools import McDonaldTools

# 券的规模上限：避免把 600 次/分钟的限流打满
MAX_COUPON_SCAN = 120
MAX_COMBO_EVALUATIONS = 24


# ----------------------------------------------------------------- 数据容器
@dataclass
class Deal:
    """一个推荐组合。"""

    meals: List[Meal]
    coupon: Optional[Coupon]
    original: float          # 原价
    payable: float           # 应付
    saved: float             # 省下
    source: str = "local"    # local(本地估算) / mcp(官方试算)
    note: str = ""
    coupon_scope: str = ""   # 券的适用范围，提醒用户别用错场景

    @property
    def title(self) -> str:
        names = " + ".join(m.name for m in self.meals if m.name)
        return names or "自定义组合"

    @property
    def rate(self) -> float:
        return (self.saved / self.original) if self.original > 0 else 0.0


@dataclass
class CouponBoard:
    """券的盘点看板。"""

    expiring: List[Coupon] = field(default_factory=list)   # 3 天内到期
    valid: List[Coupon] = field(default_factory=list)      # 有效
    expired: List[Coupon] = field(default_factory=list)    # 已过期
    total_value: float = 0.0                               # 在效券面额合计
    at_risk_value: float = 0.0                             # 快过期券面额合计
    fixed_price_count: int = 0                             # 「用券价格」型券的张数

    @property
    def active_count(self) -> int:
        return len(self.valid) + len(self.expiring)


@dataclass
class Snapshot:
    """一次雷达扫描的全部产出。"""

    stores: List[Store] = field(default_factory=list)
    coupons: CouponBoard = field(default_factory=CouponBoard)
    campaigns: List[Campaign] = field(default_factory=list)
    ongoing_campaigns: List[Campaign] = field(default_factory=list)
    account: Optional[Account] = None
    deals: List[Deal] = field(default_factory=list)
    newly_bound: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    tools_used: List[str] = field(default_factory=list)


# ----------------------------------------------------------------- 本地试算
def estimate_best_coupon(amount: float, coupons: Sequence[Coupon],
                         *, allow_stack: bool = False,
                         max_stack: int = 2) -> Tuple[List[Coupon], float]:
    """给定订单金额，算出能用掉的券组合与省下的钱。

    默认一单一张券（符合麦当劳常规规则）；`allow_stack=True` 时做一个小规模
    的"面额组合搜索"，用于少数可叠加券的场景。
    """
    usable = [
        c for c in coupons
        if not c.expired and c.discount > 0 and amount >= c.threshold
    ]
    if not usable:
        return [], 0.0

    if not allow_stack:
        best = max(usable, key=lambda c: c.discount)
        return [best], min(best.discount, amount)

    # 小规模背包：券数量有限，直接枚举组合（2^n，n 已由调用方限制）
    usable.sort(key=lambda c: -c.discount)
    pool = usable[:8]
    best_combo: List[Coupon] = []
    best_saved = 0.0
    for mask in range(1, 1 << len(pool)):
        combo = [pool[i] for i in range(len(pool)) if mask & (1 << i)]
        if len(combo) > max_stack:
            continue
        saved = sum(c.discount for c in combo)
        saved = min(saved, amount)
        if saved > best_saved:
            best_saved, best_combo = saved, combo
    return best_combo, best_saved


def merge_official_coupons(base: List[Coupon], official: List[Coupon]) -> List[Coupon]:
    """把官方结构化券补进券包，按券名配对。

    两个接口各给一半信息，必须合起来才完整：
      - ``query-my-coupons``（Markdown）：到期日、适用时段、标签 —— 但没有券芯
      - ``query-store-coupons``（JSON）  ：真实 couponId / couponCode / products[]
    缺了券芯就调不动官方试算，所以这一步是「算最优」的前提。
    """
    by_name: Dict[str, Coupon] = {}
    for c in base:
        by_name.setdefault(c.name, c)
    for o in official:
        b = by_name.get(o.name)
        if b is None:
            continue
        if o.id:
            b.id = o.id
        if o.coupon_code:
            b.coupon_code = o.coupon_code
        if o.product_codes:
            b.product_codes = o.product_codes
        if o.expire and not b.expire:
            b.expire = o.expire
        if o.scope and not b.scope:
            b.scope = o.scope
    return base


# ----------------------------------------------------------------- 引擎
class SavingsRadar:
    """「麦麦省钱雷达」主引擎。"""

    def __init__(self, tools: McDonaldTools, *, allow_stack: bool = False,
                 urgent_days: int = 3):
        self.t = tools
        self.allow_stack = allow_stack
        self.urgent_days = urgent_days

    # ------------------------------------------------------------ 1. 薅券
    def bind_all_coupons(self) -> List[str]:
        """一键领取麦麦省所有可领券，返回领到的券名列表。"""
        before = {c.id for c in self._fetch_my_coupons()}
        try:
            self.t.auto_bind_coupons()
        except Exception:
            # 领券接口失败不应让整次扫描挂掉
            return []
        after = self._fetch_my_coupons()
        return [c.name for c in after if c.id not in before]

    # ------------------------------------------------------------ 拉取原语
    @staticmethod
    def _rows(raw: Any, *keys: str) -> List[Dict[str, Any]]:
        """把 MCP 的任意返回值规整成 dict 列表。

        抹平三件事：混合文本里的 JSON、``{success,code,data}`` 信封、
        以及「列表 / 包裹键 / 单条记录」的形态差异。
        """
        data = unwrap_envelope(raw)
        if data is None:
            data = extract_json(raw)
        return [d for d in iter_payloads(data, *keys) if isinstance(d, dict)]

    def _fetch_my_coupons(self) -> List[Coupon]:
        raw = self.t.query_my_coupons()
        # 真实接口在有券时回的是 Markdown，优先走专用解析器；
        # 空券包时回一句自然语言，解析器返回空列表，自然落到下面的兜底。
        coupons = parse_my_coupons_md(raw if isinstance(raw, str) else "")
        if coupons:
            return coupons[:MAX_COUPON_SCAN]
        return [Coupon.from_payload(d) for d in self._rows(raw)][:MAX_COUPON_SCAN]

    def _fetch_available_coupons(self, store_code: Optional[str] = None) -> List[Coupon]:
        """麦麦省还有哪些券没领。该接口只回 Markdown，优先走专用解析器。"""
        raw = self.t.available_coupons()
        coupons = parse_available_coupons_md(raw if isinstance(raw, str) else "")
        if coupons:
            return coupons[:MAX_COUPON_SCAN]
        return [Coupon.from_payload(d) for d in self._rows(raw)][:MAX_COUPON_SCAN]

    def _fetch_store_coupons(self, store_code: Optional[str]) -> List[Coupon]:
        if not store_code:
            return []
        raw = self.t.query_store_coupons(store_code)
        return [Coupon.from_payload(d) for d in self._rows(raw)][:MAX_COUPON_SCAN]

    def fetch_nearby_stores(self, address: Optional[str] = None, *,
                            city: Optional[str] = None) -> List[Store]:
        """查询附近门店。

        真实接口要求 ``city`` + ``keyword`` 才能按位置搜索（``searchType=2``）；
        只给一个词时把它同时当作关键词，城市留空由服务端兜。
        """
        if not address and not city:
            return []
        try:
            raw = self.t.query_nearby_stores(keyword=address, city=city)
        except Exception:
            return []
        return [Store.from_payload(d) for d in self._rows(raw)]

    def fetch_menu(self, store_code: Optional[str] = None) -> List[Meal]:
        if not store_code:
            return []
        try:
            raw = self.t.query_meals(store_code)
        except Exception:
            return []
        data = unwrap_envelope(raw)
        # 真实菜单是「categories + meals 映射表」两段式
        meals = parse_meals_payload(data) if isinstance(data, dict) else []
        if meals:
            return meals
        return [Meal.from_payload(d) for d in self._rows(raw)]

    def fetch_campaigns(self, month: Optional[str] = None) -> List[Campaign]:
        try:
            raw = self.t.campaign_calendar()
        except Exception:
            return []
        # 活动日历只回 Markdown，走专用解析器
        text = raw if isinstance(raw, str) else ""
        campaigns = parse_campaign_calendar_md(text)
        if campaigns:
            return campaigns
        return [Campaign.from_payload(d) for d in self._rows(raw)]

    def fetch_account(self) -> Optional[Account]:
        try:
            raw = self.t.query_my_account()
        except Exception:
            return None
        rows = self._rows(raw)
        if rows:
            return Account.from_payload(rows[0])
        data = unwrap_envelope(raw)
        if isinstance(data, dict) and data:
            return Account.from_payload(data)
        return None

    # ------------------------------------------------------------ 2. 盘券
    def board_coupons(self, coupons: Sequence[Coupon]) -> CouponBoard:
        board = CouponBoard()
        for c in coupons:
            if c.expired:
                board.expired.append(c)
            elif c.days_left is not None and 0 <= c.days_left <= self.urgent_days:
                board.expiring.append(c)
            else:
                board.valid.append(c)
        # 「用券价格」型券没有面额可言，真正值多少要看它能抵扣的商品原价，
        # 这里单独计数，避免在效面额被误读成 0 元。
        board.total_value = sum(c.discount for c in board.valid + board.expiring)
        board.at_risk_value = sum(c.discount for c in board.expiring)
        board.fixed_price_count = sum(
            1 for c in board.valid + board.expiring if c.is_fixed_price)
        board.expiring.sort(key=lambda c: (c.days_left if c.days_left is not None else 999, -c.discount))
        board.valid.sort(key=lambda c: (c.days_left if c.days_left is not None else 999, -c.discount))
        return board

    # ------------------------------------------------------------ 3. 算最优
    @staticmethod
    def _meal_of(menu: Sequence[Meal], code: str) -> Optional[Meal]:
        for m in menu:
            if m.code == code:
                return m
        return None

    def _official_price(self, store_code: str, coupon: Coupon,
                        product_code: str) -> Optional[Tuple[float, float, float]]:
        """问官方「这张券到底值多少钱」。

        返回 ``(原价, 实付, 优惠金额)``，单位元；失败返回 None。

        两个硬性前提（都是踩坑踩出来的）：
          - ``couponId`` 与 ``couponCode`` **必须成对传**，只传其一报 600010
          - 返回金额单位是**分**，要 /100
        """
        if not (coupon.id and coupon.coupon_code and product_code):
            return None
        try:
            raw = self.t.calculate_price(store_code, [{
                "productCode": product_code,
                "quantity": 1,
                "couponId": coupon.id,
                "couponCode": coupon.coupon_code,
            }])
        except Exception:
            return None
        d = unwrap_envelope(raw)
        if not isinstance(d, dict) or not d:
            return None
        payable = to_float(pick(d, "price")) / 100.0
        if payable <= 0:
            return None
        discount = to_float(pick(d, "discount")) / 100.0
        original = to_float(pick(d, "originalPrice", "productOriginalPrice")) / 100.0
        if original <= 0:
            # 券绑定的专属商品常不在常规菜单里，此时用「实付 + 优惠」反推原价
            original = round(payable + discount, 2)
        return original, payable, discount

    def _deals_from_official(self, coupons: Sequence[Coupon], menu: Sequence[Meal],
                             store_code: str, budget: Optional[float],
                             want: Optional[Sequence[str]]) -> List[Deal]:
        """官方路径：券自带适用商品，直接问官方这套组合值多少。"""
        deals: List[Deal] = []
        kw = [w.lower() for w in want] if want else []
        ordered = sorted(coupons, key=lambda c: (
            0 if any(w in c.name.lower() for w in kw) else 1,
            c.days_left if c.days_left is not None else 999,
        )) if kw else coupons

        for c in ordered:
            if len(deals) >= MAX_COMBO_EVALUATIONS:
                break
            if c.expired:
                continue
            for pc in c.product_codes[:2]:
                got = self._official_price(store_code, c, pc)
                if not got:
                    continue
                original, payable, _discount = got
                if budget is not None and payable > budget:
                    continue
                meal = self._meal_of(menu, pc)
                if meal is None:
                    meal = Meal(code=pc, name=c.name, price=original,
                                origin_price=original)
                deals.append(Deal(
                    meals=[meal],
                    coupon=c,
                    original=original,
                    payable=payable,
                    saved=round(original - payable, 2),
                    source="mcp",
                    coupon_scope=c.scope or "、".join(c.tags),
                ))
                break
        return deals

    def recommend_deals(
        self,
        coupons: Sequence[Coupon],
        menu: Sequence[Meal],
        *,
        store_code: Optional[str] = None,
        budget: Optional[float] = None,
        pick_count: int = 3,
        want: Optional[Sequence[str]] = None,
    ) -> List[Deal]:
        """给出这一单最省的几个组合。

        两条路径，官方优先、本地兜底：
          - **官方**：券自带 products[] 时，调 ``calculate-price`` 拿到真实实付
          - **本地**：拿不到官方口径时，用券-单匹配算法估算，保证仍有输出
        """
        # 官方路径能出结果就以它为准，不再叠加本地估算（避免同物两套价格
        if store_code:
            official = self._deals_from_official(coupons, menu, store_code, budget, want)
            if official:
                official.sort(key=lambda d: (-d.saved, d.payable))
                return official[:pick_count]

        deals = self._deals_from_local(coupons, menu, store_code=store_code,
                                       budget=budget, want=want)
        return deals[:pick_count]

    def _deals_from_local(self, coupons: Sequence[Coupon], menu: Sequence[Meal], *,
                          store_code: Optional[str] = None,
                          budget: Optional[float] = None,
                          want: Optional[Sequence[str]] = None) -> List[Deal]:
        """本地估算路径：枚举「单餐品 × 可用券」。"""
        candidates = [m for m in menu if m.price > 0]
        if want:
            kw = [w.lower() for w in want]
            hits = [m for m in candidates if any(w in m.name.lower() for w in kw)]
            candidates = (hits + [m for m in candidates if m not in hits])
        else:
            # 没有偏好时，取价格居中的一批，覆盖面更广
            candidates = sorted(candidates, key=lambda m: m.price)

        evaluations = 0
        deals: List[Deal] = []
        for meal in candidates:
            if evaluations >= MAX_COMBO_EVALUATIONS:
                break
            total = meal.price
            if budget is not None and total > budget * 1.15 and not want:
                continue
            combo, saved_local = estimate_best_coupon(
                total, coupons, allow_stack=self.allow_stack
            )
            if not combo:
                continue
            coupon = combo[0] if len(combo) == 1 else None
            payable = max(total - saved_local, 0.0)
            source = "local"

            if store_code:
                payable_mcp, ok = self._try_mcp_price(store_code, meal, combo, total)
                evaluations += 1
                # 官方试算若一个子儿都没减（通常是该券未参与试算 / 场景不匹配），
                # 不要用它覆盖本地已有结论，否则会把「省 10 元」推荐成「省 0 元」。
                if ok and not (saved_local > 0 and payable_mcp >= total - 0.01):
                    payable, source = payable_mcp, "mcp"

            if budget is not None and payable > budget:
                continue
            deals.append(
                Deal(
                    meals=[meal],
                    coupon=coupon,
                    original=total,
                    payable=payable,
                    saved=total - payable,
                    source=source,
                    note="、".join(c.name for c in combo) if len(combo) > 1 else "",
                    coupon_scope=(coupon.scope if coupon else ""),
                )
            )

        if want:
            # 指定了想吃什么时，命中的餐品排在前面，其次才按省钱多少排
            kw = [w.lower() for w in want]
            deals.sort(key=lambda d: (
                0 if any(w in d.meals[0].name.lower() for w in kw) else 1,
                -d.saved,
            ))
        else:
            deals.sort(key=lambda d: (-d.saved, d.payable))
        return deals

    def _try_mcp_price(self, store_code: str, meal: Meal,
                       combo: Sequence[Coupon], total: float) -> Tuple[float, bool]:
        """调 calculate-price 试算，失败则返回 (本地估价, False)。

        真实契约的三个坑都必须照顾到：
          1. 商品数组叫 ``items``，项内是 ``productCode``（不是 mealCode）
          2. 券挂在单个 item 上（``couponId``），不在顶层
          3. 返回的金额单位是**分**，要 /100 才是元
        """
        if not meal.code:
            return total, False
        item: Dict[str, Any] = {"productCode": meal.code, "quantity": 1}
        if combo and combo[0].id:
            item["couponId"] = combo[0].id
        try:
            raw = self.t.calculate_price(store_code, [item])
        except Exception:
            return total, False
        data = unwrap_envelope(raw)
        if not isinstance(data, dict) or not data:
            return total, False
        # 应付总价（单位：分）
        cents = to_float(pick(data, "price", "payPrice", "payablePrice", "totalPrice",
                              "payAmount", "payableAmount", "actualPrice"))
        if cents <= 0:
            return total, False
        return round(cents / 100.0, 2), True

    # ------------------------------------------------------------ 4. 雷达汇总
    def scan(
        self,
        *,
        address: Optional[str] = None,
        city: Optional[str] = None,
        store_code: Optional[str] = None,
        bind: bool = False,
        budget: Optional[float] = None,
        want: Optional[Sequence[str]] = None,
    ) -> Snapshot:
        """跑一次完整扫描，产出给报告用的全部数据。"""
        snap = Snapshot()

        if bind:
            snap.newly_bound = self.bind_all_coupons()

        coupons = self._fetch_my_coupons()
        if not coupons:
            # 账户里没券，退一步看看麦麦省还能领什么
            extra = self._fetch_available_coupons(store_code)
            if extra:
                snap.warnings.append(
                    f"账户里暂无可用的券，但麦麦省还有 {len(extra)} 张可领 —— 建议先跑一次 `--bind`。"
                )
        snap.coupons = self.board_coupons(coupons)
        snap.tools_used = ["query-my-coupons", "campaign-calendar", "query-my-account"]

        stores = self.fetch_nearby_stores(address, city=city)
        snap.stores = stores
        if stores:
            snap.tools_used.append("query-nearby-stores")
        elif address:
            snap.warnings.append(
                "没能定位到门店：官方接口需要同时给「城市」和「位置关键词」，"
                "可用 `--address 科技园 --city 深圳` 再试。"
            )

        active_store = store_code or (stores[0].code if stores else None)
        menu: List[Meal] = []
        merged_coupons: List[Coupon] = list(coupons)
        if active_store:
            menu = self.fetch_menu(active_store)
            if menu:
                snap.tools_used.append("query-meals")
            store_coupons = self._fetch_store_coupons(active_store)
            if store_coupons:
                snap.tools_used.append("query-store-coupons")
                # Markdown 券包只有到期信息，缺少真实 couponId；官方结构化券正好
                # 补上券芯（含适用商品），两者按券名合体后才有资格做官方试算。
                merged_coupons = merge_official_coupons(coupons, store_coupons)
                snap.coupons = self.board_coupons(merged_coupons)

        snap.campaigns = self.fetch_campaigns()
        if snap.campaigns:
            snap.tools_used.append("campaign-calendar")
        snap.ongoing_campaigns = [c for c in snap.campaigns if c.ongoing]

        snap.account = self.fetch_account()
        if snap.account:
            snap.tools_used.append("query-my-account")

        if menu:
            snap.deals = self.recommend_deals(
                merged_coupons,
                menu,
                store_code=active_store,
                budget=budget,
                want=want,
            )
        if snap.deals and any(d.source == "mcp" for d in snap.deals):
            snap.tools_used.append("calculate-price")

        if not snap.coupons.active_count:
            snap.warnings.append("当前账户没有可用券，省钱空间有限；可关注活动日历等待发券节点。")
        if snap.account and snap.account.expiring > 0:
            snap.warnings.append(
                f"有 {snap.account.expiring} 积分即将过期，别让它白白蒸发。"
            )
        return snap

    # ------------------------------------------------------------ 省钱账单
    @staticmethod
    def ledger(deals: Sequence[Deal]) -> Dict[str, float]:
        original = sum(d.original for d in deals)
        payable = sum(d.payable for d in deals)
        return {
            "original": round(original, 2),
            "payable": round(payable, 2),
            "saved": round(original - payable, 2),
        }
