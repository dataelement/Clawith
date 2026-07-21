"""Apply one completed Planning v2 checkpoint to product entry Runs."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import uuid

from sqlalchemy import select

from app.config import Settings, get_settings
from app.models.agent_run import AgentRun
from app.models.audit import ChatMessage
from app.services.agent_runtime.adapter import RuntimeCommandIntake
from app.services.agent_runtime.command_worker import (
    CheckpointObservation,
    RuntimeRunRecord,
    RuntimeSessionFactory,
)
from app.services.agent_runtime.contracts import StartRunCommand
from app.services.agent_runtime.planning import checkpoint_plan
from app.services.group_message_service import (
    GroupMessageServiceError,
    ResolvedGroupMention,
    _SenderScope,
    _load_sender_scope,
    _resolve_mentions,
)


_PLANNING_ROLE = "group_planning"


class PlanningSchedulingError(RuntimeError):
    """A committed Planning checkpoint cannot be reconciled safely."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _uuid(value: object, *, field: str) -> uuid.UUID:
    if not isinstance(value, str):
        raise PlanningSchedulingError(
            "invalid_planning_checkpoint",
            f"{field} must be a UUID string",
        )
    try:
        return uuid.UUID(value)
    except ValueError as exc:
        raise PlanningSchedulingError(
            "invalid_planning_checkpoint",
            f"{field} must be a UUID string",
        ) from exc


def _required_mapping(value: object, *, field: str) -> Mapping[object, object]:
    if not isinstance(value, Mapping):
        raise PlanningSchedulingError(
            "invalid_planning_checkpoint",
            f"{field} must be an object",
        )
    return value


def _required_sequence(value: object, *, field: str) -> Sequence[object]:
    if not isinstance(value, Sequence) or isinstance(
        value,
        (str, bytes, bytearray),
    ):
        raise PlanningSchedulingError(
            "invalid_planning_checkpoint",
            f"{field} must be an array",
        )
    return value


def _candidate_participants(
    initial_input: Mapping[str, object],
) -> dict[uuid.UUID, uuid.UUID]:
    candidates = _required_sequence(
        initial_input.get("candidate_agents"),
        field="candidate_agents",
    )
    output: dict[uuid.UUID, uuid.UUID] = {}
    participant_ids: set[uuid.UUID] = set()
    for index, candidate_value in enumerate(candidates):
        candidate = _required_mapping(
            candidate_value,
            field=f"candidate_agents[{index}]",
        )
        agent_id = _uuid(
            candidate.get("agent_id"),
            field=f"candidate_agents[{index}].agent_id",
        )
        participant_id = _uuid(
            candidate.get("participant_id"),
            field=f"candidate_agents[{index}].participant_id",
        )
        if agent_id in output or participant_id in participant_ids:
            raise PlanningSchedulingError(
                "invalid_planning_checkpoint",
                "candidate_agents identities must be unique",
            )
        output[agent_id] = participant_id
        participant_ids.add(participant_id)
    if len(output) < 2:
        raise PlanningSchedulingError(
            "invalid_planning_checkpoint",
            "Planning requires at least two candidate Agents",
        )
    return output


def _authoritative_agent_mentions(
    mentions: object,
) -> dict[uuid.UUID, uuid.UUID]:
    raw_mentions = _required_sequence(mentions, field="trigger message mentions")
    output: dict[uuid.UUID, uuid.UUID] = {}
    participant_ids: set[uuid.UUID] = set()
    for index, mention_value in enumerate(raw_mentions):
        mention = _required_mapping(
            mention_value,
            field=f"trigger message mentions[{index}]",
        )
        if mention.get("valid") is not True or mention.get("triggers_agent") is not True:
            continue
        if mention.get("participant_type") != "agent":
            raise PlanningSchedulingError(
                "planning_source_invalid",
                "A triggering mention must resolve to an Agent participant",
            )
        participant_id = _uuid(
            mention.get("participant_id"),
            field=f"trigger message mentions[{index}].participant_id",
        )
        agent_id = _uuid(
            mention.get("participant_ref_id"),
            field=f"trigger message mentions[{index}].participant_ref_id",
        )
        if agent_id in output or participant_id in participant_ids:
            raise PlanningSchedulingError(
                "planning_source_invalid",
                "Triggering Agent mentions must be unique",
            )
        output[agent_id] = participant_id
        participant_ids.add(participant_id)
    return output


def _root_group_scope(
    root: AgentRun,
    run: RuntimeRunRecord,
) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    if (
        root.run_kind != "orchestration"
        or root.system_role != _PLANNING_ROLE
        or root.agent_id is not None
        or root.source_type != "chat"
        or root.source_id is None
        or root.session_id is None
        or root.runtime_thread_id != str(root.id)
        or run.thread_id != root.runtime_thread_id
        or run.session_id != str(root.session_id)
        or run.run_kind != root.run_kind
        or run.source_type != root.source_type
    ):
        raise PlanningSchedulingError(
            "planning_identity_mismatch",
            "Planning root identity is incomplete or differs from the checkpoint Run",
        )
    message_id = _uuid(root.source_id, field="Planning root source_id")
    delivery_target = _required_mapping(
        root.delivery_target,
        field="Planning root delivery_target",
    )
    if delivery_target.get("kind") != "group":
        raise PlanningSchedulingError(
            "planning_identity_mismatch",
            "Planning root delivery target must be a Group",
        )
    group_id = _uuid(
        delivery_target.get("group_id"),
        field="Planning root delivery_target.group_id",
    )
    delivery_session_id = _uuid(
        delivery_target.get("session_id"),
        field="Planning root delivery_target.session_id",
    )
    if delivery_session_id != root.session_id:
        raise PlanningSchedulingError(
            "planning_identity_mismatch",
            "Planning root session and delivery target differ",
        )
    return message_id, group_id, root.session_id


def _initial_scope(
    checkpoint: CheckpointObservation,
    *,
    message_id: uuid.UUID,
    group_id: uuid.UUID,
    session_id: uuid.UUID,
) -> tuple[Mapping[str, object], uuid.UUID, Sequence[object]]:
    initial_input = checkpoint.state["snapshots"].initial_input
    if (
        _uuid(initial_input.get("message_id"), field="initial_input.message_id") != message_id
        or _uuid(initial_input.get("group_id"), field="initial_input.group_id") != group_id
        or _uuid(initial_input.get("session_id"), field="initial_input.session_id") != session_id
    ):
        raise PlanningSchedulingError(
            "planning_identity_mismatch",
            "Planning input scope differs from the Planning root",
        )
    sender_participant_id = _uuid(
        initial_input.get("sender_participant_id"),
        field="initial_input.sender_participant_id",
    )
    mention_targets = _required_sequence(
        initial_input.get("mention_targets"),
        field="initial_input.mention_targets",
    )
    return initial_input, sender_participant_id, mention_targets


def _validate_source(
    *,
    root: AgentRun,
    message: ChatMessage,
    scope: _SenderScope,
    mention_targets: Sequence[object],
    candidate_participants: Mapping[uuid.UUID, uuid.UUID],
) -> None:
    if (
        message.created_at is None
        or message.participant_id != scope.participant.id
        or message.conversation_id != str(scope.session.id)
        or root.goal != message.content
        or root.origin_user_id != scope.user_id
        or root.origin_agent_id != scope.agent_id
    ):
        raise PlanningSchedulingError(
            "planning_source_invalid",
            "Planning trigger message or sender no longer matches the root input",
        )
    if message.mentions != list(mention_targets):
        raise PlanningSchedulingError(
            "planning_source_invalid",
            "Planning mention snapshot differs from the trigger message",
        )
    if _authoritative_agent_mentions(message.mentions) != candidate_participants:
        raise PlanningSchedulingError(
            "planning_source_invalid",
            "Planning candidates differ from the authoritative trigger mentions",
        )


def _validate_entry_targets(
    *,
    entries: Sequence[Mapping[str, object]],
    candidate_participants: Mapping[uuid.UUID, uuid.UUID],
    targets: Sequence[ResolvedGroupMention],
) -> tuple[tuple[Mapping[str, object], ResolvedGroupMention], ...]:
    if len(entries) != len(targets):
        raise PlanningSchedulingError(
            "planning_entry_unavailable",
            "Planning entry resolution returned an incomplete target set",
        )
    validated = []
    for entry, target in zip(entries, targets, strict=True):
        agent_id = _uuid(entry.get("agent_id"), field="entry_steps.agent_id")
        expected_participant_id = candidate_participants[agent_id]
        if (
            target.participant_id != expected_participant_id
            or target.participant_type != "agent"
            or target.participant_ref_id != agent_id
            or target.valid is not True
            or target.triggers_agent is not True
            or target.agent is None
            or target.agent.id != agent_id
            or target.model is None
            or target.agent.primary_model_id != target.model.id
        ):
            raise PlanningSchedulingError(
                "planning_entry_unavailable",
                "A Planning entry Agent is no longer an available Group target",
            )
        validated.append((entry, target))
    return tuple(validated)


def _entry_command(
    *,
    root: AgentRun,
    message: ChatMessage,
    scope: _SenderScope,
    mention_targets: Sequence[object],
    plan: Mapping[str, object],
    entry: Mapping[str, object],
    target: ResolvedGroupMention,
) -> StartRunCommand:
    if message.created_at is None or target.agent is None or target.model is None:
        raise PlanningSchedulingError(
            "planning_entry_unavailable",
            "A Planning entry is missing its pinned execution identity",
        )
    instruction = entry["instruction"]
    mode = plan["mode"]
    plan_prompt = plan["plan_prompt"]
    if not all(isinstance(value, str) for value in (instruction, mode, plan_prompt)):
        raise PlanningSchedulingError(
            "invalid_planning_checkpoint",
            "Planning text fields must be strings",
        )
    source_execution_id = f"group_mention:{message.id}:entry:{target.agent.id}"
    return StartRunCommand(
        tenant_id=root.tenant_id,
        agent_id=target.agent.id,
        session_id=scope.session.id,
        source_type="chat",
        source_id=str(message.id),
        source_execution_id=source_execution_id,
        goal=instruction,
        run_kind="foreground",
        model_id=target.model.id,
        parent_run_id=root.id,
        root_run_id=root.id,
        scheduling_lane_key=f"group_mention:{root.tenant_id}:{target.agent.id}",
        scheduling_position_created_at=message.created_at,
        scheduling_position_id=message.id,
        delivery_status="pending",
        delivery_target={
            "kind": "group",
            "session_id": str(scope.session.id),
            "group_id": str(scope.group.id),
        },
        idempotency_key=f"start:{source_execution_id}",
        payload={
            "message_id": str(message.id),
            "group_id": str(scope.group.id),
            "session_id": str(scope.session.id),
            "sender_participant_id": str(scope.participant.id),
            "mention_targets": list(mention_targets),
            "target_participant_id": str(target.participant_id),
            "mode": mode,
            "plan_prompt": plan_prompt,
            "current_responsibility": instruction,
            "context_cutoff": {
                "message_id": str(message.id),
                "created_at": message.created_at.isoformat(),
            },
            "source_channel": scope.session.source_channel,
        },
        origin_user_id=root.origin_user_id,
        origin_agent_id=root.origin_agent_id,
        actor_user_id=root.origin_user_id,
        actor_agent_id=root.origin_agent_id,
    )


class PlanningCheckpointScheduler:
    """Create only Planning v2 entry Runs after a stable completed checkpoint."""

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
        if run.system_role != _PLANNING_ROLE:
            return
        if checkpoint.state["lifecycle"]["status"] != "completed":
            return

        plan = checkpoint_plan(checkpoint.state)
        raw_entries = plan["entry_steps"]
        if not isinstance(raw_entries, Sequence):
            raise PlanningSchedulingError(
                "invalid_planning_checkpoint",
                "Planning entry_steps must be an array",
            )
        entries = tuple(_required_mapping(entry, field="entry_steps") for entry in raw_entries)

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
                if root is None:
                    raise PlanningSchedulingError(
                        "run_not_found",
                        "Completed Planning Run no longer exists",
                    )
                message_id, group_id, session_id = _root_group_scope(root, run)
                initial_input, sender_participant_id, mention_targets = _initial_scope(
                    checkpoint,
                    message_id=message_id,
                    group_id=group_id,
                    session_id=session_id,
                )
                candidate_participants = _candidate_participants(initial_input)

                message_result = await db.execute(
                    select(ChatMessage).where(
                        ChatMessage.id == message_id,
                        ChatMessage.conversation_id == str(session_id),
                    )
                )
                message = message_result.scalar_one_or_none()
                if message is None:
                    raise PlanningSchedulingError(
                        "planning_source_missing",
                        "Planning trigger message is unavailable",
                    )
                try:
                    scope = await _load_sender_scope(
                        db,
                        tenant_id=root.tenant_id,
                        group_id=group_id,
                        session_id=session_id,
                        sender_participant_id=sender_participant_id,
                    )
                except GroupMessageServiceError as exc:
                    raise PlanningSchedulingError(exc.code, str(exc)) from exc
                _validate_source(
                    root=root,
                    message=message,
                    scope=scope,
                    mention_targets=mention_targets,
                    candidate_participants=candidate_participants,
                )

                entry_participant_ids = tuple(
                    candidate_participants[_uuid(entry.get("agent_id"), field="entry_steps.agent_id")]
                    for entry in entries
                )
                try:
                    targets = await _resolve_mentions(
                        db,
                        tenant_id=root.tenant_id,
                        group_id=group_id,
                        participant_ids=entry_participant_ids,
                    )
                except GroupMessageServiceError as exc:
                    raise PlanningSchedulingError(exc.code, str(exc)) from exc
                validated_entries = _validate_entry_targets(
                    entries=entries,
                    candidate_participants=candidate_participants,
                    targets=targets,
                )

                adapter = RuntimeCommandIntake(db, settings=self._settings)
                for entry, target in validated_entries:
                    await adapter.start_run(
                        _entry_command(
                            root=root,
                            message=message,
                            scope=scope,
                            mention_targets=mention_targets,
                            plan=plan,
                            entry=entry,
                            target=target,
                        )
                    )
                root.delivery_status = "not_required"
                await db.flush()


__all__ = ["PlanningCheckpointScheduler", "PlanningSchedulingError"]
