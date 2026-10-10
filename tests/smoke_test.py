"""端到端冒烟测试：在进程内起一个 Mock MCP Server，把整条链路跑一遍。

    python tests/smoke_test.py

覆盖：
  1. MCP 握手 / tools/list
  2. 券解析与「到期预警」分档
  3. 一键领券的增量识别
  4. 最优点餐组合推荐
  5. HTML 报告生成
  6. SSE 传输分支的解析
"""
from __future__ import annotations

import os
import sys
import threading
from http.server import ThreadingHTTPServer

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
sys.path.insert(0, os.path.join(_ROOT, "src"))
sys.path.insert(0, _HERE)

import mock_server  # noqa: E402
from mcd_radar import MCPClient, McDonaldTools, SavingsRadar  # noqa: E402
from mcd_radar.report import render_report  # noqa: E402

PASSED, FAILED = 0, 0


def check(label: str, cond: bool, detail: str = "") -> None:
    global PASSED, FAILED
    if cond:
        PASSED += 1
        print(f"  \033[32mPASS\033[0m {label}")
    else:
        FAILED += 1
        print(f"  \033[31mFAIL\033[0m {label} {detail}")


def start_mock(*, sse: bool) -> ThreadingHTTPServer:
    mock_server._bound["done"] = False  # 重置领券状态
    mock_server.Handler.use_sse = sse
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), mock_server.Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd


def run(sse: bool) -> None:
    tag = "SSE" if sse else "JSON"
    print(f"\n=== 传输格式：{tag} ===")
    httpd = start_mock(sse=sse)
    port = httpd.server_address[1]
    try:
        client = MCPClient(endpoint=f"http://127.0.0.1:{port}", token="smoke-token")
        client.initialize()
        tools = McDonaldTools(client)

        specs = client.list_tools()
        check("tools/list 返回 33 个 Tool", len(specs) == 33, f"实际 {len(specs)}")

        radar = SavingsRadar(tools)

        # --- 券解析与分档 ---
        before = radar._fetch_my_coupons()
        check("query-my-coupons 解析出 6 张券", len(before) == 6, f"实际 {len(before)}")
        board = radar.board_coupons(before)
        check("3 天内到期券 = 3 张", len(board.expiring) == 3, f"实际 {len(board.expiring)}")
        check("已过期券 = 1 张", len(board.expired) == 1, f"实际 {len(board.expired)}")

        # --- 一键领券 ---
        newly = radar.bind_all_coupons()
        check("一键领券识别出 2 张新券", len(newly) == 2, f"实际 {newly}")

        # --- 完整扫描 ---
        snap = radar.scan(address="深圳南山科技园", bind=False, budget=40)
        check("扫到门店", len(snap.stores) >= 1, f"实际 {len(snap.stores)}")
        check("拿到菜单", any("query-meals" == t for t in snap.tools_used), str(snap.tools_used))
        check("有推荐组合", len(snap.deals) >= 1, f"实际 {len(snap.deals)}")
        check("推荐均已用券并省钱",
              all(d.saved > 0 for d in snap.deals) if snap.deals else False,
              str([(d.title, d.saved) for d in snap.deals]))
        check("识别到进行中的活动", len(snap.ongoing_campaigns) >= 1,
              f"实际 {len(snap.ongoing_campaigns)}")
        check("读到积分账户", snap.account is not None and snap.account.available > 0)

        # --- 报告生成 ---
        html_text = render_report(snap, store=snap.stores[0] if snap.stores else None)
        check("HTML 报告含券预警区块", "券到期预警" in html_text)
        check("HTML 报告含推荐组合", "今日最省组合" in html_text)
        check("HTML 报告自包含（无外部引用）",
              "http://" not in html_text.split("<style>")[0] and "<script" not in html_text)
    finally:
        httpd.shutdown()


def main() -> int:
    print("🍟 麦麦省钱雷达 · 冒烟测试")
    run(sse=False)
    run(sse=True)
    print(f"\n结果：通过 {PASSED} 项，失败 {FAILED} 项")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
