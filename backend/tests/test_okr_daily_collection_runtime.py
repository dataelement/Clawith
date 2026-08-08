"""Daily OKR agent outreach must enter the durable Runtime as a source Run."""

from __future__ import annotations

from datetime import date
from unittest.mock import AsyncMock, patch
import uuid

import pytest

from app.models.agent import Agent
from app.services.okr_daily_collection import _enqueue_agent_daily_collection


@pytest.mark.asyncio
async def test_agent_daily_collection_uses_oneshot_a2a_wait_resume() -> None:
    tenant_id = uuid.uuid4()
    okr_agent = Agent(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        creator_id=uuid.uuid4(),
        name="OKR Agent",
        primary_model_id=uuid.uuid4(),
        status="idle",
        is_expired=False,
    )
    member = Agent(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        creator_id=uuid.uuid4(),
        name="Researcher",
        primary_model_id=uuid.uuid4(),
        status="idle",
        is_expired=False,
    )
    report_day = date(2026, 7, 14)

    with patch(
        "app.services.heartbeat.run_agent_oneshot",
        new=AsyncMock(return_value=str(uuid.uuid4())),
    ) as run_oneshot:
        accepted = await _enqueue_agent_daily_collection(
            okr_agent,
            member,
            report_day,
        )

    assert accepted is True
    kwargs = run_oneshot.await_args.kwargs
    assert kwargs["agent_id"] == okr_agent.id
    assert kwargs["triggered_by_user_id"] == okr_agent.creator_id
    assert "send_message_to_agent" in kwargs["prompt"]
    assert f"target_agent_id: {member.id}" in kwargs["prompt"]
    assert "agent_name:" not in kwargs["prompt"]
    assert "msg_type: task_delegate" in kwargs["prompt"]
    assert "upsert_member_daily_report" in kwargs["prompt"]
    assert f"member_id: {member.id}" in kwargs["prompt"]
    assert f"report_date: {report_day.isoformat()}" in kwargs["prompt"]
