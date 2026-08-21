# Implementation Plan: Tool Runtime 契约与执行链路修复

**Branch**: `002-tool-runtime-contract` | **Date**: 2026-08-10 | **Spec**: [spec.md](./spec.md)
**Input**: Feature specification from `/specs/002-tool-runtime-contract/spec.md`

## Summary

在现有 Durable Runtime、`AgentToolExecution` Receipt、safe-read replay 和 unknown/reconcile 机制之上，增加一次 Model Step 固化、checkpoint 可恢复的 `StepToolContext`。新 Tool Step 只使用已接受的 Tool Contract/Execution Binding，不再调用 ToolProvider 重建 Workset；同时把 Provider Call ID、Runtime Call Instance 和 Execution Receipt 分离，统一 schema validation、authorization/approval、模型可见失败反馈，并将现有独立 Tool repair/retry 上限统一为 10。操作 deadline、取消传播和 Receipt lease 继续保持三个独立控制面。长期通过可渐进迁移的 RegisteredTool 收敛模型定义与执行能力，不一次性替换现有 Handler。

## Technical Context

**Language/Version**: Python 3.11+
**Primary Dependencies**: FastAPI, SQLAlchemy 2.x async ORM, PostgreSQL, LangGraph checkpoint, Pydantic, httpx
**Storage**: PostgreSQL `agent_tool_executions` + LangGraph PostgreSQL checkpoint；不新增第二套 Run 生命周期状态机
**Testing**: pytest, pytest-asyncio, Ruff, Alembic heads/upgrade/downgrade, `scripts/arch-guard.sh`
**Target Platform**: Linux backend workers and API processes
**Project Type**: Multi-tenant web-service backend；本功能无必需前端改动
**Performance Goals**: 新格式每个 Model Step 最多一次 ToolProvider 查询；Tool Step 为 0 次；checkpoint replay 不增加 ToolProvider 查询或副作用次数
**Constraints**: 保持旧 checkpoint 可恢复；不新增依赖；所有查询 tenant-scoped；unknown write 禁止自动重放；不弱化 Receipt fence
**Scale/Scope**: Agent Runtime 核心链路、一个兼容型 Alembic 迁移、定向 Runtime/Tool tests；长期 Registry 只建立接口和迁移门槛，不在首轮搬迁全部工具

## Constitution Check

*GATE: Phase 0 前与 Phase 1 后均通过。*

- **C1 Runtime Boundary Isolation — PASS**：`StepToolContext` 和 repair episode 属于 LangGraph checkpoint 的执行生命周期；`AgentToolExecution` 继续只保存 Receipt/结果事实，API 和产品投影不推进 Runtime 状态。
- **C2 Strict Multi-Tenant Scope — PASS**：所有 execution/authorization/binding lookup 必须携带 `tenant_id`；binding 不能成为跨 tenant 的可执行引用。
- **C3 Idempotent Side Effects — PASS**：保留 `AgentToolExecution.id`、lease owner/fence、unknown/reconcile 和 safe-read bounded retry；Call Instance 只增强身份，不绕过 Receipt。
- **C4 Client/Gateway Wrapper — PASS**：Provider/Tool 调用仍经统一 Runtime/Tool executor；不引入直接外部访问旁路。
- **C5 Database/Performance — PASS**：迁移不新增物理外键；使用单列/组合索引，不在 Tool loop 内引入 N+1；新 Tool Step 删除一次 Workset 查询。
- **C6 Modularity/Reusability — PASS**：新增 contract/validation/repair 小模块，避免继续扩张已超过建议尺寸的 `tool_step_service.py` 和 `agent_tools.py`。

## Delivery Phases

### Phase A — Stable Step Tool Context and identity

1. 定义 `StepToolContext`、`ToolWorksetEntry`、`AcceptedToolCall` 的 checkpoint JSON contract。
2. Model Step 在 Provider 调用前构建 Workset，在接受 Tool Call 时生成稳定 `call_instance_id` 并保留 `provider_call_id`。
3. Tool Step 校验 context 与 Assistant Turn 一致，只从保存 binding 执行；新 checkpoint 路径禁止调用 ToolProvider。
4. 旧 checkpoint 进入单次、可观测的 legacy resolver；同一 pending batch 只解析一次。
5. `AgentToolExecution` 增加 nullable `provider_call_id` 和 `contract_version`；现有 `tool_call_id` 语义收敛为 Call Instance，`id` 继续是 Execution/Receipt ID。

### Phase B — Shared validation, authorization and failure feedback

1. 在 Receipt reservation 前按已接受 schema 统一校验 object/required/type/enum/additional properties。
2. 将 actor/tenant/resource/credential/approval 检查收敛为不可绕过的 authorization decision。
3. 所有具备有效 Call Instance 的可修复失败生成一个 call-linked Tool Result，包含稳定 code、bounded summary、model action、side-effect state 和安全 remediation。
4. Permission/confirmation、pending、cancel、unknown 和 protocol corruption 继续使用独立控制状态。

### Phase C — Repair budgets

1. checkpoint 保存 per-tool repair episode、连续 fingerprint 计数和总计数。
2. 第 10 次连续相同失败或第 10 次同 Tool episode 失败后暂停，且不发起下一次模型调用；普通 Tool JSON repair、`write_file` JSON repair 和 safe-read replay 也只把现有独立上限改为 10，不在本轮重构计数结构。
3. Tool 成功、新 Run、用户明确纠正按 contract 重置；Provider retry、safe internal replay、permission/confirmation、pending、cancel、unknown 不计数。
4. Verifier repair 改为当前 issue episode 计数，保留全局 `model_turn_limit` 独立语义。

### Phase D — Deadlines, cancellation and lease hardening

1. 为 IMAP、DNS、AgentBay read/code 和本地 code 定义 operation-specific deadline 优先级。
2. 将 durable cancel 传播到支持的进程/网络/SDK；无法 hard-cancel 的调用停止等待并记录能力限制。
3. 长任务在 ownership 有效时 renew lease；settlement 前执行 fence；deadline/cancel 后不确定写转 unknown/reconcile。

### Phase E — RegisteredTool migration boundary

建立不影响现有工具的 `RegisteredTool` contract，要求模型 schema、handler binding、effect/retry、authorization、recovery、deadline/cancel capability 完整后才进入 Workset。首轮只迁移代表性 builtin、MCP 和 AgentBay read；其余 legacy adapter 保持隐藏或走兼容层。

## Project Structure

### Documentation

```text
specs/002-tool-runtime-contract/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   ├── step-tool-context.md
│   ├── tool-result.md
│   └── repair-and-lifecycle.md
└── tasks.md
```

### Source Code

```text
backend/
├── app/models/agent_tool_execution.py
├── app/services/agent_runtime/
│   ├── state.py
│   ├── model_step_service.py
│   ├── tool_step_service.py
│   ├── tool_execution.py
│   ├── tool_contracts.py          # new: checkpoint-safe contracts/bindings
│   ├── tool_validation.py         # new: accepted-schema validation
│   ├── tool_authorization.py      # new: shared decision envelope
│   ├── tool_repair_budget.py      # new: episode transitions
│   └── cancel_source.py
├── app/services/agent_tools.py
├── alembic/versions/
└── tests/
    ├── test_agent_runtime_tool_contracts.py
    ├── test_agent_runtime_tool_step_service.py
    ├── test_agent_runtime_tool_execution.py
    ├── test_agent_runtime_tool_repair_budget.py
    └── test_agent_tools_deadlines.py
```

**Structure Decision**: 只扩展现有 backend Runtime 边界。checkpoint contract、validation、authorization 和 repair budget 拆为小模块；Receipt persistence 继续由现有 model/service 所有，不增加平行 Runtime。

## Migration and Compatibility Strategy

- Alembic 采用 add-only、nullable staged migration；`provider_call_id` 和 `contract_version` 不参与首期唯一键。
- 现有 `(run_id, tool_call_id)` 唯一键保留；新代码把 `tool_call_id` 当作 Call Instance，旧行保持合法。
- 旧 checkpoint 无 `step_tool_context` 时，Tool Step 仅为整个 pending batch 调用一次 legacy resolver，并记录 `legacy_tool_context_resolved`；新 checkpoint 缺 context 直接视为 corruption。
- mixed-version Worker 期间，新字段写入必须向旧 Reader 兼容；删除 legacy path 需要完整保留周期、回滚窗口和使用量为零。

## Verification Strategy

1. Contract unit tests：序列化、版本、Call identity、schema validation、failure redaction、repair transitions。
2. Runtime integration tests：Model Step → checkpoint → 新 Worker Tool Step；普通 availability 变化不影响已接受 Call；安全状态变化仍阻断。
3. Receipt tests：replay 复用同一 execution；lease renewal/loss/fence；unknown write no replay；safe read bounded retry。
4. Compatibility tests：旧 checkpoint 单次 resolver、新 checkpoint 禁止 resolver、mixed-version nullable fields。
5. Lifecycle tests：统一上限 10 的 off-by-one、reset/exclusion、operation deadline、cancel propagation。
6. Static gates：scoped Ruff、pytest、Alembic single head + upgrade/downgrade、`scripts/arch-guard.sh`。

## Complexity Tracking

无 Constitution 违规。长期 Registry 和 deadline/cancel 能力表放在后续 phase，避免首个安全修复同时搬迁全部工具。
