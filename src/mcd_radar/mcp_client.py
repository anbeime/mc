"""零依赖的 MCP Streamable HTTP 客户端。

麦当劳中国 MCP Server 使用 Model Context Protocol 的 **Streamable HTTP** 传输：
    POST https://mcp.mcd.cn
    Authorization: Bearer <MCP_TOKEN>
    Accept: application/json, text/event-stream

服务端可能以两种方式返回 JSON-RPC 响应：
  1) 直接 ``application/json`` 一个 JSON 对象；
  2) ``text/event-stream``（SSE），响应体形如 ``event: message\\ndata: {...}\\n\\n``。

本模块只依赖标准库（urllib），把两种形态统一成 Python 字典，
并自动维护 ``Mcp-Session-Id``（若服务端下发）。

设计原则：**不引入任何第三方依赖**，保证参赛者 clone 下来即可运行。
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional
from urllib.parse import urlparse

DEFAULT_ENDPOINT = "https://mcp.mcd.cn"
DEFAULT_PROTOCOL_VERSION = "2024-11-05"
CLIENT_NAME = "mcd-savings-radar"
CLIENT_VERSION = "1.0.0"

_LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1", "0.0.0.0", ""}


def _is_local(endpoint: str) -> bool:
    host = (urlparse(endpoint).hostname or "").lower()
    return host in _LOCAL_HOSTS or host.endswith(".local")


def _build_opener(endpoint: str) -> urllib.request.OpenerDirector:
    """构造 opener。

    很多开发机上装有会接管 localhost 的 HTTP 代理（企业代理 / 抓包工具），
    这会让「连本地 mock / 本地 MCP」直接失败。对本地地址显式禁用代理，
    对外网地址仍然尊重系统代理设置。
    """
    if _is_local(endpoint):
        return urllib.request.build_opener(urllib.request.ProxyHandler({}))
    return urllib.request.build_opener()


class MCPError(RuntimeError):
    """MCP 调用失败的统一异常。"""

    def __init__(self, message: str, *, code: Optional[int] = None, data: Any = None):
        super().__init__(message)
        self.code = code
        self.data = data


class MCPAuthError(MCPError):
    """Token 无效 / 缺失 / 过期（HTTP 401 或 JSON-RPC 401）。"""


class MCPRateLimitError(MCPError):
    """触发限流（600 次/分钟）。"""


@dataclass
class ToolSpec:
    """一个 MCP Tool 的元数据。"""

    name: str
    description: str = ""
    input_schema: Dict[str, Any] = field(default_factory=dict)
    title: str = ""

    @classmethod
    def from_payload(cls, payload: Dict[str, Any]) -> "ToolSpec":
        annotations = payload.get("annotations") or {}
        return cls(
            name=payload.get("name", ""),
            title=annotations.get("title") or payload.get("title") or "",
            description=payload.get("description", ""),
            input_schema=payload.get("inputSchema") or {},
        )


def _parse_sse(body: str) -> List[Dict[str, Any]]:
    """把 SSE 报文解析成若干 JSON 对象（只取 data 字段）。"""
    messages: List[Dict[str, Any]] = []
    for block in body.replace("\r\n", "\n").split("\n\n"):
        data_lines = [
            line[len("data:"):].strip()
            for line in block.split("\n")
            if line.startswith("data:")
        ]
        if not data_lines:
            continue
        raw = "\n".join(data_lines).strip()
        if not raw or raw == "[DONE]":
            continue
        try:
            messages.append(json.loads(raw))
        except json.JSONDecodeError:
            continue
    return messages


class MCPClient:
    """极简的 MCP Streamable HTTP 客户端。"""

    def __init__(
        self,
        endpoint: str = DEFAULT_ENDPOINT,
        token: Optional[str] = None,
        *,
        timeout: float = 30.0,
        max_retries: int = 3,
        client_name: str = CLIENT_NAME,
    ):
        self.endpoint = (endpoint or DEFAULT_ENDPOINT).rstrip("/")
        self.token = token if token is not None else _read_token_from_env()
        self.timeout = timeout
        self.max_retries = max_retries
        self.client_name = client_name
        self.session_id: Optional[str] = None
        self._next_id = 0
        self._tool_cache: Optional[List[ToolSpec]] = None
        self._opener = _build_opener(self.endpoint)

    # ------------------------------------------------------------------ 基础
    @property
    def configured(self) -> bool:
        """是否已配置 Token。"""
        return bool(self.token)

    def _id(self) -> int:
        self._next_id += 1
        return self._next_id

    def _headers(self) -> Dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "User-Agent": f"{self.client_name}/{CLIENT_VERSION}",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id
        return headers

    def _post(self, payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """发一条 JSON-RPC 消息，返回响应字典（通知类消息返回 None）。"""
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            self.endpoint, data=data, headers=self._headers(), method="POST"
        )

        last_err: Optional[Exception] = None
        for attempt in range(self.max_retries):
            try:
                with self._opener.open(req, timeout=self.timeout) as resp:
                    sid = resp.headers.get("Mcp-Session-Id")
                    if sid:
                        self.session_id = sid
                    body = resp.read().decode("utf-8", "replace")
                    ctype = (resp.headers.get("Content-Type") or "").lower()
                    if not body.strip():
                        return None
                    if "text/event-stream" in ctype or body.lstrip().startswith("event:"):
                        msgs = _parse_sse(body)
                        # SSE 流里可能夹带 progress 通知，取第一条带 result/error 的
                        for m in msgs:
                            if "result" in m or "error" in m:
                                return m
                        return msgs[-1] if msgs else None
                    return json.loads(body)
            except urllib.error.HTTPError as e:
                detail = e.read().decode("utf-8", "replace")[:400]
                if e.code == 401 or e.code == 403:
                    raise MCPAuthError(
                        "麦当劳 MCP 鉴权失败（HTTP %s）：请检查 MCP_TOKEN 是否正确。" % e.code,
                        code=e.code,
                        data=detail,
                    )
                if e.code == 429:
                    wait = 1.5 * (attempt + 1)
                    last_err = MCPRateLimitError("触发限流（600 次/分钟），稍后重试", code=429)
                    time.sleep(wait)
                    continue
                if 500 <= e.code < 600 and attempt < self.max_retries - 1:
                    last_err = MCPError(f"服务端错误 HTTP {e.code}", code=e.code, data=detail)
                    time.sleep(0.8 * (attempt + 1))
                    continue
                raise MCPError(f"HTTP {e.code}: {detail}", code=e.code)
            except urllib.error.URLError as e:
                last_err = MCPError(f"网络不可达：{e.reason}")
                time.sleep(0.8 * (attempt + 1))
            except json.JSONDecodeError as e:
                raise MCPError(f"响应不是合法 JSON：{e}")

        raise last_err or MCPError("未知错误")

    def _rpc(self, method: str, params: Optional[Dict[str, Any]] = None) -> Any:
        """发一个请求并解包 result / error。"""
        payload: Dict[str, Any] = {"jsonrpc": "2.0", "id": self._id(), "method": method}
        if params is not None:
            payload["params"] = params
        resp = self._post(payload)
        if resp is None:
            return None
        if "error" in resp and resp["error"]:
            err = resp["error"] or {}
            code = err.get("code")
            msg = err.get("message", "MCP 调用失败")
            if code in (401, 403):
                raise MCPAuthError(msg, code=code, data=err.get("data"))
            raise MCPError(msg, code=code, data=err.get("data"))
        return resp.get("result")

    def _notify(self, method: str, params: Optional[Dict[str, Any]] = None) -> None:
        payload: Dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            payload["params"] = params
        try:
            self._post(payload)
        except MCPError:
            # 通知失败不应阻断主流程
            pass

    # -------------------------------------------------------------- 生命周期
    def initialize(self) -> Dict[str, Any]:
        result = self._rpc(
            "initialize",
            {
                "protocolVersion": DEFAULT_PROTOCOL_VERSION,
                "capabilities": {"roots": {"listChanged": False}},
                "clientInfo": {"name": self.client_name, "version": CLIENT_VERSION},
            },
        )
        self._notify("notifications/initialized", {})
        return result or {}

    def list_tools(self, *, refresh: bool = False) -> List[ToolSpec]:
        if self._tool_cache is not None and not refresh:
            return self._tool_cache
        result = self._rpc("tools/list", {}) or {}
        specs = [ToolSpec.from_payload(t) for t in result.get("tools", [])]
        self._tool_cache = specs
        return specs

    def call_tool(self, name: str, arguments: Optional[Dict[str, Any]] = None) -> Any:
        """调用一个 MCP Tool，返回其结构化内容（尽力解包 JSON）。"""
        result = self._rpc("tools/call", {"name": name, "arguments": arguments or {}})
        if result is None:
            return None
        if result.get("isError"):
            text = _content_to_text(result.get("content"))
            raise MCPError(f"Tool `{name}` 返回错误：{text}")
        return _unwrap_content(result.get("content"))

    def __enter__(self) -> "MCPClient":
        self.initialize()
        return self

    def __exit__(self, *exc: Any) -> None:
        return None


# ----------------------------------------------------------------- 辅助函数
def _content_to_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict):
                if item.get("type") == "text":
                    parts.append(str(item.get("text", "")))
                else:
                    parts.append(json.dumps(item, ensure_ascii=False))
            else:
                parts.append(str(item))
        return "\n".join(parts)
    return json.dumps(content, ensure_ascii=False) if content is not None else ""


def _unwrap_content(content: Any) -> Any:
    """把 MCP content 数组尽量还原成业务对象。

    麦当劳 MCP 多数 Tool 返回 ``[{"type": "text", "text": "<json>"}]``，
    这里把 text 尝试 json.loads，失败则原样返回字符串。
    """
    if content is None:
        return None
    if isinstance(content, dict):
        return content
    if not isinstance(content, list):
        return content

    texts = [
        str(i.get("text", ""))
        for i in content
        if isinstance(i, dict) and i.get("type") == "text"
    ]
    if not texts:
        return content
    joined = "\n".join(texts).strip()
    try:
        return json.loads(joined)
    except (json.JSONDecodeError, ValueError):
        return joined


def _read_token_from_env() -> Optional[str]:
    for key in ("MCD_MCP_TOKEN", "MCD_TOKEN", "MC_MCP_TOKEN"):
        val = os.environ.get(key)
        if val:
            return val.strip()
    return None
