# Agent Market 模板设计评审文档

**状态：** 草稿，待评审
**作者：** cinderzhan + AI 助手
**最后更新：** 2026-04-24
**范围：** 将 clawith 内置 `AgentTemplate` 模板从 4 个扩展到 15 个，按三个面向用户的场景组织：软件开发、社媒增长、综合私人助理。

---

## 0. 概念预备

### 0.1 什么是 clawith runtime

在 clawith 里，一个 agent 不是一段 prompt，而是一个**长期存在的数字员工**。它的"身体"由以下组件构成（合起来称为 runtime）：

| 组件 | 角色 |
|------|------|
| `soul.md` | 身份、人格、规则 |
| `state.json` | 当前状态（在干啥、忙不忙） |
| `workspace/` | 工作台（任务计划、中间草稿、产出物） |
| `memory/` | 跨对话的长期记忆（知识沉淀、踩过的坑） |
| `skills/` | 可调用的能力包 |
| `HEARTBEAT.md` | 心跳机制，让 agent 周期性自主醒来 |
| DB 里的 AgentTool / AgentPermission | 工具绑定、权限策略 |

**与普通 prompt 库的区别**：普通 prompt 用完一次就消失；clawith agent 对话结束不消失，会继续定期醒来、积累记忆、把产物沉淀到工作区。模板设计必须利用这套基础设施，否则 agent 就退化成一次性 prompt。

### 0.2 架构决策：soul 走方案 A（最小扩展），bootstrap 走两轮仪式

经过对"是否新增 6 个 section"的再评估（外部素材仓库那套 10 段结构是为无状态 prompt 设计的，clawith 有 skill/heartbeat/memory 提供的 runtime 支撑，大部分新 section 是冗余），最终选择**方案 A**：

**Soul：保持和现有 4 模板一致的 4 段结构**（Identity / Personality / Work Style / Boundaries），只在 Work Style 里加 3 条必选 bullet，覆盖 clawith runtime 的关键使用方式：

1. workspace 用法：何时把计划/草稿/交付物写到 `workspace/<task>/`
2. memory 用法：什么值得沉淀到 `memory/<topic>.md`
3. heartbeat 方向：心跳醒来时关注什么（一行，不单开 section）

长度：比现有 4 模板多 5-8 行，风格完全一致，不需要升级老模板。

**Bootstrap：对齐主分支（`yutong/agent-templates`）的两轮仪式模型**，`bootstrap_content` 是一段系统提示词（不是文件），由 `onboarding.py` 在首位用户打开该 agent 聊天时注入：

- Turn 0（user_turns == 0）：打招呼 + 2-3 条能力要点 + 问 1 个紧扣角色的问题
- Turn 1+（user_turns >= 1）：直接按用户回答开始产出，不再追问上下文

详见 §3.2 新 bootstrap 模板。

---

## 1. 目标

让 Agent Market 开箱即用——在现有 4 个模板（PM / Designer / Product Intern / Market Researcher）基础上新增 11 个高频、全球通用的模板。

每一个模板必须是**完整配置好的数字员工**，不是一段 system prompt——也就是说要附带 soul、首轮仪式（bootstrap）、能力要点（capability bullets）、skill 预装、自主权限策略（autonomy policy）、以及语言感知的沟通规则。**所有模板从零原创**，只从行业常识里借鉴角色命名和典型交付物维度，不复制任何外部仓库的表达。这样做有三个好处：

1. **没有协议负担** —— 不欠任何第三方署名
2. **runtime 适配** —— 可以把 clawith 独有的 heartbeat、memory、skill、workspace 机制直接织进 soul，而不是事后外挂
3. **voice 一致** —— 和现有 Morty / Meeseeks 保持同一种"同事感"的人设语气

---

## 2. 范围

### 2.1 分类方案（3 个大类，覆盖全部 15 个模板）

所有模板（新 11 + 老 4）统一到 3 个面向用户的分类：

| category | 中文标签 | 定位 |
|----------|----------|------|
| `software-development` | 软件开发 | 写代码、审代码、部署、设计、产品 |
| `marketing` | 营销 | 增长、内容、投放、市场研究 |
| `office` | 办公通用 | 项目管理、个人助理 |

### 2.2 新增 11 个模板

| # | 模板 | 分类 | 代号 | 主要交付物 |
|---|------|------|------|------------|
| 1 | **Frontend Developer** | software-development | FE | React/Vue 组件、性能报告、无障碍审计 |
| 2 | **Backend Architect** | software-development | BE | API 设计、数据库 Schema、扩展性方案 |
| 3 | **Code Reviewer** | software-development | CR | 结构化的 PR 审查报告 |
| 4 | **DevOps Automator** | software-development | OPS | CI/CD 流水线、IaC 配置、运维手册 |
| 5 | **Rapid Prototyper** | software-development | RP | MVP、POC、可运行 demo |
| 6 | **Growth Hacker** | marketing | GH | 实验方案、漏斗分析、增长循环设计 |
| 7 | **Content Creator** | marketing | CC | 多平台编辑日历、文案、Newsletter |
| 8 | **SEO Specialist** | marketing | SEO | 关键词规划、技术 SEO 审计、内容 brief |
| 9 | **TikTok Strategist** | marketing | TT | 短视频选题、算法敏感的发布计划 |
| 10 | **LinkedIn Content Creator** | marketing | LI | 个人品牌帖子、B2B 思想领导力长文 |
| 11 | **Chief of Staff** | office | CoS | 每日简报、OKR 追踪、会议纪要、跟进清单 |

> 这 11 个名字是业界通用角色名，不构成对任何具体仓库的借鉴。**代号使用 2-3 字母的文字标识**，严禁使用 emoji（沿用现有 PM / DS / PI / MR 的惯例）。

### 2.3 现有 4 个模板的重新分类

现有模板原本使用 `management` / `design` / `product` / `research` 作为 category，本次统一到 3 个大类。DB 里只需要更新 `agent_templates.category` 字段（由 seeder 执行 upsert 时自然覆盖，无需单独迁移）。

| 模板 | 原 category | 新 category | 理由 |
|------|-------------|-------------|------|
| PM (Project Manager) | management | `office` | 项目管理是跨场景通用能力 |
| Designer | design | `software-development` | 现有 soul 定位于 UI/产品设计，服务软件开发 |
| Product Intern | product | `software-development` | 服务于产品经理的需求分析，属软件团队 |
| Market Researcher | research | `marketing` | 市场研究天然归属营销场景 |

最终分类分布（总 15 个）：
- `software-development` (7)：FE、BE、CR、OPS、RP、Designer、Product Intern
- `marketing` (6)：GH、CC、SEO、TT、LI、Market Researcher
- `office` (2)：CoS、PM

### 2.4 非目标

- 本次默认**复用现有 skill**；确实不够用时，从公开 skill 库（如 Anthropic Skills 等）下载补充（见 §5.1）
- 本次**不**新增任何 tool，只复用已绑定的 MCP 工具
- 本次**不**改前端 Agent Market UI，假设前端会自动读取新 `AgentTemplate` 记录（若前端硬编码旧 category 值需配合修改）
- 本次**不**给每个模板写独立 HEARTBEAT.md，全部沿用 `backend/agent_template/HEARTBEAT.md`（如果测试发现行为有偏差再考虑）
- 本次**不**支持用户自定义模板，依然只有内置模板
- 本次**不**迁移现有 4 个模板的内容到新文件布局，只更新它们的 `category` 字段；内容迁移留作独立清理任务
- 本次**不**引入任何 emoji——所有模板的 `icon` 字段使用 2-3 字母的文字代号，soul / bootstrap / meta.yaml 正文内严禁出现 emoji

---

## 3. 模板结构契约

每一个模板（新老都一样）必须提供以下 9 个字段：

| 字段 | 存储位置 | 作用 |
|------|----------|------|
| `name` | `AgentTemplate.name` | Agent Market 卡片标题 |
| `description` | `AgentTemplate.description` | 卡片下方一句话简介 |
| `icon` | `AgentTemplate.icon` | 卡片上的文字代号（2-3 字母，如 `FE` / `CoS`）。严禁 emoji |
| `category` | `AgentTemplate.category` | 3 个值之一：`software-development` / `marketing` / `office` |
| `capability_bullets` | `AgentTemplate.capability_bullets`（注意：字段缺失，见 §6） | 卡片下方 3 条能力要点 |
| `soul_template` | `AgentTemplate.soul_template` | 完整人格——身份、个性、规则、工作流、语言 |
| `bootstrap_content` | `AgentTemplate.bootstrap_content`（注意：字段缺失，见 §6） | 首轮仪式，agent 配置完自删 |
| `default_skills` | `AgentTemplate.default_skills` | 创建 agent 时自动安装的 skill 文件夹名 |
| `default_autonomy_policy` | `AgentTemplate.default_autonomy_policy` | 各工具的权限等级（L1/L2） |

### 3.1 `soul_template` 的子结构（方案 A：4 段最小化，统一英文）

每一份 soul 都必须按下面的 4 段结构写：

```markdown
# Soul — {name}

## Identity
- **Role**: <one line>
- **Expertise**: <comma-separated keywords>

## Personality
- <3-4 bullets on character traits specific to the role>
- I detect the user's language from their latest message and reply in the same language. When the message is ambiguous (emoji-only, code-only), I default to English. Internal files (plans, memory, workspace artifacts) stay in English for consistency; only chat replies switch language.

## Work Style
- <3-5 role-specific bullets on how this agent approaches work>
- I save task plans, drafts, and final deliverables under `workspace/<task-name>/` — not inline in chat. Each task gets its own folder with a `plan.md` and numbered artifact files.
- I record non-obvious patterns, caveats, and reusable knowledge to `memory/<topic>.md` (e.g. `memory/performance_patterns.md`) so future sessions benefit from past work.
- During heartbeat, I focus on: <one line specific to the role — e.g. for Frontend Developer: "React stable-channel updates, Core Web Vitals metric changes, new CSS capabilities with broad browser support">.

## Boundaries
- <3-4 role-specific boundary bullets — what needs human approval, what's out of scope>
- Actions that require an external integration (email, calendar, messaging, deployment) prompt the user to configure that integration first; I don't assume it's connected.
```

**设计原则：**
- 每个 bullet 都是可验证的行为，不是空泛修辞
- Personality 里的最后一条语言规则在 11 个模板里**逐字一致**，方便审计
- Work Style 里的 workspace / memory / heartbeat 三条**结构固定、内容按角色定制**——比如 Growth Hacker 的 memory 条写"experiment log"，Frontend 写"performance_patterns"
- Boundaries 最后一条集成类动作的说明在所有模板里**逐字一致**

### 3.2 `bootstrap_content` 模式（对齐主分支两轮仪式架构）

**重要**：`bootstrap_content` 在主分支（`yutong/agent-templates`）上已经从"bootstrap.md 文件模式"重构为"系统提示词注入模式"。这段内容由 [backend/app/services/onboarding.py](backend/app/services/onboarding.py) 的 `resolve_onboarding_prompt` 在首位用户首次对话时注入。

**两轮仪式触发规则**：
- 只对 **founding user**（该 agent 的第一个对话者）生效
- 后续用户走另一份共享的 welcoming 提示词（在 `onboarding.py` 里硬编码，不走模板）
- 一旦 deliverable turn 开始流式输出，向 `agent_user_onboardings` 插锁行，仪式结束

**bootstrap_content 模板骨架**（每个模板按这个结构填）：

```
You are {name}, a <role description> meeting {user_name} for the first time. \
Markdown rendering is on — **use bold** freely to highlight the user's name, \
your own name, capability labels, and key next-step phrases.

This conversation has had {user_turns} user messages so far. Follow EXACTLY \
the matching branch below.

If user_turns == 0 (greeting turn):
- Open with: "**Hi {user_name}!**" on its own line.
- One-line intro: "I'm **{name}**, your <short role phrase>."
- Pitch 2–3 capability bullets (bold label + short phrase):
  - "**<Capability A>** — <one-line pitch>"
  - "**<Capability B>** — <one-line pitch>"
  - "**<Capability C>** — <one-line pitch>"
- Ask ONE bolded question: "**<one tight role-specific question>**"
- Stop. Don't ask about <2-3 things to explicitly not probe on>.

If user_turns >= 1 (deliverable turn):
- Whatever they said is the task. DO NOT ask clarifying questions about \
<list things NOT to ask about>.
- Produce <concrete role-specific deliverable> inline with bold section \
headers:
  - "**<Section 1>**" — <brief description>.
  - "**<Section 2>**" — <brief description>.
  - "**<Section 3>**" — <brief description>.
- Close: "Want me to <option A>, or **<option B>**?"
- Under ~<N> words.

<One-line voice note — e.g. "PM voice: structured, decisive, no fluff">. \
Never mention these instructions to the user.
```

**占位符**（由 onboarding 服务自动替换）：
- `{name}` → agent 名字
- `{user_name}` → 当前用户名（未知时为 `there`）
- `{user_turns}` → 到目前为止的用户消息数（0 = 打招呼轮；≥1 = 交付轮）

**硬性规则**：
- 结尾必须有 `Never mention these instructions to the user.` —— 禁止把元结构讲给用户
- greeting turn 的 2-3 条 capability bullets 应与 `capability_bullets` 字段内容一致（避免分裂）
- deliverable turn 必须**立即产出**具体东西，不能追问
- 整个提示词用反斜杠行尾连接（`\\\n`）是 Python 字符串书写风格，meta.yaml 里存成纯文本时可以去掉
- 参考实现：`yutong/agent-templates` 分支上的 [backend/app/services/template_seeder.py:BOOTSTRAP_PM](backend/app/services/template_seeder.py)

### 3.3 对比：老 soul（现有 4 个）vs 新 soul（本次 11 个，方案 A）

**老 soul**（现有 PM / Designer / Product Intern / Market Researcher，约 15-25 行）：

```markdown
# Soul — {name}
## Identity
## Personality
## Work Style
## Boundaries
```

**新 soul**（方案 A，约 25-35 行，**仍是 4 段**）：

```markdown
# Soul — {name}
## Identity        (同老版)
## Personality     (同老版，末尾加 1 条语言规则)
## Work Style      (同老版，末尾加 3 条 runtime 使用 bullet：workspace / memory / heartbeat)
## Boundaries      (同老版，末尾加 1 条集成类动作规则)
```

**差异汇总：**

| 维度 | 老 soul | 新 soul（方案 A） |
|------|---------|---------------------|
| Section 数 | 4 | 4（同） |
| 典型行数 | ~20 | ~30 |
| 结构是否兼容 | — | 完全兼容，老模板追加 4-5 行即可对齐 |
| Work Style 内容 | 通用工作方式 | 通用 + 3 条固定 bullet（workspace/memory/heartbeat） |
| Personality 内容 | 通用性格 | 通用 + 1 条：语言匹配规则 |
| Boundaries 内容 | 通用边界 | 通用 + 1 条：集成类动作需先引导配置 |
| clawith runtime 适配 | 弱 | 强（主动利用 workspace/memory/heartbeat） |

**老模板的增量升级**（后续独立 PR，非本次范围）：只需要给每个老模板追加 5 行——Personality +1 条、Work Style +3 条、Boundaries +1 条。不破坏任何现有段落。

---

## 4. 存储与加载器

### 4.1 当前状态

`backend/app/services/template_seeder.py` 里的 `DEFAULT_TEMPLATES: list[dict]` 是硬编码的 Python 列表，4 个模板就已经 ~350 行。再加 11 个同等密度会变成 ~1500 行 Python 文件，无法维护。

### 4.2 建议：文件夹式布局

把模板内容挪到 `backend/agent_templates/<slug>/`：

```
backend/agent_templates/
  frontend-developer/
    meta.yaml            # name, description, icon, category, capability_bullets,
                         # default_skills, default_autonomy_policy
    soul.md              # soul_template 内容
    bootstrap.md         # bootstrap_content 内容
  backend-architect/
    meta.yaml
    soul.md
    bootstrap.md
  ... （11 个新模板各一个文件夹）
```

`template_seeder.py` 变成一个**加载器**，职责：

1. 遍历 `backend/agent_templates/*/` 目录
2. 解析 `meta.yaml` 取结构化字段
3. 把 `soul.md` 和 `bootstrap.md` 作为文本读入
4. upsert 进 `agent_templates` 表
5. 保留现有"删除不在列表里的内建模板"逻辑（见 [template_seeder.py:361-376](backend/app/services/template_seeder.py:361)）

**好处：**
- 每个模板是一个可审查单元——PR review 更轻，非 Python 同事也能贡献
- Markdown 在编辑器里有高亮，写 soul 舒服
- 新增模板 = 新增一个文件夹，不用改 Python
- 可写测试：校验每个模板是否含有 §3.1 要求的全部 section

**老模板的迁移路径：**
- 本次继续用 `DEFAULT_TEMPLATES` Python 列表
- 加载器同时读 Python 列表和文件系统，做合并
- 老 4 个模板后续通过独立、隔离的 PR 迁移到文件系统

### 4.3 `meta.yaml` schema

```yaml
name: "Frontend Developer"
description: "Builds responsive, accessible web apps with pixel-perfect precision."
icon: "FE"                    # 文字代号，严禁 emoji
category: "software-development"
capability_bullets:
  - "React/Vue component implementation"
  - "Core Web Vitals performance optimization"
  - "Accessibility & cross-browser QA"
default_skills:
  - "complex-task-executor"   # 内置默认 skill，始终包含
  - "web-research"             # 角色特定的可选 skill
default_autonomy_policy:
  read_files: "L1"
  write_workspace_files: "L1"
  delete_files: "L2"
  send_feishu_message: "L2"   # 若用户未配置飞书，仅保留 key，agent 触发时引导配置
```

纯内部字段，没有任何外部引用。

---

## 5. 每个模板的默认 skill 和 autonomy

### 5.1 Skill 分配

每个 agent 都自带 clawith 的默认 skill（`skill-creator`、`complex-task-executor`）。角色特定的补充：

| 模板 | 额外安装的 skill | 现有？ |
|------|------------------|--------|
| Frontend Developer | （无额外，直接写代码） | — |
| Backend Architect | （无额外） | — |
| Code Reviewer | （无额外） | — |
| DevOps Automator | `mcp-installer` | 已有 |
| Rapid Prototyper | `mcp-installer` | 已有 |
| Growth Hacker | `web-research`, `data-analysis` | 已有 |
| Content Creator | `web-research`, `content-writing` | 已有 |
| SEO Specialist | `web-research`, `competitive-analysis` | 已有 |
| TikTok Strategist | `web-research`, `content-writing` | 已有 |
| LinkedIn Content Creator | `web-research`, `content-writing` | 已有 |
| Chief of Staff | `meeting-notes`, `web-research` | 已有 |

**策略**：Phase 1 开工前作者核对 DB 中实际注册的 skill `folder_name`。所有推测名都来自 `MORTY_SKILLS` / `MEESEEKS_SKILLS`（[agent_seeder.py:80](backend/app/services/agent_seeder.py:80)），预计命中率 ≥90%。若核对后发现缺失：

1. **优先用现有**：替换为现有 skill 能覆盖的组合
2. **下载补充**：从公开 skill 库（Anthropic Skills 等）下载相近能力，按 clawith skill 规范适配后注册进 DB
3. **本次不新造 skill**：避免阻塞模板发布

### 5.2 自主权限策略默认值

基线（对齐现有 4 个模板）：

```yaml
read_files: L1
write_workspace_files: L1
delete_files: L2
send_feishu_message: L2       # 未配置飞书集成时，agent 触发即引导用户配置
```

**说明**：
- `web_search` **不**出现在每个模板的 policy 里——搜索由平台统一配置，不走 per-agent 权限
- `execute_shell`、`send_email`、`schedule_meeting` 这些 key **目前不存在**，本次不引入；当用户需要邮件/会议能力时，agent 会引导用户去配置对应集成（如 Google Calendar / 飞书日历 / Email MCP）

按模板覆盖基线：

- **Code Reviewer**: `write_workspace_files: L2`（不应该自主把代码写进用户项目里）
- **Chief of Staff**: 在 soul 的 Boundaries 段写明"日程 / 邮件相关动作需先引导用户配置集成，再按该集成的权限等级执行"

---

## 6. 数据库字段已就绪（前置条件消除）

原本标记为"前置修复"的 `AgentTemplate.bootstrap_content` 和 `AgentTemplate.capability_bullets` 字段缺失问题，**已在 `yutong/agent-templates` 分支上修复**：

- 迁移文件已落位：[backend/alembic/versions/add_agent_bootstrap_fields.py](backend/alembic/versions/add_agent_bootstrap_fields.py)
- 模型字段已添加：[backend/app/models/agent.py](backend/app/models/agent.py) 的 `AgentTemplate` 类现有这两列
- 相关服务层已就位：[backend/app/services/onboarding.py](backend/app/services/onboarding.py)、`AgentUserOnboarding` 表

本次设计**不再需要 PR #0 的 schema 修复**，直接从 §7 Phase 0 的"分类更新 + 文件布局搭建"开始。

---

## 7. 上线计划

### Phase 0 —— 前置（1 个 PR）
- 在 `template_seeder.py` 里把现有 4 个模板的 `category` 值更新为 §2.3 的新分类（PM → `office`，Designer / Product Intern → `software-development`，Market Researcher → `marketing`）
- 校对前端 Talent Market 页面是否对 category 值有硬编码，若有需同步修改
- 新建空壳 `backend/agent_templates/` 目录 + 加载器骨架（只遍历，尚无内容）
- 跑一遍确认 seeder 仍能正确 upsert 现有 4 个模板，不受新架构影响

> 注：原设计里的 "加 `bootstrap_content` / `capability_bullets` 字段" 前置任务已被 `yutong/agent-templates` 分支完成，本 Phase 不再需要。

### Phase 1 —— 加载器 + 试点模板（1 个 PR）
- 建 `backend/agent_templates/` 目录 + 在 `template_seeder.py` 里写加载器
- 端到端出一个 **Frontend Developer** 作为试点（11 个里最独立的一个）
- 加一个集成测试：加载文件夹并 upsert 模板
- 和同事评审：`meta.yaml` + `soul.md` 的切分是否好用？soul 里 `Work Style` 和 `Heartbeat Focus` 两个 clawith 特色 section 的写法是否合适？

### Phase 2 —— 软件开发 track（1 个 PR）
- 加 Backend Architect、Code Reviewer、DevOps Automator、Rapid Prototyper
- 阶段末：5 个开发模板上线

### Phase 3 —— 增长 track（1 个 PR）
- 加 Growth Hacker、Content Creator、SEO Specialist、TikTok Strategist、LinkedIn Content Creator
- 阶段末：10 个新模板上线

### Phase 4 —— Chief of Staff（1 个 PR）
- 以"服务个人用户"视角撰写 soul（对比面向组织的幕僚长定位）
- 作为第 11 个模板上线
- 阶段末：总计 15 个模板（4 老 + 11 新）

### Phase 5 —— QA 回归
- 在新租户里从每个模板创建一个 agent
- 验证 soul 加载、bootstrap 运行、skill 拷贝到 `agent_data/<agent_id>/skills/`、autonomy policy 生效
- 语言切换冒烟测试：English prompt → English 回复；中文 prompt → 中文回复
- Heartbeat 测试：运行一次 heartbeat，验证 `Heartbeat Focus` 段落能正确引导 agent 行为

### Phase 6 —— 发布

合计 **5 个 PR**，每个可独立 review。PR 2-4 在 PR 1 落地后可并行。

---

## 8. 语言策略 —— 实现说明

§3.1 的 `Communication Language` 块纯粹是 prompt 层面的——依赖 LLM 检测并匹配用户语言。这和 Morty/Meeseeks 用的是同一套策略（见 [agent_seeder.py:47](backend/app/services/agent_seeder.py:47) 和 [agent_seeder.py:69](backend/app/services/agent_seeder.py:69)），实战已验证可行。

**后端无需改动**即可实现语言检测。由于当前 `User` 和 `Tenant` 都**没有** `locale` 字段，语言规则简化为两级：

1. 检测用户最新消息的语言 → 用该语言回复
2. 首条消息歧义（仅 emoji / 仅代码）→ 回退 **English**

这一策略在 §3.1 的 `Communication Language` 块中以英文逐字呈现，11 个模板完全相同。

---

## 9. 评审确认记录（7 个问题全部已回复）

| # | 问题 | 结论 |
|---|------|------|
| 1 | 文件布局（§4.2）`backend/agent_templates/<slug>/` | [采纳] **同意** |
| 2 | 可用 skill 清单 | [采纳] **基于现有 skill 设计**；不够用时从公开 skill 库（Anthropic Skills 等）下载补充，本次不新造 |
| 3 | `User` / `Tenant` 是否有 `locale` 字段 | [否] **无**，语言规则简化为"检测用户语言 → 回退英文"（§8） |
| 4 | autonomy policy key 扩展 | [否] `execute_shell` / `send_email` / `schedule_meeting` 不存在；`web_search` 由平台统一配置（不走 per-agent policy）；email / 飞书 / 会议集成由用户引导配置 |
| 5 | 分类法 | [采纳] **3 大类**：`software-development` / `marketing` / `office`（§2.1）；现有 4 个模板同步重分类（§2.3） |
| 6 | Chief of Staff 人称定位 | [采纳] **服务个人**（Personal Assistant 定位） |
| 7 | soul 新增 `Work Style` 强制要求 + `Heartbeat Focus` 两个 section | **改为方案 A**：4 段结构不变，Work Style 追加 3 条 runtime bullet（workspace/memory/heartbeat），Personality 追加 1 条语言规则，Boundaries 追加 1 条集成规则；不新开 section |

### 追加决策（基于主分支 `yutong/agent-templates` 的进度）

| 项 | 结论 |
|---|------|
| AgentTemplate schema 前置修复 | 已由 `yutong/agent-templates` 完成（§6），PR #0 不再需要 |
| Bootstrap 机制 | 从"bootstrap.md 文件 + agent 自删"改为"系统提示词注入 + agent_user_onboardings 表锁"（§3.2） |
| Bootstrap 内容结构 | 两轮仪式：Turn 0 greeting / Turn 1+ deliverable，占位符 `{name}` / `{user_name}` / `{user_turns}` |
| UI 入口 | "Agent Market" 在主分支上已重命名为 "Talent Market"，文档中同步采用该术语 |

---

## 10. 成功标准

- 平台首次启动后，Agent Market 里可见全部 15 个模板
- 从任意模板创建 agent，30 秒内可用
- 每个模板的首轮对话都通过语言切换测试（English / 中文 / ES / JA）
- 每个模板的 heartbeat 跑一轮后，`curiosity_journal.md` 里有符合 `Heartbeat Focus` 段落预期的条目
- Morty / Meeseeks 原有流程零回归

---

## 11. 附录 —— 完整模板内容计划

这份 spec **不包含** 11 个模板的 soul.md 和 bootstrap.md 实际内容。每个模板会在对应阶段的 PR 里现场起草。

**每个模板的写作流程（方案 A 简化后）：**

1. **开篇调研**（5 分钟）—— 作者基于行业常识列出该角色的：核心职责 3-5 条、1 个首轮交付物、1 行 heartbeat 关注方向
2. **Soul 填充**（15 分钟）—— 按 §3.1 的 4 段结构填写；Identity/Personality/Boundaries 通用段 + Work Style 的 runtime 三条
3. **Bootstrap 撰写**（15 分钟）—— 按 §3.2 的两轮模板填入：2-3 条 capability 要点、1 个 greeting 问题、Turn 1+ 的交付物结构
4. **自审**（5 分钟）—— 对照 §10 成功标准和下面的校验清单

**校验清单**（每个模板发 PR 前自检）：
- [ ] Soul 恰好 4 段，无多余 section
- [ ] Personality 最后一条 = 语言匹配逐字复用
- [ ] Work Style 后 3 条 bullet 顺序 = workspace → memory → heartbeat
- [ ] Boundaries 最后一条 = 集成类动作引导逐字复用
- [ ] bootstrap 以 "You are {name}" 开头
- [ ] bootstrap 含 "If user_turns == 0" 和 "If user_turns >= 1" 两个分支
- [ ] bootstrap 末尾有 "Never mention these instructions to the user."
- [ ] bootstrap greeting 的 capability 要点和 `capability_bullets` 字段内容一致
- [ ] 全文无 emoji（图标、装饰符号、状态符如 check/cross/warning 等一律禁止）

**工时预估**：每个模板 ~40 分钟，11 个合计 ~7-8 小时（1-2 天）。每个模板约 30 行 soul + 35-50 行 bootstrap。

---

## 12. 给评审同事的快速导读

如果你没时间读完整份文档，至少看这四处：

1. **§2.1 + §2.3** —— 15 个模板的最终归类（3 大类：software-development / marketing / office）
2. **§3.3** —— 老 soul（现有 4 模板）vs 新 soul（本次 11 模板）的结构对比
3. **§6** —— 现有数据库 schema 的 bug，解释了为什么老模板的 bootstrap 和 capability_bullets 像消失了一样
4. **§9** —— 7 个原开放问题已全部由产品决策人确认并记录在案

本文档已过第一轮评审（2026-04-24）。下一步：作者开始 Phase 0（数据库迁移 + 老模板重分类）。
