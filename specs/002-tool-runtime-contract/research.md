# Phase 0 Research: Tool Runtime 契约与执行链路

## Baseline

研究基线为 `upstream/main@251aeba8`。个人 fork 的 `origin/main@5aef9da4` 停留在 v1.10.1，不包含 Durable Runtime，因此本功能分支已无损快进到仓库上游主线。

当前已具备：LangGraph checkpoint、`AgentToolExecution` Receipt、`started/succeeded/failed/unknown`、safe-read bounded retry、lease owner/fence、async poll、unknown/reconcile、typed `ToolExecutionOutcome`、Provider transport retry 与全局 model turn limit。

当前缺口：Tool Step 再次调用 ToolProvider；checkpoint 无 Workset/Contract/Binding；Provider Call ID 直接作为 Runtime `tool_call_id`；参数只验证为 JSON object；failure envelope 和 repair episode 不完整；部分 IMAP/DNS/AgentBay 路径缺 deadline/cancel。

## Decisions

### D1. Workset 在 Model Step 固化，Tool Step 不重建

**Decision**: Model Step 构建一次 Workset，并把已接受 Call 所需的最小 Tool Contract/Execution Binding 存入 checkpoint。

**Rationale**: `tool_step_service.py` 当前在执行时再次调用 `get_runtime_agent_tools_for_llm`，会让 assignment/enabled/readiness 的普通变化改变已经接受的调用。稳定 binding 可以跨 Worker 恢复，同时保留当前 actor/resource/credential/cancel 安全检查。

**Rejected**: 在 Tool Step 再调用 Provider 并比较两次结果。比较仍无法证明旧 endpoint/target，且会把普通 availability 当 hard revoke。

### D2. 保留现有 `tool_call_id` 为 Call Instance，另存 Provider ID

**Decision**: 现有 `tool_call_id` 从 wire identity 收敛为 Clawith Call Instance；新增 nullable `provider_call_id`；`AgentToolExecution.id` 继续是 Execution/Receipt ID。

**Rationale**: 当前所有 Receipt、Activity、Chat、A2A 和 async poll 已围绕 `(run_id, tool_call_id)` 稳定工作。替换主键风险大，新增 Provider correlation 可兼容旧数据并允许不同 Assistant Turn 重复 Provider-local ID。

**Rejected**: 让 Provider ID 继续承担持久身份。Gemini/兼容 Provider 可能合成、缺失或重复 ID，跨 replay 不稳定。

### D3. Binding 保存引用和不可变目标，不保存秘密或可执行代码

**Decision**: Binding 保存 tool kind、registry key、handler key、MCP server/tool target、contract version 和 credential reference；执行时再读取当前 credential 并做安全授权。

**Rationale**: 既防止 endpoint/name 漂移，也允许 credential rotation/revocation 立即生效，不把秘密写入 checkpoint。

**Rejected**: checkpoint 保存解密 credential 或 Python callable。安全风险高且不具备跨版本可恢复性。

### D4. schema validation 位于 Receipt reservation 前

**Decision**: 使用 accepted schema 做通用结构校验，失败产生一个 Tool Result，但不创建执行 Receipt。

**Rationale**: 无效参数没有执行资格，不应消耗 provider attempt；同时必须返回模型可修复的、call-linked 反馈。

**Rejected**: 只依赖各 Handler 自行校验。错误格式不一致、授权/副作用前后顺序不可证明。

### D5. authorization 是统一 decision envelope，资源级检查可留在 adapter

**Decision**: 所有工具经过同一 authorization/approval orchestration；只有必须读取真实对象的资源级判定可由 adapter 执行，但必须返回统一结果。

**Rationale**: 不强迫所有 provider 使用同一权限实现，同时保证结果语义和 Receipt 前门不可绕过。

### D6. 可修复失败返回模型，控制状态不伪装失败

**Decision**: 参数、binding 和确定性业务失败生成一个 sanitized Tool Result。Permission/confirmation、pending、cancel、unknown 和 checkpoint corruption 保持独立状态。

**Rationale**: 模型需要知道“为什么失败”和“可采取什么动作”，但 unknown write 绝不能诱导自动重试。

### D7. Repair budget 是 Tool episode，不是 Provider/Receipt retry

**Decision**: 连续同 fingerprint 第 10 次、同 Tool episode 第 10 次暂停；只计模型可见、可修复失败。普通 Tool protocol repair、`write_file` protocol repair 和 safe-read replay 继续使用各自现有计数入口，但上限统一为 10，状态结构后续再整体重构。

**Rationale**: Provider transport retry 和 Receipt safe replay 都不代表模型做了错误决策；混计会过早停机或掩盖循环。

### D8. Deadline、cancel、lease 是三个控制面

**Decision**: 每个 operation 有 deadline；Run cancel 尽量传播到底层；lease 只证明 ownership，并通过 renew/fence 保护结算。

**Rationale**: lease 过期不等于 Handler timeout，timeout 也不证明外部写未发生。

### D9. Registry 渐进迁移

**Decision**: 先定义完整 RegisteredTool contract 和代表性 adapter，未完整声明能力的工具不进入新 Workset；不一次性搬迁 `agent_tools.py` 全部 Handler。

**Rationale**: 先消除执行漂移和失败盲区，再逐 family 收敛，降低回归面。

## Source Evidence

- `model_step_service.py` 在每个模型轮构建 Runtime Workset并校验名称。
- `tool_step_service.py` 在执行 pending calls 时再次调用 ToolProvider，是本次最直接的漂移来源。
- `AgentToolExecution` 已提供 durable receipt、attempt、lease 和 unknown 状态，应该扩展而不是替换。
- `tool_execution.py` 已提供 exact request comparison、safe-read retry、lease renewal/fence 和 reconciliation。
- `node_executor.py` 现有 repair 主要按 protocol code 与 verifier 累计，不等于新的 Tool repair episode。

## Open Risks Resolved by Tests

- Provider fallback 使用不同 capability Workset：最终接受 Call 必须绑定实际调用的 fallback Workset。
- 同一 response 多 Call：每个 Call 独立 identity/binding，batch context 共享 Workset version。
- Group/legacy hidden tools：只允许明确 compatibility path，不能让新模型轮重新暴露。
- mixed Worker：nullable DB 字段和 checkpoint version discriminator 保证旧 Reader 不崩溃。
- write 后断链：任何无法证明 outcome 的路径统一 unknown，不因 retryable 标记自动重放。
