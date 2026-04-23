"""Unit tests for tool_loop_runner.

Each test verifies one behavior with mocked I/O.
"""

from __future__ import annotations

import asyncio
import dataclasses
import pathlib
import re
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.tool_loop_runner.adapters import EventAbortSource
from app.services.tool_loop_runner.models import (
    CapabilityFlags,
    FLAGS_TRIGGERED_TASK,
    FLAGS_WS_CHAT,
    TOOLS_REQUIRING_ARGS,
    RunContext,
    RunStatus,
)
from app.services.tool_loop_runner.runner import ToolLoopRunner


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


def _make_model() -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        provider="openai",
        model="gpt-4o",
        base_url="https://example.com/v1",
        api_key_encrypted="test-key",
        temperature=0.2,
        max_output_tokens=None,
        request_timeout=None,
        supports_vision=False,
    )


def _make_llm_response(*, tool_calls=None, content="done", usage=None) -> SimpleNamespace:
    return SimpleNamespace(tool_calls=tool_calls, content=content, usage=usage, reasoning_content=None, finish_reason="stop")


def _make_sink() -> AsyncMock:
    sink = AsyncMock()
    sink.write_round = AsyncMock()
    return sink


def test_presets_equivalent():
    for f in dataclasses.fields(CapabilityFlags):
        assert getattr(FLAGS_WS_CHAT, f.name) == getattr(FLAGS_TRIGGERED_TASK, f.name)
    assert FLAGS_WS_CHAT == FLAGS_TRIGGERED_TASK


def test_capability_flags_no_defaults():
    with pytest.raises(TypeError):
        CapabilityFlags()  # type: ignore[call-arg]


def test_capability_flags_partial_raises():
    with pytest.raises(TypeError):
        CapabilityFlags(track_token_budget=True)  # type: ignore[call-arg]


def test_max_rounds_resolution_override_wins():
    runner = ToolLoopRunner()
    agent = _make_agent(max_tool_rounds=20)
    settings = SimpleNamespace(TOOL_LOOP_DEFAULT_MAX_ROUNDS=50)
    flags = dataclasses.replace(FLAGS_WS_CHAT, dynamic_max_rounds=True)
    ctx = SimpleNamespace(max_rounds_override=5)
    assert runner._resolve_max_rounds(ctx, agent, flags, settings) == 5


def test_max_rounds_resolution_agent_beats_default_when_dynamic():
    runner = ToolLoopRunner()
    agent = _make_agent(max_tool_rounds=20)
    settings = SimpleNamespace(TOOL_LOOP_DEFAULT_MAX_ROUNDS=50)
    flags = dataclasses.replace(FLAGS_WS_CHAT, dynamic_max_rounds=True)
    ctx = SimpleNamespace(max_rounds_override=None)
    assert runner._resolve_max_rounds(ctx, agent, flags, settings) == 20


def test_max_rounds_resolution_default_when_dynamic_false():
    runner = ToolLoopRunner()
    agent = _make_agent(max_tool_rounds=20)
    settings = SimpleNamespace(TOOL_LOOP_DEFAULT_MAX_ROUNDS=50)
    flags = dataclasses.replace(FLAGS_WS_CHAT, dynamic_max_rounds=False)
    ctx = SimpleNamespace(max_rounds_override=None)
    assert runner._resolve_max_rounds(ctx, agent, flags, settings) == 50


@pytest.mark.asyncio
async def test_quota_exceeded_short_circuit():
    agent = _make_agent(tokens_used_today=1000, max_tokens_per_day=500)
    model = _make_model()
    mock_client = AsyncMock()
    mock_client.stream = AsyncMock()
    mock_client.close = AsyncMock()
    ctx = RunContext(session=AsyncMock(), agent=agent, caller="ws", abort_source=None, round_log_sink=_make_sink(), max_rounds_override=None, on_round_complete=None)

    with (
        patch("app.services.tool_loop_runner.runner.get_settings") as mock_settings,
        patch("app.services.agent_tools.get_agent_tools_for_llm", new_callable=AsyncMock, return_value=[]),
        patch("app.services.llm_utils.create_llm_client", return_value=mock_client),
        patch("app.services.llm_utils.get_model_api_key", return_value="key"),
        patch("app.services.llm_utils.get_max_tokens", return_value=4096),
        patch("app.services.activity_logger.log_activity", new_callable=AsyncMock),
        patch("app.database.async_session") as mock_db_ctx,
    ):
        mock_settings.return_value = SimpleNamespace(TOOL_LOOP_DEFAULT_MAX_ROUNDS=50, TOOL_LOOP_V2=True)
        db_mock = AsyncMock()
        db_mock.__aenter__ = AsyncMock(return_value=db_mock)
        db_mock.__aexit__ = AsyncMock(return_value=False)
        model_result = MagicMock()
        model_result.scalar_one_or_none.return_value = model
        db_mock.execute = AsyncMock(return_value=model_result)
        mock_db_ctx.return_value = db_mock
        from app.services.llm_utils import LLMMessage

        result = await ToolLoopRunner().run(ctx, [LLMMessage(role="user", content="hello")], FLAGS_WS_CHAT)

    assert result.status == RunStatus.QUOTA_EXCEEDED
    assert result.scope == "daily"
    mock_client.stream.assert_not_called()


@pytest.mark.asyncio
async def test_abort_pre_round():
    agent = _make_agent()
    model = _make_model()
    abort_event = asyncio.Event()
    abort_event.set()
    mock_client = AsyncMock()
    mock_client.stream = AsyncMock()
    mock_client.close = AsyncMock()
    ctx = RunContext(session=AsyncMock(), agent=agent, caller="ws", abort_source=EventAbortSource(abort_event), round_log_sink=_make_sink(), max_rounds_override=None, on_round_complete=None)

    with (
        patch("app.services.tool_loop_runner.runner.get_settings") as mock_settings,
        patch("app.services.llm_utils.create_llm_client", return_value=mock_client),
        patch("app.services.llm_utils.get_model_api_key", return_value="key"),
        patch("app.services.llm_utils.get_max_tokens", return_value=4096),
        patch("app.services.agent_tools.get_agent_tools_for_llm", new_callable=AsyncMock, return_value=[]),
        patch("app.services.activity_logger.log_activity", new_callable=AsyncMock),
        patch("app.database.async_session") as mock_db_ctx,
    ):
        mock_settings.return_value = SimpleNamespace(TOOL_LOOP_DEFAULT_MAX_ROUNDS=50, TOOL_LOOP_V2=True)
        db_mock = AsyncMock()
        db_mock.__aenter__ = AsyncMock(return_value=db_mock)
        db_mock.__aexit__ = AsyncMock(return_value=False)
        model_result = MagicMock()
        model_result.scalar_one_or_none.return_value = model
        db_mock.execute = AsyncMock(return_value=model_result)
        mock_db_ctx.return_value = db_mock
        from app.services.llm_utils import LLMMessage

        result = await ToolLoopRunner().run(ctx, [LLMMessage(role="user", content="hi")], FLAGS_WS_CHAT)

    assert result.status == RunStatus.ABORTED
    mock_client.stream.assert_not_called()


@pytest.mark.asyncio
async def test_arg_guard_rejects_empty_args():
    agent = _make_agent()
    model = _make_model()
    first_response = _make_llm_response(tool_calls=[{"id": "tc_001", "function": {"name": "write_file", "arguments": "{}"}}], content=None)
    second_response = _make_llm_response(tool_calls=None, content="done")
    mock_client = AsyncMock()
    mock_client.stream = AsyncMock(side_effect=[first_response, second_response])
    mock_client.close = AsyncMock()
    ctx = RunContext(session=AsyncMock(), agent=agent, caller="ws", abort_source=None, round_log_sink=_make_sink(), max_rounds_override=2, on_round_complete=None)

    with (
        patch("app.services.tool_loop_runner.runner.get_settings") as mock_settings,
        patch("app.services.llm_utils.create_llm_client", return_value=mock_client),
        patch("app.services.llm_utils.get_model_api_key", return_value="key"),
        patch("app.services.llm_utils.get_max_tokens", return_value=4096),
        patch("app.services.agent_tools.get_agent_tools_for_llm", new_callable=AsyncMock, return_value=[]),
        patch("app.services.agent_tools.execute_tool", new_callable=AsyncMock) as mock_exec,
        patch("app.services.activity_logger.log_activity", new_callable=AsyncMock),
        patch("app.services.token_tracker.extract_usage_tokens", return_value=0),
        patch("app.services.token_tracker.estimate_tokens_from_chars", return_value=10),
        patch("app.services.token_tracker.record_token_usage", new_callable=AsyncMock),
        patch("app.database.async_session") as mock_db_ctx,
        patch("app.services.task_executor.LLM_CALL_MAX_RETRIES", 1),
        patch("app.services.task_executor.LLM_CALL_RETRY_BASE_DELAY", 0),
    ):
        mock_settings.return_value = SimpleNamespace(TOOL_LOOP_DEFAULT_MAX_ROUNDS=50, TOOL_LOOP_V2=True)
        db_mock = AsyncMock()
        db_mock.__aenter__ = AsyncMock(return_value=db_mock)
        db_mock.__aexit__ = AsyncMock(return_value=False)
        model_result = MagicMock()
        model_result.scalar_one_or_none.return_value = model
        db_mock.execute = AsyncMock(return_value=model_result)
        mock_db_ctx.return_value = db_mock
        from app.services.llm_utils import LLMMessage

        result = await ToolLoopRunner().run(ctx, [LLMMessage(role="user", content="write a file")], FLAGS_WS_CHAT)

    mock_exec.assert_not_called()
    assert result.status == RunStatus.COMPLETED


@pytest.mark.asyncio
async def test_on_round_complete_hook_invoked_per_round():
    agent = _make_agent()
    model = _make_model()
    tool_round = _make_llm_response(tool_calls=[{"id": "tc_1", "function": {"name": "read_file", "arguments": '{"path": "/x"}'}}], content=None)
    finish_round = _make_llm_response(tool_calls=None, content="finished")
    mock_client = AsyncMock()
    mock_client.stream = AsyncMock(side_effect=[tool_round, tool_round, finish_round])
    mock_client.close = AsyncMock()
    hook = AsyncMock()
    ctx = RunContext(session=AsyncMock(), agent=agent, caller="ws", abort_source=None, round_log_sink=_make_sink(), max_rounds_override=5, on_round_complete=hook)

    with (
        patch("app.services.tool_loop_runner.runner.get_settings") as mock_settings,
        patch("app.services.llm_utils.create_llm_client", return_value=mock_client),
        patch("app.services.llm_utils.get_model_api_key", return_value="key"),
        patch("app.services.llm_utils.get_max_tokens", return_value=4096),
        patch("app.services.agent_tools.get_agent_tools_for_llm", new_callable=AsyncMock, return_value=[]),
        patch("app.services.agent_tools.execute_tool", new_callable=AsyncMock, return_value="ok"),
        patch("app.services.activity_logger.log_activity", new_callable=AsyncMock),
        patch("app.services.token_tracker.extract_usage_tokens", return_value=0),
        patch("app.services.token_tracker.estimate_tokens_from_chars", return_value=10),
        patch("app.services.token_tracker.record_token_usage", new_callable=AsyncMock),
        patch("app.database.async_session") as mock_db_ctx,
        patch("app.services.task_executor.LLM_CALL_MAX_RETRIES", 1),
        patch("app.services.task_executor.LLM_CALL_RETRY_BASE_DELAY", 0),
    ):
        mock_settings.return_value = SimpleNamespace(TOOL_LOOP_DEFAULT_MAX_ROUNDS=50, TOOL_LOOP_V2=True)
        db_mock = AsyncMock()
        db_mock.__aenter__ = AsyncMock(return_value=db_mock)
        db_mock.__aexit__ = AsyncMock(return_value=False)
        model_result = MagicMock()
        model_result.scalar_one_or_none.return_value = model
        db_mock.execute = AsyncMock(return_value=model_result)
        mock_db_ctx.return_value = db_mock
        from app.services.llm_utils import LLMMessage

        await ToolLoopRunner().run(ctx, [LLMMessage(role="user", content="go")], FLAGS_WS_CHAT)

    assert hook.await_count == 2


@pytest.mark.asyncio
async def test_on_round_complete_none_is_noop():
    agent = _make_agent()
    model = _make_model()
    mock_client = AsyncMock()
    mock_client.stream = AsyncMock(return_value=_make_llm_response(tool_calls=None, content="done"))
    mock_client.close = AsyncMock()
    ctx = RunContext(session=AsyncMock(), agent=agent, caller="ws", abort_source=None, round_log_sink=_make_sink(), max_rounds_override=2, on_round_complete=None)

    with (
        patch("app.services.tool_loop_runner.runner.get_settings") as mock_settings,
        patch("app.services.llm_utils.create_llm_client", return_value=mock_client),
        patch("app.services.llm_utils.get_model_api_key", return_value="key"),
        patch("app.services.llm_utils.get_max_tokens", return_value=4096),
        patch("app.services.agent_tools.get_agent_tools_for_llm", new_callable=AsyncMock, return_value=[]),
        patch("app.services.activity_logger.log_activity", new_callable=AsyncMock),
        patch("app.services.token_tracker.extract_usage_tokens", return_value=0),
        patch("app.services.token_tracker.estimate_tokens_from_chars", return_value=10),
        patch("app.services.token_tracker.record_token_usage", new_callable=AsyncMock),
        patch("app.database.async_session") as mock_db_ctx,
        patch("app.services.task_executor.LLM_CALL_MAX_RETRIES", 1),
        patch("app.services.task_executor.LLM_CALL_RETRY_BASE_DELAY", 0),
    ):
        mock_settings.return_value = SimpleNamespace(TOOL_LOOP_DEFAULT_MAX_ROUNDS=50, TOOL_LOOP_V2=True)
        db_mock = AsyncMock()
        db_mock.__aenter__ = AsyncMock(return_value=db_mock)
        db_mock.__aexit__ = AsyncMock(return_value=False)
        model_result = MagicMock()
        model_result.scalar_one_or_none.return_value = model
        db_mock.execute = AsyncMock(return_value=model_result)
        mock_db_ctx.return_value = db_mock
        from app.services.llm_utils import LLMMessage

        result = await ToolLoopRunner().run(ctx, [LLMMessage(role="user", content="ok")], FLAGS_WS_CHAT)

    assert result.status == RunStatus.COMPLETED


def test_tools_requiring_args_matches_registry():
    core_tools = {"write_file", "read_file", "delete_file", "read_document", "send_message_to_agent", "send_feishu_message", "send_email"}
    assert len(TOOLS_REQUIRING_ARGS) >= 7
    for tool in core_tools:
        assert tool in TOOLS_REQUIRING_ARGS


def test_no_duplicate_tools_requiring_args_definitions():
    backend_root = pathlib.Path(__file__).parent.parent
    app_root = backend_root / "app"
    pattern = re.compile(r"_?TOOLS_REQUIRING_ARGS(\s*:[^=]+)?\s*=\s*(frozenset|\{)")
    matches: list[pathlib.Path] = []
    for py_file in app_root.rglob("*.py"):
        try:
            text = py_file.read_text(encoding="utf-8")
        except Exception:
            continue
        if pattern.search(text):
            matches.append(py_file)
    models_py = backend_root / "app" / "services" / "tool_loop_runner" / "models.py"
    assert models_py in matches


@pytest.mark.asyncio
async def test_sink_required_when_persist_per_round_true():
    agent = _make_agent()
    model = _make_model()
    mock_client = AsyncMock()
    mock_client.stream = AsyncMock()
    mock_client.close = AsyncMock()
    ctx = RunContext(session=AsyncMock(), agent=agent, caller="ws", abort_source=None, round_log_sink=None, max_rounds_override=None, on_round_complete=None)

    with (
        patch("app.services.tool_loop_runner.runner.get_settings") as mock_settings,
        patch("app.services.llm_utils.create_llm_client", return_value=mock_client),
        patch("app.services.llm_utils.get_model_api_key", return_value="key"),
        patch("app.services.llm_utils.get_max_tokens", return_value=4096),
        patch("app.services.agent_tools.get_agent_tools_for_llm", new_callable=AsyncMock, return_value=[]),
        patch("app.services.activity_logger.log_activity", new_callable=AsyncMock),
        patch("app.database.async_session") as mock_db_ctx,
    ):
        mock_settings.return_value = SimpleNamespace(TOOL_LOOP_DEFAULT_MAX_ROUNDS=50, TOOL_LOOP_V2=True)
        db_mock = AsyncMock()
        db_mock.__aenter__ = AsyncMock(return_value=db_mock)
        db_mock.__aexit__ = AsyncMock(return_value=False)
        model_result = MagicMock()
        model_result.scalar_one_or_none.return_value = model
        db_mock.execute = AsyncMock(return_value=model_result)
        mock_db_ctx.return_value = db_mock
        from app.services.llm_utils import LLMMessage

        with pytest.raises(AssertionError):
            await ToolLoopRunner().run(ctx, [LLMMessage(role="user", content="hi")], FLAGS_WS_CHAT)

    mock_client.stream.assert_not_called()
