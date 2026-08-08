"""Task API transaction boundary tests for Runtime intake."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
import uuid

import pytest

from app.api.tasks import create_task


class _Session:
    def __init__(self, timeline: list[str]) -> None:
        self.timeline = timeline
        self.added: list[object] = []

    def add(self, value: object) -> None:
        self.timeline.append("task_added")
        self.added.append(value)

    async def flush(self) -> None:
        self.timeline.append("task_flushed")

    async def commit(self) -> None:
        self.timeline.append("transaction_committed")


@pytest.mark.asyncio
async def test_create_todo_registers_runtime_before_committing_business_fact() -> None:
    timeline: list[str] = []
    db = _Session(timeline)
    user = SimpleNamespace(id=uuid.uuid4())
    resolved_agent = SimpleNamespace(id=uuid.uuid4())
    data = SimpleNamespace(
        title="Prepare report",
        description="Use workspace evidence",
        type="todo",
        priority="medium",
        due_date=None,
        supervision_target_name=None,
        supervision_channel=None,
        remind_schedule=None,
    )
    runtime_handle = SimpleNamespace(run_id=uuid.uuid4())

    async def enqueue(session, *, task, agent: object):
        assert session is db
        assert task in db.added
        assert agent is resolved_agent
        timeline.append("runtime_registered")
        return runtime_handle

    with (
        patch(
            "app.api.tasks.check_agent_access",
            new=AsyncMock(return_value=(resolved_agent, "manage")),
        ),
        patch(
            "app.services.task_executor.enqueue_task_runtime",
            new=AsyncMock(side_effect=enqueue),
        ) as enqueue_runtime,
        patch(
            "app.api.tasks._enrich_task_out",
            new=AsyncMock(return_value="task-response"),
        ),
        patch("asyncio.create_task", new=MagicMock()) as create_background_task,
    ):
        response = await create_task(
            agent_id=resolved_agent.id,
            data=data,
            current_user=user,
            db=db,  # type: ignore[arg-type]
        )

    assert response == "task-response"
    assert timeline.index("task_flushed") < timeline.index("runtime_registered")
    assert timeline.index("runtime_registered") < timeline.index("transaction_committed")
    enqueue_runtime.assert_awaited_once()
    create_background_task.assert_not_called()
