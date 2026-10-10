"""数据模型与「模糊字段解析」层。

现实问题：麦当劳 MCP 各 Tool 的返回结构未在文档中给出 JSON Schema，
不同版本 / 不同场景的字段命名可能是 camelCase、snake_case 或中文别名。
本模块把所有可能出现的键名收敛成一组稳定字段，让上层策略只依赖稳定字段。

如果官方后续调整了字段名，**只需在这里补一个别名**，策略层无需改动。
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timezone, timedelta
from typing import Any, Dict, Iterable, List, Optional

# --------------------------------------------------------------------- 工具
_NORM_RE = re.compile(r"[^a-z0-9\u4e00-\u9fff]+")

# --------------------------------------------------- 真实 MCP 返回：混合文本
# 麦当劳 MCP 的实际返回不是干净的 JSON，而是「自然语言说明 + 贴一段 JSON」。
# 观测到的三种形态：
#   ① 纯 dict/list                                    —— 直接可用
#   ② "## Response Structure ... ## Original Response {json}"  —— 标记后嵌 JSON
#   ③ "每个门店的基本信息按单行展示：- **门店名称**: ... {\"success\":true,...}"
#      —— 说明文字后面直接贴裸 JSON，无任何围栏
#   ④ 纯 Markdown 数据（无 JSON）                       —— 活动日历 / 可领券列表
# 因此需要一个「从任意文本里把 JSON 抠出来」的通用能力。

_ORIGINAL_RESPONSE_RE = re.compile(r"##\s*Original Response\s*(.+)", re.S)


def _iter_json_objects(text: str):
    """括号平衡扫描，依次产出文本中所有能被解析的 JSON 对象 / 数组。"""
    if not isinstance(text, str) or not text:
        return
    for m in re.finditer(r"[\[{]", text):
        start = m.start()
        depth = 0
        in_str = False
        esc = False
        for i in range(start, len(text)):
            ch = text[i]
            if esc:
                esc = False
                continue
            if ch == "\\" and in_str:
                esc = True
                continue
            if ch == '"':
                in_str = not in_str
                continue
            if in_str:
                continue
            if ch in "{[":
                depth += 1
            elif ch in "}]":
                depth -= 1
                if depth == 0:
                    blob = text[start:i + 1]
                    try:
                        obj = json.loads(blob)
                    except Exception:
                        break
                    if isinstance(obj, (dict, list)):
                        yield obj
                    break


def _score_blob(obj: Any, length: int) -> int:
    """给候选 JSON 打分：像 API 响应信封（含 success/code/data）的最优先。"""
    if isinstance(obj, dict):
        score = 100000 + length
        keys = {norm_key(k) for k in obj}
        if "success" in keys or "code" in keys:
            score += 500000
        if "data" in keys:
            score += 300000
        return score
    if isinstance(obj, list):
        return 10000 + length
    return 0


def extract_json(value: Any) -> Any:
    """把 MCP 的任意返回值规整成 dict / list（提取不出时原样返回）。"""
    if isinstance(value, (dict, list)):
        return value
    if not isinstance(value, str):
        return None
    # 优先在 "## Original Response" 之后的区域里找
    m = _ORIGINAL_RESPONSE_RE.search(value)
    zone = m.group(1) if m else value
    cands = [(o, len(json.dumps(o, ensure_ascii=False))) for o in _iter_json_objects(zone)]
    if not cands and zone is not value:
        cands = [(o, len(json.dumps(o, ensure_ascii=False))) for o in _iter_json_objects(value)]
    if not cands:
        return None
    return max(cands, key=lambda kv: _score_blob(kv[0], kv[1]))[0]


def unwrap_envelope(value: Any) -> Any:
    """剥掉 ``{success, code, message, data}`` 信封，取出真正的业务数据。"""
    obj = extract_json(value)
    if isinstance(obj, dict):
        keys = {norm_key(k) for k in obj}
        if "data" in keys and ("success" in keys or "code" in keys):
            data = obj.get("data")
            return data if data is not None else []
    return obj


def norm_key(key: Any) -> str:
    return _NORM_RE.sub("", str(key).lower())


def pick(data: Any, *keys: str, default: Any = None) -> Any:
    """在 dict 里按候选键名取值，键名做归一化匹配（大小写/下划线/中划线不敏感）。"""
    if not isinstance(data, dict):
        return default
    wanted = [norm_key(k) for k in keys]
    # 先精确归一化匹配
    table = {norm_key(k): v for k, v in data.items()}
    for w in wanted:
        if w in table and table[w] is not None:
            return table[w]
    # 再做"包含"匹配（例如 couponName 里找 name）
    for w in wanted:
        for nk, v in table.items():
            if v is not None and w and (w in nk or nk in w):
                return v
    return default


def to_float(value: Any, default: float = 0.0) -> float:
    if value is None:
        return default
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value)
    m = re.search(r"-?\d+(?:\.\d+)?", s.replace(",", ""))
    return float(m.group()) if m else default


def to_int(value: Any, default: int = 0) -> int:
    return int(to_float(value, default))


def parse_date(value: Any) -> Optional[date]:
    """尽力把时间戳 / 字符串 / 毫秒时间戳解析成 date。"""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        ts = float(value)
        if ts > 1e12:  # 毫秒
            ts /= 1000.0
        try:
            return datetime.fromtimestamp(ts, tz=timezone(timedelta(hours=8))).date()
        except (OverflowError, OSError, ValueError):
            return None
    s = str(value).strip()
    if not s:
        return None
    if s.isdigit():
        return parse_date(int(s))
    s = s.replace("/", "-").replace(".", "-").replace("年", "-").replace("月", "-").replace("日", "")
    m = re.match(r"(\d{4})-(\d{1,2})-(\d{1,2})", s)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            return None
    return None


def today_cn() -> date:
    return datetime.now(tz=timezone(timedelta(hours=8))).date()


# ------------------------------------------------------------------- 模型
@dataclass
class Coupon:
    """一张优惠券。"""

    id: str = ""
    coupon_code: str = ""          # 真实券码（官方试算必须与 couponId 成对传入）
    name: str = "优惠券"
    product_codes: List[str] = field(default_factory=list)  # 官方给出的适用商品编码
    discount: float = 0.0          # 优惠金额（元），0 表示非立减型
    fixed_price: float = 0.0       # 「用券价格」型：直接以该价成交，而非减免若干元
    threshold: float = 0.0         # 使用门槛（满 X 元）
    expire: Optional[date] = None  # 到期日
    start: Optional[date] = None   # 生效日
    scope: str = ""                # 适用范围描述
    kind: str = ""                 # 券类型（折扣/立减/兑换/买赠…）
    status: str = ""
    tags: List[str] = field(default_factory=list)
    time_window: str = ""          # 适用时段，如「周一至周五 10:30-23:59」
    raw: Dict[str, Any] = field(default_factory=dict)

    @property
    def is_fixed_price(self) -> bool:
        """是否为「用券价格」型（消费门槛不是减免，而是按固定价成交）。"""
        return self.fixed_price > 0 and self.discount <= 0

    @property
    def days_left(self) -> Optional[int]:
        if not self.expire:
            return None
        return (self.expire - today_cn()).days

    @property
    def expired(self) -> bool:
        return self.days_left is not None and self.days_left < 0

    @property
    def urgent(self) -> bool:
        d = self.days_left
        return d is not None and 0 <= d <= 3

    @property
    def value_label(self) -> str:
        # 「用券价格」型不是减免，展示固定成交价才不会误导
        if self.is_fixed_price:
            return f"用券价¥{self.fixed_price:g}"
        if self.discount > 0 and self.threshold > 0:
            return f"满{self.threshold:g}减{self.discount:g}"
        if self.discount > 0:
            return f"立减{self.discount:g}元"
        return self.kind or "优惠"

    @property
    def expire_label(self) -> str:
        """到期文案（今天到期 / N 天后到期）。"""
        d = self.days_left
        if d is None:
            return ""
        if d < 0:
            return "已过期"
        if d == 0:
            return "今天到期"
        return f"{d} 天后到期"

    @classmethod
    def from_payload(cls, d: Dict[str, Any]) -> "Coupon":
        return cls(
            id=str(pick(d, "couponId", "couponCode", "code", "id", "券id", default="")),
            coupon_code=str(pick(d, "couponCode", "券码", default="")),
            name=str(pick(d, "couponName", "name", "title", "券名称", default="优惠券")),
            product_codes=cls._product_codes(d),
            discount=to_float(pick(d, "discountAmount", "discount", "amount", "value",
                                   "faceValue", "parValue", "couponValue")),
            threshold=to_float(pick(d, "threshold", "minAmount", "condition", "useCondition",
                                    "limitAmount", "satisfyAmount", "minConsume")),
            expire=parse_date(pick(d, "expireTime", "expiredTime", "validEndTime", "endTime",
                                   "expireDate", "endDate", "useEndTime", "validEnd",
                                   # 真实字段名（query-store-coupons 字段说明）
                                   "tradeDateTime", "validEndDate", "effectiveEndTime")),
            scope=cls._scope_of(d),
            kind=str(pick(d, "couponType", "type", "typeName", "券类型", default="")),
            status=str(pick(d, "status", "state", default="")),
            raw=d,
        )

    @staticmethod
    def _product_codes(d: Dict[str, Any]) -> List[str]:
        """适用商品编码（query-store-coupons 会明确给出 products[]）。"""
        return scope_product_codes(d)

    @staticmethod
    def _scope_of(d: Dict[str, Any]) -> str:
        """适用范围：优先用现成字段，否则从 products[] 里拼出商品名。"""
        scope = pick(d, "scope", "useScope", "适用", "scopeDesc", "productNames", default="")
        if scope:
            return str(scope)
        products = pick(d, "products", "mealList", "skuList", default=[])
        if isinstance(products, list):
            names = [
                str(p.get("productName") or p.get("name") or p.get("mealName") or "")
                for p in products if isinstance(p, dict)
            ]
            names = [n for n in names if n]
            if names:
                return "、".join(names[:4]) + ("…" if len(names) > 4 else "")
        return ""


@dataclass
class Meal:
    """一个可售餐品 / 套餐。"""

    code: str = ""
    name: str = ""
    price: float = 0.0
    origin_price: float = 0.0
    category: str = ""
    tags: List[str] = field(default_factory=list)
    raw: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_payload(cls, d: Dict[str, Any]) -> "Meal":
        tags = pick(d, "tags", "tagList", "labels", default=[]) or []
        if isinstance(tags, str):
            tags = [tags]
        return cls(
            code=str(pick(d, "mealCode", "productCode", "skuCode", "code", "id", default="")),
            name=str(pick(d, "mealName", "productName", "name", "title", default="")),
            price=to_float(pick(d, "price", "currentPrice", "salePrice", "sellPrice", "amount")),
            origin_price=to_float(pick(d, "originPrice", "originalPrice", "listPrice", "stdPrice")),
            category=str(pick(d, "category", "categoryName", "categoryCode", "typeName", default="")),
            tags=[str(t.get("name") if isinstance(t, dict) else t) for t in tags],
            raw=d,
        )


@dataclass
class Store:
    """一家餐厅。"""

    code: str = ""
    name: str = ""
    address: str = ""
    distance: Optional[float] = None
    raw: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_payload(cls, d: Dict[str, Any]) -> "Store":
        dist = pick(d, "distance", "distanceMeter", "distanceText")
        dist_val: Optional[float] = None
        if dist is not None:
            dist_val = to_float(dist, default=-1)
            if dist_val < 0:
                dist_val = None
            elif dist_val > 1000:  # 疑似米
                dist_val = round(dist_val / 1000.0, 2)
        return cls(
            code=str(pick(d, "storeCode", "storeId", "code", "id", default="")),
            name=str(pick(d, "storeName", "name", "title", default="")),
            address=str(pick(d, "address", "storeAddress", "addr", "addressDetail", default="")),
            distance=dist_val,
            raw=d,
        )


@dataclass
class Campaign:
    """一条营销活动。"""

    name: str = ""
    start: Optional[date] = None
    end: Optional[date] = None
    status: str = ""
    desc: str = ""
    raw: Dict[str, Any] = field(default_factory=dict)

    @property
    def ongoing(self) -> bool:
        t = today_cn()
        if self.start and self.end:
            return self.start <= t <= self.end
        return (self.status or "").lower() in {"ongoing", "进行中", "active", "1"}

    @classmethod
    def from_payload(cls, d: Dict[str, Any]) -> "Campaign":
        return cls(
            name=str(pick(d, "name", "title", "activityName", "campaignName", "subject", default="活动")),
            start=parse_date(pick(d, "startTime", "startDate", "beginTime", "beginDate")),
            end=parse_date(pick(d, "endTime", "endDate", "finishTime", "expireDate")),
            status=str(pick(d, "status", "state", "activityStatus", default="")),
            desc=str(pick(d, "description", "desc", "content", "summary", "intro", default="")),
            raw=d,
        )


@dataclass
class Account:
    """积分账户。"""

    available: int = 0
    total: int = 0
    frozen: int = 0
    expiring: int = 0
    raw: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_payload(cls, d: Dict[str, Any]) -> "Account":
        return cls(
            available=to_int(pick(d, "availablePoints", "available", "usablePoints", "balancePoints",
                                  "points", "currentPoints",
                                  # 真实字段名（query-my-account 字段说明）
                                  "availablePoint")),
            total=to_int(pick(d, "totalPoints", "total", "accumulatePoints", "accumulatedPoints",
                              "accumulativePoint")),
            frozen=to_int(pick(d, "frozenPoints", "frozen", "lockPoints", "frozenPoint")),
            expiring=to_int(pick(d, "expiringPoints", "expirePoints", "soonExpirePoints",
                                 "willExpirePoints", "aboutToExpirePoints",
                                 # 真实字段名：本月 / 下月将过期积分
                                 "currentMouthExpirePoint", "nextMouthExpirePoint")),
            raw=d,
        )


def scope_product_codes(d: Dict[str, Any]) -> List[str]:
    """从 products[] / skuList[] 里抽出商品编码。"""
    rows = pick(d, "products", "skuList", "mealList", "productList", default=[])
    if not isinstance(rows, list):
        return []
    codes: List[str] = []
    for p in rows:
        if isinstance(p, dict):
            c = pick(p, "productCode", "mealCode", "skuCode", "code")
            if c:
                codes.append(str(c))
        elif isinstance(p, (str, int)):
            codes.append(str(p))
    return codes


#: 常见的「数据被包在外层字段里」的键名，按优先级排列
_WRAPPER_KEYS = (
    "data", "result", "list", "items", "rows", "records", "content",
    "coupons", "couponList", "stores", "storeList", "meals", "products",
    "activities", "campaigns", "orders", "addresses", "cities", "sessions",
    "dates", "prizes", "points", "goods",
)


def iter_payloads(value: Any, *keys: str) -> Iterable[Dict[str, Any]]:
    """从 Tool 返回值里迭代出 dict 列表。

    兼容这些常见形态：
      [ {...}, {...} ]                      —— 直接就是数组
      { "coupons": [ ... ] }                —— 业务键包裹
      { "data": { "list": [ ... ] } }       —— 多层包裹
      { ... }                               —— 单条记录
    """
    if value is None:
        return
    if isinstance(value, list):
        for item in value:
            if isinstance(item, dict):
                yield item
        return
    if not isinstance(value, dict):
        return
    if not value:
        return

    # 1) 调用方显式指定的键
    for k in keys:
        v = value.get(k)
        if isinstance(v, list):
            for item in v:
                if isinstance(item, dict):
                    yield item
            return
        if isinstance(v, dict):
            yield from iter_payloads(v, *keys)
            return

    # 2) 通用包裹键
    for key in _WRAPPER_KEYS:
        v = value.get(key)
        if isinstance(v, list):
            for item in v:
                if isinstance(item, dict):
                    yield item
            return
        if isinstance(v, dict) and v:
            yield from iter_payloads(v, *keys)
            return

    # 3) 任意第一个 list 值
    for v in value.values():
        if isinstance(v, list):
            for item in v:
                if isinstance(item, dict):
                    yield item
            return

    # 4) 兜底：把整个对象当成单条记录
    yield value


# --------------------------------------------------------- 纯 Markdown 返回
# 少数 Tool（available-coupons / campaign-calendar / query-my-coupons）返回的是
# 给人看的 Markdown，里面一个 JSON 都没有，只能按文本结构解析。

_AVAIL_BLOCK_RE = re.compile(r"-\s*优惠券标题[:：]\s*(.+)")
_AVAIL_STATE_RE = re.compile(r"状态[:：]\s*(.+)")
_AVAIL_IMG_RE = re.compile(r'<img\s+src="([^"]+)"')
_AVAIL_TAG_RE = re.compile(r"促销标签[:：]\s*(.+)")

# 注意：分隔符必须用 [ \t] 而不是 \s —— \s 会吃掉换行，把下一行内容误当成日期后缀
_CAMP_DATE_RE = re.compile(r"^####[ \t]*(\d{4})年(\d{1,2})月(\d{1,2})日[ \t]*(.*)$", re.M)
_CAMP_TITLE_RE = re.compile(r"\*\*活动标题\*\*[:：]\s*(.+)")
_CAMP_DESC_RE = re.compile(r"\*\*活动内容介绍\*\*[:：]\s*(.+)")
_CAMP_IMG_RE = re.compile(r'<img\s+src="([^"]+)"')


def _clean_line(s: str) -> str:
    """去掉行尾的 Markdown 续行反斜杠与多余空白。"""
    return s.replace("\\", "").strip()


def parse_available_coupons_md(text: str) -> List["Coupon"]:
    """解析 available-coupons 的 Markdown 列表（可领取的麦麦省券）。

    ::
        - 优惠券标题：麦旋风任选 \\
          状态：可领取 \\
          优惠券图片：\\
            <img src="https://...">
    """
    if not isinstance(text, str) or not text:
        return []
    out: List["Coupon"] = []
    # 以「- 优惠券标题：」为分隔符切条
    chunks = re.split(r"-\s*优惠券标题[:：]", text)
    for chunk in chunks[1:]:
        lines = [ln.strip() for ln in chunk.strip().splitlines() if ln.strip()]
        if not lines:
            continue
        name = _clean_line(lines[0])
        status = ""
        tags = ""
        for ln in lines[1:]:
            m = _AVAIL_STATE_RE.match(ln)
            if m:
                status = _clean_line(m.group(1))
                continue
            m = _AVAIL_TAG_RE.match(ln)
            if m:
                tags = _clean_line(m.group(1))
        img = ""
        mi = _AVAIL_IMG_RE.search(chunk)
        if mi:
            img = mi.group(1)
        out.append(Coupon(
            id="",
            name=name or "优惠券",
            status=status,
            kind=tags or "",
            raw={"title": name, "status": status, "image": img},
        ))
    return out


def parse_campaign_calendar_md(text: str) -> List["Campaign"]:
    """解析 campaign-calendar 的 Markdown（按日期分组的营销活动）。

    ::
        #### 2026年10月9日 今日
        -   **活动标题**：xxx
            **活动内容介绍**：多行文案
            **活动图片介绍**：
            <img src="https://...">
    """
    if not isinstance(text, str) or not text:
        return []
    # 先记下当前时间行，用于判断「今日」
    now_line = ""
    mn = re.search(r"当前时间[:：]\s*(.+)", text)
    if mn:
        now_line = mn.group(1).strip()
    today = parse_date(now_line) or today_cn()

    marks = list(_CAMP_DATE_RE.finditer(text))
    out: List["Campaign"] = []
    for idx, m in enumerate(marks):
        year, month, day = int(m.group(1)), int(m.group(2)), int(m.group(3))
        tail = m.group(4).strip()
        start = end = None
        try:
            start = end = date(year, month, day)
        except ValueError:
            pass
        bound = marks[idx + 1].start() if idx + 1 < len(marks) else len(text)
        body = text[m.end():bound]

        # 日期行后面的状态后缀：「今日」/「往期回顾」/ 空（未来）
        status = ""
        if "往期" in tail or "回顾" in tail:
            status = "往期回顾"
        elif "今日" in tail:
            status = "进行中"
        else:
            status = "进行中" if (start and start == today) else "未来"

        for block in re.split(r"-\s+\*\*活动标题\*\*", body)[1:]:
            first, *rest = block.splitlines()
            title = _clean_line(first).lstrip(":：").strip()
            if not title:
                continue
            joined = "\n".join(rest)
            desc = ""
            md = _CAMP_DESC_RE.search(joined)
            if md:
                tail_text = joined[md.end():]
                desc_lines = []
                for ln in tail_text.splitlines():
                    s = ln.strip()
                    if not s or s.startswith("**") or s.startswith("<img"):
                        break
                    desc_lines.append(s)
                desc = " ".join(desc_lines).strip()
            img = ""
            mi = _CAMP_IMG_RE.search(joined)
            if mi:
                img = mi.group(1)
            out.append(Campaign(
                name=title,
                start=start,
                end=end,
                status=status,
                desc=desc,
                raw={"date": f"{year:04d}-{month:02d}-{day:02d}", "image": img},
            ))
    return out


# ::
#     # 您的优惠券列表
#     共 8 张可用优惠券 | 第 1/1 页 | 每页 200 条
#     ## 9.9元中杯冰美式
#     - **优惠**: ¥9.9 (用券价格)
#     - **有效期**: 2026-10-09 00:00-2026-10-15 23:59
#     - **领取时间**: 今日收到
#     - **标签**: 到店专用、外送专用
_MY_HEADER_RE = re.compile(r"^##[ \t]+(.+)$", re.M)
_MY_FIELD_RE = re.compile(r"^-[ \t]*\*\*(.+?)\*\*[ \t]*[:：][ \t]*(.+)$", re.M)
_MY_RANGE_RE = re.compile(
    r"(\d{4}-\d{2}-\d{2})[ \t]+(\d{2}:\d{2})\s*-\s*(\d{4}-\d{2}-\d{2})[ \t]+(\d{2}:\d{2})")
_MY_PRICE_RE = re.compile(r"[¥￥]?\s*([\d]+(?:\.\d+)?)\s*(?:\(([^)]*)\))?")


def parse_my_coupons_md(text: str) -> List["Coupon"]:
    """解析 ``query-my-coupons`` 的 Markdown 卡包。

    该接口有两种完全不同的返回：
      - **券包为空**时只回一句自然语言（``暂无可用优惠券``）
      - **有券**时回 Markdown，且**没有任何 JSON**，只能按标题分段解析

    Markdown 里没有 couponId，这里用「券名 + 到期日」合成稳定 id，
    让上层「领券前后做 diff」的逻辑依然成立。
    """
    if not isinstance(text, str) or not text:
        return []
    stripped = text.strip()
    if not stripped or "暂无" in stripped[:80]:
        return []

    heads = list(_MY_HEADER_RE.finditer(text))
    out: List["Coupon"] = []
    for idx, m in enumerate(heads):
        name = m.group(1).strip()
        if not name or name.startswith("#"):
            continue
        bound = heads[idx + 1].start() if idx + 1 < len(heads) else len(text)
        body = text[m.end():bound]

        fields: Dict[str, str] = {}
        for fm in _MY_FIELD_RE.finditer(body):
            fields[fm.group(1).strip()] = fm.group(2).strip()

        discount = 0.0
        fixed = 0.0
        kind = ""
        pm = _MY_PRICE_RE.search(fields.get("优惠", ""))
        if pm:
            val = to_float(pm.group(1))
            label = (pm.group(2) or "").strip()
            kind = label
            # 「用券价格」是按固定价成交，不是减免若干元 —— 两者不能混用
            if ("用券" in label) or ("价格" in label and "减" not in label):
                fixed = val
            else:
                discount = val

        start: Optional[date] = None
        end: Optional[date] = None
        window = ""
        validity = fields.get("有效期", "")
        rm = _MY_RANGE_RE.search(validity)
        if rm:
            start = parse_date(rm.group(1))
            end = parse_date(rm.group(3))
            window = validity[rm.end():].strip()
        elif validity:
            end = parse_date(validity)

        tags = [x.strip() for x in re.split(r"[、,，/|]", fields.get("标签", ""))
                if x.strip()]

        cid = f"{name}@{end.isoformat()}" if end else name
        out.append(Coupon(
            id=cid,
            name=name,
            discount=discount,
            fixed_price=fixed,
            expire=end,
            start=start,
            kind=kind,
            tags=tags,
            time_window=window,
            status="可用" if "可用" in stripped[:80] else "",
            raw=fields,
        ))
    return out


def parse_meals_payload(data: Any) -> List["Meal"]:
    """解析 query-meals 的真实结构。

    真实返回里菜单是**两段式**：
      ``data.categories[]`` —— 分类名 + 该分类下的餐品编码与标签
      ``data.meals``        —— 编码 -> 餐品详情（名称/图片/现价/原价）的映射表
    """
    if not isinstance(data, dict):
        return []
    meal_map = data.get("meals") if isinstance(data.get("meals"), dict) else {}
    categories = data.get("categories") if isinstance(data.get("categories"), list) else []

    out: List["Meal"] = []
    seen: Dict[str, "Meal"] = {}

    if isinstance(meal_map, dict):
        for code, detail in meal_map.items():
            if not isinstance(detail, dict):
                continue
            merged = dict(detail)
            merged.setdefault("code", code)
            meal = Meal.from_payload(merged)
            if meal.code and meal.code not in seen:
                seen[meal.code] = meal
                out.append(meal)

    for cat in categories:
        if not isinstance(cat, dict):
            continue
        cname = str(cat.get("name") or "")
        for item in (cat.get("meals") or []):
            if not isinstance(item, dict):
                continue
            code = str(item.get("code") or "")
            if not code:
                continue
            tags = item.get("tags") or []
            if isinstance(tags, str):
                tags = [tags]
            if code in seen:
                meal = seen[code]
                if cname and not meal.category:
                    meal.category = cname
                meal.tags = meal.tags or [str(t.get("name") if isinstance(t, dict) else t)
                                          for t in tags]
                continue
            merged = dict(item)
            merged["code"] = code
            meal = Meal.from_payload(merged)
            meal.category = cname
            seen[code] = meal
            out.append(meal)

    return [m for m in out if m.price > 0]
