"""Ephemeral Runtime phase updates without changing lifecycle state."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
import uuid

from sqlalchemy.dialects.postgresql import insert

from app.models.agent_run_event import AgentRunEvent
from app.services.agent_runtime.command_worker import RuntimeSessionFactory
from app.services.agent_runtime.state import RuntimeGraphState
from app.services.group_realtime import publish_group_runtime_status


def _uuid_value(value: object) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError):
        return None


def _phase_position(state: RuntimeGraphState) -> str:
    messages = state["lifecycle"].get("run_messages")
    if isinstance(messages, list):
        for message in reversed(messages):
            if isinstance(message, Mapping):
                message_id = message.get("id")
                if isinstance(message_id, str) and message_id:
                    return message_id
    return str(state["lifecycle"].get("model_step_count", 0))


class RuntimeCompactPhasePublisher:
    """Publish compacting/working as UI phases, never as lifecycle states."""

    def __init__(self, *, session_factory: RuntimeSessionFactory) -> None:
        self._session_factory = session_factory

    async def __call__(self, state: RuntimeGraphState, status: str) -> None:
        registry = state["registry"]
        tenant_id = uuid.UUID(registry.tenant_id)
        run_id = uuid.UUID(registry.run_id)
        agent_id = _uuid_value(registry.agent_id)
        phase_key = f"run-compact:{_phase_position(state)}:{status}"
        event_id = uuid.uuid5(run_id, phase_key)
        previous_status = "compacting" if status == "working" else "working"
        now = datetime.now(UTC)

        async with self._session_factory() as db:
            async with db.begin():
                statement = (
                    insert(AgentRunEvent)
                    .values(
                        id=event_id,
                        tenant_id=tenant_id,
                        run_id=run_id,
                        agent_id=agent_id,
                        event_type="status_changed",
                        summary=f"Run phase changed from {previous_status} to {status}.",
                        payload={
                            "status": status,
                            "previous_status": previous_status,
                            "phase": "run_compact",
                        },
                        artifact_refs=[],
                        idempotency_key=f"phase:{phase_key}",
                        source_checkpoint_id=None,
                        created_at=now,
                    )
                    .on_conflict_do_nothing(
                        constraint="uq_agent_run_events_run_idempotency"
                    )
                )
                await db.execute(statement)

        initial_input = state["snapshots"].initial_input
        group_id = _uuid_value(initial_input.get("group_id"))
        session_id = _uuid_value(initial_input.get("session_id") or registry.session_id)
        if group_id is None or session_id is None:
            return
        await publish_group_runtime_status(
            tenant_id=tenant_id,
            group_id=group_id,
            session_id=session_id,
            run_id=run_id,
            status=status,
            agent_id=agent_id,
        )


__all__ = ["RuntimeCompactPhasePublisher"]
