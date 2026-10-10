# MCP 接入说明

本文说明「麦麦省钱雷达」如何真实使用麦当劳中国的 MCP 能力：接的是哪个 Server、调了哪些 Tool、调用顺序为什么这样设计、解决什么业务问题。

---

## 1. 接入的 MCP Server

| 项 | 值 |
|---|---|
| Server 地址 | `https://mcp.mcd.cn` |
| 提供方 | 麦当劳中国（金拱门） |
| 传输协议 | Streamable HTTP |
| 鉴权方式 | 请求头 `Authorization: Bearer <MCP_TOKEN>` |
| 协议版本 | MCP `2024-11-05` |
| 官方文档 | https://github.com/M-China/mcd-mcp-server |
| 覆盖版本 | Tool 列表对齐官方 v1.0.9（2026-09-10） |
| 限流 | 每 Token 每分钟 600 次请求（超出返回 429） |

Token 申请入口：https://open.mcd.cn/mcp → 登录 → 控制台 → 激活。

### 客户端接入方式

本项目**自带 MCP 客户端实现**（`src/mcd_radar/mcp_client.py`），只用 Python 标准库完成
`initialize` → `tools/list` → `tools/call` 全流程，并同时兼容服务端返回
`application/json` 与 `text/event-stream`(SSE) 两种响应体。

因此它可以两种姿势使用：

1. **作为独立 CLI**：直接 `python -m mcd_radar`，自己完成 MCP 握手；
2. **作为 WorkBuddy / Cherry Studio / Cursor 等宿主里的 Skill**：宿主负责挂载 MCP，
   本项目的 Skill 定义（`skills/mcd-savings-radar/SKILL.md`）负责编排策略。

---

## 2. 实际调用的 Tool 及其角色

本项目**真实调用**以下 10 个 Tool。括号内是调用时机。

> 以下为**真机实测**结果（2026-10-09，线上 35 个 Tool），非文档推测。

| Tool | 业务角色 | 真实入参（`*` 为必填） | 返回形态 → 取什么 |
|---|---|---|---|
| `query-my-coupons` | 拉取账户券包 | `page` / `pageSize`（可选） | **Markdown**：券名 / 用券价 / 有效期区间 / 标签 |
| `auto-bind-coupons` | 一键领券 | 无 | 纯文本成功与否（不返回明细） |
| `available-coupons` | 还能领什么 | 无 | **Markdown** 列表：标题 / 状态 / 图片 |
| `query-store-coupons` | 门店真正可用的券 | `storeCode`* `beType`* `orderType`* | **文本+JSON**：`couponId` / `couponCode` / `products[]` |
| `query-nearby-stores` | 定位门店 | `beType`* `searchType`* `city` `keyword` | **文本+JSON**：`storeCode` / `storeName` / `distance` |
| `query-meals` | 门店菜单 | `storeCode`* `beType`* `orderType`* | **JSON**：`categories[]` + `meals` 编码→详情映射 |
| `calculate-price` | 官方口径试算 | `storeCode`* `items[]`* `beType`* `orderType`* | **JSON**：`originalPrice` / `price` / `discount`（**单位：分**） |
| `campaign-calendar` | 活动日历 | `specifiedDate`（可选） | **Markdown**：按日期分组的活动 |
| `query-my-account` | 积分账户 | 无 | **文本+JSON**：`availablePoint` / `accumulativePoint` |
| `now-time-info` | 校准时间 | 无 | **JSON**：`datetime` / `formatted` / `timestamp` |

> 其余 25 个 Tool（下单、商城兑换、抽奖、主题活动预约等）已在 `tools.py` 中完整封装，
> 供后续扩展使用，但**本次参赛版本未纳入主流程**，以避免在未充分验证的情况下调用写操作类接口。

### 三个不看 samples 就一定会踩的坑

1. **`calculate-price` 用的是 `items`，不是 `products`**；项内商品编码叫 `productCode`，不是 `mealCode`。

2. **优惠券必须 `couponId` + `couponCode` 成对传入**，只给其一直接报错：
   `{"success":false,"code":600010,"message":"使用优惠券需要couponId和couponCode"}`

3. **金额单位是「分」**，展示前必须 `/100`。例：冰美式 `originalPrice=1900`, `price=990` → 原价 19 元、实付 9.9 元。

此外 `query-nearby-stores` 的 `searchType=2`（按位置搜索）**必须搭配 `city` 使用**，
只给关键词会返回「收藏餐厅列表为空」。

---

## 3. 端到端调用流程

一次 `python -m mcd_radar --address 科技园 --city 深圳 --budget 40 --bind` 的真实调用序列：

```
① initialize                              ← 握手，协商协议版本，拿到 Session
② [--bind] auto-bind-coupons              ← 一键领取麦麦省所有可领券
③ query-my-coupons                        ← 券包（Markdown）：到期日 / 用券价 / 适用时段
④ query-nearby-stores(city+keyword)       ← 定位门店，取最近一家的 storeCode
⑤ query-meals(storeCode)                  ← 门店菜单（categories + meals 映射表）
⑥ query-store-coupons(storeCode)          ← 门店可用券（JSON，含真实 couponId）
   └ 合并 ③⑥ ─ 券名配对，给 ③ 的券补上「券芯」与适用商品
⑦ calculate-price × N                     ← 逐张券做官方试算，拿真实实付与优惠
                                             N ≤ 24（硬上限，防限流）
⑧ campaign-calendar                       ← 活动雷达数据
⑨ query-my-account                        ← 积分与即将过期积分
⑩ 本地组装 Snapshot → 终端摘要 / HTML 报告
```

### 关键设计点

- **⑥→⑦ 是「算最优」能拿到真实价格的前提。**
  券包接口（Markdown）只告诉你是哪张券、什么时候过期，**却不给 `couponId`**；
  门店券接口（JSON）才给出真实券芯和适用商品编码。
  `merge_official_coupons()` 按券名把两者拼合 —— **少了这一步，官方试算根本调不动**，只能退回本地瞎估。

- **⑦ 拿到的优惠金额是官方口径的真实数字。**
  不再依赖猜测。实测样例（同一家门店、同一时刻）：

  | 券 | 原价 | 实付 | 省下 |
  |---|---|---|---|
  | 9.9元中杯冰美式 | ¥19.0 | ¥9.9 | **¥9.1** |
  | 薯薯任选 | ¥16.0 | ¥9.9 | **¥6.1** |
  | 麦旋风任选 | ¥14.0 | ¥9.9 | **¥4.1** |

- **并发症 menu 与券商品不是同一套编码。** 券绑定的往往是专属商品（如「麦旋风任选1」），
  单独查价会返回 0，因为不在当前时段的可售列表里；但**带上券一起试算就能正常出价**。
  代码里因此以「带券试算」为主，并在原价缺失时用 `实付 + 优惠` 反推。

- **⑦ 的上限是刻意的。** 官方限制 600 次/分钟，组合试算按 24 次封顶；
  未指定门店或试算异常时直接走本地估算，保证功能不中断。

- **降级不回退原则**：若官方试算完全没有体现优惠，程序不会用它覆盖本地已有结论 ——
  避免把明显能省的组合推荐成「省 0 元」。

---

## 4. 业务价值

| 面向 | 价值 |
|---|---|
| 普通消费者 | 券不再放过期；点餐前 5 秒知道"今天怎么点最省"；积分到期有预警 |
| 高频麦门用户 | 一键领券省掉逐张点击；活动日历提前掌握发券与新品节点 |
| 门店 / 品牌侧 | 提升券的核销率与门店到访频次，让营销投放真正触达用户 |
| 开发者 | 一个可直接复用的「MCP Streamable HTTP 零依赖客户端 + Tool 语义化封装」参考实现 |

一句话总结业务价值：**把麦当劳已经发给用户的优惠，真正变成用户口袋里的钱。**

---

## 5. 健壮性与合规

- **鉴权失败**（401/403）：抛出 `MCPAuthError`，CLI 给出明确的 Token 检查指引，不静默失败。
- **限流**（429）：按指数退避重试，最多 3 次。
- **写操作边界**：主流程**只调用只读类 Tool**（查询/试算）；`create-order`、
  `mall-create-order`、`party-order-create` 等下单类接口不参与自动流程，避免误下单。
- **Token 安全**：仅从环境变量 `MCD_MCP_TOKEN` 读取；仓库内配置示例只含占位符；
  `.gitignore` 已排除 `.env` 与任何本地凭据文件。
- **代理兼容**：自动识别本地地址（`127.0.0.1` / `localhost`）并绕过系统 HTTP 代理 ——
  很多开发机上装有接管 localhost 的代理，会导致本地 mock / 本地 MCP 连不上。
