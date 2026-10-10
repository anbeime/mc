"""麦麦省钱雷达（McDonald's Savings Radar）。

基于麦当劳中国 MCP Server（https://open.mcd.cn/mcp）开发的开源 Skill：
一站式完成「薅券 → 盘券 → 算最优 → 盯活动」，把优惠券的价值榨干。

    from mcd_radar import MCPClient, McDonaldTools, SavingsRadar

    client = MCPClient()           # 自动读取环境变量 MCD_MCP_TOKEN
    client.initialize()
    radar = SavingsRadar(McDonaldTools(client))
    snapshot = radar.scan(bind=True, budget=40)
"""
from .mcp_client import (
    MCPAuthError,
    MCPClient,
    MCPError,
    MCPRateLimitError,
    ToolSpec,
)
from .models import Account, Campaign, Coupon, Meal, Store
from .strategies import CouponBoard, Deal, SavingsRadar, Snapshot
from .tools import McDonaldTools

__version__ = "1.0.0"

__all__ = [
    "MCPClient",
    "MCPError",
    "MCPAuthError",
    "MCPRateLimitError",
    "ToolSpec",
    "McDonaldTools",
    "SavingsRadar",
    "Snapshot",
    "CouponBoard",
    "Deal",
    "Coupon",
    "Meal",
    "Store",
    "Campaign",
    "Account",
    "__version__",
]
