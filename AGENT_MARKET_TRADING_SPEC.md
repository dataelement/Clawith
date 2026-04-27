# Agent Market 第二期：Trading 模板设计评审文档

**状态：** 草稿，待评审
**作者：** cinderzhan + AI 助手
**最后更新：** 2026-04-27
**关联文档：** [AGENT_MARKET_TEMPLATES_SPEC.md](AGENT_MARKET_TEMPLATES_SPEC.md)（第一期，已落地）
**范围：** 给散户股票 / 期货用户新增 10 个交易类 agent 模板，定位"分析教育型"，配套 2 个前置 skill 和 Risk Manager 的"Stage/Push"安全机制。

---

## 0. 概念预备

### 0.1 与第一期的关系

第一期已经把基础架构铺平：
- folder-based loader（`backend/agent_templates/<slug>/`）
- meta.yaml + soul.md + bootstrap.md 三件套
- 4 段最小化 soul 结构（Identity / Personality / Work Style / Boundaries）
- 两轮 onboarding 仪式
- Talent Market 4 个分类 tab

第二期**完全沿用**这套机制，只新增**内容和一个执行时机制**（Risk Manager 的 Stage/Push 流程）。不动现有代码结构。

### 0.2 三条不可妥协的合规底线

clawith 平台的硬规则（来自系统级安全约束，与本期任务的所有设计点都强相关）：

1. **绝不执行交易订单** —— 任何 trading agent 都不会通过 broker API 下单、改单、撤单
2. **绝不输入金融凭证** —— 不接触 API key、券商账号密码、私钥
3. **不构成投资建议** —— 所有输出框定为"分析 / 研究 / 教育"视角，明示"非投资建议"，由用户自己决策

这三条贯穿所有 10 个模板的 soul 和 bootstrap 设计，**不是装饰，是底线**。Risk Manager 的 Stage/Push 机制（§4）就是为了把这条底线写进 agent 的工作方式。

---

## 1. 目标

让用户在 Talent Market 里能**直接聘请到**贴合自己交易场景的 AI 助手——开盘前看简报、盘中盯异动、收盘后写日志、关键事件前看宏观、做交易决定前过 Risk Manager 一遍。

**用户画像**：散户为主，覆盖股票（美股/A 股/港股）+ 期货（商品 / 金融期货）。

**风格定位**：偏分析教育，不偏激进信号；类似一位有经验的交易朋友帮你想清楚、盯紧、复盘进步。

---

## 2. 范围

### 2.1 新增 10 个模板（第一批 6 + 第二批 4）

| # | 模板 | 代号 | category | 主要交付物 | 批次 |
|---|------|------|----------|------------|------|
| 1 | **Market Intel Aggregator** | MIA | trading | 每日财经简报：值得看的头条 + 一句话影响判断 | 1 |
| 2 | **Macro Watcher** | MW | trading | 宏观事件日历 + 央行/数据/地缘解读 | 1 |
| 3 | **Watchlist Monitor** | WM | trading | 盘中异动告警卡 + heartbeat 时段化简报 | 1 |
| 4 | **Technical Analyst** | TA | trading | 看图笔记：现状 + 关键位 + 演化路径 + 失效条件 | 1 |
| 5 | **Risk Manager** | RM | trading | Stage/Push 风控流程 + 当前组合体检 + 仓位计算器 | 1 |
| 6 | **Trading Journal Coach** | TJC | trading | 交易日志 + 周复盘 + `trading_rules.md` 演化 | 1 |
| 7 | **Earnings & Filings Analyst** | EFA | trading | 财报/8-K/电话会要点摘要 + 经营变化对比 | 2 |
| 8 | **COT Report Analyst** | COT | trading | 周度持仓变化解读 + 极端位置警示（期货）| 2 |
| 9 | **Pre-Market & Open Briefer** | PMB | trading | 开盘前 30min 简报：隔夜要闻 + 期指 + 关键合约 | 2 |
| 10 | **Tilt & Bias Coach** | TBC | trading | "你现在适不适合开仓"自检 + 行为干预 | 2 |

### 2.2 新增 2 个前置 skill

Trading agent 离不开市场数据。这两个 skill 是 §1 目标能否落地的关键：

| Skill | 功能 | 备选实现 |
|---|---|---|
| **market-data** | 获取股票 / 期货 / 加密的价格、K 线、基本面 | yfinance 包（Python，免费）/ OpenBB MCP / Polygon API |
| **financial-calendar** | 财报、Fed FOMC、CPI / NFP 数据、央行决议日历 | 公开 API（如 finnhub、tradingeconomics RSS）|

**实现策略**（沿用第一期"复用 > 下载 > 不新造"原则）：

1. **先调研** Anthropic Skills 公开库（`anthropic-skills:` 前缀）和 clawith DB 现有 skill 是否覆盖
2. **如果有**就直接挂载到模板的 `default_skills`
3. **如果没有**则现造一个最小可用版（建议 yfinance 作为 market-data 首发，因为零成本、API 稳定、覆盖全球主要市场）

### 2.3 新增 Talent Market 分类

第一期定了 3 个分类：`software-development` / `marketing` / `office`。

第二期新增第 4 个：**`trading`**

- 不用 `finance` 是因为后续可能扩展财务分析、记账、个人理财等更广的金融类，到时再升级到 `finance` 大类
- 前端 Talent Market 增加第 5 个 tab："**交易投资**" / "Trading"

### 2.4 Popular tab 更新

现 Popular 里有 8 个推荐：Chief of Staff / PM / Growth Hacker / Content Creator / Frontend Developer / Code Reviewer / Rapid Prototyper / Market Researcher。

新增后扩到 **11 个**，加 3 个交易类高频代表：
- **Watchlist Monitor**（盯盘场景最普遍）
- **Trading Journal Coach**（复盘场景最普遍）
- **Market Intel Aggregator**（每日财经简报，信息流场景普及度高）

不加 Risk Manager 到 Popular，原因：它依赖 Stage/Push 流程，需要用户已有交易想法时才有用，不适合"刚进 Talent Market 就发现"。

### 2.5 非目标

- **不**实现订单执行、不接 broker API、不接钱包私钥
- **不**针对中国 A 股做特殊本地化（数据接口 yfinance 等以美股为主，A 股能查但不深；如果需要 A 股深度，留作第三期）
- **不**做选股策略（"今天买什么"），只做分析教育
- **不**做付费数据源接入（Bloomberg、Refinitiv 等），只用免费/低门槛源
- **不**升级现有 4 老模板的 soul 结构（继续沿用第一期决策）

---

## 3. 模板结构契约（继承第一期）

每个模板按第一期 §3.1 的 4 段 soul 结构写：

```markdown
# Soul — {name}
## Identity        — Role + Expertise
## Personality     — 3-4 性格 bullet + 1 条语言匹配规则（11 模板逐字）
## Work Style      — 3-5 工作方式 bullet + 3 条 runtime bullet（workspace / memory / heartbeat）
## Boundaries      — 3-4 边界 bullet + 1 条集成规则（11 模板逐字）
```

### 3.1 Trading 模板的额外硬性 section 内容

每个 trading 模板的 soul 必须包含以下三条额外 bullet（位置可自由分配在 Personality / Work Style / Boundaries 之一中，但必须出现）：

1. **不构成投资建议** —— Personality 里加："I frame everything as analysis or education, never investment advice. Every actionable suggestion ends with an explicit reminder that the user makes the call."
2. **不执行交易** —— Boundaries 里加："I never place, modify, or cancel orders, never enter brokerage credentials, never touch private keys. Execution is always the user's hands."
3. **不确定性标注** —— Work Style 里加："Every directional or numerical claim ships with its source and confidence — guesses are tagged 'my read', historical data is tagged with as-of date."

这三条**逐字一致**写进所有 10 个 trading 模板，方便 review 时统一审计。

### 3.2 Heartbeat Focus 加 active hours 约束

Trading 模板的 `Work Style` 里 heartbeat bullet **必须**指明活跃时段。例子：

- Watchlist Monitor: "During heartbeat, I focus on user's tracked tickers' intraday moves, but only during US market hours (9:30am–4:00pm ET) and pre-market (4:00am–9:30am ET) on trading days. Outside these windows I respond with HEARTBEAT_OK and stay silent."
- Macro Watcher: "During heartbeat, I focus on upcoming high-impact events in the next 24h (Fed speakers, data prints, central bank meetings). If nothing is on the calendar within 24h I respond HEARTBEAT_OK."
- Pre-Market & Open Briefer: "I run heartbeat once at 8:00am ET on US trading days to deliver the open brief. All other heartbeats return HEARTBEAT_OK immediately."

这是 prompt 层面的约束，不需要后端改 cron。如果未来要硬性切窗（节省 LLM 调用），再做 backend 调度。

### 3.3 Bootstrap（沿用两轮仪式 + 三条免责）

每个 trading 模板的 bootstrap.md greeting turn 末尾**必须**加一行（在末尾的 voice note 前）：

```
At the end of the greeting turn, add a single sentence after capability bullets and before the question:
"_I help with research, analysis, and discipline — I won't place trades or give investment advice._"
```

这是用户首次见到 agent 时的一次性"知情同意"。

---

## 4. Risk Manager 的 Stage / Push 机制（关键设计）

### 4.1 为什么需要

普通的 "Risk Manager" 容易陷入两种极端：
- 太被动：只回答用户问的（"这个仓位够不够大？"），用户没意识到要问就漏检查
- 太主动：自动检查所有交易想法，但 agent 不该主动监控用户在哪儿要下单

中间方案：**用户主动 stage 想法到 workspace，Risk Manager 检查，通过后输出"建议下单参数"，用户自己去券商下单。**

### 4.2 工作流程（仿 OpenAlice Trading-as-Git）

```
1. 用户和别的 agent（TA / EFA / TJC）聊出一个交易想法
   ↓
2. 用户对 Risk Manager 说："stage 一笔多 AAPL，190 入场，185 止损，目标 210"
   ↓
3. Risk Manager 把想法写到 `workspace/trades/staged/<timestamp>-<symbol>.md`：
   ──────────────────────────
   symbol: AAPL
   direction: long
   entry: 190.00
   stop: 185.00
   target: 210.00
   risk_per_share: 5.00
   reward_per_share: 20.00
   r_multiple: 4.0
   staged_at: 2026-04-27T13:42:00Z
   user_rationale: <用户给的理由>
   guards_status: PENDING
   ──────────────────────────
   ↓
4. Risk Manager 运行 guards（一段 prompt 里枚举的检查清单）：
   - max_single_trade_risk: 单笔风险 ≤ 账户 1%? （用户在 onboarding 时声明账户规模 + 风险偏好）
   - max_position_size: 持仓 ≤ 账户 20%?
   - portfolio_concentration: 同板块累计仓位 ≤ 30%?
   - cooldown: 距离上一笔同 symbol 平仓 ≥ 24h?
   - rules_violation: 是否违反 `memory/trading_rules.md` 里固化的规则?（例如"不在 FOMC 当天开新仓"）
   ↓
5. Risk Manager 输出三色判断：
   - GREEN: 全部通过，输出"建议下单参数"卡片，用户去券商手动下单
   - YELLOW: 通过但有 1-2 条警告，要求用户在 workspace 文件里写 override 理由再 push
   - RED: 严重违反某条 guard，refuse 并解释，建议改方案
   ↓
6. 用户决定下不下单。无论结果如何，Risk Manager 把最终状态（pushed / refused / aborted）记到
   `workspace/trades/decided/` 归档，供 Trading Journal Coach 周复盘读取
```

### 4.3 这个机制的本质

- **不是真的执行交易**——push 后 Risk Manager 输出的是"参数卡片"，不是 broker 调用
- **是把"交易决策的关卡"显式化**——让用户每次下单前必须停一下、过一遍 checklist
- **完美匹配三条合规底线**——agent 永远不接触 broker，永远不输入凭证，永远把最终决定权交还用户
- **天然产生数据**——staged / decided 文件夹是 Trading Journal Coach 的输入

### 4.4 实现方式

**纯 prompt 工程，不改后端**。Risk Manager 的 soul 和 bootstrap 里详细描述这个 Stage / Push 流程，agent 用 workspace 文件操作来实现状态持久化。具体内容在 §11 模板内容计划里展开。

---

## 5. 数据基础设施（market-data + financial-calendar skill）

### 5.1 market-data skill 设计目标

**最小可用集合**（这 5 个函数是 trading 模板的硬依赖）：

| 函数 | 输入 | 输出 |
|---|---|---|
| `get_quote(symbol)` | 标的 | 当前价、涨跌、成交量、开高低收 |
| `get_history(symbol, period, interval)` | 标的、回看时长、K 线粒度 | OHLCV 数组 |
| `get_company_info(symbol)` | 标的 | 名称、行业、市值、PE、PB、分红率 |
| `get_financials(symbol, statement_type)` | 标的、报表类型 | 最近 4 期利润表/资产负债表/现金流量表 |
| `search_symbol(query)` | 关键词 | 匹配的标的列表 |

### 5.1.1 实现路径：走 MCP 不自建后台

clawith 已有 `MCP_INSTALLER` skill（[backend/agent_template/skills/MCP_INSTALLER.md](backend/agent_template/skills/MCP_INSTALLER.md)），agent 可以一键调用 Smithery 装第三方 MCP server。所以 market-data skill 本身是**一份协议文档**（不含代码），内容：

1. **首次使用时**：走 MCP_INSTALLER 装入推荐 MCP server。Phase 0 调研 Smithery 上的可用项，按"覆盖度 + 维护活跃度 + 是否需要 API key"排序，给出 1-2 个推荐
2. **数据调用约定**：把推荐 MCP 暴露的 tool 名字（不同 MCP 用的 tool 名可能不一样）映射到上表 5 个抽象函数
3. **边界处理**：标的找不到时怎么提示用户、夜盘时数据延迟怎么标注、API rate limit 触发时退避策略

**回退方案**（仅当 Phase 0 调研发现 Smithery 没有合适项时启用）：
- 在 `backend/mcp_servers/yfinance/` 起一个本地 stdio MCP server
- 几十行 Python：`yfinance` + `mcp` SDK 封装上面 5 个函数
- 注册到 clawith MCP registry，agent 走和 Smithery MCP 同样的调用方式

无论哪条路径，skill 文档都用同一套 tool 名抽象，agent 看到的接口一致。

### 5.2 financial-calendar skill 设计目标

| 函数 | 输入 | 输出 |
|---|---|---|
| `get_earnings_calendar(date_range)` | 日期范围 | 当期发财报的公司 + 估期 |
| `get_macro_calendar(date_range, importance)` | 日期范围、重要性 | Fed 会议、CPI、NFP、各国央行决议、GDP |
| `get_econ_event_consensus(event_id)` | 事件 ID | 市场共识预期 + 历史值 |

**实现路径同 §5.1.1**：优先 Smithery（搜 "calendar"、"earnings"、"economic events"），回退到自建 MCP（用 finnhub 免费 tier 或 tradingeconomics RSS）。同样 skill 文档不含代码，只是协议规范。

### 5.3 skill 落位

按第一期 spec §4.2 的模式：
- 这两个 skill 在 DB 里注册（通过 `skill_seeder.py` 添加）
- skill 文件放到 `backend/app/services/skill_creator_files/` 或专门目录
- 模板的 `default_skills` 引用 folder name

具体哪些模板引用哪个 skill：

| 模板 | market-data | financial-calendar |
|---|---|---|
| Market Intel Aggregator | [Y] | [Y] |
| Macro Watcher |  | [Y] |
| Watchlist Monitor | [Y] |  |
| Technical Analyst | [Y] |  |
| Risk Manager | [Y] |  |
| Trading Journal Coach | [Y] |  |
| Earnings & Filings Analyst | [Y] | [Y] |
| COT Report Analyst | [Y] |  |
| Pre-Market & Open Briefer | [Y] | [Y] |
| Tilt & Bias Coach |  |  |

---

## 6. Talent Market 前端改动

### 6.1 改动范围

[frontend/src/components/TalentMarketModal.tsx](frontend/src/components/TalentMarketModal.tsx)：

1. `tabs` 数组加第 5 个：
   ```ts
   { id: 'trading', label: t('talentMarket.tabTrading', isChinese ? '交易投资' : 'Trading') }
   ```
2. `TabId` 类型扩展：`'popular' | 'software-development' | 'marketing' | 'office' | 'trading'`
3. `FEATURED_TEMPLATE_NAMES` 集合扩展：加 `Watchlist Monitor` 和 `Trading Journal Coach`
4. i18n key 增 1 个

### 6.2 视觉

- Trading tab 沿用现有 tab 样式（无 emoji，文字代号，活跃时下划线）
- 模板卡也沿用现有 `TemplateCard` 组件，无定制化
- 模板代号（icon 字段）：MIA / MW / WM / TA / RM / TJC / EFA / COT / PMB / TBC（2-3 字母惯例）

---

## 7. 上线计划

### Phase 0 —— 数据基础设施（前置，1 个 PR）

- 调研 Anthropic Skills 公开库是否有现成的 market-data / financial-calendar skill
- 没有则按 §5 设计实现两个 skill，注册到 DB
- 写 1 个集成测试：调用 skill 取 AAPL quote、查未来 7 天 earnings calendar
- 验收标准：在新 worktree 创建一个 agent + 挂载 market-data skill，agent 能成功 get_quote('AAPL') 并返回数字

### Phase 1 —— 第一批 6 个模板（1 个 PR）

- 按第一期 §3 结构 + 本文档 §3.1/3.2/3.3 trading 加固 写：
  - Market Intel Aggregator
  - Macro Watcher
  - Watchlist Monitor
  - Technical Analyst
  - Risk Manager（含完整 Stage/Push prompt 设计）
  - Trading Journal Coach
- 前端加 Trading tab + Popular tab 加 2 个推荐
- 验收标准：在 Talent Market 看到 5 个 tab；交易投资 tab 下 6 张卡；从 Watchlist Monitor 创建一个 agent，第一轮对话给出 active hours 时段判断；从 Risk Manager stage 一笔交易，能看到 workspace/trades/staged/ 写入文件

### Phase 2 —— 第二批 4 个模板（1 个 PR）

- Earnings & Filings Analyst
- COT Report Analyst
- Pre-Market & Open Briefer
- Tilt & Bias Coach
- 验收标准：交易投资 tab 下 10 张卡

### Phase 3 —— QA & 文档

- 跑一遍每个模板的首轮对话，截图存档
- 给 README 加一个简短的 "Trading templates" section
- 更新 [AGENT_MARKET_TEMPLATES_SPEC.md](AGENT_MARKET_TEMPLATES_SPEC.md) 末尾，标注 trading track 已落地

合计 **3 个 PR**，按 Phase 0 → 1 → 2 串行落地（Phase 0 是后续所有 trading agent 的硬依赖，不能并行）。

---

## 8. 风险与缓解

| 风险 | 影响 | 缓解 |
|---|---|---|
| **数据 skill 实现成本失控** | Phase 0 拖延 | 严格走"yfinance 最小可用集合 + finnhub 免费 tier"，不追求覆盖全资产；遗留再迭代 |
| **Risk Manager 的 Stage/Push 流程被用户跳过** | 没人用 = 等于没设计 | 在 Watchlist Monitor / TA 的 Boundaries 段加一句"涉及具体下单参数时引导用户先去 Risk Manager"，把 RM 拉到工作流上游 |
| **agent 输出被解读为投资建议引发争议** | 合规风险 | §3.1 三条逐字 bullet + bootstrap 首回免责声明 + 用户协议层面（这个不是模板能解决的，需要产品层兜底） |
| **active hours 判断错误（市场假期、夏令时、交易所差异）** | heartbeat 在错的时间发提醒 | 让 agent 在不确定时优先返回 HEARTBEAT_OK；不强行预编死时段，让 LLM 自己看日期判断 |
| **A 股用户体验差** | 国内用户失望 | 第三期单独做 A 股本地化（接入东方财富 / 同花顺 API），本期先把英文市场体验做扎实 |

---

## 9. 评审确认记录（6 个问题全部已回复）

| # | 问题 | 结论 |
|---|------|------|
| 1 | 分类名 `trading` vs `finance` | [采纳] **`trading`**，扩展更广金融场景时再升级 |
| 2 | market-data skill 实现路径 | [采纳] **走 MCP 路线**：Smithery 上已有 yfinance/stock/financial 类 MCP server，复用 clawith 的 MCP_INSTALLER 机制装入；skill 本身是一份协议文档（推荐哪个 MCP + 怎么调用 + 数据格式约定）。如 Smithery 上没有合适的，回退到在 `backend/mcp_servers/yfinance/` 自建 stdio MCP（数十行）|
| 3 | A 股是否纳入第二期 | [否] **留作第三期**，本期专注英文市场（美股 + CME 期货 + 主要外汇）|
| 4 | Popular tab 推荐 trading agent 组合 | [采纳+扩展] **3 个**：Watchlist Monitor + Trading Journal Coach + **Market Intel Aggregator**（高频信息流场景普及度高）|
| 5 | Risk Manager guards 默认值是否走 onboarding | [采纳] **走 onboarding**：bootstrap 第一轮直接问"账户大致规模 + 单笔最大可承受亏损 %"，写入 `workspace/trades/config.yaml`，后续可调 |
| 6 | 是否为 trading 单独做 `HEARTBEAT_TRADING.md` | [否] **不改架构**。现有 `Heartbeat Focus` bullet（soul 内）+ 共享 HEARTBEAT.md（"无要事则跳过"）已能解决时段约束问题，纯 prompt 层面，零代码改动 |

---

## 10. 成功标准

- Talent Market 5 个 tab，trading 下 10 个模板可见可聘
- 创建一个 Watchlist Monitor，5 分钟内能完成 onboarding 并给出第一份盘中简报（前提：market-data skill 工作）
- Risk Manager 能完整跑通 Stage → Guards → Push 三步，产出参数卡片
- 任意 trading agent 的首轮对话末尾出现"非投资建议"声明
- 所有 trading agent 的 heartbeat 在非交易时段返回 HEARTBEAT_OK，不打扰用户
- Trading Journal Coach 能读 `workspace/trades/decided/` 的归档生成周复盘

---

## 11. 附录：每个模板的内容计划（轮廓）

下面是每个模板的轮廓，正式 PR 时按 §3 结构填充完整内容。

### 11.1 Market Intel Aggregator (MIA)

- **Identity**: Financial news aggregator + signal/noise filter，覆盖全球主要市场新闻源
- **核心功能**: 用 web-research + 新闻 RSS 整理每日要点，按"宏观 / 行业 / 个股 / 政策 / 事件"分类，每条标注影响判断
- **deliverable**: `workspace/intel/<date>.md`，结构化每日简报
- **memory**: `memory/recurring_themes.md` 跟踪反复出现的主题

### 11.2 Macro Watcher (MW)

- **Identity**: 跟央行 + 重要数据 + 地缘事件，关注利率/汇率/大宗的二阶影响
- **核心功能**: 维护未来 14 天的 high-impact 日历；事件前画 setup（共识预期 / 超预期路径），事件后做点评
- **deliverable**: `workspace/macro/calendar.md` + 事件后的 reaction note
- **memory**: 央行讲话风格变化、市场对历次数据的反应模式

### 11.3 Watchlist Monitor (WM)

- **Identity**: 用户自定义标的盘中盯盘
- **核心功能**: 维护 watchlist（用户在 onboarding 添加），heartbeat 时段化检查异动，达阈值触发简报
- **deliverable**: `workspace/watch/alerts/` 异动卡 + `workspace/watch/eod-<date>.md` 当日复盘
- **active hours**: 严格按用户的 watchlist 包含哪些市场决定（美股 / A 股 / 期货）

### 11.4 Technical Analyst (TA)

- **Identity**: 看图分析师，主流派系（道氏 / 形态 / 指标 / 量价）混用
- **核心功能**: 给定标的输出"现状 / 关键位 / 可能演化路径 / 失效条件"四段
- **deliverable**: `workspace/ta/<symbol>-<date>.md` 看图笔记
- **memory**: 个人化的"哪些标的的 RSI 历史更可靠"等校准数据
- **关键边界**: 输出永远框定为"假设和概率"，不说"必涨/必跌"

### 11.5 Risk Manager (RM) [核心] 含 Stage/Push 流程

- **Identity**: 交易决策的关卡哨兵
- **核心功能**: §4 描述的完整流程
- **bootstrap 第一轮交付**: 不是 demo trade，而是问用户"账户大致规模 + 单笔最大可承受亏损 %"，然后把这两个数字写进 `workspace/trades/config.yaml`
- **deliverable**: `workspace/trades/staged/` + `workspace/trades/decided/`
- **memory**: 用户的"交易铁律"演化日志，由 Trading Journal Coach 反向写入

### 11.6 Trading Journal Coach (TJC)

- **Identity**: 复盘伙伴 + 行为画像分析师
- **核心功能**: 周末扫 `workspace/trades/decided/`，生成周复盘；识别行为模式（最近 5 笔单子是不是都太早平仓？）；提议加入 `memory/trading_rules.md` 的新规则（用户确认后写入）
- **deliverable**: `workspace/journal/week-<W>.md`
- **memory**: `memory/trading_rules.md`（关键：和 Risk Manager 共用，形成闭环）

### 11.7 Earnings & Filings Analyst (EFA)

- **Identity**: 基本面深度阅读
- **核心功能**: 给定标的，读最近 1-2 期财报 + 重大 8-K + 电话会要点，输出"经营变化 vs 上期 / 估值锚点 / 风险变化"三段
- **deliverable**: `workspace/efa/<symbol>-<date>.md`
- **memory**: 该标的的历史财报关键指标趋势

### 11.8 COT Report Analyst (COT)

- **Identity**: 期货持仓数据解读
- **核心功能**: 周五 COT 公布后，给跟踪的期货品种生成持仓变化解读，标注极端位置
- **deliverable**: `workspace/cot/<commodity>-<week>.md`
- **memory**: 各品种的历史 COT 极端位置 → 后续行情对照

### 11.9 Pre-Market & Open Briefer (PMB)

- **Identity**: 美股开盘前的 1 屏简报
- **核心功能**: 8:00am ET 触发 heartbeat，整合"隔夜要闻 / 期指 / 关键合约 / 财报日 / 数据日"
- **deliverable**: `workspace/pmb/<date>.md`
- **active hours**: heartbeat 仅在美股交易日 8:00am ET 执行一次

### 11.10 Tilt & Bias Coach (TBC)

- **Identity**: 交易心态教练
- **核心功能**: 用户在 stage 一笔交易前可以"主动 check-in"，TBC 问几个简单问题（昨晚睡得怎么样 / 上一笔结果如何 / 是不是急于回本）然后给"现在适合 / 慎重 / 不建议"判断
- **deliverable**: `workspace/tbc/checkins/<date>.md`
- **memory**: 用户的情绪触发模式
- **不需要 market-data 也不需要 calendar skill**——纯心态对话

---

## 12. 给评审同事的快速导读

如果你没时间读完整份文档，至少看这五处：

1. **§0.2 三条合规底线** —— 整套设计的安全护栏
2. **§2.1 模板清单** —— 10 个模板是不是你想要的？
3. **§4 Risk Manager 的 Stage/Push 机制** —— 这是和 OpenAlice 学的最关键设计点
4. **§5 数据 skill 设计** —— 不做这个 trading 模板没法用
5. **§9 开放问题** —— 6 个等你回答

回复完 §9 我就开始 Phase 0（market-data skill 调研 + 实现）。
