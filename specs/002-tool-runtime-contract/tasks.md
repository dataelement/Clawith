# Tasks: Tool Runtime 契约与执行链路修复

**Input**: Design documents from `/specs/002-tool-runtime-contract/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/, quickstart.md
**Tests**: 规范明确要求 Runtime contract、replay、repair budget、deadline/cancel/lease 与兼容测试；各故事按 test-first 执行。

## Phase 1: Setup

**Purpose**: 固定基线、测试入口和迁移拓扑。

- [x] T001 记录 `upstream/main` 基线、branch 和 dirty state 到 `specs/002-tool-runtime-contract/quickstart.md`
- [x] T002 [P] 核对并记录现有 Tool Runtime 定向测试清单到 `specs/002-tool-runtime-contract/quickstart.md`
- [x] T003 [P] 运行 Alembic single-head 与 `scripts/arch-guard.sh` 基线检查并记录结果到 `specs/002-tool-runtime-contract/quickstart.md`

---

## Phase 2: Foundational Contracts

**Purpose**: 建立所有故事共享的 checkpoint-safe contract 与兼容边界。

**⚠️ CRITICAL**: 本阶段完成前不修改 Model/Tool 执行主链。

- [x] T004 [P] 为 `StepToolContext`、`ToolWorksetEntry`、`AcceptedToolCall` 编写失败态 contract tests 于 `backend/tests/test_agent_runtime_tool_contracts.py`
- [x] T005 [P] 为三层身份与 legacy JSON compatibility 编写失败态 tests 于 `backend/tests/test_agent_runtime_tool_contracts.py`
- [x] T006 实现 versioned、bounded、secret-free Tool contracts 于 `backend/app/services/agent_runtime/tool_contracts.py`
- [x] T007 将 `step_tool_context` 与 `tool_repair_episodes` 类型接入 `backend/app/services/agent_runtime/state.py`
- [x] T008 在 `backend/tests/test_agent_runtime_contracts.py` 增加 checkpoint 序列化/旧 state 兼容测试

**Checkpoint**: Contract 可独立序列化，旧 checkpoint 仍可读取。

---

## Phase 3: User Story 1 — 已接受的 Tool Call 稳定执行 (Priority: P1) 🎯 MVP

**Goal**: Model Step 固化 Workset/Binding；新 Tool Step 不再重建 Workset。

**Independent Test**: 接受 Tool Call 后修改 assignment/enabled/readiness 并换 Worker 恢复，当前 Call 仍使用原 binding；actor/resource/credential/cancel 变化仍阻断。

### Tests

- [x] T009 [P] [US1] 在 `backend/tests/test_agent_runtime_model_step_service.py` 增加实际 primary/fallback Workset 固化与 stable Call Instance 测试
- [x] T010 [P] [US1] 在 `backend/tests/test_agent_runtime_tool_step_service.py` 增加新 checkpoint ToolProvider 调用为 0、availability 漂移不影响当前 Call 测试
- [x] T011 [P] [US1] 在 `backend/tests/test_agent_runtime_tool_step_service.py` 增加 context mismatch/corruption 在 Receipt 前失败测试
- [x] T012 [P] [US1] 在 `backend/tests/test_agent_runtime_tool_step_service.py` 增加 actor/resource/credential/cancel 仍按当前状态阻断测试

### Implementation

- [x] T013 [US1] 在 `backend/app/services/agent_runtime/model_step_service.py` 生成实际 Provider Workset 的 `StepToolContext` 与稳定 Call Instance
- [x] T014 [US1] 在 `backend/app/services/agent_runtime/node_executor.py` 原子写入 assistant message、pending calls 与 `step_tool_context`
- [x] T015 [US1] 在 `backend/app/services/agent_runtime/tool_step_service.py` 从 context 解析 accepted schema/policy/binding 并移除新格式 ToolProvider 查询
- [x] T016 [US1] 在 `backend/app/services/agent_runtime/tool_step_service.py` 实现整批一次的 legacy resolver 与可观测 compatibility marker
- [x] T017 [US1] 在 `backend/app/services/agent_runtime/tool_step_service.py` 保留当前安全 authorization/cancel gate 并阻止 context corruption fallback

**Checkpoint**: US1 测试独立通过，SC-001 达成。

---

## Phase 4: User Story 2 — Tool 失败能够驱动模型修正 (Priority: P1)

**Goal**: 在 Receipt 前统一 schema validation；有效 Call 的可修复失败产生一个 sanitized Tool Result。

**Independent Test**: 缺 required 字段、类型错误、binding unavailable 和确定性业务拒绝分别返回 call-linked failure envelope；secret/stack/provider body 不可见。

### Tests

- [x] T018 [P] [US2] 在 `backend/tests/test_agent_runtime_tool_validation.py` 增加 object/required/type/enum/additionalProperties contract tests
- [x] T019 [P] [US2] 在 `backend/tests/test_agent_runtime_tool_step_service.py` 增加 schema failure 不创建 Receipt且恰好一个 Tool Result 测试
- [x] T020 [P] [US2] 在 `backend/tests/test_agent_runtime_tool_outcome_contract.py` 增加 model_action/side_effect_state/remediation 与 redaction 测试
- [x] T021 [P] [US2] 在 `backend/tests/test_agent_runtime_tool_step_service.py` 增加 permission/confirmation/pending/cancel/unknown 不伪装普通失败测试

### Implementation

- [x] T022 [US2] 实现 accepted-schema validator 于 `backend/app/services/agent_runtime/tool_validation.py`
- [x] T023 [US2] 扩展 bounded `ToolExecutionOutcome`/Tool Result envelope 于 `backend/app/services/agent_runtime/tool_execution.py`
- [x] T024 [US2] 在 `backend/app/services/agent_runtime/tool_step_service.py` 接入 Receipt 前 validation 和 exactly-once failure message
- [x] T025 [US2] 在 `backend/app/services/agent_runtime/checkpoint_side_effects.py` 投影 execution/call/provider identity 与 sanitized failure metadata

**Checkpoint**: US2 测试独立通过，SC-004 达成。

---

## Phase 5: User Story 3 — 调用身份与 Receipt 不冲突 (Priority: P1)

**Goal**: Provider Call、Call Instance、Execution Receipt 和业务幂等身份职责分离。

**Independent Test**: 不同 Assistant Turn 使用相同 Provider-local ID 时，产生两个 Call Instance/Receipt；同一 Call replay 只复用原 execution。

### Tests

- [x] T026 [P] [US3] 在 `backend/tests/test_agent_runtime_tool_execution.py` 增加 provider ID 重复与 Call Instance 唯一性测试
- [x] T027 [P] [US3] 在 `backend/tests/test_agent_runtime_tool_step_service.py` 增加 checkpoint replay 复用 execution 且不重复副作用测试
- [x] T028 [P] [US3] 在 `backend/tests/test_agent_runtime_checkpoint_side_effects.py` 增加 Activity/Chat/A2A identity projection 测试
- [x] T029 [P] [US3] 在 `backend/tests/test_agent_runtime_tool_execution_migration.py` 增加 nullable 新字段 upgrade/downgrade 与旧行兼容测试

### Implementation

- [x] T030 [US3] 扩展 `provider_call_id` 与 `contract_version` 字段于 `backend/app/models/agent_tool_execution.py`
- [x] T031 [US3] 创建 single-head、DDL-only、可回滚 migration 于 `backend/alembic/versions/`
- [x] T032 [US3] 在 `backend/app/services/agent_runtime/tool_execution.py` reservation/exact request/outcome 中保存并校验 Provider correlation 和 contract version
- [x] T033 [US3] 在 `backend/app/services/agent_runtime/model_step_service.py`、`backend/app/services/agent_runtime/tool_step_service.py` 保留 Provider wire pairing 并内部使用 Call Instance
- [x] T034 [US3] 在 `backend/app/services/agent_runtime/async_tool_poll.py`、`backend/app/services/agent_runtime/a2a_runtime.py` 和 `backend/app/services/agent_runtime/checkpoint_side_effects.py` 透传三层 identity

**Checkpoint**: US3 测试独立通过，SC-002/SC-003 达成。

---

## Phase 6: User Story 4 — 修复次数按问题边界计算 (Priority: P2)

**Goal**: 实现连续同错 10、同 Tool episode 10，并将现有独立 Tool repair/retry 上限统一为 10；本轮不重构计数结构。

**Independent Test**: 统一上限 10 的边界、fingerprint 变化、Tool success、新 Run、用户纠正、无关 Tool success及所有 exclusion 均按 contract 转移。

### Tests

- [x] T035 [P] [US4] 在 `backend/tests/test_agent_runtime_tool_repair_budget.py` 增加统一上限 10 的 off-by-one 与 fingerprint 测试
- [x] T036 [P] [US4] 在 `backend/tests/test_agent_runtime_tool_repair_budget.py` 增加 success/new Run/user correction/reset scope 测试
- [x] T037 [P] [US4] 在 `backend/tests/test_agent_runtime_tool_repair_budget.py` 增加 Provider retry/safe replay/approval/pending/cancel/unknown exclusion 测试
- [x] T038 [P] [US4] 在 `backend/tests/test_agent_runtime_node_executor.py` 增加暂停发生在下一次 Model 调用之前的集成测试
- [x] T039 [P] [US4] 在 `backend/tests/test_agent_runtime_node_executor.py` 增加 Verifier issue episode 与全局 model turn limit 独立测试

### Implementation

- [x] T040 [US4] 实现纯函数 repair episode transitions 于 `backend/app/services/agent_runtime/tool_repair_budget.py`
- [x] T041 [US4] 在 `backend/app/services/agent_runtime/node_executor.py` 对 Tool Result 应用 episode、暂停与 stop reason
- [x] T042 [US4] 在 `backend/app/services/agent_runtime/node_executor.py` 将 verifier aggregate count 迁为 issue fingerprint episode
- [x] T043 [US4] 在 `backend/app/services/agent_runtime/model_step_service.py` 标识 explicit user correction reset 边界并记录 telemetry

**Checkpoint**: US4 测试独立通过，SC-005/SC-006 达成。

---

## Phase 7: User Story 5 — 长时间 Tool 可控且不会被错误重放 (Priority: P2)

**Goal**: operation deadline、durable cancel 和 Receipt lease 独立且可验证。

**Independent Test**: IMAP、DNS、AgentBay read/code、本地 code 在策略时限内结束；取消尽可能传播；lease loss 阻止旧 owner；不确定写不重放。

### Tests

- [x] T044 [P] [US5] 在 `backend/tests/test_agent_tools_deadlines.py` 增加 IMAP/DNS/AgentBay read/code deadline 优先级测试
- [x] T045 [P] [US5] 在 `backend/tests/test_agent_runtime_cancel_source.py` 增加 cancel token 传播与不支持 hard-cancel telemetry 测试
- [x] T046 [P] [US5] 在 `backend/tests/test_agent_runtime_tool_execution.py` 增加 lease renew/loss/fence 和 stale settlement 测试
- [x] T047 [P] [US5] 在 `backend/tests/test_agent_runtime_tool_outcome_contract.py` 增加 deadline/cancel 后 possible write → unknown/no replay 测试

### Implementation

- [x] T048 [US5] 定义 deadline/cancel capability policy 于 `backend/app/services/agent_runtime/tool_contracts.py`
- [x] T049 [US5] 在 `backend/app/services/agent_tools.py` 为 IMAP、DNS、本地 code 接入 bounded deadline 和正确 outcome classification
- [x] T050 [US5] 在 `backend/app/services/agentbay_client.py` 与 `backend/app/services/agent_tools.py` 实际执行 AgentBay read/code timeout
- [x] T051 [US5] 在 `backend/app/services/agent_runtime/tool_step_service.py` 运行长任务 lease renewal 并在 settlement 前 fence
- [x] T052 [US5] 在 `backend/app/services/agent_runtime/cancel_source.py` 和 Tool adapter 接口传播 cancel token/capability telemetry

**Checkpoint**: US5 测试独立通过，SC-007/SC-008 达成。

---

## Phase 8: User Story 6 — 旧 Run 兼容与长期 Tool 注册迁移 (Priority: P3)

**Goal**: 旧 checkpoint 可观测恢复；新 RegisteredTool 不完整时不可暴露。

**Independent Test**: legacy fixtures 恢复且每 batch 只解析一次；新 checkpoint 永不 fallback；不完整 Registry entry 被拒绝；代表性 builtin/MCP/AgentBay read 可执行。

### Tests

- [x] T053 [P] [US6] 在 `backend/tests/test_agent_runtime_tool_step_service.py` 增加 legacy batch resolver、telemetry 与 deletion gate fixtures
- [x] T054 [P] [US6] 在 `backend/tests/test_builtin_tool_contracts.py` 增加 RegisteredTool completeness/hidden-by-default tests
- [x] T055 [P] [US6] 在 `backend/tests/test_agent_tools_legacy_contract_compatibility.py` 增加代表性 builtin/MCP/AgentBay adapter tests

### Implementation

- [x] T056 [US6] 定义 `RegisteredTool` completeness gate 与 lookup 于 `backend/app/services/agent_runtime/tool_registry.py`
- [x] T057 [US6] 将一个 builtin、一个 MCP 和一个 AgentBay read 注册到 `backend/app/services/agent_runtime/tool_registry.py`
- [x] T058 [US6] 在 `backend/app/services/agent_tools.py` 保留明确 legacy adapter 并隐藏不完整注册项
- [x] T059 [US6] 在 `backend/app/services/agent_runtime/tool_step_service.py` 增加 legacy usage metric/log 和删除条件

**Checkpoint**: US6 测试独立通过，SC-009 达成。

---

## Phase 9: Polish & Cross-Cutting Verification

- [x] T060 [P] 修复 `set_trigger` validation、`read_document` truncation 和 invisible Tool description 漂移于 `backend/app/services/agent_tools.py` 及对应 tests
- [x] T061 [P] 将 provider `parallel_tool_calls` capability 与业务并行执行能力分离于 `backend/app/services/llm/` 及对应 tests
- [x] T062 运行并修复 scoped Ruff 与 Tool Runtime pytest，命令记录到 `specs/002-tool-runtime-contract/quickstart.md`
- [x] T063 运行 Alembic heads、upgrade/downgrade 和 migration tests，结果记录到 `specs/002-tool-runtime-contract/quickstart.md`
- [x] T064 运行 `scripts/arch-guard.sh`、全量 `backend/tests/test_agent_runtime_*.py` 并记录剩余风险到 `specs/002-tool-runtime-contract/quickstart.md`
- [x] T065 核对所有 docs path、contract/version、legacy deletion gate 与 `git diff --check`，更新 `specs/002-tool-runtime-contract/quickstart.md`

---

## Dependencies & Execution Order

### Phase Dependencies

- Setup → Foundational Contracts → US1。
- US2 依赖 US1 的 accepted schema/binding。
- US3 依赖 US1 的 stable Call Instance，但其 DB migration/tests 可与 US2 tests 并行。
- US4 依赖 US2 的标准 failure envelope。
- US5 依赖 US1/US3 的 binding/Receipt identity，不依赖 US4。
- US6 依赖 US1 的 compatibility boundary，代表性 Registry 可在 US4/US5 后独立完成。
- Polish 依赖所选故事完成。

### User Story Completion Order

```text
US1 stable context
├── US2 validation/failure ──> US4 repair budget
├── US3 identity ────────────> US5 lifecycle hardening
└── US6 registry compatibility
```

### Parallel Opportunities

- 每个故事的 `[P]` tests 可在不同文件并行准备，但必须先失败再实现。
- US2 outcome contract tests 与 US3 migration tests 可并行。
- US4 pure transition module与 US5 operation-specific handler tests 可在 US3 完成后并行。
- T060/T061 彼此独立，最后统一回归。

## Parallel Example: User Story 1

```text
T009 Model Step context tests
T010 ToolProvider-zero and availability drift tests
T011 corruption tests
T012 live safety revocation tests
```

## Implementation Strategy

### MVP First

1. 完成 T001–T008 固定 contract。
2. 完成 T009–T017，交付稳定 Workset/Binding 的 US1。
3. 独立验证 SC-001 后再进入 failure/identity。

### Incremental Delivery

1. US1 消除已接受 Call 的 Workset 漂移。
2. US2/US3 补齐模型可修复反馈和身份兼容。
3. US4 落地统一上限 10 的修复次数，保留现有独立计数结构。
4. US5 加固长任务生命周期。
5. US6 建立长期 Registry 迁移边界。

## Notes

- 所有 Runtime 行为改动先写失败测试，再修改生产代码。
- `Tool Step ToolProvider calls = 0` 只适用于新 checkpoint；legacy batch 允许恰好一次。
- 不把 Receipt lease、operation deadline 或 durable cancel 合并成单一 timeout。
- 不把长期 Registry 扩展为一次性迁移全部工具。
