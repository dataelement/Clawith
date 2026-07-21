"""Checkpoint-derived release for serialized group mention scheduling lanes."""

from __future__ import annotations

from sqlalchemy import select

from app.models.agent_run import AgentRun
from app.services.agent_runtime.command_worker import (
    CheckpointObservation,
    RuntimeRunRecord,
    RuntimeSessionFactory,
)


_TERMINAL_STATUSES = frozenset({"completed", "failed", "cancelled"})


class SchedulingLaneError(RuntimeError):
    """A checkpoint-derived lane transition cannot be applied safely."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class SchedulingLaneCompletionHandler:
    """Release a held lane only from an authoritative terminal checkpoint."""

    def __init__(self, *, session_factory: RuntimeSessionFactory) -> None:
        self._session_factory = session_factory

    async def handle(
        self,
        *,
        run: RuntimeRunRecord,
        checkpoint: CheckpointObservation,
    ) -> None:
        status = checkpoint.state["lifecycle"]["status"]
        if status not in _TERMINAL_STATUSES:
            return
        if checkpoint.metadata.get("clawith_run_id") != str(run.run_id):
            raise SchedulingLaneError(
                "checkpoint_identity_mismatch",
                "lane release checkpoint metadata does not match the Run Registry",
            )

        async with self._session_factory() as db:
            async with db.begin():
                result = await db.execute(
                    select(AgentRun)
                    .where(
                        AgentRun.tenant_id == run.tenant_id,
                        AgentRun.id == run.run_id,
                    )
                    .with_for_update()
                )
                stored = result.scalar_one_or_none()
                if stored is None:
                    raise SchedulingLaneError(
                        "run_not_found",
                        "lane Run does not exist in its tenant",
                    )
                if stored.scheduling_lane_key is None or not stored.lane_held:
                    return
                stored.lane_held = False
                stored.lane_claimed_at = None
                await db.flush()


__all__ = ["SchedulingLaneCompletionHandler", "SchedulingLaneError"]
