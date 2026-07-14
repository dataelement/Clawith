"""Committed-checkpoint projection and delivery tests."""

from __future__ import annotations

from dataclasses import replace
from unittest.mock import AsyncMock, patch
import uuid

import pytest

from app.services.agent_runtime.checkpoint_side_effects import (
    RuntimeCheckpointSideEffectError,
    RuntimeCheckpointSideEffects,
    delivery_from_checkpoint,
)
from app.services.agent_runtime.delivery import DeliveryReceipt
from app.services.agent_runtime.command_worker import (
    CheckpointObservation,
    RuntimeCommandRecord,
    RuntimeRunRecord,
)
from app.services.agent_runtime.projector import ProjectionResult
from app.services.agent_runtime.state import (
    RunInputSnapshots,
    RunRegistrySnapshot,
    RuntimeGraphState,
)


class _ScalarResult:
    def __init__(self, value: object) -> None:
        self.value = value

    def scalar_one_or_none(self) -> object:
        return self.value


class _Transaction:
    def __init__(self, timeline: list[str]) -> None:
        self.timeline = timeline

    async def __aenter__(self) -> "_Transaction":
        self.timeline.append("transaction_enter")
        return self

    async def __aexit__(self, exc_type, exc, traceback) -> bool:
        self.timeline.append("transaction_exit")
        return False


class _Session:
    def __init__(self, timeline: list[str], delivery_status: str) -> None:
        self.timeline = timeline
        self.delivery_status = delivery_status

    async def __aenter__(self) -> "_Session":
        self.timeline.append("session_enter")
        return self

    async def __aexit__(self, exc_type, exc, traceback) -> bool:
        self.timeline.append("session_exit")
        return False

    def begin(self) -> _Transaction:
        return _Transaction(self.timeline)

    async def execute(self, _statement) -> _ScalarResult:
        self.timeline.append("load_delivery_status")
        return _ScalarResult(self.delivery_status)


class _SessionFactory:
    def __init__(self, timeline: list[str], delivery_status: str = "pending") -> None:
        self.timeline = timeline
        self.delivery_status = delivery_status

    def __call__(self) -> _Session:
        return _Session(self.timeline, self.delivery_status)


class _Projector:
    def __init__(self, timeline: list[str], *, status: str, checkpoint_id: str) -> None:
        self.timeline = timeline
        self.status = status
        self.checkpoint_id = checkpoint_id
        self.calls: list[tuple[object, uuid.UUID, uuid.UUID]] = []

    async def project_run(self, db, *, tenant_id, run_id):
        self.timeline.append("project")
        self.calls.append((db, tenant_id, run_id))
        return ProjectionResult(
            tenant_id=tenant_id,
            run_id=run_id,
            applied_checkpoint_ids=(self.checkpoint_id,),
            added_event_types=(),
            authoritative_status=self.status,
            authoritative_terminal=self.status in {"completed", "failed", "cancelled"},
        )


class _TerminalHandler:
    def __init__(self, timeline: list[str], *, error: Exception | None = None) -> None:
        self.timeline = timeline
        self.error = error
        self.calls = 0

    async def handle(self, *, run, checkpoint) -> None:
        del run, checkpoint
        self.calls += 1
        self.timeline.append("terminal_handler")
        if self.error is not None:
            raise self.error


class _CheckpointHandler:
    def __init__(self, timeline: list[str], *, error: Exception | None = None) -> None:
        self.timeline = timeline
        self.error = error
        self.calls = 0

    async def handle(self, *, run, checkpoint) -> None:
        del run, checkpoint
        self.calls += 1
        self.timeline.append("checkpoint_handler")
        if self.error is not None:
            raise self.error


class _CodedError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _records(
    *,
    status: str = "completed",
    checkpoint_id: str = "checkpoint-1",
    lifecycle: dict | None = None,
) -> tuple[RuntimeRunRecord, RuntimeCommandRecord, CheckpointObservation]:
    tenant_id = uuid.uuid4()
    run_id = uuid.uuid4()
    registry = RunRegistrySnapshot(
        tenant_id=str(tenant_id),
        run_id=str(run_id),
        goal="answer",
        run_kind="foreground",
        source_type="chat",
        model_id=str(uuid.uuid4()),
        graph_name="runtime_graph",
        graph_version="v1",
        agent_id=str(uuid.uuid4()),
        session_id=str(uuid.uuid4()),
    )
    state: RuntimeGraphState = {
        "registry": registry,
        "snapshots": RunInputSnapshots(
            session_context={},
            session_context_version=0,
            recent_session_messages=(),
            related_run_summaries=(),
            initial_input={},
        ),
        "lifecycle": {
            "status": status,  # type: ignore[typeddict-item]
            "next_route": "terminal" if status in {"completed", "failed", "cancelled"} else "wait",
            **(lifecycle or {}),
        },
    }
    run = RuntimeRunRecord(
        tenant_id=tenant_id,
        run_id=run_id,
        thread_id=str(run_id),
        runtime_type="langgraph",
        registry=registry,
    )
    command = RuntimeCommandRecord(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        run_id=run_id,
        command_type="start",
        payload={},
        actor_user_id=uuid.uuid4(),
        actor_agent_id=None,
    )
    return run, command, CheckpointObservation(checkpoint_id=checkpoint_id, state=state)


@pytest.mark.asyncio
async def test_projects_then_delivers_completed_checkpoint() -> None:
    timeline: list[str] = []
    run, command, checkpoint = _records(
        lifecycle={
            "final_answer": "fallback answer",
            "delivery_request": {"content": "verified answer"},
        }
    )
    projector = _Projector(timeline, status="completed", checkpoint_id=checkpoint.checkpoint_id)
    handler = RuntimeCheckpointSideEffects(
        session_factory=_SessionFactory(timeline),  # type: ignore[arg-type]
        projector=projector,  # type: ignore[arg-type]
    )

    message_id = uuid.uuid4()

    async def delivered(_db, request):
        timeline.append("deliver")
        assert request.content == "verified answer"
        assert request.checkpoint_id == checkpoint.checkpoint_id
        assert request.lifecycle_status == "completed"
        return DeliveryReceipt(
            tenant_id=run.tenant_id,
            run_id=run.run_id,
            idempotency_key=request.idempotency_key,
            status="delivered",
            delivery_kind="terminal",
            checkpoint_id=checkpoint.checkpoint_id,
            message_id=message_id,
            requested_session_id=None,
            actual_session_id=None,
            fallback_reason=None,
            error_code=None,
        )

    async def published(**_kwargs):
        timeline.append("publish")

    with (
        patch(
            "app.services.agent_runtime.checkpoint_side_effects.deliver_runtime_message",
            new=AsyncMock(side_effect=delivered),
        ) as deliver,
        patch(
            "app.services.agent_runtime.checkpoint_side_effects.publish_committed_group_message",
            new=AsyncMock(side_effect=published),
        ) as broadcast,
    ):
        await handler.handle(run=run, command=command, checkpoint=checkpoint)

    assert timeline.index("project") < timeline.index("deliver")
    assert max(
        index for index, item in enumerate(timeline) if item == "transaction_exit"
    ) < timeline.index("publish")
    deliver.assert_awaited_once()
    broadcast.assert_awaited_once_with(
        tenant_id=run.tenant_id,
        message_id=message_id,
    )


@pytest.mark.asyncio
async def test_checkpoint_product_handler_runs_only_after_projection_commit() -> None:
    timeline: list[str] = []
    run, command, checkpoint = _records(status="waiting_agent")
    product = _CheckpointHandler(timeline)
    handler = RuntimeCheckpointSideEffects(
        session_factory=_SessionFactory(timeline),  # type: ignore[arg-type]
        projector=_Projector(
            timeline,
            status="waiting_agent",
            checkpoint_id=checkpoint.checkpoint_id,
        ),  # type: ignore[arg-type]
        checkpoint_handlers=(product,),
    )

    await handler.handle(run=run, command=command, checkpoint=checkpoint)

    assert product.calls == 1
    assert timeline.index("transaction_exit") < timeline.index("checkpoint_handler")


def test_completed_planning_root_has_no_public_terminal_delivery() -> None:
    run, _, checkpoint = _records(lifecycle={"final_answer": "internal"})
    registry = replace(
        run.registry,
        run_kind="orchestration",
        agent_id=None,
        system_role="group_planning",
    )
    planning_run = replace(run, registry=registry)
    planning_checkpoint = replace(
        checkpoint,
        state={**checkpoint.state, "registry": registry},
    )

    assert delivery_from_checkpoint(planning_run, planning_checkpoint) is None


@pytest.mark.asyncio
async def test_terminal_delivery_happens_before_a_failing_derived_handler() -> None:
    timeline: list[str] = []
    run, command, checkpoint = _records(lifecycle={"final_answer": "done"})
    terminal = _TerminalHandler(timeline, error=RuntimeError("compact failed"))
    handler = RuntimeCheckpointSideEffects(
        session_factory=_SessionFactory(timeline),  # type: ignore[arg-type]
        projector=_Projector(
            timeline,
            status="completed",
            checkpoint_id=checkpoint.checkpoint_id,
        ),  # type: ignore[arg-type]
        terminal_handlers=(terminal,),
    )

    async def delivered(_db, _request):
        timeline.append("deliver")

    with (
        patch(
            "app.services.agent_runtime.checkpoint_side_effects.deliver_runtime_message",
            new=AsyncMock(side_effect=delivered),
        ) as deliver,
        pytest.raises(RuntimeError, match="compact failed"),
    ):
        await handler.handle(run=run, command=command, checkpoint=checkpoint)

    assert timeline.index("deliver") < timeline.index("terminal_handler")
    deliver.assert_awaited_once()
    assert terminal.calls == 1


@pytest.mark.asyncio
async def test_missing_optional_compact_model_does_not_retry_delivered_chat() -> None:
    timeline: list[str] = []
    run, command, checkpoint = _records(lifecycle={"final_answer": "done"})
    terminal = _TerminalHandler(
        timeline,
        error=_CodedError(
            "session_compact_model_unavailable",
            "Session Agent has no current primary model",
        ),
    )
    handler = RuntimeCheckpointSideEffects(
        session_factory=_SessionFactory(timeline),  # type: ignore[arg-type]
        projector=_Projector(
            timeline,
            status="completed",
            checkpoint_id=checkpoint.checkpoint_id,
        ),  # type: ignore[arg-type]
        terminal_handlers=(terminal,),
    )

    with patch(
        "app.services.agent_runtime.checkpoint_side_effects.deliver_runtime_message",
        new=AsyncMock(),
    ) as deliver:
        await handler.handle(run=run, command=command, checkpoint=checkpoint)

    deliver.assert_awaited_once()
    assert terminal.calls == 1


@pytest.mark.asyncio
async def test_delivery_failure_does_not_skip_terminal_product_handlers() -> None:
    timeline: list[str] = []
    run, command, checkpoint = _records(lifecycle={"final_answer": "done"})
    terminal = _TerminalHandler(timeline)
    handler = RuntimeCheckpointSideEffects(
        session_factory=_SessionFactory(timeline),  # type: ignore[arg-type]
        projector=_Projector(
            timeline,
            status="completed",
            checkpoint_id=checkpoint.checkpoint_id,
        ),  # type: ignore[arg-type]
        terminal_handlers=(terminal,),
    )

    with (
        patch(
            "app.services.agent_runtime.checkpoint_side_effects.deliver_runtime_message",
            new=AsyncMock(side_effect=RuntimeError("delivery failed")),
        ),
        pytest.raises(RuntimeError, match="delivery failed"),
    ):
        await handler.handle(run=run, command=command, checkpoint=checkpoint)

    assert terminal.calls == 1
    assert "terminal_handler" in timeline


def test_waiting_delivery_uses_correlation_id_and_prompt() -> None:
    run, _, checkpoint = _records(
        status="waiting_user",
        lifecycle={
            "waiting_request": {
                "waiting_type": "user",
                "correlation_id": "confirm-1",
                "question": "Continue?",
            }
        },
    )

    delivery = delivery_from_checkpoint(run, checkpoint)

    assert delivery is not None
    assert delivery.kind == "waiting"
    assert delivery.content == "Continue?"
    assert delivery.interrupt_id == "confirm-1"
    assert delivery.lifecycle_status == "waiting_user"


@pytest.mark.asyncio
async def test_not_required_run_projects_without_delivery() -> None:
    timeline: list[str] = []
    run, command, checkpoint = _records(lifecycle={"final_answer": "done"})
    handler = RuntimeCheckpointSideEffects(
        session_factory=_SessionFactory(timeline, delivery_status="not_required"),  # type: ignore[arg-type]
        projector=_Projector(
            timeline,
            status="completed",
            checkpoint_id=checkpoint.checkpoint_id,
        ),  # type: ignore[arg-type]
    )

    with patch(
        "app.services.agent_runtime.checkpoint_side_effects.deliver_runtime_message",
        new=AsyncMock(),
    ) as deliver:
        await handler.handle(run=run, command=command, checkpoint=checkpoint)

    assert "project" in timeline
    deliver.assert_not_awaited()


@pytest.mark.asyncio
async def test_projection_mismatch_stops_before_delivery() -> None:
    timeline: list[str] = []
    run, command, checkpoint = _records(lifecycle={"final_answer": "done"})
    handler = RuntimeCheckpointSideEffects(
        session_factory=_SessionFactory(timeline),  # type: ignore[arg-type]
        projector=_Projector(
            timeline,
            status="running",
            checkpoint_id=checkpoint.checkpoint_id,
        ),  # type: ignore[arg-type]
    )

    with (
        patch(
            "app.services.agent_runtime.checkpoint_side_effects.deliver_runtime_message",
            new=AsyncMock(),
        ) as deliver,
        pytest.raises(RuntimeCheckpointSideEffectError, match="differs") as raised,
    ):
        await handler.handle(run=run, command=command, checkpoint=checkpoint)

    assert raised.value.code == "projection_checkpoint_mismatch"
    deliver.assert_not_awaited()


@pytest.mark.asyncio
async def test_rejects_checkpoint_outside_run_scope() -> None:
    timeline: list[str] = []
    run, command, checkpoint = _records(lifecycle={"final_answer": "done"})
    checkpoint = replace(
        checkpoint,
        state={
            **checkpoint.state,
            "registry": replace(checkpoint.state["registry"], tenant_id=str(uuid.uuid4())),
        },
    )
    projector = _Projector(timeline, status="completed", checkpoint_id=checkpoint.checkpoint_id)
    handler = RuntimeCheckpointSideEffects(
        session_factory=_SessionFactory(timeline),  # type: ignore[arg-type]
        projector=projector,  # type: ignore[arg-type]
    )

    with pytest.raises(RuntimeCheckpointSideEffectError, match="Run Registry") as raised:
        await handler.handle(run=run, command=command, checkpoint=checkpoint)

    assert raised.value.code == "checkpoint_identity_mismatch"
    assert projector.calls == []
