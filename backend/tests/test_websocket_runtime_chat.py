"""WebSocket cutover tests for durable native Web Chat runs."""

from __future__ import annotations

import asyncio
from collections import deque
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
import uuid

from fastapi import WebSocketDisconnect
import pytest

from app.api.websocket import (
    AcceptedWebChatMessage,
    WebChatRuntimeIntake,
    WebSocketChatHandler,
)
from app.models.agent_run import AgentRun
from app.models.agent_run_command import AgentRunCommand
from app.models.chat_session import ChatSession
from app.models.llm import LLMModel
from app.models.user import User
from app.services.agent_runtime.chat_intake import ChatRuntimeIntake, ChatRuntimeIntakeError
from app.services.agent_runtime.chat_intake import onboarding_source_execution_id
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
        self.closed_code: int | None = None

    async def receive_json(self):
        if not self.incoming:
            raise WebSocketDisconnect()
        return self.incoming.pop(0)

    async def send_json(self, packet: dict) -> None:
        self.sent.append(packet)

    async def close(self, code: int) -> None:
        self.closed_code = code


class _Transaction:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False


def test_attach_cursor_requires_stable_timezone_position() -> None:
    event_id = uuid.uuid4()
    cursor = WebSocketChatHandler._event_cursor(
        f"2026-07-17T10:00:00+00:00|{event_id}"
    )
    assert cursor == RuntimeEventCursor(
        datetime(2026, 7, 17, 10, 0, tzinfo=UTC),
        event_id,
    )
    with pytest.raises(ChatRuntimeIntakeError, match="timezone"):
        WebSocketChatHandler._event_cursor(
            f"2026-07-17T10:00:00|{event_id}"
        )


class _AsyncContext:
    def __init__(self, value: object) -> None:
        self.value = value

    async def __aenter__(self):
        return self.value

    async def __aexit__(self, exc_type, exc, traceback):
        return False


class _Result:
    def __init__(self, value: object = None) -> None:
        self.value = value

    def scalar_one_or_none(self):
        return self.value

    def scalars(self):
        return self

    def all(self):
        if self.value is None:
            return []
        return self.value if isinstance(self.value, list) else [self.value]


class _Session:
    def __init__(
        self,
        records: dict[type, object] | None = None,
        *results: object,
    ) -> None:
        self.records = records or {}
        self.results = deque(results)

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False

    def begin(self):
        return _Transaction()

    async def get(self, model, _identity):
        return self.records.get(model)

    async def execute(self, _statement):
        return _Result(self.results.popleft() if self.results else None)

    async def commit(self):
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


@pytest.mark.asyncio
async def test_explicit_session_scope_mismatch_fails_closed_without_primary_fallback() -> None:
    websocket = _WebSocket()
    handler = _handler(websocket)
    assert handler.user is not None
    explicit_id = uuid.uuid4()
    handler.session_id_param = str(explicit_id)
    handler.agent = SimpleNamespace(
        id=handler.agent_id,
        tenant_id=handler.user.tenant_id,
    )
    wrong_tenant_session = ChatSession(
        id=explicit_id,
        tenant_id=uuid.uuid4(),
        session_type="direct",
        agent_id=handler.agent_id,
        user_id=handler.user.id,
        title="Wrong tenant",
        source_channel="web",
        is_group=False,
        is_primary=True,
    )
    db = _Session(None, wrong_tenant_session)

    resolved = await handler._resolve_chat_session(db, handler.user.id)  # type: ignore[arg-type]

    assert resolved is None
    assert websocket.closed_code == 4002
    assert websocket.sent[-1]["code"] == "chat_session_scope_mismatch"
    assert len(db.results) == 0  # No second query may silently select the primary session.


@pytest.mark.asyncio
async def test_missing_explicit_session_fails_closed_without_primary_fallback() -> None:
    websocket = _WebSocket()
    handler = _handler(websocket)
    assert handler.user is not None
    handler.session_id_param = str(uuid.uuid4())
    handler.agent = SimpleNamespace(
        id=handler.agent_id,
        tenant_id=handler.user.tenant_id,
    )
    db = _Session(None, None)

    resolved = await handler._resolve_chat_session(db, handler.user.id)  # type: ignore[arg-type]

    assert resolved is None
    assert websocket.closed_code == 4002
    assert websocket.sent[-1]["code"] == "chat_session_scope_mismatch"
    assert len(db.results) == 0


@pytest.mark.asyncio
async def test_failed_pair_onboarding_allocates_one_durable_retry_attempt() -> None:
    websocket = _WebSocket()
    handler = _handler(websocket)
    assert handler.user is not None
    handler.agent = SimpleNamespace(
        id=handler.agent_id,
        tenant_id=handler.user.tenant_id,
    )
    first_execution = onboarding_source_execution_id(
        handler.user.tenant_id,
        handler.agent_id,
        handler.user.id,
        attempt=1,
    )
    failed_run = SimpleNamespace(
        id=uuid.uuid4(),
        source_execution_id=first_execution,
    )
    db = _Session(None, [failed_run])
    reader = SimpleNamespace(
        get_run_state=AsyncMock(
            return_value=SimpleNamespace(execution_status="failed")
        )
    )

    with (
        patch("app.api.websocket.async_session", return_value=db),
        patch("app.api.websocket.is_onboarded", new=AsyncMock(return_value=False)),
        patch(
            "app.api.websocket.open_run_state_reader",
            return_value=_AsyncContext(reader),
        ),
    ):
        execution_id = await handler._handle_onboarding_trigger_guard()

    assert execution_id == onboarding_source_execution_id(
        handler.user.tenant_id,
        handler.agent_id,
        handler.user.id,
        attempt=2,
    )
    assert websocket.sent == []


@pytest.mark.asyncio
async def test_inflight_pair_onboarding_rejects_stale_cross_session_trigger() -> None:
    websocket = _WebSocket()
    handler = _handler(websocket)
    assert handler.user is not None
    handler.agent = SimpleNamespace(
        id=handler.agent_id,
        tenant_id=handler.user.tenant_id,
    )
    execution_id = onboarding_source_execution_id(
        handler.user.tenant_id,
        handler.agent_id,
        handler.user.id,
        attempt=1,
    )
    active_run = SimpleNamespace(
        id=uuid.uuid4(),
        source_execution_id=execution_id,
    )
    db = _Session(None, [active_run])
    reader = SimpleNamespace(
        get_run_state=AsyncMock(
            return_value=SimpleNamespace(execution_status="running")
        )
    )

    with (
        patch("app.api.websocket.async_session", return_value=db),
        patch("app.api.websocket.is_onboarded", new=AsyncMock(return_value=False)),
        patch(
            "app.api.websocket.open_run_state_reader",
            return_value=_AsyncContext(reader),
        ),
    ):
        accepted_execution_id = await handler._handle_onboarding_trigger_guard()

    assert accepted_execution_id is None
    assert websocket.sent == [
        {
            "type": "onboarding_pending",
            "agent_id": str(handler.agent_id),
            "run_id": str(active_run.id),
        }
    ]


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


def _direct_cancel_records(
    handler: WebSocketChatHandler,
    run_id: uuid.UUID,
    *,
    lane_held: bool = True,
) -> tuple[object, ChatSession, AgentRun]:
    assert handler.user is not None and handler.conv_id is not None
    agent = SimpleNamespace(id=handler.agent_id, tenant_id=handler.user.tenant_id)
    session = ChatSession(
        id=uuid.UUID(handler.conv_id),
        tenant_id=handler.user.tenant_id,
        session_type="direct",
        agent_id=handler.agent_id,
        user_id=handler.user.id,
        title="Direct",
        source_channel="web",
        is_group=False,
        is_primary=True,
    )
    run = AgentRun(
        id=run_id,
        tenant_id=handler.user.tenant_id,
        agent_id=handler.agent_id,
        session_id=session.id,
        source_type="chat",
        goal="Answer",
        run_kind="foreground",
        model_id=uuid.uuid4(),
        model_turn_limit=50,
        runtime_type="langgraph",
        runtime_thread_id=str(session.id),
        graph_name="runtime_graph",
        graph_version="v1",
        scheduling_lane_key=(
            f"direct_chat_thread:{handler.user.tenant_id}:{session.id}"
        ),
        scheduling_position_created_at=datetime.now(UTC),
        scheduling_position_id=uuid.uuid4(),
        lane_held=lane_held,
        delivery_status="pending",
        origin_user_id=handler.user.id,
    )
    return agent, session, run


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
async def test_resume_requires_explicit_run_and_correlation_from_client() -> None:
    run_id = uuid.uuid4()
    websocket = _WebSocket(
        {
            "content": "Yes, publish it",
            "run_id": str(run_id),
            "correlation_id": "publish-confirmation",
        }
    )
    handler = _handler(websocket)
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

    assert enqueue.await_args.kwargs["resume_run_id"] == run_id
    assert enqueue.await_args.kwargs["resume_correlation_id"] == "publish-confirmation"


@pytest.mark.asyncio
async def test_plain_message_never_uses_connection_memory_as_implicit_resume() -> None:
    websocket = _WebSocket({"content": "New ordinary turn"})
    handler = _handler(websocket)
    model = SimpleNamespace(id=uuid.uuid4())
    intake = ChatRuntimeIntake(
        handle=_handle(handler.user.tenant_id),
        message_id=uuid.uuid4(),
        resumed=False,
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

    assert enqueue.await_args.kwargs["resume_run_id"] is None
    assert enqueue.await_args.kwargs["resume_correlation_id"] is None


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
        patch.object(
            handler,
            "_handle_onboarding_trigger_guard",
            new=AsyncMock(return_value="onboarding:test:attempt:1"),
        ),
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
    assert (
        enqueue.await_args.kwargs["onboarding_source_execution_id"]
        == "onboarding:test:attempt:1"
    )
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
    run_state_reader = SimpleNamespace()

    with (
        patch("app.api.websocket.async_session", return_value=db),
        patch(
            "app.api.websocket.open_run_state_reader",
            return_value=_AsyncContext(run_state_reader),
        ),
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
            onboarding_source_execution_id="onboarding:test:attempt:1",
        )

    assert result == WebChatRuntimeIntake(
        run=intake,
        onboarding_target_phase="greeted",
    )
    assert enqueue.await_args.kwargs["runtime_instruction"] == "Trusted greeting prompt"
    assert enqueue.await_args.kwargs["onboarding_target_phase"] == "greeted"
    assert enqueue.await_args.kwargs["persist_user_message"] is False
    assert (
        enqueue.await_args.kwargs["source_execution_id_override"]
        == "onboarding:test:attempt:1"
    )
    assert enqueue.await_args.kwargs["application_tools_enabled"] is False
    assert enqueue.await_args.kwargs["run_state_reader"] is run_state_reader
    assert session.title == "Onboarding"


@pytest.mark.asyncio
async def test_abort_enqueues_a_durable_cancel_command() -> None:
    handler = _handler(_WebSocket())
    handle = _handle(handler.user.tenant_id)
    agent = SimpleNamespace(id=handler.agent_id, tenant_id=handler.user.tenant_id)
    session = ChatSession(
        id=uuid.UUID(handler.conv_id),
        tenant_id=handler.user.tenant_id,
        session_type="direct",
        agent_id=handler.agent_id,
        user_id=handler.user.id,
        title="Direct",
        source_channel="web",
        is_group=False,
        is_primary=True,
    )
    run = AgentRun(
        id=handle.run_id,
        tenant_id=handler.user.tenant_id,
        agent_id=handler.agent_id,
        session_id=session.id,
        source_type="chat",
        goal="Answer",
        run_kind="foreground",
        model_id=uuid.uuid4(),
        model_turn_limit=50,
        runtime_type="langgraph",
        runtime_thread_id=str(session.id),
        graph_name="runtime_graph",
        graph_version="v1",
        scheduling_lane_key=(
            f"direct_chat_thread:{handler.user.tenant_id}:{session.id}"
        ),
        scheduling_position_created_at=datetime.now(UTC),
        scheduling_position_id=uuid.uuid4(),
        lane_held=True,
        delivery_status="pending",
        origin_user_id=handler.user.id,
    )
    db = _Session(
        {User: handler.user, ChatSession: session},
        run,
        None,
    )

    with (
        patch("app.api.websocket.async_session", return_value=db),
        patch(
            "app.api.websocket.check_agent_access",
            new=AsyncMock(return_value=(agent, None)),
        ),
        patch(
            "app.api.websocket.RuntimeCommandIntake.cancel_run",
            new=AsyncMock(return_value=handle),
        ) as cancel_run,
    ):
        result = await handler._cancel_runtime_run(handle.run_id)

    assert result == handle
    command = cancel_run.await_args.args[0]
    assert isinstance(command, CancelRunCommand)
    assert command.run_id == handle.run_id
    assert command.idempotency_key == f"cancel:web:{handle.run_id}"
    assert command.actor_user_id == handler.user.id


@pytest.mark.asyncio
async def test_cancel_rejects_run_from_another_session() -> None:
    handler = _handler(_WebSocket())
    run_id = uuid.uuid4()
    agent, session, run = _direct_cancel_records(handler, run_id)
    run.session_id = uuid.uuid4()
    db = _Session({User: handler.user, ChatSession: session}, run)

    with (
        patch("app.api.websocket.async_session", return_value=db),
        patch(
            "app.api.websocket.check_agent_access",
            new=AsyncMock(return_value=(agent, None)),
        ),
    ):
        with pytest.raises(ChatRuntimeIntakeError) as raised:
            await handler._cancel_runtime_run(run_id)

    assert getattr(raised.value, "code", None) == "chat_cancel_scope_mismatch"


@pytest.mark.asyncio
async def test_duplicate_cancel_remains_idempotent_after_lane_release() -> None:
    handler = _handler(_WebSocket())
    handle = _handle(handler.user.tenant_id)
    agent, session, run = _direct_cancel_records(
        handler,
        handle.run_id,
        lane_held=False,
    )
    existing = AgentRunCommand(
        id=uuid.uuid4(),
        tenant_id=run.tenant_id,
        run_id=run.id,
        command_type="cancel",
        payload={"reason": "cancelled_by_user"},
        actor_user_id=handler.user.id,
        idempotency_key=f"cancel:web:{run.id}",
        status="applied",
        attempt_count=1,
        created_at=datetime.now(UTC),
        applied_at=datetime.now(UTC),
    )
    db = _Session({User: handler.user, ChatSession: session}, run, existing)

    with (
        patch("app.api.websocket.async_session", return_value=db),
        patch(
            "app.api.websocket.check_agent_access",
            new=AsyncMock(return_value=(agent, None)),
        ),
        patch(
            "app.api.websocket.RuntimeCommandIntake.cancel_run",
            new=AsyncMock(return_value=handle),
        ) as cancel_run,
    ):
        result = await handler._cancel_runtime_run(run.id)

    assert result == handle
    cancel_run.assert_awaited_once()


@pytest.mark.asyncio
async def test_main_message_loop_accepts_cancel_after_waiting_stream_has_ended() -> None:
    run_id = uuid.uuid4()
    websocket = _WebSocket({"type": "abort", "run_id": str(run_id)})
    handler = _handler(websocket)
    handle = _handle(handler.user.tenant_id)
    handle = RunHandle(
        tenant_id=handle.tenant_id,
        run_id=run_id,
        thread_id=handle.thread_id,
        command_id=handle.command_id,
        runtime_type=handle.runtime_type,
        created=handle.created,
    )

    with patch.object(
        handler,
        "_cancel_runtime_run",
        new=AsyncMock(return_value=handle),
    ) as cancel:
        with pytest.raises(WebSocketDisconnect):
            await handler.message_loop()

    cancel.assert_awaited_once_with(run_id)
    assert websocket.sent == [
        {
            "type": "runtime_status",
            "run_id": str(run_id),
            "event": "cancel_requested",
            "status": "cancelling",
        }
    ]


@pytest.mark.asyncio
async def test_cancel_without_run_id_fails_closed() -> None:
    websocket = _WebSocket({"type": "abort"})
    handler = _handler(websocket)

    with patch.object(handler, "_cancel_runtime_run", new=AsyncMock()) as cancel:
        with pytest.raises(WebSocketDisconnect):
            await handler.message_loop()

    cancel.assert_not_awaited()
    assert websocket.sent[0]["code"] == "missing_cancel_run_id"


@pytest.mark.asyncio
async def test_openclaw_abort_keeps_existing_non_runtime_behavior() -> None:
    handler = _handler(_WebSocket({"type": "abort"}))
    handler.agent_type = "openclaw"

    with patch.object(handler, "_cancel_runtime_run", new=AsyncMock()) as cancel:
        with pytest.raises(WebSocketDisconnect):
            await handler.message_loop()

    cancel.assert_not_awaited()


class _BlockingWebSocket(_WebSocket):
    async def receive_json(self):
        if self.incoming:
            return self.incoming.pop(0)
        await asyncio.sleep(10)
        raise AssertionError("unreachable")


@pytest.mark.asyncio
async def test_followup_message_is_durably_accepted_while_current_stream_runs() -> None:
    websocket = _BlockingWebSocket({"content": "Queue this next"})
    handler = _handler(websocket)
    current = ChatRuntimeIntake(
        handle=_handle(handler.user.tenant_id),
        message_id=uuid.uuid4(),
        resumed=False,
    )
    queued = AcceptedWebChatMessage(
        runtime=WebChatRuntimeIntake(
            run=ChatRuntimeIntake(
                handle=_handle(handler.user.tenant_id),
                message_id=uuid.uuid4(),
                resumed=False,
            )
        ),
        user_content="Queue this next",
    )
    outcome = ChatRuntimeStreamOutcome(
        status="completed",
        content="First done",
        cursor=RuntimeEventCursor(datetime.now(UTC), uuid.uuid4()),
    )

    async def _stream(**_kwargs):
        await asyncio.sleep(0.01)
        return outcome

    with (
        patch("app.api.websocket.stream_web_chat_run", new=_stream),
        patch.object(
            handler,
            "_accept_client_message",
            new=AsyncMock(return_value=queued),
        ) as accept,
        patch.object(handler, "_update_activity_and_quota", new=AsyncMock()),
        patch("app.api.websocket.async_session", return_value=_Session()),
        patch(
            "app.api.websocket.maybe_mark_session_read_for_active_viewer",
            new=AsyncMock(return_value=False),
        ),
    ):
        returned, queued_messages = await handler._run_runtime_and_stream(
            current,
            user_content="First",
        )

    assert returned == outcome
    assert queued_messages == [queued]
    accept.assert_awaited_once_with({"content": "Queue this next"})
    assert any(
        packet.get("event") == "queued"
        and packet.get("run_id") == str(queued.runtime.run.handle.run_id)
        for packet in websocket.sent
    )
