"""Transient group Runtime activity that never enters chat history."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import uuid

from app.services.agent_runtime.command_worker import (
    CheckpointObservation,
    RuntimeCommandRecord,
    RuntimeRunRecord,
)
from app.services.group_realtime import publish_group_runtime_status


_TERMINAL_STATUSES = frozenset({"completed", "failed", "cancelled"})


def _uuid_value(value: object) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError):
        return None


def _candidate_agent_ids(payload: Mapping[str, object]) -> tuple[uuid.UUID, ...]:
    raw_candidates = payload.get("candidate_agents")
    if not isinstance(raw_candidates, Sequence) or isinstance(
        raw_candidates,
        (str, bytes, bytearray),
    ):
        return ()
    candidates: list[uuid.UUID] = []
    for raw_candidate in raw_candidates:
        if not isinstance(raw_candidate, Mapping):
            continue
        candidate_id = _uuid_value(raw_candidate.get("agent_id"))
        if candidate_id is not None and candidate_id not in candidates:
            candidates.append(candidate_id)
    return tuple(candidates)


async def _publish(
    *,
    run: RuntimeRunRecord,
    payload: Mapping[str, object],
    status: str,
) -> None:
    group_id = _uuid_value(payload.get("group_id"))
    session_id = _uuid_value(payload.get("session_id") or run.registry.session_id)
    if group_id is None or session_id is None:
        return
    await publish_group_runtime_status(
        tenant_id=run.tenant_id,
        group_id=group_id,
        session_id=session_id,
        run_id=run.run_id,
        status=status,
        agent_id=_uuid_value(run.registry.agent_id),
        candidate_agent_ids=(
            _candidate_agent_ids(payload)
            if run.registry.system_role == "group_planning"
            else ()
        ),
    )


class RuntimeGroupStartStatusHandler:
    """Publish accepted group work as an ephemeral status, not a ChatMessage."""

    async def handle(
        self,
        *,
        run: RuntimeRunRecord,
        command: RuntimeCommandRecord,
        checkpoint: CheckpointObservation | None,
    ) -> None:
        del checkpoint
        if command.command_type != "start":
            return
        await _publish(
            run=run,
            payload=command.payload,
            status=("planning" if run.registry.system_role == "group_planning" else "working"),
        )


class RuntimeGroupCheckpointStatusHandler:
    """Update or clear transient activity from authoritative checkpoints."""

    async def handle(
        self,
        *,
        run: RuntimeRunRecord,
        checkpoint: CheckpointObservation,
    ) -> None:
        lifecycle_status = checkpoint.state["lifecycle"]["status"]
        status: str | None = None
        if run.registry.system_role == "group_planning" and lifecycle_status == "waiting_agent":
            status = "delegated"
        elif lifecycle_status == "waiting_user":
            status = "waiting"
        elif lifecycle_status in _TERMINAL_STATUSES:
            status = lifecycle_status
        if status is None:
            return

        initial_input = checkpoint.state["snapshots"].initial_input
        if not isinstance(initial_input, Mapping):
            return
        await _publish(run=run, payload=initial_input, status=status)


__all__ = [
    "RuntimeGroupCheckpointStatusHandler",
    "RuntimeGroupStartStatusHandler",
]
