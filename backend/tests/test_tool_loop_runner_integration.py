from __future__ import annotations

import asyncio
import uuid
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.tool_loop_runner import (
    FLAGS_TRIGGERED_TASK,
    FLAGS_WS_CHAT,
    EventAbortSource,
    InProcessTaskAbortSource,
    RunContext,
    RunStatus,
    ToolLoopRunner,
    TaskLogSink,
    request_abort,
    release_abort,
)
from app.services.tool_loop_runner.adapters import _TASK_ABORT_EVENTS, translate_to_ws_frame


def _make_agent(*, max_tool_rounds: int | None = 10, tokens_used_today: int = 0, max_tokens_per_day: int | None = None, tokens_used_month: int = 0, max_tokens_per_month: int | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        name="TestAgent",
        primary_model_id=uuid.uuid4(),
        fallback_model_id=None,
        creator_id=uuid.uuid4(),
        max_tool_rounds=max_tool_rounds,
        tokens_used_today=tokens_used_today,
        max_tokens_per_day=max_tokens_per_day,
        tokens_used_month=tokens_used_month,
        max_tokens_per_month=max_tokens_per_month,
    )


def _make_model(supports_vision: bool = False) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        provider="openai",
        model="gpt-4o",
        base_url="https://example.com/v1",
        api_key_encrypted="test-key",
        temperature=0.2,
        max_output_tokens=None,
        request_timeout=None,
        supports_vision=supports_vision,
    )


def _tool_response(tool_name: str, args: str = '{"path": "/x"}') -> SimpleNamespace:
    return SimpleNamespace(tool_calls=[{"id": f"tc_{uuid.uuid4().hex[:8]}", "function": {"name": tool_name, "arguments": args}}], content=None, usage=None, reasoning_content=None, finish_reason="tool_calls")


def _text_response(content: str = "done") -> SimpleNamespace:
    return SimpleNamespace(tool_calls=None, content=content, usage=None, reasoning_content=None, finish_reason="stop")


def _build_mock_client(*responses) -> AsyncMock:
    client = AsyncMock()
    client.stream = AsyncMock(side_effect=list(responses))
    client.close = AsyncMock()
    return client


def _db_mock_for_model(model: SimpleNamespace) -> AsyncMock:
    db = AsyncMock()
    db.__aenter__ = AsyncMock(return_value=db)
    db.__aexit__ = AsyncMock(return_value=False)
    result = MagicMock()
    result.scalar_one_or_none.return_value = model
    db.execute = AsyncMock(return_value=result)
    return db


def _make_sink() -> AsyncMock:
    sink = AsyncMock()
    sink.write_round = AsyncMock()
    return sink


def _standard_patches(mock_client, db_mock):
    return [
        patch("app.services.tool_loop_runner.runner.get_settings", return_value=SimpleNamespace(TOOL_LOOP_DEFAULT_MAX_ROUNDS=50, TOOL_LOOP_V2=True)),
        patch("app.services.llm_utils.create_llm_client", return_value=mock_client),
        patch("app.services.llm_utils.get_model_api_key", return_value="key"),
        patch("app.services.llm_utils.get_max_tokens", return_value=4096),
        patch("app.services.llm_utils.try_create_fallback_client", return_value=None),
        patch("app.services.agent_tools.get_agent_tools_for_llm", new_callable=AsyncMock, return_value=[]),
        patch("app.services.agent_tools.execute_tool", new_callable=AsyncMock, return_value="tool result"),
        patch("app.services.activity_logger.log_activity", new_callable=AsyncMock),
        patch("app.services.token_tracker.extract_usage_tokens", return_value=0),
        patch("app.services.token_tracker.estimate_tokens_from_chars", return_value=10),
        patch("app.services.token_tracker.record_token_usage", new_callable=AsyncMock),
        patch("app.database.async_session", return_value=db_mock),
        patch("app.services.task_executor.LLM_CALL_MAX_RETRIES", 1),
        patch("app.services.task_executor.LLM_CALL_RETRY_BASE_DELAY", 0),
    ]


async def _run_with_patches(ctx: RunContext, responses, flags=FLAGS_WS_CHAT, *, agent=None, model=None, extra_patches=None):
    if model is None:
        model = _make_model()
    mock_client = _build_mock_client(*responses)
    db = _db_mock_for_model(model)
    from app.services.llm_utils import LLMMessage

    patches = _standard_patches(mock_client, db)
    if extra_patches:
        patches.extend(extra_patches)
    with ExitStack() as stack:
        for p in patches:
            stack.enter_context(p)
        result = await ToolLoopRunner().run(ctx, [LLMMessage(role="user", content="go")], flags)
    return result, mock_client


@pytest.mark.asyncio
async def test_ws_chat_golden_path():
    agent = _make_agent(max_tool_rounds=10)
    sink = _make_sink()
    ctx = RunContext(session=AsyncMock(), agent=agent, caller="ws", abort_source=None, round_log_sink=sink, max_rounds_override=10, on_round_complete=None)
    responses = [_tool_response("read_file", '{"path": "/x"}') for _ in range(5)]
    responses.append(_text_response("all done"))
    result, _ = await _run_with_patches(ctx, responses, agent=agent)
    assert result.status == RunStatus.COMPLETED
    assert result.final_text == "all done"
    assert sink.write_round.await_count == 5
    assert len(result.rounds) == 5


@pytest.mark.asyncio
async def test_ws_chat_abort_mid_loop():
    agent = _make_agent(max_tool_rounds=10)
    sink = _make_sink()
    abort_event = asyncio.Event()
    call_count = 0

    async def _stream_with_abort(messages, tools, temperature, max_tokens):
        nonlocal call_count
        call_count += 1
        if call_count == 2:
            abort_event.set()
        return _tool_response("read_file", '{"path": "/x"}')

    mock_client = AsyncMock()
    mock_client.stream = AsyncMock(side_effect=_stream_with_abort)
    mock_client.close = AsyncMock()
    ctx = RunContext(session=AsyncMock(), agent=agent, caller="ws", abort_source=EventAbortSource(abort_event), round_log_sink=sink, max_rounds_override=10, on_round_complete=None)
    db = _db_mock_for_model(_make_model())
    from app.services.llm_utils import LLMMessage

    with ExitStack() as stack:
        for p in _standard_patches(mock_client, db):
            stack.enter_context(p)
        result = await ToolLoopRunner().run(ctx, [LLMMessage(role="user", content="go")], FLAGS_WS_CHAT)

    assert result.status == RunStatus.ABORTED
    assert len(result.rounds) == 2
    assert sink.write_round.await_count == 2


@pytest.mark.asyncio
async def test_ws_chat_quota_exceeded():
    agent = _make_agent(tokens_used_today=1000, max_tokens_per_day=500)
    sink = _make_sink()
    ctx = RunContext(session=AsyncMock(), agent=agent, caller="ws", abort_source=None, round_log_sink=sink, max_rounds_override=None, on_round_complete=None)
    result, mock_client = await _run_with_patches(ctx, [_text_response("never reached")], agent=agent)
    assert result.status == RunStatus.QUOTA_EXCEEDED
    assert result.scope == "daily"
    mock_client.stream.assert_not_called()
    sink.write_round.assert_not_called()
    frame = translate_to_ws_frame(result)
    assert frame["type"] == "done"


@pytest.mark.asyncio
async def test_executor_respects_max_tool_rounds_20():
    agent = _make_agent(max_tool_rounds=20)
    sink = _make_sink()
    ctx = RunContext(session=AsyncMock(), agent=agent, caller="executor", abort_source=None, round_log_sink=sink, max_rounds_override=None, on_round_complete=None)
    responses = [_tool_response("read_file", '{"path": "/x"}') for _ in range(25)]
    result, mock_client = await _run_with_patches(ctx, responses, flags=FLAGS_TRIGGERED_TASK, agent=agent)
    assert result.status == RunStatus.MAX_ROUNDS
    assert mock_client.stream.await_count == 20
    assert len(result.rounds) == 20


@pytest.mark.asyncio
async def test_executor_persists_per_round_task_log():
    agent = _make_agent(max_tool_rounds=10)
    task_log_writes: list[dict] = []

    class _RecordingTaskLogSink:
        async def write_round(self, round_idx, tool_calls, tool_results, usage):
            task_log_writes.append({"round_idx": round_idx, "tool_calls": tool_calls, "tool_results": tool_results})

    ctx = RunContext(session=AsyncMock(), agent=agent, caller="executor", abort_source=None, round_log_sink=_RecordingTaskLogSink(), max_rounds_override=None, on_round_complete=None)
    responses = [_tool_response("read_file", '{"path": "/x"}') for _ in range(4)]
    responses.append(_text_response("finished"))
    result, _ = await _run_with_patches(ctx, responses, flags=FLAGS_TRIGGERED_TASK, agent=agent)
    assert result.status == RunStatus.COMPLETED
    assert len(task_log_writes) == 4


@pytest.mark.asyncio
async def test_executor_aborts_via_request_abort():
    task_id = uuid.uuid4()
    agent = _make_agent(max_tool_rounds=10)
    sink = _make_sink()
    _TASK_ABORT_EVENTS.setdefault(task_id, asyncio.Event())
    call_count = 0

    async def _stream_set_abort(messages, tools, temperature, max_tokens):
        nonlocal call_count
        call_count += 1
        if call_count == 2:
            request_abort(task_id)
        return _tool_response("read_file", '{"path": "/x"}')

    mock_client = AsyncMock()
    mock_client.stream = AsyncMock(side_effect=_stream_set_abort)
    mock_client.close = AsyncMock()
    ctx = RunContext(session=AsyncMock(), agent=agent, caller="executor", abort_source=InProcessTaskAbortSource(task_id), round_log_sink=sink, max_rounds_override=10, on_round_complete=None)
    db = _db_mock_for_model(_make_model())
    from app.services.llm_utils import LLMMessage

    with ExitStack() as stack:
        for p in _standard_patches(mock_client, db):
            stack.enter_context(p)
        result = await ToolLoopRunner().run(ctx, [LLMMessage(role="user", content="go")], FLAGS_TRIGGERED_TASK)

    release_abort(task_id)
    assert result.status == RunStatus.ABORTED
    assert len(result.rounds) == 2
    assert mock_client.stream.await_count == 2


@pytest.mark.asyncio
async def test_executor_enforces_quota():
    agent = _make_agent(tokens_used_month=10000, max_tokens_per_month=5000)
    sink = _make_sink()
    ctx = RunContext(session=AsyncMock(), agent=agent, caller="executor", abort_source=None, round_log_sink=sink, max_rounds_override=None, on_round_complete=None)
    result, mock_client = await _run_with_patches(ctx, [_text_response("never reached")], flags=FLAGS_TRIGGERED_TASK, agent=agent)
    assert result.status == RunStatus.QUOTA_EXCEEDED
    assert result.scope == "monthly"
    mock_client.stream.assert_not_called()


@pytest.mark.asyncio
async def test_feature_flag_off_legacy_path():
    task_id = uuid.uuid4()
    agent_id = uuid.uuid4()
    model_id = uuid.uuid4()
    task_obj = SimpleNamespace(id=task_id, title="test task", description="desc", type="todo", supervision_target_name=None, status="pending", completed_at=None)
    task_done = SimpleNamespace(id=task_id, title="test task", description="desc", type="todo", supervision_target_name=None, status="doing", completed_at=None)
    agent_obj = SimpleNamespace(id=agent_id, name="Test", primary_model_id=model_id, fallback_model_id=None, creator_id=uuid.uuid4(), role_description="", status="idle")
    mock_client = AsyncMock()
    mock_client.stream = AsyncMock(return_value=SimpleNamespace(tool_calls=None, content="task complete", usage=None, reasoning_content=None, finish_reason="stop"))
    mock_client.close = AsyncMock()
    runner_run_calls = []

    async def _fake_runner_run(self, ctx, messages, flags):
        runner_run_calls.append(True)
        raise RuntimeError("ToolLoopRunner.run should NOT be called on legacy path")

    from app.services.task_executor import execute_task

    class _DummyResult:
        def __init__(self, val):
            self._val = val
        def scalar_one_or_none(self):
            return self._val

    session_dbs = [
        _build_recording_db([_DummyResult(task_obj)]),
        _build_recording_db([_DummyResult(agent_obj)]),
        _build_recording_db([_DummyResult(agent_obj)]),
        _build_recording_db([_DummyResult(task_done)]),
    ]

    with (
        patch("app.services.task_executor.async_session") as mock_session_ctx,
        patch("app.services.agent_context.build_agent_context", new_callable=AsyncMock, return_value=("static", "dynamic")),
        patch("app.services.llm.call_agent_llm_with_tools", new_callable=AsyncMock, return_value="task complete"),
        patch("app.services.activity_logger.log_activity", new_callable=AsyncMock),
        patch("app.services.task_executor.settings", SimpleNamespace(TOOL_LOOP_V2=False)),
        patch("app.services.tool_loop_runner.runner.ToolLoopRunner.run", _fake_runner_run),
    ):
        mock_session_ctx.return_value.__aenter__ = AsyncMock(side_effect=session_dbs)
        mock_session_ctx.return_value.__aexit__ = AsyncMock(return_value=False)
        await execute_task(task_id, agent_id)

    assert not runner_run_calls
    assert task_done.status == "done"


class _RecordingDB:
    def __init__(self, responses):
        self.responses = list(responses)
        self.added = []
        self.commits = 0

    async def execute(self, _stmt, _params=None):
        if not self.responses:
            class _Empty:
                def scalar_one_or_none(self): return None
            return _Empty()
        return self.responses.pop(0)

    def add(self, value):
        self.added.append(value)

    async def commit(self):
        self.commits += 1


def _build_recording_db(responses):
    return _RecordingDB(responses)
