## 1. Scaffolding

- [x] 1.1 Create `backend/app/services/tool_loop_runner/` package (with `__init__.py`, `runner.py`, `adapters.py`, `models.py`)
- [x] 1.2 Define `CapabilityFlags` frozen dataclass with 8 required fields and no defaults
- [x] 1.3 Define `RunContext` dataclass (session, agent, caller, abort_source, round_log_sink, max_rounds_override, on_round_complete)
- [x] 1.4 Define `RunResult` dataclass with status enum (`completed` | `quota_exceeded` | `aborted` | `max_rounds`) and scope field
- [x] 1.5 Define `RoundOutcome` dataclass (round index, tool calls, token usage, duration_ms)
- [x] 1.6 Add `FLAGS_WS_CHAT` and `FLAGS_TRIGGERED_TASK` presets (all 8 flags `True`)
- [x] 1.7 Move `TOOLS_REQUIRING_ARGS` constant into this module as public frozenset
- [x] 1.8 Add `TOOL_LOOP_V2` and `TOOL_LOOP_DEFAULT_MAX_ROUNDS` (default `50`, per OQ3) to `backend/app/config.py` Settings

## 2. Runner core

- [x] 2.1 Implement `ToolLoopRunner.run(ctx, messages, flags)` skeleton with round loop bounded by resolved max-rounds
- [x] 2.2 Implement max-rounds resolution: `max_rounds_override` > `agent.max_tool_rounds` > module default
- [x] 2.3 Implement pre-round abort check (`listen_abort`) via `AbortSource` adapter
- [x] 2.4 Implement pre-round quota check (`enforce_quota`)
- [x] 2.5 Implement image-stripping preprocessing (`strip_images`)
- [x] 2.6 Implement vision tool injection (`inject_vision`)
- [x] 2.7 Implement tool-arg guard (`tool_arg_guard`)
- [x] 2.8 Implement LLM call + tool dispatch + token accounting (`track_token_budget`) per round
- [x] 2.9 Implement per-round persistence through sink (`persist_per_round`)
- [x] 2.10 Invoke `on_round_complete(RoundOutcome)` when non-None
- [x] 2.11 Emit one `AgentActivityLog(action_type="tool_loop_completed")` row per run completion

## 3. Adapters

- [x] 3.1 Create `backend/app/services/tool_loop_runner/adapters.py`
- [x] 3.2 Implement `EventAbortSource(asyncio.Event)` for WS caller
- [x] 3.3 Implement `InProcessTaskAbortSource(task_id)` backed by `_TASK_ABORT_EVENTS`
- [x] 3.4 Define `RoundLogSink` protocol
- [x] 3.5 Implement `ChatMessageSinkFull(conversation_id, agent_id, user_id)`
- [x] 3.6 Implement `TaskLogSink(task_id)`
- [x] 3.7 Implement `translate_to_ws_frame(result: RunResult) -> dict`
- [x] 3.8 Implement `translate_to_task_failure(result: RunResult, task_id, task_type) -> None`

## 4. WebSocket call-site migration

- [ ] 4.1 In `backend/app/api/websocket.py`, branch on `settings.TOOL_LOOP_V2`
- [ ] 4.2 On v2 path: build `RunContext(caller="ws", ...)` and call runner with `FLAGS_WS_CHAT`
- [ ] 4.3 On legacy path: leave inline loop untouched
- [ ] 4.4 Verify activity_logger call sites remain unchanged
- [ ] 4.5 Remove local `_TOOLS_REQUIRING_ARGS` definition; import from `tool_loop_runner`
- [ ] 4.6 Wire `abort_event: asyncio.Event` through the WS path

## 5. Task executor call-site migration

- [x] 5.1 In `backend/app/services/task_executor.py`, branch on `settings.TOOL_LOOP_V2`
- [x] 5.2 On v2 path: build `RunContext(caller="executor", ...)` and call runner with `FLAGS_TRIGGERED_TASK`
- [x] 5.3 Remove hardcoded `range(50)` loop from the v2 path
- [x] 5.4 Preserve existing terminal `TaskLog` write on legacy path

## 6. Deduplicate TOOLS_REQUIRING_ARGS

- [ ] 6.1 Replace local copies in heartbeat/scheduler/websocket with shared import

## 7. Tests — unit

- [x] 7.1 `test_presets_equivalent`
- [x] 7.2 `test_capability_flags_no_defaults`
- [x] 7.3 `test_max_rounds_resolution`
- [x] 7.4 `test_quota_exceeded_short_circuit`
- [x] 7.5 `test_abort_pre_round`
- [x] 7.6 `test_arg_guard_rejects_empty_args`
- [x] 7.7 `test_on_round_complete_hook_invoked_per_round`
- [x] 7.8 `test_on_round_complete_none_is_noop`
- [x] 7.9 `test_tools_requiring_args_matches_registry`
- [x] 7.10 `test_no_duplicate_tools_requiring_args_definitions`
- [x] 7.11 `test_sink_required_when_persist_per_round_true`

## 8. Tests — integration

- [x] 8.1 WS chat golden-path
- [x] 8.2 WS chat abort mid-loop
- [x] 8.3 WS chat quota-exceeded
- [x] 8.4 Executor v2 honors `agent.max_tool_rounds=20`
- [x] 8.5 Executor v2 persists per-round messages
- [x] 8.6 Executor v2 aborts when `request_abort(task_id)` is called between rounds
- [x] 8.7 Executor v2 enforces quota
- [x] 8.8 Feature-flag-off path keeps legacy behavior

**Test result**: 22/22 passed.
