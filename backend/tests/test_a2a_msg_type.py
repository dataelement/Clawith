"""Tests for async A2A msg_type differentiation (notify/consult/task_delegate).

Validates the branching logic in _send_message_to_agent:
- notify:    fire-and-forget, returns immediately
- task_delegate: async with callback, creates focus + trigger
- consult:   synchronous request-response (original behaviour)
"""

import json
import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ── Helpers ──────────────────────────────────────────────────────────

class DummyResult:
    def __init__(self, values=None, scalar_value=None, scalars_list=None):
        self._values = list(values or [])
        self._scalar_value = scalar_value
        self._scalars_list = scalars_list

    def scalar_one_or_none(self):
        if self._scalar_value is not None:
            return self._scalar_value
        return self._values[0] if self._values else None

    def scalars(self):
        return self

    def all(self):
        return list(self._scalars_list or self._values)

    def first(self):
        if self._scalars_list:
            return self._scalars_list[0] if self._scalars_list else None
        return self._values[0] if self._values else None

    def scalar(self):
        if self._scalar_value is not None:
            return self._scalar_value
        return self._values[0] if self._values else None


class RecordingDB:
    def __init__(self, responses=None):
        self.responses = list(responses or [])
        self.added = []
        self.committed = False
        self.flushed = False

    async def execute(self, _statement, _params=None):
        if not self.responses:
            raise AssertionError("unexpected execute() call")
        return self.responses.pop(0)

    def add(self, value):
        self.added.append(value)

    async def commit(self):
        self.committed = True

    async def flush(self):
        self.flushed = True


def _make_agent(agent_id=None, name="TestAgent", tenant_id=None, agent_type="native",
                expired=False, primary_model_id=None):
    agent = MagicMock()
    agent.id = agent_id or uuid.uuid4()
    agent.name = name
    agent.tenant_id = tenant_id or uuid.uuid4()
    agent.agent_type = agent_type
    agent.is_expired = expired
    agent.expires_at = None
    agent.creator_id = uuid.uuid4()
    agent.primary_model_id = primary_model_id
    agent.fallback_model_id = None
    agent.role_description = ""
    agent.max_tool_rounds = 50
    return agent


def _make_participant(part_id=None, ref_id=None):
    p = MagicMock()
    p.id = part_id or uuid.uuid4()
    p.type = "agent"
    p.ref_id = ref_id or uuid.uuid4()
    return p


def _make_tenant(a2a_async_enabled=True):
    t = MagicMock()
    t.a2a_async_enabled = a2a_async_enabled
    return t


# ── Tests ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_notify_returns_immediately():
    """notify msg_type should return immediately without calling LLM."""
    from app.services.agent_tools import _send_message_to_agent

    from_agent_id = uuid.uuid4()
    target_id = uuid.uuid4()
    rel_id = uuid.uuid4()
    session_id = uuid.uuid4()
    src_participant = _make_participant(ref_id=from_agent_id)
    tgt_participant = _make_participant(ref_id=target_id)
    source_agent = _make_agent(from_agent_id, name="Alice")
    target_agent = _make_agent(target_id, name="Bob")

    session = MagicMock()
    session.id = session_id
    session.last_message_at = None

    db = RecordingDB(responses=[
        DummyResult(scalar_value=source_agent),
        DummyResult(scalars_list=[target_agent]),
        DummyResult(scalar_value=rel_id),
        DummyResult(scalar_value=src_participant),
        DummyResult(scalar_value=tgt_participant),
        DummyResult(scalar_value=session),
        DummyResult(scalar_value=_make_tenant()),
    ])

    with patch("app.services.agent_tools.async_session") as mock_session_ctx, \
         patch("app.services.agent_tools._wake_agent_async", new_callable=AsyncMock) as mock_wake:

        mock_session_ctx.return_value.__aenter__ = AsyncMock(return_value=db)
        mock_session_ctx.return_value.__aexit__ = AsyncMock(return_value=False)

        result = await _send_message_to_agent(from_agent_id, {
            "agent_name": "Bob",
            "message": "Please review the document",
            "msg_type": "notify",
        })

    assert "Notification sent to Bob" in result
    assert "asynchronously" in result
    mock_wake.assert_awaited_once()


@pytest.mark.asyncio
async def test_task_delegate_creates_focus_and_trigger():
    """task_delegate should create a focus item and an on_message trigger."""
    from app.services.agent_tools import _send_message_to_agent

    from_agent_id = uuid.uuid4()
    target_id = uuid.uuid4()
    rel_id = uuid.uuid4()
    session_id = uuid.uuid4()
    src_participant = _make_participant(ref_id=from_agent_id)
    tgt_participant = _make_participant(ref_id=target_id)
    source_agent = _make_agent(from_agent_id, name="Alice")
    target_agent = _make_agent(target_id, name="Bob")

    session = MagicMock()
    session.id = session_id
    session.last_message_at = None

    db = RecordingDB(responses=[
        DummyResult(scalar_value=source_agent),
        DummyResult(scalars_list=[target_agent]),
        DummyResult(scalar_value=rel_id),
        DummyResult(scalar_value=src_participant),
        DummyResult(scalar_value=tgt_participant),
        DummyResult(scalar_value=session),
        DummyResult(scalar_value=_make_tenant()),
    ])

    with patch("app.services.agent_tools.async_session") as mock_session_ctx, \
         patch("app.services.agent_tools._append_focus_item", new_callable=AsyncMock) as mock_focus, \
         patch("app.services.agent_tools._create_on_message_trigger", new_callable=AsyncMock) as mock_trigger, \
         patch("app.services.agent_tools._wake_agent_async", new_callable=AsyncMock) as mock_wake:

        mock_session_ctx.return_value.__aenter__ = AsyncMock(return_value=db)
        mock_session_ctx.return_value.__aexit__ = AsyncMock(return_value=False)

        result = await _send_message_to_agent(from_agent_id, {
            "agent_name": "Bob",
            "message": "Please prepare the Q3 report",
            "msg_type": "task_delegate",
        })

    assert "Task delegated to Bob" in result
    assert "notified when they complete" in result
    mock_focus.assert_awaited_once()
    mock_trigger.assert_awaited_once()
    mock_wake.assert_awaited_once()

    focus_call = mock_focus.call_args
    assert "wait_bob_task" in focus_call[0][1]
    assert "Bob" in focus_call[0][2]

    trigger_call = mock_trigger.call_args
    assert trigger_call[1]["from_agent_name"] == "Bob"
    assert trigger_call[1]["focus_ref"] == focus_call[0][1]


@pytest.mark.asyncio
async def test_consult_calls_llm_synchronously():
    """consult msg_type should call LLM synchronously and return reply."""
    from app.services.agent_tools import _send_message_to_agent

    from_agent_id = uuid.uuid4()
    target_id = uuid.uuid4()
    rel_id = uuid.uuid4()
    session_id = uuid.uuid4()
    model_id = uuid.uuid4()
    src_participant = _make_participant(ref_id=from_agent_id)
    tgt_participant = _make_participant(ref_id=target_id)
    source_agent = _make_agent(from_agent_id, name="Alice")
    target_agent = _make_agent(target_id, name="Bob", primary_model_id=model_id)

    session = MagicMock()
    session.id = session_id
    session.last_message_at = None

    model = MagicMock()
    model.provider = "openai"
    model.model = "gpt-4"
    model.api_key_encrypted = "sk-test"
    model.base_url = None
    model.temperature = 0.7
    model.request_timeout = 60

    response = MagicMock()
    response.content = "Here is the answer"
    response.tool_calls = None
    response.usage = None

    mock_llm_client = AsyncMock()
    mock_llm_client.complete = AsyncMock(return_value=response)
    mock_llm_client.close = AsyncMock()

    db = RecordingDB(responses=[
        DummyResult(scalar_value=source_agent),
        DummyResult(scalars_list=[target_agent]),
        DummyResult(scalar_value=rel_id),
        DummyResult(scalar_value=src_participant),
        DummyResult(scalar_value=tgt_participant),
        DummyResult(scalar_value=session),
        DummyResult(scalar_value=_make_tenant()),
        DummyResult(scalar_value=model),
        DummyResult(scalars_list=[]),
    ])

    db2 = RecordingDB(responses=[
        DummyResult(scalar_value=tgt_participant),
    ])

    call_count = 0
    session_dbs = [db, db2]

    async def mock_session_enter(self):
        nonlocal call_count
        result = session_dbs[min(call_count, len(session_dbs) - 1)]
        call_count += 1
        return result

    with patch("app.services.agent_tools.async_session") as mock_session_ctx, \
         patch("app.services.agent_context.build_agent_context", new_callable=AsyncMock, return_value=("static", "dynamic")), \
         patch("app.services.llm_utils.create_llm_client", return_value=mock_llm_client), \
         patch("app.services.agent_tools.get_agent_tools_for_llm", new_callable=AsyncMock, return_value=[]), \
         patch("app.services.llm_utils.get_provider_base_url", return_value="https://api.openai.com/v1"), \
         patch("app.services.token_tracker.record_token_usage", new_callable=AsyncMock), \
         patch("app.services.activity_logger.log_activity", new_callable=AsyncMock):

        mock_session_ctx.return_value.__aenter__ = AsyncMock(side_effect=[
            db,
            db2,
        ])
        mock_session_ctx.return_value.__aexit__ = AsyncMock(return_value=False)

        result = await _send_message_to_agent(from_agent_id, {
            "agent_name": "Bob",
            "message": "What is 2+2?",
            "msg_type": "consult",
        })

    assert "Bob replied" in result
    assert "Here is the answer" in result
    mock_llm_client.complete.assert_awaited()


@pytest.mark.asyncio
async def test_default_msg_type_is_notify():
    """When msg_type is not specified, it should default to notify."""
    from app.services.agent_tools import _send_message_to_agent

    from_agent_id = uuid.uuid4()
    target_id = uuid.uuid4()
    rel_id = uuid.uuid4()
    session_id = uuid.uuid4()
    src_participant = _make_participant(ref_id=from_agent_id)
    tgt_participant = _make_participant(ref_id=target_id)
    source_agent = _make_agent(from_agent_id, name="Alice")
    target_agent = _make_agent(target_id, name="Bob")

    session = MagicMock()
    session.id = session_id
    session.last_message_at = None

    db = RecordingDB(responses=[
        DummyResult(scalar_value=source_agent),
        DummyResult(scalars_list=[target_agent]),
        DummyResult(scalar_value=rel_id),
        DummyResult(scalar_value=src_participant),
        DummyResult(scalar_value=tgt_participant),
        DummyResult(scalar_value=session),
        DummyResult(scalar_value=_make_tenant()),
    ])

    with patch("app.services.agent_tools.async_session") as mock_session_ctx, \
         patch("app.services.agent_tools._wake_agent_async", new_callable=AsyncMock) as mock_wake:

        mock_session_ctx.return_value.__aenter__ = AsyncMock(return_value=db)
        mock_session_ctx.return_value.__aexit__ = AsyncMock(return_value=False)

        result = await _send_message_to_agent(from_agent_id, {
            "agent_name": "Bob",
            "message": "Heads up about the meeting",
        })

    assert "Notification sent" in result
    mock_wake.assert_awaited_once()


@pytest.mark.asyncio
async def test_missing_agent_name_returns_error():
    """Missing agent_name should return an error."""
    from app.services.agent_tools import _send_message_to_agent

    result = await _send_message_to_agent(uuid.uuid4(), {
        "agent_name": "",
        "message": "Hello",
    })

    assert "❌" in result


@pytest.mark.asyncio
async def test_no_relationship_returns_error():
    """No relationship between agents should return an error."""
    from app.services.agent_tools import _send_message_to_agent

    from_agent_id = uuid.uuid4()
    target_id = uuid.uuid4()
    source_agent = _make_agent(from_agent_id, name="Alice")
    target_agent = _make_agent(target_id, name="Bob")
    src_participant = _make_participant(ref_id=from_agent_id)
    tgt_participant = _make_participant(ref_id=target_id)

    db = RecordingDB(responses=[
        DummyResult(scalar_value=source_agent),
        DummyResult(scalars_list=[target_agent]),
        DummyResult(scalar_value=None),
        DummyResult(scalar_value=src_participant),
        DummyResult(scalar_value=tgt_participant),
    ])

    with patch("app.services.agent_tools.async_session") as mock_session_ctx:
        mock_session_ctx.return_value.__aenter__ = AsyncMock(return_value=db)
        mock_session_ctx.return_value.__aexit__ = AsyncMock(return_value=False)

        result = await _send_message_to_agent(from_agent_id, {
            "agent_name": "Bob",
            "message": "Hello",
            "msg_type": "notify",
        })

    assert "do not have a relationship" in result


@pytest.mark.asyncio
async def test_append_focus_item_creates_file(tmp_path):
    """_append_focus_item should create/append to focus.md."""
    from app.services.agent_tools import _append_focus_item, WORKSPACE_ROOT

    agent_id = uuid.uuid4()
    with patch("app.services.agent_tools.WORKSPACE_ROOT", tmp_path):
        await _append_focus_item(agent_id, "test_item", "Test description")

        focus_path = tmp_path / str(agent_id) / "focus.md"
        assert focus_path.exists()
        content = focus_path.read_text()
        assert "test_item" in content
        assert "Test description" in content
        assert "- [ ]" in content


@pytest.mark.asyncio
async def test_append_focus_item_no_duplicate(tmp_path):
    """_append_focus_item should not duplicate existing items."""
    from app.services.agent_tools import _append_focus_item

    agent_id = uuid.uuid4()
    focus_path = tmp_path / str(agent_id) / "focus.md"
    focus_path.parent.mkdir(parents=True, exist_ok=True)
    focus_path.write_text("# Focus\n\n- [ ] test_item: Existing description\n")

    with patch("app.services.agent_tools.WORKSPACE_ROOT", tmp_path):
        await _append_focus_item(agent_id, "test_item", "New description")

    content = focus_path.read_text()
    assert content.count("test_item") == 1


@pytest.mark.asyncio
async def test_create_on_message_trigger():
    """_create_on_message_trigger should create a trigger in DB."""
    from app.services.agent_tools import _create_on_message_trigger

    agent_id = uuid.uuid4()

    snap_db = RecordingDB(responses=[
        DummyResult(scalar_value=None),
    ])
    trigger_db = RecordingDB(responses=[
        DummyResult(scalar_value=None),
    ])

    enter_count = 0
    dbs = [snap_db, trigger_db]

    async def _enter():
        nonlocal enter_count
        db = dbs[min(enter_count, len(dbs) - 1)]
        enter_count += 1
        return db

    with patch("app.services.agent_tools.async_session") as mock_session_ctx:
        mock_session_ctx.return_value.__aenter__ = AsyncMock(side_effect=_enter)
        mock_session_ctx.return_value.__aexit__ = AsyncMock(return_value=False)

        await _create_on_message_trigger(
            agent_id=agent_id,
            trigger_name="test_trigger",
            from_agent_name="Bob",
            reason="Test reason",
            focus_ref="test_focus",
        )

    assert trigger_db.committed
    assert len(trigger_db.added) == 1

    trigger = trigger_db.added[0]
    assert trigger.name == "test_trigger"
    assert trigger.type == "on_message"
    assert trigger.config["from_agent_name"] == "Bob"
    assert trigger.reason == "Test reason"
    assert trigger.focus_ref == "test_focus"


@pytest.mark.asyncio
async def test_wake_agent_async_calls_trigger_daemon():
    """_wake_agent_async should delegate to trigger_daemon.wake_agent_with_context."""
    from app.services.agent_tools import _wake_agent_async

    agent_id = uuid.uuid4()
    context = "[From Alice] Hello Bob"

    with patch("app.services.trigger_daemon.wake_agent_with_context", new_callable=AsyncMock) as mock_wake:
        await _wake_agent_async(agent_id, context)
        mock_wake.assert_awaited_once_with(agent_id, context, from_agent_id=None, skip_dedup=False)


@pytest.mark.asyncio
async def test_openclaw_target_still_queues():
    """OpenClaw targets should still use the gateway queue regardless of msg_type."""
    from app.services.agent_tools import _send_message_to_agent

    from_agent_id = uuid.uuid4()
    target_id = uuid.uuid4()
    rel_id = uuid.uuid4()
    session_id = uuid.uuid4()
    src_participant = _make_participant(ref_id=from_agent_id)
    tgt_participant = _make_participant(ref_id=target_id)
    source_agent = _make_agent(from_agent_id, name="Alice")
    target_agent = _make_agent(target_id, name="OpenClawBot", agent_type="openclaw")
    target_agent.openclaw_last_seen = datetime.now(UTC)

    session = MagicMock()
    session.id = session_id
    session.last_message_at = None

    db = RecordingDB(responses=[
        DummyResult(scalar_value=source_agent),
        DummyResult(scalars_list=[target_agent]),
        DummyResult(scalar_value=rel_id),
        DummyResult(scalar_value=src_participant),
        DummyResult(scalar_value=tgt_participant),
        DummyResult(scalar_value=session),
    ])

    with patch("app.services.agent_tools.async_session") as mock_session_ctx, \
         patch("app.services.activity_logger.log_activity", new_callable=AsyncMock):

        mock_session_ctx.return_value.__aenter__ = AsyncMock(return_value=db)
        mock_session_ctx.return_value.__aexit__ = AsyncMock(return_value=False)

        result = await _send_message_to_agent(from_agent_id, {
            "agent_name": "OpenClawBot",
            "message": "Hello",
            "msg_type": "notify",
        })

    assert "OpenClaw agent" in result
    assert "queued" in result


@pytest.mark.asyncio
async def test_feature_flag_off_falls_back_to_consult():
    """When tenant a2a_async_enabled=False, notify and task_delegate fall back to consult."""
    from app.services.agent_tools import _send_message_to_agent

    from_agent_id = uuid.uuid4()
    target_id = uuid.uuid4()
    model_id = uuid.uuid4()
    rel_id = uuid.uuid4()
    session_id = uuid.uuid4()
    src_participant = _make_participant(ref_id=from_agent_id)
    tgt_participant = _make_participant(ref_id=target_id)
    source_agent = _make_agent(from_agent_id, name="Alice")
    source_agent.tenant_id = uuid.uuid4()
    target_agent = _make_agent(target_id, name="Bob", primary_model_id=model_id)

    tenant = MagicMock()
    tenant.a2a_async_enabled = False

    session = MagicMock()
    session.id = session_id
    session.last_message_at = None

    model = MagicMock()
    model.provider = "openai"
    model.model = "gpt-4"
    model.api_key_encrypted = "sk-test"
    model.base_url = None
    model.temperature = 0.7
    model.request_timeout = 60

    response = MagicMock()
    response.content = "Got it"
    response.tool_calls = None
    response.usage = None

    mock_llm_client = AsyncMock()
    mock_llm_client.complete = AsyncMock(return_value=response)
    mock_llm_client.close = AsyncMock()

    db = RecordingDB(responses=[
        DummyResult(scalar_value=source_agent),
        DummyResult(scalars_list=[target_agent]),
        DummyResult(scalar_value=rel_id),
        DummyResult(scalar_value=src_participant),
        DummyResult(scalar_value=tgt_participant),
        DummyResult(scalar_value=session),
        DummyResult(scalar_value=tenant),
        DummyResult(scalar_value=model),
        DummyResult(scalars_list=[]),
    ])

    db2 = RecordingDB(responses=[
        DummyResult(scalar_value=tgt_participant),
    ])

    with patch("app.services.agent_tools.async_session") as mock_session_ctx, \
         patch("app.services.agent_context.build_agent_context", new_callable=AsyncMock, return_value=("s", "d")), \
         patch("app.services.llm_utils.create_llm_client", return_value=mock_llm_client), \
         patch("app.services.agent_tools.get_agent_tools_for_llm", new_callable=AsyncMock, return_value=[]), \
         patch("app.services.llm_utils.get_provider_base_url", return_value="https://api.openai.com/v1"), \
         patch("app.services.token_tracker.record_token_usage", new_callable=AsyncMock), \
         patch("app.services.activity_logger.log_activity", new_callable=AsyncMock):

        mock_session_ctx.return_value.__aenter__ = AsyncMock(side_effect=[db, db2])
        mock_session_ctx.return_value.__aexit__ = AsyncMock(return_value=False)

        result = await _send_message_to_agent(from_agent_id, {
            "agent_name": "Bob",
            "message": "Hello",
            "msg_type": "notify",
        })

    assert "Bob replied" in result
    assert "Got it" in result


@pytest.mark.asyncio
async def test_feature_flag_on_uses_notify():
    """When tenant a2a_async_enabled=True, notify works normally."""
    from app.services.agent_tools import _send_message_to_agent

    from_agent_id = uuid.uuid4()
    target_id = uuid.uuid4()
    rel_id = uuid.uuid4()
    session_id = uuid.uuid4()
    src_participant = _make_participant(ref_id=from_agent_id)
    tgt_participant = _make_participant(ref_id=target_id)
    source_agent = _make_agent(from_agent_id, name="Alice")
    source_agent.tenant_id = uuid.uuid4()
    target_agent = _make_agent(target_id, name="Bob")

    tenant = MagicMock()
    tenant.a2a_async_enabled = True

    session = MagicMock()
    session.id = session_id
    session.last_message_at = None

    db = RecordingDB(responses=[
        DummyResult(scalar_value=source_agent),
        DummyResult(scalars_list=[target_agent]),
        DummyResult(scalar_value=rel_id),
        DummyResult(scalar_value=src_participant),
        DummyResult(scalar_value=tgt_participant),
        DummyResult(scalar_value=session),
        DummyResult(scalar_value=tenant),
    ])

    with patch("app.services.agent_tools.async_session") as mock_session_ctx, \
         patch("app.services.agent_tools._wake_agent_async", new_callable=AsyncMock) as mock_wake:

        mock_session_ctx.return_value.__aenter__ = AsyncMock(return_value=db)
        mock_session_ctx.return_value.__aexit__ = AsyncMock(return_value=False)

        result = await _send_message_to_agent(from_agent_id, {
            "agent_name": "Bob",
            "message": "Hello",
            "msg_type": "notify",
        })

    assert "Notification sent" in result
    mock_wake.assert_awaited_once()
