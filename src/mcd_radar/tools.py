"""麦当劳 MCP 33 个 Tool 的语义化封装。

方法名用 snake_case，内部映射到官方 kebab-case 的 Tool 名。
每个方法只做参数整形 + 调用，不掺业务逻辑；策略在 ``strategies.py``。

参考：https://github.com/M-China/mcd-mcp-server （工具列表 v1.0.9，2026-09-10）
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from .mcp_client import MCPClient


class McDonaldTools:
    """对 MCPClient 的语义化包装。"""

    #: Tool 名 -> 中文名（用于报告与文档）
    TOOL_TITLES: Dict[str, str] = {
        "list-nutrition-foods": "餐品营养信息列表",
        "delivery-query-addresses": "获取用户可配送地址列表",
        "delivery-create-address": "新增配送地址",
        "delivery-query-stores": "查询可配送的门店列表",
        "query-meal-assistance": "查询助餐服务（团餐）",
        "query-nearby-stores": "查询附近可用门店",
        "query-store-coupons": "查询当前门店下可用的优惠券列表",
        "query-meals": "查询当前可售卖的餐品列表",
        "query-meal-detail": "查询餐品详情",
        "calculate-price": "商品价格计算",
        "create-order": "创建订单",
        "cancel-order": "取消订单",
        "query-order": "查询订单详情",
        "order-list": "查询历史订单",
        "campaign-calendar": "活动日历查询工具",
        "available-coupons": "麦麦省券列表查询",
        "auto-bind-coupons": "麦麦省一键领券",
        "query-my-coupons": "我的优惠券查询",
        "query-my-account": "我的积分查询",
        "mall-points-products": "查询麦麦商城商品列表",
        "mall-product-detail": "查询麦麦商城商品详情",
        "mall-create-order": "积分兑换商品下单",
        "now-time-info": "获取当前时间信息",
        "query-lottery-info": "查看积分抽奖活动信息",
        "draw-lottery": "积分抽奖",
        "query-my-prizes": "查看我的奖品",
        "mall-order-list": "麦麦商城订单查询",
        "mall-order-detail": "麦麦商城订单详情查询",
        "query-party-city": "主题活动城市列表查询",
        "query-party-store": "主题活动门店列表查询",
        "query-partystore-date": "主题活动可预约日期查询",
        "query-partystore-session": "主题活动可预约日期场次查询",
        "party-order-create": "主题活动订单创建",
    }

    def __init__(self, client: MCPClient):
        self.client = client

    # ------------------------------------------------------------ 内部
    def call(self, tool: str, **arguments: Any) -> Any:
        """调用任意 Tool（透传，未知参数以 None 过滤）。"""
        clean = {k: v for k, v in arguments.items() if v is not None}
        return self.client.call_tool(tool, clean)

    # ------------------------------------------------------------ 时间 / 账户
    def now_time_info(self) -> Any:
        return self.call("now-time-info")

    def query_my_account(self) -> Any:
        """我的积分账户：可用/累计/冻结/即将过期积分。"""
        return self.call("query-my-account")

    # ------------------------------------------------------------ 餐品 / 营养
    def list_nutrition_foods(self, keyword: Optional[str] = None) -> Any:
        return self.call("list-nutrition-foods", keyword=keyword)

    def query_meals(self, store_code: str, *, be_type: int = 1,
                    order_type: int = 1, **kw: Any) -> Any:
        """查询指定门店可售卖的餐品列表。

        ``storeCode``/``beType``/``orderType`` 均为必填。
          - ``beType``: 1=到店自提，5=得来速
          - ``orderType``: 1=到店自提，2=外送
        返回结构是「categories 分类 + meals 编码→详情映射表」两段式。
        """
        return self.call("query-meals", storeCode=store_code,
                         beType=be_type, orderType=order_type, **kw)

    def query_meal_detail(self, meal_code: str, store_code: Optional[str] = None, **kw: Any) -> Any:
        return self.call("query-meal-detail", mealCode=meal_code, storeCode=store_code, **kw)

    # ------------------------------------------------------------ 门店 / 配送
    def query_nearby_stores(self, keyword: Optional[str] = None, *,
                            city: Optional[str] = None,
                            be_type: int = 1, search_type: int = 2,
                            **kw: Any) -> Any:
        """查询附近门店。

        真实入参（官方 schema）：``beType`` 与 ``searchType`` 均**必填**。
          - ``beType``: 1=到店自提（返回的门店无 beCode），5=得来速车道取餐
          - ``searchType``: 1=搜索收藏餐厅（无收藏会报错），2=按位置搜索
          - ``city``/``keyword``: searchType=2 时必填，用于城市+位置关键词检索
        """
        return self.call("query-nearby-stores", keyword=keyword, city=city,
                         beType=be_type, searchType=search_type, **kw)

    def delivery_query_addresses(self) -> Any:
        return self.call("delivery-query-addresses")

    def delivery_create_address(self, **kw: Any) -> Any:
        return self.call("delivery-create-address", **kw)

    def delivery_query_stores(self, address_id: Optional[str] = None, **kw: Any) -> Any:
        return self.call("delivery-query-stores", addressId=address_id, **kw)

    def query_meal_assistance(self, store_code: Optional[str] = None, **kw: Any) -> Any:
        return self.call("query-meal-assistance", storeCode=store_code, **kw)

    # ------------------------------------------------------------ 优惠券
    def query_store_coupons(self, store_code: str, *, be_type: int = 1,
                            order_type: int = 1, **kw: Any) -> Any:
        """查询【指定门店 + 订单类型】下用户可用的优惠券。

        ``storeCode``/``beType``/``orderType`` 按官方 schema 传入。
        """
        return self.call("query-store-coupons", storeCode=store_code,
                         beType=be_type, orderType=order_type, **kw)

    def available_coupons(self, **kw: Any) -> Any:
        """麦麦省当前可领取的券（无入参，返回 Markdown 列表）。"""
        return self.call("available-coupons", **kw)

    def auto_bind_coupons(self, **kw: Any) -> Any:
        """一键领取麦麦省所有当前可领的券（无需指定 couponId）。"""
        return self.call("auto-bind-coupons", **kw)

    def query_my_coupons(self, **kw: Any) -> Any:
        """我的账户下所有可用券。"""
        return self.call("query-my-coupons", **kw)

    # ------------------------------------------------------------ 活动
    def campaign_calendar(self, specified_date: Optional[str] = None, **kw: Any) -> Any:
        """当月营销活动日历（今日 / 往期回顾 / 未来），返回 Markdown。

        ``specifiedDate`` 为可选日期（yyyy-MM-dd），不传则取当月。
        """
        return self.call("campaign-calendar", specifiedDate=specified_date, **kw)

    # ------------------------------------------------------------ 价格 / 下单
    def calculate_price(self, store_code: str, items: List[Dict[str, Any]], *,
                        be_type: int = 1, order_type: int = 1,
                        be_code: Optional[str] = None, **kw: Any) -> Any:
        """计算商品金额（含优惠）。

        真实入参要点（与直觉不同的地方）：
          - 商品数组字段名为 ``items``，**不是** ``products``
          - item 内是 ``productCode``，**不是** ``mealCode``
          - 优惠券 ``couponId`` / ``couponCode`` 挂在**单个 item 上**，不在顶层
          - ``beType`` / ``orderType`` 必填；到店自取(beType=1) 不传 beCode
          - 返回的金额字段单位为**分**，使用前需 /100 转元
        """
        return self.call("calculate-price", storeCode=store_code, items=items,
                         beType=be_type, orderType=order_type,
                         beCode=be_code, **kw)

    def create_order(self, **kw: Any) -> Any:
        return self.call("create-order", **kw)

    def cancel_order(self, order_id: Optional[str] = None, **kw: Any) -> Any:
        return self.call("cancel-order", orderId=order_id, **kw)

    def query_order(self, order_id: Optional[str] = None, **kw: Any) -> Any:
        return self.call("query-order", orderId=order_id, **kw)

    def order_list(self, **kw: Any) -> Any:
        return self.call("order-list", **kw)

    # ------------------------------------------------------------ 积分商城 / 抽奖
    def mall_points_products(self, **kw: Any) -> Any:
        return self.call("mall-points-products", **kw)

    def mall_product_detail(self, product_id: Optional[str] = None, **kw: Any) -> Any:
        return self.call("mall-product-detail", productId=product_id, **kw)

    def mall_create_order(self, **kw: Any) -> Any:
        return self.call("mall-create-order", **kw)

    def mall_order_list(self, **kw: Any) -> Any:
        return self.call("mall-order-list", **kw)

    def mall_order_detail(self, order_id: Optional[str] = None, **kw: Any) -> Any:
        return self.call("mall-order-detail", orderId=order_id, **kw)

    def query_lottery_info(self) -> Any:
        return self.call("query-lottery-info")

    def draw_lottery(self, **kw: Any) -> Any:
        return self.call("draw-lottery", **kw)

    def query_my_prizes(self, **kw: Any) -> Any:
        return self.call("query-my-prizes", **kw)

    # ------------------------------------------------------------ 主题活动
    def query_party_city(self, **kw: Any) -> Any:
        return self.call("query-party-city", **kw)

    def query_party_store(self, city_id: Optional[str] = None, **kw: Any) -> Any:
        return self.call("query-party-store", cityId=city_id, **kw)

    def query_partystore_date(self, store_id: Optional[str] = None, **kw: Any) -> Any:
        return self.call("query-partystore-date", storeId=store_id, **kw)

    def query_partystore_session(self, store_id: Optional[str] = None,
                                 date: Optional[str] = None, **kw: Any) -> Any:
        return self.call("query-partystore-session", storeId=store_id, date=date, **kw)

    def party_order_create(self, **kw: Any) -> Any:
        return self.call("party-order-create", **kw)
