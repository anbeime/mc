"""把一次雷达扫描渲染成一份自包含的 HTML 省钱报告。

约束：单文件、零外部资源（不引用 CDN / 字体 / 图片），
随便丢进浏览器或截图都能直接用，方便在 README 里当演示图。
"""
from __future__ import annotations

import html
from datetime import datetime, timezone, timedelta
from typing import List, Optional

from .models import Account, Campaign, Coupon, Store
from .strategies import Deal, Snapshot

MCD_RED = "#DA291C"
MCD_YELLOW = "#FFC72C"
INK = "#1F2328"
MUTED = "#6B7280"
LINE = "#E8E3DA"


def _e(text) -> str:
    return html.escape(str(text if text is not None else ""))


def _money(value: float) -> str:
    return f"¥{value:,.2f}".rstrip("0").rstrip(".") if value else "¥0"


def _days_label(c: Coupon) -> str:
    d = c.days_left
    if d is None:
        return "长期有效"
    if d < 0:
        return "已过期"
    if d == 0:
        return "今天到期"
    return f"{d} 天后到期"


def _render_coupon_rows(coupons: List[Coupon], mode: str) -> str:
    if not coupons:
        hint = "暂时没有快过期的券，挺好。" if mode == "urgent" else "账户里还没有可用的券。"
        return f'<div class="empty">{hint}</div>'
    rows = []
    for c in coupons:
        cls = "danger" if mode == "urgent" else "ok"
        rows.append(
            f"""<div class="coupon {cls}">
  <div class="c-main">
    <div class="c-name">{_e(c.name)}</div>
    <div class="c-meta">{_e(c.scope) or '全场通用'}{(' · 满 ' + format(c.threshold, 'g') + ' 元可用') if c.threshold else ''}</div>
  </div>
  <div class="c-right">
    <div class="c-value">{_e(c.value_label)}</div>
    <div class="c-expire">{_e(_days_label(c))}</div>
  </div>
</div>"""
        )
    return "".join(rows)


def _render_deals(deals: List[Deal]) -> str:
    if not deals:
        return '<div class="empty">这次没算出更优组合 —— 换个预算或关键词再试试。</div>'
    cards = []
    for i, d in enumerate(deals, 1):
        badge = {1: "最省", 2: "次优", 3: "备选"}.get(i, f"#{i}")
        coupon_line = f"用券：{_e(d.coupon.name)}" if d.coupon else ("用券：" + _e(d.note) if d.note else "无需用券")
        if d.coupon_scope:
            coupon_line += f"（限 {_e(d.coupon_scope)}）"
        source = "官方试算" if d.source == "mcp" else "本地估算"
        cards.append(
            f"""<div class="deal">
  <div class="deal-head"><span class="badge">{badge}</span><span class="deal-title">{_e(d.title)}</span></div>
  <div class="deal-price">
    <span class="pay">{_money(d.payable)}</span>
    <span class="was">{_money(d.original)}</span>
    <span class="save">省 {_money(d.saved)}</span>
  </div>
  <div class="deal-meta">{coupon_line} · 折扣 {d.rate*100:.0f}% · 数据口径：{source}</div>
</div>"""
        )
    return "".join(cards)


def _render_campaigns(campaigns: List[Campaign]) -> str:
    if not campaigns:
        return '<div class="empty">活动日历这会儿没取到数据。</div>'
    items = []
    for c in campaigns[:8]:
        period = ""
        if c.start or c.end:
            period = f"{c.start or '?'} → {c.end or '?'}"
        flag = '<span class="pill on">进行中</span>' if c.ongoing else '<span class="pill">待开始/已结束</span>'
        items.append(
            f"""<li>
  <div class="tl-dot"></div>
  <div class="tl-body">
    <div class="tl-title">{_e(c.name)} {flag}</div>
    <div class="tl-meta">{_e(period)}{(' · ' + _e(c.desc[:60])) if c.desc else ''}</div>
  </div>
</li>"""
        )
    return f'<ul class="timeline">{"".join(items)}</ul>'


def render_report(snap: Snapshot, *, title: str = "麦麦省钱雷达",
                  store: Optional[Store] = None) -> str:
    """渲染 HTML 报告字符串。"""
    now = datetime.now(tz=timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M")
    board = snap.coupons
    best_saving = max((d.saved for d in snap.deals), default=0.0)
    acct: Optional[Account] = snap.account

    kpi = [
        ("可用券", f"{board.active_count}", "张"),
        ("快过期", f"{len(board.expiring)}", f"张 · 面额 {_money(board.at_risk_value)}"),
        ("单笔最高省", _money(best_saving), f"试算 {len(snap.deals)} 个组合"),
        ("我的积分", f"{acct.available:,}" if acct else "—",
         f"即将过期 {acct.expiring:,}" if acct and acct.expiring else "积分账户"),
    ]
    kpi_html = "".join(
        f"""<div class="kpi">
  <div class="kpi-label">{_e(a)}</div>
  <div class="kpi-value">{_e(b)}</div>
  <div class="kpi-sub">{_e(c)}</div>
</div>"""
        for a, b, c in kpi
    )

    warnings = ""
    if snap.warnings:
        warnings = '<div class="warn">' + "".join(f"<div>⚠️ {_e(w)}</div>" for w in snap.warnings) + "</div>"

    bound = ""
    if snap.newly_bound:
        bound = f'<div class="ok-bar">✅ 本次新领到 {len(snap.newly_bound)} 张券：' \
                f'{_e("、".join(snap.newly_bound[:8]))}</div>'

    tools = " · ".join(f"`{t}`" for t in dict.fromkeys(snap.tools_used))
    store_line = ""
    if store:
        store_line = f"<div class='store'>📍 {_e(store.name)} {('· ' + _e(store.address)) if store.address else ''}</div>"

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{_e(title)} · 省钱报告</title>
<style>
  * {{ box-sizing: border-box; }}
  body {{ margin:0; background:#FBF8F3; color:{INK};
    font-family: -apple-system, BlinkMacSystemFont, "PingFang SC", "Microsoft YaHei", "Segoe UI", sans-serif;
    line-height:1.6; }}
  .wrap {{ max-width: 880px; margin: 0 auto; padding: 32px 20px 64px; }}
  header {{ background: linear-gradient(135deg, {MCD_RED}, #A81E14); color:#fff;
    border-radius: 18px; padding: 26px 28px; position: relative; overflow:hidden; }}
  header .glow {{ position:absolute; right:-40px; top:-40px; width:180px; height:180px;
    background:{MCD_YELLOW}; opacity:.22; border-radius:50%; }}
  header h1 {{ margin:0 0 6px; font-size:26px; letter-spacing:.5px; }}
  header .sub {{ opacity:.92; font-size:13px; }}
  .store {{ margin-top:10px; font-size:13px; background:rgba(255,255,255,.16);
    display:inline-block; padding:4px 10px; border-radius:999px; }}
  .kpis {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(160px,1fr)); gap:12px; margin:18px 0 22px; }}
  .kpi {{ background:#fff; border:1px solid {LINE}; border-radius:14px; padding:14px 16px; }}
  .kpi-label {{ font-size:12px; color:{MUTED}; }}
  .kpi-value {{ font-size:26px; font-weight:700; margin:2px 0 2px; color:{MCD_RED}; }}
  .kpi-sub {{ font-size:12px; color:{MUTED}; }}
  section {{ background:#fff; border:1px solid {LINE}; border-radius:16px; padding:18px 20px; margin-bottom:16px; }}
  section h2 {{ font-size:16px; margin:0 0 12px; display:flex; align-items:center; gap:8px; }}
  section h2::before {{ content:""; width:4px; height:16px; background:{MCD_YELLOW}; border-radius:2px; }}
  .coupon {{ display:flex; justify-content:space-between; align-items:center; gap:12px;
    padding:10px 12px; border-radius:10px; border:1px dashed {LINE}; margin-bottom:8px; }}
  .coupon.danger {{ border-color:#F2B8B5; background:#FFF6F5; }}
  .coupon.ok {{ background:#FCFBF9; }}
  .c-name {{ font-weight:600; font-size:14px; }}
  .c-meta {{ font-size:12px; color:{MUTED}; }}
  .c-right {{ text-align:right; white-space:nowrap; }}
  .c-value {{ font-weight:700; color:{MCD_RED}; font-size:14px; }}
  .c-expire {{ font-size:12px; color:{MUTED}; }}
  .coupon.danger .c-expire {{ color:{MCD_RED}; font-weight:600; }}
  .deal {{ border:1px solid {LINE}; border-radius:12px; padding:12px 14px; margin-bottom:10px; background:#FCFBF9; }}
  .deal-head {{ display:flex; align-items:center; gap:8px; }}
  .badge {{ background:{MCD_YELLOW}; color:#3B2A00; font-size:11px; font-weight:700;
    padding:2px 8px; border-radius:999px; }}
  .deal-title {{ font-weight:600; }}
  .deal-price {{ margin:6px 0 2px; display:flex; align-items:baseline; gap:10px; }}
  .pay {{ font-size:22px; font-weight:800; color:{MCD_RED}; }}
  .was {{ color:{MUTED}; text-decoration:line-through; font-size:13px; }}
  .save {{ background:#E9F7EC; color:#186A3B; font-size:12px; padding:2px 8px; border-radius:999px; font-weight:600; }}
  .deal-meta {{ font-size:12px; color:{MUTED}; }}
  .timeline {{ list-style:none; margin:0; padding:0 0 0 6px; }}
  .timeline li {{ display:flex; gap:10px; padding:6px 0; }}
  .tl-dot {{ width:8px; height:8px; background:{MCD_RED}; border-radius:50%; margin-top:8px; flex:0 0 auto; }}
  .tl-title {{ font-size:14px; font-weight:600; }}
  .tl-meta {{ font-size:12px; color:{MUTED}; }}
  .pill {{ font-size:11px; padding:1px 8px; border-radius:999px; background:#EEE9E1; color:{MUTED}; margin-left:6px; }}
  .pill.on {{ background:#E9F7EC; color:#186A3B; }}
  .warn {{ background:#FFF6F5; border:1px solid #F2B8B5; color:#8C1D18;
    border-radius:12px; padding:12px 14px; font-size:13px; margin-bottom:16px; }}
  .ok-bar {{ background:#F0F8F3; border:1px solid #BFE3CB; color:#186A3B;
    border-radius:12px; padding:10px 14px; font-size:13px; margin-bottom:16px; }}
  .empty {{ color:{MUTED}; font-size:13px; padding:8px 0; }}
  footer {{ font-size:12px; color:{MUTED}; text-align:center; margin-top:20px; }}
  footer code {{ background:#F1EDE6; padding:1px 5px; border-radius:4px; }}
</style>
</head>
<body>
<div class="wrap">
  <header>
    <div class="glow"></div>
    <h1>🍟 {_e(title)}</h1>
    <div class="sub">扫描时间 {_e(now)}（北京时间） · 数据来自麦当劳 MCP 实时接口</div>
    {store_line}
  </header>

  <div class="kpis">{kpi_html}</div>
  {bound}
  {warnings}

  <section>
    <h2>⏰ 券到期预警</h2>
    {_render_coupon_rows(board.expiring, "urgent")}
  </section>

  <section>
    <h2>🎯 今日最省组合</h2>
    {_render_deals(snap.deals)}
  </section>

  <section>
    <h2>🗓️ 活动雷达</h2>
    {_render_campaigns(snap.campaigns)}
  </section>

  <section>
    <h2>🎟️ 我的券包（{board.active_count} 张在效）</h2>
    {_render_coupon_rows(board.valid[:12], "valid")}
  </section>

  <footer>
    数据来源 Tool：{tools}<br>
    本报告由「麦麦省钱雷达」本地生成 · 仅供参考，实际价格与券规则以麦当劳官方为准
  </footer>
</div>
</body>
</html>
"""
