"""Focused tests for the external channel delivery outbox worker."""

from collections import deque
from datetime import UTC, datetime
import uuid

import pytest

from app.config import Settings
from app.models.agent_run import AgentRun
from app.models.agent_run_event import AgentRunEvent
from app.models.audit import ChatMessage
from app.models.channel_delivery import ChannelDelivery
from app.models.chat_session import ChatSession
from app.services.agent_runtime.channel_delivery import (
    ChannelDeliveryWorker,
    ChannelSendResult,
    stage_channel_delivery,
)


NOW = datetime(2026, 7, 14, 16, 0, tzinfo=UTC)


class _Result:
    def __init__(self, value=None) -> None:
        self.value = value

    def scalar_one_or_none(self):
        return self.value


class _Session:
    def __init__(self, *values) -> None:
        self.values = deque(values)
        self.added = []
        self.commits = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False

    async def execute(self, _statement):
        if not self.values:
            raise AssertionError("unexpected query")
        return _Result(self.values.popleft())

    def add(self, value) -> None:
        self.added.append(value)

    async def commit(self) -> None:
        self.commits += 1


class _Factory:
    def __init__(self, *sessions: _Session) -> None:
        self.sessions = deque(sessions)

    def __call__(self):
        if not self.sessions:
            raise AssertionError("unexpected session")
        return self.sessions.popleft()


class _Sender:
    def __init__(self, *, error: Exception | None = None) -> None:
        self.error = error
        self.envelopes = []

    async def send(self, envelope):
        self.envelopes.append(envelope)
        if self.error is not None:
            raise self.error
        return ChannelSendResult(provider_message_id="provider-1")


def _entities(*, attempt_count: int = 0):
    tenant_id = uuid.uuid4()
    agent_id = uuid.uuid4()
    session = ChatSession(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        session_type="direct",
        agent_id=agent_id,
        user_id=uuid.uuid4(),
        title="External",
        source_channel="slack",
        external_conv_id="slack_D123",
        is_group=False,
        is_primary=False,
    )
    run_id = uuid.uuid4()
    run = AgentRun(
        id=run_id,
        tenant_id=tenant_id,
        agent_id=agent_id,
        session_id=session.id,
        source_type="chat",
        goal="Reply",
        run_kind="foreground",
        model_id=uuid.uuid4(),
        runtime_type="langgraph",
        runtime_thread_id=str(run_id),
        graph_name="runtime",
        graph_version="v1",
        delivery_status="pending",
        delivery_target={
            "kind": "direct",
            "session_id": str(session.id),
            "channel_delivery": {
                "version": 1,
                "channel": "slack",
                "target": {"channel_id": "D123"},
            },
        },
    )
    message = ChatMessage(
        id=uuid.uuid4(),
        agent_id=agent_id,
        user_id=session.user_id,
        role="assistant",
        content="Durable reply",
        conversation_id=str(session.id),
        mentions=[],
    )
    delivery = ChannelDelivery(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        run_id=run_id,
        agent_id=agent_id,
        session_id=session.id,
        message_id=message.id,
        channel="slack",
        target={"channel_id": "D123"},
        idempotency_key=f"run:{run_id}:terminal:completed",
        status="pending",
        attempt_count=attempt_count,
        next_attempt_at=NOW,
        created_at=NOW,
        updated_at=NOW,
    )
    return run, session, message, delivery


def test_stage_channel_delivery_is_in_the_chat_message_transaction() -> None:
    run, session, message, _delivery = _entities()
    db = _Session()

    staged = stage_channel_delivery(
        db,
        run=run,
        session=session,
        message_id=message.id,
        idempotency_key=f"run:{run.id}:terminal:completed",
        clock=lambda: NOW,
    )

    assert staged is not None
    assert staged in db.added
    assert staged.channel == "slack"
    assert staged.target == {"channel_id": "D123"}
    assert staged.status == "pending"
    assert staged.attempt_count == 0


@pytest.mark.asyncio
async def test_worker_delivers_without_touching_graph_state() -> None:
    run, _session, message, delivery = _entities()
    claim = _Session(delivery, message)
    complete = _Session(delivery, delivery.id, run)
    sender = _Sender()
    worker = ChannelDeliveryWorker(
        session_factory=_Factory(claim, complete),  # type: ignore[arg-type]
        sender=sender,
        claimant="worker-1",
        settings=Settings(AGENT_RUNTIME_CHANNEL_DELIVERY_MAX_ATTEMPTS=3),
        clock=lambda: NOW,
    )

    result = await worker.run_once()

    assert result.status == "delivered"
    assert delivery.status == "delivered"
    assert delivery.provider_message_id == "provider-1"
    assert delivery.attempt_count == 1
    assert run.delivery_status == "delivered"
    assert claim.commits == complete.commits == 1
    events = [item for item in complete.added if isinstance(item, AgentRunEvent)]
    assert len(events) == 1
    assert events[0].event_type == "channel_delivery_delivered"
    assert not hasattr(delivery, "checkpoint_id")
    assert not hasattr(delivery, "next_node")


@pytest.mark.asyncio
async def test_worker_retries_provider_failure_without_resuming_run() -> None:
    run, _session, message, delivery = _entities()
    claim = _Session(delivery, message)
    failed = _Session(delivery)
    sender = _Sender(error=RuntimeError("POST https://secret.example failed Bearer token-1"))
    worker = ChannelDeliveryWorker(
        session_factory=_Factory(claim, failed),  # type: ignore[arg-type]
        sender=sender,
        claimant="worker-1",
        settings=Settings(AGENT_RUNTIME_CHANNEL_DELIVERY_MAX_ATTEMPTS=3),
        clock=lambda: NOW,
    )

    result = await worker.run_once()

    assert result.status == "retry"
    assert delivery.status == "pending"
    assert delivery.attempt_count == 1
    assert delivery.claimed_by is None
    assert "secret.example" not in (delivery.last_error or "")
    assert "token-1" not in (delivery.last_error or "")
    assert run.delivery_status == "pending"
    assert failed.added == []


@pytest.mark.asyncio
async def test_worker_marks_only_the_latest_delivery_failed_after_max_attempts() -> None:
    run, _session, message, delivery = _entities(attempt_count=2)
    claim = _Session(delivery, message)
    failed = _Session(delivery, delivery.id, run)
    sender = _Sender(error=RuntimeError("provider unavailable"))
    worker = ChannelDeliveryWorker(
        session_factory=_Factory(claim, failed),  # type: ignore[arg-type]
        sender=sender,
        claimant="worker-1",
        settings=Settings(AGENT_RUNTIME_CHANNEL_DELIVERY_MAX_ATTEMPTS=3),
        clock=lambda: NOW,
    )

    result = await worker.run_once()

    assert result.status == "failed"
    assert delivery.status == "failed"
    assert delivery.attempt_count == 3
    assert run.delivery_status == "failed"
    events = [item for item in failed.added if isinstance(item, AgentRunEvent)]
    assert len(events) == 1
    assert events[0].event_type == "channel_delivery_failed"
