"""Checkpoint-driven Planning child scheduling and child-result resumption."""

from __future__ import annotations

from collections.abc import Mapping
import re
import uuid

from sqlalchemy import select

from app.config import Settings, get_settings
from app.models.agent import Agent
from app.models.agent_run import AgentRun
from app.models.audit import ChatMessage
from app.models.chat_session import ChatSession
from app.models.group import Group, GroupMember
from app.models.llm import LLMModel
from app.models.participant import Participant
from app.services.agent_runtime.adapter import TransactionalAgentRuntimeAdapter
from app.services.agent_runtime.command_worker import (
    CheckpointObservation,
    RuntimeRunRecord,
    RuntimeSessionFactory,
)
from app.services.agent_runtime.contracts import StartRunCommand
from app.services.agent_runtime.persistence import enqueue_resume
from app.services.agent_runtime.planning import checkpoint_plan, ready_plan_steps


_PLANNING_ROLE = "group_planning"
_ACTIVE_AGENT_STATUSES = frozenset({"creating", "running", "idle"})
_STEP_SOURCE = re.compile(r"^group_mention:(?P<message_id>[0-9a-f-]{36}):step:(?P<step_id>.+)$")


class PlanningSchedulingError(RuntimeError):
    """A committed Planning checkpoint cannot be reconciled safely."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _step_failure_resume(
    *,
    root: AgentRun,
    step_id: str,
    error_code: str,
) -> dict:
    return {
        "resume_type": "agent_result",
        "correlation_id": f"planning:{root.id}",
        "payload": {
            "step_id": step_id,
            "status": "failed",
            "child_run_id": None,
            "result_summary": None,
            "error": {
                "code": error_code,
                "message": "The planned Agent step could not be started.",
            },
        },
    }


async def _enqueue_step_failure(
    db,
    *,
    root: AgentRun,
    step_id: str,
    error_code: str,
) -> None:
    await enqueue_resume(
        db,
        tenant_id=root.tenant_id,
        run_id=root.id,
        payload=_step_failure_resume(
            root=root,
            step_id=step_id,
            error_code=error_code,
        ),
        idempotency_key=f"resume:planning:{root.id}:step:{step_id}:{error_code}",
        actor_user_id=root.origin_user_id,
        actor_agent_id=root.origin_agent_id,
    )


def _dependency_summaries(plan: Mapping[str, object], step: Mapping[str, object]) -> list[dict]:
    raw_steps = plan.get("steps")
    dependencies = step.get("depends_on_step_ids")
    if not isinstance(raw_steps, list) or not isinstance(dependencies, list):
        raise PlanningSchedulingError(
            "invalid_planning_checkpoint",
            "Planning steps or dependencies are malformed",
        )
    by_id = {
        str(candidate.get("step_id")): candidate
        for candidate in raw_steps
        if isinstance(candidate, Mapping)
    }
    output = []
    for dependency_id in dependencies:
        dependency = by_id.get(str(dependency_id))
        if dependency is None or dependency.get("status") != "completed":
            raise PlanningSchedulingError(
                "planning_dependency_not_ready",
                "Scheduler selected a step whose dependency is not complete",
            )
        output.append(
            {
                "planning_step_id": str(dependency_id),
                "child_run_id": dependency.get("child_run_id"),
                "result_summary": dependency.get("result_summary"),
            }
        )
    return output


def _planning_runtime_instruction(step: Mapping[str, object]) -> str:
    """Keep Planning as coordination scope without replacing the user request."""
    required_tools = step.get("required_tool_names", [])
    required_artifacts = step.get("required_artifact_paths", [])
    tool_text = ", ".join(str(name) for name in required_tools) or "none"
    artifact_text = ", ".join(str(path) for path in required_artifacts) or "none"
    return "\n".join(
        (
            "The user message in this Run is the complete, authoritative original group request.",
            (
                "Planning supplies coordination order and a bounded work scope only; "
                "it never replaces, rewrites, or summarizes the original request."
            ),
            "Execute only the assigned Planning step in this Run; do not complete sibling steps.",
            f"Assigned Planning step ID: {step['step_id']}",
            f"Assigned Planning step: {step['instruction']}",
            f"Required group tools: {tool_text}",
            f"Required workspace artifacts: {artifact_text}",
            (
                "Use dependency summaries as evidence, preserve every applicable constraint "
                "from the original request, then report only this step's result."
            ),
        )
    )


class PlanningCheckpointScheduler:
    """Create every ready child Run from a committed Planning checkpoint."""

    def __init__(
        self,
        *,
        session_factory: RuntimeSessionFactory,
        settings: Settings | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._settings = settings or get_settings()

    async def handle(
        self,
        *,
        run: RuntimeRunRecord,
        checkpoint: CheckpointObservation,
    ) -> None:
        if run.registry.system_role != _PLANNING_ROLE:
            return
        status = checkpoint.state["lifecycle"]["status"]
        if status == "completed":
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
                    root = result.scalar_one_or_none()
                    if root is None:
                        raise PlanningSchedulingError(
                            "run_not_found",
                            "Completed Planning Run no longer exists",
                        )
                    root.delivery_status = "not_required"
                    await db.flush()
            return
        if status != "waiting_agent":
            return

        plan = checkpoint_plan(checkpoint.state)
        ready = ready_plan_steps(plan)
        if not ready:
            return

        async with self._session_factory() as db:
            async with db.begin():
                root_result = await db.execute(
                    select(AgentRun)
                    .where(
                        AgentRun.tenant_id == run.tenant_id,
                        AgentRun.id == run.run_id,
                    )
                    .with_for_update()
                )
                root = root_result.scalar_one_or_none()
                if (
                    root is None
                    or root.run_kind != "orchestration"
                    or root.system_role != _PLANNING_ROLE
                    or root.agent_id is not None
                    or root.source_id is None
                    or root.session_id is None
                ):
                    raise PlanningSchedulingError(
                        "planning_identity_mismatch",
                        "Planning root registry is incomplete",
                    )
                try:
                    message_id = uuid.UUID(root.source_id)
                except ValueError as exc:
                    raise PlanningSchedulingError(
                        "planning_source_invalid",
                        "Planning root source_id must be the trigger message UUID",
                    ) from exc
                message_result = await db.execute(
                    select(ChatMessage).where(
                        ChatMessage.id == message_id,
                        ChatMessage.conversation_id == str(root.session_id),
                    )
                )
                message = message_result.scalar_one_or_none()
                if message is None or message.created_at is None:
                    raise PlanningSchedulingError(
                        "planning_source_missing",
                        "Planning trigger message is unavailable",
                    )
                session_result = await db.execute(
                    select(ChatSession).where(
                        ChatSession.id == root.session_id,
                        ChatSession.tenant_id == root.tenant_id,
                        ChatSession.session_type == "group",
                        ChatSession.deleted_at.is_(None),
                    )
                )
                session = session_result.scalar_one_or_none()
                if session is None or session.group_id is None:
                    raise PlanningSchedulingError(
                        "planning_session_unavailable",
                        "Planning group session is unavailable",
                    )
                group_result = await db.execute(
                    select(Group).where(
                        Group.id == session.group_id,
                        Group.tenant_id == root.tenant_id,
                        Group.deleted_at.is_(None),
                    )
                )
                if group_result.scalar_one_or_none() is None:
                    raise PlanningSchedulingError(
                        "planning_group_unavailable",
                        "Planning group is unavailable",
                    )

                adapter = TransactionalAgentRuntimeAdapter(db, settings=self._settings)
                initial_input = checkpoint.state["snapshots"].initial_input
                mention_targets = initial_input.get("mention_targets", [])
                sender_participant_id = initial_input.get("sender_participant_id")
                for step in ready:
                    step_id = str(step["step_id"])
                    agent_id = uuid.UUID(str(step["agent_id"]))
                    source_execution_id = f"group_mention:{message.id}:step:{step_id}"
                    existing_result = await db.execute(
                        select(AgentRun.id).where(
                            AgentRun.source_type == "chat",
                            AgentRun.source_execution_id == source_execution_id,
                        )
                    )
                    if existing_result.scalar_one_or_none() is not None:
                        continue

                    agent_result = await db.execute(
                        select(Agent).where(
                            Agent.id == agent_id,
                            Agent.tenant_id == root.tenant_id,
                            Agent.status.in_(_ACTIVE_AGENT_STATUSES),
                            Agent.is_expired.is_(False),
                            Agent.access_mode != "private",
                        )
                    )
                    agent = agent_result.scalar_one_or_none()
                    if agent is None or agent.primary_model_id is None:
                        await _enqueue_step_failure(
                            db,
                            root=root,
                            step_id=step_id,
                            error_code="agent_unavailable",
                        )
                        continue
                    participant_result = await db.execute(
                        select(Participant).where(
                            Participant.type == "agent",
                            Participant.ref_id == agent.id,
                        )
                    )
                    participant = participant_result.scalar_one_or_none()
                    if participant is None:
                        await _enqueue_step_failure(
                            db,
                            root=root,
                            step_id=step_id,
                            error_code="agent_not_group_member",
                        )
                        continue
                    membership_result = await db.execute(
                        select(GroupMember).where(
                            GroupMember.group_id == session.group_id,
                            GroupMember.participant_id == participant.id,
                            GroupMember.removed_at.is_(None),
                        )
                    )
                    if membership_result.scalar_one_or_none() is None:
                        await _enqueue_step_failure(
                            db,
                            root=root,
                            step_id=step_id,
                            error_code="agent_not_group_member",
                        )
                        continue
                    model_result = await db.execute(
                        select(LLMModel).where(
                            LLMModel.id == agent.primary_model_id,
                            LLMModel.enabled.is_(True),
                        )
                    )
                    model = model_result.scalar_one_or_none()
                    if model is None or model.tenant_id not in {None, root.tenant_id}:
                        await _enqueue_step_failure(
                            db,
                            root=root,
                            step_id=step_id,
                            error_code="agent_model_unavailable",
                        )
                        continue

                    await adapter.start_run(
                        StartRunCommand(
                            tenant_id=root.tenant_id,
                            agent_id=agent.id,
                            session_id=session.id,
                            source_type="chat",
                            source_id=str(message.id),
                            source_execution_id=source_execution_id,
                            goal=str(step["instruction"]),
                            run_kind="foreground",
                            model_id=model.id,
                            parent_run_id=root.id,
                            root_run_id=root.id,
                            scheduling_lane_key=f"group_mention:{root.tenant_id}:{agent.id}",
                            scheduling_position_created_at=message.created_at,
                            scheduling_position_id=message.id,
                            delivery_status="pending",
                            delivery_target={
                                "kind": "group",
                                "session_id": str(session.id),
                                "group_id": str(session.group_id),
                            },
                            idempotency_key=f"start:{source_execution_id}",
                            payload={
                                "message_id": str(message.id),
                                "group_id": str(session.group_id),
                                "session_id": str(session.id),
                                "sender_participant_id": sender_participant_id,
                                "mention_targets": mention_targets,
                                "target_participant_id": str(participant.id),
                                "planning_root_run_id": str(root.id),
                                "planning_step_id": step_id,
                                "planning_instruction": str(step["instruction"]),
                                "planning_required_tool_names": list(
                                    step.get("required_tool_names", [])
                                ),
                                "planning_required_artifact_paths": list(
                                    step.get("required_artifact_paths", [])
                                ),
                                "input_content": message.content,
                                "runtime_instruction": _planning_runtime_instruction(step),
                                "related_run_summaries": _dependency_summaries(plan, step),
                                "source_channel": session.source_channel,
                            },
                            origin_user_id=root.origin_user_id,
                            origin_agent_id=root.origin_agent_id,
                            actor_user_id=root.origin_user_id,
                            actor_agent_id=root.origin_agent_id,
                        )
                    )


class PlanningChildCompletionHandler:
    """Resume one Planning root from a child terminal checkpoint exactly once."""

    def __init__(self, *, session_factory: RuntimeSessionFactory) -> None:
        self._session_factory = session_factory

    async def handle(
        self,
        *,
        run: RuntimeRunRecord,
        checkpoint: CheckpointObservation,
    ) -> None:
        status = checkpoint.state["lifecycle"]["status"]
        if status not in {"completed", "failed", "cancelled"}:
            return
        if run.registry.parent_run_id is None or run.registry.system_role is not None:
            return

        async with self._session_factory() as db:
            async with db.begin():
                child_result = await db.execute(
                    select(AgentRun).where(
                        AgentRun.tenant_id == run.tenant_id,
                        AgentRun.id == run.run_id,
                    )
                )
                child = child_result.scalar_one_or_none()
                if (
                    child is None
                    or child.parent_run_id is None
                    or child.source_execution_id is None
                ):
                    return
                match = _STEP_SOURCE.fullmatch(child.source_execution_id)
                if match is None:
                    return
                root_result = await db.execute(
                    select(AgentRun).where(
                        AgentRun.tenant_id == child.tenant_id,
                        AgentRun.id == child.parent_run_id,
                        AgentRun.run_kind == "orchestration",
                        AgentRun.system_role == _PLANNING_ROLE,
                    )
                )
                root = root_result.scalar_one_or_none()
                if root is None:
                    return
                lifecycle = checkpoint.state["lifecycle"]
                await enqueue_resume(
                    db,
                    tenant_id=root.tenant_id,
                    run_id=root.id,
                    payload={
                        "resume_type": "agent_result",
                        "correlation_id": f"planning:{root.id}",
                        "payload": {
                            "step_id": match.group("step_id"),
                            "status": status,
                            "child_run_id": str(child.id),
                            "result_summary": lifecycle.get("result_summary"),
                            "error": lifecycle.get("error"),
                        },
                    },
                    idempotency_key=(
                        f"resume:planning:{root.id}:child:{child.id}:terminal:{status}"
                    ),
                    actor_agent_id=child.agent_id,
                )


__all__ = [
    "PlanningCheckpointScheduler",
    "PlanningChildCompletionHandler",
    "PlanningSchedulingError",
]
