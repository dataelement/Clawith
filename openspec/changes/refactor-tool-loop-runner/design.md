## Context

Three code paths currently run a near-identical LLM tool-calling loop:

| Path | File | Current capabilities |
|---|---|---|
| WS chat | `backend/app/api/websocket.py` (lines 137–156, 148–151, 175, 214–306, 333, 348–355, 391–407, 489, 631, 709–736, 817–838, 866–898, 987–998) | **Full** — token accounting, daily/monthly quota, image stripping, vision injection, abort signal, dynamic `max_tool_rounds`, `_TOOLS_REQUIRING_ARGS` guard, per-round `ChatMessage` persistence |
| Task executor | `backend/app/services/task_executor.py` (189, 199 `range(50)`, 299, 342–355, 388–396) | **Minimal** — hardcoded 50 rounds, no token/quota/vision/abort/image/arg-guard, terminal-only persistence |
| Heartbeat / scheduler dispatch | `backend/app/services/heartbeat.py:391`, `backend/app/services/scheduler.py:163` | Only carries a duplicated `_TOOLS_REQUIRING_ARGS` copy used for pre-dispatch argument validation |

Three independent copies of `_TOOLS_REQUIRING_ARGS` have already drifted in ordering and will continue to drift whenever a new arg-required tool ships. Triggered task runs silently bypass billing / quota / safety features that WS chat enforces, which is the single largest correctness risk in the current agent loop.

RFC-001 Step A mandates a unified `ToolLoopRunner`. Steps B/C/D (phase FSM, `RoundEvent`, orchestrator contract hook, model routing) all assume this extraction is done first and therefore **cannot** begin until Step A lands.

Stakeholders: backend agent team (owner), platform/billing (quota correctness), ops (cost observability), eng-leads approving RFC-001 rollout.

## Goals / Non-Goals

**Goals:**

- One package (`backend/app/services/tool_loop_runner/`) owns the per-round LLM call + tool dispatch + guard logic; adapters (abort source, round-log sinks, result translators) live in a sibling submodule.
- Capability set is explicit, flag-driven, and identical for WS and executor by default; any divergence is a deliberate flag value, not an accident of code duplication.
- Zero behavior change for WS chat at flag-on; executor gains the seven missing capabilities at flag-on.
- Single source of truth for `_TOOLS_REQUIRING_ARGS`; heartbeat and scheduler import from the runner module.
- Runnable under a kill-switch (`TOOL_LOOP_V2`) for 2 weeks so we can revert without a deploy.
- Observability: emit `tool_loop.rounds_used{caller}` and `tool_loop.quota_rejected_total{caller}` so staged rollout can be measured.

**Non-Goals:**

- No `RoundEvent` table / model — that is RFC-001 Step C. The runner will expose a per-round hook (`on_round_complete`) but will not persist events itself.
- No phase FSM, no `FocusItem.allowed_tools`, no `FocusItem.phase` — Step B/D; those require the separate "Focus materialization" migration that RFC-001 does not yet contain.
- No orchestrator contract-validation hook — Step D (will attach at `finalize_work_item_from_task`, out of scope here).
- No model routing (Haiku/Sonnet/Opus selection) — Step D.
- No rule-based short-circuit — Step D.
- No change to DB schema, public HTTP/WS API, or frontend.
- No consolidation of `activity_logger` semantics — preserved as-is on both sides.

## Decisions

### D1. Shape of `ToolLoopRunner`

Adopted: a single async entry point that accepts a typed capability bundle.

```python
# backend/app/services/tool_loop_runner.py
@dataclass(frozen=True)
class CapabilityFlags:
    track_token_budget: bool
    enforce_quota: bool
    strip_images: bool
    inject_vision: bool
    listen_abort: bool
    dynamic_max_rounds: bool
    tool_arg_guard: bool
    persist_per_round: bool

@dataclass
class RunContext:
    session: AsyncSession
    agent: Agent
    caller: Literal["ws", "executor"]
    abort_source: AbortSource | None
    round_log_sink: RoundLogSink | None
    max_rounds_override: int | None
    on_round_complete: Callable[[RoundOutcome], Awaitable[None]] | None

class ToolLoopRunner:
    async def run(self, ctx: RunContext, messages: list[Message], flags: CapabilityFlags) -> RunResult: ...
```

**Rationale**: flags are the explicit contract between call sites. Rejected alternatives:

- *Subclassing* (`WSRunner(ToolLoopRunner)`, `ExecutorRunner(ToolLoopRunner)`): re-introduces two code paths, defeats the goal.
- *Kwargs-with-defaults*: silent defaults are exactly how the current drift happened.
- *Strategy-pattern per capability*: 8 strategies × 2 callers = dispatch cost without test benefit for this size of code.

`CapabilityFlags` is `frozen=True` so a test can snapshot the WS flag set and assert executor uses the same.

### D2. Default flag presets

Two named presets live in the runner module:

- `FLAGS_WS_CHAT`: all eight `True`. Matches today's `websocket.py` behavior.
- `FLAGS_TRIGGERED_TASK`: all eight `True`. This is the executor upgrade — executor was previously running the equivalent of all seven `False` plus `persist_per_round=False`.

A deliberate choice: executor does **not** get a reduced preset. If a real need to downgrade any flag emerges (e.g. internal system tasks should skip quota), we introduce a third preset with documented justification — we do not let call sites hand-craft flags.

**Rejected**: "flags default to False and each call site opts in." This reproduces the current silent-bypass failure mode.

### D3. Feature flag `TOOL_LOOP_V2`

- Source: env var read once at process start into a module-level constant in `backend/app/config.py`.
- Default: `true` in dev/test; `true` in staging immediately; `false` → `true` in prod on a staged schedule (see Migration Plan).
- When `false`: both `websocket.py` and `task_executor.py` execute their legacy inline loops unchanged. The legacy code is kept behind `if not settings.tool_loop_v2:` branches and deleted in a follow-up PR 2 weeks after the flag defaults true in prod.
- **Rejected**: per-caller flags (`TOOL_LOOP_V2_WS`, `..._EXECUTOR`). Splitting the rollout per caller doubles test matrix and lets the two paths drift again during the rollout window. We accept a coarser rollback in exchange for a single behavior invariant.

### D4. `_TOOLS_REQUIRING_ARGS` deduplication

Owner: module-level constant `TOOLS_REQUIRING_ARGS: frozenset[str]` in `tool_loop_runner.py`. Public re-export.

- `heartbeat.py:391` and `scheduler.py:163` replace their local copies with `from app.services.tool_loop_runner import TOOLS_REQUIRING_ARGS`.
- A unit test enumerates all tools marked as arg-required in tool registry and asserts the constant equals that set, preventing future drift.

### D5. Per-round persistence — sink protocol

**Problem**: WS and executor persist rounds to different tables. WS writes `ChatMessage(conversation_id=f"web_{user_id}", role="tool_call")` (`websocket.py:815-838`). Executor writes `TaskLog` rows (`task_executor.py:347-355`) and never writes `ChatMessage`. `ChatMessage.conversation_id` is `NOT NULL` (`audit.py:59`) and history retrieval filters by `conv_id = f"web_{user_id}"` (`websocket.py:76-109`) — writing task-originated rounds into the same conversation would pollute WS chat history.

**Decision**: `persist_per_round=True` does NOT hardcode a table. The runner calls a caller-provided `RoundLogSink` protocol:

```python
class RoundLogSink(Protocol):
    async def write_round(
        self,
        round_idx: int,
        tool_calls: list[dict],
        tool_results: list[dict],
        usage: TokenUsage,
    ) -> None: ...
```

Two implementations ship in `backend/app/services/tool_loop_runner/adapters.py`:

- `ChatMessageSink(session, conversation_id)` — WS caller. Writes `ChatMessage(role="tool_call", conversation_id=...)` matching today's payload shape.
- `TaskLogSink(session, task_id)` — executor caller. Writes `TaskLog` rows preserving today's executor schema; does NOT write `ChatMessage`.

`RunContext` carries `round_log_sink: RoundLogSink | None`. When `persist_per_round=True` the sink MUST be non-None (runtime assertion). Rejected alternative: write `ChatMessage` for both callers with `conversation_id=f"task_{task_id}"` — would require a matching history-query change and is out of scope for Step A.

### D6. Per-round hook vs. inline event logic

The runner exposes `on_round_complete: Callable[[RoundOutcome], Awaitable[None]] | None`. It is `None` in Step A. Step C (`RoundEvent`) will pass a callback that writes the event row. This keeps the runner ignorant of Step C's schema while giving Step C a clean attachment point.

### D7. Abort signal source

**Fact check**: `Task` model has field `status` (not `state`) with enum values `pending/doing/done/failed/cancelled` — there is no `cancel_requested` value (`backend/app/models/task.py:27-31`). Executor today has **no in-flight cancel mechanism at all**: `task_executor.py:199-297` runs `for round_i in range(50)` with no DB poll, no `asyncio.Event`, no Future registry. The only cancel path is `asyncio.CancelledError` on process restart (`task_executor.py:311-321`).

Adding a `cancel_requested` enum value requires Alembic `ALTER TYPE`, which would break proposal's "No DB schema change (no Alembic migration)" commitment.

**Decision (scheme B — no DB schema change)**: Step A introduces in-process abort only. A module-level registry maps `task_id → asyncio.Event` inside `backend/app/services/tool_loop_runner/adapters.py`.

### D8. Observability — ActivityLog-backed (no Prometheus)

**Fact check**: The repo has no Prometheus infrastructure. `prometheus_client` is not in `backend/pyproject.toml`; no `Histogram`/`Counter` usage anywhere; no `/metrics` scrape endpoint. Shipping new Prometheus metrics would require adding a dependency + scrape endpoint + dashboard wiring, which is out of Step A scope.

**Decision**: runner emits one `AgentActivityLog` row per run completion instead of Prometheus metrics.

## Risks / Trade-offs

- **Executor begins enforcing quota on triggered runs** → some previously-succeeding trigger runs will 429.
- **Per-round `ChatMessage` writes inflate executor DB load** → measurable regression possible.
- **Capability-flag preset drift** → mitigated by `CapabilityFlags` as frozen dataclass with no defaults on any field.
- **Legacy branches lingering behind `TOOL_LOOP_V2=false`** → dead-code risk if cleanup is missed.

## Migration Plan

1. **PR 1 (this change)**: land `ToolLoopRunner`, both presets, both call sites delegating behind `TOOL_LOOP_V2`.
2. **Shadow phase**: enable sampled v2 for 10% of executor traffic in prod; collect write-delta and quota-rejection metrics.
3. **Full enable**: flip default to `true` for all traffic.
4. **Cleanup PR**: remove legacy branches and `TOOL_LOOP_V2` reads.

## Open Questions — Resolved

- **OQ1** — *Decision: A (day 0 enforcement)*.
- **OQ2** — *Decision: A (revised substrate)*: use `AgentActivityLog` rows + SQL aggregates instead of Prometheus.
- **OQ3** — *Decision: B (env-configurable default, value 50)*.
