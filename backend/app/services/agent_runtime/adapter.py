"""Caller-transaction command intake for the durable Runtime."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.models.agent import Agent
from app.models.agent_run import AgentRun
from app.models.agent_run_command import AgentRunCommand
from app.models.llm import LLMModel
from app.services.agent_runtime.config import RuntimeGateDecision, RuntimeRolloutPolicy
from app.services.agent_runtime.contracts import (
    CancelRunCommand,
    RUNTIME_COMMAND_METADATA_KEY,
    ResumeRunCommand,
    RunHandle,
    StartRunCommand,
)
from app.services.agent_runtime.graph import RuntimeGraphIdentity
from app.services.agent_runtime.model_capabilities import (
    ModelCapabilityError,
    ModelCapabilityResolver,
)
from app.services.agent_runtime.persistence import (
    RunRegistration,
    enqueue_cancel,
    enqueue_resume,
    register_run_with_start,
)


class RuntimeAdapterError(RuntimeError):
    """A Runtime command cannot be accepted through the v2 adapter."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class RuntimeCommandIntake:
    """Persist Runtime commands inside an AsyncSession owned by the caller.

    This layer accepts commands and returns stable identities. It never commits,
    invokes a Graph, or passes an ``AgentRun`` ORM instance into execution code.
    """

    def __init__(
        self,
        db: AsyncSession,
        *,
        settings: Settings | None = None,
    ) -> None:
        runtime_settings = settings or get_settings()
        self._db = db
        self._rollout = RuntimeRolloutPolicy.from_settings(runtime_settings)
        self._current_graph = RuntimeGraphIdentity.from_settings(runtime_settings)
        self._planning_graph = RuntimeGraphIdentity.planning_from_settings(runtime_settings)

    @staticmethod
    def _require_v2(decision: RuntimeGateDecision) -> None:
        if not decision.use_v2:
            raise RuntimeAdapterError(
                "runtime_v2_disabled",
                f"Agent Runtime v2 is not enabled for this command ({decision.reason})",
            )

    async def _find_start_retry(self, command: StartRunCommand) -> AgentRun | None:
        if command.source_execution_id is None:
            return None
        result = await self._db.execute(
            select(AgentRun).where(
                AgentRun.tenant_id == command.tenant_id,
                AgentRun.source_type == command.source_type,
                AgentRun.source_execution_id == command.source_execution_id,
            )
        )
        return result.scalar_one_or_none()

    async def _get_run(self, *, tenant_id: uuid.UUID, run_id: uuid.UUID) -> AgentRun:
        result = await self._db.execute(
            select(AgentRun).where(
                AgentRun.tenant_id == tenant_id,
                AgentRun.id == run_id,
            )
        )
        run = result.scalar_one_or_none()
        if run is None:
            raise RuntimeAdapterError(
                "run_not_found",
                f"run {run_id} does not exist in tenant {tenant_id}",
            )
        if run.tenant_id != tenant_id:
            raise RuntimeAdapterError("run_scope_mismatch", "loaded Run is outside the requested tenant")
        return run

    async def _configured_model_turn_limit(self, command: StartRunCommand) -> int | None:
        """Resolve the immutable Run budget without a Runtime-side fallback."""
        requested = command.requested_model_turn_limit
        if requested is not None and (
            isinstance(requested, bool)
            or not isinstance(requested, int)
            or requested <= 0
        ):
            raise RuntimeAdapterError(
                "invalid_requested_model_turn_limit",
                "requested_model_turn_limit must be a positive integer",
            )

        if command.run_kind == "orchestration":
            if requested is not None:
                raise RuntimeAdapterError(
                    "invalid_requested_model_turn_limit",
                    "Planning Runs use their own bounded attempt policy",
                )
            return None

        if command.agent_id is None:
            raise RuntimeAdapterError(
                "agent_required",
                "Agent Runs require an agent_id before resolving their model turn limit",
            )
        result = await self._db.execute(
            select(Agent).where(
                Agent.tenant_id == command.tenant_id,
                Agent.id == command.agent_id,
            )
        )
        agent = result.scalar_one_or_none()
        if agent is None:
            raise RuntimeAdapterError(
                "agent_not_found",
                "Agent does not exist in the Runtime command tenant",
            )
        configured = agent.max_tool_rounds
        if (
            isinstance(configured, bool)
            or not isinstance(configured, int)
            or configured <= 0
        ):
            raise RuntimeAdapterError(
                "invalid_agent_model_turn_limit",
                "Agent max_tool_rounds must be a positive model turn limit",
            )
        return configured if requested is None else min(configured, requested)

    async def _require_agent_runtime_model(self, command: StartRunCommand) -> None:
        """Reject new tool-driven Agent Runs before any durable Run is created."""
        if command.run_kind == "orchestration":
            return
        if command.model_id is None:
            raise RuntimeAdapterError(
                "model_required",
                "Agent Runtime requires a pinned model before the Run can start",
            )
        result = await self._db.execute(
            select(LLMModel).where(LLMModel.id == command.model_id)
        )
        model = result.scalar_one_or_none()
        if (
            model is None
            or not model.enabled
            or model.tenant_id not in {None, command.tenant_id}
        ):
            raise RuntimeAdapterError(
                "model_unavailable",
                "Agent Runtime model is disabled or outside the command tenant",
            )
        try:
            ModelCapabilityResolver.require_native_tool_calling(model)
        except ModelCapabilityError as exc:
            raise RuntimeAdapterError(exc.code, str(exc)) from exc

    @staticmethod
    def _start_payload(command: StartRunCommand) -> dict:
        requested = command.requested_model_turn_limit
        if requested is not None and (
            isinstance(requested, bool)
            or not isinstance(requested, int)
            or requested <= 0
        ):
            raise RuntimeAdapterError(
                "invalid_requested_model_turn_limit",
                "requested_model_turn_limit must be a positive integer",
            )
        if command.run_kind == "orchestration" and requested is not None:
            raise RuntimeAdapterError(
                "invalid_requested_model_turn_limit",
                "Planning Runs use their own bounded attempt policy",
            )
        if RUNTIME_COMMAND_METADATA_KEY in command.payload:
            raise RuntimeAdapterError(
                "reserved_runtime_metadata",
                f"{RUNTIME_COMMAND_METADATA_KEY} is reserved for Runtime control metadata",
            )
        payload = dict(command.payload)
        if command.run_kind != "orchestration":
            payload[RUNTIME_COMMAND_METADATA_KEY] = {
                "requested_model_turn_limit": command.requested_model_turn_limit,
            }
        return payload

    def _require_existing_v2(self, run: AgentRun) -> None:
        decision = self._rollout.decide(
            agent_id=run.agent_id,
            source_type=run.source_type,
            existing_runtime_type=run.runtime_type,
        )
        self._require_v2(decision)
        self._require_run_identity(run)

    @staticmethod
    def _require_run_identity(run: AgentRun) -> None:
        if run.runtime_type != "langgraph":
            raise RuntimeAdapterError(
                "runtime_type_mismatch",
                "v2 adapter may only return handles for LangGraph Runs",
            )
        if not run.runtime_thread_id or not run.runtime_thread_id.strip():
            raise RuntimeAdapterError(
                "runtime_identity_mismatch",
                "Run thread_id must be a non-empty stable identity",
            )

    @staticmethod
    def _handle(
        run: AgentRun,
        command: AgentRunCommand,
        *,
        created: bool,
    ) -> RunHandle:
        RuntimeCommandIntake._require_run_identity(run)
        if command.tenant_id != run.tenant_id or command.run_id != run.id:
            raise RuntimeAdapterError(
                "command_scope_mismatch",
                "accepted command does not belong to the returned Run",
            )
        return RunHandle(
            tenant_id=run.tenant_id,
            run_id=run.id,
            thread_id=run.runtime_thread_id,
            command_id=command.id,
            runtime_type="langgraph",
            created=created,
        )

    async def start_run(self, command: StartRunCommand) -> RunHandle:
        """Atomically register one Run and its start command without committing."""
        start_payload = self._start_payload(command)
        existing = await self._find_start_retry(command)
        if existing is None:
            decision = self._rollout.decide(
                agent_id=command.agent_id,
                source_type=command.source_type,
            )
            runtime_type = "langgraph"
            graph_identity = (
                self._planning_graph
                if command.run_kind == "orchestration"
                and command.system_role == "group_planning"
                else self._current_graph
            )
            model_turn_limit = None
        else:
            decision = self._rollout.decide(
                agent_id=existing.agent_id,
                source_type=existing.source_type,
                existing_runtime_type=existing.runtime_type,
            )
            runtime_type = existing.runtime_type
            graph_identity = RuntimeGraphIdentity(
                name=existing.graph_name,
                version=existing.graph_version,
            )
            model_turn_limit = existing.model_turn_limit
        self._require_v2(decision)
        if existing is None:
            model_turn_limit = await self._configured_model_turn_limit(command)
            await self._require_agent_runtime_model(command)
        elif existing.run_kind == "orchestration":
            if model_turn_limit is not None:
                raise RuntimeAdapterError(
                    "invalid_stored_model_turn_limit",
                    "Planning Run unexpectedly has an Agent model turn limit",
                )
        elif (
            isinstance(model_turn_limit, bool)
            or not isinstance(model_turn_limit, int)
            or model_turn_limit <= 0
        ):
            raise RuntimeAdapterError(
                "invalid_stored_model_turn_limit",
                "Existing Agent Run has no valid immutable model turn limit",
            )
        if existing is not None:
            self._require_run_identity(existing)

        registered = await register_run_with_start(
            self._db,
            RunRegistration(
                tenant_id=command.tenant_id,
                agent_id=command.agent_id,
                session_id=command.session_id,
                source_type=command.source_type,
                source_id=command.source_id,
                source_execution_id=command.source_execution_id,
                correlation_id=command.correlation_id,
                origin_user_id=command.origin_user_id,
                origin_agent_id=command.origin_agent_id,
                parent_run_id=command.parent_run_id,
                root_run_id=command.root_run_id,
                goal=command.goal,
                run_kind=command.run_kind,
                system_role=command.system_role,
                model_id=command.model_id,
                model_turn_limit=model_turn_limit,
                runtime_thread_id=command.runtime_thread_id,
                runtime_type=runtime_type,
                graph_name=graph_identity.name,
                graph_version=graph_identity.version,
                scheduling_lane_key=command.scheduling_lane_key,
                scheduling_position_created_at=command.scheduling_position_created_at,
                scheduling_position_id=command.scheduling_position_id,
                delivery_status=command.delivery_status,
                delivery_target=command.delivery_target,
            ),
            start_payload=start_payload,
            start_idempotency_key=command.idempotency_key,
            actor_user_id=command.actor_user_id,
            actor_agent_id=command.actor_agent_id,
        )
        return self._handle(
            registered.run,
            registered.start_command,
            created=registered.created,
        )

    async def resume_run(self, command: ResumeRunCommand) -> RunHandle:
        """Persist a resume for an existing LangGraph Run without committing."""
        run = await self._get_run(tenant_id=command.tenant_id, run_id=command.run_id)
        self._require_existing_v2(run)
        enqueued = await enqueue_resume(
            self._db,
            tenant_id=command.tenant_id,
            run_id=command.run_id,
            payload=command.payload,
            idempotency_key=command.idempotency_key,
            actor_user_id=command.actor_user_id,
            actor_agent_id=command.actor_agent_id,
        )
        return self._handle(run, enqueued.command, created=enqueued.created)

    async def cancel_run(self, command: CancelRunCommand) -> RunHandle:
        """Persist cooperative cancellation without committing or mutating projections."""
        run = await self._get_run(tenant_id=command.tenant_id, run_id=command.run_id)
        self._require_existing_v2(run)
        enqueued = await enqueue_cancel(
            self._db,
            tenant_id=command.tenant_id,
            run_id=command.run_id,
            idempotency_key=command.idempotency_key,
            reason=command.reason,
            actor_user_id=command.actor_user_id,
            actor_agent_id=command.actor_agent_id,
        )
        return self._handle(run, enqueued.command, created=enqueued.created)


__all__ = [
    "RuntimeAdapterError",
    "RuntimeCommandIntake",
]
