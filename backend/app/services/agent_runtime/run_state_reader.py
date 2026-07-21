"""Exact, typed Run queries over Command and LangGraph checkpoint truth."""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
import json
from typing import cast
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent_run import AgentRun
from app.models.agent_run_command import AgentRunCommand
from app.services.agent_runtime.checkpointer import create_checkpointer, runtime_thread_config
from app.services.agent_runtime.command_worker import (
    CheckpointObservation,
    RuntimeRunRecord,
    classify_checkpoint,
)
from app.services.agent_runtime.contracts import (
    DeliveryStatus,
    RunKind,
    RuntimeSourceType,
    RuntimeType,
    RunView,
)
from app.services.agent_runtime.langgraph_driver import (
    RuntimeGraphRegistry,
    observation_from_snapshot,
)
from app.services.agent_runtime.graph import build_agent_runtime_graph
from app.services.agent_runtime.state import JsonObject, LifecycleStatus


class RunStateReadError(RuntimeError):
    """A target Run cannot be mapped to one trustworthy typed view."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def runtime_run_record(run: AgentRun) -> RuntimeRunRecord:
    if not run.runtime_thread_id or not run.runtime_thread_id.strip():
        raise RunStateReadError("runtime_identity_mismatch", "Run thread_id is blank")
    if run.model_id is None or not run.graph_name or not run.graph_version:
        raise RunStateReadError("invalid_graph_identity", "Run graph identity is incomplete")
    return RuntimeRunRecord(
        tenant_id=run.tenant_id,
        run_id=run.id,
        thread_id=run.runtime_thread_id,
        runtime_type=run.runtime_type,
        goal=run.goal,
        run_kind=run.run_kind,
        source_type=run.source_type,
        model_id=str(run.model_id),
        graph_name=run.graph_name,
        graph_version=run.graph_version,
        agent_id=str(run.agent_id) if run.agent_id is not None else None,
        session_id=str(run.session_id) if run.session_id is not None else None,
        system_role=run.system_role,
        parent_run_id=str(run.parent_run_id) if run.parent_run_id is not None else None,
        root_run_id=str(run.root_run_id) if run.root_run_id is not None else None,
        model_turn_limit=run.model_turn_limit,
        source_id=run.source_id,
        scheduling_position_created_at=run.scheduling_position_created_at,
        scheduling_position_id=run.scheduling_position_id,
    )


def _text(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _summary(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError):
        return None


class RunStateReader:
    """Read one Run by its own Command/checkpoint identity, never Thread latest."""

    def __init__(
        self,
        db: AsyncSession,
        *,
        graph_registry: RuntimeGraphRegistry,
    ) -> None:
        self._db = db
        self._graph_registry = graph_registry

    async def _load_run(self, tenant_id: uuid.UUID, run_id: uuid.UUID) -> AgentRun:
        result = await self._db.execute(
            select(AgentRun).where(
                AgentRun.tenant_id == tenant_id,
                AgentRun.id == run_id,
            )
        )
        run = result.scalar_one_or_none()
        if run is None:
            raise RunStateReadError(
                "run_not_found",
                f"run {run_id} does not exist in tenant {tenant_id}",
            )
        return run

    async def _commands(self, run: AgentRun) -> list[AgentRunCommand]:
        result = await self._db.execute(
            select(AgentRunCommand)
            .where(
                AgentRunCommand.tenant_id == run.tenant_id,
                AgentRunCommand.run_id == run.id,
            )
            .order_by(AgentRunCommand.created_at, AgentRunCommand.id)
        )
        return list(result.scalars().all())

    async def _read_exact(
        self,
        run: RuntimeRunRecord,
        checkpoint_id: str,
    ) -> CheckpointObservation:
        graph = self._graph_registry.resolve(run)
        snapshot = await graph.compiled.aget_state(
            runtime_thread_config(run.thread_id, checkpoint_id=checkpoint_id)
        )
        observation = observation_from_snapshot(snapshot)
        if observation is None or observation.checkpoint_id != checkpoint_id:
            raise RunStateReadError(
                "checkpoint_not_found",
                "applied Command checkpoint is not available on the target Thread",
            )
        return observation

    async def _read_unsettled(
        self,
        run: RuntimeRunRecord,
        command: AgentRunCommand,
    ) -> CheckpointObservation | None:
        graph = self._graph_registry.resolve(run)
        async for snapshot in graph.compiled.aget_state_history(
            runtime_thread_config(run.thread_id),
            filter={
                "clawith_run_id": str(run.run_id),
                "clawith_command_id": str(command.id),
            },
            limit=1,
        ):
            return observation_from_snapshot(snapshot)
        return None

    @staticmethod
    def _validate_observation(
        run: RuntimeRunRecord,
        observation: CheckpointObservation,
        *,
        command_id: uuid.UUID | None,
    ) -> None:
        if observation.metadata.get("clawith_run_id") != str(run.run_id):
            raise RunStateReadError(
                "checkpoint_identity_mismatch",
                "checkpoint metadata does not match the target Run",
            )
        if (
            command_id is not None
            and observation.metadata.get("clawith_command_id") != str(command_id)
        ):
            raise RunStateReadError(
                "checkpoint_command_mismatch",
                "checkpoint metadata does not match the selected Command",
            )
        if classify_checkpoint(observation) == "inconsistent":
            raise RunStateReadError(
                "inconsistent_checkpoint",
                "checkpoint values, next, tasks, and interrupts disagree",
            )

    @staticmethod
    def _view(
        run: AgentRun,
        *,
        observation: CheckpointObservation | None,
        control_status: LifecycleStatus | None,
        fallback_status: LifecycleStatus | None,
        control_error_code: str | None = None,
    ) -> RunView:
        lifecycle: Mapping[str, object] = {}
        if observation is not None:
            raw_lifecycle = observation.state.get("lifecycle")
            if isinstance(raw_lifecycle, Mapping):
                lifecycle = raw_lifecycle
        status = control_status or cast(
            LifecycleStatus | None,
            lifecycle.get("status", fallback_status),
        )
        waiting = lifecycle.get("waiting_request")
        waiting_map = waiting if isinstance(waiting, Mapping) else {}
        error = lifecycle.get("error")
        error_map = error if isinstance(error, Mapping) else {}
        verification = lifecycle.get("verification_result")
        verification_result = (
            cast(JsonObject, dict(verification)) if isinstance(verification, Mapping) else None
        )
        current_node = None
        if observation is not None and observation.next_nodes:
            current_node = ",".join(observation.next_nodes)
        raw_count = lifecycle.get("model_step_count", 0)
        model_step_count = raw_count if isinstance(raw_count, int) and raw_count >= 0 else 0
        return RunView(
            tenant_id=run.tenant_id,
            run_id=run.id,
            thread_id=run.runtime_thread_id,
            session_id=run.session_id,
            source_type=cast(RuntimeSourceType, run.source_type),
            run_kind=cast(RunKind, run.run_kind),
            goal=run.goal,
            runtime_type=cast(RuntimeType, run.runtime_type),
            execution_status=status,
            current_node=current_node,
            model_step_count=model_step_count,
            waiting_type=(
                _text(waiting_map.get("waiting_type"))
                or (status.removeprefix("waiting_") if status and status.startswith("waiting_") else None)
            ),
            waiting_reason=(
                _text(waiting_map.get("reason"))
                or _text(waiting_map.get("question"))
                or _text(waiting_map.get("prompt"))
            ),
            waiting_correlation_id=_text(waiting_map.get("correlation_id")),
            result_summary=_summary(lifecycle.get("result_summary")),
            error_code=_text(error_map.get("code")) or _text(control_error_code),
            last_error=_text(error_map.get("message")) or _text(lifecycle.get("reason")),
            verification_result=verification_result,
            delivery_status=cast(DeliveryStatus, run.delivery_status),
            applied_checkpoint_id=(
                observation.checkpoint_id if observation is not None else None
            ),
            checkpoint_created_at=(
                observation.created_at if observation is not None else None
            ),
            created_at=run.created_at,
            updated_at=run.updated_at,
        )

    async def get_run_state(self, tenant_id: uuid.UUID, run_id: uuid.UUID) -> RunView:
        run = await self._load_run(tenant_id, run_id)
        if run.runtime_type != "langgraph":
            raise RunStateReadError(
                "legacy_runtime",
                "typed checkpoint RunView is only available for LangGraph Runs",
            )
        run_record = runtime_run_record(run)
        commands = await self._commands(run)

        applied_cancel = next(
            (
                command
                for command in reversed(commands)
                if command.command_type == "cancel" and command.status == "applied"
            ),
            None,
        )
        if applied_cancel is not None:
            observation = None
            if applied_cancel.applied_checkpoint_id is not None:
                observation = await self._read_exact(
                    run_record,
                    applied_cancel.applied_checkpoint_id,
                )
                self._validate_observation(run_record, observation, command_id=None)
            return self._view(
                run,
                observation=observation,
                control_status="cancelled",
                fallback_status="cancelled",
            )

        applied_graph = next(
            (
                command
                for command in reversed(commands)
                if command.command_type in {"start", "resume"}
                and command.status == "applied"
                and command.applied_checkpoint_id is not None
            ),
            None,
        )
        rejected_start = next(
            (
                command
                for command in reversed(commands)
                if command.command_type == "start" and command.status == "rejected"
            ),
            None,
        )
        active = next(
            (
                command
                for command in commands
                if command.status in {"pending", "claimed"}
            ),
            None,
        )
        applied_observation: CheckpointObservation | None = None
        if applied_graph is not None:
            applied_observation = await self._read_exact(
                run_record,
                cast(str, applied_graph.applied_checkpoint_id),
            )
            self._validate_observation(
                run_record,
                applied_observation,
                command_id=applied_graph.id,
            )

        if active is not None and active.command_type != "cancel":
            observation = await self._read_unsettled(run_record, active)
            if observation is not None:
                self._validate_observation(
                    run_record,
                    observation,
                    command_id=active.id,
                )
                return self._view(
                    run,
                    observation=observation,
                    control_status=None,
                    fallback_status="running",
                )
            if applied_observation is not None:
                return self._view(
                    run,
                    observation=applied_observation,
                    control_status=None,
                    fallback_status=None,
                )
            fallback: LifecycleStatus = "running" if active.status == "claimed" else "queued"
            return self._view(
                run,
                observation=None,
                control_status=None,
                fallback_status=fallback,
            )

        if applied_observation is not None:
            return self._view(
                run,
                observation=applied_observation,
                control_status=None,
                fallback_status=None,
            )

        if rejected_start is not None:
            return self._view(
                run,
                observation=None,
                control_status="failed",
                fallback_status="failed",
                control_error_code=rejected_start.error_code,
            )

        return self._view(
            run,
            observation=None,
            control_status=None,
            fallback_status="created",
        )


@asynccontextmanager
async def open_run_state_reader(db: AsyncSession) -> AsyncIterator[RunStateReader]:
    """Compose an exact checkpoint reader without introducing query projections."""
    async with create_checkpointer() as checkpointer:
        graph = build_agent_runtime_graph(checkpointer=checkpointer)
        yield RunStateReader(
            db,
            graph_registry=RuntimeGraphRegistry([graph]),
        )


__all__ = [
    "RunStateReadError",
    "RunStateReader",
    "open_run_state_reader",
    "runtime_run_record",
]
