"""OpenClaw gateway cutover tests for native A2A Runtime execution."""

from __future__ import annotations

from collections import deque
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
import uuid

import pytest

from app.api import gateway
from app.models.agent import Agent
from app.models.audit import ChatMessage
from app.models.gateway_message import GatewayMessage
from app.schemas.schemas import GatewayReportRequest, GatewaySendMessageRequest
from app.services.agent_runtime.a2a_runtime import (
    GatewayA2ARuntimeCompletion,
    GatewayA2ARuntimeIntake,
)


class _Scalars:
    def __init__(self, values: list[object]) -> None:
        self.values = values

    def all(self) -> list[object]:
        return self.values

    def first(self):
        return self.values[0] if self.values else None


class _Result:
    def __init__(self, values: list[object]) -> None:
        self.values = values

    def scalars(self) -> _Scalars:
        return _Scalars(self.values)

    def scalar_one_or_none(self):
        return self.values[0] if self.values else None


class _Session:
    def __init__(self, *results: object) -> None:
        self.results = deque(results)
        self.commits = 0
        self.rollbacks = 0

    async def execute(self, _statement) -> _Result:
        value = self.results.popleft()
        return _Result([] if value is None else [value])

    async def commit(self) -> None:
        self.commits += 1

    async def rollback(self) -> None:
        self.rollbacks += 1


class _ReportSession:
    def __init__(self, *results: object) -> None:
        self.results = deque(results)
        self.added: list[object] = []
        self.commits = 0
        self.rollbacks = 0

    async def execute(self, _statement) -> _Result:
        value = self.results.popleft()
        return _Result([] if value is None else [value])

    async def get(self, _model, _identity):
        return None

    def add(self, value: object) -> None:
        self.added.append(value)

    async def commit(self) -> None:
        self.commits += 1

    async def rollback(self) -> None:
        self.rollbacks += 1


@pytest.mark.asyncio
async def test_gateway_native_agent_message_commits_runtime_before_acceptance() -> None:
    tenant_id = uuid.uuid4()
    source = Agent(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        creator_id=uuid.uuid4(),
        name="OpenClaw Coordinator",
        status="running",
        is_expired=False,
        agent_type="openclaw",
    )
    target = Agent(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        creator_id=uuid.uuid4(),
        name="Native Researcher",
        primary_model_id=uuid.uuid4(),
        status="idle",
        is_expired=False,
        agent_type="native",
        access_mode="company",
    )
    relationship = SimpleNamespace(target_agent=target)
    db = _Session(target, relationship)
    message_id = uuid.uuid4()
    run_id = uuid.uuid4()
    session_id = uuid.uuid4()
    intake = GatewayA2ARuntimeIntake(
        gateway_message_id=message_id,
        target_run_id=run_id,
        session_id=session_id,
    )

    with (
        patch("app.api.gateway._get_agent_by_key", new=AsyncMock(return_value=source)),
        patch(
            "app.api.gateway.evaluate_agent_relationship_status",
            new=AsyncMock(return_value={"access_status": "active"}),
        ),
        patch(
            "app.api.gateway.enqueue_gateway_a2a_runtime",
            new=AsyncMock(return_value=intake),
        ) as enqueue,
    ):
        result = await gateway.send_message(
            GatewaySendMessageRequest(
                target=target.name,
                content="Research the incident",
                channel="agent",
                message_id=message_id,
            ),
            x_api_key="secret",
            db=db,  # type: ignore[arg-type]
        )

    assert result["status"] == "accepted"
    assert result["message_id"] == str(message_id)
    assert result["run_id"] == str(run_id)
    assert db.commits == 1
    assert db.rollbacks == 0
    enqueue.assert_awaited_once()
    assert enqueue.await_args.kwargs["message_id"] == message_id
    assert not hasattr(gateway, "_send_to_agent_background")


@pytest.mark.asyncio
async def test_openclaw_report_resumes_native_run_in_the_gateway_commit() -> None:
    tenant_id = uuid.uuid4()
    source_agent_id = uuid.uuid4()
    target = Agent(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        creator_id=uuid.uuid4(),
        name="OpenClaw Researcher",
        status="running",
        is_expired=False,
        agent_type="openclaw",
    )
    message = GatewayMessage(
        id=uuid.uuid4(),
        agent_id=target.id,
        sender_agent_id=source_agent_id,
        content="Research the incident",
        status="delivered",
        conversation_id=str(uuid.uuid4()),
    )
    participant = SimpleNamespace(id=uuid.uuid4())
    db = _ReportSession(message, participant)
    source_run_id = uuid.uuid4()

    async def complete(report_db, **_kwargs):
        assert report_db is db
        assert db.commits == 0
        return GatewayA2ARuntimeCompletion(
            source_run_id=source_run_id,
            resumed=True,
        )

    with (
        patch("app.api.gateway._get_agent_by_key", new=AsyncMock(return_value=target)),
        patch(
            "app.api.gateway.complete_gateway_a2a_runtime",
            new=AsyncMock(side_effect=complete),
        ) as complete_runtime,
    ):
        result = await gateway.report_result(
            GatewayReportRequest(
                message_id=message.id,
                result="Verified research result",
            ),
            x_api_key="secret",
            db=db,  # type: ignore[arg-type]
        )

    assert result == {"status": "ok"}
    assert db.commits == 1
    assert db.rollbacks == 0
    complete_runtime.assert_awaited_once()
    result_message = next(
        value for value in db.added if isinstance(value, ChatMessage)
    )
    assert result_message.id == uuid.uuid5(message.id, "gateway-report-result")
    assert result_message.content == "Verified research result"
    assert not any(isinstance(value, GatewayMessage) for value in db.added)
