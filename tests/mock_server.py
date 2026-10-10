"""本地 Mock 麦当劳 MCP Server —— 用于在没有真实 Token 时端到端自测。

严格复刻麦当劳 MCP 的 Streamable HTTP 契约：
    POST http://127.0.0.1:<port>/
    请求头 Authorization: Bearer <任意非空>
    Accept: application/json, text/event-stream

默认用 application/json 返回；加 --sse 则用 text/event-stream，
用来验证客户端的 SSE 解析分支。

    python tests/mock_server.py --port 8765
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, List

CN = timezone(timedelta(hours=8))
TODAY = datetime.now(tz=CN).date()


def _d(offset_days: int) -> str:
    return (TODAY + timedelta(days=offset_days)).strftime("%Y-%m-%d")


# ------------------------------------------------------------------ 假数据
MY_COUPONS = [
    {"couponId": "C001", "couponName": "巨无霸立减 8 元券", "discountAmount": 8,
     "threshold": 20, "expireTime": _d(1), "couponType": "立减", "scope": "指定汉堡"},
    {"couponId": "C002", "couponName": "麦乐送满 30 减 12", "discountAmount": 12,
     "threshold": 30, "expireTime": _d(12), "couponType": "立减", "scope": "外送订单"},
    {"couponId": "C003", "couponName": "中薯条买一送一", "discountAmount": 0,
     "threshold": 0, "expireTime": _d(2), "couponType": "买赠", "scope": "小食"},
    {"couponId": "C004", "couponName": "早餐满 15 减 6", "discountAmount": 6,
     "threshold": 15, "expireTime": _d(45), "couponType": "立减", "scope": "早餐时段"},
    {"couponId": "C005", "couponName": "任意消费减 4 元", "discountAmount": 4,
     "threshold": 0, "expireTime": _d(-2), "couponType": "立减", "scope": "全场"},
    {"couponId": "C006", "couponName": "鸡块 5 块装 1 元换购", "discountAmount": 9.5,
     "threshold": 25, "expireTime": _d(3), "couponType": "换购", "scope": "小食"},
]

AVAILABLE_COUPONS = [
    {"couponId": "A101", "couponName": "新客首单减 10 元", "discountAmount": 10, "threshold": 20,
     "expireTime": _d(7)},
    {"couponId": "A102", "couponName": "麦麦省专享 6 元券", "discountAmount": 6, "threshold": 15,
     "expireTime": _d(5)},
]

STORES = [
    {"storeCode": "S1001", "storeName": "麦当劳·科技园店", "address": "深圳市南山区科技园南区 1 号",
     "distance": 380},
    {"storeCode": "S1002", "storeName": "麦当劳·深大地铁站店", "address": "深圳市南山区深南大道 10000 号",
     "distance": 1200},
]

MEALS = [
    {"mealCode": "M001", "mealName": "巨无霸汉堡", "price": 25.5, "originPrice": 25.5, "category": "汉堡"},
    {"mealCode": "M002", "mealName": "双层吉士汉堡", "price": 21.5, "category": "汉堡"},
    {"mealCode": "M003", "mealName": "麦辣鸡腿堡套餐", "price": 36.0, "originPrice": 40.0, "category": "套餐"},
    {"mealCode": "M004", "mealName": "板烧鸡腿堡", "price": 23.0, "category": "汉堡"},
    {"mealCode": "M005", "mealName": "中薯条", "price": 12.5, "category": "小食"},
    {"mealCode": "M006", "mealName": "麦旋风", "price": 13.5, "category": "甜品"},
    {"mealCode": "M007", "mealName": "巨无霸套餐", "price": 39.0, "originPrice": 45.0, "category": "套餐"},
    {"mealCode": "M008", "mealName": "热咖啡（中）", "price": 11.0, "category": "饮品"},
]

CAMPAIGNS = [
    {"name": "1024 程序员节 · 麦麦代码日", "startTime": _d(-1), "endTime": _d(16),
     "status": "ongoing", "content": "出示代码截图可领小食券"},
    {"name": "会员日 · 每周三积分翻倍", "startTime": _d(-30), "endTime": _d(60),
     "status": "ongoing", "content": "星期三下单积分双倍累计"},
    {"name": "巨无霸 55 周年庆", "startTime": _d(20), "endTime": _d(40), "status": "future",
     "content": "经典回归，限量包装"},
]

ACCOUNT = {"availablePoints": 1860, "totalPoints": 5420, "frozenPoints": 0,
           "expiringPoints": 320}

TOOLS = [
    "list-nutrition-foods", "delivery-query-addresses", "delivery-create-address",
    "delivery-query-stores", "query-meal-assistance", "query-nearby-stores",
    "query-store-coupons", "query-meals", "query-meal-detail", "calculate-price",
    "create-order", "cancel-order", "query-order", "order-list", "campaign-calendar",
    "available-coupons", "auto-bind-coupons", "query-my-coupons", "query-my-account",
    "mall-points-products", "mall-product-detail", "mall-create-order",
    "now-time-info", "query-lottery-info", "draw-lottery", "query-my-prizes",
    "mall-order-list", "mall-order-detail", "query-party-city", "query-party-store",
    "query-partystore-date", "query-partystore-session", "party-order-create",
]

TITLES = {
    "query-my-coupons": "我的优惠券查询",
    "query-store-coupons": "查询当前门店下可用的优惠券列表",
    "available-coupons": "麦麦省券列表查询",
    "auto-bind-coupons": "麦麦省一键领券",
    "query-nearby-stores": "查询附近可用门店",
    "query-meals": "查询当前可售卖的餐品列表",
    "campaign-calendar": "活动日历查询工具",
    "query-my-account": "我的积分查询",
    "calculate-price": "商品价格计算",
    "now-time-info": "获取当前时间信息",
}

# 记录一键领券的调用（用于演示"新领到券"）
_bound: Dict[str, bool] = {"done": False}


def call_tool(name: str, args: Dict[str, Any]) -> Any:
    if name == "query-my-coupons":
        data = list(MY_COUPONS)
        if _bound["done"]:
            data += AVAILABLE_COUPONS
        return {"coupons": data}
    if name == "query-store-coupons":
        return {"data": MY_COUPONS[:3]}
    if name == "available-coupons":
        return {"list": AVAILABLE_COUPONS}
    if name == "auto-bind-coupons":
        _bound["done"] = True
        return {"success": True, "message": "已为你领取全部可领优惠券",
                "count": len(AVAILABLE_COUPONS)}
    if name == "query-nearby-stores":
        return {"stores": STORES}
    if name == "query-meals":
        return {"meals": MEALS}
    if name == "campaign-calendar":
        return {"activities": CAMPAIGNS}
    if name == "query-my-account":
        return ACCOUNT
    if name == "now-time-info":
        return {"datetime": datetime.now(tz=CN).isoformat(), "timestamp": int(time.time())}
    if name == "calculate-price":
        products = args.get("products") or []
        total = 0.0
        by_code = {m["mealCode"]: m for m in MEALS}
        for p in products:
            m = by_code.get(p.get("mealCode"), {})
            total += float(m.get("price", 0)) * int(p.get("quantity", 1))
        discount = 0.0
        cid = args.get("couponId")
        if cid:
            pool = MY_COUPONS + (AVAILABLE_COUPONS if _bound["done"] else [])
            for c in pool:
                if c["couponId"] == cid:
                    discount = float(c.get("discountAmount", 0))
        delivery = 0.0
        payable = max(total - discount, 0) + delivery
        return {"totalAmount": total, "discountAmount": discount,
                "deliveryFee": delivery, "payableAmount": round(payable, 2)}
    return {"message": f"mock: tool `{name}` 未实现（仅用于自测）", "ok": True}


# ------------------------------------------------------------------ HTTP
class Handler(BaseHTTPRequestHandler):
    server_version = "MockMCD/1.0"
    use_sse = False

    def log_message(self, fmt, *a):  # 静音
        if "--verbose" in sys.argv:
            sys.stderr.write("[mock] " + fmt % a + "\n")

    def _send(self, obj: Any, status: int = 200, *, sse: bool = False):
        if sse:
            body = f"event: message\ndata: {json.dumps(obj, ensure_ascii=False)}\n\n".encode()
            ctype = "text/event-stream"
        else:
            body = json.dumps(obj, ensure_ascii=False).encode()
            ctype = "application/json"
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Mcp-Session-Id", "mock-session-0001")
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):  # noqa: N802
        auth = self.headers.get("Authorization", "")
        if not auth.startswith("Bearer ") or len(auth) <= 8:
            self._send({"jsonrpc": "2.0", "id": None,
                        "error": {"code": 401, "message": "MCP Token invalid"}}, 401)
            return
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length).decode("utf-8")
        try:
            req = json.loads(raw)
        except json.JSONDecodeError:
            self._send({"jsonrpc": "2.0", "id": None,
                        "error": {"code": -32700, "message": "Parse error"}}, 400)
            return

        method = req.get("method")
        rid = req.get("id")
        if rid is None:  # 通知
            self.send_response(202)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return

        if method == "initialize":
            result = {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "mock-mcd-mcp", "version": "1.0.0"},
            }
        elif method == "tools/list":
            result = {"tools": [
                {"name": n, "description": TITLES.get(n, n) + "（mock）",
                 "inputSchema": {"type": "object", "properties": {}}}
                for n in TOOLS
            ]}
        elif method == "tools/call":
            params = req.get("params") or {}
            payload = call_tool(params.get("name", ""), params.get("arguments") or {})
            result = {"content": [{"type": "text",
                                   "text": json.dumps(payload, ensure_ascii=False)}],
                      "isError": False}
        else:
            self._send({"jsonrpc": "2.0", "id": rid,
                        "error": {"code": -32601, "message": f"method not found: {method}"}})
            return

        self._send({"jsonrpc": "2.0", "id": rid, "result": result}, sse=self.use_sse)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--sse", action="store_true", help="用 text/event-stream 返回")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()
    Handler.use_sse = args.sse
    httpd = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    scheme = "SSE" if args.sse else "JSON"
    print(f"Mock MCP Server 已启动：http://127.0.0.1:{args.port}  (响应格式：{scheme})")
    print("Token 校验：Bearer 开头且非空即可通过")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
