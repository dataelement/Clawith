"""WebSocket cutover tests for durable native Web Chat runs."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
import uuid

from fastapi import WebSocketDisconnect
import pytest

from app.api.websocket import WebChatRuntimeIntake, WebSocketChatHandler
from app.models.chat_session import ChatSession
from app.models.llm import LLMModel
from app.models.user import User
from app.services.agent_runtime.chat_intake import ChatRuntimeIntake
from app.services.agent_runtime.chat_stream import ChatRuntimeStreamOutcome
from app.services.agent_runtime.contracts import (
    CancelRunCommand,
    RunHandle,
    RuntimeEventCursor,
)


class _WebSocket:
    def __init__(self, *incoming: dict) -> None:
        self.incoming = list(incoming)
        self.sent: list[dict] = []

    async def receive_json(self):
        if not self.incoming:
            raise WebSocketDisconnect()
        return self.incoming.pop(0)

    async def send_json(self, packet: dict) -> None:
        self.sent.append(packet)


class _Transaction:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False


class _Session:
    def __init__(self, records: dict[type, object] | None = None) -> None:
        self.records = records or {}

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False

    def begin(self):
        return _Transaction()

    async def get(self, model, _identity):
        return self.records.get(model)

    async def commit(self) -> None:
        return None


def _handler(websocket: _WebSocket) -> WebSocketChatHandler:
    user = User(
        id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        display_name="Ada",
        role="member",
        is_active=True,
    )
    handler = WebSocketChatHandler(
        websocket,  # type: ignore[arg-type]
        uuid.uuid4(),
        "token",
    )
    handler.user = user
    handler.agent_type = "native"
    handler.agent_name = "Analyst"
    handler.conv_id = str(uuid.uuid4())
    handler.history_messages = [SimpleNamespace()]
    handler.conversation = []
    return handler


def _handle(tenant_id: uuid.UUID) -> RunHandle:
    run_id = uuid.uuid4()
    return RunHandle(
        tenant_id=tenant_id,
        run_id=run_id,
        thread_id=str(run_id),
        command_id=uuid.uuid4(),
        runtime_type="langgraph",
        created=True,
    )


@pytest.mark.asyncio
async def test_native_message_uses_runtime_without_entering_legacy_tool_loop() -> None:
    websocket = _WebSocket({"content": "Investigate the issue"})
    handler = _handler(websocket)
    model = SimpleNamespace(id=uuid.uuid4())
    handle = _handle(handler.user.tenant_id)
    intake = ChatRuntimeIntake(
        handle=handle,
        message_id=uuid.uuid4(),
        resumed=False,
    )
    outcome = ChatRuntimeStreamOutcome(
        status="completed",
        content="Investigation complete",
        cursor=RuntimeEventCursor(
            datetime(2026, 7, 14, 10, 0, tzinfo=UTC),
            uuid.uuid4(),
        ),
    )

    with (
        patch.object(handler, "_resolve_effective_model", new=AsyncMock(return_value=model)),
        patch.object(handler, "_check_quotas", new=AsyncMock(return_value=True)),
        patch.object(
            handler,
            "_enqueue_runtime_chat",
            new=AsyncMock(return_value=WebChatRuntimeIntake(run=intake)),
        ) as enqueue,
        patch.object(
            handler,
            "_run_runtime_and_stream",
            new=AsyncMock(return_value=(outcome, [])),
        ) as run_runtime,
        patch.object(handler, "_save_user_message", new=AsyncMock()) as legacy_save,
    ):
        with pytest.raises(WebSocketDisconnect):
            await handler.message_loop()

    enqueue.assert_awaited_once()
    assert enqueue.await_args.kwargs["content"] == "Investigate the issue"
    assert enqueue.await_args.kwargs["model_id"] == model.id
    assert enqueue.await_args.kwargs["is_onboarding_trigger"] is False
    run_runtime.assert_awaited_once_with(
        intake,
        user_content="Investigate the issue",
    )
    legacy_save.assert_not_awaited()
    assert not hasattr(handler, "_run_llm_and_stream")
    assert handler.conversation == [
        {"role": "user", "content": "Investigate the issue"},
        {"role": "assistant", "content": "Investigation complete"},
    ]


@pytest.mark.asyncio
async def test_next_message_resumes_the_exact_wait_returned_on_this_socket() -> None:
    websocket = _WebSocket({"content": "Yes, publish it"})
    handler = _handler(websocket)
    handler.waiting_runtime_run_id = uuid.uuid4()
    handler.waiting_runtime_correlation_id = "publish-confirmation"
    model = SimpleNamespace(id=uuid.uuid4())
    intake = ChatRuntimeIntake(
        handle=_handle(handler.user.tenant_id),
        message_id=uuid.uuid4(),
        resumed=True,
    )

    with (
        patch.object(handler, "_resolve_effective_model", new=AsyncMock(return_value=model)),
        patch.object(handler, "_check_quotas", new=AsyncMock(return_value=True)),
        patch.object(
            handler,
            "_enqueue_runtime_chat",
            new=AsyncMock(return_value=WebChatRuntimeIntake(run=intake)),
        ) as enqueue,
        patch.object(
            handler,
            "_run_runtime_and_stream",
            new=AsyncMock(return_value=(None, [])),
        ),
    ):
        with pytest.raises(WebSocketDisconnect):
            await handler.message_loop()

    assert enqueue.await_args.kwargs["resume_run_id"] == handler.waiting_runtime_run_id
    assert enqueue.await_args.kwargs["resume_correlation_id"] == "publish-confirmation"


@pytest.mark.asyncio
async def test_disabled_runtime_fails_closed_without_legacy_execution() -> None:
    websocket = _WebSocket({"content": "Do not run this through legacy"})
    handler = _handler(websocket)
    model = SimpleNamespace(id=uuid.uuid4())

    with (
        patch.object(handler, "_resolve_effective_model", new=AsyncMock(return_value=model)),
        patch.object(handler, "_check_quotas", new=AsyncMock(return_value=True)),
        patch.object(handler, "_enqueue_runtime_chat", new=AsyncMock(return_value=None)),
        patch.object(handler, "_save_user_message", new=AsyncMock()) as legacy_save,
    ):
        with pytest.raises(WebSocketDisconnect):
            await handler.message_loop()

    assert websocket.sent == [
        {
            "type": "error",
            "content": "Durable Runtime is not enabled for native Web Chat.",
            "code": "runtime_disabled",
        }
    ]
    legacy_save.assert_not_awaited()
    assert not hasattr(handler, "_run_llm_and_stream")


@pytest.mark.asyncio
async def test_onboarding_trigger_uses_runtime_and_advances_after_completion() -> None:
    websocket = _WebSocket({"kind": "onboarding_trigger"})
    handler = _handler(websocket)
    model = SimpleNamespace(id=uuid.uuid4())
    intake = ChatRuntimeIntake(
        handle=_handle(handler.user.tenant_id),
        message_id=uuid.uuid4(),
        resumed=False,
    )
    outcome = ChatRuntimeStreamOutcome(
        status="completed",
        content="Welcome",
        cursor=RuntimeEventCursor(
            datetime(2026, 7, 14, 10, 0, tzinfo=UTC),
            uuid.uuid4(),
        ),
    )

    with (
        patch.object(handler, "_handle_onboarding_trigger_guard", new=AsyncMock(return_value=False)),
        patch.object(handler, "_resolve_effective_model", new=AsyncMock(return_value=model)),
        patch.object(handler, "_check_quotas", new=AsyncMock(return_value=True)),
        patch.object(
            handler,
            "_enqueue_runtime_chat",
            new=AsyncMock(
                return_value=WebChatRuntimeIntake(
                    run=intake,
                    onboarding_target_phase="greeted",
                )
            ),
        ) as enqueue,
        patch.object(
            handler,
            "_run_runtime_and_stream",
            new=AsyncMock(return_value=(outcome, [])),
        ) as run_runtime,
        patch.object(handler, "_mark_onboarding_runtime_phase", new=AsyncMock()) as mark,
    ):
        with pytest.raises(WebSocketDisconnect):
            await handler.message_loop()

    assert enqueue.await_args.kwargs["content"] == "Please begin the onboarding."
    assert enqueue.await_args.kwargs["is_onboarding_trigger"] is True
    run_runtime.assert_awaited_once_with(
        intake,
        user_content="Please begin the onboarding.",
    )
    mark.assert_awaited_once_with("greeted")
    assert not hasattr(handler, "_run_llm_and_stream")
    assert handler.conversation == [{"role": "assistant", "content": "Welcome"}]


@pytest.mark.asyncio
async def test_web_intake_pins_onboarding_metadata_without_a_visible_user_message() -> None:
    handler = _handler(_WebSocket())
    model = SimpleNamespace(id=uuid.uuid4())
    session = SimpleNamespace(title="Session 1")
    agent = SimpleNamespace(id=handler.agent_id)
    intake = ChatRuntimeIntake(
        handle=_handle(handler.user.tenant_id),
        message_id=uuid.uuid4(),
        resumed=False,
    )
    db = _Session({User: handler.user, ChatSession: session, LLMModel: model})
    onboarding = SimpleNamespace(
        prompt="Trusted greeting prompt",
        target_phase="greeted",
        lock_on_first_chunk=True,
        is_greeting_turn=True,
    )

    with (
        patch("app.api.websocket.async_session", return_value=db),
        patch("app.api.websocket.check_agent_access", new=AsyncMock(return_value=(agent, None))),
        patch(
            "app.api.websocket.resolve_onboarding_prompt",
            new=AsyncMock(return_value=onboarding),
        ),
        patch(
            "app.api.websocket.enqueue_chat_runtime",
            new=AsyncMock(return_value=intake),
        ) as enqueue,
    ):
        result = await handler._enqueue_runtime_chat(
            content="Please begin the onboarding.",
            display_content="",
            file_name="",
            model_id=model.id,
            message_id=None,
            resume_run_id=None,
            resume_correlation_id=None,
            is_onboarding_trigger=True,
        )

    assert result == WebChatRuntimeIntake(
        run=intake,
        onboarding_target_phase="greeted",
    )
    assert enqueue.await_args.kwargs["runtime_instruction"] == "Trusted greeting prompt"
    assert enqueue.await_args.kwargs["onboarding_target_phase"] == "greeted"
    assert enqueue.await_args.kwargs["persist_user_message"] is False
    assert enqueue.await_args.kwargs["application_tools_enabled"] is False
    assert session.title == "Onboarding"


@pytest.mark.asyncio
async def test_abort_enqueues_a_durable_cancel_command() -> None:
    handler = _handler(_WebSocket())
    handle = _handle(handler.user.tenant_id)

    with (
        patch("app.api.websocket.async_session", return_value=_Session()),
        patch(
            "app.api.websocket.TransactionalAgentRuntimeAdapter.cancel_run",
            new=AsyncMock(return_value=handle),
        ) as cancel_run,
    ):
        await handler._cancel_runtime_run(handle)

    command = cancel_run.await_args.args[0]
    assert isinstance(command, CancelRunCommand)
    assert command.run_id == handle.run_id
    assert command.idempotency_key == f"cancel:web:{handle.run_id}"
    assert command.actor_user_id == handler.user.id


@pytest.mark.asyncio
async def test_runtime_stream_acknowledges_the_durable_command_before_waiting() -> None:
    websocket = _WebSocket()
    blocked = asyncio.Event()

    async def receive_while_runtime_runs():
        await blocked.wait()
        return {}

    websocket.receive_json = receive_while_runtime_runs
    handler = _handler(websocket)
    handle = _handle(handler.user.tenant_id)
    intake = ChatRuntimeIntake(
        handle=handle,
        message_id=uuid.uuid4(),
        resumed=False,
    )
    outcome = ChatRuntimeStreamOutcome(
        status="completed",
        content="pong",
        cursor=RuntimeEventCursor(
            datetime(2026, 7, 14, 10, 0, tzinfo=UTC),
            uuid.uuid4(),
        ),
    )

    async def stream(**kwargs):
        assert kwargs["handle"] == handle
        return outcome

    with (
        patch("app.api.websocket.stream_web_chat_run", new=stream),
        patch("app.api.websocket.async_session", return_value=_Session()),
        patch(
            "app.api.websocket.maybe_mark_session_read_for_active_viewer",
            new=AsyncMock(),
        ),
        patch.object(
            handler,
            "_update_activity_and_quota",
            new=AsyncMock(),
        ),
    ):
        resolved, queued = await handler._run_runtime_and_stream(
            intake,
            user_content="ping",
        )

    assert resolved == outcome
    assert queued == []
    assert websocket.sent[0] == {
        "type": "runtime_status",
        "run_id": str(handle.run_id),
        "event": "accepted",
        "status": "queued",
    }
