# WorkBuddy 开发对话上下文

> 本文件由参赛者使用腾讯 WorkBuddy 开发「麦麦省钱雷达」过程中的真实对话上下文整理导出，
> 用于核验本项目符合本次大赛的 WorkBuddy 联动活动奖励条件。
>
> 开发日期：2026-10-09（活动首日）
> 使用工具：腾讯 WorkBuddy（Agent 模式，含本地文件读写、命令执行、联网检索能力）

---

## 轮次 1 · 投喂赛事链接，明确任务边界

**用户**：

> https://mp.weixin.qq.com/s/MUWVx9D78zM-FSADLXfGLw
> 看一下这个比赛，帮我准备一下参赛的资料。
> 开发完的东西，记得建立专题跟项目，保存到"知易知识专题库"里，并成为卡片

**WorkBuddy 的处理**：
公众号正文是图片形式，先抓 HTML 后提取正文图片并逐张读图，得到赛事概览：
1024 程序员节 · **麦当劳程序员创意开发大赛**，2026-10-09 10:30 ~ 10-25 23:59，
奖项为巨无霸免费兑换券 + WorkBuddy AI 积分。

随后定位到官方赛事仓库（`M-China/mcd-developer-innovation-challenge`）与
MCP 仓库（`M-China/mcd-mcp-server`），拉取并通读四份规则文档：
`README.md` / `activityGuidelines.md` / `RANKING.md` / `CONTEST_DECLARATION.md`。

**确认到的硬性约束**：
- 排名依据 = GitHub 项目公开 Star 数（10-26 00:00 定榜）
- 仓库必须包含 README.md、CONTEST_DECLARATION.md（不可改）、MCP_INTEGRATION.md、
  mcp-config.example.json、源代码
- 参加 WorkBuddy 专项奖励需额外提交 `workbuddy.md`

---

## 轮次 2 · 三个关键决策点

WorkBuddy 提出三个必须由参赛者拍板的问题（避免在错误方向上浪费开发量）：

1. **作品方向** —— 给出四个候选及其打分逻辑（MCP 工具覆盖面 / 使用频次 / 传播性 / 竞争激烈度）。
2. **GitHub 发布方式** —— 因为检测到本机 git 依赖的 Xcode CLT 未安装、且没有 `gh` CLI。
3. **是否已有 MCP Token** —— 决定能否做真实端到端联调。

**参赛者的决策**：

| 问题 | 选择 |
|---|---|
| 作品方向 | **麦麦省钱雷达**（自动领券 + 券到期预警 + 门店比价 + 活动日历预警） |
| 发布方式 | 使用自己的仓库 `https://gitee.com/anbeime/mc.git` |
| MCP Token | 暂无，按官方文档契约开发 |

> 方向选择理由：希望做一个**高频、真实有用**的东西，而不是演示性质的玩具。
> 优惠券会过期这件事，几乎每个麦当劳用户都吃过亏。

---

## 轮次 3 · 方案设计

WorkBuddy 给出的技术方案要点（已落地）：

- **形态**：一个零第三方依赖的 Python 包 + CLI，外加一个 WorkBuddy Skill 封装
- **核心竞争力**：不是"能查券"，而是**「薅 → 盘 → 算 → 哨」四件事串成一条闭环**
- **差异化设计**：
  1. `auto-bind-coupons` 不返回明细 → 用「领券前/后各查一次做 diff」把"新领了几张"显性化
  2. 券有适用范围 → 推荐组合时把券的 scope 一并展示，不替用户假设场景
  3. 无 Token 也要能被评审验证 → 同时提供 Mock Server 与离线示例生成器

---

## 轮次 4 · 开发实现

WorkBuddy 依次产出：

```
src/mcd_radar/mcp_client.py   # 零依赖 MCP Streamable HTTP 客户端（JSON + SSE 双解析）
src/mcd_radar/tools.py        # 33 个 MCP Tool 的语义化封装
src/mcd_radar/models.py       # 数据模型 + 模糊字段解析层
src/mcd_radar/strategies.py   # 省钱策略引擎（薅/盘/算/哨 + 本地估算兜底）
src/mcd_radar/report.py       # 自包含 HTML 省钱报告
src/mcd_radar/cli.py          # CLI（scan / bind / coupons / deals / tools）
tests/mock_server.py          # 复刻 MCP 契约的 Mock Server
tests/smoke_test.py           # 28 项端到端断言
examples/generate_demo.py     # 离线示例报告生成
```

---

## 轮次 5 · 调试过程中被真实解决的问题

这些都是 WorkBuddy 在联调阶段实际发现并修掉的缺陷，记录在此以佐证开发过程的真实性：

### ① 返回结构解析不全 —— 只能解析出 1 张券

初版 `iter_payloads` 的包裹键白名单里没有 `coupons` / `stores` / `meals` 等业务键，
导致 `{"coupons": [...]}` 这种返回被当成"单条记录"，6 张券只解析出 1 张、门店名显示为空。
**修复**：扩展包裹键白名单 + 增加"取第一个 list 值"的兜底分支。

### ② `calculate-price` 参数名不匹配 —— 静默走了本地估算

`McDonaldTools.calculate_price(store_code, products)` 是语义化的 snake_case 签名，
而调用处按 MCP 原始的 `storeCode` 传参，触发 `TypeError` 被 `except` 吞掉，
表现为"官方试算永远不生效"。**修复**：改走原始 Tool 名调用，并保留语义化包装作为公开 API。

### ③ 官方试算覆盖了更优的本地估算 —— 把"省 10 元"显示成"省 0 元"

Mock 场景下 `calculate-price` 未识别到券，返回原价。初版逻辑无条件采纳官方结果，
于是出现「应付 21.5 元、原价 21.5 元、省 0 元、用券：新客首单减 10 元」这种自相矛盾的推荐。
**修复**：加入"官方试算若未体现任何优惠则不覆盖本地结论"的保守回退策略。

### ④ 本机代理导致连不上本地服务

开发机装有接管 localhost 的系统级 HTTP 代理，`urllib` 会因此把本地请求也发往代理，
报 `Connection refused`。**修复**：客户端识别本地地址后显式禁用代理
（这同时也是一个对使用者有价值的能力，已写进 MCP_INTEGRATION.md）。

### ⑤ 后台进程被回收

初次用 `nohup ... &` 启动 Mock Server，进程很快被环境回收，导致联调时"服务莫名连不上"。
**修复**：改用受管的后台任务方式启动，并把端口管理下沉到测试脚本内部
（`smoke_test.py` 在进程内起临时端口，彻底消除人工管理端口的负担）。

---

## 轮次 6 · 验证

```
$ python tests/smoke_test.py
🍟 麦麦省钱雷达 · 冒烟测试
=== 传输格式：JSON ===
  PASS tools/list 返回 33 个 Tool
  PASS query-my-coupons 解析出 6 张券
  PASS 3 天内到期券 = 3 张
  PASS 已过期券 = 1 张
  PASS 一键领券识别出 2 张新券
  PASS 扫到门店 / 拿到菜单 / 有推荐组合
  PASS 推荐均已用券并省钱
  PASS 识别到进行中的活动
  PASS 读到积分账户
  PASS HTML 报告含券预警区块 / 含推荐组合 / 自包含
=== 传输格式：SSE ===
  （同上 14 项全部通过）

结果：通过 28 项，失败 0 项
```

CLI 实测输出（对着 Mock Server）：

```
🍟 麦麦省钱雷达 · 扫描结果
   门店：麦当劳·科技园店 深圳市南山区科技园南区 1 号
──────────────────────────────────────────────
  可用券      7 张（在效面额约 51.5 元）
  快过期      3 张，涉及面额 17.5 元 —— 别放过期
  我的积分    1,860（320 即将过期）
`$ python -m mcd_radar --address 科技园 --city 深圳 --budget 40`（真实账号）：

```
🍟 麦麦省钱雷达 · 扫描结果
   门店：麦当劳深圳同方信息港餐厅 科技园北区朗山路11号同方信息港E栋G层5号商铺
────────────────────────────────────────────────────────────
  可用券      8 张（其中 8 张为指定商品优惠价）
  快过期      5 张，3 天内到期 —— 别放过期
  我的积分    0

  ⏰ 券到期预警
     • 麦旋风任选  用券价¥9.9  [今天到期]
     • 薯薯任选  用券价¥9.9  [今天到期]
     • 巧克力味厚松饼猪柳蛋套餐  用券价¥29.9  [1 天后到期]

  🎯 今日最省组合   （合计省 19.3 元）
     1. 麦咖啡™美式      应付 9.9  原价 19  省 9.1  （官方试算）
     2. 薯薯任选         应付 9.9  原价 16  省 6.1  （官方试算）
     3. 麦旋风任选       应付 9.9  原价 14  省 4.1  （官方试算）
```

---

## 轮次 7 · 参赛资料与知识归档

WorkBuddy 完成：
- 参赛交付文件：README / MCP_INTEGRATION / mcp-config.example.json / workbuddy.md /
  CONTEST_DECLARATION（官方原文，校验和比对一致后放入）
- 视觉素材：手写 SVG banner + 用 headless Chrome 渲染的省钱报告截图
- 报名 Issue 文案
- 按参赛者要求，把整个项目沉淀为「知易知识专题库」里的一个专题与若干四色卡片

---

## 轮次 8 · 拿到真实 Token，做真机联调（推翻并重写了解析层）

参赛者提供了 MCP Token，第一次打通官方线上接口。**结论：按文档写的假设基本全错，
之前那套「解析 JSON」的实现在线上会直接失效。** 主要差异：

| 项 | 先前的假设 | 真实情况 |
|---|---|---|
| 返回格式 | 结构化 JSON | 「自然语言说明 + 贴一段数据」，共四种形态，且同一接口在不同状态下还会变 |
| Tool 数量 | 33（文档所列） | **35**（已有增补，仍在演进） |
| `calculate-price` 入参 | `products[].mealCode` | **`items[].productCode`** |
| 优惠券入参 | 顶层 `couponId` | 挂在 item 上，且 `couponId` **必须**与 `couponCode` 成对（否则报 600010） |
| `query-meals` 入参 | 只要 `storeCode` | 还需必填 `beType` + `orderType` |
| 找门店 | `address` | 需 `city` + `keyword`，且 `searchType=2` |
| 金额单位 | 元 | **分**，必须 /100 |
| 券的类型 | 面额立减 | 绝大多数是「**用券价格**」型（按 ¥9.9 成交），不是减多少 |

对应的改造：

1. **重写解析层**：新增「括号平衡扫描 + 候选打分」的 JSON 提取器，并为 `available-coupons` /
   `campaign-calendar` / `query-my-coupons` 各写了 Markdown 解析器。
   券包接口尤其麻烦 —— **空的时候回一句话，有券的时候回 Markdown，两者都不是 JSON**。
2. **新增 `fixed_price` 字段**与 `discount` 并列，UI 分别展示，避免把「花 9.9 元」误报成「省 9.9 元」。
3. **新增 `merge_official_coupons()`**：把 Markdown 券包与 JSON 门店券按券名合体，
   后者才有真实 `couponId` —— **没有它官方试算调不动**。
4. 修正 `tools.py` 全部相关签名，对齐真实必填入参。

踩到的一个隐蔽 bug：解析活动日历时正则写成了 `\s*`（会**吃掉换行**），
导致下一行内容被误判为日期后缀，12 组日期只剩下 3 组日期有效。改成 `[ \t]*` 后修复。

回归验证：`tests/smoke_test.py` 在 JSON / SSE 双传输下**28 项断言仍全绿**，
说明 Mock（JSON 形态）与真实（Markdown 形态）两条路径都覆盖到了。

---

## 说明

- 本项目全部代码在 WorkBuddy 会话中生成。
- 先用严格复刻契约的 Mock Server 做端到端验证；参赛者提供 Token 后又**完整对接了官方线上接口真跑一遍**，
  并据此重写了解析层与入参封装。
- 凭据安全：Token 仅从环境变量 `MCD_MCP_TOKEN` 读取，`.gitignore` 已排除 `.env`，全程未进入任何提交。
- 所有线上交互**仅限读操作与领券**，未调用下单、支付等写操作接口。
