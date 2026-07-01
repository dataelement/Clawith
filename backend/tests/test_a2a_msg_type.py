"""Tests for async A2A msg_type differentiation (notify/consult/task_delegate).

Validates the branching logic in _send_message_to_agent:
- notify:    fire-and-forget, returns immediately
- task_delegate: async with callback, creates focus + trigger
- consult:   synchronous request-response (original behaviour)
"""

import json
import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ── Helpers ──────────────────────────────────────────────────────────

DEFAULT_TENANT_ID = uuid.uuid4()

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


def _make_agent(
    agent_id=None,
    name="TestAgent",
    tenant_id=None,
    agent_type="native",
    expired=False,
    primary_model_id=None,
    access_mode="company",
    status="running",
):
    agent = MagicMock()
    agent.id = agent_id or uuid.uuid4()
    agent.name = name
    agent.tenant_id = tenant_id or DEFAULT_TENANT_ID
    agent.agent_type = agent_type
    agent.status = status
    agent.is_expired = expired
    agent.expires_at = None
    agent.creator_id = uuid.uuid4()
    agent.primary_model_id = primary_model_id
    agent.fallback_model_id = None
    agent.role_description = ""
    agent.max_tool_rounds = 50
    agent.access_mode = access_mode
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

    db = RecordingDB(
        responses=[
            DummyResult(scalar_value=source_agent),
            DummyResult(scalar_value=target_agent),
            DummyResult(scalar_value=src_participant),
            DummyResult(scalar_value=tgt_participant),
            DummyResult(scalar_value=session),
            DummyResult(scalar_value=_make_tenant()),
        ]
    )

    with (
        patch("app.services.agent_tools.async_session") as mock_session_ctx,
        patch("app.services.agent_tools._wake_agent_async", new_callable=AsyncMock) as mock_wake,
    ):
        mock_session_ctx.return_value.__aenter__ = AsyncMock(return_value=db)
        mock_session_ctx.return_value.__aexit__ = AsyncMock(return_value=False)

        result = await _send_message_to_agent(
            from_agent_id,
            {
                "target_agent_id": str(target_id),
                "message": "Please review the document",
                "msg_type": "notify",
            },
        )

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

    db = RecordingDB(
        responses=[
            DummyResult(scalar_value=source_agent),
            DummyResult(scalar_value=target_agent),
            DummyResult(scalar_value=src_participant),
            DummyResult(scalar_value=tgt_participant),
            DummyResult(scalar_value=session),
            DummyResult(scalar_value=_make_tenant()),
        ]
    )

    with (
        patch("app.services.agent_tools.async_session") as mock_session_ctx,
        patch("app.services.agent_tools._append_focus_item", new_callable=AsyncMock) as mock_focus,
        patch("app.services.agent_tools._create_on_message_trigger", new_callable=AsyncMock) as mock_trigger,
        patch("app.services.agent_tools._wake_agent_async", new_callable=AsyncMock) as mock_wake,
    ):
        mock_session_ctx.return_value.__aenter__ = AsyncMock(return_value=db)
        mock_session_ctx.return_value.__aexit__ = AsyncMock(return_value=False)

        result = await _send_message_to_agent(
            from_agent_id,
            {
                "target_agent_id": str(target_id),
                "message": "Please prepare the Q3 report",
                "msg_type": "task_delegate",
            },
        )

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
    response.content = ""
    response.tool_calls = [
        {
            "id": "call_finish",
            "type": "function",
            "function": {
                "name": "finish",
                "arguments": json.dumps({"content": "Here is the answer"}),
            },
        }
    ]
    response.usage = None

    mock_llm_client = AsyncMock()
    mock_llm_client.complete = AsyncMock(return_value=response)
    mock_llm_client.stream = AsyncMock(return_value=response)
    mock_llm_client.close = AsyncMock()

    db = RecordingDB(
        responses=[
            DummyResult(scalar_value=source_agent),
            DummyResult(scalar_value=target_agent),
            DummyResult(scalar_value=src_participant),
            DummyResult(scalar_value=tgt_participant),
            DummyResult(scalar_value=session),
            DummyResult(scalar_value=_make_tenant()),
            DummyResult(scalar_value=model),
            DummyResult(scalars_list=[]),
        ]
    )

    db2 = RecordingDB(
        responses=[
            DummyResult(scalar_value=tgt_participant),
        ]
    )

    call_count = 0
    session_dbs = [db, db2]

    async def mock_session_enter(self):
        nonlocal call_count
        result = session_dbs[min(call_count, len(session_dbs) - 1)]
        call_count += 1
        return result

    with (
        patch("app.services.agent_tools.async_session") as mock_session_ctx,
        patch(
            "app.services.agent_context.build_agent_context", new_callable=AsyncMock, return_value=("static", "dynamic")
        ),
        patch("app.services.llm.caller.create_llm_client", return_value=mock_llm_client),
        patch("app.services.agent_tools.get_agent_tools_for_llm", new_callable=AsyncMock, return_value=[]),
        patch("app.services.llm.get_provider_base_url", return_value="https://api.openai.com/v1"),
        patch("app.services.token_tracker.record_token_usage", new_callable=AsyncMock),
        patch("app.services.activity_logger.log_activity", new_callable=AsyncMock),
    ):
        mock_session_ctx.return_value.__aenter__ = AsyncMock(
            side_effect=[
                db,
                db2,
            ]
        )
        mock_session_ctx.return_value.__aexit__ = AsyncMock(return_value=False)

        result = await _send_message_to_agent(
            from_agent_id,
            {
                "target_agent_id": str(target_id),
                "message": "What is 2+2?",
                "msg_type": "consult",
            },
        )

    assert "Bob replied" in result
    assert "Here is the answer" in result
    mock_llm_client.stream.assert_awaited()


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

    db = RecordingDB(
        responses=[
            DummyResult(scalar_value=source_agent),
            DummyResult(scalar_value=target_agent),
            DummyResult(scalar_value=src_participant),
            DummyResult(scalar_value=tgt_participant),
            DummyResult(scalar_value=session),
            DummyResult(scalar_value=_make_tenant()),
        ]
    )

    with (
        patch("app.services.agent_tools.async_session") as mock_session_ctx,
        patch("app.services.agent_tools._wake_agent_async", new_callable=AsyncMock) as mock_wake,
    ):
        mock_session_ctx.return_value.__aenter__ = AsyncMock(return_value=db)
        mock_session_ctx.return_value.__aexit__ = AsyncMock(return_value=False)

        result = await _send_message_to_agent(
            from_agent_id,
            {
                "target_agent_id": str(target_id),
                "message": "Heads up about the meeting",
            },
        )

    assert "Notification sent" in result
    mock_wake.assert_awaited_once()


@pytest.mark.asyncio
async def test_missing_target_agent_id_returns_error():
    """Missing target_agent_id should return an error."""
    from app.services.agent_tools import _send_message_to_agent

    result = await _send_message_to_agent(
        uuid.uuid4(),
        {
            "target_agent_id": "",
            "message": "Hello",
        },
    )

    assert "❌" in result


@pytest.mark.asyncio
async def test_legacy_agent_name_returns_roster_routing_error():
    """Memory or old tasks using agent_name must be routed back through query_directory."""
    from app.services.agent_tools import _send_message_to_agent

    result = await _send_message_to_agent(
        uuid.uuid4(),
        {
            "agent_name": "Native Research Partner",
            "message": "Hello",
            "msg_type": "notify",
        },
    )

    assert "agent_name is no longer supported" in result
    assert "query_directory" in result
    assert "target_agent_id" in result


def test_company_auto_contact_helper_rejects_non_company_boundaries():
    """Phase-1 company auto-contact only applies inside tenant, not self, and not expired."""
    from app.core.permissions import can_auto_contact_company_agent

    tenant_id = uuid.uuid4()
    source = _make_agent(tenant_id=tenant_id, access_mode="company")
    company_target = _make_agent(tenant_id=tenant_id, access_mode="company")
    custom_target = _make_agent(tenant_id=tenant_id, access_mode="custom")
    foreign_target = _make_agent(tenant_id=uuid.uuid4(), access_mode="company")
    expired_target = _make_agent(tenant_id=tenant_id, access_mode="company", expired=True)
    stopped_target = _make_agent(tenant_id=tenant_id, access_mode="company", status="stopped")

    assert can_auto_contact_company_agent(source, company_target) is True
    assert can_auto_contact_company_agent(source, source) is False
    assert can_auto_contact_company_agent(source, custom_target) is False
    assert can_auto_contact_company_agent(source, foreign_target) is False
    assert can_auto_contact_company_agent(source, expired_target) is False
    assert can_auto_contact_company_agent(source, stopped_target) is False


@pytest.mark.asyncio
async def test_relationship_prompt_excludes_company_agent_without_relationship():
    """Digital employees should be discovered through query_directory, not preloaded prompt lists."""
    from app.services.agent_context import _load_relationships_from_db

    tenant_id = uuid.uuid4()
    source_agent = _make_agent(tenant_id=tenant_id, name="Alice", access_mode="company")
    company_agent = _make_agent(tenant_id=tenant_id, name="Bob", access_mode="company")
    company_agent.role_description = "Backend helper"

    db = RecordingDB(
        responses=[
            DummyResult(values=[]),
        ]
    )

    relationships = await _load_relationships_from_db(db, source_agent.id)

    assert "数字员工同事" not in relationships
    assert "Bob" not in relationships
    assert "Backend helper" not in relationships


@pytest.mark.asyncio
async def test_relationship_prompt_keeps_human_notes_without_send_entry():
    """Human relationship notes stay context-only while sending goes through query_directory."""
    from app.services.agent_context import _load_relationships_from_db

    tenant_id = uuid.uuid4()
    source_agent = _make_agent(tenant_id=tenant_id, name="Alice", access_mode="company")
    member = MagicMock()
    member.name = "张三"
    member.title = "产品经理"
    member.status = "active"
    member.tenant_id = tenant_id
    member.user_id = None
    rel = MagicMock()
    rel.agent_id = source_agent.id
    rel.member_id = uuid.uuid4()
    rel.member = member
    rel.relation = "collaborator"
    rel.description = "负责产品需求"

    db = RecordingDB(
        responses=[
            DummyResult(values=[(rel, "飞书", "feishu")]),
            DummyResult(scalar_value=source_agent),
        ]
    )

    relationships = await _load_relationships_from_db(db, source_agent.id)

    assert "## 人类同事背景" not in relationships
    assert "## 人类协作备注" in relationships
    assert "不是联系人或发送入口" in relationships
    assert "query_directory" in relationships
    assert "张三" in relationships
    assert "负责产品需求" in relationships
    assert "数字员工同事" not in relationships
    assert "send_feishu_message" not in relationships


@pytest.mark.asyncio
async def test_company_agent_without_relationship_can_notify():
    """Company agents can be contacted without AgentAgentRelationship in phase 1."""
    from app.services.agent_tools import _send_message_to_agent

    tenant_id = uuid.uuid4()
    from_agent_id = uuid.uuid4()
    target_id = uuid.uuid4()
    session_id = uuid.uuid4()
    src_participant = _make_participant(ref_id=from_agent_id)
    tgt_participant = _make_participant(ref_id=target_id)
    source_agent = _make_agent(from_agent_id, name="Alice", tenant_id=tenant_id)
    target_agent = _make_agent(target_id, name="Bob", tenant_id=tenant_id, access_mode="company")

    session = MagicMock()
    session.id = session_id
    session.last_message_at = None

    db = RecordingDB(
        responses=[
            DummyResult(scalar_value=source_agent),
            DummyResult(scalar_value=target_agent),
            DummyResult(scalar_value=src_participant),
            DummyResult(scalar_value=tgt_participant),
            DummyResult(scalar_value=session),
            DummyResult(scalar_value=_make_tenant()),
        ]
    )

    with (
        patch("app.services.agent_tools.async_session") as mock_session_ctx,
        patch("app.services.agent_tools._wake_agent_async", new_callable=AsyncMock) as mock_wake,
    ):
        mock_session_ctx.return_value.__aenter__ = AsyncMock(return_value=db)
        mock_session_ctx.return_value.__aexit__ = AsyncMock(return_value=False)

        result = await _send_message_to_agent(
            from_agent_id,
            {
                "target_agent_id": str(target_id),
                "message": "Hello",
                "msg_type": "notify",
            },
        )

    assert "Notification sent to Bob" in result
    mock_wake.assert_awaited_once()


@pytest.mark.asyncio
async def test_gateway_company_agent_without_relationship_queues_message():
    """Gateway send-message can target company agents without explicit A2A rows."""
    from app.api.gateway import send_message
    from app.schemas.schemas import GatewaySendMessageRequest

    tenant_id = uuid.uuid4()
    source_id = uuid.uuid4()
    target_id = uuid.uuid4()
    source_agent = _make_agent(source_id, name="Alice", tenant_id=tenant_id, agent_type="openclaw")
    target_agent = _make_agent(
        target_id,
        name="Bob",
        tenant_id=tenant_id,
        agent_type="openclaw",
        access_mode="company",
    )
    db = RecordingDB(
        responses=[
            DummyResult(scalars_list=[target_agent]),
            DummyResult(scalars_list=[]),
        ]
    )

    with patch("app.api.gateway._get_agent_by_key", new_callable=AsyncMock, return_value=source_agent):
        result = await send_message(
            GatewaySendMessageRequest(target="Bob", content="Hello"),
            x_api_key="test-key",
            db=db,
        )

    assert result["status"] == "accepted"
    assert result["target"] == "Bob"
    assert result["type"] == "openclaw_agent"
    assert db.committed is True
    assert len(db.added) == 1
    queued = db.added[0]
    assert queued.agent_id == target_id
    assert queued.sender_agent_id == source_id
    assert queued.content == "Hello"
    assert queued.status == "pending"


@pytest.mark.asyncio
async def test_invisible_target_returns_error():
    """Invisible target agents should return an error."""
    from app.services.agent_tools import _send_message_to_agent

    from_agent_id = uuid.uuid4()
    target_id = uuid.uuid4()
    tenant_id = uuid.uuid4()
    source_agent = _make_agent(from_agent_id, name="Alice", tenant_id=tenant_id, access_mode="company")
    target_agent = _make_agent(target_id, name="Bob", tenant_id=tenant_id, access_mode="private")

    db = RecordingDB(
        responses=[
            DummyResult(scalar_value=source_agent),
            DummyResult(scalar_value=target_agent),
        ]
    )

    with patch("app.services.agent_tools.async_session") as mock_session_ctx:
        mock_session_ctx.return_value.__aenter__ = AsyncMock(return_value=db)
        mock_session_ctx.return_value.__aexit__ = AsyncMock(return_value=False)

        result = await _send_message_to_agent(
            from_agent_id,
            {
                "target_agent_id": str(target_id),
                "message": "Hello",
                "msg_type": "notify",
            },
        )

    assert "not visible" in result


@pytest.mark.asyncio
async def test_append_focus_item_success():
    """_append_focus_item should call ensure_focus_item."""
    from app.services.agent_tools import _append_focus_item

    agent_id = uuid.uuid4()
    with patch("app.services.agent_tools.ensure_focus_item", new_callable=AsyncMock) as mock_ensure:
        await _append_focus_item(agent_id, "test_item", "Test description")
        mock_ensure.assert_awaited_once_with(agent_id, focus_ref="test_item", description="Test description")


@pytest.mark.asyncio
async def test_create_on_message_trigger():
    """_create_on_message_trigger should create a trigger in DB."""
    from app.services.agent_tools import _create_on_message_trigger

    agent_id = uuid.uuid4()

    snap_db = RecordingDB(
        responses=[
            DummyResult(scalar_value=None),
        ]
    )
    trigger_db = RecordingDB(
        responses=[
            DummyResult(scalar_value=None),
        ]
    )

    enter_count = 0
    dbs = [snap_db, trigger_db]

    async def _enter():
        nonlocal enter_count
        db = dbs[min(enter_count, len(dbs) - 1)]
        enter_count += 1
        return db

    with (
        patch("app.services.agent_tools.async_session") as mock_session_ctx,
        patch("app.services.agent_tools.ensure_focus_item", new_callable=AsyncMock) as mock_ensure,
    ):
        mock_ensure.return_value = "test_focus"
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
async def test_create_on_message_trigger_resets_fire_count():
    """_create_on_message_trigger should reset fire_count to 0 for an existing trigger."""
    from app.services.agent_tools import _create_on_message_trigger
    from app.models.trigger import AgentTrigger

    agent_id = uuid.uuid4()

    existing_trigger = AgentTrigger(
        agent_id=agent_id,
        name="test_trigger",
        type="on_message",
        config={"from_agent_name": "Bob"},
        reason="Old reason",
        focus_ref="old_focus",
        is_enabled=False,
        fire_count=1,
        max_fires=1,
    )

    snap_db = RecordingDB(
        responses=[
            DummyResult(scalar_value=None),
        ]
    )
    trigger_db = RecordingDB(
        responses=[
            DummyResult(scalar_value=existing_trigger),
        ]
    )

    enter_count = 0
    dbs = [snap_db, trigger_db]

    async def _enter():
        nonlocal enter_count
        db = dbs[min(enter_count, len(dbs) - 1)]
        enter_count += 1
        return db

    with (
        patch("app.services.agent_tools.async_session") as mock_session_ctx,
        patch("app.services.agent_tools.ensure_focus_item", new_callable=AsyncMock) as mock_ensure,
    ):
        mock_ensure.return_value = "new_focus"
        mock_session_ctx.return_value.__aenter__ = AsyncMock(side_effect=_enter)
        mock_session_ctx.return_value.__aexit__ = AsyncMock(return_value=False)

        await _create_on_message_trigger(
            agent_id=agent_id,
            trigger_name="test_trigger",
            from_agent_name="Bob",
            reason="New reason",
            focus_ref="new_focus",
        )

    assert trigger_db.committed
    assert existing_trigger.is_enabled is True
    assert existing_trigger.fire_count == 0
    assert existing_trigger.reason == "New reason"
    assert existing_trigger.focus_ref == "new_focus"


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

    db = RecordingDB(
        responses=[
            DummyResult(scalar_value=source_agent),
            DummyResult(scalar_value=target_agent),
            DummyResult(scalar_value=src_participant),
            DummyResult(scalar_value=tgt_participant),
            DummyResult(scalar_value=session),
        ]
    )

    with (
        patch("app.services.agent_tools.async_session") as mock_session_ctx,
        patch("app.services.activity_logger.log_activity", new_callable=AsyncMock),
    ):
        mock_session_ctx.return_value.__aenter__ = AsyncMock(return_value=db)
        mock_session_ctx.return_value.__aexit__ = AsyncMock(return_value=False)

        result = await _send_message_to_agent(
            from_agent_id,
            {
                "target_agent_id": str(target_id),
                "message": "Hello",
                "msg_type": "notify",
            },
        )

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
    tenant_id = uuid.uuid4()
    source_agent.tenant_id = tenant_id
    target_agent = _make_agent(target_id, name="Bob", tenant_id=tenant_id, primary_model_id=model_id)

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
    response.content = ""
    response.tool_calls = [
        {
            "id": "call_finish",
            "type": "function",
            "function": {
                "name": "finish",
                "arguments": json.dumps({"content": "Got it"}),
            },
        }
    ]
    response.usage = None

    mock_llm_client = AsyncMock()
    mock_llm_client.complete = AsyncMock(return_value=response)
    mock_llm_client.stream = AsyncMock(return_value=response)
    mock_llm_client.close = AsyncMock()

    db = RecordingDB(
        responses=[
            DummyResult(scalar_value=source_agent),
            DummyResult(scalar_value=target_agent),
            DummyResult(scalar_value=src_participant),
            DummyResult(scalar_value=tgt_participant),
            DummyResult(scalar_value=session),
            DummyResult(scalar_value=tenant),
            DummyResult(scalar_value=model),
            DummyResult(scalars_list=[]),
        ]
    )

    db2 = RecordingDB(
        responses=[
            DummyResult(scalar_value=tgt_participant),
        ]
    )

    with (
        patch("app.services.agent_tools.async_session") as mock_session_ctx,
        patch("app.services.agent_context.build_agent_context", new_callable=AsyncMock, return_value=("s", "d")),
        patch("app.services.llm.caller.create_llm_client", return_value=mock_llm_client),
        patch("app.services.agent_tools.get_agent_tools_for_llm", new_callable=AsyncMock, return_value=[]),
        patch("app.services.llm.get_provider_base_url", return_value="https://api.openai.com/v1"),
        patch("app.services.token_tracker.record_token_usage", new_callable=AsyncMock),
        patch("app.services.activity_logger.log_activity", new_callable=AsyncMock),
    ):
        mock_session_ctx.return_value.__aenter__ = AsyncMock(side_effect=[db, db2])
        mock_session_ctx.return_value.__aexit__ = AsyncMock(return_value=False)

        result = await _send_message_to_agent(
            from_agent_id,
            {
                "target_agent_id": str(target_id),
                "message": "Hello",
                "msg_type": "notify",
            },
        )

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
    tenant_id = uuid.uuid4()
    source_agent.tenant_id = tenant_id
    target_agent = _make_agent(target_id, name="Bob", tenant_id=tenant_id)

    tenant = MagicMock()
    tenant.a2a_async_enabled = True

    session = MagicMock()
    session.id = session_id
    session.last_message_at = None

    db = RecordingDB(
        responses=[
            DummyResult(scalar_value=source_agent),
            DummyResult(scalar_value=target_agent),
            DummyResult(scalar_value=src_participant),
            DummyResult(scalar_value=tgt_participant),
            DummyResult(scalar_value=session),
            DummyResult(scalar_value=tenant),
        ]
    )

    with (
        patch("app.services.agent_tools.async_session") as mock_session_ctx,
        patch("app.services.agent_tools._wake_agent_async", new_callable=AsyncMock) as mock_wake,
    ):
        mock_session_ctx.return_value.__aenter__ = AsyncMock(return_value=db)
        mock_session_ctx.return_value.__aexit__ = AsyncMock(return_value=False)

        result = await _send_message_to_agent(
            from_agent_id,
            {
                "target_agent_id": str(target_id),
                "message": "Hello",
                "msg_type": "notify",
            },
        )

    assert "Notification sent" in result
    mock_wake.assert_awaited_once()


@pytest.mark.asyncio
async def test_handle_set_trigger_resets_fire_count():
    """_handle_set_trigger should reset fire_count to 0 if it has reached max_fires when re-enabling."""
    from app.services.agent_tools import _handle_set_trigger
    from app.models.trigger import AgentTrigger

    agent_id = uuid.uuid4()
    agent_mock = MagicMock()
    agent_mock.max_triggers = 10

    existing_trigger = AgentTrigger(
        agent_id=agent_id,
        name="test_trigger",
        type="once",
        config={"at": "2026-03-10T09:00:00+08:00"},
        reason="Old reason",
        focus_ref="old_focus",
        is_enabled=False,
        fire_count=1,
        max_fires=1,
    )

    db = RecordingDB(
        responses=[
            DummyResult(scalar_value=agent_mock),  # Load agent to get per-agent trigger limit
            DummyResult(scalar_value=0),  # Check max triggers (count)
            DummyResult(scalar_value=existing_trigger),  # Check for duplicate name
        ]
    )

    with (
        patch("app.services.agent_tools.async_session") as mock_session_ctx,
        patch("app.services.agent_tools.ensure_focus_item", new_callable=AsyncMock) as mock_ensure,
    ):
        mock_ensure.return_value = "new_focus"
        mock_session_ctx.return_value.__aenter__ = AsyncMock(return_value=db)
        mock_session_ctx.return_value.__aexit__ = AsyncMock(return_value=False)

        arguments = {
            "name": "test_trigger",
            "type": "once",
            "config": {"at": "2026-03-10T09:00:00+08:00"},
            "reason": "New reason",
            "focus_ref": "new_focus",
        }

        result = await _handle_set_trigger(agent_id, arguments)

    assert "re-enabled" in result
    assert existing_trigger.is_enabled is True
    assert existing_trigger.fire_count == 0
    assert existing_trigger.reason == "New reason"


@pytest.mark.asyncio
async def test_execute_tool_failure_writes_system_message():
    """execute_tool should write a system error message to the session if a messaging tool fails."""
    from app.services.agent_tools import execute_tool

    agent_id = uuid.uuid4()
    user_id = uuid.uuid4()
    session_id = str(uuid.uuid4())

    tenant_id = uuid.uuid4()
    db = RecordingDB(
        responses=[
            DummyResult(scalar_value=tenant_id),  # tenant_id
            DummyResult(scalar_value=None),  # query in _send_channel_message (returns empty -> fails)
        ]
    )

    with (
        patch("app.services.agent_tools.async_session") as mock_session_ctx,
        patch("app.services.activity_logger.log_activity", new_callable=AsyncMock),
    ):
        mock_session_ctx.return_value.__aenter__ = AsyncMock(return_value=db)
        mock_session_ctx.return_value.__aexit__ = AsyncMock(return_value=False)

        args = {
            "member_name": "hi",
            "message": "Hello from Ray",
        }

        result = await execute_tool(
            "send_channel_message",
            args,
            agent_id=agent_id,
            user_id=user_id,
            session_id=session_id,
        )

    assert result.startswith("❌")
    assert db.committed
    assert len(db.added) == 1

    error_msg = db.added[0]
    assert error_msg.conversation_id == session_id
    assert error_msg.role == "assistant"
    assert "系统提示" in error_msg.content
    assert "send_channel_message" in error_msg.content
