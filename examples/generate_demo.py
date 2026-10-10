"""离线生成一份示例省钱报告（不需要 Token、不需要联网）。

用途：让任何人在 clone 之后一条命令就能看到产品长什么样。

    python examples/generate_demo.py            # 输出到 docs/demo.html
    python examples/generate_demo.py --open     # 生成后用浏览器打开
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import timedelta
from typing import List

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "src"))

from mcd_radar.models import Account, Campaign, Coupon, Meal, Store, today_cn  # noqa: E402
from mcd_radar.report import render_report  # noqa: E402
from mcd_radar.strategies import CouponBoard, Deal, Snapshot  # noqa: E402


def _day(offset: int):
    return today_cn() + timedelta(days=offset)


def build_snapshot() -> Snapshot:
    coupons: List[Coupon] = [
        Coupon(id="C001", name="巨无霸立减 8 元券", discount=8, threshold=20,
               expire=_day(0), kind="立减", scope="指定汉堡"),
        Coupon(id="C002", name="薯条买一送一", discount=0, threshold=0,
               expire=_day(1), kind="买赠", scope="小食"),
        Coupon(id="C003", name="鸡块 5 块装 1 元换购", discount=9.5, threshold=25,
               expire=_day(2), kind="换购", scope="小食"),
        Coupon(id="C004", name="麦乐送满 30 减 12", discount=12, threshold=30,
               expire=_day(11), kind="立减", scope="外送订单"),
        Coupon(id="C005", name="早餐满 15 减 6", discount=6, threshold=15,
               expire=_day(38), kind="立减", scope="早餐时段"),
        Coupon(id="C006", name="麦咖啡第二杯半价", discount=5.5, threshold=20,
               expire=_day(25), kind="折扣", scope="饮品"),
        Coupon(id="C007", name="任意消费减 4 元", discount=4, threshold=0,
               expire=_day(60), kind="立减", scope="全场通用"),
    ]

    board = CouponBoard()
    board.expiring = [c for c in coupons if c.days_left is not None and 0 <= c.days_left <= 3]
    board.valid = [c for c in coupons if c not in board.expiring]
    board.total_value = sum(c.discount for c in board.valid + board.expiring)
    board.at_risk_value = sum(c.discount for c in board.expiring)
    board.expiring.sort(key=lambda c: c.days_left)
    board.valid.sort(key=lambda c: c.days_left)

    mcd = Meal(code="M007", name="巨无霸套餐（中薯+中可）", price=39.0, origin_price=45.0, category="套餐")
    spicy = Meal(code="M003", name="麦辣鸡腿堡套餐", price=36.0, origin_price=40.0, category="套餐")
    double = Meal(code="M002", name="双层吉士汉堡", price=21.5, category="汉堡")

    deals = [
        Deal(meals=[mcd], coupon=coupons[3], original=39.0, payable=27.0, saved=12.0,
             source="mcp", coupon_scope=coupons[3].scope),
        Deal(meals=[spicy], coupon=coupons[5], original=36.0, payable=30.5, saved=5.5,
             source="mcp", coupon_scope=coupons[5].scope),
        Deal(meals=[double], coupon=coupons[2], original=21.5, payable=12.0, saved=9.5,
             source="mcp", coupon_scope=coupons[2].scope),
    ]
    deals.sort(key=lambda d: -d.saved)

    campaigns = [
        Campaign(name="1024 程序员节 · 麦麦代码日", start=_day(-1), end=_day(16),
                 status="ongoing", desc="出示代码截图可领小食券"),
        Campaign(name="会员日 · 每周三积分翻倍", start=_day(-30), end=_day(60),
                 status="ongoing", desc="星期三下单，积分双倍累计"),
        Campaign(name="巨无霸 55 周年庆", start=_day(20), end=_day(40),
                 status="future", desc="经典回归，限量复古包装"),
        Campaign(name="早餐新品尝鲜周", start=_day(5), end=_day(12),
                 status="future", desc="新早餐组合 9.9 元起"),
    ]

    return Snapshot(
        stores=[Store(code="S1001", name="麦当劳·科技园店",
                      address="深圳市南山区科技园南区 1 号", distance=0.38)],
        coupons=board,
        campaigns=campaigns,
        ongoing_campaigns=[c for c in campaigns if c.ongoing],
        account=Account(available=1860, total=5420, frozen=0, expiring=320),
        deals=deals,
        newly_bound=["新客首单减 10 元", "麦麦省专享 6 元券"],
        warnings=["有 320 积分即将过期，别让它白白蒸发。"],
        tools_used=["query-my-coupons", "query-nearby-stores", "query-meals",
                    "query-store-coupons", "calculate-price", "campaign-calendar",
                    "query-my-account"],
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(_ROOT, "docs", "demo.html"))
    ap.add_argument("--open", action="store_true")
    args = ap.parse_args()

    snap = build_snapshot()
    html_text = render_report(snap, store=snap.stores[0])
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(html_text)
    print(f"✓ 示例报告已生成：{os.path.abspath(args.out)}")
    if args.open:
        import webbrowser
        webbrowser.open("file://" + os.path.abspath(args.out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
