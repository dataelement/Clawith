"""Terminal public-mention handoff for native Group Agent Runs.

The model submits participant IDs through the shared ``finish`` tool.  This
module validates every target before the source Run can become terminal, freezes
one immutable delivery intent, and later applies that exact intent inside the
ordinary Runtime delivery transaction.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Callable
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.models.agent import Agent
from app.models.agent_run import AgentRun
from app.models.audit import ChatMessage
from app.services.agent_runtime.adapter import RuntimeCommandIntake
from app.services.agent_runtime.contracts import RunHandle, StartRunCommand
from app.services.agent_runtime.config import RuntimeRolloutPolicy
from app.services.agent_runtime.cycle_guard import (
    AgentCycleGuard,
    AgentCycleGuardError,
)
from app.services.agent_runtime.state import (
    JsonObject,
    RuntimeContext,
    RuntimeGraphState,
)
from app.services.group_message_service import (
    GroupMessageServiceError,
    ResolvedGroupMention,
    _SenderScope,
    _dedupe_mentions,
    _load_sender_scope,
    _persist_message,
    _required_content,
    _resolve_mentions,
)


_INTENT_VERSION = 1


class GroupAgentHandoffError(RuntimeError):
    """A handoff cannot be frozen or applied without violating Group scope."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        repairable: bool,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.repairable = repairable


def _uuid(value: object, *, field: str) -> uuid.UUID:
    if not isinstance(value, str):
        raise GroupAgentHandoffError(
            "group_handoff_intent_invalid",
            f"{field} must be a UUID string",
            repairable=False,
        )
    try:
        return uuid.UUID(value)
    except ValueError as exc:
        raise GroupAgentHandoffError(
            "group_handoff_intent_invalid",
            f"{field} must be a UUID string",
            repairable=False,
        ) from exc


def _optional_uuid(value: object, *, field: str) -> uuid.UUID | None:
    if value is None:
        return None
    return _uuid(value, field=field)


def _optional_text(value: object, *, field: str) -> str | None:
    if value is None:
        return None
    if (
        not isinstance(value, str)
        or not value.strip()
        or value != value.strip()
    ):
        raise GroupAgentHandoffError(
            "group_handoff_intent_invalid",
            f"{field} must be a canonical non-empty string when present",
            repairable=False,
        )
    return value


def _timestamp(value: object, *, field: str) -> datetime:
    if not isinstance(value, str):
        raise GroupAgentHandoffError(
            "group_handoff_intent_invalid",
            f"{field} must be an ISO timestamp",
            repairable=False,
        )
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise GroupAgentHandoffError(
            "group_handoff_intent_invalid",
            f"{field} must be an ISO timestamp",
            repairable=False,
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise GroupAgentHandoffError(
            "group_handoff_intent_invalid",
            f"{field} must include a timezone",
            repairable=False,
        )
    return parsed


@dataclass(frozen=True, slots=True)
class GroupAgentHandoffIntent:
    """Immutable product-delivery facts saved in the terminal checkpoint."""

    source_run_id: uuid.UUID
    source_agent_id: uuid.UUID
    sender_participant_id: uuid.UUID
    group_id: uuid.UUID
    session_id: uuid.UUID
    child_parent_run_id: uuid.UUID
    child_root_run_id: uuid.UUID
    mention_participant_ids: tuple[uuid.UUID, ...]
    trigger_message_id: uuid.UUID
    cutoff_created_at: datetime
    idempotency_key: str
    origin_user_id: uuid.UUID | None
    mode: str | None
    plan_prompt: str | None

    def payload(self) -> JsonObject:
        return {
            "version": _INTENT_VERSION,
            "source_run_id": str(self.source_run_id),
            "source_agent_id": str(self.source_agent_id),
            "sender_participant_id": str(self.sender_participant_id),
            "group_id": str(self.group_id),
            "session_id": str(self.session_id),
            "child_parent_run_id": str(self.child_parent_run_id),
            "child_root_run_id": str(self.child_root_run_id),
            "mention_participant_ids": [
                str(participant_id) for participant_id in self.mention_participant_ids
            ],
            "trigger_message_id": str(self.trigger_message_id),
            "context_cutoff": {
                "message_id": str(self.trigger_message_id),
                "created_at": self.cutoff_created_at.isoformat(),
            },
            "idempotency_key": self.idempotency_key,
            "origin_user_id": (
                str(self.origin_user_id) if self.origin_user_id is not None else None
            ),
            "mode": self.mode,
            "plan_prompt": self.plan_prompt,
        }

    @classmethod
    def from_payload(cls, value: object) -> "GroupAgentHandoffIntent":
        if not isinstance(value, Mapping) or value.get("version") != _INTENT_VERSION:
            raise GroupAgentHandoffError(
                "group_handoff_intent_invalid",
                "Group handoff intent has an unsupported version",
                repairable=False,
            )
        raw_mentions = value.get("mention_participant_ids")
        if (
            not isinstance(raw_mentions, Sequence)
            or isinstance(raw_mentions, (str, bytes, bytearray))
            or not raw_mentions
        ):
            raise GroupAgentHandoffError(
                "group_handoff_intent_invalid",
                "Group handoff intent requires participant IDs",
                repairable=False,
            )
        parsed_mentions = tuple(
            _uuid(participant_id, field="mention_participant_ids")
            for participant_id in raw_mentions
        )
        try:
            mentions = _dedupe_mentions(list(parsed_mentions))
        except GroupMessageServiceError as exc:
            raise GroupAgentHandoffError(
                exc.code,
                str(exc),
                repairable=False,
            ) from exc
        if mentions != parsed_mentions:
            raise GroupAgentHandoffError(
                "group_handoff_intent_invalid",
                "Group handoff participant IDs must already be unique and ordered",
                repairable=False,
            )
        cutoff = value.get("context_cutoff")
        if not isinstance(cutoff, Mapping):
            raise GroupAgentHandoffError(
                "group_handoff_intent_invalid",
                "Group handoff intent requires a context cutoff",
                repairable=False,
            )
        trigger_message_id = _uuid(
            value.get("trigger_message_id"),
            field="trigger_message_id",
        )
        cutoff_message_id = _uuid(
            cutoff.get("message_id"),
            field="context_cutoff.message_id",
        )
        if cutoff_message_id != trigger_message_id:
            raise GroupAgentHandoffError(
                "group_handoff_intent_invalid",
                "Group handoff trigger and cutoff message IDs differ",
                repairable=False,
            )
        idempotency_key = value.get("idempotency_key")
        if (
            not isinstance(idempotency_key, str)
            or not idempotency_key.strip()
            or idempotency_key != idempotency_key.strip()
        ):
            raise GroupAgentHandoffError(
                "group_handoff_intent_invalid",
                "Group handoff intent requires a canonical stable idempotency key",
                repairable=False,
            )
        return cls(
            source_run_id=_uuid(value.get("source_run_id"), field="source_run_id"),
            source_agent_id=_uuid(
                value.get("source_agent_id"),
                field="source_agent_id",
            ),
            sender_participant_id=_uuid(
                value.get("sender_participant_id"),
                field="sender_participant_id",
            ),
            group_id=_uuid(value.get("group_id"), field="group_id"),
            session_id=_uuid(value.get("session_id"), field="session_id"),
            child_parent_run_id=_uuid(
                value.get("child_parent_run_id"),
                field="child_parent_run_id",
            ),
            child_root_run_id=_uuid(
                value.get("child_root_run_id"),
                field="child_root_run_id",
            ),
            mention_participant_ids=mentions,
            trigger_message_id=trigger_message_id,
            cutoff_created_at=_timestamp(
                cutoff.get("created_at"),
                field="context_cutoff.created_at",
            ),
            idempotency_key=idempotency_key,
            origin_user_id=_optional_uuid(
                value.get("origin_user_id"),
                field="origin_user_id",
            ),
            mode=_optional_text(value.get("mode"), field="mode"),
            plan_prompt=_optional_text(
                value.get("plan_prompt"),
                field="plan_prompt",
            ),
        )


@dataclass(frozen=True, slots=True)
class GroupAgentHandoffApplyResult:
    """The public message and new child Runs staged in the caller transaction."""

    message: ChatMessage
    run_handles: tuple[RunHandle, ...]


@dataclass(frozen=True, slots=True)
class _ValidatedHandoff:
    scope: _SenderScope
    targets: tuple[ResolvedGroupMention, ...]


async def _load_source_run(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    run_id: uuid.UUID,
) -> AgentRun:
    result = await db.execute(
        select(AgentRun).where(
            AgentRun.tenant_id == tenant_id,
            AgentRun.id == run_id,
        )
    )
    run = result.scalar_one_or_none()
    if run is None:
        raise GroupAgentHandoffError(
            "group_handoff_source_invalid",
            "The Group handoff source Run no longer exists",
            repairable=False,
        )
    return run


def _context_uuid(value: str | None, *, field: str) -> uuid.UUID:
    if value is None:
        raise GroupAgentHandoffError(
            "group_handoff_source_invalid",
            f"Runtime Context is missing {field}",
            repairable=False,
        )
    return _uuid(value, field=field)


def _snapshot_scope(
    state: RuntimeGraphState,
    context: RuntimeContext,
) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID, uuid.UUID]:
    initial_input = state["snapshots"].initial_input
    group_context = initial_input.get("group_context")
    if not isinstance(group_context, Mapping):
        raise GroupAgentHandoffError(
            "group_handoff_source_invalid",
            "Only a validated Group Agent Run can create a public handoff",
            repairable=False,
        )
    group = group_context.get("group")
    session = group_context.get("session")
    agent = group_context.get("agent")
    if not all(isinstance(item, Mapping) for item in (group, session, agent)):
        raise GroupAgentHandoffError(
            "group_handoff_source_invalid",
            "The frozen Group scope is incomplete",
            repairable=False,
        )
    group_id = _uuid(initial_input.get("group_id"), field="group_id")
    session_id = _uuid(initial_input.get("session_id"), field="session_id")
    source_agent_id = _context_uuid(context.agent_id, field="agent_id")
    sender_participant_id = _uuid(
        agent.get("participant_id"),
        field="group_context.agent.participant_id",
    )
    if (
        _uuid(group.get("group_id"), field="group_context.group.group_id")
        != group_id
        or _uuid(
            session.get("session_id"),
            field="group_context.session.session_id",
        )
        != session_id
        or _uuid(
            agent.get("agent_id"),
            field="group_context.agent.agent_id",
        )
        != source_agent_id
        or _context_uuid(context.session_id, field="session_id") != session_id
    ):
        raise GroupAgentHandoffError(
            "group_handoff_source_invalid",
            "Runtime Context and the frozen Group scope do not match",
            repairable=False,
        )
    return group_id, session_id, source_agent_id, sender_participant_id


def _source_run_matches(
    source_run: AgentRun,
    *,
    tenant_id: uuid.UUID,
    run_id: uuid.UUID,
    agent_id: uuid.UUID,
    session_id: uuid.UUID,
    group_id: uuid.UUID,
) -> None:
    if (
        source_run.tenant_id != tenant_id
        or source_run.id != run_id
        or source_run.agent_id != agent_id
        or source_run.session_id != session_id
        or source_run.source_type != "chat"
        or source_run.run_kind not in {"foreground", "delegated"}
        or source_run.system_role is not None
        or source_run.runtime_type != "langgraph"
        or source_run.runtime_thread_id != str(source_run.id)
        # Group start acknowledgement may already be delivered while the Run
        # remains active.  This projection is not a Runtime lifecycle state.
        or source_run.delivery_status not in {"pending", "delivered"}
    ):
        raise GroupAgentHandoffError(
            "group_handoff_source_invalid",
            "The source Run is not an active native Group Agent delivery source",
            repairable=False,
        )
    target = source_run.delivery_target
    if (
        not isinstance(target, Mapping)
        or target.get("kind") != "group"
        or target.get("session_id") != str(session_id)
        or target.get("group_id") != str(group_id)
    ):
        raise GroupAgentHandoffError(
            "group_handoff_source_invalid",
            "The source Run delivery target does not match its Group session",
            repairable=False,
        )


def _target_budget_available(agent: Agent, *, now: datetime) -> bool:
    if (
        isinstance(agent.max_tool_rounds, bool)
        or not isinstance(agent.max_tool_rounds, int)
        or agent.max_tool_rounds <= 0
    ):
        return False
    if agent.max_tokens_per_day and (agent.tokens_used_today or 0) >= agent.max_tokens_per_day:
        if agent.last_daily_reset is None or agent.last_daily_reset.date() == now.date():
            return False
    if agent.max_tokens_per_month and (agent.tokens_used_month or 0) >= agent.max_tokens_per_month:
        if (
            agent.last_monthly_reset is None
            or (
                agent.last_monthly_reset.year,
                agent.last_monthly_reset.month,
            )
            == (now.year, now.month)
        ):
            return False
    if (
        agent.max_llm_calls_per_day
        and (agent.llm_calls_today or 0) >= agent.max_llm_calls_per_day
        and (
            agent.llm_calls_reset_at is None
            or agent.llm_calls_reset_at.date() == now.date()
        )
    ):
        return False
    return True


async def _validate_targets(
    db: AsyncSession,
    *,
    source_run: AgentRun,
    source_agent_id: uuid.UUID,
    sender_participant_id: uuid.UUID,
    group_id: uuid.UUID,
    session_id: uuid.UUID,
    participant_ids: tuple[uuid.UUID, ...],
    settings: Settings,
    clock: datetime,
) -> _ValidatedHandoff:
    try:
        participant_ids = _dedupe_mentions(list(participant_ids))
        scope = await _load_sender_scope(
            db,
            tenant_id=source_run.tenant_id,
            group_id=group_id,
            session_id=session_id,
            sender_participant_id=sender_participant_id,
        )
        resolved = await _resolve_mentions(
            db,
            tenant_id=source_run.tenant_id,
            group_id=group_id,
            participant_ids=participant_ids,
        )
    except GroupMessageServiceError as exc:
        raise GroupAgentHandoffError(
            exc.code,
            str(exc),
            repairable=True,
        ) from exc
    if scope.agent_id != source_agent_id or scope.participant.id != sender_participant_id:
        raise GroupAgentHandoffError(
            "group_handoff_source_invalid",
            "The Group message sender is not the source Run Agent",
            repairable=False,
        )
    invalid = [
        mention
        for mention in resolved
        if (
            not mention.valid
            or not mention.triggers_agent
            or mention.participant_type != "agent"
            or mention.agent is None
            or mention.model is None
        )
    ]
    if invalid:
        reasons = ", ".join(
            f"{mention.participant_id}:{mention.reason or 'not_wakeable_agent'}"
            for mention in invalid
        )
        raise GroupAgentHandoffError(
            "group_handoff_target_invalid",
            "Every handoff target must be an active, wakeable Agent in this Group: "
            + reasons,
            repairable=True,
        )
    if tuple(mention.participant_id for mention in resolved) != participant_ids:
        raise GroupAgentHandoffError(
            "group_handoff_target_invalid",
            "Group mention resolution did not preserve the frozen participant order",
            repairable=True,
        )
    for mention in resolved:
        assert mention.agent is not None
        if not _target_budget_available(mention.agent, now=clock):
            raise GroupAgentHandoffError(
                "group_handoff_budget_unavailable",
                f"Agent participant {mention.participant_id} has no available Run budget",
                repairable=True,
            )
        rollout = RuntimeRolloutPolicy.from_settings(settings).decide(
            agent_id=mention.agent.id,
            source_type="chat",
        )
        if not rollout.use_v2:
            raise GroupAgentHandoffError(
                "group_handoff_runtime_unavailable",
                f"Agent participant {mention.participant_id} cannot start a durable Group child Run",
                repairable=True,
            )

    guard = AgentCycleGuard(max_cycle_count=settings.MAX_AGENT_CYCLE_COUNT)
    try:
        for mention in resolved:
            assert mention.agent is not None
            await guard.ensure_delegation_allowed(
                db,
                tenant_id=source_run.tenant_id,
                source_run_id=source_run.id,
                source_agent_id=source_agent_id,
                target_agent_id=mention.agent.id,
            )
    except AgentCycleGuardError as exc:
        raise GroupAgentHandoffError(
            exc.code,
            str(exc),
            repairable=True,
        ) from exc
    return _ValidatedHandoff(scope=scope, targets=resolved)


def _planning_values(state: RuntimeGraphState) -> tuple[str | None, str | None]:
    initial_input = state["snapshots"].initial_input
    group_context = initial_input.get("group_context")
    planning_hint = (
        group_context.get("planning_hint")
        if isinstance(group_context, Mapping)
        else None
    )
    raw_mode = initial_input.get("mode")
    if raw_mode is None and isinstance(planning_hint, Mapping):
        raw_mode = planning_hint.get("mode")
    raw_plan_prompt = initial_input.get("plan_prompt")
    if raw_plan_prompt is None and isinstance(planning_hint, Mapping):
        raw_plan_prompt = planning_hint.get("plan_prompt")
    return (
        raw_mode.strip() if isinstance(raw_mode, str) and raw_mode.strip() else None,
        (
            raw_plan_prompt.strip()
            if isinstance(raw_plan_prompt, str) and raw_plan_prompt.strip()
            else None
        ),
    )


async def preflight_group_agent_handoff(
    db: AsyncSession,
    *,
    state: RuntimeGraphState,
    context: RuntimeContext,
    content: str,
    mention_participant_ids: tuple[str, ...],
    settings: Settings | None = None,
    clock: Callable[[], datetime] | None = None,
) -> GroupAgentHandoffIntent:
    """Validate every target and freeze the exact post-checkpoint delivery input."""
    try:
        _required_content(content)
    except GroupMessageServiceError as exc:
        raise GroupAgentHandoffError(exc.code, str(exc), repairable=True) from exc
    if state["lifecycle"].get("status") != "running":
        raise GroupAgentHandoffError(
            "group_handoff_source_invalid",
            "A handoff finish may be submitted only by a running Group Agent Run",
            repairable=False,
        )
    tenant_id = _context_uuid(context.tenant_id, field="tenant_id")
    run_id = _context_uuid(context.run_id, field="run_id")
    group_id, session_id, source_agent_id, sender_participant_id = _snapshot_scope(
        state,
        context,
    )
    try:
        participant_ids = _dedupe_mentions(
            [_uuid(value, field="mention_participant_ids") for value in mention_participant_ids]
        )
    except GroupMessageServiceError as exc:
        raise GroupAgentHandoffError(exc.code, str(exc), repairable=True) from exc
    if not participant_ids:
        raise GroupAgentHandoffError(
            "group_handoff_target_invalid",
            "A Group handoff requires at least one participant ID",
            repairable=True,
        )
    source_run = await _load_source_run(
        db,
        tenant_id=tenant_id,
        run_id=run_id,
    )
    _source_run_matches(
        source_run,
        tenant_id=tenant_id,
        run_id=run_id,
        agent_id=source_agent_id,
        session_id=session_id,
        group_id=group_id,
    )
    context_parent_run_id = _optional_uuid(
        context.parent_run_id,
        field="parent_run_id",
    )
    context_root_run_id = _optional_uuid(
        context.root_run_id,
        field="root_run_id",
    )
    if (
        context_parent_run_id != source_run.parent_run_id
        or context_root_run_id != source_run.root_run_id
    ):
        raise GroupAgentHandoffError(
            "group_handoff_source_invalid",
            "Runtime Context lineage does not match the source Run",
            repairable=False,
        )
    now = (clock or (lambda: datetime.now(UTC)))()
    if now.tzinfo is None or now.utcoffset() is None:
        raise GroupAgentHandoffError(
            "group_handoff_cutoff_invalid",
            "Group handoff cutoff clock must be timezone-aware",
            repairable=False,
        )
    await _validate_targets(
        db,
        source_run=source_run,
        source_agent_id=source_agent_id,
        sender_participant_id=sender_participant_id,
        group_id=group_id,
        session_id=session_id,
        participant_ids=participant_ids,
        settings=settings or get_settings(),
        clock=now,
    )
    idempotency_key = f"run:{run_id}:terminal:completed"
    trigger_message_id = uuid.uuid5(
        run_id,
        f"delivery-message:{idempotency_key}",
    )
    mode, plan_prompt = _planning_values(state)
    return GroupAgentHandoffIntent(
        source_run_id=run_id,
        source_agent_id=source_agent_id,
        sender_participant_id=sender_participant_id,
        group_id=group_id,
        session_id=session_id,
        child_parent_run_id=run_id,
        child_root_run_id=source_run.root_run_id or run_id,
        mention_participant_ids=participant_ids,
        trigger_message_id=trigger_message_id,
        cutoff_created_at=now,
        idempotency_key=idempotency_key,
        origin_user_id=source_run.origin_user_id,
        mode=mode,
        plan_prompt=plan_prompt,
    )


def _handoff_child_command(
    *,
    source_run: AgentRun,
    scope: _SenderScope,
    intent: GroupAgentHandoffIntent,
    content: str,
    mentions: tuple[ResolvedGroupMention, ...],
    target: ResolvedGroupMention,
) -> StartRunCommand:
    if target.agent is None or target.model is None:
        raise GroupAgentHandoffError(
            "group_handoff_target_invalid",
            "A handoff target has no pinned Agent model",
            repairable=False,
        )
    source_execution_id = (
        f"group_mention:{intent.trigger_message_id}:agent:{target.agent.id}"
    )
    target_name = target.display_name or target.agent.name
    current_responsibility = (
        f"You are {target_name}. Respond in the current group as yourself only to "
        "the request addressed to you in the source message below. Do not repeat "
        "or forward the source message, and do not answer on behalf of any other "
        "mentioned participant. Reply once and normally finish without mentioning "
        "anyone. If you refer to another Agent without requiring a new reply, write "
        f"its display name without @.\n\nSource message:\n{content}"
    )
    payload: JsonObject = {
        "message_id": str(intent.trigger_message_id),
        "group_id": str(intent.group_id),
        "session_id": str(intent.session_id),
        "sender_participant_id": str(intent.sender_participant_id),
        "mention_targets": [mention.payload() for mention in mentions],
        "target_participant_id": str(target.participant_id),
        "source_channel": scope.session.source_channel,
        "source_run_id": str(source_run.id),
        "current_responsibility": current_responsibility,
        "context_cutoff": {
            "message_id": str(intent.trigger_message_id),
            "created_at": intent.cutoff_created_at.isoformat(),
        },
    }
    if intent.mode is not None:
        payload["mode"] = intent.mode
    if intent.plan_prompt is not None:
        payload["plan_prompt"] = intent.plan_prompt
    return StartRunCommand(
        tenant_id=source_run.tenant_id,
        agent_id=target.agent.id,
        session_id=intent.session_id,
        source_type="chat",
        source_id=str(intent.trigger_message_id),
        source_execution_id=source_execution_id,
        goal=current_responsibility,
        run_kind="delegated",
        model_id=target.model.id,
        scheduling_lane_key=(
            f"group_mention:{source_run.tenant_id}:{target.agent.id}"
        ),
        scheduling_position_created_at=intent.cutoff_created_at,
        scheduling_position_id=intent.trigger_message_id,
        delivery_status="pending",
        delivery_target={
            "kind": "group",
            "session_id": str(intent.session_id),
            "group_id": str(intent.group_id),
        },
        idempotency_key=f"start:{source_execution_id}",
        payload=payload,
        origin_user_id=intent.origin_user_id,
        origin_agent_id=source_run.agent_id,
        parent_run_id=intent.child_parent_run_id,
        root_run_id=intent.child_root_run_id,
        actor_user_id=intent.origin_user_id,
        actor_agent_id=source_run.agent_id,
    )


async def apply_group_agent_handoff(
    db: AsyncSession,
    *,
    source_run: AgentRun,
    content: str,
    intent_payload: object,
    expected_idempotency_key: str,
    expected_message_id: uuid.UUID,
    settings: Settings | None = None,
    clock: Callable[[], datetime] | None = None,
) -> GroupAgentHandoffApplyResult:
    """Revalidate and stage message, mentions, child Runs, and commands atomically."""
    intent = GroupAgentHandoffIntent.from_payload(intent_payload)
    if (
        intent.idempotency_key != expected_idempotency_key
        or intent.trigger_message_id != expected_message_id
    ):
        raise GroupAgentHandoffError(
            "group_handoff_intent_invalid",
            "Group handoff identity does not match the terminal delivery receipt",
            repairable=False,
        )
    _source_run_matches(
        source_run,
        tenant_id=source_run.tenant_id,
        run_id=intent.source_run_id,
        agent_id=intent.source_agent_id,
        session_id=intent.session_id,
        group_id=intent.group_id,
    )
    if (
        intent.child_parent_run_id != source_run.id
        or intent.child_root_run_id != (source_run.root_run_id or source_run.id)
        or intent.origin_user_id != source_run.origin_user_id
    ):
        raise GroupAgentHandoffError(
            "group_handoff_intent_invalid",
            "Group handoff lineage differs from the source Run",
            repairable=False,
        )
    try:
        content = _required_content(content)
    except GroupMessageServiceError as exc:
        raise GroupAgentHandoffError(exc.code, str(exc), repairable=False) from exc
    validated = await _validate_targets(
        db,
        source_run=source_run,
        source_agent_id=intent.source_agent_id,
        sender_participant_id=intent.sender_participant_id,
        group_id=intent.group_id,
        session_id=intent.session_id,
        participant_ids=intent.mention_participant_ids,
        settings=settings or get_settings(),
        clock=(clock or (lambda: datetime.now(UTC)))(),
    )
    intake = RuntimeCommandIntake(db, settings=settings or get_settings())
    handles: list[RunHandle] = []
    for target in validated.targets:
        handles.append(
            await intake.start_run(
                _handoff_child_command(
                    source_run=source_run,
                    scope=validated.scope,
                    intent=intent,
                    content=content,
                    mentions=validated.targets,
                    target=target,
                )
            )
        )
    previous_last_message_at = validated.scope.session.last_message_at
    previous_updated_at = validated.scope.session.updated_at
    try:
        message, _ = await _persist_message(
            db,
            message_id=intent.trigger_message_id,
            scope=validated.scope,
            content=content,
            mentions=validated.targets,
            clock=intent.cutoff_created_at,
        )
    except GroupMessageServiceError as exc:
        raise GroupAgentHandoffError(exc.code, str(exc), repairable=False) from exc
    if (
        previous_last_message_at is not None
        and (
            validated.scope.session.last_message_at is None
            or previous_last_message_at > validated.scope.session.last_message_at
        )
    ):
        validated.scope.session.last_message_at = previous_last_message_at
    if (
        previous_updated_at is not None
        and (
            validated.scope.session.updated_at is None
            or previous_updated_at > validated.scope.session.updated_at
        )
    ):
        validated.scope.session.updated_at = previous_updated_at
    if message.created_at != intent.cutoff_created_at:
        raise GroupAgentHandoffError(
            "group_handoff_intent_invalid",
            "Existing public message has a different immutable cutoff",
            repairable=False,
        )
    return GroupAgentHandoffApplyResult(
        message=message,
        run_handles=tuple(handles),
    )


__all__ = [
    "GroupAgentHandoffApplyResult",
    "GroupAgentHandoffError",
    "GroupAgentHandoffIntent",
    "apply_group_agent_handoff",
    "preflight_group_agent_handoff",
]
