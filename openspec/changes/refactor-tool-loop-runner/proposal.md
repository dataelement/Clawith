## Why

The LLM tool-calling loop is duplicated across three execution paths with divergent feature sets: `backend/app/api/websocket.py` (full-featured, 244-898), `backend/app/services/task_executor.py` (minimal, hardcoded `range(50)`), and parts of `backend/app/services/heartbeat.py` / `scheduler.py`. Task executions (triggered runs) silently miss token accounting, per-day/month quotas, image stripping, vision injection, abort handling, dynamic `max_tool_rounds`, and the `_TOOLS_REQUIRING_ARGS` guard. This creates correctness gaps (quotas bypassed on trigger-fired work), cost risk, and blocks Step B/C/D of RFC-001 which all assume a single loop.

Step A of RFC-001 addresses this by extracting a single `ToolLoopRunner` that both WS chat and task execution call. This proposal scopes Step A only.

## What Changes

- Introduce `ToolLoopRunner` service in `backend/app/services/tool_loop_runner.py` encapsulating: per-round LLM call, tool dispatch, token accounting, quota enforcement, image payload stripping, vision tool injection, abort signal check, dynamic `max_tool_rounds`, and `_TOOLS_REQUIRING_ARGS` validation — all behind typed capability flags (`TokenAccounting`, `QuotaEnforcement`, `ImageStripping`, `VisionInjection`, `AbortListener`, `DynamicRounds`, `ArgGuard`).
- Refactor `websocket.py` chat loop to delegate to `ToolLoopRunner` with all flags enabled.
- Refactor `task_executor.py` to delegate to `ToolLoopRunner` with the same flag set (fixes the silent quota / vision / abort / rounds bypass).
- Deduplicate `_TOOLS_REQUIRING_ARGS` copies across `websocket.py`, `heartbeat.py`, `scheduler.py` to a single module-level constant in the runner.
- Add per-round `ChatMessage(role="tool_call")` persistence in the runner so both WS and executor paths record the loop incrementally (previously only WS did).
- Feature flag `TOOL_LOOP_V2` (env var, default `true` in dev, staged in prod) to allow rollback for 2 weeks.
- **BREAKING** (internal only): `task_executor.execute_task()` internal signature changes to accept a `ToolLoopRunner` instance. No external API / DB schema change.

Explicit non-goals (deferred to later RFC steps):
- No `RoundEvent` model (Step C).
- No `FocusItem.allowed_tools` / `phase` columns (Step B/D).
- No `Task.contract_json` (Step C).
- No phase FSM, no orchestrator contract hook, no rule-based short-circuit, no model routing.

## Capabilities

### New Capabilities
- `tool-loop-runner`: Unified iterative LLM tool-calling loop used by conversational (WebSocket) and triggered (task executor) agent runs, with feature-flagged capability set and per-round persistence.

### Modified Capabilities
<!-- None. No existing openspec/specs/ entries. -->

## Impact

**Affected code**:
- NEW: `backend/app/services/tool_loop_runner.py`
- MODIFIED: `backend/app/api/websocket.py` (chat loop body → delegate)
- MODIFIED: `backend/app/services/task_executor.py` (loop body → delegate; gains quota/vision/abort/dynamic-rounds/image-strip/arg-guard)
- MODIFIED: `backend/app/services/heartbeat.py` (drop local `_TOOLS_REQUIRING_ARGS` copy)
- MODIFIED: `backend/app/services/scheduler.py` (drop local `_TOOLS_REQUIRING_ARGS` copy)

**Config**:
- NEW env: `TOOL_LOOP_V2` (bool, default `true`). When `false`, both call sites fall back to legacy inline loops. Flag removed 2 weeks after production rollout.

**Dependencies / DB / APIs**:
- No new Python dependencies.
- No DB schema change (no Alembic migration).
- No public HTTP / WebSocket API change.
- No frontend change.

**Risks**:
- Task-executor runs that previously silently skipped quota checks may now hit quota errors on the first triggered run of busy agents. Mitigated by staged rollout via flag + pre-rollout quota audit.
- Incremental `ChatMessage` writes in executor path add write load. Measured in Shadow mode before enabling.

**Observability**:
- New metric `tool_loop.rounds_used` labeled by `caller={ws,executor}`.
- New metric `tool_loop.quota_rejected_total` labeled by `caller`.
- Existing `activity_logger` calls preserved.
