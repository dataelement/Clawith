"""Product synchronization after settled checkpoint/control boundaries."""

from __future__ import annotations

from dataclasses import replace
from unittest.mock import AsyncMock, patch
import uuid

import pytest
from sqlalchemy.dialects import postgresql

from app.services.agent_runtime.checkpoint_side_effects import (
    RuntimeCheckpointSideEffectError,
    RuntimeCheckpointSideEffects,
    delivery_from_checkpoint,
)
from app.services.agent_runtime.command_worker import (
    CheckpointObservation,
    RuntimeCommandRecord,
    RuntimeRunRecord,
)
from app.services.agent_runtime.delivery import DeliveryReceipt
from app.services.agent_runtime.state import RunInputSnapshots, RunRegistrySnapshot


class _ScalarResult:
    def __init__(self, value: object) -> None:
        self.value = value

    def scalar_one_or_none(self) -> object:
        return self.value


class _Transaction:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False


class _StoredRun:
    lane_held = True
    lane_claimed_at = object()


class _Session:
    def __init__(self, value: object) -> None:
        self.value = value
        self.flush_count = 0
        self.statements = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False

    def begin(self) -> _Transaction:
        return _Transaction()

    async def execute(self, statement) -> _ScalarResult:
        self.statements.append(statement)
        return _ScalarResult(self.value)

    async def flush(self) -> None:
        self.flush_count += 1


class _SessionFactory:
    def __init__(self, value: object = "pending") -> None:
        self.value = value
        self.sessions: list[_Session] = []

    def __call__(self) -> _Session:
        session = _Session(self.value)
        self.sessions.append(session)
        return session


class _Handler:
    def __init__(self) -> None:
        self.statuses: list[str] = []

    async def handle(self, *, run, checkpoint) -> None:
        del run
        self.statuses.append(checkpoint.state["lifecycle"]["status"])


def _records(
    *,
    status: str = "completed",
    lifecycle: dict | None = None,
    command_type: str = "start",
) -> tuple[RuntimeRunRecord, RuntimeCommandRecord, CheckpointObservation]:
    tenant_id = uuid.uuid4()
    run_id = uuid.uuid4()
    command_id = uuid.uuid4()
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
    )
    run = RuntimeRunRecord(
        tenant_id=tenant_id,
        run_id=run_id,
        thread_id=str(run_id),
        runtime_type="langgraph",
        goal=registry.goal,
        run_kind=registry.run_kind,
        source_type=registry.source_type,
        model_id=registry.model_id,
        graph_name=registry.graph_name,
        graph_version=registry.graph_version,
        agent_id=registry.agent_id,
        session_id=registry.session_id,
        system_role=registry.system_role,
        parent_run_id=registry.parent_run_id,
        root_run_id=registry.root_run_id,
    )
    command = RuntimeCommandRecord(
        id=command_id,
        tenant_id=tenant_id,
        run_id=run_id,
        command_type=command_type,  # type: ignore[arg-type]
        payload={"reason": "user_abort"} if command_type == "cancel" else {},
        actor_user_id=uuid.uuid4(),
        actor_agent_id=None,
    )
    terminal = status in {"completed", "failed", "cancelled"}
    checkpoint = CheckpointObservation(
        checkpoint_id="checkpoint-1",
        state={
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
                "next_route": "terminal" if terminal else "wait",
                **(lifecycle or {}),
            },
        },
        next_nodes=() if terminal else ("wait",),
        tasks=() if terminal else (object(),),
        interrupts=() if terminal else (object(),),
        metadata={
            "clawith_run_id": str(run_id),
            "clawith_command_id": str(command_id),
        },
    )
    return run, command, checkpoint


@pytest.mark.asyncio
async def test_completed_checkpoint_delivers_without_projection_round_trip() -> None:
    run, command, checkpoint = _records(
        lifecycle={
            "final_answer": "fallback",
            "delivery_request": {"content": "verified"},
        }
    )
    checkpoint = replace(
        checkpoint,
        state={
            **checkpoint.state,
            "messages": [
                {
                    "id": "assistant-final",
                    "role": "assistant",
                    "content": "verified",
                    "runtime_run_id": str(run.run_id),
                    "runtime_intent": "finish",
                    "reasoning_content": "Validated the evidence",
                }
            ],
        },
    )
    handler = RuntimeCheckpointSideEffects(
        session_factory=_SessionFactory(),  # type: ignore[arg-type]
    )

    with patch(
        "app.services.agent_runtime.checkpoint_side_effects.deliver_runtime_message",
        new=AsyncMock(),
    ) as deliver:
        await handler.handle(run=run, command=command, checkpoint=checkpoint)

    request = deliver.await_args.args[1]
    assert request.content == "verified"
    assert request.checkpoint_id == "checkpoint-1"
    assert request.thinking == "Validated the evidence"


@pytest.mark.asyncio
async def test_waiting_checkpoint_projects_lifecycle_event() -> None:
    run, command, checkpoint = _records(
        status="waiting_external",
        lifecycle={
            "waiting_request": {
                "waiting_type": "external",
                "correlation_id": "poll-1",
                "reason": "async_tool_poll_pending",
            }
        },
    )
    sessions = _SessionFactory("not_required")

    await RuntimeCheckpointSideEffects(
        session_factory=sessions,  # type: ignore[arg-type]
    ).handle(run=run, command=command, checkpoint=checkpoint)

    compiled = sessions.sessions[0].statements[0].compile(
        dialect=postgresql.dialect()
    )
    assert compiled.params["event_type"] == "waiting_started"
    assert compiled.params["payload"]["waiting_type"] == "external"
    assert compiled.params["payload"]["correlation_id"] == "poll-1"


@pytest.mark.asyncio
async def test_resume_terminal_checkpoint_projects_resume_and_terminal_events() -> None:
    run, command, checkpoint = _records(
        command_type="resume",
        lifecycle={"final_answer": "done"},
    )
    sessions = _SessionFactory("not_required")

    await RuntimeCheckpointSideEffects(
        session_factory=sessions,  # type: ignore[arg-type]
    ).handle(run=run, command=command, checkpoint=checkpoint)

    compiled = [
        statement.compile(dialect=postgresql.dialect()).params
        for statement in sessions.sessions[0].statements
    ]
    assert [
        params["event_type"] for params in compiled if "event_type" in params
    ] == [
        "resumed",
        "run_completed",
    ]


@pytest.mark.asyncio
async def test_checkpoint_projects_replayable_tool_activity_with_redacted_arguments() -> None:
    run, command, checkpoint = _records(lifecycle={"final_answer": "done"})
    checkpoint = replace(
        checkpoint,
        state={
            **checkpoint.state,
            "messages": [
                {
                    "id": "assistant-1",
                    "role": "assistant",
                    "content": "",
                    "runtime_run_id": str(run.run_id),
                    "reasoning_content": "Inspect the file",
                    "tool_calls": [
                        {
                            "id": "call-1",
                            "function": {
                                "name": "read_file",
                                "arguments": '{"path":"README.md","api_key":"secret"}',
                            },
                        }
                    ],
                },
                {
                    "id": "tool-result-1",
                    "role": "tool",
                    "tool_call_id": "call-1",
                    "name": "read_file",
                    "content": "contents",
                    "execution_status": "succeeded",
                },
            ],
        },
    )
    sessions = _SessionFactory("not_required")

    await RuntimeCheckpointSideEffects(
        session_factory=sessions,  # type: ignore[arg-type]
    ).handle(run=run, command=command, checkpoint=checkpoint)

    compiled = [
        statement.compile(dialect=postgresql.dialect()).params
        for statement in sessions.sessions[0].statements
    ]
    activities = [
        params["payload"]
        for params in compiled
        if params.get("event_type") == "status_changed"
        and isinstance(params.get("payload"), dict)
        and params["payload"].get("activity_type")
    ]
    assert [activity["activity_type"] for activity in activities] == [
        "thinking",
        "tool_call",
        "tool_call",
    ]
    assert activities[1]["status"] == "running"
    assert activities[2]["status"] == "done"
    assert activities[2]["result"] == "contents"
    assert activities[1]["args"]["api_key"] == "[REDACTED]"


@pytest.mark.asyncio
async def test_terminal_realtime_publish_runs_after_delivery_commit() -> None:
    run, command, checkpoint = _records(lifecycle={"final_answer": "done"})
    events: list[str] = []

    class _OrderedTransaction:
        async def __aenter__(self):
            events.append("begin")
            return self

        async def __aexit__(self, exc_type, exc, traceback):
            events.append("commit")
            return False

    class _OrderedSession(_Session):
        def begin(self):
            return _OrderedTransaction()

    class _OrderedFactory:
        def __call__(self):
            return _OrderedSession("pending")

    session_id = uuid.uuid4()
    message_id = uuid.uuid4()
    receipt = DeliveryReceipt(
        tenant_id=run.tenant_id,
        run_id=run.run_id,
        idempotency_key=f"run:{run.run_id}:terminal:completed",
        status="delivered",
        delivery_kind="terminal",
        checkpoint_id=checkpoint.checkpoint_id,
        message_id=message_id,
        requested_session_id=session_id,
        actual_session_id=session_id,
        fallback_reason=None,
        error_code=None,
    )

    async def fake_deliver(*_args, **_kwargs):
        events.append("deliver")
        return receipt

    async def fake_publish(*_args, **_kwargs):
        assert events == ["begin", "deliver", "commit", "cite"]
        events.append("publish")
        return True

    async def fake_record_citations(text, *, agent_id, session_id, message_id):
        assert events == ["begin", "deliver", "commit"]
        assert text == "done"
        assert agent_id == run.agent_id
        assert session_id == receipt.actual_session_id
        assert message_id == receipt.message_id
        events.append("cite")
        return 1

    handler = RuntimeCheckpointSideEffects(
        session_factory=_OrderedFactory(),  # type: ignore[arg-type]
    )
    with (
        patch(
            "app.services.agent_runtime.checkpoint_side_effects.deliver_runtime_message",
            new=fake_deliver,
        ),
        patch(
            "app.services.agent_runtime.checkpoint_side_effects.publish_stored_group_message",
            new=fake_publish,
        ),
        patch(
            "app.services.agent_runtime.checkpoint_side_effects.record_experience_citations",
            new=fake_record_citations,
        ),
    ):
        await handler.handle(run=run, command=command, checkpoint=checkpoint)

    assert events == ["begin", "deliver", "commit", "cite", "publish"]


@pytest.mark.asyncio
async def test_cancel_uses_control_disposition_without_mutating_preserved_checkpoint() -> None:
    run, command, checkpoint = _records(
        status="waiting_user",
        lifecycle={
            "waiting_request": {
                "waiting_type": "user",
                "correlation_id": "confirm-1",
            }
        },
        command_type="cancel",
    )
    terminal = _Handler()
    handler = RuntimeCheckpointSideEffects(
        session_factory=_SessionFactory("not_required"),  # type: ignore[arg-type]
        terminal_handlers=(terminal,),
    )

    await handler.handle(run=run, command=command, checkpoint=checkpoint)

    assert checkpoint.state["lifecycle"]["status"] == "waiting_user"
    assert checkpoint.next_nodes == ("wait",)
    assert terminal.statuses == ["cancelled"]


@pytest.mark.asyncio
async def test_cancel_before_start_releases_lane_without_fabricating_checkpoint() -> None:
    run, command, _ = _records(command_type="cancel")
    stored = _StoredRun()
    sessions = _SessionFactory(stored)
    handler = RuntimeCheckpointSideEffects(
        session_factory=sessions,  # type: ignore[arg-type]
    )

    await handler.handle(run=run, command=command, checkpoint=None)

    assert stored.lane_held is False
    assert stored.lane_claimed_at is None
    assert sessions.sessions[0].flush_count == 1


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


def test_failed_delivery_preserves_backend_error_fields() -> None:
    run, _, checkpoint = _records(
        status="failed",
        lifecycle={
            "reason": "model_call_failed",
            "error": {
                "code": "model_call_failed",
                "message": "HTTP 429 Too Many Requests",
            },
        },
    )

    delivery = delivery_from_checkpoint(run, checkpoint)

    assert delivery is not None
    assert delivery.failure_code == "model_call_failed"
    assert delivery.failure_message == "HTTP 429 Too Many Requests"


def test_completed_planning_root_has_no_public_delivery() -> None:
    run, _, checkpoint = _records(lifecycle={"final_answer": "internal"})
    planning_run = replace(
        run,
        run_kind="orchestration",
        agent_id=None,
        system_role="group_planning",
    )

    assert delivery_from_checkpoint(planning_run, checkpoint) is None


def test_completed_group_handoff_preserves_frozen_intent_from_checkpoint() -> None:
    handoff = {
        "version": 1,
        "source_run_id": str(uuid.uuid4()),
        "mention_participant_ids": [str(uuid.uuid4())],
        "idempotency_key": "stable-handoff-key",
    }
    run, _, checkpoint = _records(
        lifecycle={
            "final_answer": "fallback",
            "delivery_request": {
                "content": "Public handoff reply",
                "group_handoff": handoff,
            },
        }
    )

    delivery = delivery_from_checkpoint(run, checkpoint)

    assert delivery is not None
    assert delivery.content == "Public handoff reply"
    assert delivery.group_handoff_intent == handoff


@pytest.mark.asyncio
async def test_rejects_checkpoint_metadata_outside_run_scope() -> None:
    run, command, checkpoint = _records(lifecycle={"final_answer": "done"})
    checkpoint = replace(
        checkpoint,
        metadata={
            **checkpoint.metadata,
            "clawith_run_id": str(uuid.uuid4()),
        },
    )

    with pytest.raises(RuntimeCheckpointSideEffectError) as raised:
        await RuntimeCheckpointSideEffects(
            session_factory=_SessionFactory(),  # type: ignore[arg-type]
        ).handle(run=run, command=command, checkpoint=checkpoint)

    assert raised.value.code == "checkpoint_identity_mismatch"
