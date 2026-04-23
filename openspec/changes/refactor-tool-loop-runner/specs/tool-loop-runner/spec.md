## ADDED Requirements

### Requirement: Unified iterative LLM tool-calling loop

The system SHALL provide a single `ToolLoopRunner` service that executes the per-round LLM call + tool dispatch cycle on behalf of all agent run call sites (WebSocket chat and task executor).

#### Scenario: WebSocket chat delegates to runner

- **WHEN** a WebSocket chat message arrives and `TOOL_LOOP_V2=true`
- **THEN** the WS path constructs a `RunContext` with `caller="ws"` and invokes `ToolLoopRunner.run(...)` with the `FLAGS_WS_CHAT` preset

#### Scenario: Task executor delegates to runner

- **WHEN** a triggered task is executed and `TOOL_LOOP_V2=true`
- **THEN** `task_executor.py` constructs a `RunContext` with `caller="executor"` and invokes `ToolLoopRunner.run(...)` with the `FLAGS_TRIGGERED_TASK` preset

#### Scenario: Feature flag off falls back to legacy

- **WHEN** `TOOL_LOOP_V2=false`
- **THEN** call sites execute their pre-existing inline loops

### Requirement: Explicit capability flags

The runner SHALL accept a frozen `CapabilityFlags` dataclass with no default values on any field.

#### Scenario: Flags are explicit

- **WHEN** a caller constructs `CapabilityFlags` without specifying every field
- **THEN** Python raises `TypeError`

#### Scenario: WS and executor presets are equivalent by default

- **WHEN** test `test_presets_equivalent` compares `FLAGS_WS_CHAT` and `FLAGS_TRIGGERED_TASK`
- **THEN** every field is equal

### Requirement: Per-round persistence via caller-provided sink

When `persist_per_round=True`, the runner SHALL invoke a caller-provided `RoundLogSink.write_round(...)` at the end of each completed round.

#### Scenario: Executor caller uses TaskLogSink

- **GIVEN** an executor run with `FLAGS_TRIGGERED_TASK` and `RunContext(round_log_sink=TaskLogSink(task_id))`
- **WHEN** the run completes 4 rounds
- **THEN** 4 `TaskLog` rows are written for that `task_id`

#### Scenario: persist_per_round requires a sink

- **WHEN** `persist_per_round=True` and `RunContext.round_log_sink is None`
- **THEN** the runner raises `AssertionError` before any LLM call

### Requirement: Observability via AgentActivityLog

The runner SHALL write exactly one `AgentActivityLog(action_type="tool_loop_completed")` row per run with `detail_json` containing `caller`, `rounds_used`, `status`, `quota_rejected`, `arg_guard_rejections`, `duration_ms`.

#### Scenario: Completion row recorded

- **WHEN** a run completes with 7 rounds for a WS caller
- **THEN** one `AgentActivityLog` row exists with `action_type="tool_loop_completed"`
