"""麦麦省钱雷达 · 命令行入口。

    mcd-radar                 # 完整扫描（终端摘要）
    mcd-radar --html out.html # 扫描并输出 HTML 省钱报告
    mcd-radar bind            # 一键领取麦麦省所有可领券
    mcd-radar coupons         # 只看券包与到期预警
    mcd-radar deals --budget 30 --want 巨无霸
    mcd-radar tools           # 列出麦当劳 MCP 提供的全部 Tool
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from typing import List, Optional

from .mcp_client import MCPAuthError, MCPClient, MCPError
from .models import Store
from .report import render_report
from .strategies import SavingsRadar, Snapshot
from .tools import McDonaldTools

BOLD = "\033[1m"
DIM = "\033[2m"
RED = "\033[31m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
RESET = "\033[0m"


def _supports_color() -> bool:
    return sys.stdout.isatty() and os.environ.get("NO_COLOR") is None


def c(text: str, color: str) -> str:
    return f"{color}{text}{RESET}" if _supports_color() else text


def _hr(char: str = "─", width: int = 60) -> str:
    return char * width


# ------------------------------------------------------------------ 输出
def print_summary(snap: Snapshot, *, store: Optional[Store] = None) -> None:
    board = snap.coupons
    print()
    print(c(f"🍟 麦麦省钱雷达 · 扫描结果", BOLD))
    if store:
        print(c(f"   门店：{store.name} {store.address}", DIM))
    print(_hr())

    if board.fixed_price_count and board.total_value <= 0:
        value_note = f"（其中 {board.fixed_price_count} 张为指定商品优惠价）"
    elif board.total_value > 0:
        value_note = f"（在效面额约 {board.total_value:g} 元）"
    else:
        value_note = ""
    print(f"  {c('可用券', BOLD)}      {board.active_count} 张"
          f"{c(value_note, DIM)}")
    if board.expiring:
        risky = ("，3 天内到期 —— 别放过期"
                 if board.at_risk_value <= 0 else
                 f'，涉及面额 {board.at_risk_value:g} 元 —— 别放过期')
        print(f"  {c('快过期', RED)}      {len(board.expiring)} 张"
              f"{c(risky, RED)}")
    if snap.account:
        print(f"  {c('我的积分', BOLD)}    {snap.account.available:,}"
              + (c(f"（{snap.account.expiring:,} 即将过期）", YELLOW)
                 if snap.account.expiring else ""))

    if snap.newly_bound:
        print(f"  {c('本次新领', GREEN)}    {len(snap.newly_bound)} 张："
              f"{'、'.join(snap.newly_bound[:6])}")

    if board.expiring:
        print()
        print(c("  ⏰ 券到期预警", BOLD))
        for cp in board.expiring[:6]:
            print(f"     {c('•', RED)} {cp.name}  {c(cp.value_label, YELLOW)}  "
                  f"{c(f'[{cp.expire_label}]', RED)}")

    if snap.deals:
        ledger = SavingsRadar.ledger(snap.deals)
        print()
        print(c("  🎯 今日最省组合", BOLD)
              + c(f"   （合计省 {ledger['saved']:g} 元）", GREEN))
        for i, d in enumerate(snap.deals, 1):
            src = "官方试算" if d.source == "mcp" else "本地估算"
            print(f"     {i}. {BOLD if _supports_color() else ''}{d.title}"
                  f"{RESET if _supports_color() else ''}")
            print(f"        应付 {c(f'{d.payable:g} 元', RED)}"
                  f"  {c(f'原价 {d.original:g} 元', DIM)}"
                  f"  {c(f'省 {d.saved:g} 元', GREEN)}"
                  f"  {c(f'（{src}）', DIM)}")
            if d.coupon:
                scope = f"（限：{d.coupon_scope}）" if d.coupon_scope else ""
                print(f"        用券：{d.coupon.name}{scope}")

    if snap.ongoing_campaigns:
        print()
        print(c("  🗓️ 正在进行的活动", BOLD))
        for cp in snap.ongoing_campaigns[:5]:
            print(f"     • {cp.name}")

    if snap.warnings:
        print()
        for w in snap.warnings:
            print(f"  {c('⚠️  ' + w, YELLOW)}")

    print()
    print(c(f"  本次调用 Tool：{'、'.join(dict.fromkeys(snap.tools_used))}", DIM))
    print(_hr())
    print(c("  提示：加 --html report.html 可导出可视化省钱报告", DIM))
    print()


# ------------------------------------------------------------------ 命令
def cmd_scan(args: argparse.Namespace) -> int:
    client = MCPClient(endpoint=args.endpoint, timeout=args.timeout)
    if not client.configured:
        print(c("✗ 未检测到 MCP Token。", RED))
        print("  请先到 https://open.mcd.cn/mcp 申请 Token，然后：")
        print("    export MCD_MCP_TOKEN=<你的Token>")
        return 2

    tools = McDonaldTools(client)
    client.initialize()
    radar = SavingsRadar(tools)

    store: Optional[Store] = None
    if args.store:
        store = Store(code=args.store, name="指定门店")

    snap = radar.scan(
        address=args.address,
        city=args.city,
        store_code=args.store,
        bind=args.bind,
        budget=args.budget,
        want=args.want.split(",") if args.want else None,
    )
    if store is None and snap.stores:
        store = snap.stores[0]

    if args.json:
        payload = {
            "store": store.name if store else None,
            "coupons_active": snap.coupons.active_count,
            "coupons_expiring": [c.name for c in snap.coupons.expiring],
            "deals": [
                {"title": d.title, "payable": d.payable, "original": d.original,
                 "saved": d.saved, "source": d.source} for d in snap.deals
            ],
            "campaigns": [c.name for c in snap.ongoing_campaigns],
            "warnings": snap.warnings,
            "tools_used": list(dict.fromkeys(snap.tools_used)),
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print_summary(snap, store=store)

    if args.html:
        html_text = render_report(snap, store=store)
        with open(args.html, "w", encoding="utf-8") as f:
            f.write(html_text)
        print(c(f"✓ 已生成省钱报告：{os.path.abspath(args.html)}", GREEN))
    return 0


def cmd_bind(args: argparse.Namespace) -> int:
    client = MCPClient(endpoint=args.endpoint, timeout=args.timeout)
    if not client.configured:
        print(c("✗ 未检测到 MCP Token。", RED))
        return 2
    tools = McDonaldTools(client)
    client.initialize()
    radar = SavingsRadar(tools)
    got: List[str] = radar.bind_all_coupons()
    if got:
        print(c(f"✓ 新领到 {len(got)} 张券：", GREEN) + "、".join(got))
    else:
        print("这次没有新券可领（可能已经领完了）。")
    return 0


def cmd_coupons(args: argparse.Namespace) -> int:
    client = MCPClient(endpoint=args.endpoint, timeout=args.timeout)
    if not client.configured:
        print(c("✗ 未检测到 MCP Token。", RED))
        return 2
    tools = McDonaldTools(client)
    client.initialize()
    radar = SavingsRadar(tools)
    board = radar.board_coupons(radar._fetch_my_coupons())
    print()
    print(c(f"券包盘点：在效 {board.active_count} 张 · 快过期 {len(board.expiring)} 张 · "
            f"已过期 {len(board.expired)} 张", BOLD))
    print(_hr())
    for cp in board.expiring + board.valid:
        print(f"  {cp.value_label:<10} {cp.name}  {c(_expire_text(cp), DIM)}")
    if not board.active_count:
        print("  （空）")
    print()
    return 0


def _expire_text(cp) -> str:
    d = cp.days_left
    if d is None:
        return "长期有效"
    if d < 0:
        return "已过期"
    return "今天到期" if d == 0 else f"{d} 天后到期"


def cmd_deals(args: argparse.Namespace) -> int:
    return cmd_scan(args)


def cmd_tools(args: argparse.Namespace) -> int:
    client = MCPClient(endpoint=args.endpoint, timeout=args.timeout)
    if not client.configured:
        print("（未配置 Token，展示内置工具清单）\n")
        for name, title in McDonaldTools.TOOL_TITLES.items():
            print(f"  {name:<26} {title}")
        return 0
    client.initialize()
    specs = client.list_tools()
    print(c(f"麦当劳 MCP 共提供 {len(specs)} 个 Tool：\n", BOLD))
    for s in specs:
        print(f"  {s.name:<26} {s.title or McDonaldTools.TOOL_TITLES.get(s.name, '')}")
        if s.description and args.verbose:
            print(f"      {c(s.description[:100], DIM)}")
    return 0


# ------------------------------------------------------------------ parser
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="mcd-radar",
        description="🍟 麦麦省钱雷达 —— 让每一张券都花在刀刃上",
    )
    p.add_argument("--endpoint", default=os.environ.get("MCD_MCP_ENDPOINT", "https://mcp.mcd.cn"),
                   help="麦当劳 MCP 接入地址（默认 https://mcp.mcd.cn）")
    p.add_argument("--timeout", type=float, default=30.0, help="单次请求超时（秒）")
    p.add_argument("--address", help="用于查询附近门店的位置关键词，如 科技园、中山公园")
    p.add_argument("--city", help="城市名，如 深圳。官方接口要求 city+关键词 才能按位置找店")
    p.add_argument("--store", help="指定门店编码（跳过按地址找店）")
    p.add_argument("--budget", type=float, help="预算上限（元）")
    p.add_argument("--want", help="想吃什么，逗号分隔，如 巨无霸,咖啡")
    p.add_argument("--bind", action="store_true", help="扫描前先一键领取麦麦省所有可领券")
    p.add_argument("--html", help="把结果导出为 HTML 报告到指定路径")
    p.add_argument("--json", action="store_true", help="以 JSON 输出（便于脚本消费）")
    p.add_argument("-v", "--verbose", action="store_true")

    sub = p.add_subparsers(dest="command")
    for name, fn, help_text in (
        ("scan", cmd_scan, "完整扫描（默认）"),
        ("bind", cmd_bind, "一键领取麦麦省所有可领券"),
        ("coupons", cmd_coupons, "只看券包与到期预警"),
        ("deals", cmd_deals, "只看最省组合推荐"),
        ("tools", cmd_tools, "列出 MCP 提供的全部 Tool"),
    ):
        sp = sub.add_parser(name, help=help_text)
        sp.set_defaults(func=fn)
    return p


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    fn = getattr(args, "func", cmd_scan)
    try:
        return fn(args)
    except MCPAuthError as e:
        print(c(f"✗ 鉴权失败：{e}", RED))
        print("  请检查 MCD_MCP_TOKEN 是否有效（https://open.mcd.cn/mcp 可重新申请）。")
        return 2
    except MCPError as e:
        print(c(f"✗ MCP 调用失败：{e}", RED))
        return 1
    except KeyboardInterrupt:
        print("\n已中断。")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
