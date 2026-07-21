# Clawith 单 Agent Runtime、Run State 与 Context Summarization PRD

> 状态：已与 LangGraph 主 Runtime 终版技术方案对齐
>
> 详细技术设计：`technical-design.md`

## 1. 背景

当前 Clawith 单 Agent 已经具备对话、工具调用、文件操作、触发器唤醒和 A2A 协作能力，但这些能力仍分散在 Web、渠道、Task、Trigger、Heartbeat 和 A2A 的不同执行路径中。运行过程主要依赖最近聊天历史和函数内的 tool-call loop；服务重启、等待外部输入、跨天任务和工具副作用重试时，缺少统一的 checkpoint、interrupt、resume 和幂等机制。

Claude Code、Codex、OpenCode 等主流 Agent 产品已经把 Context Summarization、Agent Run、tool-call loop、checkpoint、恢复和验证视为统一 Runtime 能力。Clawith 的升级不能只增加几张状态表，也不应该把租户权限、A2A 关系和产品状态全部交给通用框架。

本 PRD 最终采用 LangGraph 主 Runtime 方案：

1. LangGraph 执行层：是 Run 生命周期、tool-call loop、checkpoint、interrupt、resume、pending writes、最终执行结果和 durable execution 的唯一事实来源。
2. Clawith 产品层：保留租户权限、Run Registry、A2A 关系、可靠 Runtime Command、交付状态、Session Summary、工具副作用 receipt 和可重建产品投影。
3. Runtime Adapter：作为所有入口的统一执行接口，隔离 Clawith 业务语义与 LangGraph 内部实现。

本方案不维护 `agent_runs` 与 LangGraph 两套可推进状态机。Clawith 的执行状态字段只允许是 checkpoint 派生的查询投影；Graph 路由、恢复、重试、取消和工具执行一律不能读取这些投影做决定。

本期核心产品能力是 Context Summarization、可查询的 Agent Run 状态和可恢复执行；详细实现以同目录 `technical-design.md` 为准。

## 2. 目标

### 2.1 产品目标

本阶段的产品目标是让 Clawith 单 Agent 在 Context Summarization、Agent Run 状态和任务恢复能力上，逐步向 Claude Code、Codex、OpenCode 等主流 Agent 的设计看齐。

具体表现是：让单 Agent 在长对话、跨天任务、A2A 委托、等待外部回复或工具结果较多的场景中，能够知道：

- 当前要完成什么。
- 已经做了哪些步骤。
- 哪些结论已经确认。
- 关键证据在哪里。
- 是否正在等人、等 Agent、等外部系统或等时间。
- 被 trigger / A2A / 用户继续消息唤醒后应该从哪一步接着做。

### 2.2 工程目标

本阶段的工程目标是建立统一 Agent Runtime Adapter、可靠 Command Inbox、LangGraph checkpoint 执行路径和可重建产品投影。

Clawith 侧定义五张业务/技术支撑表：

```text
Session Context 表：session_context_states
Agent Run Registry / Projection 表：agent_runs
Runtime Command 表：agent_run_commands
Run Events 表：agent_run_events
工具幂等账本：agent_tool_executions
```

五张表都需要持久化保存，但不承担 LangGraph checkpoint 的职责：

- `session_context_states` 保存当前 session 的最新滚动 Session Summary，每次压缩覆盖更新同一条 current state 数据。
- `agent_runs` 保存 Run 的静态身份、来源、归属、固定配置、交付事实和可重建查询投影。
- `agent_run_commands` 可靠保存 start / resume / cancel 输入，避免入口事务提交后、调用 Graph 前崩溃造成任务丢失。
- `agent_run_events` 追加保存产品关心的关键事件；执行生命周期事件从 checkpoint 幂等派生。
- `agent_tool_executions` 保存副作用工具的幂等执行结果，避免恢复和重试造成重复操作。

LangGraph checkpoint 独立保存 Run 生命周期、waiting、当前执行节点、下一节点、pending writes、最终执行结果和 resume cursor，是执行与精确恢复的唯一事实来源。

完成后，单 Agent 的 LLM 调用由 Runtime Context 统一组装，最近消息作为局部上下文参与组装：

```text
soul.md
+ Session Summary
+ 当前 Agent Run 状态
+ 最近少量消息
```

Runtime Context 由 Clawith 产品事实、会话摘要、最近消息和 LangGraph 当前 Run State 共同构造。Clawith 表负责权限、可靠输入和产品查询；LangGraph 负责全部执行状态与恢复，两者不重复实现状态机。

## 3. 核心概念

### 3.1 Chat Session 与 Session Context

Chat Session 是用户与 Agent 的持续对话容器，对应现有 `ChatSession`。一个 session 可以持续产生消息，并创建多个彼此独立的 Agent Run。

Session Context 是该会话历史的结构化压缩结果。它回答的问题是：这个 session 到目前为止发生了什么、用户确认了哪些要求和结论、还有哪些事项未完成、关键证据和 workspace 文件在哪里。

同一个 session 默认只有一条当前 Session Context，由 `session_context_states` 作为唯一事实来源。它基于旧版本、watermark 后的新 `ChatMessage` 和已完成 Run 提交的 `SessionContextDelta` 滚动更新；原始消息不删除。创建新 Run 时，不注入完整 Session，而是读取当前 Session Context、watermark 后的最近消息，并形成版本化 `Session Context Pack` 快照。

### 3.2 Agent Run

Agent Run 是一次可恢复的 Agent 执行单元，用于表达 Agent 当前正在处理的一件事。

它可以由这些入口创建或续接：

- 用户在 chat 中发起一个明确任务。
- trigger 唤醒 Agent。
- `task_executor` 执行后台任务。
- A2A `task_delegate` 发起或返回。
- heartbeat 等系统机制唤醒。

它回答的问题是：这次执行的目标是什么、当前状态是什么、做到哪一步、有哪些阻塞、下一次唤醒后应该如何继续。

Agent Run 在 Clawith 产品层登记目标、来源、归属、父子关系、固定运行配置和交付状态。Run 的生命周期、等待类型、具体 Graph node、下一步和恢复位置由同一 `run_id` 对应的 LangGraph checkpoint 保存。UI 需要的状态由 Projector 从 checkpoint 生成，允许短暂滞后且可完整重建。

大段消息正文和工具结果正文继续由消息、工具日志或 workspace 文件承载。

### 3.3 LangGraph Thread 与 Checkpoint

LangGraph Thread 是单个 Agent Run 的持久化执行容器，不是产品会话，也不是第三种上下文。固定关系为：

```text
Session 1 ── N Run
Run     1 ── 1 Thread
Thread  1 ── N Checkpoint
```

`thread_id` 默认等于 `agent_run.id`。同一 session 可以并行存在用户任务、Trigger 任务和等待 A2A 返回的旧任务，因此禁止使用 `ChatSession.id` 作为唯一 `thread_id`。

Checkpoint 保存 Graph 当前节点、下一节点、pending writes、interrupt 和 resume 所需状态。Thread 和历史 checkpoint 不直接进入模型上下文；模型只从最新 checkpoint 恢复 Graph State，再由 Context Builder 选择本次调用需要的字段。

### 3.4 Run Events

Run Events 是 Agent Run 的产品关键事件投影，用于查询状态变化、等待、恢复、验证、完成、失败和交付结果。

它回答的问题是：从产品视角看，这个 run 发生了哪些重要变化。它不复制 LangGraph 的每个 node update、model call、tool call 或 checkpoint，也不承担精确恢复。

底层执行历史由 LangGraph checkpoint 和 trace 层负责；执行生命周期事件由 checkpoint 派生，交付事件来自 Clawith 交付事务，工具副作用是否已经执行由 `agent_tool_executions` 负责。

### 3.5 Runtime Context

Runtime Context 是每次调用 LLM 前构造的上下文输入，用于把 Session Context Pack、当前 Run、明确相关的 Run 摘要和最近 Run 消息组合成一次模型调用可理解的输入。

它来自：

- 当前 `ChatSession` 的版本化 Session Context Pack。
- 当前 Agent Run 的目标、摘要和精确的 pending / waiting / verification 状态。
- 只包含与当前 Run 有明确 parent / child / dependency 关系的 Run 结果摘要。
- 最近少量 Session 消息和 Run 内部消息。
- 现有 Agent system prompt，例如 `soul.md`、角色、工具规则。

已完成、失败、取消、长期未使用或与当前目标无关的 Run，即使仍保存在数据库和 checkpoint 中，也不进入当前模型上下文。需要长期保留的结论必须先沉淀到 Session Context。

### 3.6 模型与 Runtime 的职责边界

业务模型每一轮只处理当前局部目标，只看到裁剪后的 Context、可用工具和完整工具结果，并输出 `tool_calls`、`wait` 或 `finish`。模型不需要理解 Command、checkpoint、Projector、锁、调度 lane、恢复和交付机制。

生命周期转换、依赖调度、结构校验、工具幂等、重试上限、取消、恢复和用户可见交付全部由确定性后端负责。`finish` 只是候选完成声明，必须经过 Runtime verify；非法结构只允许在尚无副作用时做有界修复。

能力不足时按功能降级，而不是把更多 Runtime 规则写进 Prompt：不支持可靠 tool calling 的模型只提供纯 Chat 或 legacy 能力；简单工具模型只开放 Single Run；多 Agent Planning 必须使用单独配置且通过结构化输出校验的 Planning 模型；Compact 使用独立的模型与窗口规则。配置或能力校验失败时显式提示，不静默替换模型。

## 4. Runtime 数据设计

Runtime 数据分为三类：Clawith 产品事实与可靠 Command、LangGraph checkpoint、Clawith 可重建投影。

Clawith 产品事实由 Runtime Adapter、Run Service、Delivery Service 和 SessionContextService 写入，LLM 不直接操作数据库。LangGraph 负责保存 Graph State、Run 生命周期、执行节点、waiting、最终执行结果和恢复位置；Projector 从 checkpoint 生成 UI 查询字段与生命周期事件。

写入责任：

1. 产品事实由后端写入，例如 `tenant_id`、`agent_id`、`session_id`、`source_type`、关联 ID、固定模型和 `delivery_status`。
2. start / resume / cancel 先写 `agent_run_commands`，再由 Command Worker 调用 LangGraph。
3. 生命周期与执行位置只由 LangGraph checkpoint 写入，例如 `lifecycle_status`、waiting、当前 node、next node、pending writes、最终结果和 resume cursor。
4. Projector 根据 checkpoint 幂等写 `projected_execution_status`、`projected_waiting_type`、`projected_result_summary`、`projected_last_error` 和生命周期事件；这些值不得反向驱动 Graph。
5. 语义内容可以由 LLM 生成候选，再由后端校验并落库，例如 `goal`、Session `summary`、`decisions` 和 `open_items`。

五张 Clawith 表的职责如下：

| 表 | 职责 | 保存方式 |
|-|-|-|
| `session_context_states` | 保存一个 session 当前最新的 Session Summary，供下一次 LLM 调用恢复上下文。 | 持久化保存，滚动覆盖更新；v1 不保留每次压缩的历史版本。 |
| `agent_runs` | 保存 Run Registry、交付事实和 checkpoint 派生查询投影。 | 每次 Run 一条；静态事实长期保存，`projected_*` 可重建。 |
| `agent_run_commands` | 保存 start / resume / cancel 可靠输入及消费结果。 | 持久化追加保存，按幂等键唯一。 |
| `agent_run_events` | 保存 checkpoint 派生生命周期事件和 Clawith 原生交付事件。 | 持久化追加保存；派生部分可重建。 |
| `agent_tool_executions` | 保存副作用工具的执行预占、结果和幂等状态。 | 持久化保存，每个 `run_id + tool_call_id` 唯一。 |

各层关系：

```text
agent_run_commands 可靠接收外部输入
LangGraph checkpoint 保存唯一执行状态
agent_runs / agent_run_events 保存可重建产品投影
session_context_states 保存下次 LLM 可注入的 Session Summary
agent_tool_executions 防止工具重复执行
```

Runtime DB 只保存摘要和引用。大段工具结果、大文档正文或大网页正文继续保存在现有消息、工具日志、workspace 文件或外部系统中。

### 4.1 agent_runs

`agent_runs` 表登记一次可恢复的 Agent Run，并提供产品查询投影。

它解决的问题是：这件任务是谁的、从哪里来、使用什么固定配置、关联哪些父子 Run、当前投影显示什么以及是否已经交付。它不回答 Graph 下一步如何执行。

一个 Agent Run 可以由 chat、trigger、task、A2A、heartbeat 等入口创建。所有新 Runtime 入口统一通过 Runtime Adapter 创建或续接 run；迁移期间可由 `runtime_type` 区分 `legacy` 和 `langgraph`。

`agent_runs` 不保存 Graph 的 `current_step`、`pending_steps` 或 resume cursor。所有 `projected_*` 字段只允许 Projector 写入，具体执行状态以 LangGraph checkpoint 为准。

建议字段：

| 字段 | 说明 |
|-|-|
| `id` | run ID。 |
| `tenant_id` | 所属租户。 |
| `agent_id` | 执行 Agent；`run_kind = orchestration` 的系统编排 Run 为空。 |
| `session_id` | 关联 `ChatSession.id`，可为空。 |
| `source_type` | `chat` / `trigger` / `task` / `a2a` / `heartbeat`等。 |
| `source_id` | 来源对象 ID，如 message_id、trigger_id、task_id、A2A session id。 |
| `source_execution_id` | 一次实际执行的稳定幂等来源 ID，可为空。 |
| `origin_user_id` | 原始请求人，可为空。 |
| `origin_agent_id` | 原始请求 Agent，A2A 场景使用，可为空。 |
| `parent_run_id` | 父 run，A2A 委托或派生任务使用，可为空。 |
| `root_run_id` | 整条派生链的根 run，可为空。 |
| `goal` | 本次 run 的目标。 |
| `run_kind` | `foreground` / `background` / `delegated` / `orchestration`。 |
| `system_role` | 系统编排类型，例如 `group_planning`；非 orchestration Run 为空。 |
| `model_id` | Run 创建时解析并固化的初始执行模型；Planning Run 使用独立配置模型。 |
| `runtime_type` | `legacy` / `langgraph`，创建后不可切换。 |
| `runtime_thread_id` | LangGraph thread ID，默认等于 `run.id`。 |
| `graph_name` / `graph_version` | 创建时固化的 Graph 定义；恢复不得静默切换到新版本。 |
| `scheduling_lane_key` | 需要业务串行时使用的窄调度通道；群 mention 按租户和 Agent 生成，其他 Run 为空。 |
| `scheduling_position_created_at` / `scheduling_position_id` | 群 mention 的 Message Position，用于确定同 lane 顺序。 |
| `lane_held` / `lane_claimed_at` | 业务调度占用事实；不表示 Graph 生命周期，Projector 不写。 |
| `projected_execution_status` | LangGraph `lifecycle_status` 的查询投影，不是执行事实源。 |
| `delivery_status` | 结果交付状态，与执行完成分开。 |
| `projected_waiting_type` | waiting 类型查询投影，可为空。 |
| `projected_waiting_reason` | 等待原因投影。 |
| `projected_result_summary` | 最终结果摘要投影。 |
| `projected_last_error` | 最近错误投影。 |
| `projected_checkpoint_id` | Projector 已处理的 checkpoint watermark。 |
| `projection_updated_at` | 投影最近更新时间。 |
| `created_at` | 创建时间。 |
| `updated_at` | 更新时间。 |
| `projected_completed_at` | 完成时间投影。 |

LangGraph State 中的权威 `lifecycle_status` 枚举：

```text
created
queued
running
waiting_user
waiting_external
waiting_agent
verifying
completed
failed
cancelled
```

状态规则：

1. `created`：业务 run 已创建。
2. `queued`：等待 worker 领取。
3. `running`：Graph 正在执行。
4. `waiting_user`：需要用户输入或确认。
5. `waiting_external`：等待外部系统、webhook、时间或第三方回复。
6. `waiting_agent`：等待另一个 Agent 的 target run 返回。
7. `verifying`：模型声明完成，系统正在校验结果。
8. `completed`：执行完成；结果可能仍处于待交付或交付失败状态。
9. `failed`：无法继续执行，且已记录原因。
10. `cancelled`：被用户或系统取消。

对于已经向用户公开发送“已接受/开始处理”ACK 的前台 Run，`completed`、`waiting_user`、`failed` 和 `cancelled` 都必须有对应的用户可见消息。失败消息必须脱敏；目标 Session 已删除或权限已失效时不得改写到其他 primary，只将 `delivery_status` 标记为 `failed` 并保留内部结果。

交付状态：

```text
not_required
pending
delivered
failed
```

### 4.2 agent_run_commands

`agent_run_commands` 是 Runtime 的可靠输入日志，只保存 `start`、`resume` 和 `cancel`。用户消息、TriggerExecution 或 A2A callback 必须与对应 Command 在同一 Clawith PostgreSQL 事务提交；Worker 再异步调用 LangGraph。

建议字段：

| 字段 | 说明 |
|-|-|
| `id` | Command ID，同时进入 Graph State 的去重记录。 |
| `tenant_id` / `run_id` | 租户与目标 Run。 |
| `command_type` | `start` / `resume` / `cancel`。 |
| `payload` | 已校验的小型输入，不保存密钥和大正文。 |
| `actor_user_id` / `actor_agent_id` | 发起者。 |
| `idempotency_key` | 同一 Run 内唯一。 |
| `status` | `pending` / `claimed` / `applied` / `rejected`。 |
| `claimed_by` / `claim_expires_at` | Worker claim。 |
| `applied_checkpoint_id` | 已确认应用该 Command 的 checkpoint。 |
| `error_code` | 拒绝或处理失败原因。 |
| `created_at` / `applied_at` | 时间戳。 |

Command 状态只表示输入是否已提交给 Graph，不表示 Run 当前执行状态。Worker 在调用返回前崩溃时，必须根据 checkpoint 中的 Command ID 对账；已经应用的 resume/cancel 只能补写 `applied`，不能再次推进 Graph。

同一 Thread 的推进固定使用以 `run_id` 为 key 的 PostgreSQL session-level advisory lock。取消采用协作式 control guard：模型或新工具开始前后检查有效 cancel；未开始的调用不再启动，已经进入 `started` 的工具先记录真实结果，再停止后续节点。进程崩溃后锁随连接释放，新 Worker 从 checkpoint 继续应用同一 cancel。不能通过直接改投影或只取消 asyncio task 伪造 `cancelled`。

### 4.3 agent_run_events

`agent_run_events` 表是 Agent Run 的追加式产品事件投影。

它解决的问题是：产品需要查询一次 run 的关键生命周期变化，而不应直接解析 LangGraph checkpoint。

`agent_run_events` 从产品视角记录关键事件摘要、结构化小字段和产物引用，用于 UI、审计和排查。生命周期事件由 Projector 从 checkpoint 派生；交付事件由 Clawith 交付事务原生写入。模型调用、工具调用和 node update 的完整 trace 不写入该表。

建议字段：

| 字段 | 说明 |
|-|-|
| `id` | event ID。 |
| `run_id` | 所属 run。 |
| `tenant_id` | 所属租户。 |
| `agent_id` | 执行 Agent。 |
| `event_type` | 事件类型。 |
| `summary` | 事件短摘要。 |
| `payload` | 结构化小字段。 |
| `artifact_refs` | 产物引用，包括文件、消息、工具结果和外部链接。 |
| `idempotency_key` | 事件幂等键。 |
| `source_checkpoint_id` | 生命周期事件的来源 checkpoint；交付事件可为空。 |
| `created_at` | 创建时间。 |

事件类型：

```text
run_created
status_changed
evidence_added
waiting_started
resumed
verification_updated
run_completed
run_failed
run_cancelled
delivery_succeeded
delivery_failed
```

大文本规则：

1. `payload` 保存结构化小字段和短摘要。
2. 大工具结果保存在现有工具日志、ChatMessage 或 workspace 文件中，`artifact_refs` 保存引用。
3. 完整执行恢复依赖 LangGraph checkpoint，完整审计继续依赖原始消息、工具日志和产物。
4. 生命周期事件使用 `(run_id, source_checkpoint_id, event_type)` 幂等，并可从 checkpoint history 完整重建。

### 4.4 session_context_states

`session_context_states` 表表示一个 session 当前最新的 Session Context，是会话级压缩结果的唯一事实来源。

它解决的问题是：长对话需要减少对完整历史回读的依赖。系统需要把已经发生过的用户可见对话、已确认 Run 结果、关键结论和引用整理成下一次 Run 可读取的 Session Context Pack。

同一个 session 默认只有一个当前 Session Summary 记录。每次压缩基于旧摘要和新增上下文生成新摘要，并覆盖更新同一条 current state。生成失败时保留旧 Session Summary 记录，并继续使用最近消息作为兜底上下文。

建议字段：

| 字段 | 说明 |
|-|-|
| `id` | context state ID。 |
| `tenant_id` | 所属租户。 |
| `agent_id` | direct / A2A session 的当前 Agent；共享 group session 为空。 |
| `session_id` | 当前 ChatSession。 |
| `summary` | session 滚动摘要。 |
| `requirements` | 用户已确认的要求和约束。 |
| `decisions` | 已确认结论和决策。 |
| `open_items` | 未完成事项。 |
| `evidence_refs` | 关键证据引用。 |
| `workspace_refs` | 相关 workspace 文件引用。 |
| `covered_through_message_id` | 已压缩覆盖到的最后一条 ChatMessage watermark。 |
| `version` | Session Summary 版本。 |
| `created_at` | 创建时间。 |
| `updated_at` | 更新时间。 |

规则：

1. 同一个 `session_id` 默认只有一个当前 Session Summary 记录。
2. Session Context 只接受 watermark 后的新 `ChatMessage` 和已完成 Run 的 `SessionContextDelta`，任意 Run 不得直接覆盖整条 Session Context。
3. 更新必须校验 `expected_version + expected_covered_through_message_id`，通过事务或 compare-and-swap 防止并发 Run 用旧摘要覆盖新摘要。
4. 压缩结果覆盖更新，默认保留当前最新版本；原始 `ChatMessage` 不删除。
5. 压缩失败或版本冲突时保留旧 state，重新读取最新版后再合并。
6. Session 与 Run 的关联通过 `agent_runs.session_id` 查询；不得因为某个 run 仍 active 就默认把它注入所有其他 Run 的上下文。
7. `session_type = group` 时 Session Context 属于共享 `ChatSession`，`agent_id` 必须为空；具体 Agent 的运行状态只进入自己的 Run Context。
8. v1 不新增 `message_seq`；ChatMessage 统一按 `(created_at, id)` 定义 Message Position。`covered_through_message_id` 只标识 watermark 消息，增量查询必须先解析其 Message Position，不得按 UUID 大小比较。

### 4.5 agent_tool_executions

`agent_tool_executions` 是副作用工具的幂等执行账本。

它解决的问题是：LangGraph 从 checkpoint 恢复时可能重新进入工具节点，系统必须避免重复发送消息、重复创建文档或重复修改外部数据。

建议字段：

| 字段 | 说明 |
|-|-|
| `id` | 执行记录 ID。 |
| `tenant_id` | 所属租户。 |
| `run_id` | 所属 Agent Run。 |
| `tool_call_id` | 模型工具调用 ID。 |
| `assistant_message_id` | 产生该 call 的 assistant 消息 ID；parallel calls 共享同一值。 |
| `tool_name` | 工具名称。 |
| `arguments_hash` | 参数摘要，用于一致性检查。 |
| `sanitized_arguments` / `request_ref` | 用于恢复完整 Tool Exchange 的脱敏参数或请求引用。 |
| `status` | `started` / `succeeded` / `failed` / `unknown`。 |
| `result_summary` | 结果短摘要。 |
| `result_ref` | 完整结果或产物引用。 |
| `started_at` | 开始时间。 |
| `completed_at` | 完成时间。 |

规则：

1. `run_id + tool_call_id` 唯一。
2. 工具执行前先原子写入 `started`。
3. 已有 `succeeded` 时复用原结果，不重复执行。
4. 副作用工具超时且无法确定外部结果时写入 `unknown`，禁止自动重试。
5. 只读工具可以按策略重试，但仍需保留执行结果供恢复使用。
6. 持久化历史和恢复时优先使用真实 `tool_call_id`；只有旧数据缺失时才能生成稳定的兼容 ID。

## 5. Runtime Context 构造

Runtime Context 是 Clawith 产品事实、LangGraph 当前 State 和最近消息进入 LLM 的统一入口，是每次模型调用前临时构造的上下文输入。

v1 Runtime Context 采用组合式压缩策略，而不是纯摘要策略：

```text
Session Context Pack
+ Current Run State
+ Related Run Summaries
+ Recent Run Messages
+ Tool Result Handling
```

v1 的原则：

1. 先注入稳定状态，再注入最近消息。
2. 优先保证当前任务状态完整，最近消息作为局部上下文补充。
3. 大正文只通过摘要和引用进入 prompt。
4. 保留现有 `ctx_size` 作为兜底策略，分阶段接入 Runtime Context。

### 5.1 构造输入

每次调用 LLM 前，构造 Runtime Context：

```json
{
  "session_context_snapshot": {
    "version": 12,
    "summary": "...",
    "requirements": [],
    "decisions": [],
    "open_items": [],
    "evidence_refs": [],
    "workspace_refs": [],
    "covered_through_message_id": "msg_100"
  },
  "recent_session_messages_snapshot": [],
  "current_run": {
    "run_id": "...",
    "goal": "...",
    "lifecycle_status": "running",
    "run_summary": {},
    "waiting_request": null,
    "verification_result": null
  },
  "related_runs": [],
  "recent_messages_policy": {
    "max_messages": 20,
    "preserve_tool_pairs": true
  }
}
```

### 5.2 注入顺序

推荐注入顺序：

1. Agent static context：身份、角色、工具规则。
2. Session Context Snapshot：summary、requirements、decisions、open items 和引用。
3. Current Run：目标、Run Summary 和当前 pending / waiting / verification 状态。
4. Related Run Summaries：只注入有明确依赖关系的 Run 结果摘要和产物引用。
5. Recent Session Messages Snapshot：新 Run 创建时默认固定最近 20 条用户可见消息，优先保留最近用户原话。
6. Recent Run Messages 与当前入口输入。

不注入完整 Session 历史、Session 下的所有 Run、整个 Thread、历史 Checkpoint 或完整大工具结果。

新 Run 创建时读取最新 Session Context，并把最近 20 条用户可见消息一起固化为快照。已有 Run 从 checkpoint 恢复时继续使用原 `session_context_snapshot + recent_session_messages_snapshot`；本次用户回复作为明确 resume input 加入 Run，不自动混入 Session 中其他并行任务后来产生的消息。

### 5.3 最近消息策略

当前 `ctx_size` 仍保留，但语义从“主要上下文”降级为“短期局部上下文”。

v1 建议：

- Web chat 最近消息默认目标为 20 条，并优先保留最近用户原话；如果窗口边界落在 tool pair 中间，为保留完整 pair 可以少量超过 20 条。
- tool call / tool result 必须继续保持 pair integrity。
- 已完成但超过预算的 Tool Exchange 整组移出活动消息，只在 Run Summary 保留执行摘要和产物引用。
- 如果有当前 run，优先保证 run 状态注入，最近消息数量按剩余上下文预算控制。

Tool Pair Integrity 规则：

1. 一条 `assistant(tool_calls=[A, B...])` 与其全部 tool results 组成一个原子 `Tool Exchange Block`；parallel calls 属于同一个 Block。
2. Runtime Context 只允许注入完整 Block。最近消息窗口落在 Block 中间时向前扩展，允许少量超过 20 条；如果完整且已结束的 Block 超出 token 预算，则 call 和全部 results 一起移出活动消息，并在 Run Summary 写入一条结构化执行摘要和引用。
3. 对持久化 tool call，应优先使用真实 `tool_call_id` 重建合法序列，不得为了通过 Provider 校验而静默删除已经执行的结果。
4. 完整 Block 能放入预算时整组保留；放不下时整组移出并摘要，不做单独缩短某个 result 的中间策略，也禁止从原 assistant 消息中单独删除某个 parallel tool call。
5. 移出后的摘要至少保留 call ID、工具名、是否产生副作用、执行状态、结果摘要和 `result_ref / artifact_refs`，但摘要本身不伪装成 tool result。
6. 发送模型前必须执行严格完整性校验；Provider Adapter 只能作为最后保护层，不承担静默修复 Runtime 语义的职责。

### 5.4 不完整 Tool Exchange 处理

| 状态 | 处理 |
|-|-|
| assistant calls 与全部 results 完整且能放入预算 | 整组原样保留 |
| assistant calls 与全部 results 完整但超出预算 | call 和全部 results 整组移出活动消息，写入 Run Summary 后继续调用模型，不重新执行工具 |
| 窗口边界切入完整 Block | 向前扩展并整组保留 |
| 工具确认从未执行 | 丢弃整组 assistant proposal，重新调用模型；允许模型生成新的 Tool Call |
| 工具已 `succeeded` 但 result 消息缺失 | 能重建则重建完整 Block；无法成对重建时 call/result 一起移出并写执行摘要，禁止重新执行工具 |
| 工具处于 `started` | 等待或对账，禁止继续调用模型制造重复执行 |
| 副作用工具为 `unknown` | interrupt / 人工确认，禁止自动重试 |
| 只有 tool result、没有 assistant call | 优先重建 call；无法成对重建时整组移出并保留结构化执行事实，禁止自动重试工具 |
| parallel group 只缺部分 results | 整组暂不交给模型，先完成重建或对账 |

重新调用模型不等于重新执行工具。只有账本能够证明工具从未进入 `started` 时，才允许丢弃未完成 proposal、重新调用模型并执行新 Tool Call。已经 `succeeded` 的工具在消息无法成对恢复时整组移出并以摘要续接；任何 `started` 或 `unknown` 工具都必须等待或对账，不能因上下文裁剪而重做。

## 6. Session Compact 与 Run Compact

系统只定义两套语义压缩：Session Compact 负责长期会话背景，Run Compact 负责单次任务内部不断增长的执行历史。LangGraph Thread 只是 checkpoint 容器，不建立第三套 Thread Compact。

### 6.1 Session Compact

Session Compact 由 Clawith `SessionContextService` 负责，不由任意 Run 的 Graph State 直接覆盖。输入为旧 Session Context、watermark 后新增的用户可见 `ChatMessage`，以及已完成 Run 提交并通过权限校验的 `SessionContextDelta`。

输出契约：

```json
{
  "schema_version": "session_context_v1",
  "summary": "...",
  "requirements": [],
  "decisions": [],
  "open_items": [],
  "evidence_refs": [],
  "workspace_refs": [],
  "covered_through_message_id": "msg_100",
  "expected_version": 12
}
```

满足以下任一条件时触发：

1. 当前 session 新增消息超过配置阈值。
2. Session Context Pack 达到分配给会话背景的 token 预算。
3. 用户显式要求总结当前会话。
4. Run 进入 `completed` / `failed` 并产生待合并的 `SessionContextDelta`。

写入时必须执行版本和 watermark 校验，失败时保留旧版本并重试合并。生成失败时继续使用旧 Session Context 加最近 20 条消息作为兜底。

### 6.2 Run Compact

Run Compact 在当前 Run 的 LangGraph 节点中运行，只处理本次 Run 内不断增长的模型消息、工具结果、中间进展和历史验证反馈。短 Run 不强制压缩；满足以下任一条件时触发：

1. Runtime Context 达到当前 primary model 有效输入预算的 85%。
2. Run 内模型消息或工具结果累计长度超过配置阈值。
3. Run 从 `running` 进入 `waiting_*`，需要形成稳定恢复说明。
4. resume 后发现原执行上下文已经过大。
5. verify / repair 循环次数超过配置阈值。
6. 用户显式要求总结当前任务进展。

输出契约：

```json
{
  "run_summary": {
    "goal": "...",
    "progress": [],
    "completed_steps": [],
    "run_decisions": [],
    "blockers": [],
    "evidence_refs": [],
    "artifact_refs": [],
    "next_step": "..."
  },
  "covered_through_run_message_id": "rmsg_80"
}
```

Run Compact 不修改 Session Context，也不得摘要化 Graph node、pending writes、精确工具参数、waiting request、interrupt / resume 数据、工具幂等状态或 checkpoint metadata。

每条 `run_message` 必须有稳定 ID。Run Compact 只能覆盖完整结束并已安全沉淀的 `Tool Exchange Block`，`covered_through_run_message_id` 不得停在 call/result 中间，也不得跨越 `pending`、`started` 或 `unknown` Block。压缩成功后只从最新 Graph State 的活动消息窗口中移除已被 `run_summary` 覆盖的旧消息，保留最近消息和完整 Tool Exchange；历史 checkpoint 是否保留由 checkpoint retention 决定。

### 6.3 Token 预算

LLM 调用前估算 Runtime Context token 数达到当前 primary model 有效输入预算的 85% 时，优先压缩 Run 内语义历史，再按剩余预算裁剪最近消息。`ModelCapabilityResolver` 分开保存输入输出共享总窗口、独立输入上限和独立输出上限；每次调用根据本次实际请求的输出额度计算输入限制：

```text
request_input_limit = min_defined(
  max_input_tokens,
  context_window_tokens - requested_max_output_tokens
)

effective_runtime_budget =
  request_input_limit
  - reserved_system_and_tools_budget
  - safety_margin_tokens

compact_threshold =
  effective_runtime_budget * 0.85
```

`min_defined` 只比较 Provider 明确提供的限制项。`max_input_tokens` 只表示独立输入硬上限，不能预先扣除输出；`context_window_tokens` 表示输入输出共享总窗口，只有这一项需要减去本次 `requested_max_output_tokens`。如果 Provider 只提供其中一种限制就直接使用该项；如果限制语义无法确认，按共享总窗口保守处理。`max_output_tokens` 仍只表示 Provider 输出硬上限，不得代替输入限制。

正常执行只按 primary model 计算，不预先受 fallback model 限制。只有 primary 调用在尚未产生有效响应、也尚未开始本轮工具副作用时发生可 failover 错误，Runtime 才使用 fallback model 的能力和本次输出额度重新计算 `request_input_limit`、执行 Context Builder，并在必要时再次触发 Run Compact；该临时小窗口投影不得覆盖共享 Session Context。

单 Agent 不单独配置 Compact 模型。Run Compact 使用当前实际执行该 Run 的模型：正常路径使用 primary model，发生安全 failover 后使用当前 fallback model；单 Agent 的 Session Compact 使用该 Agent 当前配置模型。多 Agent 共享上下文压缩必须显式配置独立的 `compact_model_id`，不得临时从参与 Agent 中挑选模型；压缩触发预算仍按所有参与模型中最小的有效输入预算计算。

压缩或裁剪后仍必须保持 Tool Exchange 原子性和 Tool Pair Integrity。

### 6.4 Run 完成后的 Session 沉淀

Run 完成后只提交增量，不提交整份 Session Summary：

```json
{
  "source_run_id": "run_123",
  "new_requirements": [],
  "new_decisions": [],
  "resolved_open_items": [],
  "new_open_items": [],
  "evidence_refs": [],
  "workspace_refs": [],
  "result_summary": "..."
}
```

`SessionContextService` 校验来源、权限、引用和当前版本后合并。合并成功后，该 terminal Run 默认从后续 Runtime Context 中退出；Run 记录和 checkpoint 是否保留由 retention 策略决定，与是否进入上下文无关。

## 7. Agent Run 接入流程

Agent Run 接入的目标是让 Web、渠道、Trigger、Task、Heartbeat 和 A2A 逐步统一进入 Runtime Adapter 和 LangGraph，而不是长期沿用多套 tool-call loop。

v1 接入原则：

1. 所有新路径通过 `start_run`、`resume_run`、`cancel_run`、`get_run_state` 和 `stream_run` 接入。
2. 每个 run 使用 `agent_run.id` 作为 LangGraph `thread_id`。
3. LangGraph stream 通过 Runtime Event Mapper 转换为 Clawith 稳定事件。
4. Runtime Projector 根据已提交 checkpoint 幂等更新 `agent_runs.projected_*` 和生命周期 `agent_run_events`；这些投影不参与执行决策。
5. 所有工具通过 `agent_tool_executions` 幂等执行。
6. 迁移期保留 legacy feature flag，但一个 run 创建后不得在两种 runtime 之间切换。

### 7.1 用户消息入口

用户发消息后：

```text
1. 解析 ChatSession。
2. 保存用户 ChatMessage。
3. 如果消息明确关联一个 `waiting_user` run，调用 `resume_run`。
4. 否则调用 `start_run` 创建新的 foreground run。
5. Runtime Adapter 构造 Runtime Context 并启动或恢复 Graph。
6. WebSocket 消费 Runtime Event；非流式渠道消费阶段事件和最终结果。
7. Graph 完成后通过幂等交付服务保存 assistant ChatMessage；execution 由 terminal checkpoint 持久化，delivery 由 Clawith 交付事务更新。
8. 用户显式 abort 时调用 `cancel_run`；连接断开本身不等于取消。
```

消息入口不得在 ChatMessage 提交后只通过内存调用创建 Run。需要唤醒 Agent 的消息必须在同一 PostgreSQL 事务内同时创建 Run Registry 和 `start` Command：单 Agent 消息直接创建目标 Run，多 Agent 群消息创建 Planning Run。Planning Graph 输出 `parallel`、`sequential` 或 `dependency` 策略，并使用稳定 `source_execution_id` 只幂等创建当前依赖已经满足的子 Run 与 start Command；仍有子步骤时把权威 `lifecycle_status` 保存在 Planning checkpoint 中。Worker 通知失败时由 pending Command 扫描和 reconciliation 续跑。

这里新增的是专用 `agent_run_commands` Inbox，不是再建一套执行状态，也不是泛化业务 Outbox。它只承载 Runtime 输入，解决入口事务与 checkpoint 提交之间的崩溃窗口。

Planning 模型必须通过 `MULTI_AGENT_PLANNING_MODEL_ID` 单独配置，不使用业务 Agent 或共享 Compact 模型代替。配置缺失、无效或最终调用失败时，Planning Run 进入 `failed`，不创建子 Run，并在原 ChatSession 写入一条脱敏的可见系统消息；原 Session 不可用时只记录交付失败，不改写到其他 primary。

Planning Run 使用 `run_kind = orchestration`、`agent_id = null` 和 `system_role = group_planning`，不伪造业务 Agent 或 Participant。创建时把配置解析得到的真实模型 ID 固化到 `agent_runs.model_id`；后续切换 Planning 模型配置只影响新 Run，已有 Run 恢复时继续使用原 `model_id`。

### 7.2 Trigger 入口

trigger 唤醒时：

```text
1. 使用 `TriggerExecution.id` 作为稳定 `source_execution_id`。
2. 普通 Trigger 调用 `start_run(source_type=trigger)`；重复投递命中同一 run。
3. 如果 Trigger 是某个 waiting run 的外部返回，则通过 `run_id + correlation_id` 调用 `resume_run`。
4. Runtime Adapter 推进 Graph 并投影 TriggerExecution 状态。
5. Trigger 不再维护独立 tool-call loop。
```

### 7.3 A2A task_delegate

source Agent 发起 `task_delegate` 时：

```text
1. source run 创建独立 target run，并写入 `parent_run_id` / `root_run_id` / `correlation_id`。
2. source Graph 进入 interrupt，checkpoint 的 `lifecycle_status` 为 `waiting_agent`；产品投影随后更新。
3. target run 使用自己的 LangGraph thread 独立执行。
4. target 完成后保存 result summary 和 artifact refs。
5. callback 通过 source run ID 和 correlation ID 调用 `resume_run`。
6. source 从原 checkpoint 恢复，Graph State 进入 running 或 verifying。
```

A2A 只限制当前 Run 祖先链中的 Agent 循环，不设置正常链路的总深度或总派生次数。系统把每个 delegated Run 表达为 `(origin_agent_id → agent_id)` 有向边；同一条边在当前父链中每重复出现一次，循环次数增加 1。候选派生会使累计循环次数达到 5 时拒绝创建 target Run，向 source Run 返回 `agent_cycle_limit_reached`，由 source Agent 继续完成并告知用户。

### 7.4 task_executor

现有 `task_executor` 从一次性执行改为 run 驱动：

```text
1. 每次 Task 实际执行创建独立 agent run，`Task.id` 作为业务来源。
2. Task run 通过统一 Graph 执行，不再使用 `call_agent_llm_with_tools()` 的独立循环。
3. 若等待外部，run 进入 waiting；Task 看板状态按产品规则投影。
4. 验证通过后 run 进入 completed，再更新 Task 为 done。
5. 周期性 supervision Task 每次执行创建新 run，不复用旧 checkpoint。
```

### 7.5 Heartbeat / oneshot

Heartbeat 和 oneshot 统一创建后台 run：

```text
1. 创建 `source_type=heartbeat` 或对应后台来源的 agent run。
2. 通过 Runtime Adapter 启动 Graph。
3. 没有实际工作时快速完成；需要等待时进入 waiting。
4. 删除 Heartbeat / oneshot 内部独立的多轮 tool-call loop。
```

## 8. 权限和数据边界

1. 读取 Clawith 业务表和 LangGraph checkpoint 前，必须先按 session / tenant / agent / origin 权限校验。
2. A2A 的 source run 和 target run 使用不同 checkpoint，只共享完成任务必需且明确授权的 delegation input、result summary 和 artifact refs。
3. 业务表与 checkpoint 只保存必要状态、摘要和引用，大段工具结果和外部文档正文继续保存在原始来源。
4. `agent_run_events` 是 append-only 产品事件流；checkpoint 派生事件通过重放修复，Clawith 原生交付事件的业务修正通过追加新事件表达。
5. `agent_tool_executions` 的租户和 run 归属必须与 `agent_runs` 一致。
6. checkpoint 不保存 API key、access token、数据库凭据或不可序列化运行对象。
7. 客户端不得直接指定或查询任意 LangGraph `thread_id`；Runtime 必须先用 `run_id` 读取 Run Registry、校验 tenant 和 actor，再由服务端解析 thread。
8. Run 创建时固化 `graph_name + graph_version`。waiting / interrupted Run 恢复时继续使用原版本；可能被旧 checkpoint 恢复到的 node 在对应 active Thread 清零前不得 rename 或删除。

### 8.1 Session 删除后的 Runtime Context 生命周期

用户侧删除 session 属于 soft delete。

系统不立即物理删除该 session 关联的 `session_context_states`、`agent_runs`、`agent_run_commands`、`agent_run_events`、`agent_tool_executions` 和 checkpoint，但必须将其从 Runtime Context 查询和注入路径中排除。

被删除 session 的压缩摘要不得注入新 Run，也不得影响新的前台回复。删除前已经创建的独立后台 Run 可以继续使用自己 checkpoint 中已经固化的执行状态，但不得重新读取该 Session 的最新摘要或消息；历史 run state 和 run events 仍可用于恢复、审计、排查或延迟清理。

v1 可优先只依赖 `ChatSession.deleted_at` 判断；Runtime Context 查询时过滤 `deleted_at is null` 的 session。后续如需要更清晰的生命周期管理，再为 runtime 表补充 `archived_at` 或 `deleted_at` 字段。

direct 和 group session 删除统一使用 soft delete。删除 primary 时按所属 scope 自动选举最近活跃的未删除 Session；没有 replacement 时允许该 scope 暂时没有 primary。

删除只取消依赖该 Session 的前台协作：`foreground`、`orchestration` 及其派生的 `delegated` Run。系统写入幂等 cancel Command；尚未建立 checkpoint 的 start Command 被拒绝，已有 Thread 由 Graph 在安全节点进入 `cancelled`。已经开始的工具先记录真实结果，不自动回滚。Task、Trigger、Heartbeat 和 oneshot 等具有独立业务来源的 `background` Run 不取消，继续按原 checkpoint 执行，但不再读取被删除 Session 的最新 Context。

前台 Run 不回退 replacement primary。后台 Run 交付结果时优先使用原 Session；写入前已确认原 Session 删除、不存在或不可写时，回退到同一 direct/group scope 当前 primary。没有 primary 时记录交付失败且不自动创建 Group Session。原目标写入结果处于 unknown、可能已成功时必须先幂等对账，禁止立即 fallback 造成重复交付。

## 9. 实施顺序

### 前置条件：统一聊天模型强制迁移

开发首先编写并在测试数据库验证 `../group-chat/chat-model-refactor.md` 第 7 章的统一聊天模型强制迁移及 Runtime 表迁移；生产环境不能先于新 Single Chat 后端执行迁移。生产切换时，迁移与新 Single Chat 后端必须在同一维护窗口发布。该 Schema 切换不采用新旧字段长期双写，也不允许依赖旧 Schema 与新 Schema 的后端版本混跑。

下述 Phase 0–5 描述的是 Runtime 能力和各执行入口的渐进迁移，不代表统一聊天 Schema 继续处于双写状态。Runtime 入口可以使用 `runtime_type` 和 feature flag 分阶段切换，但同一个 run 创建后不得跨 runtime 切换。

### Phase 0：Schema、迁移与依赖基线

- 编写并验证统一聊天模型迁移、五张 Runtime 表、群 mention 调度字段、Model Capability 字段及全部约束和索引。
- 锁定 LangGraph / Checkpointer 版本并建立独立 `langgraph_checkpoint` schema 的 setup / upgrade 流程。
- 只在测试数据库完成迁移、回滚、历史数据校验和 Checkpointer smoke test；生产迁移等待 Single 后端同窗口切换。

### Phase 1：Single Runtime Core 与 Task PoC

- 建立 Runtime Adapter、Command Worker、checkpoint Projector、thread advisory lock、协作式取消、Tool Execution Ledger、交付和 reconciliation。
- 选择 `task_executor` 作为第一条实验入口，验证 PostgreSQL checkpoint、服务重启恢复、interrupt/resume、cancel、重复 Command 和副作用工具幂等。

### Phase 2：Single Chat 与全部单 Agent 后端入口

- Web Chat 通过 Runtime Adapter 创建 foreground run。
- LangGraph stream 映射成现有 WebSocket 事件。
- 接入组合式 Runtime Context、Session Compact、Run Compact 和 token budget。
- 支持 abort → cancel run、waiting_user → resume run。
- 迁移 Task、Trigger、Heartbeat 和 oneshot。
- 迁移飞书及其他渠道。
- 渠道只保留消息解析、session 解析和结果交付，不再维护 tool loop。
- `task_delegate` 建立 source run / target run。
- callback 通过 correlation ID 恢复 source run。
- 收敛 `consult` 和 `notify` 的 run/event 语义。

### Phase 3：Group Chat 后端

- 实现群领域、mention、Planning Graph、`parallel / sequential / dependency` 调度、群上下文、共享 Compact 和群消息交付。
- 同一 Agent 的多条群 mention 按 Message Position 通过 scheduling lane 串行；该 lane 不限制 Direct、Task、Trigger、Heartbeat 等其他 Run。

### Phase 4：后端整体验证与旧 Runtime Loop 清理

- 完成 Single、Group、渠道、A2A、删除、Compact、故障恢复和维护窗口演练。
- 删除 `call_agent_llm_with_tools()` 和 Heartbeat / oneshot 的独立循环。
- `call_llm()` 降级为 Graph node 内的单次模型调用能力。
- 所有生产入口稳定后移除 legacy feature flag。

### Phase 5：前端统一更新

- 后端契约稳定后统一更新 Single Chat 与 Group Chat 前端。
- 前端只消费产品 API、ChatMessage 和稳定 RuntimeEvent，不读取 checkpoint 或内部 Graph node。

## 10. 最终方案：LangGraph 主 Runtime + Clawith 产品投影

<callout emoji="✅">
**最终结论：**LangGraph 负责完整执行生命周期，是唯一执行状态源；Clawith 保留产品事实、可靠 Command、Session Context、工具副作用 receipt、交付事实和可重建查询投影。两层不双写同一种权威状态。
</callout>

### 10.1 总体架构

```text
Clawith 产品与渠道层
Web / 飞书 / Trigger / Task / A2A / Heartbeat
↓
Agent Runtime Adapter
├─ 产品事务 → Run Registry + Runtime Command
└─ Command Worker → LangGraph 执行层
                      ↓
              PostgreSQL Checkpoint
                 ├─ Projector → Clawith 查询投影
                 └─ 幂等副作用 → Tool / Delivery Receipt
```

Clawith 自建表保存稳定产品语义和可靠输入，LangGraph 保存全部可执行状态。上层业务通过 Runtime Adapter 提交 Command，不直接依赖 LangGraph 内部对象和存储结构。

### 10.2 两层职责划分

| 层 | 主要职责 | 不负责 |
|-|-|-|
| Clawith 产品层 | 租户和 Agent 权限、任务归属、来源入口、A2A 委托、可靠 Command、摘要查询、证据引用、交付状态和产品投影 | 不自行实现 Run 生命周期、waiting、node checkpoint、pending writes 和精确恢复 |
| LangGraph 执行层 | Run 生命周期、tool-call loop、状态路由、步骤 checkpoint、interrupt、resume、失败恢复、待执行节点和最终执行结果 | 不作为 Clawith UI、权限和运营查询的直接数据模型 |

### 10.3 Runtime 表调整

#### agent_runs：Run Registry + 交付事实 + 查询投影

`agent_runs` 表示 Clawith 中登记的一件业务任务。静态归属和交付字段是产品事实，执行字段只是查询投影：

- `runtime_type`：当前执行引擎，例如 `langgraph`。
- `runtime_thread_id`：关联 LangGraph thread，建议默认使用 `agent_run.id`。
- `projected_checkpoint_id`：Projector watermark；可丢失并重建。
- `projected_execution_status` / `projected_waiting_type` / `projected_result_summary`：checkpoint 派生投影。
- `delivery_status`：结果交付状态，与执行完成分开。
- `parent_run_id` / `root_run_id`：A2A 和派生任务关系。

`goal`、`source_type`、`origin_user_id`、`origin_agent_id` 和权限关系继续由 Clawith 管理。任何 Graph 路由和恢复不得读取 `projected_*`。

#### agent_run_commands：可靠 Runtime 输入

入口事务把 start / resume / cancel 写入 `agent_run_commands`。Command Worker 领取后调用 LangGraph；Graph State 记录 Command ID 用于崩溃对账和重复输入抑制。Command 的 pending/applied 只表示输入传输状态，不表示 Run 生命周期。

#### session_context_states：保留为会话上下文事实源

`session_context_states` 保存当前 session 可快速读取和注入的摘要、用户要求、决策、未完成事项、证据、workspace 引用、watermark 和版本。它是 Session Context 的唯一事实来源，用于 Runtime Context 构造、UI 查询和多渠道复用，但不是执行 checkpoint，也不承担精确恢复。

LangGraph State 只保存当前 Run 创建或恢复时使用的 `session_context_snapshot + session_context_version`，不得把 Run 内摘要直接投影或覆盖回本表。Run 完成后只提交 `SessionContextDelta`，由 `SessionContextService` 使用版本和 watermark 校验合并。

#### agent_run_events：收缩为产品关键事件

`agent_run_events` 不重复保存 LangGraph 的全部 node、model call、checkpoint 和内部 state update，只记录 Clawith 产品关心的稳定事件：

- `run_created`
- `status_changed`
- `waiting_started`
- `resumed`
- `evidence_added`
- `verification_updated`
- `run_completed` / `run_failed` / `run_cancelled`
- `delivery_failed`

生命周期事件带 `source_checkpoint_id`，由 Projector 幂等派生并可重建。底层 node writes、pending tasks 和恢复位置由 LangGraph checkpoint 负责；模型和工具的详细观测数据由 trace 层负责。

#### agent_tool_executions：新增为工具幂等账本

`agent_tool_executions` 不承担 trace 职责，只记录 `run_id + tool_call_id` 对应的执行预占、结果状态和产物引用。

- 已成功的工具在恢复后复用原结果，不重复执行。
- 副作用工具超时且结果未知时进入 `unknown`，禁止自动重试。
- 消息发送、文档写入、任务创建、日程审批、A2A 委托等写操作都必须接入该账本。

### 10.4 LangGraph Agent State

每个 `agent_run` 对应一个独立的 LangGraph thread。建议 State 至少包含：

```text
tenant_id
agent_id
session_id
run_id
lifecycle_status
lifecycle_reason
last_applied_command_ids
session_context_snapshot
session_context_version
recent_session_messages_snapshot
run_messages
run_summary
covered_through_run_message_id
related_run_summaries
pending_tool_calls
waiting_request
verification_result
final_answer
error
```

不建议直接使用 `ChatSession.id` 作为唯一 LangGraph thread ID，因为同一个 session 内可能同时存在用户任务、trigger 任务和等待 A2A 返回的旧任务。使用 `agent_run.id` 可以隔离每个可恢复任务；会话级信息继续通过 `session_context_states` 聚合。

Thread 不建立独立摘要。`run_messages` 的增长由 Run Compact 处理；Graph node、pending writes、interrupt 和 resume 状态由 checkpoint 原样保存。历史 checkpoint 不进入模型上下文，只通过 retention / pruning 管理存储。

### 10.5 Graph 节点建议

1. **prepare_context**：新 Run 初始化时读取 Agent static context、版本化 Session Context Pack、当前 Run、相关 Run 摘要和最近 20 条用户可见消息；resume 时复用 checkpoint 中的原快照并追加明确恢复输入。
2. **compact_run_if_needed**：根据实际 token budget 判断是否压缩 Run 内语义历史，只更新 `run_summary` 和 Run 消息窗口。
3. **call_model**：复用现有 LLM client、模型配置、token tracking 和 failover。
4. **execute_tools**：复用现有工具定义、权限校验和 `execute_tool`。
5. **wait_for_external**：等待用户、A2A、外部系统或时间条件时调用 interrupt。
6. **verify**：根据真实工具结果和验证结果判断是否完成。
7. **finalize**：验证通过后生成 terminal Graph State、最终回复、SessionContextDelta 和 delivery request。
8. **deliver_result**：checkpoint 提交后幂等写用户可见结果和交付事实。
9. **handle_error**：在 Graph State 记录可恢复或不可恢复错误并决定等待、重试或失败。

现有 `finish(content=...)` 只表示模型声明完成，Graph 必须先进入 `verify`；验证通过后才能进入 `finalize` 和 `completed`。

### 10.6 一致性规则

- **执行状态唯一来源：**生命周期、waiting、最终执行结果、node、next step、pending writes 和 resume cursor 只以 LangGraph checkpoint 为准。
- **会话上下文唯一来源：**Session Summary、requirements、decisions、open items 和引用只以 `session_context_states` 最新版本为准。
- **产品事实唯一来源：**任务归属、租户权限、A2A 关系和交付状态以 Clawith 业务表为准；UI 执行状态是 checkpoint 投影，不是独立事实。
- **Run 选择：**模型上下文只注入当前 Run 和明确相关 Run 的结果摘要，不按 session 批量注入全部 active / terminal Run。
- **工具幂等：**有外部副作用的工具使用 `run_id + tool_call_id` 生成幂等键，避免恢复或重试造成重复发送、重复创建和重复写入。
- **单向投影：**Projector 只执行 checkpoint → `agent_runs.projected_*` / lifecycle events；禁止投影反向修改 checkpoint。
- **可靠输入：**入口只写 `agent_run_commands`，Command Worker 是调用 Graph 的唯一入口。
- **权限前置：**读取 checkpoint 前先通过 `agent_runs` 校验 tenant、agent、session 和 origin 权限。
- **框架隔离：**所有入口只依赖 `start_run`、`resume_run`、`cancel_run`、`get_run_state` 和 `stream_run` 等 Clawith Runtime 接口。

### 10.7 分阶段实施

最终实施顺序以第 9 章 Phase 0–5 为准：Schema/迁移与依赖基线 → Single Runtime Core 与 Task PoC → 全部 Single 后端 → Group 后端 → 后端整体验证与旧循环清理 → 前端统一更新。

每个阶段必须独立灰度和验收；在所有生产入口完成迁移前保留 legacy path，但同一个 run 创建后不得跨 runtime 切换。

### 10.8 本方案的收益

- Clawith 保有自己的业务语言、权限模型、查询能力和 UI 数据契约。
- 避免自行实现复杂的 checkpoint、pending writes、interrupt 和 durable execution。
- LangGraph 可替换，业务表不会因 runtime 框架变化而整体迁移。
- Session Context 适合多渠道快速构造 Runtime Context，checkpoint 负责精确执行恢复。
- 可以分入口渐进迁移，降低一次性重写现有多渠道和 A2A 路径的风险。

<callout emoji="💡">
**范围说明：**在 LangGraph 接管 tool loop 之前，Clawith 只能支持基于摘要和旧执行路径的语义续接，不能声称具备 checkpoint 级精确恢复。LangGraph 接入完成后，恢复能力以实际 node 边界、checkpoint durability、thread 推进互斥和工具幂等实现为准。
</callout>

详细的依赖包、模块目录、数据库字段与索引、Graph 伪代码、入口时序、并发恢复、配置项、迁移文件清单和测试标准见同目录：`technical-design.md`。

## 11. 产品验收标准

本期完成需要同时满足：

1. **统一入口：**至少 Web Chat、Task、Trigger 和 Heartbeat 能按实施阶段通过同一个 Runtime Adapter 执行；完成迁移的入口不再保留独立 tool loop。
2. **可恢复：**服务重启或 worker 退出后，未完成 run 能从同一 LangGraph checkpoint 继续，而不是从头重做。
3. **状态可见：**用户或上层系统能查询 run 的目标、来源、执行状态、等待类型、结果和交付状态。
4. **等待可续接：**`waiting_user`、`waiting_external` 和 `waiting_agent` 都能通过明确 run ID 和 correlation ID 恢复。
5. **工具不重复：**同一 `run_id + tool_call_id` 的已成功副作用工具在恢复或重复投递时不会再次执行。
6. **上下文连续：**长 session 在触发压缩后仍保留已确认决策、未完成事项、证据引用和最近消息，原始消息不删除。
7. **最近消息保留：**Web Chat 默认保留最近 20 条用户可见消息，优先保留最近用户原话，并继续保证 Tool Pair Integrity。
8. **双层压缩隔离：**Session Compact 只由 `SessionContextService` 更新；Run Compact 只更新当前 Thread 的 Run State，不直接覆盖 Session Context。
9. **Run 相关性选择：**模型上下文只包含当前 Run 和明确相关 Run 摘要，无关、terminal 或长期未使用 Run 不因仍在数据库中而被注入。
10. **Tool Exchange 原子性：**窗口裁剪和 Run Compact 只能整体保留、移出或压缩完整 Tool Exchange，不静默删除单侧 call/result。
11. **副作用安全：**不完整 Tool Exchange 必须结合工具账本重建、等待或对账；只有确认工具从未执行时才能重新调用模型。
12. **A2A 可追踪：**source run 和 target run 独立存在并显式关联，target 返回后 source 能恢复。
13. **执行与交付分离：**run completed 后即使渠道交付失败，也只重试 delivery，不重新执行任务。
14. **权限不扩大：**checkpoint、Session Context、A2A context 和工具结果继续遵守 tenant、agent、session 和 origin 权限边界。
15. **可灰度回滚：**legacy / langgraph 可按 Agent 或入口灰度；已有 langgraph run 不允许中途回退 legacy。
16. **模型职责可控：**业务模型只输出局部 `tool_calls / wait / finish` 意图；生命周期、调度、幂等、取消、恢复和交付均由确定性 Runtime 负责。
17. **取消可恢复：**cancel 经过 control guard 在安全边界生效；已开始工具记录真实结果，Worker 崩溃后仍能从 checkpoint 继续取消。
18. **群 mention 有序且不全局阻塞：**同一 Agent 的多条群 mention 按 Message Position 串行，Direct、Task、Trigger、Heartbeat 等其他 Run 不进入该调度 lane。

## 12. 本期不做

- 不把 LangGraph checkpoint 直接作为产品数据库或 UI 查询模型。
- 不引入独立 Agent Server 作为第一阶段部署形态。
- 不要求把现有 LLM provider client 全部改成 LangChain ChatModel。
- 不使用 LangGraph Store 替代 Clawith 的长期记忆和业务数据。
- 不把 `agent_run_events` 扩展成完整模型/工具 trace 平台。
- 不在 v1 自动重试结果为 `unknown` 的副作用工具。
- 不为历史消息和历史任务回填 checkpoint。
- 不在所有入口完成迁移前删除 legacy runtime。
