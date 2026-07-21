"""Concrete LangGraph driver for Runtime Command Worker inputs."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import cast
import uuid

from langgraph.types import Command
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession

from app.services.agent_runtime.checkpointer import (
    runtime_command_config,
    runtime_thread_config,
)
from app.services.agent_runtime.command_worker import (
    CheckpointObservation,
    CommandExecutionRejected,
    RetryableCommandError,
    RuntimeCommandRecord,
    RuntimeRunRecord,
)
from app.services.agent_runtime.contracts import RUNTIME_COMMAND_METADATA_KEY
from app.services.agent_runtime.context_builder import ContextBuilder
from app.services.agent_runtime.graph import AgentRuntimeGraph
from app.services.agent_runtime.state import (
    JsonObject,
    RunInputSnapshots,
    RuntimeContext,
    RuntimeGraphState,
    RuntimeNodeExecutor,
)


_TERMINAL_STATUSES = frozenset({"completed", "failed", "cancelled"})
_WAITING_RESUME_TYPES = {
    "waiting_user": frozenset({"user_input"}),
    "waiting_agent": frozenset({"agent_result"}),
    "waiting_external": frozenset({"external_event", "timer"}),
}


class RuntimeGraphRegistry:
    """Resolve the currently deployed graph for each stable Runtime topology."""

    def __init__(self, graphs: Sequence[AgentRuntimeGraph]) -> None:
        installed = tuple(graphs)
        if not installed:
            raise ValueError("at least one Runtime graph must be installed")
        agent_graphs = tuple(
            graph
            for graph in installed
            if not graph.identity.name.endswith("_group_planning")
        )
        planning_graphs = tuple(
            graph
            for graph in installed
            if graph.identity.name.endswith("_group_planning")
        )
        if len(agent_graphs) > 1 or len(planning_graphs) > 1:
            raise ValueError(
                "install only the current graph for each Runtime topology"
            )
        self._agent_graph = agent_graphs[0] if agent_graphs else installed[0]
        self._planning_graph = (
            planning_graphs[0] if planning_graphs else self._agent_graph
        )

    def resolve(self, run: RuntimeRunRecord) -> AgentRuntimeGraph:
        # graph_name/version on AgentRun remain trace metadata. Compatible old
        # checkpoints always resume with the current deployed graph code.
        return (
            self._planning_graph
            if run.system_role == "group_planning"
            else self._agent_graph
        )


class RuntimeInputSnapshotFactory:
    """Capture immutable new-Run inputs on the advisory-lock connection."""

    def __init__(self, context_builder: ContextBuilder) -> None:
        self._context_builder = context_builder

    async def capture(
        self,
        *,
        connection: AsyncConnection,
        run: RuntimeRunRecord,
        command: RuntimeCommandRecord,
    ) -> RunInputSnapshots:
        if command.command_type != "start":
            raise ValueError("Runtime input snapshots can only be captured for start")
        session_id = uuid.UUID(run.session_id) if run.session_id is not None else None
        initial_input = {
            key: value
            for key, value in command.payload.items()
            if key != RUNTIME_COMMAND_METADATA_KEY
        }
        related = initial_input.get("related_run_summaries", [])
        if not isinstance(related, Sequence) or isinstance(related, (str, bytes, bytearray)):
            raise CommandExecutionRejected(
                "invalid_related_run_summaries",
                "related_run_summaries must be an array",
            )
        if any(not isinstance(summary, Mapping) for summary in related):
            raise CommandExecutionRejected(
                "invalid_related_run_summaries",
                "each related Run summary must be an object",
            )
        async with AsyncSession(bind=connection, expire_on_commit=False) as db:
            return await self._context_builder.capture_run_inputs(
                db,
                tenant_id=run.tenant_id,
                session_id=session_id,
                agent_id=(
                    uuid.UUID(run.agent_id)
                    if run.agent_id is not None
                    else None
                ),
                source_type=run.source_type,
                source_id=run.source_id,
                scheduling_position_created_at=(
                    run.scheduling_position_created_at
                ),
                scheduling_position_id=run.scheduling_position_id,
                initial_input=initial_input,
                related_run_summaries=cast(Sequence[Mapping[str, object]], related),
            )


@dataclass(frozen=True, slots=True)
class StaticRuntimeInputSnapshotFactory:
    """A concrete factory for callers that already captured trusted snapshots."""

    snapshots: RunInputSnapshots

    async def capture(
        self,
        *,
        connection: AsyncConnection,
        run: RuntimeRunRecord,
        command: RuntimeCommandRecord,
    ) -> RunInputSnapshots:
        del connection, run
        if command.command_type != "start":
            raise ValueError("Runtime input snapshots can only be captured for start")
        return self.snapshots


def _checkpoint_id(snapshot: object) -> str:
    config = getattr(snapshot, "config", None)
    if not isinstance(config, dict):
        raise RetryableCommandError(
            "invalid_checkpoint_config",
            "LangGraph snapshot has no checkpoint configuration",
        )
    configurable = config.get("configurable")
    if not isinstance(configurable, dict):
        raise RetryableCommandError(
            "invalid_checkpoint_config",
            "LangGraph snapshot has no configurable checkpoint identity",
        )
    checkpoint_id = configurable.get("checkpoint_id")
    if not isinstance(checkpoint_id, str) or not checkpoint_id:
        raise RetryableCommandError(
            "invalid_checkpoint_id",
            "LangGraph snapshot has no checkpoint ID",
        )
    return checkpoint_id


def _require_scope(run: RuntimeRunRecord, command: RuntimeCommandRecord) -> None:
    if command.tenant_id != run.tenant_id or command.run_id != run.run_id:
        raise CommandExecutionRejected(
            "command_scope_mismatch",
            "Runtime command does not belong to the locked Run",
        )
    if not run.thread_id.strip():
        raise RetryableCommandError(
            "runtime_identity_mismatch",
            "Runtime thread_id must not be blank",
        )


def _runtime_context(
    run: RuntimeRunRecord,
    command: RuntimeCommandRecord,
    executor: RuntimeNodeExecutor,
) -> RuntimeContext:
    return RuntimeContext(
        tenant_id=str(run.tenant_id),
        run_id=str(run.run_id),
        command_id=str(command.id),
        executor=executor,
        goal=run.goal,
        run_kind=run.run_kind,
        source_type=run.source_type,
        model_id=run.model_id,
        graph_name=run.graph_name,
        graph_version=run.graph_version,
        agent_id=run.agent_id,
        session_id=run.session_id,
        system_role=run.system_role,
        parent_run_id=run.parent_run_id,
        root_run_id=run.root_run_id,
        model_turn_limit=run.model_turn_limit,
        actor_user_id=(str(command.actor_user_id) if command.actor_user_id is not None else None),
        actor_agent_id=(str(command.actor_agent_id) if command.actor_agent_id is not None else None),
    )


def _resume_value(checkpoint: CheckpointObservation, command: RuntimeCommandRecord) -> JsonObject:
    lifecycle = checkpoint.state["lifecycle"]
    status = lifecycle["status"]
    allowed_resume_types = _WAITING_RESUME_TYPES.get(status)
    if allowed_resume_types is None:
        raise CommandExecutionRejected(
            "run_not_waiting",
            "resume requires a waiting checkpoint",
        )

    resume_type = command.payload.get("resume_type")
    correlation_id = command.payload.get("correlation_id")
    payload = command.payload.get("payload")
    if resume_type not in allowed_resume_types:
        raise CommandExecutionRejected(
            "resume_type_mismatch",
            "resume type does not match the checkpoint waiting type",
        )
    if not isinstance(correlation_id, str) or not correlation_id:
        raise CommandExecutionRejected(
            "invalid_resume_correlation",
            "resume correlation_id must be a non-empty string",
        )
    if not isinstance(payload, dict):
        raise CommandExecutionRejected(
            "invalid_resume_payload",
            "resume payload must be an object",
        )
    waiting_request = lifecycle.get("waiting_request")
    if not isinstance(waiting_request, dict):
        raise RetryableCommandError(
            "invalid_waiting_checkpoint",
            "waiting checkpoint has no waiting request",
        )
    expected_correlation = waiting_request.get("correlation_id")
    if expected_correlation != correlation_id:
        raise CommandExecutionRejected(
            "resume_correlation_mismatch",
            "resume correlation_id does not match the waiting checkpoint",
        )
    return dict(command.payload)


def _initial_thread_message(
    run: RuntimeRunRecord,
    snapshots: RunInputSnapshots,
) -> JsonObject:
    """Create the one exact current input appended for this logical Run."""
    initial_input = snapshots.initial_input
    content = initial_input.get("input_content")
    if not isinstance(content, str) or not content:
        content = initial_input.get("content")
    if not isinstance(content, str) or not content:
        content = initial_input.get("message")
    if not isinstance(content, str) or not content:
        content = f"Current Run Directive:\n{run.goal}"
    message_id = initial_input.get("message_id")
    if not isinstance(message_id, str) or not message_id:
        message_id = str(
            uuid.uuid5(
                run.run_id,
                "current-thread-input",
            )
        )
    return {
        "id": message_id,
        "role": "user",
        "content": content,
        "runtime_input": "current",
        "runtime_run_id": str(run.run_id),
    }


def observation_from_snapshot(snapshot: object) -> CheckpointObservation | None:
    values = getattr(snapshot, "values", None)
    if not values:
        return None
    if not isinstance(values, dict):
        raise RetryableCommandError(
            "invalid_checkpoint_state",
            "LangGraph checkpoint values must be an object",
        )
    metadata = getattr(snapshot, "metadata", None)
    if not isinstance(metadata, Mapping):
        raise RetryableCommandError(
            "invalid_checkpoint_metadata",
            "LangGraph checkpoint metadata must be an object",
        )
    raw_created_at = getattr(snapshot, "created_at", None)
    created_at: datetime | None = None
    if isinstance(raw_created_at, str):
        try:
            created_at = datetime.fromisoformat(raw_created_at.replace("Z", "+00:00"))
        except ValueError:
            created_at = None
    return CheckpointObservation(
        checkpoint_id=_checkpoint_id(snapshot),
        state=cast(RuntimeGraphState, dict(values)),
        next_nodes=tuple(str(node) for node in getattr(snapshot, "next", ())),
        tasks=tuple(getattr(snapshot, "tasks", ())),
        interrupts=tuple(getattr(snapshot, "interrupts", ())),
        metadata=dict(metadata),
        created_at=created_at,
    )


class LangGraphRuntimeDriver:
    """Read checkpoints and advance them with the current compatible graph."""

    def __init__(
        self,
        *,
        graph_registry: RuntimeGraphRegistry,
        snapshot_factory: RuntimeInputSnapshotFactory | StaticRuntimeInputSnapshotFactory,
        node_executor: RuntimeNodeExecutor,
    ) -> None:
        self._graph_registry = graph_registry
        self._snapshot_factory = snapshot_factory
        self._node_executor = node_executor

    async def read_latest(
        self,
        *,
        connection: AsyncConnection,
        run: RuntimeRunRecord,
    ) -> CheckpointObservation | None:
        del connection
        if not run.thread_id.strip():
            raise RetryableCommandError(
                "runtime_identity_mismatch",
                "Runtime thread_id must not be blank",
            )
        graph = self._graph_registry.resolve(run)
        async for snapshot in graph.compiled.aget_state_history(
            runtime_thread_config(run.thread_id),
            filter={"clawith_run_id": str(run.run_id)},
            limit=1,
        ):
            return observation_from_snapshot(snapshot)
        return None

    async def read_for_command(
        self,
        *,
        connection: AsyncConnection,
        run: RuntimeRunRecord,
        command: RuntimeCommandRecord,
    ) -> CheckpointObservation | None:
        del connection
        _require_scope(run, command)
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

    async def read_checkpoint(
        self,
        *,
        run: RuntimeRunRecord,
        checkpoint_id: str,
    ) -> CheckpointObservation | None:
        """Read one stable checkpoint by Thread + checkpoint identity."""
        graph = self._graph_registry.resolve(run)
        snapshot = await graph.compiled.aget_state(
            runtime_thread_config(run.thread_id, checkpoint_id=checkpoint_id)
        )
        observation = observation_from_snapshot(snapshot)
        if observation is None or observation.checkpoint_id != checkpoint_id:
            return None
        return observation

    async def execute(
        self,
        *,
        connection: AsyncConnection,
        run: RuntimeRunRecord,
        command: RuntimeCommandRecord,
        checkpoint: CheckpointObservation | None,
    ) -> None:
        _require_scope(run, command)
        graph = self._graph_registry.resolve(run)
        config = runtime_command_config(
            run.thread_id,
            run_id=run.run_id,
            command_id=command.id,
            checkpoint_id=(checkpoint.checkpoint_id if checkpoint is not None else None),
        )
        context = _runtime_context(run, command, self._node_executor)

        if (
            checkpoint is not None
            and checkpoint.metadata.get("clawith_run_id") == str(run.run_id)
            and checkpoint.metadata.get("clawith_command_id") == str(command.id)
        ):
            await graph.compiled.ainvoke(
                None,
                config,
                context=context,
                durability="sync",
            )
            return

        if command.command_type == "start":
            if checkpoint is not None:
                raise RetryableCommandError(
                    "start_checkpoint_conflict",
                    "start cannot replace an existing checkpoint",
                )
            snapshots = await self._snapshot_factory.capture(
                connection=connection,
                run=run,
                command=command,
            )
            initial_state: RuntimeGraphState = {
                "snapshots": snapshots,
                "messages": [_initial_thread_message(run, snapshots)],
                "lifecycle": {
                    "status": "running",
                    "next_route": (
                        "model"
                        if run.system_role == "group_planning"
                        else "compact"
                    ),
                    "model_step_count": 0,
                    "verification_attempt_count": 0,
                    "pending_tool_calls": [],
                },
            }
            await graph.compiled.ainvoke(
                initial_state,
                config,
                context=context,
                durability="sync",
            )
            return

        if checkpoint is None:
            raise CommandExecutionRejected(
                "thread_not_started",
                "resume and cancel require an existing checkpoint",
            )
        status = checkpoint.state["lifecycle"]["status"]
        if status in _TERMINAL_STATUSES:
            raise CommandExecutionRejected(
                "terminal_run",
                "terminal Runtime threads cannot accept new commands",
            )

        if command.command_type == "resume":
            resume_value = _resume_value(checkpoint, command)
            await graph.compiled.ainvoke(
                Command(resume=resume_value),
                config,
                context=context,
                durability="sync",
            )
            return

        if command.command_type == "cancel":
            raise CommandExecutionRejected(
                "cancel_is_control_plane",
                "cancel preserves the last checkpoint and is settled by the Command Worker",
            )

        raise CommandExecutionRejected(
            "unsupported_command",
            f"unsupported Runtime command {command.command_type!r}",
        )


__all__ = [
    "LangGraphRuntimeDriver",
    "RuntimeGraphRegistry",
    "RuntimeInputSnapshotFactory",
    "StaticRuntimeInputSnapshotFactory",
    "observation_from_snapshot",
]
