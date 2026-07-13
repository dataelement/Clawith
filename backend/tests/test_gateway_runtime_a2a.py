"""OpenClaw gateway cutover tests for native A2A Runtime execution."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
import uuid

import pytest

from app.api import gateway
from app.models.agent import Agent
from app.schemas.schemas import GatewaySendMessageRequest
from app.services.agent_runtime.a2a_runtime import GatewayA2ARuntimeIntake


class _Scalars:
    def __init__(self, values: list[object]) -> None:
        self.values = values

    def all(self) -> list[object]:
        return self.values


class _Result:
    def __init__(self, values: list[object]) -> None:
        self.values = values

    def scalars(self) -> _Scalars:
        return _Scalars(self.values)


class _Session:
    def __init__(self, relationship: object) -> None:
        self.relationship = relationship
        self.commits = 0
        self.rollbacks = 0

    async def execute(self, _statement) -> _Result:
        return _Result([self.relationship])

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
    )
    relationship = SimpleNamespace(target_agent=target)
    db = _Session(relationship)
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
