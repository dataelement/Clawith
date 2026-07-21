# Clawith 单 Agent Runtime 终版技术方案

> 状态：开发基线（终版）
> 需求基线：`runtime-context-compression-prd.md`，飞书 revision `142`
> 核心结论：LangGraph 是唯一执行状态源，Clawith 只保存产品事实、可靠命令、外部投递 Outbox 和可重建投影

## 1. 最终决策

Clawith 保留自己的产品数据模型，同时把 Agent tool loop、生命周期状态、checkpoint、interrupt、resume 和 durable execution 全部交给 LangGraph。不能让 `agent_runs` 与 LangGraph 同时拥有一套可推进的执行状态机。

```text
Web / 飞书 / 其他渠道 / Trigger / Task / A2A / Heartbeat
                              ↓
                    AgentRuntimeAdapter
                              ↓
                 LangGraph Runtime
                  ↓            ↓
       PostgreSQL Checkpoint   Runtime Event Projector
                  ↓            ↓
             唯一执行状态      Clawith 可重建投影
```

最终事实源分工：

| 事实 | 唯一事实来源 |
|-|-|
| 租户、Agent、用户、权限、来源入口、A2A 关系 | Clawith 业务表 |
| Run 生命周期、当前/待执行节点、waiting、resume cursor、pending writes、最终执行结果 | LangGraph 最新 checkpoint |
| Run 身份、租户、来源、归属、父子关系和固定运行配置 | `agent_runs` 静态 Run Registry |
| start / resume / cancel 的可靠输入 | `agent_run_commands` |
| 已持久化 ChatMessage 的外部 Provider 投递 | `channel_deliveries` |
| UI 查询所需的执行状态 | `agent_runs.projected_*` 可重建投影 |
| 用户可见交付状态 | `agent_runs.delivery_status` 与幂等 ChatMessage |
| 会话摘要、已确认决策、未完成事项 | `session_context_states` |
| 产品关键事件 | `agent_run_events` |
| 有副作用工具的幂等执行结果 | `agent_tool_executions` |
| 原始消息 | `chat_messages` |

禁止让 `agent_runs.projected_execution_status`、`agent_run_events` 或 `session_context_states` 参与 Graph 路由、恢复、重试、取消判定或工具执行。业务投影允许短暂落后，也必须可以完全从 checkpoint 和产品事实重建。

这不是“业务状态与 checkpoint 双写并保持强一致”的方案，而是单写者 + 异步投影：

1. 入口事务只写产品事实与可靠 Command，不写 LangGraph 执行状态。
2. LangGraph 每个 step 只通过 Checkpointer 持久化执行状态。
3. Projector 在 checkpoint 提交后读取 `StateSnapshot`，幂等更新产品投影。
4. Projector 失败不影响 Graph 正确性；恢复、调度和重试绝不读取旧投影做决定。

Session、Run、Thread 的固定关系：

```text
Session 1 ── N Run
Run     1 ── 1 LangGraph Thread
Thread  1 ── N Checkpoint
```

- Session 是长期对话容器，`session_context_states` 是其压缩上下文唯一事实来源。
- Run 是一次有目标、可等待和可恢复的业务执行单元。
- Thread 是 Run 的 checkpoint 命名空间，`thread_id` 默认等于 `run_id`，不是第三种 Context。
- 语义压缩只有 Session Compact 和 Run Compact；Thread 只做持久化、恢复和 retention / pruning。

## 2. 迁移前实现基线

下表记录本分支改造前的入口状态，用于说明为什么必须统一；它不是当前 Runtime 实现现状：

| 入口 | 迁移前执行路径 | 主要问题 |
|-|-|-|
| Web Chat | `api/websocket.py` → `call_llm_with_failover()` → `call_llm()` | tool loop 位于函数内部 |
| 飞书及其他渠道 | 各渠道入口 → `call_llm()` | 各入口自行截断历史、组装消息 |
| Trigger | `trigger_runtime/invoker.py` → `call_llm()` | 有 trigger session，没有可恢复 run |
| Task | `task_executor.py` → `call_agent_llm_with_tools()` | 存在第二套 tool loop |
| Heartbeat / oneshot | `heartbeat.py` 内部独立循环 | 存在第三套 tool loop |
| A2A | `agent_tools.py` 内同步调用或 trigger 唤醒 | 没有 source run / target run 关系 |

迁移前 Context 只有保护性截断：

- `Agent.context_window_size` 表示消息条数，默认 `100`，不是模型 token context window。
- Web、飞书等入口使用 `truncate_messages_with_pair_integrity()`；当时的实现先按条数硬切，再静默删除孤立 call/result，只保证 Provider 格式合法，不能保证已经执行的工具语义不丢失。
- OpenAI Responses Adapter 还会再次静默移除孤立 `function_call` / `function_call_output`，存在模型忘记已执行副作用并重新调用的风险。
- tool result 持久化时存在长度裁剪。
- `LLMModel.max_output_tokens` 只表示输出预算，当时没有可靠的 `max_input_tokens` 或统一 Model Capability Resolver。
- 当时没有 Session Summary、运行 checkpoint 或统一 resume 机制。

因此迁移目标不是只给 Web Chat 包一层 Graph，而是让所有 Agent 执行入口逐步汇聚到同一个 Runtime Adapter。

## 3. 技术选型边界

### 3.1 嵌入式 LangGraph

第一阶段把 LangGraph 作为 Python 库嵌入现有 FastAPI 后端，不引入独立 Agent Server：

1. 继续复用现有 `LLMClient`、模型池、failover、token tracking 和供应商适配。
2. 继续复用 `get_agent_tools_for_llm()`、`execute_tool()` 和现有权限校验。
3. 不把租户身份、数据库访问和 Clawith 工具迁移到另一个服务边界。
4. 先验证恢复价值，再决定是否拆出独立 Runtime 服务。

LangGraph 负责 Graph 编排、Run 生命周期和 checkpoint，但不要求把现有模型调用改成 LangChain ChatModel。

### 3.2 持久化

生产使用 `AsyncPostgresSaver`，连接现有 PostgreSQL，但 checkpoint 表固定放在独立 `langgraph_checkpoint` schema，与 Alembic 管理的业务表逻辑隔离。Runtime 使用独立 DSN / `search_path` 访问该 schema；Phase 0 必须用实际锁定版本验证官方 saver 的 setup、读写和升级流程。

- `thread_id = str(agent_run.id)`，使用 UUID。
- 每个 Run 创建时固化 `graph_name + graph_version`；恢复必须从版本化 Graph Registry 取得相同版本的 compiled graph，不能自动使用当前最新 Graph。
- Checkpointer setup/升级进入部署流程，不在请求中执行。
- checkpoint 必须有 retention 策略。
- checkpoint 中不保存数据库 session、client、token 或其他运行对象。
- 数据库连接、工具注册和调用者信息通过 LangGraph runtime context 传入。
- Checkpointer 只能由 Runtime Service 访问。外部 API 先通过 Run Registry 校验 tenant / actor / session，再由服务端生成 `thread_id` config；禁止接受客户端直接指定任意 thread ID。

Graph 升级规则：

1. 新增 node、edge 或兼容 State key 时发布新 `graph_version`，只影响新 Run。
2. waiting / interrupted Run 继续加载旧版本；旧 node 名和路由代码必须保留到该版本所有 active Thread terminal 或完成显式迁移。
3. 不原地 rename / remove waiting Thread 可能恢复到的 node，不原地改变已有 State key 的不兼容类型。
4. Checkpointer package schema setup / migration 与业务 Alembic 分开执行，但必须纳入同一部署前检查和备份流程。
5. 不直接读写 LangGraph 私有 checkpoint 表修复业务问题；通过 Checkpointer API 和版本化迁移工具处理。

### 3.3 v1 暂不引入

- LangGraph Store 作为 Clawith 长期记忆。
- LangSmith 作为产品运行依赖。
- 多 Graph / subgraph 复杂编排。
- 业务 UI 直接读取 checkpoint。
- 生产 time travel 或 run fork。

### 3.4 最小依赖包

本方案不是只借鉴 LangGraph 的设计，而是实际使用它的 Graph Runtime 和 Checkpointer。否则 checkpoint、pending writes、interrupt/resume、线程恢复和并发推进都要由 Clawith 重新实现，LangGraph 主 Runtime 方案就失去了主要价值。

后端最小新增依赖：

```toml
dependencies = [
    "langgraph",
    "langgraph-checkpoint-postgres",
    "psycopg[binary,pool]",
]
```

各包职责：

| 包 | 用途 | 是否必需 |
|-|-|-|
| `langgraph` | `StateGraph`、路由、`Command`、`interrupt`、stream | 必需 |
| `langgraph-checkpoint-postgres` | `AsyncPostgresSaver`、checkpoint、pending writes | 生产必需 |
| `psycopg[binary,pool]` | PostgreSQL checkpointer 使用的异步驱动和连接池 | 生产必需 |
| `langchain` | 高层 Agent、Prompt、Model 抽象 | 不需要 |
| `langgraph-sdk` | 调用独立 Agent Server | 不需要 |
| `langgraph-cli` | 本地 Agent Server / Studio 工作流 | 不需要 |
| `langsmith` | 托管 tracing / observability | 不作为运行依赖 |

Clawith 现有 SQLAlchemy 继续使用 `asyncpg`。新增 `psycopg` 只服务于官方 PostgreSQL checkpointer，两套驱动用途不同，不应为了复用连接池而自建 Checkpointer。

依赖版本必须在 Phase 0 开始时锁进项目 lock 文件。版本策略使用“固定 minor、允许 patch”，升级前运行 checkpoint 兼容性和恢复回归测试，不直接跟随 latest。

## 4. Runtime Adapter

所有入口只依赖统一接口：

```python
class AgentRuntimeAdapter(Protocol):
    async def start_run(self, command: StartRunCommand) -> RunHandle: ...
    async def resume_run(self, run_id: UUID, resume: ResumeCommand) -> RunHandle: ...
    async def cancel_run(self, run_id: UUID, reason: str | None = None) -> None: ...
    async def get_run_state(self, run_id: UUID) -> RunView: ...
    def stream_run(
        self,
        handle: RunHandle,
        *,
        after: RuntimeEventCursor | None = None,
    ) -> AsyncIterator[RuntimeEvent]: ...
```

`StartRunCommand` 至少包含：

```text
tenant_id, agent_id?, session_id?, source_type, source_id?,
origin_user_id?, origin_agent_id?, parent_run_id?,
input_message_id?, goal, run_kind, system_role?, model_id?, delivery_target?
```

Adapter 负责权限校验、Run Registry / Command 创建、Graph 调用门面、stream 转换和最终交付；持久化执行投影由独立 Projector 完成。上层渠道不得直接读取 checkpoint、调用 Graph node、修改 Run 投影或实现新 tool loop。

### 4.1 建议模块结构

```text
backend/app/
├── models/
│   ├── agent_run.py
│   ├── agent_run_command.py
│   ├── agent_run_event.py
│   ├── session_context_state.py
│   ├── agent_tool_execution.py
│   └── channel_delivery.py
├── services/agent_runtime/
│   ├── __init__.py
│   ├── contracts.py          # Command、RunHandle、RunView、RuntimeEvent
│   ├── adapter.py            # 对上层入口的唯一门面
│   ├── run_service.py        # Run Registry、Command 写入与交付事实
│   ├── command_worker.py     # start/resume/cancel Command 领取与 Graph 调用
│   ├── session_context_service.py # Session Compact、CAS 合并和 watermark
│   ├── projector.py          # checkpoint → 可重建 Run 投影与产品事件
│   ├── checkpointer.py       # AsyncPostgresSaver 生命周期
│   ├── graph.py              # StateGraph 构建和 compile
│   ├── state.py              # RuntimeState、RuntimeContext
│   ├── routing.py            # Graph 条件路由
│   ├── nodes/
│   │   ├── prepare_context.py
│   │   ├── compact_run.py
│   │   ├── call_model.py
│   │   ├── execute_tools.py
│   │   ├── wait.py
│   │   ├── verify.py
│   │   └── finalize.py
│   ├── context_builder.py    # token budget 和消息组装
│   ├── model_capabilities.py # Provider 能力发现、Registry、缓存与人工覆盖
│   ├── tool_exchange.py      # 原子 Block 构建、窗口选择、完整性校验和重建
│   ├── tool_execution.py     # 工具分类、幂等预占和结果落库
│   ├── event_mapper.py       # LangGraph stream → RuntimeEvent
│   ├── delivery.py           # 最终结果交付协调
│   ├── channel_delivery.py   # 外部 Provider 投递 Outbox 与 Worker
│   ├── channel_provider_delivery.py # Provider 适配和业务响应校验
│   └── reconciliation.py     # checkpoint 与业务投影修复
└── schemas/
    └── agent_runtime.py      # API schema
```

分层约束：

- `graph.py` 和 `nodes/` 不 import WebSocket、飞书、Trigger API。
- `adapter.py` 不直接实现模型或工具执行。
- `call_model.py` 只依赖现有 LLM port，不依赖具体渠道。
- `execute_tools.py` 只能通过 `tool_execution.py` 调用 `execute_tool()`。
- 渠道入口只负责把外部事件转换成 `StartRunCommand` / `ResumeCommand`。

### 4.2 Command 语义

`start_run()` 只创建新 thread；`resume_run()` 只恢复已经存在的 thread，二者不能自动互换。

- Web 用户继续 `waiting_user`：客户端显式携带 `run_id`；没有 `run_id` 时，只能在 session 内唯一存在一个 `waiting_user` run 的情况下自动关联。
- A2A 返回：使用 `correlation_id + source_run_id` 定位，不按最近 run 猜测。
- Trigger：`source_id` 保存 `AgentTrigger.id`，`source_execution_id` 使用稳定的 `TriggerExecution.id`；重复投递命中同一 run。
- Task：每次实际执行生成独立 run；`Task.id` 是业务来源，执行批次需要独立 `source_execution_id` 或 `attempt_no`。
- 新任务：即使同一 session 还有 waiting run，也创建新 thread，不覆盖旧 checkpoint。

需要由消息触发新 Run 时，入口服务必须在调用方已有的 PostgreSQL 事务中同时创建静态 `agent_run` Registry 记录和 `agent_run_commands(type=start)`。群内单 Agent mention 使用 `source_execution_id = group_mention:{message_id}:agent:{agent_id}`；多 Agent mention 先以 `group_mention:{message_id}:plan` 创建 Planning Run。Planning Graph 输出 `parallel`、`sequential` 或 `dependency` 策略，并以 `group_mention:{message_id}:step:{step_id}` 幂等写入 ready 子 Run 的 Registry 与 start Command。事务提交后只负责通知 Command Worker；通知失败由 Command 扫描恢复。

`agent_run_commands` 不是第二套执行状态机，也不是通用 Outbox。它只解决“产品事务已经接收 start / resume / cancel，但进程在调用 Graph 前崩溃”的可靠输入问题。Command 是否已应用是传输事实；应用后 Run 的生命周期完全由 LangGraph checkpoint 决定。

外部渠道使用另一个窄表 `channel_deliveries`：它只解决 ChatMessage 已持久化、但进程在调用 Provider 前后退出造成回复丢失的问题。Delivery Worker 不读取 checkpoint 决定 Graph 下一步，也绝不调用 start / resume / cancel；Command Inbox 与 Delivery Outbox 不能合并。

## 5. 数据模型

### 5.1 `agent_runs`

`agent_runs` 是静态 Run Registry、交付事实和查询投影，不是执行状态机，也不是 Worker 的恢复依据。

关键字段：

```text
id, tenant_id, agent_id?, session_id,
source_type, source_id, source_execution_id, correlation_id,
origin_user_id, origin_agent_id,
parent_run_id, root_run_id,
goal, run_kind, system_role, model_id,
runtime_type, runtime_thread_id, graph_name, graph_version,
scheduling_lane_key, scheduling_position_created_at,
scheduling_position_id, lane_held, lane_claimed_at,
projected_execution_status, projected_waiting_type,
projected_waiting_reason, projected_result_summary,
projected_error_code, projected_last_error,
projected_checkpoint_id, projection_updated_at,
session_context_applied_checkpoint_id,
delivery_status,
projected_started_at, projected_completed_at,
created_at, updated_at
```

所有 `projected_*` 字段都只允许由 Projector 写入，并可随时从 Checkpointer 重建。恢复时始终由 Checkpointer 按 `thread_id` 读取最新 checkpoint；任何 Graph 节点、Command Worker 或 reconciliation 都不得根据投影字段决定下一步。

`session_context_applied_checkpoint_id` 不是执行投影，而是终态副作用收据：它只表示该 Run 的 `SessionContextDelta` 已与哪一个 terminal checkpoint 一起幂等合并。每个 Run 最多写入一次；同一 checkpoint 重放为 no-op，不同 checkpoint 不得覆盖。该字段不能参与 Graph 路由、恢复或生命周期判断。

LangGraph State 中的权威执行状态：

```text
created → queued → running → verifying → completed
                    ↓   ↑
      waiting_user / waiting_external / waiting_agent
                    ↓
             failed / cancelled
```

规则：

- `completed` 表示 Graph 执行完成，不代表已经送达。
- `delivery_status` 为 `not_required / pending / delivered / failed`。Web / 内部 Session 在 ChatMessage 事务完成后即可 `delivered`；外部渠道创建 outbox 后保持 `pending`，直到 Provider 确认或达到失败上限。
- 同一 Session 可以同时存在多个 `queued`、`running` 或 `waiting_*` Run。Planning 子 Run 是否可启动只由 Planning Graph State 中的 `strategy` 和权威依赖字段 `depends_on_step_ids` 决定，不设置 Session 级全局串行约束。
- 同一个 Run 仍只能由一个持有有效 thread lock 的 Worker 推进；这是单 Run 并发保护，不限制不同 Run 按计划并发。
- 前台 Run 一旦公开 ACK，必须在 `completed`、`waiting_user`、`failed` 或 `cancelled` 时交付对应的用户可见消息；原 Session 不可用时只记录交付失败，不路由到 replacement primary。
- ACK、waiting prompt 和 terminal message 分别使用 `run:{run_id}:ack`、`run:{run_id}:waiting:{interrupt_id}`、`run:{run_id}:terminal:{lifecycle_status}` 幂等；ChatMessage、产品 delivery event 和可选 `channel_deliveries` 在同一事务写入。无外部 outbox 时该事务直接把 `delivery_status` 设为 `delivered`；有 outbox 时先设为 `pending`，由 Delivery Worker 最终更新。执行生命周期事件仍由 Projector 从 checkpoint 派生。

Session soft delete 时，Run Service 为绑定该 Session 的 `foreground`、`orchestration` 及其前台派生 `delegated` Run 写入幂等 cancel Command。尚未建立 checkpoint 的 start Command 被拒绝；已有 Thread 由 Graph 在安全节点把 `lifecycle_status` 转为 `cancelled`。已经进入 `started` 的工具先记录真实结果，随后禁止推进新节点或新工具。

具有独立业务来源的 `background` Run 不因 Session 删除取消，也不再读取该 Session 的最新 Context。交付前确认原 Session 不可用时，Delivery Service 按 direct `(tenant_id, agent_id, user_id)` 或 group `group_id` 查找当前 primary；无 primary 时交付失败。若原目标写入结果为 unknown，先使用 terminal delivery 幂等键对账或重试原目标，不能立即切换 primary。delivery event 必须记录请求目标、实际目标和 fallback 原因。

### 5.2 `agent_run_commands`

`agent_run_commands` 是 Runtime 的可靠输入日志，只接受 `start`、`resume` 和 `cancel`：

```text
id, tenant_id, run_id, command_type,
payload, actor_user_id, actor_agent_id,
idempotency_key,
status: pending / claimed / applied / rejected,
claimed_by, claim_expires_at,
attempt_count, applied_checkpoint_id, error_code,
created_at, applied_at
```

规则：

1. ChatMessage / TriggerExecution / A2A callback 与对应 Command 在同一 Clawith PostgreSQL 事务落库。
2. `unique(run_id, idempotency_key)` 防止重复输入；同一 `resume` 不得推进 Graph 两次。
3. Worker 先按 Run Registry 校验租户和权限，再使用同一 `thread_id` 调用 Graph。
4. `applied` 只有在调用返回可观察到的新 checkpoint 后才能写入；Worker 在返回前崩溃时，通过 checkpoint 与 Command ID 对账，不能直接重复 resume。
5. Graph State 保存 `last_applied_command_ids` 的有界集合或最近 watermark，使重复 Command 可以被确定性忽略。
6. Command 表不保存当前执行节点、下一节点、waiting 状态或最终结果。

### 5.3 `agent_run_events`

只记录产品稳定事件：

```text
run_created, status_changed, waiting_started, resumed,
evidence_added, verification_updated,
run_completed, run_failed, run_cancelled,
delivery_succeeded, delivery_failed,
channel_delivery_delivered, channel_delivery_failed
```

`delivery_succeeded / delivery_failed` 描述 Clawith 产品交付事务；`channel_delivery_delivered / channel_delivery_failed` 描述外部 Provider 的最终结果。后两类事件不带 checkpoint ID，也不能被 Projector 反向解释为执行状态。

字段至少包含 `run_id`、`event_type`、`summary`、小型 `payload`、`artifact_refs`、`idempotency_key` 和 `created_at`。

该表不复制每个 Graph node update、完整模型输入输出、checkpoint state 或大工具结果。

每条由 checkpoint 派生的事件必须保存 `source_checkpoint_id`，并以 `(run_id, source_checkpoint_id, event_type)` 幂等。删除全部派生事件后重新运行 Projector，必须能得到同样的产品事件序列。

### 5.4 `session_context_states`

每个未删除 session 只有一条当前 Session Context：

```text
session_id (unique), tenant_id, agent_id?,
summary, requirements, decisions, open_items,
evidence_refs, workspace_refs,
covered_through_message_id, version,
created_at, updated_at
```

它用于多渠道 Runtime Context 和产品查询，不保存 resume cursor。direct / A2A session 的 `agent_id` 指向当前 Agent；共享 group session 的 `agent_id` 为空。更新使用 `expected_version + expected_covered_through_message_id` 做 compare-and-swap；任意 Run 不得直接覆盖整条记录。Session 与 Run 的关联通过 `agent_runs.session_id` 查询，`active_run_ids` 不作为模型上下文注入依据。session soft delete 后记录可保留，但必须从正常 Context 查询中排除。

ChatMessage 不新增 `message_seq`。消息列表、Cursor、最近窗口、未读边界和 Session Compact watermark 统一使用 `(created_at, id)` 作为 Message Position。`covered_through_message_id` 需要解析到 watermark 消息的 Message Position 后再读取增量；不得比较 UUID 大小。watermark 消息缺失或不属于当前 session 时进入 Session Context 重建，不猜测覆盖范围。

### 5.5 `agent_tool_executions`

恢复可能重新进入工具节点。为避免外部副作用重复，新增窄表作为幂等账本：

```text
id, tenant_id, run_id, tool_call_id, tool_name,
assistant_message_id, arguments_hash,
sanitized_arguments, request_ref,
status: started / succeeded / failed / unknown,
result_summary, result_ref,
lease_owner, lease_expires_at,
started_at, completed_at, updated_at
```

唯一约束：`unique(run_id, tool_call_id)`。

执行规则：

1. 执行工具前原子创建 `started`。
2. 已有 `succeeded` 时复用结果引用，不重复执行。
3. 已有未过期 `started` 时不并发执行。
4. 副作用工具超时且结果未知时标记 `unknown`，禁止自动重试。
5. 只读工具可按策略安全重试，但仍记录结果。
6. parallel calls 使用同一个 `assistant_message_id` 组成 Tool Exchange group。
7. 重建时优先使用真实 `tool_call_id`、脱敏参数和 `result_ref`；旧记录缺失真实 ID 时才生成稳定兼容 ID。

### 5.6 字段类型、约束与索引

#### `agent_runs`

| 字段 | PostgreSQL 类型 | 约束 |
|-|-|-|
| `id` | `uuid` | PK |
| `tenant_id` | `uuid` | NOT NULL、FK、index |
| `agent_id` | `uuid` | nullable、FK、index；仅 orchestration Run 允许为空 |
| `session_id` | `uuid` | nullable、FK |
| `source_type` | `varchar(32)` | NOT NULL、check constraint |
| `source_id` | `varchar(200)` | nullable |
| `source_execution_id` | `varchar(200)` | nullable |
| `correlation_id` | `varchar(200)` | nullable；跨 Run 等待与回调的稳定关联 ID |
| `origin_user_id` | `uuid` | nullable、FK `users.id` |
| `origin_agent_id` | `uuid` | nullable、FK `agents.id` |
| `parent_run_id` / `root_run_id` | `uuid` | nullable、self FK |
| `run_kind` | `varchar(24)` | `foreground/background/delegated/orchestration` |
| `system_role` | `varchar(32)` | nullable；v1 orchestration 使用 `group_planning` |
| `model_id` | `uuid` | nullable、FK `llm_models.id`；Run 创建时固化 |
| `runtime_type` | `varchar(24)` | `legacy/langgraph` |
| `runtime_thread_id` | `varchar(255)` | NOT NULL、unique |
| `graph_name` | `varchar(100)` | NOT NULL；创建时固化 |
| `graph_version` | `varchar(64)` | NOT NULL；创建时固化 |
| `scheduling_lane_key` | `varchar(255)` | nullable；仅用于需要业务串行的调度通道，不表示 Run 生命周期 |
| `scheduling_position_created_at` | `timestamptz` | nullable；群 mention 使用触发消息的 `created_at` |
| `scheduling_position_id` | `uuid` | nullable；群 mention 使用触发消息 ID，与时间共同构成 Message Position |
| `lane_held` | `boolean` | NOT NULL default false；表示该 Run 当前占有业务调度通道 |
| `lane_claimed_at` | `timestamptz` | nullable；只用于协调和排障 |
| `projected_execution_status` | `varchar(32)` | nullable、check constraint；只读投影 |
| `projected_waiting_type` | `varchar(24)` | nullable；只读投影 |
| `projected_checkpoint_id` | `varchar(255)` | nullable；Projector watermark |
| `projected_error_code` | `varchar(100)` | nullable；只读投影 |
| `projection_updated_at` | `timestamptz` | nullable |
| `projected_started_at` / `projected_completed_at` | `timestamptz` | nullable；只读投影 |
| `delivery_status` | `varchar(24)` | NOT NULL、check constraint |
| `goal` | `text` | NOT NULL |
| `projected_waiting_reason` | `text` | nullable；只读投影 |
| `projected_result_summary` / `projected_last_error` | `text` | nullable；只读投影 |
| `delivery_target` | `jsonb` | nullable、小型结构 |
| `created_at` / `updated_at` | `timestamptz` | NOT NULL |

建议索引：

```text
index (tenant_id, agent_id, projected_execution_status)
index (session_id, created_at desc)
index (parent_run_id)
index (root_run_id)
index (source_type, source_id)
unique (runtime_thread_id)
unique (source_type, source_execution_id) where source_execution_id is not null
unique (scheduling_lane_key) where scheduling_lane_key is not null and lane_held
index (scheduling_lane_key, scheduling_position_created_at, scheduling_position_id, created_at, id)
  where scheduling_lane_key is not null
```

Planning Run 约束：

```sql
CHECK (
  (run_kind = 'orchestration' AND agent_id IS NULL AND system_role IS NOT NULL AND model_id IS NOT NULL)
  OR
  (run_kind <> 'orchestration' AND agent_id IS NOT NULL AND system_role IS NULL)
)
```

所有 `runtime_type = 'langgraph'` 的 Run 必须固化 `model_id`。调度字段必须成组出现：`scheduling_lane_key` 为空时两个 position 字段都为空且 `lane_held = false`；lane key 非空时两个 position 字段都非空。`agent_run_commands.attempt_count >= 0`，`session_context_states.version >= 1`。

v1 的 orchestration Run 只允许 `system_role = 'group_planning'`。它不对应 `participants` 或 `group_members`。Planning 模型配置变化只影响新建 Run；已有 Planning Run 的恢复、重试和结构修复必须继续使用该 Run 固化的 `model_id`。

不建立 Session 级前台 Run 唯一索引。`UNIQUE (session_id)` 会阻止同一群聊 Session 中的多个 Agent 按 `parallel` 或 `dependency` 策略并发，也会导致第二个 Run 无法登记。

Run 间调度直接复用 Planning Agent 已确认的统一策略：

1. `parallel`：所有 `depends_on_step_ids` 为空的步骤均可立即创建或推进。
2. `sequential`：每一步依赖前一步，只在前置步骤完成后推进下一步。
3. `dependency`：按 `depends_on_step_ids` 表达的 DAG，仅推进依赖已经完成的 ready steps。
4. 多个待启动 Run 可以在同一 Session 中共存；Planning Graph 根据上述依赖关系写入 ready 子 Run 的 start Command。
5. 同一 Agent 已经创建的多条群 mention 业务 Run 的串行消费属于调度器规则，按 Message Position 排序，不通过 Session 唯一索引实现。

群 mention 的业务串行使用 `agent_runs` 上的窄调度字段，不新增第二套执行状态机：

1. 每个群 mention 业务 Run 写入 `scheduling_lane_key = group_mention:{tenant_id}:{agent_id}`，并固化触发消息的 `(created_at, id)`；Direct、Task、Trigger、Heartbeat、A2A 和 Planning Run 的该字段为空。
2. Scheduler 只允许同一 lane 中 Message Position 最小的未终态 Run 竞争通道，并通过 partial unique index 原子把自身 `lane_held` 置为 true。
3. 是否终态必须读取对应 LangGraph checkpoint，不得读取 `projected_execution_status`；投影落后不能导致越序。
4. Run 在 `waiting_user`、`waiting_external` 或 `waiting_agent` 时仍占有 lane；到 `completed / failed / cancelled` 后释放。这样“上一条处理完成后再处理下一条”的产品规则在服务重启后仍成立。
5. 进程崩溃不会直接释放业务 lane；Reconciliation 读取 checkpoint，发现持有者已终态后幂等释放。checkpoint 仍 active 时禁止根据超时猜测完成。
6. `lane_held` 只决定一个业务 Run 何时可以开始或恢复，不决定 Graph 内节点、waiting、取消或最终结果；Projector 不写这些协调字段。

Planning Run 不进入目标 Agent lane。多 Agent 消息只有在 Planning 完成并创建具体业务 Run 后才参与上述排序；v1 接受 Planning 期间后到的单 Agent Run 先进入 lane，只保证已经创建的同 lane 业务 Run 串行且不并发。

数据库唯一约束只负责 Run 来源幂等与重复创建防护，例如 `runtime_thread_id`、`(source_type, source_execution_id)`。Command claim 只防止同一输入被多个 Worker 同时消费；它不承担任务间并发策略。

#### 其他五张表

```text
agent_run_events:
  index (run_id, created_at)
  index (tenant_id, event_type, created_at)
  unique (run_id, idempotency_key)

agent_run_commands:
  unique (run_id, idempotency_key)
  index (status, claim_expires_at, created_at)
  index (run_id, created_at, id)

session_context_states:
  unique (session_id)
  index (tenant_id, agent_id, updated_at)
  version integer NOT NULL

agent_tool_executions:
  unique (run_id, tool_call_id)
  index (tenant_id, status, started_at)
  index (status, lease_expires_at)

channel_deliveries:
  unique (run_id, idempotency_key)
  unique (message_id)
  index (status, next_attempt_at, claim_expires_at, created_at)
  index (run_id, created_at, id)
```

状态字段优先使用 `varchar + check constraint`，避免每次增加状态都修改 PostgreSQL native enum。JSONB 只用于小型、非核心过滤字段；租户、归属、状态、来源和关联 ID 必须使用独立列。

### 5.7 `channel_deliveries`

该表是外部渠道专用 Transactional Outbox，不是 Graph checkpoint、Command Inbox 或 Run 投影：

```text
id, tenant_id, run_id, agent_id, session_id, message_id,
channel, target, idempotency_key,
status: pending / claimed / delivered / failed,
attempt_count, next_attempt_at,
claimed_by, claim_expires_at,
provider_message_id, last_error_code, last_error,
created_at, updated_at, delivered_at
```

1. `DeliveryService` 在保存 assistant/system ChatMessage 的同一事务中创建稳定 outbox ID；重复 checkpoint side effect 先命中已有产品 delivery receipt，不重复创建消息或 outbox。
2. Delivery Worker 使用 `FOR UPDATE SKIP LOCKED` 领取到期的 `pending` 或 claim 已过期记录，提交 claim 后才调用 Provider，避免持有数据库事务跨网络等待。
3. 发送成功后写 `delivered`、Provider message ID、`channel_delivery_delivered` 和最新 Run 的 `delivery_status = delivered`；确定失败按指数退避，达到上限后写 `failed`、脱敏错误和 `channel_delivery_failed`。
4. route 只保存版本化的小型目的地；Provider credential 每次从当前 `ChannelConfig` 读取，不复制进 outbox。动态 session webhook 或 interaction token 无法从配置重建时允许保存在 `target`，但按敏感字段处理，不写日志或 event payload。
5. Delivery Worker 只读取已持久化 ChatMessage，不调用 Graph、Checkpointer、Runtime Adapter 或工具。Run 已 `completed` 时投递失败也只重试该 outbox。
6. Provider 不支持幂等键时只能保证 at-least-once：如果远端已接收、进程却在本地确认前退出，重试可能重复。支持稳定消息 ID 的 Provider 使用 `channel_deliveries.id`；v1 不为分段发送再引入一套可推进状态机。

## 6. LangGraph State 与 Context

Checkpoint 中只保存可序列化、恢复执行所需的动态状态：

```text
run_id,
lifecycle_status, lifecycle_reason,
last_applied_command_ids,
session_context_snapshot, session_context_version,
recent_session_messages_snapshot,
run_messages, run_summary, covered_through_run_message_id,
related_run_summaries,
pending_tool_calls, waiting_request,
verification_result, final_answer, error
```

`lifecycle_status` 是 Run 执行生命周期的唯一事实来源。Graph 路由、interrupt/resume、取消和 terminal 判定只读取当前 `StateSnapshot.values`、`StateSnapshot.next`、interrupts 与 tasks，不读取 `agent_runs.projected_*`。`last_applied_command_ids` 只保留足以覆盖 Command 重投与对账窗口的有界集合；更早记录由 `agent_run_commands` 的唯一键保留。

`session_context_snapshot` 是 Run 创建时读取的版本化会话背景，`recent_session_messages_snapshot` 是同一时刻固定的最近用户可见消息窗口，Web Chat 默认最多 20 条。两者都不是第二个 Session Context 事实源。已有 Run 从 checkpoint 恢复时默认继续使用这组快照，并把本次明确的 resume payload 作为新输入；不得因为其他并行 Run 更新了 Session Context，就静默替换当前 Run 的执行背景。新 Run 始终读取最新 Session Context 版本和最近消息窗口。

`run_messages` 和大型工具结果是唯一需要在 Thread 内做语义压缩的增长字段；压缩结果写入 `run_summary`。Graph node、pending writes、精确工具参数、interrupt / resume 数据和工具幂等状态不做摘要化。

SQLAlchemy session、LLM client、API key、access token、大文件正文和超大工具结果不进入 State。单次调用所需的 ID、权限上下文、工具引用和 stream writer 通过 runtime context 传入；Node 内按 ID 建立短生命周期数据库会话。

模型输入顺序：

1. Agent static context：身份、角色、工具规则、`soul.md`。
2. Session Context Snapshot：摘要、requirements、决策、未完成事项和引用。
3. Current Run：目标、Run Summary、pending / waiting / verification 状态。
4. Related Run Summaries：仅包含明确 parent / child / dependency Run 的结果摘要和产物引用。
5. Recent Session Messages Snapshot：Web Chat 在新 Run 创建时默认固定最近 20 条用户可见消息，优先保留最近用户原话。
6. Recent Run Messages：按剩余 token budget 选择。
7. 当前入口输入。

继续保留 Tool Pair Integrity。已完成但超出预算的 Tool Exchange 不单独裁剪 result，而是整组移出活动消息，并在 `run_summary` 保留执行摘要与 `result_ref`。

已完成、失败、取消、长期未使用或与当前目标无关的 Run，即使仍保存在业务表或 checkpoint 中，也不进入模型上下文。完整 Session 历史、整个 Thread 和历史 Checkpoint 同样不进入模型上下文。

## 7. Graph 设计

```text
START
  ↓
prepare_context
  ↓
compact_run_if_needed
  ↓
call_model
  ├─ tool_calls → execute_tools → route_after_tools ─┐
  ├─ wait       → wait_for_external (interrupt)      │
  ├─ finish     → verify → finalize → deliver_result → END
  └─ error      → handle_error → END                 │
                                                    │
                    └───────────────────────────────┘
```

| Node | 责任 |
|-|-|
| `prepare_context` | 新 Run 初始化时读取权限、版本化 Session Context、最近 20 条用户可见消息、当前 Run 和相关 Run 摘要；resume 时复用 checkpoint 快照并追加明确输入 |
| `compact_run_if_needed` | 计算 token budget，必要时滚动压缩 Run 内模型/工具历史，不写 Session Context |
| `call_model` | 复用现有 LLM client、模型配置、failover 和 token tracking |
| `execute_tools` | 复用工具定义和 `execute_tool()`，通过幂等账本执行 |
| `route_after_tools` | 决定继续模型、等待或验证 |
| `wait_for_external` | 只产生 JSON interrupt，不在 interrupt 前执行非幂等副作用 |
| `verify` | 根据真实结果判断是否完成 |
| `finalize` | 只生成 terminal Graph State、结果摘要、SessionContextDelta 和 delivery request |
| `deliver_result` | 通过幂等交付服务写用户可见结果；重复进入不重复发送 |
| `handle_error` | 把错误写入 Graph State 并决定失败或等待 |

现有 `finish(content=...)` 继续作为模型声明完成的协议，但不直接把 run 标记为 `completed`。它只路由到 `verify`，验证通过后才完成 run。

### 7.1 State 与 Context 类型

建议使用显式 TypedDict / dataclass，不在 Graph 中传播任意字典：

```python
from dataclasses import dataclass
from typing import Annotated, Literal, TypedDict

from langgraph.graph.message import add_messages


class RuntimeState(TypedDict, total=False):
    run_id: str
    lifecycle_status: Literal[
        "created", "queued", "running", "waiting_user",
        "waiting_external", "waiting_agent", "verifying",
        "completed", "failed", "cancelled",
    ]
    lifecycle_reason: str | None
    last_applied_command_ids: list[str]
    session_context_snapshot: dict
    session_context_version: int
    recent_session_messages_snapshot: list[dict]
    run_messages: Annotated[list[dict], add_messages]
    run_summary: dict | None
    covered_through_run_message_id: str | None
    related_run_summaries: list[dict]
    pending_tool_calls: list[dict]
    waiting_request: dict | None
    verification_result: dict | None
    final_answer: str | None
    error: dict | None


@dataclass(frozen=True)
class RuntimeContext:
    tenant_id: str
    run_id: str
    command_id: str
    agent_id: str | None
    model_id: str
    run_kind: str
    system_role: str | None
    user_id: str | None
    session_id: str | None
    source_type: str
    # 传 factory/service，不传已打开的 DB transaction
    db_session_factory: object
    llm_service: object
    tool_service: object
```

Graph 初始化一次并复用：

```python
builder = StateGraph(RuntimeState, context_schema=RuntimeContext)
builder.add_node("prepare_context", prepare_context)
builder.add_node("compact_run_if_needed", compact_run_if_needed)
builder.add_node("call_model", call_model)
builder.add_node("execute_tools", execute_tools)
builder.add_node("wait_for_external", wait_for_external)
builder.add_node("verify", verify)
builder.add_node("finalize", finalize)
builder.add_node("handle_error", handle_error)

graph = builder.compile(checkpointer=async_postgres_saver)
```

Graph 不在请求中重复 compile。Checkpointer 和 Graph 由 FastAPI lifespan 创建，在进程关闭时释放连接池。

### 7.2 `call_model` 节点

`call_model` 复用当前 provider client，但只完成一次模型调用，不在节点内部再运行多轮循环：

```python
async def call_model(state: RuntimeState, runtime: Runtime[RuntimeContext]):
    model_input = await context_builder.build(state, runtime.context)
    response = await llm_service.complete_once(
        agent_id=runtime.context.agent_id,
        messages=model_input.messages,
        tools=model_input.tools,
        stream_writer=runtime.stream_writer,
    )
    return {
        "run_messages": [response.assistant_message],
        "pending_tool_calls": response.tool_calls,
    }
```

路由规则：

```text
有合法 finish call             → verify
有普通 tool calls              → execute_tools
无 tool call 但有普通文本       → 追加 finish reminder，再次 call_model
模型可重试错误且尚无副作用      → 同 checkpoint 内 failover
模型不可重试错误               → handle_error
达到最大模型步骤               → handle_error
```

原有 `_sanitize_tool_calls_for_context()`、finish protocol、token tracking 和 provider failover 逻辑应拆成可复用 service，避免复制进 node。

### 7.3 `execute_tools` 节点

同一模型响应中存在多个 tool calls 时，v1 默认顺序执行。只有工具元数据明确标记 `parallel_safe=true` 且彼此无依赖时才允许并行。

```python
async def execute_tools(state: RuntimeState, runtime: Runtime[RuntimeContext]):
    tool_messages = []
    for call in state["pending_tool_calls"]:
        reservation = await tool_execution.reserve(
            run_id=state["run_id"],
            tool_call_id=call["id"],
            tool_name=call["name"],
            arguments=call["arguments"],
        )

        if reservation.reusable_result:
            result = reservation.reusable_result
        elif reservation.blocked:
            return {"error": reservation.error}
        else:
            try:
                result = await tool_service.execute(call, runtime.context)
                await tool_execution.mark_succeeded(reservation.id, result)
            except OutcomeUnknownError as exc:
                await tool_execution.mark_unknown(reservation.id, exc)
                return {"error": {"code": "tool_outcome_unknown"}}
            except Exception as exc:
                await tool_execution.mark_failed(reservation.id, exc)
                result = tool_error_message(exc)

        tool_messages.append(to_tool_message(call, result))

    return {"run_messages": tool_messages, "pending_tool_calls": []}
```

第一批必须标记为副作用工具的类别：

- 发送消息、邮件、短信和通知。
- 创建、修改、移动、删除文档或文件。
- 创建、修改、删除任务、日程、审批和业务记录。
- A2A `notify` / `task_delegate`。
- 任何对外部 SaaS、浏览器或代码环境产生写操作的工具。

工具注册元数据至少增加：

```text
effect: read | write | external_write
retry_policy: safe | conditional | never
parallel_safe: bool
timeout_seconds: int
```

### 7.4 `verify` 与 `finalize`

`verify` v1 不再调用一个独立“评审模型”自动兜底所有任务。验证来源按优先级为：

1. 工具返回的结构化成功/失败状态。
2. 任务类型定义的 deterministic verifier。
3. 用户或上游入口提供的验收条件。
4. 没有专用 verifier 时，只检查 finish 内容、未处理 tool error 和 pending step。

`finalize` 不写 `agent_runs.projected_*`，只返回 Graph State update：

1. `lifecycle_status = completed`。
2. `final_answer` 与结构化 `result_summary`。
3. `SessionContextDelta`。
4. 带稳定幂等键的 `delivery_request`。

该 update 由 Checkpointer 保存后，`deliver_result` 才调用交付服务。ChatMessage、产品 delivery event 和可选 `channel_deliveries` 在一个 Clawith 短事务写入；重复执行使用 `run:{run_id}:terminal:{lifecycle_status}` 命中同一结果。外部 Provider 发送在事务提交后由独立 Delivery Worker 完成，不阻塞或恢复 Graph。Projector 从 terminal checkpoint 派生 `projected_execution_status`、`projected_result_summary` 和 `run_completed`。`SessionContextService` 同样按 `source_run_id + terminal_checkpoint_id` 幂等合并 delta；合并、投影或外部发送失败都不回滚已经完成的 Graph。

### 7.5 模型执行契约与能力降级

Runtime 的复杂性由确定性代码承担，不转嫁给业务模型。每次模型调用只看到完成当前局部步骤所需的信息：当前目标、裁剪后的 Context、可用工具定义、完整 Tool Exchange 结果和必要的等待输入。模型不接触 Command、checkpoint、Projector、调度 lane、锁、租约或重试计数。

业务模型只允许输出三类结构化意图：

```text
tool_calls  调用一个或多个已声明工具
wait        明确等待用户、Agent 或外部系统
finish      提交候选最终结果，随后由 verify 节点确定是否完成
```

确定性 Runtime 负责生命周期转换、依赖就绪判断、调度、参数校验、工具幂等、重试边界、取消、恢复、交付和审计。Planning 模型只输出群聊技术方案定义的计划 Schema；Compact 模型只输出摘要 Schema，二者都不推进业务工具。

模型能力按以下规则 gate：

1. 不支持可靠 tool calling 的模型不进入 LangGraph 工具 Runtime；只允许经过验证的纯 Chat Graph，不能回退 legacy 路径。
2. 支持 tool calling 但结构化输出稳定性不足的模型，只允许简单 Single Run；`wait`、Planning 和复杂依赖必须通过 Capability 校验后开放。
3. 多 Agent Planning 必须使用显式配置且通过结构化输出验证的 Planning 模型；配置或验证失败时显式失败，不静默改用成员模型。
4. Compact 使用第 9 章的独立规则；能力或窗口未知时 fail closed，不让模型猜测裁剪。
5. Provider 输出必须先经过 Runtime Schema 校验；非法输出可以在无副作用前提下做有界修复，超过上限进入可见失败或等待，不循环提示模型自行修复。

## 8. Interrupt 与 Resume

Interrupt payload 使用稳定结构：

```json
{
  "run_id": "...",
  "waiting_type": "user|agent|external",
  "reason": "...",
  "expected_input_schema": {},
  "correlation_id": "..."
}
```

Interrupt 本身及 `waiting_type` 保存在 checkpoint。Projector 将其异步映射成 `projected_waiting_type` 和 `waiting_started`；等待恢复不能依赖该投影。新消息、A2A callback、Trigger 或 webhook 必须持久化 `resume` Command，由 Worker 使用同一 `thread_id` 和 `Command(resume=...)` 推进。

恢复必须使用同一 `thread_id`。LangGraph 恢复时会从 interrupt 所在 node 开头重新执行，因此该 node 在 interrupt 前不得执行不可重复副作用。

### 8.1 Web Chat 时序

```text
1. WebSocket 收到用户消息并保存 ChatMessage。
2. Ingress 校验用户、Agent、tenant 和 session。
3. 若消息携带 waiting_user run_id，在同一事务写 resume Command。
4. 否则在同一事务创建 foreground Run Registry 和 start Command。
5. Command Worker 领取 Command，锁定 thread 后调用 graph.astream / stream_events。
6. RuntimeEventMapper 转换 thinking、tool、workspace 和 answer 事件。
7. WebSocket 继续使用当前前端事件协议发送。
8. Graph 完成后保存 assistant ChatMessage。
9. abort 请求调用 cancel_run，而不是只取消进程内 asyncio task。
```

WebSocket 断开不等于取消 run。后台执行可以继续，重连后通过 `get_run_state()` 和消息记录恢复 UI；只有用户显式 abort 才进入 cancelled。

### 8.2 Task / Trigger / Heartbeat 时序

Task：

```text
Task execution record → start_run(source_type=task)
→ Graph 执行 → completed/failed
→ 更新 Task 状态和 TaskLog 投影
```

`Task.status` 继续表达任务看板状态；一次实际执行的权威状态来自 LangGraph，`agent_runs.projected_execution_status` 只用于列表查询。周期性 supervision Task 每次执行都创建新 Run，不复用旧 checkpoint。

Trigger：

```text
TriggerExecution claimed
→ start_run(source_execution_id=TriggerExecution.id)
→ 重复投递命中同一 run
→ Graph 完成/等待
→ TriggerExecution 投影为 completed/failed/processing
```

Heartbeat 是一种 `source_type=heartbeat` 的后台 run，不再保留自己的 tool loop。若一次 heartbeat 没有实际工作，可以快速完成并记录轻量结果。

### 8.3 A2A Resume 时序

```text
source execute_tools 调用 task_delegate
→ 原子创建 target run + correlation_id
→ source Graph 路由到 wait_for_external
→ source checkpoint interrupt
→ target worker 执行 target run
→ target finalize 保存结果引用
→ callback 调用 resume_run(source_run_id, target_result)
→ source 从同一 checkpoint 继续 verify / call_model
```

创建 target run 前执行统一 Agent 循环检查。只统计 `run_kind = delegated` 的祖先边 `(origin_agent_id, agent_id)`；人类入口和 Planning 初始步骤不计入。把候选 `(source_agent_id, target_agent_id)` 加入当前父链后，循环次数按 `sum(max(edge_count - 1, 0))` 计算。候选会使计数达到 `MAX_AGENT_CYCLE_COUNT` 时不创建 target run，`task_delegate` / Agent mention 返回 `agent_cycle_limit_reached`。v1 固定 `MAX_AGENT_CYCLE_COUNT = 5`，从数据库父链重算，不能使用服务进程内计数。

目标 Agent 失败不会直接把 source run 标记为 failed。source 恢复后根据 target error 决定重试委托、改用其他 Agent、向用户说明或结束任务。

### 8.4 恢复输入契约

```python
class ResumeCommand(TypedDict):
    resume_type: Literal["user_input", "agent_result", "external_event", "timer"]
    correlation_id: str
    payload: dict
    actor_user_id: str | None
    actor_agent_id: str | None
    idempotency_key: str
```

Adapter 在执行 `Command(resume=payload)` 前再次校验 actor 权限、waiting type 和 correlation ID。重复 resume 使用 `idempotency_key` 返回第一次结果，不重复推进 Graph。

### 8.5 协作式取消

`cancel` 不能只停掉进程内 asyncio task，也不能等待当前 Graph 自然跑到终点。Graph 在 `call_model`、`execute_tools` 等可能产生新工作的位置前后统一经过 control guard，读取该 Run 是否存在尚未应用的有效 cancel Command：

1. 模型调用尚未产生有效响应且 Provider 支持中止时，停止当前请求并把 Graph 路由到 `cancelled`。
2. 工具尚未进入 `started` 时禁止再启动；工具已经 `started` 时不得强杀未知副作用，先记录真实 `succeeded / failed / unknown` 结果，再在下一 control guard 进入 `cancelled`。
3. 模型 stream 期间按固定间隔轮询 control Command；非流式调用最迟在调用返回后的 guard 生效。
4. Active Worker 持有 thread advisory lock 时，由它消费 cancel；其他 Worker 不并发推进同一 Thread。Worker 崩溃后数据库连接释放锁，新 Worker 从同一 checkpoint 领取并应用 cancel。
5. cancel Command 只有在 checkpoint 已包含对应 Command ID 且生命周期已转为 `cancelled` 后才标记 `applied`；重复 cancel 只对账，不重复推进。
6. terminal Run 收到 cancel 时 Command 记为 `rejected`，返回实际 terminal 状态，不改写历史。

## 9. Session Compact 与 Run Compact

系统只有两套语义压缩：

| 压缩 | 粒度 | 唯一事实来源 / 写入位置 | 处理内容 |
|-|-|-|-|
| Session Compact | `ChatSession` | `session_context_states` | 长期会话摘要、用户要求、已确认决策、未完成事项和引用 |
| Run Compact | `AgentRun` | 当前 Thread 最新 Graph State 的 `run_summary` | 本次执行的模型消息、工具结果、中间进展和验证历史 |

LangGraph Thread 不建立第三套摘要。它只是 `run_id` 对应的 checkpoint 容器；checkpoint 历史通过 retention / pruning 管理存储，而不是作为 prompt 内容压缩。

### 9.1 Model Capability 与 Token 预算

Runtime 不直接使用含义不统一的 Provider “context window”，而是通过 `ModelCapabilityResolver` 规范化为模型本次请求允许的最大输入量：

```text
context_window_tokens
max_input_tokens
max_output_tokens
capability_source
capability_checked_at
```

`LLMModel` 建议新增：

```text
context_window_tokens
context_window_tokens_override
max_input_tokens
max_input_tokens_override
max_output_tokens
capability_source: manual / provider_api / builtin_registry / runtime_config
capability_checked_at
```

解析优先级：

1. 管理员按限制语义显式配置 override：`max_input_tokens_override` 只覆盖独立输入上限，`context_window_tokens_override` 只覆盖输入输出共享总窗口；两种限制同时存在时仍共同参与取最小值，不能用一种 override 抹掉另一种有效硬限制。
2. Provider 官方能力接口。
3. Clawith 内置 Model Capability Registry。
4. 部署运行时配置，例如 Ollama `num_ctx`。
5. 仍未知时不允许该模型启用新 Runtime，要求管理员补充最大输入限制，不猜测大窗口。

Provider 适配：

| Provider | 获取方式 |
|-|-|
| Anthropic | 按官方模型元数据实际声明的输入/共享窗口语义解析；缺失时使用 Registry 或管理员配置 |
| Gemini | `models.get` 的 `inputTokenLimit` / `outputTokenLimit` 按独立输入/输出限制保存 |
| Ollama | `/api/ps` 当前 `context_length` 与 `/api/show` 模型 `context_length`、部署 `num_ctx` 取实际可用最小共享窗口 |
| OpenAI | Models API 未提供可靠窗口字段时，使用带语义标记的内置 Registry 或管理员配置 |
| 其他 OpenAI-compatible | Provider 专用发现；不支持时使用 Registry 或管理员配置 |

能力在模型创建、`provider / model / base_url` 修改和定期刷新时解析并缓存，模型调用热路径只读取缓存。人工 override 不被后台刷新覆盖；context length error 只触发能力失配告警和刷新，不作为主要发现机制。

Resolver 必须记录限制的真实语义，不能把共享总窗口提前扣除固定输出额度后伪装成 `max_input_tokens`。如果 Provider 返回独立输入上限，原样写入 `max_input_tokens`；如果返回输入输出共享总窗口，写入 `context_window_tokens`。语义不明确时按共享总窗口保守记录。每次模型调用统一按本次实际输出额度计算：

```text
effective_max_input_tokens =
  max_input_tokens_override ?? max_input_tokens

effective_context_window_tokens =
  context_window_tokens_override ?? context_window_tokens

requested_max_output_tokens = min(
  request.max_output_tokens,
  model.max_output_tokens
)

request_input_limit = min_defined(
  effective_max_input_tokens,
  effective_context_window_tokens - requested_max_output_tokens
)

effective_runtime_budget =
  request_input_limit
  - static_prompt_tokens
  - tool_schema_tokens
  - reserved_runtime_tokens
  - safety_margin_tokens

compact_threshold =
  effective_runtime_budget * 0.85
```

`min_defined` 只比较存在的能力项；两项都未知时该模型不得启用新 Runtime。Provider 明确给出的独立输入上限不再减输出额度；只有共享 `context_window_tokens` 扣除本次 `requested_max_output_tokens`，避免重复预留输出。

预计输入达到阈值时强制执行 Run Compact；消息数、工具结果长度、进入 `waiting_*`、resume 后上下文过大以及 verify / repair 循环可以提前触发。压缩后仍保留最近消息窗口和 Tool Exchange 原子性。

单 Agent 正常执行只使用 primary model 的预算，不预先取 fallback 最小值。发生可 failover 错误后，必须以 fallback model 的能力和该次调用输出额度重新计算 `request_input_limit`、重新运行 Context Builder，并在需要时分批执行 Run Compact。只有 primary 尚未产生有效响应且本轮工具副作用尚未进入 `started` 时允许普通 failover；否则进入 checkpoint 恢复和工具对账。Fallback 专用的小窗口投影不回写 `session_context_states`。

Compact 模型选择规则：

1. 单 Agent 不配置独立 Compact 模型。Run Compact 使用当前实际执行该 Run 的模型；正常路径是 primary model，安全 failover 后是当前 fallback model。
2. 单 Agent 的 Session Compact 使用该 Agent 当前配置模型。
3. 多 Agent 共享上下文压缩必须显式配置独立 `compact_model_id`，不得根据参与 Agent 顺序或运行时可用性临时挑选某个 Agent 的模型。
4. 多 Agent 的压缩触发阈值按所有参与模型中最小的 `effective_runtime_budget * 0.85` 计算；独立 Compact 模型只负责执行压缩，不改变共享上下文必须能被所有参与模型读取的预算边界。
5. Compact 模型窗口不足以一次处理待压缩内容时，按完整消息块和 Tool Exchange Block 分批压缩，不得破坏 Tool Pair Integrity。

### 9.2 Session Compact

Session Compact 由独立 `SessionContextService` 编排。输入为：

```text
旧 Session Context
+ Message Position 位于 covered_through_message_id 对应位置之后的 ChatMessage
+ 已完成 Run 提交的 SessionContextDelta
```

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

写入流程：

1. 读取当前 `session_context_states` 和 watermark 后的用户可见消息。
2. 校验待合并 `SessionContextDelta` 的 tenant、session、source run 和引用权限。
3. 生成候选 Session Context，并校验结构、引用和关键字面量。
4. 使用 `expected_version + expected_covered_through_message_id` 执行 compare-and-swap。
5. 版本冲突时重新读取最新版本并重新合并，不允许旧 Run 覆盖新 Session Context。
6. 生成失败时保留旧 Session Context，并使用旧摘要加最近 20 条用户可见消息作为兜底。
7. 原始 `ChatMessage` 不删除。

触发条件包括新增消息数量阈值、Session Context token 预算、用户显式总结请求，以及 Run 进入 `completed` / `failed` 后产生待合并 delta。

### 9.3 Run Compact

Run Compact 在 `compact_run_if_needed` 节点中运行。输入为旧 `run_summary`、watermark 后新增的 `run_messages`、工具结果摘要、验证反馈和 artifact refs；输出为：

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

成功后更新当前 Graph State，并在后续模型输入中使用 `run_summary + recent run_messages`。失败时保留旧 Run Summary 和合法的最近 tool pairs，不阻断当前 Run。Run Compact 不写 `session_context_states`，也不修改业务状态或 checkpoint cursor。

每条 `run_message` 必须有稳定 message ID。压缩成功后，节点通过 LangGraph message reducer 的删除语义从最新 State 的活动窗口移除已被 `run_summary` 覆盖的旧消息，保留最近窗口和完整 tool pairs；不得直接修改历史 checkpoint。默认最近消息目标为 20 条，如果窗口边界落在 tool pair 中间，可以少量超过 20 条以保留合法配对。

以下精确状态不得进入摘要后被删除：Graph node、pending writes、`pending_tool_calls` 的工具名/参数/ID、`waiting_request`、interrupt / resume payload、`verification_result` 当前值、工具幂等状态和 checkpoint metadata。

### 9.4 Run 完成后的 Session 合并

Run 完成后由 `finalize` 生成增量，不生成整份 Session Summary：

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

`SessionContextService` 合并成功后，该 terminal Run 默认不再进入其他 Run 的模型上下文。Run 是否仍保留在数据库或 checkpoint 中由 retention 策略决定，与 Context 选择无关。

### 9.5 Run 选择规则

Context Builder 只选择：

1. 当前 Run 的完整可注入状态。
2. 与当前 Run 存在明确 `parent_run_id`、child delegation、等待关联或显式 dependency 的 Run 结果摘要。

其他 active Run、terminal Run、长期未使用的 waiting Run 和无关并行 Run 一律不注入。需要让后续任务长期知道的信息必须先进入 Session Context 的 `decisions`、`open_items` 或引用。

### 9.6 Tool Exchange 原子裁剪

#### 原子块模型

Context Builder 不再直接执行 `messages[-N:]` 后删除孤儿消息，而是先把消息规范化为：

```text
NormalMessageBlock
ToolExchangeBlock
PendingToolExchangeBlock
MalformedToolExchangeBlock
```

一个 `ToolExchangeBlock` 包含原始 assistant 消息及其声明的全部 calls 和 results：

```text
assistant(tool_calls=[A, B])
+ tool_result(A)
+ tool_result(B)
```

parallel calls 必须作为同一个 Block 处理。不得通过删除 assistant 中的某一个 call 来制造表面合法的新历史。

#### 窗口选择算法

建议接口：

```python
blocks = build_message_blocks(messages, tool_execution_ledger)
selected = select_recent_blocks(
    blocks,
    target_messages=20,
    token_budget=remaining_budget,
)
validate_tool_exchange_integrity(selected)
```

`select_recent_blocks` 从后向前选择完整 Block：

1. 普通消息按单条加入。
2. 边界落在 Tool Exchange 中间时向前扩展，允许少量超过 20 条。
3. 完整且已结束的 Block 超出硬 token budget 时，call 和全部 results 整组移出活动消息，并向 `run_summary` 追加包含 call ID、工具名、副作用分类、执行状态、结果摘要和 `result_ref` 的结构化摘要。
4. 禁止只保留 call、只保留 result，或只保留 parallel group 的一部分。
5. 最终输出必须保持消息原始顺序，call ID 唯一且每个 call 恰好对应一个 result。

#### 不完整 Block 判定

| Block 状态 | Runtime 行为 |
|-|-|
| `complete` 且能放入预算 | 整组原样保留 |
| `complete` 但超出预算 | 整组移出活动消息并写入 Run Summary，继续调用模型，禁止重做工具 |
| `not_started` | 丢弃整组 assistant proposal并重新调用模型；允许模型生成新的 Tool Call，但不得执行已经丢弃的旧 proposal |
| `succeeded_but_message_missing` | 能重建则从 checkpoint / `agent_tool_executions` 重建完整 Block；无法成对重建时整组移出并写执行摘要，禁止重做工具 |
| `started` | 等待 lease 或 reconciliation，不调用模型继续规划 |
| `unknown` 且可能有副作用 | interrupt 并要求确认，禁止自动重试 |
| `orphan_result` | 使用真实 `tool_call_id`、`assistant_message_id` 和请求引用重建 call；无法成对重建时整组移出并保留结构化执行事实，禁止自动重试 |
| parallel group 部分缺失 | 整组暂不进入模型输入，先重建或对账 |

“重新调用模型”与“重新执行工具”必须分开。只有账本确认该 call 从未进入 `started` 时，才允许丢弃旧 proposal、重新调用模型并执行新 Tool Call。`succeeded` 但无法成对恢复的 Block 使用执行摘要续接；任何 `started` 或 `unknown` call 都不得通过裁剪路径触发工具重做。

#### Run Compact watermark

每条 `run_message` 必须有稳定 ID。Run Compact 计算 `covered_through_run_message_id` 时只能推进到最后一个完整且已经安全沉淀的 Block，不得跨越 `pending`、`started`、`unknown` 或 malformed Block。

完成压缩后，使用 LangGraph message reducer 的删除语义从最新 Graph State 移除已经被摘要覆盖的完整 Block；不直接修改历史 checkpoint。工具大结果可以替换为短结果和 `result_ref`，但配对 ID 和执行事实必须保留。

#### Provider Adapter 边界

Provider Adapter 保留格式校验作为最后防线，但不再静默改变正常 Runtime 历史：

1. 检测到孤立 call/result 时记录结构化 ERROR、run ID、thread ID 和 call IDs。
2. 拒绝本次模型调用并返回 Runtime 的重建 / reconciliation 分支。
3. 只有 legacy 路径在 feature flag 保护下允许降级删除孤立项，并必须记录 metric；新 LangGraph Runtime 默认 fail closed。
4. 历史 `tool_call` 记录转换时优先使用持久化的真实 `tool_call_id`，仅对旧数据使用 `call_{message_id}` 兼容值。

## 10. Streaming 与渠道适配

`RuntimeEventMapper` 把 Graph stream 转成 Clawith 稳定事件：

```text
run_started, thinking_delta, assistant_delta,
tool_call_started, tool_call_delta, tool_call_completed,
workspace_output, waiting, verification, completed, failed
```

WebSocket 再映射为现有前端兼容事件。飞书、企业微信、Slack 等非流式渠道在入站事务中只提交 Runtime Command 和版本化 delivery route；最终 ChatMessage 由 `DeliveryService` 生成，再通过 `channel_deliveries` Worker 发送。渠道入口不等待 Runtime stream，也不持有进程内 background task。

Runtime Event 是 Clawith 契约；LangGraph 的 stream mode 只存在于 Adapter 内部。

稳定流分成两层：`agent_run_events` 提供可重连的生命周期、waiting、verification、terminal 和 delivery 基线，cursor 必须使用 `(created_at, event_id)`，不能只用时间戳；`thinking_delta / assistant_delta / tool_call_delta / workspace_output` 是执行期瞬时事件，由 Worker 内的 `RuntimeEventMapper` 发布，不写入 `agent_run_events`。重连时先从稳定事件和 `ChatMessage` 恢复，再订阅瞬时流；前端不得把瞬时 delta 当作恢复事实。

## 11. A2A

`task_delegate`：

```text
source run
  → 创建 target run（parent_run_id = source run）
  → source run 进入 waiting_agent
  → target run 独立执行
  → target run completed
  → correlation_id 恢复 source run
  → source run 进入 running / verifying
```

source 与 target 使用不同 `thread_id`，不共享完整 checkpoint，只交换明确授权的 delegation input、result summary 和 artifact refs。

- `consult`：v1 可继续同步调用，但通过子 run 记录；后续统一成短生命周期 target run。
- `notify`：只产生消息交付，不创建需要等待的 target run。

## 12. 一致性与失败处理

本方案不尝试把 Clawith 表与 LangGraph checkpoint 做成两个同步状态源，也不要求跨两套写入协议的分布式事务。可靠性通过 Command Inbox、LangGraph 单写、幂等副作用、Delivery Outbox 和可重建投影实现：

1. 产品入口事务原子写业务事实与 `agent_run_commands`。
2. Command Worker 只向 LangGraph 提交输入；LangGraph Checkpointer 是执行状态唯一写入者。
3. Runtime Projector 只在 checkpoint 提交后更新 `agent_runs.projected_*` 和派生 `agent_run_events`。
4. 投影失败或延迟只影响 UI 新鲜度，不影响 Graph 正确恢复。
5. 所有外部副作用使用独立幂等 receipt；不能确定结果时进入 `unknown`，不自动重做。
6. 模型 failover 仅在本轮没有产生有效响应且工具副作用尚未 `started` 时允许；否则从同一 checkpoint 对账恢复。
7. cancel 是可靠 Command，不是直接改 `projected_execution_status`；已发生副作用不承诺自动回滚。
8. 外部 Provider 投递只消费 `channel_deliveries`；发送失败或 Worker 重启不重新 invoke Graph。

### 12.1 Command Claim 与 Thread 推进互斥

领取 Command 使用数据库短事务：

```sql
SELECT command.id
FROM agent_run_commands AS command
WHERE (
    command.status = 'pending'
    OR (command.status = 'claimed' AND command.claim_expires_at < now())
)
AND NOT EXISTS (
    SELECT 1
    FROM agent_run_commands AS previous
    WHERE previous.run_id = command.run_id
      AND previous.status IN ('pending', 'claimed')
      AND (previous.created_at, previous.id) < (command.created_at, command.id)
)
ORDER BY command.created_at, command.id
FOR UPDATE SKIP LOCKED
LIMIT 1;
```

领取后写入 `claimed_by` 与 `claim_expires_at`，并原子增加 `attempt_count`。同一 Run 的 Command 固定按 `(created_at, id)` 消费；若已有未完成调用，后续 Command 保持 pending。达到最大领取次数前必须先与 checkpoint 中的 Command ID 对账，已经应用的 Command 只补写 `applied`，不得重复 invoke。

Thread 推进互斥固定使用 PostgreSQL session-level advisory lock，不再保留“租约或 advisory lock”二选一：

1. Worker 使用 `run_id` 稳定生成 lock key，并在专用数据库连接上获取 advisory lock；从读取 checkpoint 到 Graph invoke/stream 返回、Command 对账完成期间保持同一连接。
2. 未获得锁的 Worker 不调用 Graph，Command 保持可重领；它不能读取 `projected_execution_status` 猜测 Run 是否空闲。
3. 连接或进程退出时 PostgreSQL 自动释放锁，新 Worker 再从 checkpoint 恢复；正常结束必须在 `finally` 中显式释放。
4. Advisory lock 只保护同一 Thread 不被并发推进，不限制不同 Run，也不承担群 mention 的业务串行；后者由 5.6 节的 scheduling lane 负责。
5. 已经发出的模型/工具调用必须按真实结果对账，不能因为连接丢失就假装未执行。

建议默认值：

```text
command_claim_ttl_seconds = 60
command_claim_renew_interval_seconds = 20
max_command_attempts = 5
```

这些值必须配置化。单次模型请求可能超过 60 秒，因此 Command claim 续期任务必须独立于模型 stream 消费运行；它与 thread advisory lock 是两个不同层次的机制。

### 12.2 一致性矩阵

| 故障点 | 结果 | 恢复方式 |
|-|-|-|
| Run Registry + start Command 成功，Graph 未启动 | pending Command | Worker 重新领取同一 Command |
| checkpoint 成功，Command 未标记 applied | Graph 已包含 Command ID | 对账后只补写 Command 状态，不重复 invoke |
| checkpoint 成功，产品投影未更新 | 投影落后 | Projector 从 checkpoint history 重建 |
| 投影先被误写或损坏 | UI 数据不可信 | 删除派生值并按 checkpoint 重放 |
| 工具成功，结果账本写入失败 | outcome 可能 unknown | 禁止自动重试，进入人工/工具专用核对 |
| 工具账本成功，checkpoint 未推进 | 已有 succeeded receipt | 恢复时复用结果，不重做工具 |
| ChatMessage + outbox 已提交，Provider 尚未调用 | 执行结果和消息已持久化 | Delivery Worker 领取同一 outbox，不重跑 Graph |
| Provider 明确失败 | outbox 回到 pending 或最终 failed | 按退避只重试 delivery；达到上限写脱敏失败事件 |
| Provider 已接收，本地确认前崩溃 | 远端结果 unknown | 使用稳定 Provider ID 时幂等；否则接受 at-least-once 重复风险，不重跑 Graph |
| run completed，渠道交付失败 | 执行完成、交付失败 | 只重试同一 `channel_deliveries` 记录 |
| source waiting，target callback 重复 | 重复 resume Command | Command 唯一键与 Graph 内 Command ID 双重拦截 |
| Worker 进程退出 | claim 最终过期 | 新 Worker 读取 checkpoint 后对账续接 |

### 12.3 Runtime Projector

Graph stream 只用于低延迟推送，不能作为持久化投影的可靠来源；连接断开或 Worker 崩溃会丢失内存 stream event。权威 Projector 必须在 checkpoint 提交后通过 Checkpointer API 读取 `StateSnapshot`：

1. 从 Run Registry 找到 `runtime_thread_id`，先完成 tenant / run 权限校验。
2. 通过 Checkpointer API 读取 checkpoint history，沿历史/父链定位 `projected_checkpoint_id` watermark 后的记录，再按最旧到最新顺序处理；checkpoint ID 只用于相等匹配，禁止按字符串或 UUID 大小比较。
3. 使用 `checkpoint_id + lifecycle_status + interrupts + next + state result` 生成稳定投影事件。
4. 在一个 Clawith 事务中 CAS 更新 `agent_runs.projected_*`、`projected_checkpoint_id`，并追加 `agent_run_events`。
5. 唯一键冲突表示该 checkpoint 已投影，安全跳过。
6. 实时 stream mapper 可以提前刷新内存 UI，但持久化值最终必须由 Projector 确认。

Projector 可以重放全部 checkpoint history，也可以只重建最新快照。前者恢复事件时间线，后者快速恢复 Run 列表；两种模式都不能调用 Graph 或产生外部副作用。若 watermark 对应历史已经被 retention 清理，Projector 必须从最新快照重建当前投影并记录 event-gap 告警，不能伪造缺失的事件时间线。

### 12.4 Reconciliation Job

后台任务定期扫描：

- claim 已过期或长时间 pending 的 Command。
- Command 未 `applied`，但 checkpoint 已包含其 Command ID 的记录。
- checkpoint 已 terminal、`projected_execution_status` 仍 active 的 Run。
- `lane_held = true` 但 checkpoint 已 terminal 的群 mention Run；幂等释放 lane 并唤醒同 lane 下一 Message Position。
- checkpoint 存在但 Run Registry 缺失或租户关联不一致的异常记录；只告警隔离，不自动猜测归属。
- `completed + delivery_failed` 的 run。
- 到期 `pending` 或 claim 已过期的 `channel_deliveries`；只重新发送已持久化消息。
- `agent_tool_executions.status=unknown` 的记录。
- 超过 retention 的 terminal checkpoint。

Reconciliation 只修复可由 checkpoint、Command 唯一键或产品原始事实确定的内容，不猜测外部副作用是否成功，也不得反向使用投影改写 Graph State。

## 13. 安全边界

- 先通过业务表校验权限，再读取 checkpoint。
- checkpoint 查询不能仅凭用户提供的 `thread_id`。
- State、event payload 和摘要不得保存密钥、token 或数据库凭据。
- `channel_deliveries.target` 只允许保存完成投递必需的小型路由；动态 webhook / interaction token 按敏感数据处理，不得复制到日志、Run event 或模型 Context。
- 大正文和大工具输出保存在现有 workspace、message 或 artifact 存储，Graph 只存引用。
- session soft delete 后，关联 state 不再进入正常 Context；后台按 retention 延迟清理。

### 13.1 Checkpoint 加密与内容限制

Checkpoint 可能包含用户消息、工具参数和摘要，敏感级别不低于 `chat_messages`。生产环境建议为 Checkpointer 配置 `EncryptedSerializer`，密钥从部署 secret 注入，不写入数据库或配置文件。

即使启用加密，仍必须在写入 State 前移除：

- provider API key、OAuth token、cookie、Authorization header。
- 数据库 DSN 和临时签名 URL。
- 工具返回中的原始凭据字段。
- 超过大小上限的正文和二进制内容。

### 13.2 Retention

Retention 分开管理：

| 数据 | 建议初始策略 |
|-|-|
| active/waiting checkpoint | run 存续期间保留 |
| completed/failed checkpoint | 先保留 30 天，再根据恢复/审计需求调整 |
| `agent_runs` | 作为业务记录长期保留或按产品策略归档 |
| `agent_run_commands` | 至少覆盖 checkpoint retention 与最大重投/对账周期 |
| `agent_run_events` | 与业务审计策略一致 |
| `agent_tool_executions` | 至少覆盖外部系统最大重试/对账周期 |
| `channel_deliveries` | 至少覆盖最大自动重试、人工排障和 Provider 对账周期 |
| `session_context_states` | session 有效期间保留，删除后延迟清理 |

30 天只是首个可运营默认值，不是最终合规结论；上线前需结合租户数据策略确认。

Retention 与 Context 选择相互独立：Run 或 checkpoint 仍在存储中，不代表它会进入模型上下文。Context Builder 默认排除 terminal、无关和长期未使用的 Run；恢复指定 Run 时才按 `thread_id` 读取其最新 checkpoint。

### 13.3 配置项

建议新增：

```text
AGENT_RUNTIME_V2_ENABLED=false
AGENT_RUNTIME_V2_AGENT_IDS=
AGENT_RUNTIME_V2_SOURCE_TYPES=task
AGENT_RUNTIME_GRAPH_NAME=clawith_agent_runtime
AGENT_RUNTIME_GRAPH_VERSION=v1
LANGGRAPH_CHECKPOINT_DATABASE_URL=
LANGGRAPH_AES_KEY=
AGENT_RUNTIME_COMMAND_CLAIM_TTL_SECONDS=60
AGENT_RUNTIME_COMMAND_CLAIM_RENEW_SECONDS=20
AGENT_RUNTIME_COMMAND_MAX_ATTEMPTS=5
AGENT_RUNTIME_CHANNEL_DELIVERY_CLAIM_TTL_SECONDS=120
AGENT_RUNTIME_CHANNEL_DELIVERY_MAX_ATTEMPTS=8
AGENT_RUNTIME_CHANNEL_DELIVERY_SCAN_SECONDS=0.5
AGENT_RUNTIME_SUMMARY_THRESHOLD_RATIO=0.85
AGENT_RUNTIME_SESSION_RECENT_MESSAGES=20
AGENT_RUNTIME_SESSION_COMPACT_MESSAGE_THRESHOLD=
AGENT_RUNTIME_RUN_COMPACT_MESSAGE_THRESHOLD=
AGENT_RUNTIME_RUN_COMPACT_TOOL_RESULT_BYTES=
AGENT_RUNTIME_VERIFY_REPAIR_COMPACT_ROUNDS=
AGENT_RUNTIME_MODEL_CAPABILITY_REFRESH_SECONDS=86400
MULTI_AGENT_COMPACT_MODEL_ID=
MULTI_AGENT_PLANNING_MODEL_ID=
AGENT_RUNTIME_CHECKPOINT_RETENTION_DAYS=30
AGENT_RUNTIME_EVENT_PAYLOAD_MAX_BYTES=16384
AGENT_RUNTIME_TOOL_RESULT_INLINE_MAX_BYTES=8192
```

`MULTI_AGENT_COMPACT_MODEL_ID` 和 `MULTI_AGENT_PLANNING_MODEL_ID` 都是平台级配置，只允许解析到启用且 `tenant_id IS NULL` 的 `llm_models` 记录；配置缺失、模型停用或指向租户私有模型时显式失败，不回退到任意业务 Agent 模型。

入口启用优先级：显式 Agent allowlist > source type > 全局开关。开关只决定是否接受新的 LangGraph Run；未命中时入口必须显式失败，不得回退旧 tool loop。已经创建并产生 checkpoint 的 LangGraph Run 必须继续按原 `graph_name + graph_version` 恢复，不受新 Run 开关影响。历史 `runtime_type = legacy` 只用于识别旧记录，Runtime Worker 不推进它，也不能把它转换为 LangGraph Run。

## 14. 分阶段实施

### 14.1 代码改造清单

| 位置 | 改造 |
|-|-|
| `backend/pyproject.toml` | 增加三个最小依赖，固定版本范围 |
| `backend/app/models/` | 新增六个 Runtime 模型；`channel_deliveries` 只作为投递 Outbox |
| `backend/alembic/versions/` | 新增表、约束、索引和 `LLMModel.max_input_tokens`、override、capability metadata |
| `backend/app/services/agent_runtime/` | 新增 Adapter、Command Worker、Graph、nodes、Projector、幂等和 reconciliation |
| `backend/app/services/llm/caller.py` | 抽出供 Graph node 使用的单次模型调用 port；Backend Cutover 后删除入口级旧 tool loop |
| `backend/app/services/agent_tools.py` | 增加工具 effect/retry 元数据；A2A 关联 run |
| `backend/app/api/websocket.py` | 改为消费 RuntimeEvent；abort 对应 cancel_run |
| `backend/app/api/feishu.py` | 迁移到 Runtime Adapter，删除入口级 runtime 逻辑 |
| 其他渠道入口 | 只保留消息解析、session 解析和 Runtime intake；Provider 结果由统一 outbox Worker 发送 |
| `backend/app/services/task_executor.py` | Single Runtime PoC 的首个 Adapter 调用方 |
| `backend/app/services/trigger_runtime/invoker.py` | TriggerExecution 映射 source_execution_id |
| `backend/app/services/heartbeat.py` | 删除独立 tool loop，改为 start_run |
| `backend/tests/` | 新增 runtime unit/integration/e2e suites |

### 14.2 向后兼容策略

- 统一聊天模型 Schema 必须先按 `../group-chat/chat-model-refactor.md` 第 7 章在维护窗口内强制迁移；停止全部写入方、完成迁移与校验后只启动依赖新 Schema 的后端，不做聊天 Schema 双写，也不允许新旧后端混跑。
- 下述 `runtime_type` 和 feature flag 只用于 Runtime 执行入口渐进迁移，不用于兼容统一聊天表的旧 Schema。
- `runtime_type` 在 run 创建时确定，之后不可改变。
- legacy ChatMessage 和 Tool Pair 继续可读，不做历史 checkpoint 回填。
- 上线前已存在的 session 没有 `session_context_states` 时按空摘要启动，首次达到阈值后创建。
- 前端先兼容现有 WebSocket 事件；RuntimeEvent 只作为后端内部稳定契约。
- Runtime 自身的业务表和 nullable 字段可以按入口渐进增加；这不改变统一聊天 Schema 已完成强制迁移的前置条件。
- 旧 loop 删除后不得通过 feature flag 恢复；回滚只能整体回滚到仍与旧 Schema 匹配的发布版本，不能在同一部署中混跑两套 Runtime。

### Phase 0：Schema、迁移与依赖基线

- 先编写并验证统一聊天模型强制迁移、六张 Runtime 业务表、`agent_runs` 调度字段、Model Capability 字段及全部约束和索引。
- 锁定 LangGraph / Checkpointer 固定 minor 版本并写入 lock file；建立独立 `langgraph_checkpoint` schema 的 setup / upgrade 脚本。
- 迁移只在测试数据库执行并完成 upgrade / downgrade / 历史数据校验；生产迁移仍与新 Single Chat 后端在同一维护窗口切换，不提前部署 Schema。

退出条件：迁移、约束、索引、Checkpointer setup 和实际版本 smoke test 全部通过，后续代码不再依赖未定义表结构。

### Phase 1：Single Runtime Core 与 Task PoC

- 实现 Adapter、最小 Graph、Command Worker、advisory lock、Projector、control guard、工具 receipt、delivery 和 reconciliation。
- 选择 `task_executor` 作为第一条实验入口，验证服务重启恢复、interrupt/resume、cancel、投影重建和副作用工具幂等，不改 Web Chat 主路径。
- PoC 使用“读取临时 workspace 文件 → 生成摘要 → 写入测试文件 → interrupt 等待确认 → resume 后完成”。

退出条件：同一 Run 在服务重启、重复投递和双 Worker 竞争下只推进一次，已成功工具不重复执行，cancel 能在安全边界停止。

### Phase 2：Single Chat 与全部单 Agent 后端入口

- Web Chat 通过 Adapter 创建前台 run。
- Graph stream 映射为现有 WebSocket 事件。
- 接入 Session Summary 和 token-budget context builder。
- 支持 abort → cancel run。
- 迁移 Task、Trigger、Heartbeat、oneshot、飞书及其他单聊渠道；渠道仅保留解析和交付。
- 接入 Single A2A 的 source / target Run 与 callback resume。

退出条件：所有 Single 入口使用统一 Runtime；普通问答、多轮工具、failover、断线重连、等待/恢复和后台重启恢复通过后端测试。

### Phase 3：Group Chat 后端

- 按 `../group-chat/technical-design.md` 实现群领域、mention、Planning Graph、dependency 调度、group context、共享 Compact 和群消息交付。
- 启用同 Agent 群 mention scheduling lane，验证 Message Position 串行不影响 Direct、Task、Trigger、Heartbeat 等其他 Run。

退出条件：Single 与 Group 共用同一聊天模型和 Runtime 底座，多 Agent 的 `parallel / sequential / dependency` 与失败传播通过集成测试。

### Phase 4：后端整体验证与旧循环清理

- 执行 Single、Group、渠道、A2A、删除、Compact、故障恢复和迁移维护窗口演练。
- 删除 `call_agent_llm_with_tools()` 与 Heartbeat / oneshot 等入口级独立循环；`call_llm()` 只保留 Graph node 内单次模型调用能力。
- 所有生产入口稳定后移除 legacy feature flag；旧循环删除使用独立 PR。

退出条件：代码搜索不存在生产入口级多轮 tool loop；所有后端路径通过统一验收，且没有新旧 Schema 或新旧 Runtime 混跑。

### Phase 5：前端统一更新

- 在后端契约稳定后统一更新 Direct Chat 与 Group Chat 前端、Run 状态、waiting/cancel、未读和错误提示。
- 前端只消费产品 API、ChatMessage 和稳定 RuntimeEvent，不读取 checkpoint 或内部 node。

退出条件：Single / Group 前端回归通过，前后端切换和回滚手册完成。

## 15. 测试与验收

### 单元测试

- Graph lifecycle transition、Command 去重、权限校验、Context budget、Tool Pair Integrity。
- Model Capability Resolver 按 `manual override > provider API > builtin registry > runtime config` 解析；人工 override 不被刷新覆盖。
- 独立 `max_input_tokens` 不扣输出，共享 `context_window_tokens` 按本次 `requested_max_output_tokens` 扣除；同时存在时取两个输入限制的较小值，未知语义按共享窗口处理。
- Provider Adapter 必须把独立输入限制写入 `max_input_tokens`，把输入输出共享限制写入 `context_window_tokens`；Gemini 独立输入/输出、Ollama 共享 `context_length` 和 Registry 语义分别覆盖测试。
- 仅有共享总窗口时在每次请求计算中扣除本次输出预留，不在 Resolver 缓存阶段提前扣除；能力仍未知的模型不得启用新 Runtime。
- primary failover 到更小窗口模型时重新执行 Context Builder 和 Run Compact，且不覆盖共享 Session Context。
- 单 Agent Compact 使用当前执行模型；多 Agent 缺少显式 `compact_model_id` 时不得启动共享上下文压缩。
- 多 Agent 压缩阈值取参与模型最小有效输入预算，Compact 模型窗口较小时按完整消息块分批处理。
- 工具幂等状态机、Graph route、finish → verify。
- Runtime Event 映射兼容性。
- 模型输出只接受 `tool_calls / wait / finish`；非法结构有界修复，能力不足模型按 gate 拒绝复杂 Runtime。
- control guard 在模型前后、工具前后识别 cancel，terminal cancel 被拒绝。
- Session Context Pack 默认最近 20 条消息、watermark 选择和 Run 相关性过滤。
- Run Compact 只替换已覆盖的 `run_messages`，不修改 pending / waiting / verification 精确状态。
- Tool Exchange 窗口边界分别落在 call 前、call/result 中间和 result 后时，只输出完整 Block。
- parallel tool calls 缺第一个、中间或最后一个 result 时，不得改单个 call 后继续请求模型。
- 最近 20 条边界需要扩展到 21～23 条时，完整 Tool Exchange 优先于硬消息条数。
- `covered_through_run_message_id` 不跨越 pending、started、unknown 或 malformed Tool Exchange。

### PostgreSQL 集成测试

- checkpoint 创建、读取、interrupt 和恢复。
- worker 中断、服务重启后续跑。
- pending writes 和双 worker 竞争。
- 同一 `run_id` 的双 Worker 只有持有 advisory lock 的 Worker 可以推进；连接退出后新 Worker 从 checkpoint 接管。
- active Worker 轮询 cancel；工具已 `started` 时先落真实结果再进入 `cancelled`。
- 同一 `scheduling_lane_key` 只能有一个 `lane_held`；按 `(created_at, id)` 串行，terminal reconciliation 能释放 stale lane。
- checkpoint 与业务投影不一致后的 reconciliation。
- 删除全部 `projected_*` 与 checkpoint 派生 lifecycle events 后，可从 checkpoint 重建相同最新状态和事件序列。
- Command 已进入 checkpoint、但 Worker 未写 `applied` 时，只补写 Command 状态，不重复 resume/cancel。
- waiting Thread 在发布新 `graph_version` 后仍使用原版本恢复；不兼容 node rename 必须被部署检查拦截。
- 两个并行 Run 同时提交 `SessionContextDelta` 时，旧 version 不能覆盖新 Session Context。
- Session Compact 失败后保留旧版本，并回退到旧摘要加最近 20 条用户可见消息。
- `succeeded` 但消息缺失的工具从账本重建 pair 并复用结果，不重复执行。
- `started` / `unknown` 副作用工具进入等待或 reconciliation，不通过裁剪触发重试。

### 端到端测试

- Web Chat 流式和多轮工具调用不回归。
- Task 重启恢复后不重复发送。
- Trigger 重复投递幂等。
- A2A target 完成后 source 正确恢复。
- 同一 Agent 已经创建的两条群 mention 业务 Run 严格按 Message Position 执行，同时 Direct/Task Run 不被该 lane 阻塞；另有用例确认 Planning 创建子 Run 前不占 lane。
- waiting_user 收到回复后继续原 run。
- soft delete session 不再注入旧摘要。
- completed 与 delivered 可独立失败和重试。
- terminal、无关和长期未使用 Run 不进入新 Run 上下文；明确 parent / child / dependency Run 只注入结果摘要和引用。
- Run 恢复继续使用原 `session_context_snapshot`，新 Run 使用最新 Session Context 版本。
- OpenAI Responses、OpenAI-compatible 和 Anthropic 输入均不包含孤立 call/result；新 Runtime 检测异常时 fail closed。
- 历史工具记录优先使用真实 `tool_call_id`，旧记录缺失时使用稳定兼容 ID。

### 上线门槛

- legacy / langgraph 可按 Agent 或入口灰度。
- 可查询 run 数、等待时长、恢复成功率、重复工具拦截数和交付失败数。
- 每阶段单独验收，不一次性全量切换。
- 删除旧 loop 前，所有生产入口必须已有等价 Graph 路径和回归测试。

## 16. 明确不做

- 不让 UI 或业务 API 直接解析 LangGraph checkpoint；通过可重建投影查询。
- 不把 `agent_run_events` 变成完整 trace 平台。
- 不同时维护自建生命周期状态机与 LangGraph checkpoint。
- 不在 v1 自动恢复 `unknown` 的副作用工具。
- 不在第一阶段重写现有 LLM provider client 和全部工具。
- 不声称 Graph 接入前已具备 checkpoint 级精确恢复。

## 17. 编码基线参数

开发前参数已经收口：

1. LangGraph / checkpointer 使用固定 minor、允许 patch 的版本策略；Phase 0 选择通过集成测试的实际版本并写入 lock file，之后升级必须执行 checkpoint 恢复回归。
2. Checkpoint 表固定使用同库独立 `langgraph_checkpoint` schema，通过独立 DSN / `search_path` 和官方 Checkpointer API 访问。
3. Command 使用可续期 claim；同一 Thread 推进互斥固定使用 PostgreSQL session-level advisory lock，不读取产品投影判断状态。
4. 第一批强制进入 Tool Execution Ledger 的副作用范围以 7.3 节清单为准；未明确分类的写工具按 `external_write + retry_policy=never` 保守处理。
5. terminal checkpoint 默认保留 30 天，可配置；`agent_runs`、交付事实和审计事件按产品数据保留策略管理，不随 checkpoint 自动删除。
6. Single Runtime PoC 使用 Phase 1 已定义的临时 workspace 读写、interrupt/resume 场景，并增加 cancel、重复 Command 和双 Worker 竞争验收。
7. 群 mention 业务串行使用 5.6 节 scheduling lane；Direct、Task、Trigger、Heartbeat 和其他 Run 不进入该 lane。

因此已经没有需要产品讨论才能开始编码的开放架构项。Phase 0 仍需用锁定依赖版本和真实 PostgreSQL 完成兼容性 smoke test；测试失败属于实现阻塞，不重新引入第二套状态系统。

## 18. 官方能力依据

- LangGraph Persistence：<https://docs.langchain.com/oss/python/langgraph/persistence>
- LangGraph Interrupts：<https://docs.langchain.com/oss/python/langgraph/interrupts>
- LangGraph Durable Execution / Functional API：<https://docs.langchain.com/oss/python/langgraph/functional-api>
- LangGraph Streaming：<https://docs.langchain.com/oss/python/langgraph/streaming>
- LangGraph Context：<https://docs.langchain.com/oss/python/concepts/context>
- Anthropic Models API：<https://platform.claude.com/docs/en/api/models/retrieve>
- Gemini Models API：<https://ai.google.dev/api/models>
- Ollama Show model details：<https://docs.ollama.com/api-reference/show-model-details>
- Ollama List running models：<https://docs.ollama.com/api/ps>
- OpenAI Models object：<https://platform.openai.com/docs/api-reference/models/object>
