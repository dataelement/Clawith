# Clawith 单 Agent 问题清单（2026-07-15）

> 状态：问题盘点、逐项决策与实施跟踪。Runtime 真值、Direct Session/Thread、Compact、Prompt V1、确定性 Verifier、RunView、Wait/Resume、Group 受影响路径和一次性 schema 迁移已经实现并完成定向回归；本轮确认的 Tool provider families 已分批迁入 typed Runtime，剩余名称继续 fail closed。ModelGateway、通用观测优化和部署运维不纳入本轮新增方案。
> 当前代码基线：分支 `feature/unified-chat-directory-pr760-regression` 的工作树；最终提交前以本文和 `runtime-architecture-decisions-2026-07-15.md` 的实施证据重新核对，不再把早期 `da9c18d8` 当作当前实现事实。
> 范围：单 Agent 的 Runtime、Context Compact、Prompt、工具调用、Verifier、Skill、产品状态与可观测性。Group Chat / 多 Agent 协作问题暂不进入本清单。
> 当前架构决策基线：[runtime-architecture-decisions-2026-07-15.md](runtime-architecture-decisions-2026-07-15.md)。该文档取代本文早期讨论中“保留 RuntimeProjector / `projected_*`”和“新增 `agent_run_execution_jobs`”的方案；本文对应段落保留问题证据，但不再作为实现依据。

## 0. 当前实施快照

截至 2026-07-16，本轮已经完成并有定向回归覆盖的主线包括：

1. 删除 `RuntimeProjector`，以 LangGraph checkpoint/Command 为执行真值，并由 `RunStateReader` 精确读取目标 Run。
2. Direct `ChatSession` 与 LangGraph Thread 统一为同一对话语义；跨 Run 继续同一 Thread，Run 仍表示一次独立调用。
3. `Agent.max_tool_rounds` 在 Run 创建时冻结为 model-turn limit；Runtime 不再用隐藏的 50 轮上限覆盖 Agent 配置。
4. 两套 Compact 合并为 Thread Compact，采用 `80% / 50%` high/low watermark、五段式有界摘要和 LangGraph RetryPolicy。
5. Prompt V1、动态上下文装配、Gemini 动态内容、单一 Current Run Directive、Role/Atlassian/OKR 重复注入与 Skill 路径闭环已经修复；Group/Agent dynamic data 与 allowlist Runtime Context 已从 system role 移入独立 user-role reference-data message。
6. `ToolExecutionOutcome`、Tool Ledger、私有 `ToolResultStore`、canonical Schema/readiness 和 typed-only Runtime 门禁已经落地；默认工具、Search、E2B、Dynamic MCP、Feishu F1-F4、Email、OKR O1-O2、Deploy、四个 Image 工具与 AgentBay A0/A1 已完成原生 typed 迁移，当前 canonical application-tool 计数为 `131 / 107 / 23`。剩余 `21` 个 AgentBay action、`feishu_approval_create` 和 `send_feishu_message` 继续 fail closed，不会回退到字符串式 legacy 执行。
7. RunView、刷新后的 Wait/Resume、终态/等待态前端 fail-closed 与 Group 公开 handoff/cutoff 路径已经完成回归。
8. 本轮表结构已经相对 `upstream/main` 合并为单个迁移脚本；不再叠加临时 feature migration。

最终完成状态、精确 typed 工具计数和全量回归结果以 D-020 实施记录及本轮最终验证为准；本快照不替代逐项验收，也不表示外部 Provider 已完成在线联调。

## 1. 证据口径

本文只按根因计数，不把同一根因造成的多个下游现象重复登记。

| 标记 | 含义 |
|---|---|
| `3010 已复现` | 已在 `192.168.106.118:3010` 的运行、数据库或日志中观察到 |
| `Benchmark 已复现` | Toolathlon 或专项长任务证据中已观察到，但不等同于所有线上场景都会发生 |
| `当前代码确认` | 当前 HEAD 的确定性代码路径存在，尚未完成对应线上场景复现 |
| `验证缺口` | 目标契约已有，但真实 PostgreSQL、容器、多副本或外部 sandbox 尚未验证 |
| `设计待确认` | 问题方向已记录，但最终技术方案尚未纳入当前决策基线 |

主要证据来源：

- [3010 HY3 单 Agent 长任务复盘](runtime-validation/2026-07-15-hy3-single-agent-longrun/review.md)
- [HY3 长任务产物质量复盘](runtime-validation/2026-07-15-hy3-single-agent-longrun/artifact-quality-review.md)
- [单 Agent 能力问题与改进方案](../benchmark/clawith-single-agent-capability-improvement-plan.md)
- [Toolathlon DeepSeek V4 Pro 详细报告](../benchmark/toolathlon-clawith-deepseek-v4-pro-report-2026-07-14.md)
- [单 Agent Runtime 测试用例](runtime-test-cases.md)
- 当前 HEAD 代码与 2026-07-15 的 3010 新鲜 Run 回归

## 2. 总览

目前共整理出 **19 个根因项**：P0 11 项、P1 7 项、P2 1 项。P0 不代表必须一次性一起修改，而是表示它会影响执行真值、任务完成可信度、发布安全或长任务回归。

| ID | 主题 | 问题 | 证据 | 优先级 |
|---|---|---|---|---|
| SA-R01 | Runtime 投影 | 合法 Graph 终态被空 bootstrap checkpoint 阻断投影 | 3010 已复现 | P0 |
| SA-R02 | Runtime 对账 | Command 重试耗尽后形成永久 `pending` 墓碑 | 3010 已复现 | P0 |
| SA-R04 | Runtime 调度 | Command 推进与产品副作用职责混在同一重试单元 | 3010 下游已复现 + 当前代码确认 | P0 |
| SA-R03 | 步数 | 固定 50 model steps，Agent `max_tool_rounds` 未接入 | 3010 已复现 | P0 |
| SA-C01 | Run Compact | 触发阈值小于保留窗口，形成 Compact 风暴 | 3010 已复现 | P0 |
| SA-C02 | Run Compact | 失败无熔断、超大块无降级、摘要无上界、异常难诊断 | Benchmark 已复现 + 当前代码确认 | P1 |
| SA-C03 | Session Compact | 无可用主模型时后台重复失败，缺少有效退避和运维信号 | 3010 日志已观察，待最小复现 | P1 |
| SA-P01 | Prompt | 全局 Prompt 过宽、重复、与当前工具能力可能不一致 | Benchmark 已复现 + 当前代码确认 | P0 |
| SA-T01 | 工具结果 | Wrapper 正常返回会掩盖业务失败并记为 `succeeded` | Benchmark 已复现 + 当前代码确认 | P0 |
| SA-T02 | 工具持久化 | Tool Result 含 NUL 时 PostgreSQL 落库失败 | Benchmark 已复现 + 当前代码确认 | P0 |
| SA-T03 | 工具选择 | 有效工具集解析、Schema 单源与可用性过滤不一致 | 3010 / Benchmark 已观察 + 当前代码确认 | P0 |
| SA-T04 | 工具安全 | 原始参数写入 `sanitized_arguments`，结果允许内联至 1 MB | 当前代码确认 | P0 |
| SA-V01 | Verifier | 默认 Verifier 只验证协议完成，不验证用户任务完成 | Benchmark 已复现 + 当前代码确认 | P0 |
| SA-S01 | Skill | Catalog 到实际可读 `SKILL.md` 的闭环存在确定性缺口 | 当前代码确认；待匹配 Skill 的定向回归 | P1 |
| SA-U01 | 产品状态 | 没有公开 Run 详情 API / 页面，失败时部分成果也不可见 | 3010 已复现 | P1 |
| SA-U02 | Wait / Resume | 前端未持久化并回传 `run_id/correlation_id`，刷新后可能断链 | 当前代码确认 | P1 |
| SA-O01 | Provider / Trace | Provider 根因、reasoning state 和阶段耗时不可完整追踪 | Benchmark 已复现 + 当前代码确认 | P1 |
| SA-O02 | Metrics / Retention | 调用计数不准、Runtime 指标和 checkpoint retention job 缺失 | 3010 已观察 + 当前代码确认 | P1 |
| SA-N01 | 运行噪音 | 每轮重复装载工具并产生大量 AgentBay 未配置 ERROR | 3010 已观察 | P2 |

## 3. Runtime 执行真值与步数

### SA-R01：Graph 已完成，但产品投影失败

> 方案状态：问题与根因仍成立；“保留并修复投影”的旧方案已经废弃。最终方向是删除 `RuntimeProjector` 与 `agent_runs.projected_*`，由 `RunStateReader` 按目标 Run/Command 的 checkpoint identity 精确读取完整 `StateSnapshot`。

- **现象：**3010 新鲜 Run `b6955669-dcaf-4e69-9859-bdffcb0ccc6e` 的 LangGraph checkpoint 已到 `completed`，但 `agent_runs.projected_execution_status`、Task 终态和产品结果没有更新。
- **根因：**LangGraph 1.2 会先产生一条 `step=-1 / source=input / values={} / next=__start__` 的框架 bootstrap checkpoint。Driver 已把空 `values` 当作“尚无业务 checkpoint”，Projector 却对 history 每一条都要求存在 `registry`，因此在第一条记录就报 `checkpoint registry is required`，后面的合法终态从未被处理。
- **代码位置：**`backend/app/services/agent_runtime/projector.py:232-290`、`langgraph_driver.py:291-300`。
- **影响：**Graph 的执行真值与产品看到的状态分裂；线上回归会表现为 Task 卡住，即使 Agent 实际已经完成。
- **测试缺口：**现有 Projector fixture 构造的每条 snapshot 都带 `registry`，没有使用真实 compiled graph 产生 bootstrap checkpoint，因此相关单测是假绿。
- **已确认方案：**
  1. 删除 `RuntimeProjector`、`projected_*`、projection watermark、相关索引与 history replay，不增加新的 projection table。
  2. `RunStateReader` 先校验 tenant/run/thread scope；稳定 Run 按最新 applied Graph Command 的 `applied_checkpoint_id` 读取，未 settle Command 才按 namespaced metadata 定位。
  3. 状态 classifier 同时检查 `values / next / tasks / interrupts`；framework bootstrap 只作为无业务状态处理，其他非法或矛盾快照 fail closed。
  4. 产品同步在稳定 checkpoint 之后独立幂等执行，失败不能重跑 Graph 或改写 Command/Tool 执行事实。具体以 ADR D-003—D-006、D-022 为准。

### SA-R02：Command 重试耗尽后形成永久墓碑

> 方案状态：问题与根因仍成立；Command 继续作为 durable invocation，负责从输入接受推进到合法 waiting/terminal，不再拆出 `agent_run_execution_jobs`。具体边界以当前架构决策基线为准。

- **现象：**上述 Run 的 `start` Command 在 post-checkpoint handler 失败后被重试 5 次，最终仍是 `pending / attempt_count=5 / post_checkpoint_handler_failed`。
- **根因：**每次领取先增加 `attempt_count`，side effect 失败后又把 Command 写回 `pending`；claim SQL 同时要求 `attempt_count < max_attempts`。达到上限的记录永远不会再被 claim，代码中的 `>= max_attempts` 对账分支在生产 SQL 路径不可达，后续 Command 还会被 `earlier_unfinished` 阻塞。
- **代码位置：**`backend/app/services/agent_runtime/persistence.py:486-540,611-630,737-755`、`command_worker.py:458-477`。
- **影响：**一次投影或终态 side effect 故障可永久堵塞 Run 的产品状态和后续输入；重启 Worker 也不会恢复。
- **已确认方案：**同一 `AgentRunCommand` 从输入接受一直推进到合法 waiting/terminal，不增加 Execution Job。checkpoint metadata 证明输入已经进入 Graph；仍 runnable 时用空输入从同一 checkpoint 继续，只有到达稳定边界才写 `status=applied + applied_checkpoint_id`。claim/进程失败后按 checkpoint 对账，绝不重复提交已接受输入；恢复耗尽必须进入显式 reconciliation/quarantine，不能留下不可领取的 pending 墓碑。Thread lock contention 只短退避，不消耗业务 attempt；产品同步失败不改回 Command pending。

### SA-R04：Command 推进与产品副作用职责混在同一重试单元

> 方案状态：职责混合的根因仍成立；“新增 Execution Job”的旧解法已被放弃。最终方向是由同一 `AgentRunCommand` 承担 durable claim、checkpoint 续跑和稳定边界收口，产品同步在其后独立幂等执行。

- **现状：**当前 Command Worker 观察到 checkpoint 已包含本次 Command ID 后，仍同步执行 Projector、用户可见交付、Session Context 合并、Task / Trigger / Heartbeat 回写及 lane release，全部成功后才把 Command 标记为 `applied`。
- **根因：**实现把三种不同事实合并成了一个重试单元：外部输入是否已被 Graph 接受、Graph 是否已经运行到稳定边界、Clawith 产品副作用是否已完成。这样既会让产品副作用失败反向消耗 Command attempts，也无法表达“Command 已接受，但 checkpoint 仍可继续执行或处于可恢复错误”的状态。
- **代码位置：**`backend/app/services/agent_runtime/command_worker.py:458-576`、`checkpoint_side_effects.py:180-254`、`worker_service.py:270-303`。
- **架构决策：**
  1. checkpoint metadata 中观察到 Command ID 只证明输入已被接受；`Command.applied + applied_checkpoint_id` 固定表示同一次 invocation 已到合法 waiting/terminal，或 cancel 已到控制边界。
  2. 同一 Command Worker 在 thread advisory lock 下从该 Command 的最新 checkpoint 持续推进，不增加 Execution Job 或第二张协调表；进程崩溃后由新 Worker 对账恢复。
  3. checkpoint 分类必须同时检查 `StateSnapshot.next`、`tasks`、`interrupts` 与 checkpoint 中的 `lifecycle`。只有这些信号一致时才可认定 waiting 或 terminal；仅凭 `lifecycle.status`、投影字段或 Command 状态均不成立。
  4. Delivery、Session Context 合并、Task / Trigger / Heartbeat 回写和 lane release 都是 checkpoint 之后的产品同步。删除 Projector，第一版不新增通用 `agent_run_effects`；各目标表使用自身唯一键、状态条件、CAS 或 watermark 作为 receipt，由 Reconciler 补齐。任何同步失败都不得把已收口的 Command 改回 pending，也不得驱动 Graph 重跑。
- **状态边界：**非 terminal lifecycle 且存在可运行的 `next / tasks` 为 runnable；非 terminal checkpoint 的 task error 为 execution error；合法 interrupt 与 `waiting_*` lifecycle 一致时才是 waiting；terminal lifecycle 同时要求无后续 node、无 active task、无 interrupt。`waiting_*` 无 interrupt、terminal 仍有 next / active task、interrupt 与 lifecycle 类型不一致等情况全部 quarantine，不交付伪造的等待或终态。
- **验收条件：**注入 Delivery 和任一来源回写失败后，Command 仍保持 `applied`；runnable / execution-error checkpoint 能在重启后由同一 Command work item 继续到真实 waiting / terminal；矛盾 checkpoint 不继续执行、不投递终态并产生可查询的 reconciliation 告警。

#### 当前实现调用链

```text
Command Worker claim
→ LangGraphRuntimeDriver.read_latest（当前只保留 checkpoint_id + values）
→ Command ID 未出现时执行完整 ainvoke
→ 再次读取 checkpoint
→ RuntimeCheckpointSideEffects
   → Projector 在 agent_runs 行锁内扫描完整 checkpoint history
   → Delivery
   → Planning checkpoint handler
   → Session / Task / Trigger / Heartbeat / Onboarding / A2A / lane terminal handlers
→ 上述全部成功后 mark Command applied
```

因此当前实现虽然没有让 Graph 节点直接读取投影，但 Projector 或任一产品处理失败都会通过 `post_checkpoint_handler_failed` 间接阻塞 Command；同时 Driver 丢掉 `next / tasks / interrupts`，无法区分“输入已接受”和“执行已停稳”。

#### 已确认的目标调用链

```text
claim AgentRunCommand + 获取同 Thread advisory lock
→ 按 thread_id + clawith_command_id 定位完整 StateSnapshot
→ 无该 Command checkpoint：提交一次输入
→ 已接受且 runnable：ainvoke(None) 继续同一 invocation
→ 合法 interrupt waiting / terminal：Command applied + applied_checkpoint_id
→ lifecycle 与 next/tasks/interrupts 矛盾：reconciliation / quarantine

Checkpoint 之后的产品同步
→ RunStateReader 按需读取，不写 projection
→ 创建/对账 channel_deliveries
→ 幂等合并 Session Context
→ 幂等回写 Task / Trigger / Heartbeat / Onboarding / A2A
→ 幂等执行 Planning 调度和 lane release
```

#### 历史草案：`agent_run_execution_jobs`（已废弃，不实现）

以下表结构是讨论过程中的旧草案，仅为保留决策记录；ADR D-005 已明确由 `AgentRunCommand` 自身完成 durable claim、checkpoint 续跑与稳定边界收口，因此**不得创建这张表**。

旧草案曾计划用 `requested_command_id` 唯一的 pending/claimed/settled/quarantined Job 保存恢复信号。最终拒绝理由是：同一次 invocation 已经有 `AgentRunCommand`、claim 和 checkpoint metadata，再加一张 Job 只会复制 work ownership、attempt 和 settle 状态。需要的 quarantine/error metadata 应收敛到 Command/诊断边界，而不是保留第二个调度实体。

#### 已确认的重试预算

| 故障 | 负责方 | 是否消耗 Command attempt |
|---|---|---:|
| 输入提交前数据库 / Checkpointer 临时故障 | Command Worker，指数退避 + jitter | 是 |
| Thread advisory lock busy | Command Worker，短 jitter | 否 |
| 非法 resume type / correlation / scope | Command Worker 直接 `rejected` | 否 |
| application checkpoint 已包含 Command ID | Command 不再提交输入；从同一 checkpoint 继续 | 不重复消耗输入提交 attempt |
| checkpoint runnable 或安全可恢复 task error | 同一 Command work item `ainvoke(None)` | 否 |
| waiting/terminal 与 `next/tasks/interrupts` 矛盾 | Command 进入 reconciliation/quarantine | 否 |
| Session / 来源回写失败 | Reconciler 根据目标表状态补齐 | 否 |
| 外部 Provider 发送失败 | `channel_deliveries` 自己退避 | 否 |
| 有副作用工具结果 unknown | Tool Ledger + interrupt / reconciliation，禁止盲重试 | 否 |

Command 的输入提交与 checkpoint 续跑必须分别记录可诊断错误，但仍由同一 durable work item 拥有；固定 0.1 秒循环重试必须移除，达到安全恢复上限后进入明确 reconciliation/quarantine，不能留下永久 pending 墓碑。

#### 对 Trigger 和其他入口的影响

该修复修改共享 LangGraph Runtime，因此所有使用 `AgentRun + AgentRunCommand` 的入口都会获得相同语义；入口的创建逻辑不变，变化发生在输入确认、崩溃恢复和终态产品同步阶段。

| 入口 | 保持不变 | 变化与验收重点 |
|---|---|---|
| Chat | start/resume/cancel 的产品入口 | checkpoint metadata 防止重复提交，稳定边界后 Command applied；崩溃后从 checkpoint 续跑；结果继续走 ChatMessage + Delivery Outbox |
| Task | Run 创建与 Task 归属 | terminal 后条件更新 Task/TaskLog；重复处理不重复完成 |
| Trigger | cron/event 条件与 TriggerExecution 创建 | terminal 回写可短暂延迟但可补偿；重复唤醒不能重复创建 Run |
| Heartbeat | 调度规则 | terminal 回写幂等；Reconciler 延迟不能产生重复 heartbeat |
| A2A | source/target Run 与 callback 关系 | callback 重复仍由 Command 唯一键拦截；完成回写不阻塞 Graph |
| Onboarding | 入口与业务状态 | terminal 回写可重放 |
| Group Planning | Planning Graph 与子 Run 计划语义 | 同样使用 classifier 和 Command 恢复；不能因产品同步失败重复规划 |
| Scheduling lane | Message Position 顺序 | lane release 可补偿；允许短暂延迟，不能因超时猜测 terminal 而越序 |
| Session Context | delta 生成逻辑 | 使用版本/CAS/watermark 幂等合并，失败不重跑 Graph |

虽然本文不盘点 Group Chat 产品问题，但共享 Runtime 改动必须覆盖 Planning Graph 和 lane release 回归，不能让同一套基础设施出现两种 Command 语义。

### SA-R03：固定 50 步忽略 Agent 配置

- **现象：**3010 HY3 长任务完成 A01-A11 后，在第 50 个 model step 以 `model_step_limit_reached` 结束；A12、manifest 和最终检查没有发生。
- **根因：**`DeterministicRuntimeNodeExecutor` 默认 `max_model_steps=50`；`requested_max_steps` 只会执行 `min(requested, 50)`，因此只能缩小、不能放大。生产 Worker 没有传其他上限，Task / Web Chat / 普通 Heartbeat / Schedule 也没有把 `Agent.max_tool_rounds` 固化进 Run snapshot。
- **代码位置：**`backend/app/services/agent_runtime/node_executor.py:341-352,439-468`、`worker_service.py:215-224`、`task_executor.py:87-108`。
- **影响：**配置为 80、100 或 200 的 Agent 在新 Runtime 中仍会在 50 步被硬截断，长任务无法自然完成。
- **去重说明：**A12 缺失、manifest 缺失、plan 未完成、最终全量复核未发生，都是该根因的下游结果，不再拆成多个问题。
- **测试缺口：**当前测试只验证 requested limit 可以缩小，没有覆盖第 51 步继续运行或 Agent 配置进入 immutable snapshot。
- **已确认目标语义：**`Agent.max_tool_rounds` 是一次 Run 的模型决策轮次硬上限，一次模型回复只计一轮，工具执行不另外计数。上限在 Run 创建时固定，wait/resume 不清零；达到上限时不做第 N+1 次调用，以 `model_step_limit_reached` 结束。
- **已确认入口语义：**Chat、Task、Trigger 和普通 Heartbeat 使用 Agent 配置；oneshot 只能通过 `min(Agent 上限, oneshot 请求值)` 收紧，不能放大。数据库默认 50 只用于新 Agent 配置，Runtime 不再保留隐藏 50 轮 hard cap 或静默兜底。
- **已确认实现落点：**`RuntimeCommandIntake.start_run()` 计算最终上限，新增 `agent_runs.model_turn_limit` 在 Run 创建时写入；每次 invocation 通过 `RuntimeContext.model_turn_limit` 传入，`node_executor` 只计数和执行。oneshot 原始请求作为 Runtime 内部元数据保存，不进入模型 `initial_input`。
- **迁移约束：**暂不单独创建迁移脚本；待所有表结构决策完成后，对照 `main` 的最新 schema head 一次整理，不堆叠多个临时迁移。

## 4. Context Compact

### SA-C01：Run Compact 风暴

- **现象：**3010 长任务出现 41 段可见 Compact，平均 86.5 秒、最长 148 秒、合计约 3547 秒，占整次 Run 的约 88%；Run Summary 从约 2.3 KB 增长到 18.8 KB。
- **根因：**部署配置 `AGENT_RUNTIME_RUN_COMPACT_MESSAGE_THRESHOLD=10`，压缩后却保留 20 条消息。当前触发逻辑只判断“消息总数是否达到阈值”，没有 watermark 增量、高低水位、cooldown 或 hysteresis；压缩完成后依然满足触发条件，因此每轮工具后再次压缩。
- **代码位置：**`backend/app/services/agent_runtime/run_compactor.py:178-215,546-575`、`config.py:138-145`。
- **影响：**长任务主要时间和 token 消耗在重复压缩，而不是业务模型或文件工具。单纯放开 50 步后直接重跑，会把风暴延长到第 50 步以后。
- **配置缺口：**配置层只校验每个值大于 0，不拒绝 `message_threshold <= retained_window`；Run Compact 还复用了 Session 的 recent-window 配置。
- **测试缺口：**现有测试只验证 `threshold=21 / recent=20` 的单次成功，不验证连续工具批次后的重复触发次数。

### SA-C02：Compact 失败恢复和可诊断性不足

- **已观察问题：**
  - `academic-pdf-report` 连续 20 次压缩失败，没有熔断，额外增加约 131 秒；checkpoint history 接近 946 MB。
  - `hk-top-conf` 出现 16 次 `run_compact_block_too_large`。
  - 3010 长任务有 9 次 `boundary unavailable`、2 次 invalid output、2 次 generic failure。
  - generic exception 被收敛成无堆栈的 `run_compact_failed`，缺少 provider / parse correlation。
- **当前代码风险：**Summary schema 和服务没有明确尺寸上界；相同错误没有退避或 circuit breaker；单个超大 Tool Exchange 没有结构化降级路径。
- **上下文连续性风险：**模型通常只注入最近 20 条 Run Message，而 token Compact 主阈值约为窗口的 85%。在部分任务中，旧消息可能先退出 recent window、又尚未进入摘要。长任务本次依靠 `Summary + Recent Messages` 继续到 A11，但不能据此证明所有任务都无空档。
- **必须保留的正确行为：**成功 Compact 已验证不会拆散 Tool Call / Tool Result 原子对，这部分不是问题，修复时不能回退。

### SA-C03：Session Compact 无可用模型时重复失败

- **现象：**3010 后台日志反复出现 Session Agent 没有当前 primary model、无法计算 compact budget / 选择 compact model 的错误。
- **边界：**Toolathlon 本轮 Run 都是 `session_id=null`，没有真正验证 Session Compact；因此该项来自线上后台日志，不应借用 Benchmark 分数证明。
- **影响：**产生无效扫描与日志噪音，且管理员无法区分“无需 Compact”“暂时不可用”和“配置错误”。
- **待补验证：**需要一个有 Session、无 primary model 的最小集成用例，确认扫描频率、退避和告警行为后再定最终修复。

### Context Compact 已确认修复方向

Compact 的最终决策以 `runtime-architecture-decisions-2026-07-15.md` 的 D-015 / D-016 为准：

1. Direct Chat 只保留一个跨 Run 的 Thread Compact，不再并存 Run Compact 与 Session Compact；现有两套实现合并后，SA-C03 不再单独扩展后台压缩策略。
2. 使用有界 Running Summary 和未压缩 Recent suffix；摘要固定保留任务目标与约束、已完成结果、关键决定与证据、未完成或受阻事项、接下来准备做什么。
3. 按有效输入预算的 `80% / 50%` high/low watermark 触发和收缩；Summary 上限为 `min(4096, effective_input_budget * 25%)`，Recent 上限为 `min(8000, effective_input_budget * 25%)`，二者合计不超过有效预算的 50%。
4. Recent suffix 按完整语义块和 token 选择，不固定保留 20 条；Tool Exchange 必须整体保留、整体摘要、按账本重建或等待对账，不能拆分 call/result，也不能因 Compact 重跑已执行工具。
5. Compact node 不吞异常。瞬时错误直接使用 LangGraph `RetryPolicy(max_attempts=3)`；确定性错误不重试。重试耗尽后 invocation 停在失败 node，保留旧 Summary、原消息和 watermark，不继续向业务模型发送超限上下文。
6. 跨 invocation 的重试统一归属 Runtime Command Retry；Compact 不新增失败计数、fingerprint、熔断表或专用持久化字段。

## 5. Prompt 与动态上下文

### SA-P01：Prompt 过宽、重复且可能与实际能力不一致

- **现状：**Benchmark 第三轮基础静态 Prompt 约 6,733 字符；3010 长任务每次业务调用固定注入的静态/动态 prompt 约 4.7k 估算 token，另有约 8.4k token 工具 Schema，业务消息前固定上下文约 13.1k token。
- **主要问题：**
  - 无关任务也长期注入 Agent 身份、Role、Soul、公司信息以及 Workspace、Memory、Focus、Trigger、MCP、飞书、Atlassian 等手册。
  - Tool 参数同时存在于 Prompt 和 Schema，存在重复与漂移。
  - Prompt 可能描述当前 Schema 中没有或当前环境不可用的工具。
  - 强制检查 Focus、写 Memory、先 `list_files` 等规则会诱导无意义 Tool Call。
  - 全局默认“编辑杂志视觉风格”会污染普通检索、数据处理和工具任务。
  - Durable Runtime Context 仍包含较多空值、控制字段和审计字段；同一用户任务还可能以 goal、initial input 和 user message 多次出现。
- **影响：**提高每步 token 成本、挤占任务注意力，并增加错误工具选择和无关调用概率。
- **已确认的 Prompt V1：**以 `Name + Clawith digital employee identity + Soul` 开头；`role_description` 完全不进入模型 Prompt，也不作为 Soul 缺失兜底。随后是 Clawith 协作组织，以及 Memory、Workspace、Focus、Trigger、Directory 五个短机制说明，再进入 Objective、Instructions、Constraints、Runtime Protocol、Tool Policy、条件 Capability/Skill、Output 和 Verification。完整模板与装配顺序见 `runtime-architecture-decisions-2026-07-15.md` 的 D-017 / D-018。
- **已确认的加载边界：**Memory、Workspace、Focus、Trigger、Directory 的机制不变量短常驻；动态 Memory 内容走动态后缀。Focus/Trigger 的具体操作、Experience、MCP、飞书、Atlassian、A2A、Group 等仅按本轮真实能力条件注入，名称和参数以 Tool Schema 为准。
- **实施状态：**原生 Gemini `dynamic_content` parity、当前输入与 `runtime_instruction` 去重、Role 重复注入和 Atlassian 错误硬编码工具名均已按 D-018/D-019 修复；Agent dynamic data 与 Runtime Context 也已退出 system role。这里保留原问题证据，不再把它们列为待实现缺口。
- **本轮完整审计新增确认：**后续不能只替换 `agent_context.py` 的文案，还必须同步解决以下装配问题：
  - Runtime 已先解析最终工具却没把工具集合交给 Prompt builder；legacy caller 则先 build Prompt、后解析工具，导致条件 Capability 无法可靠实现。
  - Task、Heartbeat、Oneshot、Schedule、Planning child 没有独立 user message，唯一 goal 目前只在标为 `data, not instructions` 的 Runtime JSON 中；必须统一生成一个可信 `Current Run Directive`。
  - Trigger、Heartbeat、Onboarding 和两类 A2A 的任务正文分别在 message、goal、payload/initial_input/runtime instruction 中重复；每次模型请求只能保留一个权威指令正文。
  - 全量 Active Triggers、Company Information 和 Relationships 当前以过宽或错误信任级别进入 System Prompt；Trigger 只留相关且有界的结构化状态，Company/Relationships 只作有界动态 data。
  - `supervision_reminder.py` 当前没有生产入口且自身存在 tenant/幂等缺陷；本批删除或显式隔离该死路径，不为它新增 plain-reply Prompt。未来恢复督办时走 Task/Trigger Runtime。
  - Onboarding 首轮只有 `finish/wait`，Group 有单独 scope 和 Group tools；两者都不能继承与真实工具不符的通用操作提示。Group Planning 根节点及 Compact 内部模型调用继续使用各自专用 Prompt。
  - `role_description` 只退出“当前 Agent 的模型身份/指令”；创建表单、UI、Directory、规划候选和其他成员能力元数据继续保留。初始化/修复 Soul、onboarding greeting 和 Group self context 的旁路必须一并清理。
- **统一修改清单：**所有 Prompt、Tool Schema、特殊 Agent/功能、入口矩阵、无需改项目与回归用例已记录在 `runtime-architecture-decisions-2026-07-15.md` 的 D-019。后续实现以该清单为范围，不再靠删除旧 Prompt 后临时补漏。
- **本版本边界：**只修上述正确性问题，不引入 Tool Search、不建设复杂语义 Verifier，也不继续做 Prompt 文案和工具面调优。

## 6. 工具调用与结果语义

### SA-T01：工具执行成功与业务成功混在一起

- **现象：**工具 Wrapper 只要没有抛 Python exception，返回的字符串即使表达 HTTP 403、依赖不可用、非零退出或业务失败，也可能被 ledger 记录为 `succeeded`。
- **代码位置：**`backend/app/services/agent_runtime/tool_step_service.py:358-380` 直接把返回字符串交给 `mark_tool_execution_succeeded`。
- **影响：**模型和 Verifier 读取到错误的执行事实，可能把“工具调用完成”误判成“用户目标完成”。
- **证据边界：**该问题在此前 Benchmark 中已复现；3010 HY3 noexec 长任务按要求跳过 `execute_code`，不能写成该次长跑再次复现。
- **已确认方案：**保留 `agent_tool_executions`，不增加第二张结果表或平行 outcome 类型；扩展现有 `ToolExecutionOutcome` 供普通工具与 A2A 共用。`succeeded` 必须由工具契约明确证明业务成功，明确失败记 `failed`，副作用发出后不可判定记 `unknown`。各工具族必须依据 Provider 结构化响应、HTTP 状态或 exit code 适配，不能再把任意正常返回或展示文本前缀当执行事实。
- **重试边界（已确认并实现）：**`tool` 节点使用 LangGraph 原生 `RetryPolicy`；一个 node task 只推进一个 pending call，使 node retry budget 与 ledger receipt 一一对应，并在切片前校验整批 call ID 唯一且非空。`waiting_agent` resume 会先完成同一 assistant message 的剩余 Tool Call，再把协作结果交回模型，保证每个 call 恰有一个 ToolMessage 且不丢 tail。只有持久化策略为 `read + safe` 且 typed outcome 明确为 `failed + retryable` 时，才在同一 `tool_call_id` / 同一 ledger receipt 内最多执行 3 次 Provider attempt（首次 + 2 次重试）。每次授权前持久化递增独立 `attempt_count`，中间失败不生成 ToolMessage；最终成功、确定性失败或预算耗尽后只生成一个 Tool Result。预算耗尽会返回 non-retryable，并阻止模型无新信息地原样重复。Terminal failed 不再重开；只有明确的 durable retry-pending marker 能授权下一次 Provider attempt，单纯 lease 过期不算失败证据。无 marker 的过期 safe read 必须先探测私有 Result Store：有成功 envelope 则恢复 success，确认 missing/损坏/scope 不匹配后才关闭为“结果不可用”的 non-retryable failed；瞬时存储或 ledger 异常只 defer，不关闭 Run/receipt。所有路径都不重调 Provider，也不伪装成 retry-exhausted。未分类 exception、归档/settlement 失败、write/external_write 和 unknown 均不进入该重试。模型之后主动生成的新 call 是新的模型决策，不计入 Runtime 自动重试。第一版仍保持 Tool Call 顺序串行。

### SA-T02：NUL 字节导致 Tool Result 无法持久化

- **现象：**PDF / Playwright 工具结果含 NUL 时，写入 PostgreSQL `agent_tool_executions.result_summary` 报 `invalid byte sequence for encoding UTF8: 0x00`，随后表现为 Tool Call / Result 不成对或 Run 等待。
- **根因：**Tool Result 入库前没有统一移除或转义 NUL，也没有强制大结果转 `result_ref`。
- **影响：**工具可能已经执行并产生副作用，但结果账本没有落库；自动重试存在重复副作用风险。
- **已确认方案：**Tool output 在入库前统一规范化，实际 NUL 不进入 PostgreSQL，同时保持 execution fact 与 Tool Call/Result 配对；合法 tab/newline/carriage return 保留，已知 credential material 同步脱敏并记录计数。复用现有 8192-byte inline 配置；超限的规范化、credential-redacted 结果写入 Runtime 私有 StorageBackend namespace，ledger 只保存 opaque `tool-result://{execution_id}`。读取时必须经 ledger 校验 tenant/run，opaque ref 自身不是授权边界。
- **非原子边界：**先用 execution ID 写确定性 Result Store envelope，再 settle ledger；对象成功但 DB settle 失败时保持 started/fail-closed，由 Reconciler 补 settle且绝不重执行。归档失败不改变已确认外部副作用事实；cleanup 失败同样不能触发重执行。

### SA-T03：有效工具集解析、Schema 单源与可用性过滤不一致

- **现状：**生产源码约有 43 个默认工具；飞书 Agent 可能再增加约 25 个，Group 场景再增加 8 个，Runtime 还加入 `finish` / `wait`。3010 长任务每个业务模型步骤发送 42 个工具 Schema，约 25.2 KB / 8.4k 估算 token。
- **问题：**现有过滤主要依据 Agent 静态配置、Channel 和 Group 条件，没有针对当前任务形成 Run 级工作集；明确禁用或依赖不健康的工具仍可能出现在 Schema 中，只靠 Prompt 约束模型不调用。
- **影响：**固定 token 成本高；“Schema 可见”被误认为“能力可用”；不相关工具增加误调用概率。
- **已确认方案：**本版不做 Run 级语义工作集、语义任务路由、Tool Search 或 `enable_tool_group`。每个 model step 根据全局启用、Agent assignment、授权、Channel/Group/source scope 与确定性的本地 readiness/prerequisites 解析当前有效工具集，tool 执行前再次校验撤权；不做 live health ping，也不因 Provider 临时故障让 Schema 抖动。工具数量/token 的进一步优化后置。
- **修复前的相邻契约问题（已落地）：**模型工具曾主要来自数据库 Seeder，`AGENT_TOOLS` 与 `BUILTIN_TOOLS` 双源且共享名字中存在大量 description/schema 漂移，Seeder 还重复定义 4 个 OKR 工具。当前 Seeder 与 Runtime 已统一从 canonical builtin definitions 派生，Trigger、消息、A2A、Workspace、Focus、图片、代码执行、MCP、Feishu、AgentBay、wait 和 Group memory 的已确认契约冲突按 ADR D-019/D-020 修复或显式 fail closed。
- **Schema 单源：**建立一个简单的 builtin definition 数据模块，Seeder 与 Runtime 都从它派生；数据库只保存 builtin 的启停、分配和配置，MCP/Atlassian 等动态工具仍以数据库/发现结果为真相。不新增 Registry class 或插件框架。

### SA-T04：工具账本没有真正脱敏，大结果仍内联

- **现状：**`tool_step_service.py:342-355` 把原始 `arguments` 同时作为 `arguments` 和 `sanitized_arguments` 传入；`tool_execution.py:597-601` 允许 `result_summary` 最长 1,000,000 字符。
- **影响：**API key、OAuth token、cookie、Authorization、DSN、签名 URL 和大段正文可能进入 tool ledger、checkpoint 或后续摘要，形成安全、存储和 retention 风险。
- **证据状态：**当前代码确定存在，但尚未用真实 secret 做线上复现；应作为发布安全门禁处理，而不是等待线上泄漏证明。
- **已确认方案：**raw args 仍必须随 pending Tool Call 保存在 LangGraph checkpoint，除此以外不再复制进 ledger/log；`sanitized_arguments` 按 canonical sensitive paths 与通用 secret 规则递归脱敏，本批不新建 request store。统一 migration 只在原表增加显式 `effect / retry_policy / result_metadata`，不增加冗余 `business_status`；旧 metadata 必须 backfill，缺失/非法按 `external_write/never`，迁移期保留兼容读取。`result_metadata` 使用固定白名单和尺寸上限，同时清理 autonomy、activity/chat error 等旁路日志。
- **相邻契约：**Focus 默认不返回 completed；Feishu Calendar 明确为 Bot 主日历，update/delete 只要求 `event_id`；Group member 对 Agent 显式返回 `agent_id` 供 Group Memory 使用。完整定义、影响文件和拒绝方案见 ADR D-020。

## 7. 完成验收与 Skill

### SA-V01：Runtime `completed` 不等于用户任务完成

- **现状：**默认 `DeterministicRuntimeVerifier` 只检查：`finish` 内容非空、`finish` 是本轮唯一 Tool Call、没有 pending Tool Call；生产 Worker 没有注入 task-specific verifier。
- **Benchmark 证据：**第三轮有 26 个任务进入 Runtime `completed`，但只有 5 个通过外部 Evaluator；其余 21 个属于“协议正常结束，业务结果不合格”。这不能当作模型最终能力分数，但足以证明默认完成条件过弱。
- **典型后果：**必要文件或字段缺失、近似结果未核实、只完成部分步骤、失败工具被当成功，仍然可以调用 `finish`。
- **已确认语义：**`completed` 只表示 Runtime 正常结束并通过确定性协议检查，不等于外部 Evaluator 已证明业务答案完全正确。
- **已确认方案：**保留 `RuntimeVerifier` 边界，生产 Worker 注入能读取可信 Tool Ledger/Result Store 的确定性实现：finish 非空且独占、无 pending Tool Call、无 started/unknown；Finalizer 只从 succeeded `ToolExecutionOutcome` 收集 artifact/evidence refs，Verifier 校验其属于当前 tenant/Run 且真实可读。`finish` 仍只接收 `content`，不增加第二套引用申报协议。并非所有历史 failed 都阻止完成，因为 Agent 可能已经通过替代路径恢复。
- **repair 边界：**可修协议/引用错误最多 repair 两次；权限、配置和不可恢复错误直接 typed failure，unknown side effect 由 Tool Ledger 在 finish 前进入 wait/reconciliation。Verifier 只返回 pass/repair/fail，不建立第二套等待路由。Preflight/用户确认属于 Tool Policy。
- **本版明确不做：**不生成 LLM Task Contract，不引入第二模型或通用语义 Verifier。`finish` 文案同步强调“用户要求已完成且必要验证通过后调用，不用于中间进度”。完整方案见 ADR D-021。

### SA-S01：Skill Catalog 到实际读取存在确定性缺口

- **现状：**Clawith 会把 Skill 的 `name / description / path` 组成 Catalog，并提示模型用通用 `read_file` 读取 `SKILL.md`。
- **确定的代码问题：**Catalog 可能存在但 `read_file/list_files` 未实际启用；发现小写 `skill.md` 时，索引仍可能广告大写 `SKILL.md`，在大小写敏感存储上会得到不可读路径。
- **证据边界：**35 个已启动 Benchmark 任务读取 `SKILL.md` 为 0，不能单独证明路由失败，因为这些任务可能本来就不匹配任何 Skill。稳定性必须用“任务明确匹配某个已安装 Skill”的定向回归验证。
- **本版本方案：**继续使用现有 `read_file`，不新增 `load_skill`、自动路由器或 Tool Search。只有 loader 实际可用且广告路径可读时才注入 Catalog；明确匹配时，Prompt 要求先读取完整指令再执行。

## 8. 产品状态、Wait / Resume 与部分结果

### SA-U01：Run 状态没有产品化

- **现象：**当前没有可点击的 Task / Run 详情 URL，也没有公开的 Run 状态/时间线 API。Agent Activity Log 不是 Run 详情页。
- **3010 影响：**长任务 A01-A11 产物真实存在，但失败后 `projected_result_summary=null`，用户看不到“已完成到 A11、A12 未完成”的正式部分结果；本次新鲜 Run 又因 SA-R01 完全没有终态投影。
- **技术方案已确认：**本批不做完整 Run 详情页、时间线或 Dashboard，只建立 `RunStateReader -> typed RunView`。稳定 Run 按其最新 applied Graph Command 的 `applied_checkpoint_id` 精确读取，未 settle Command 按 namespaced metadata 定位，并用完整 `StateSnapshot` classifier；绝不读取同 Thread 最新 checkpoint 代替目标 Run，也不依赖 `projected_*`。
- **最小契约：**RunView 返回 identity、source/goal、execution status、current node、model step count、waiting/correlation、result/error、verification、delivery 与关键时间；不暴露 checkpoint blob、完整 Prompt、raw Tool 参数或秘密。
- **仍在 backlog：**可点击的 Run 详情页、完整时间线和失败后的部分结果产品展示没有在本批解决；不能把 RunView 技术契约写成 SA-U01 整体已完成。

### SA-U02：Web Wait / Resume 依赖当前 WebSocket 内存

- **后端现状：**后端支持显式 `run_id` / `correlation_id` resume，并在 waiting/done packet 中返回这些字段；同一 WebSocket 连接中还会暂存在 handler 内存。
- **前端现状：**`AgentDetailPage.tsx:3430-3446` 发送用户消息时不带两个 ID，`6783-6792` 的 abort 也只发送 `{type: 'abort'}`；收到 done/wait packet 后没有持久化对应 ID。
- **影响：**刷新页面、重连、同 Session 多个 Run 或连接迁移后，回复/取消可能无法精确指向原 waiting Run。
- **证据状态：**代码路径已确认，刷新场景尚未完成真实 E2E；进入 Web Chat 修复阶段时应按该阶段 P0 验收。
- **已确认方案：**服务端持久状态是唯一真相，增加 Session runtime-state 查询；`activeRun` 只是按 ChatSession 保存的前端运行时缓存，不是 localStorage 真相，打开、刷新和重连时必须从服务端重建。回复 waiting Run 必须带 `run_id + correlation_id`，cancel 必须带 `run_id`；`waiting_user` 不能被 done packet 当终态清空，waiting 后仍可入队 cancel。该查询保证状态与 resume/cancel identity，最终消息投递仍由 Delivery/Reconciliation 保证。
- **拒绝猜测：**缺 ID、旧 correlation、错 tenant/agent/session/user 或多个候选全部 fail closed；删除 WebSocket 连接内存隐式 resume 兜底。完整方案与回归矩阵见 ADR D-022 / D-023。

## 9. Provider、指标、Retention 与日志

### SA-O01：Provider 和模型调用归因不足

- Provider 原始错误类型、HTTP 状态、request ID 和安全截断 body 没有完整保留，多个失败最终只剩 `model_call_failed`。
- 非流式 Provider 路径没有完整保存 `message.reasoning_content`，后续工具回合无法稳定回传 reasoning state。
- thinking 配置、reasoning usage、context build、provider queue、TTFT、generation、compact validation 等阶段缺少统一 ledger / span。
- 4 个 Benchmark `model_call_failed` 因此无法可靠归因给 DeepSeek、网络、超时、解析或 reasoning 工具协议，不能武断算成 Agent 或模型问题。

### SA-O02：调用计量、Runtime 指标与 Retention 不完整

- 3010 长任务 token 增量约 1,664,939，但 `llm_calls_today` 仍为 0，说明新 Runtime 的调用次数/配额计量没有正确接入；token 计量本身有增长。
- 当前没有可查询的 Run 数、等待时长、恢复成功率、重复工具拦截数、outbox backlog、交付失败/重试等 Runtime dashboard。
- `AGENT_RUNTIME_CHECKPOINT_RETENTION_DAYS` 配置存在，但当前没有独立 retention / pruning job；设计文档中的 30 天只是建议初值，不能当作已经生效的合规策略。

### SA-N01：重复装载和错误日志噪音

- 每个模型/工具轮次都会重新装载工具；3010 长任务日志中出现 100 次 `No DB config found for agentbay_browser_navigate` ERROR。
- 本次没有证据表明这些 ERROR 直接造成错误结果，因此列为 P2；但它们会掩盖真实故障，并暴露同一 Run 可缓存的静态 Prompt / Tool Schema 重复组装。

## 10. 发布与测试门禁（不计入上述 19 个根因项）

### D-GATE-01：Checkpoint bootstrap 发布链不完整

- **状态：**P0 发布风险，现有 3010 库已经初始化，因此不是这次长任务失败根因。
- **本轮边界：**保留问题证据，但按当前决策不继续讨论或实施部署运维方案，也不把它混入本轮 Agent Runtime 代码批次。
- 当前 `backend/entrypoint.sh` 只执行 Alembic 后启动服务，没有执行 `backend/app/scripts/setup_langgraph_checkpoints.py`。
- setup 脚本没有多副本并发 advisory lock；checkpoint DSN 对 asyncpg `ssl=` 的兼容规范化也未落入当前 HEAD。
- Worker 已有 schema readiness fail-fast，但 readiness 只能拒绝未初始化环境，不能代替 bootstrap。
- `c959dffe` 仍不是当前 HEAD `da9c18d8` 的祖先。新库、灾备重建和多副本部署前必须恢复并做生产等价演练。

### TEST-GAP-01：测试目录不是失败统计表

`runtime-test-cases.md` 同时包含目标契约、已覆盖用例、`BLOCKED` 项和真实环境验收缺口。不能把其中所有 P0/P1 或未执行项都算成当前线上故障。尤其以下内容应标为验证缺口：

- 真实 PostgreSQL checkpoint setup / restart / advisory lock；
- 多副本容器启动、维护窗口和回滚；
- 飞书、钉钉、企业微信、微信、Slack、Teams、WhatsApp、Discord 的真实 sandbox 收发与失败重试；
- Session Compact 和 Group shared compact 的专项测试。

## 11. 明确排除或不重复计数

以下内容不作为独立 confirmed 产品问题：

1. **A12、manifest、final checks 缺失：**都是 SA-R03 的下游结果。
2. **Run Summary 只到 A09：**A10/A11 仍在 Recent Messages，符合当前 `Summary + Recent` 结构；这次没有发生确定的数据丢失。摘要滞后和潜在窗口空档已归入 SA-C02。
3. **Tool Call / Result 原子性：**成功 Compact 已验证没有拆散工具对，是必须保留的正向行为。
4. **HY3 provider 稳定性：**3010 长跑没有出现 timeout、429 或 provider failure，不能把慢归因给 HY3 网络；主要耗时证据指向 Compact 风暴。
5. **`execute_code`：**该长任务明确 SKIPPED；此前 Benchmark 的 Redis / 非零退出问题分别归入环境健康与 SA-T01，不能伪称本次线上复现。
6. **5 / 37 跑分：**三轮不是严格单变量 A/B，不能把 5 / 37 当成 Clawith 单 Agent 或模型的最终能力上限。
7. **双 Workspace：**Toolathlon 同时开放两套 Workspace 是 Benchmark harness 问题，不是生产 Runtime 根因。
8. **`daily_token_usage.usage_date`：**collector 查询了不存在的字段，真实字段为 `date`；这是证据采集工具 bug，不是产品计量根因。产品计量问题只保留 SA-O02 的独立线上证据。
9. **未知模型能力 fail closed：**`41b892ff` 已有意改为 runtime-config fallback，这是设计决策变化，不再作为当前 bug。
10. **显式总结会话、admin requeue、完整 dashboard：**属于产品或运维 backlog，未有回归证据时不包装成线上故障。

## 12. 建议讨论和修复顺序

下面是依赖顺序，不表示要合并成一个大改动；每一刀都应先加回归测试并独立验收。本轮不展开部署运维工作。

1. **先统一 Runtime 执行真值：SA-R04 + SA-R01 + SA-R02。**建立读取完整 `StateSnapshot` 的 classifier；由同一 `AgentRunCommand` 从输入接受持续推进到合法 waiting/terminal；删除 RuntimeProjector 和 `projected_*`，不增加 projection table 或 `agent_run_execution_jobs`。
2. **再解耦产品同步。**ChatMessage/Delivery、Session、Task、Trigger、Heartbeat、Onboarding、A2A、Planning 和 lane 使用目标表天然幂等边界独立补齐；任何失败都不能重跑 Graph 或把 Command 改回 pending。第一阶段不增加通用 `agent_run_effects`。
3. **再同时解除长任务两道门：SA-R03 + SA-C01。**Step budget 和 Compact 防风暴可以分两个 PR，但两者都完成前不要重跑完整长任务：只修步数会把压缩风暴延长，只修压缩仍会在第 50 步终止。
4. **统一 Tool Schema 与事实：SA-T01—T04。**先建立 canonical builtin definitions，再扩展既有 `ToolExecutionOutcome`，完成 NUL/大结果、Result Store、脱敏与 ledger 字段；各工具族随后逐步接入，不能先删旧 Prompt 手册再补契约。
5. **修完成可信度：SA-V01。**只在可信 ledger 之上接入确定性 Artifact/Evidence 验证和两次 repair；本版不做 Task Contract、LLM 语义 Verifier或第二模型。
6. **收缩模型干扰：SA-T03 + SA-P01。**让 Prompt 与当前有效工具集同源，再按 D-019 删除重复手册、错误能力和重复输入；本版不做 Tool Search 或语义工作集。
7. **补 Compact 韧性：SA-C02 + SA-C03。**加入迟滞/增长量、错误退避、超大块降级、摘要上界和可诊断日志，同时保留工具对原子性。
8. **补渐进能力：SA-S01。**沿用 `read_file` 建立 Catalog、真实大小写路径和当前工具可用性的闭环；本版本不新增专用 loader、Tool Search 或自动路由器。
9. **补技术查询与恢复：SA-U01 + SA-U02。**先实现精确 RunStateReader/RunView 和 Session runtime-state，再保证刷新、重连后仍可精确 resume / abort；完整详情页和 Dashboard 延后。
10. **最后完善运营质量：SA-O01 + SA-O02 + SA-N01。**补 Provider ledger/spans、计量、retention、dashboard、缓存和日志降噪。

具体文件影响和全入口回归矩阵以 ADR D-023 为准。D-GATE-01 的证据保留，但部署运维不属于本轮实施序列。

## 13. 决策进度与后续边界

### 13.1 已确认基线（实施状态见第 0 节及 ADR D-020）

1. checkpoint 是唯一执行真值；统一 classifier 同时检查 `next / tasks / interrupts / lifecycle`。
2. 保留 OSS LangGraph、PostgreSQL Checkpointer、interrupt/resume 与 graph version；当前阶段不迁移 Agent Server。
3. 保留 Clawith 自研 Agent harness，不使用 LangChain `create_agent` 或 Deep Agents 替换生产 Agent loop；后续在 harness 内形成明确的 model/context/tool/completion/compact/verification policy 边界。
4. `AgentRunCommand` 本身承担 start/resume/cancel 的 durable invocation、崩溃恢复和合法 waiting/terminal 收口；不增加 `agent_run_execution_jobs`。同一 row 必须区分“输入已进入 checkpoint”和“invocation 已到稳定边界”。
5. 删除 `RuntimeProjector`、`agent_runs.projected_*`、projection watermark 与 history replay；不增加新投影表。Run 查询由后端 `RunStateReader` 按目标 Run 最新 applied Command 的 `applied_checkpoint_id` 精确读取完整 `StateSnapshot`；未 settle Command 才按 namespaced metadata 定位。
6. `agent_run_events` 第一阶段只保留稳定产品边界、重连 cursor 和必要 delivery receipt，不再镜像 Graph lifecycle，也不参与执行判断。
7. 不新增通用 `agent_run_effects`；Session、Task、Trigger、Heartbeat、Onboarding、A2A、Planning 和 lane 使用目标表天然幂等状态，由 Reconciler 补齐；Provider 继续使用 `channel_deliveries`。
8. 产品同步失败不能重跑 Graph、重复提交 Command、重复有副作用工具或改写 Graph 终态。
9. 共享 Runtime 修改同时覆盖 Chat、Task、Trigger、Heartbeat、A2A、Onboarding、Group Planning、Scheduling lane 和 Session Context 的回归，但不改变各入口的触发条件与来源业务语义。
10. `Agent.max_tool_rounds` 保留现有字段名，但语义固定为 Run 级模型决策轮次硬上限；`start_run()` 计算后固化到 `agent_runs.model_turn_limit`，每次 invocation 通过 `RuntimeContext` 传入，具体计数、resume、oneshot 收紧、达限终止和无 Runtime 隐藏 hard cap 规则以 ADR D-008 为准。
11. 本轮所有 schema 变更在讨论完成后统一对照 `main` 的最新 schema head 制作一次迁移，不按单个议题拆成多个迁移脚本；业务代码仍可按依赖拆成小而可审查的改动。
12. Direct Chat Compact 统一为 Thread Running Summary；预算、水位线、recent suffix、Tool Exchange 与失败语义均已按 D-015 / D-016 确认，不再保留 Run/Session 两套压缩器或自建 Compact 重试状态机。
13. Prompt/Context 按 D-017—D-019 实施：Name + Soul、Base Prompt V1、当前输入/指令单一位置、Gemini dynamic parity、canonical Tool Schema、Skill 与所有特殊入口同步修改。
14. Tool 按 D-020 实施：保留现有 ledger 并扩展 `ToolExecutionOutcome`，started/unknown fail-closed，顺序执行，8 KiB inline + 私有 Result Store，参数/日志脱敏和 model-step 有效工具集。
15. Finish/Verifier 按 D-021 实施：本版只做确定性下限和最多两次 repair，不做 LLM Task Contract、第二模型或默认语义裁判。
16. RunView 与 Web Wait/Resume 按 D-022 实施：按目标 Run/Command checkpoint 精确读取，服务端持久状态恢复 active Run，resume/cancel 显式携带 identity，缺失或歧义 fail closed。
17. Onboarding 的 22 个 bootstrap 文件和 4 个内联正文统一退役；模板识别改用 `template_id`，督办未接通死路径不新增 Prompt profile，未来恢复时走 Task/Trigger Runtime。

### 13.2 不纳入当前统一实现的后续议题

当前批次没有剩余的 Prompt、Tool、确定性 Verifier、RunView 或 Wait/Resume 架构选择；统一迁移和主线实现已经落地，当前只做最终全入口回归、文档核对与明确后置项收口，不再边写代码边临时重新设计。

后续优化只保留：Tool Search/语义工作集/并行 read tools、模型专属 Prompt、Task-specific 语义评估、ModelGateway、通用 Retry/Observability 和完整产品 Run 页面。部署运维问题保留原证据，但按当前边界先不处理。
