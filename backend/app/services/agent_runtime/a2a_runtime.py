"""Transactional Agent-to-Agent delegation behind Runtime tool receipts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.core.permissions import (
    evaluate_agent_relationship_status,
    evaluate_roster_agent_visibility,
)
from app.models.agent import Agent
from app.models.agent_run import AgentRun
from app.models.agent_tool_execution import AgentToolExecution
from app.models.audit import ChatMessage
from app.models.chat_session import ChatSession
from app.models.gateway_message import GatewayMessage
from app.models.org import AgentAgentRelationship
from app.services.agent_runtime.adapter import RuntimeCommandIntake
from app.services.agent_runtime.command_worker import RuntimeSessionFactory
from app.services.agent_runtime.config import decide_runtime_v2
from app.services.agent_runtime.contracts import ResumeRunCommand, StartRunCommand
from app.services.agent_runtime.cycle_guard import (
    AgentCycleGuard,
    AgentCycleGuardError,
)
from app.services.agent_runtime.tool_execution import (
    ToolExecutionOutcome,
    ToolExecutionReservation,
    mark_tool_execution_failed,
    mark_tool_execution_succeeded,
)
from app.services import agent_directory
from app.services.participant_identity import get_or_create_agent_participant


A2AMode = Literal["notify", "consult", "task_delegate"]
_RESPONSE_MODES = frozenset({"consult", "task_delegate"})


class A2ARuntimeError(RuntimeError):
    """A model-proposed A2A request is invalid before any side effect commits."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class A2ARuntimeToolResult:
    """Durable tool result plus an optional source-Run interrupt."""

    outcome: ToolExecutionOutcome
    target_run_id: uuid.UUID | None
    waiting_request: dict | None = None


@dataclass(frozen=True, slots=True)
class GatewayA2ARuntimeIntake:
    """Durable acceptance receipt for an OpenClaw-to-native message."""

    gateway_message_id: uuid.UUID
    target_run_id: uuid.UUID
    session_id: uuid.UUID


@dataclass(frozen=True, slots=True)
class GatewayA2ARuntimeCompletion:
    """A native source Run resumed from an OpenClaw report."""

    source_run_id: uuid.UUID
    resumed: bool


@dataclass(frozen=True, slots=True)
class _A2ARequest:
    target_agent_id: uuid.UUID | None
    target_name: str | None
    message: str
    mode: A2AMode


def _request(arguments: dict) -> _A2ARequest:
    raw_target_id = str(arguments.get("target_agent_id") or "").strip()
    target_name = str(arguments.get("agent_name") or "").strip()
    message = str(arguments.get("message") or "").strip()
    raw_mode = str(arguments.get("msg_type") or "notify").strip().lower()
    target_agent_id: uuid.UUID | None = None
    if raw_target_id:
        try:
            target_agent_id = uuid.UUID(raw_target_id)
        except ValueError as exc:
            raise A2ARuntimeError(
                "a2a_target_id_invalid",
                "A2A target_agent_id must be a valid UUID",
            ) from exc
    if (target_agent_id is None and not target_name) or not message:
        raise A2ARuntimeError(
            "a2a_input_missing",
            "A2A requires target_agent_id and message",
        )
    if raw_mode not in {"notify", "consult", "task_delegate"}:
        raise A2ARuntimeError(
            "a2a_mode_invalid",
            "A2A msg_type must be notify, consult, or task_delegate",
        )
    return _A2ARequest(
        target_agent_id=target_agent_id,
        target_name=target_name or None,
        message=message,
        mode=raw_mode,  # type: ignore[arg-type]
    )


def _source_execution_id(source_run_id: uuid.UUID, tool_call_id: str) -> str:
    occurrence_id = uuid.uuid5(source_run_id, f"a2a-target:{tool_call_id}")
    return f"a2a:{occurrence_id}"


def _correlation_id(
    source_run_id: uuid.UUID,
    tool_call_id: str,
    mode: A2AMode,
) -> str:
    correlation = uuid.uuid5(source_run_id, f"a2a-result:{tool_call_id}")
    return f"a2a:{mode}:{correlation}"


def a2a_mode_from_correlation(correlation_id: str) -> A2AMode:
    parts = correlation_id.split(":", 2)
    if len(parts) != 3 or parts[0] != "a2a":
        raise A2ARuntimeError(
            "a2a_correlation_invalid",
            "A2A correlation ID has an invalid format",
        )
    mode = parts[1]
    if mode not in {"notify", "consult", "task_delegate"}:
        raise A2ARuntimeError(
            "a2a_correlation_invalid",
            "A2A correlation ID has an unsupported mode",
        )
    try:
        uuid.UUID(parts[2])
    except ValueError as exc:
        raise A2ARuntimeError(
            "a2a_correlation_invalid",
            "A2A correlation ID has an invalid occurrence UUID",
        ) from exc
    return mode  # type: ignore[return-value]


def a2a_waiting_request(
    *,
    source_run_id: uuid.UUID,
    tool_call_id: str,
    arguments: dict,
    result_ref: str | None,
) -> dict | None:
    """Rebuild the same interrupt from a reusable A2A tool receipt."""
    request = _request(arguments)
    if request.mode not in _RESPONSE_MODES or result_ref is None:
        return None
    ref_field: str
    ref_prefix: str
    if result_ref.startswith("agent-run:"):
        ref_field = "target_run_id"
        ref_prefix = "agent-run:"
    elif result_ref.startswith("gateway-message:"):
        ref_field = "gateway_message_id"
        ref_prefix = "gateway-message:"
    else:
        return None
    try:
        target_ref_id = uuid.UUID(result_ref.removeprefix(ref_prefix))
    except ValueError as exc:
        raise A2ARuntimeError(
            "a2a_result_ref_invalid",
            "A2A tool receipt has an invalid target reference",
        ) from exc
    return {
        "waiting_type": "agent",
        "correlation_id": _correlation_id(
            source_run_id,
            tool_call_id,
            request.mode,
        ),
        "reason": f"waiting_for_{request.mode}",
        ref_field: str(target_ref_id),
    }


def _session_id(tenant_id: uuid.UUID, first: uuid.UUID, second: uuid.UUID) -> uuid.UUID:
    ordered = sorted((first, second), key=str)
    return uuid.uuid5(
        tenant_id,
        f"a2a-session:{ordered[0]}:{ordered[1]}",
    )


def _input_message_id(source_run_id: uuid.UUID, tool_call_id: str) -> uuid.UUID:
    return uuid.uuid5(source_run_id, f"a2a-input:{tool_call_id}")


def _gateway_message_id(source_run_id: uuid.UUID, tool_call_id: str) -> uuid.UUID:
    return uuid.uuid5(source_run_id, f"a2a-gateway:{tool_call_id}")


async def complete_gateway_a2a_runtime(
    db: AsyncSession,
    *,
    gateway_message: GatewayMessage,
    target_agent: Agent,
    result: str,
    settings: Settings | None = None,
) -> GatewayA2ARuntimeCompletion | None:
    """Resume a native source Run when its OpenClaw target reports a result.

    A missing tool receipt means this is a user-originated or OpenClaw-to-OpenClaw
    gateway message and the caller should retain the ordinary gateway behavior.
    """
    normalized_result = result.strip()
    if not normalized_result:
        raise A2ARuntimeError(
            "a2a_gateway_result_missing",
            "Gateway A2A result must not be blank",
        )
    if (
        target_agent.tenant_id is None
        or target_agent.agent_type != "openclaw"
        or gateway_message.agent_id != target_agent.id
        or gateway_message.sender_agent_id is None
    ):
        raise A2ARuntimeError(
            "a2a_gateway_result_scope_mismatch",
            "Gateway A2A result does not match an OpenClaw target message",
        )

    receipt_result = await db.execute(
        select(AgentToolExecution)
        .where(
            AgentToolExecution.tenant_id == target_agent.tenant_id,
            AgentToolExecution.tool_name == "send_message_to_agent",
            AgentToolExecution.status == "succeeded",
            AgentToolExecution.result_ref
            == f"gateway-message:{gateway_message.id}",
        )
        .limit(2)
    )
    receipts = receipt_result.scalars().all()
    if not receipts:
        return None
    if len(receipts) != 1:
        raise A2ARuntimeError(
            "a2a_gateway_receipt_ambiguous",
            "Gateway A2A result matches more than one tool receipt",
        )
    receipt = receipts[0]
    request = _request(receipt.sanitized_arguments or {})

    source_result = await db.execute(
        select(AgentRun).where(
            AgentRun.tenant_id == target_agent.tenant_id,
            AgentRun.id == receipt.run_id,
        )
    )
    source_run = source_result.scalar_one_or_none()
    if (
        source_run is None
        or source_run.agent_id != gateway_message.sender_agent_id
        or source_run.runtime_type != "langgraph"
    ):
        raise A2ARuntimeError(
            "a2a_gateway_source_run_mismatch",
            "Gateway A2A receipt does not match its native source Run",
        )
    if request.mode == "notify":
        return GatewayA2ARuntimeCompletion(
            source_run_id=source_run.id,
            resumed=False,
        )

    correlation_id = _correlation_id(
        source_run.id,
        receipt.tool_call_id,
        request.mode,
    )
    await RuntimeCommandIntake(
        db,
        settings=settings or get_settings(),
    ).resume_run(
        ResumeRunCommand(
            tenant_id=target_agent.tenant_id,
            run_id=source_run.id,
            idempotency_key=f"gateway-a2a-result:{gateway_message.id}",
            payload={
                "resume_type": "agent_result",
                "correlation_id": correlation_id,
                "payload": {
                    "gateway_message_id": str(gateway_message.id),
                    "target_agent_id": str(target_agent.id),
                    "status": "completed",
                    "result_summary": normalized_result,
                    "artifact_refs": [],
                    "error": None,
                },
            },
            actor_user_id=source_run.origin_user_id,
            actor_agent_id=target_agent.id,
        )
    )
    return GatewayA2ARuntimeCompletion(
        source_run_id=source_run.id,
        resumed=True,
    )


async def _load_source_run(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    source_run_id: uuid.UUID,
    source_agent_id: uuid.UUID,
) -> AgentRun:
    result = await db.execute(
        select(AgentRun)
        .where(
            AgentRun.tenant_id == tenant_id,
            AgentRun.id == source_run_id,
        )
        .with_for_update()
    )
    source_run = result.scalar_one_or_none()
    if (
        source_run is None
        or source_run.agent_id != source_agent_id
        or source_run.runtime_type != "langgraph"
    ):
        raise A2ARuntimeError(
            "a2a_source_run_invalid",
            "A2A source Run does not match the executing Agent",
        )
    return source_run


async def _resolve_target(
    db: AsyncSession,
    *,
    source_agent: Agent,
    target_agent_id: uuid.UUID | None,
    target_name: str | None,
    actor_user_id: uuid.UUID | None,
) -> Agent:
    if target_agent_id is not None:
        target_result = await db.execute(
            select(Agent).where(
                Agent.tenant_id == source_agent.tenant_id,
                Agent.id != source_agent.id,
                Agent.id == target_agent_id,
            )
        )
        target = target_result.scalar_one_or_none()
    else:
        assert target_name is not None
        exact_result = await db.execute(
            select(Agent).where(
                Agent.tenant_id == source_agent.tenant_id,
                Agent.id != source_agent.id,
                Agent.name == target_name,
            )
        )
        target = exact_result.scalars().first()
    if target is None and target_agent_id is None:
        assert target_name is not None
        safe_name = target_name.replace("%", "").replace("_", r"\_")
        fuzzy_result = await db.execute(
            select(Agent)
            .where(
                Agent.tenant_id == source_agent.tenant_id,
                Agent.id != source_agent.id,
                Agent.name.ilike(f"%{safe_name}%"),
            )
            .limit(2)
        )
        matches = fuzzy_result.scalars().all()
        if len(matches) > 1:
            raise A2ARuntimeError(
                "a2a_target_ambiguous",
                f"More than one Agent matches {target_name!r}",
            )
        target = matches[0] if matches else None
    if target is None:
        target_label = str(target_agent_id) if target_agent_id else repr(target_name)
        raise A2ARuntimeError(
            "a2a_target_not_found",
            f"No related Agent matches {target_label}",
        )

    if target_agent_id is not None:
        authorized_custom_target = False
        if target.access_mode == "custom":
            authorized_custom_target = (
                await agent_directory.is_custom_agent_target_authorized(
                    db,
                    source_agent_id=source_agent.id,
                    target_agent_id=target.id,
                )
            )
        visibility = evaluate_roster_agent_visibility(
            source_agent,
            target,
            authorized_custom_target=authorized_custom_target,
        )
        if not visibility.visible:
            raise A2ARuntimeError(
                "a2a_target_not_visible",
                f"Agent {target.name} is not visible in the source Agent's Directory",
            )
        if not visibility.can_contact:
            reason = visibility.unavailable_reason or "target_unavailable"
            raise A2ARuntimeError(
                "a2a_target_unavailable",
                f"Agent {target.name} is unavailable ({reason})",
            )
        return target

    if target.is_expired or target.status not in {"creating", "running", "idle"}:
        raise A2ARuntimeError(
            "a2a_target_unavailable",
            f"Agent {target.name} is unavailable",
        )

    relationship_result = await db.execute(
        select(AgentAgentRelationship).where(
            AgentAgentRelationship.agent_id == source_agent.id,
            AgentAgentRelationship.target_agent_id == target.id,
        )
    )
    relationship = relationship_result.scalar_one_or_none()
    if relationship is None:
        raise A2ARuntimeError(
            "a2a_relationship_missing",
            f"Agent {source_agent.name} has no relationship with {target.name}",
        )
    relationship.__dict__["target_agent"] = target
    relationship_status = await evaluate_agent_relationship_status(
        db,
        relationship,
        current_user_id=actor_user_id,
    )
    if relationship_status["access_status"] != "active":
        reason = relationship_status.get("access_status_reason") or "restricted"
        raise A2ARuntimeError(
            "a2a_relationship_restricted",
            f"Relationship with {target.name} is not active ({reason})",
        )
    return target


async def ensure_a2a_session(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    source_agent: Agent,
    target_agent: Agent,
    owner_user_id: uuid.UUID,
) -> tuple[ChatSession, uuid.UUID, uuid.UUID]:
    source_participant = await get_or_create_agent_participant(
        db,
        source_agent.id,
        source_agent.name,
        source_agent.avatar_url,
    )
    target_participant = await get_or_create_agent_participant(
        db,
        target_agent.id,
        target_agent.name,
        target_agent.avatar_url,
    )
    ordered = sorted((source_agent.id, target_agent.id), key=str)
    result = await db.execute(
        select(ChatSession).where(
            ChatSession.tenant_id == tenant_id,
            ChatSession.session_type == "a2a",
            ChatSession.agent_id == ordered[0],
            ChatSession.peer_agent_id == ordered[1],
            ChatSession.deleted_at.is_(None),
        )
    )
    session = result.scalar_one_or_none()
    if session is None:
        deterministic_id = _session_id(
            tenant_id,
            source_agent.id,
            target_agent.id,
        )
        stored = await db.get(ChatSession, deterministic_id)
        session_id = deterministic_id if stored is None else uuid.uuid4()
        session = ChatSession(
            id=session_id,
            tenant_id=tenant_id,
            session_type="a2a",
            group_id=None,
            agent_id=ordered[0],
            peer_agent_id=ordered[1],
            user_id=owner_user_id,
            created_by_participant_id=source_participant.id,
            title=f"{source_agent.name} ↔ {target_agent.name}"[:200],
            source_channel="agent",
            is_group=False,
            participant_id=source_participant.id,
            is_primary=False,
            deleted_at=None,
        )
        db.add(session)
        await db.flush()
    elif session.tenant_id != tenant_id or session.session_type != "a2a":
        raise A2ARuntimeError(
            "a2a_session_scope_mismatch",
            "A2A session exists outside the requested tenant scope",
        )
    return session, source_participant.id, target_participant.id


async def enqueue_gateway_a2a_runtime(
    db: AsyncSession,
    *,
    source_agent: Agent,
    target_agent: Agent,
    content: str,
    message_id: uuid.UUID | None = None,
    settings: Settings | None = None,
) -> GatewayA2ARuntimeIntake | None:
    """Atomically persist a gateway message, Chat input, and target Run Command."""
    runtime_settings = settings or get_settings()
    message = content.strip()
    if not message:
        raise A2ARuntimeError(
            "a2a_input_missing",
            "Gateway A2A content must not be blank",
        )
    if (
        source_agent.tenant_id is None
        or source_agent.tenant_id != target_agent.tenant_id
        or source_agent.id == target_agent.id
    ):
        raise A2ARuntimeError(
            "a2a_scope_mismatch",
            "Gateway A2A Agents must be distinct members of one tenant",
        )
    if source_agent.agent_type != "openclaw" or target_agent.agent_type == "openclaw":
        raise A2ARuntimeError(
            "a2a_gateway_type_mismatch",
            "Gateway Runtime intake requires an OpenClaw source and native target",
        )
    if target_agent.is_expired or target_agent.status not in {
        "creating",
        "running",
        "idle",
    }:
        raise A2ARuntimeError(
            "a2a_target_unavailable",
            f"Agent {target_agent.name} is unavailable",
        )
    if target_agent.primary_model_id is None:
        raise A2ARuntimeError(
            "a2a_target_model_missing",
            f"Agent {target_agent.name} has no primary model",
        )
    decision = decide_runtime_v2(
        agent_id=target_agent.id,
        source_type="a2a",
        settings=runtime_settings,
    )
    if not decision.use_v2:
        return None

    tenant_id = source_agent.tenant_id
    owner_user_id = source_agent.creator_id
    session, source_participant_id, _ = await ensure_a2a_session(
        db,
        tenant_id=tenant_id,
        source_agent=source_agent,
        target_agent=target_agent,
        owner_user_id=owner_user_id,
    )
    resolved_message_id = message_id or uuid.uuid4()
    inbound = await db.get(GatewayMessage, resolved_message_id)
    if inbound is None:
        inbound = GatewayMessage(
            id=resolved_message_id,
            agent_id=target_agent.id,
            sender_agent_id=source_agent.id,
            content=message,
            status="delivered",
            conversation_id=str(session.id),
            delivered_at=datetime.now(UTC),
        )
        db.add(inbound)
    elif (
        inbound.agent_id != target_agent.id
        or inbound.sender_agent_id != source_agent.id
        or inbound.content != message
        or inbound.conversation_id != str(session.id)
    ):
        raise A2ARuntimeError(
            "a2a_gateway_message_mismatch",
            "Gateway message ID already exists with different immutable input",
        )

    chat_message_id = uuid.uuid5(
        resolved_message_id,
        "gateway-a2a-input",
    )
    chat_message = await db.get(ChatMessage, chat_message_id)
    if chat_message is None:
        db.add(
            ChatMessage(
                id=chat_message_id,
                agent_id=session.agent_id,
                user_id=owner_user_id,
                role="user",
                content=message,
                conversation_id=str(session.id),
                participant_id=source_participant_id,
                mentions=[],
            )
        )
    elif (
        chat_message.conversation_id != str(session.id)
        or chat_message.content != message
        or chat_message.participant_id != source_participant_id
    ):
        raise A2ARuntimeError(
            "a2a_input_mismatch",
            "Gateway A2A Chat input has different immutable content",
        )

    source_execution_id = f"gateway-a2a:{resolved_message_id}"
    handle = await RuntimeCommandIntake(
        db,
        settings=runtime_settings,
    ).start_run(
        StartRunCommand(
            tenant_id=tenant_id,
            agent_id=target_agent.id,
            session_id=session.id,
            source_type="a2a",
            source_id=str(session.id),
            source_execution_id=source_execution_id,
            correlation_id=f"gateway:a2a:{resolved_message_id}",
            goal=(
                "Answer this message from an OpenClaw Agent and return the result "
                f"through the gateway. Source Agent: {source_agent.name}. Request: {message}"
            ),
            run_kind="delegated",
            model_id=target_agent.primary_model_id,
            origin_user_id=owner_user_id,
            origin_agent_id=source_agent.id,
            delivery_status="not_required",
            idempotency_key=f"start:{source_execution_id}",
            payload={
                "message_id": str(chat_message_id),
                "input_content": message,
                "runtime_instruction": (
                    "This Run was initiated by another digital employee through the "
                    "OpenClaw gateway. Reply naturally; the verified final answer is "
                    "delivered back automatically. Do not call send_message_to_agent "
                    "merely to return this answer."
                ),
                "application_tools_enabled": True,
                "a2a_mode": "consult",
                "source_agent_id": str(source_agent.id),
                "source_agent_name": source_agent.name,
                "gateway_message_id": str(resolved_message_id),
                "gateway_reply_agent_id": str(source_agent.id),
            },
            actor_user_id=owner_user_id,
            actor_agent_id=source_agent.id,
        )
    )
    session.last_message_at = datetime.now(UTC)
    return GatewayA2ARuntimeIntake(
        gateway_message_id=resolved_message_id,
        target_run_id=handle.run_id,
        session_id=session.id,
    )


def _target_goal(source_agent: Agent, request: _A2ARequest) -> str:
    if request.mode == "notify":
        prefix = "Process this one-way notification; take useful internal action if needed"
    elif request.mode == "consult":
        prefix = "Answer this concise consultation from another Agent"
    else:
        prefix = "Complete this delegated task and return a usable result"
    return f"{prefix}. Source Agent: {source_agent.name}. Request: {request.message}"


def _target_runtime_instruction(mode: A2AMode) -> str:
    if mode in _RESPONSE_MODES:
        return (
            "This Run was initiated by another digital employee through Clawith "
            "A2A. The verified final answer is returned to the source Run "
            "automatically. Do not call send_message_to_agent merely to return "
            "this answer."
        )
    return (
        "This is a one-way Clawith A2A notification. Process it within the "
        "authorized scope and do not call send_message_to_agent merely to "
        "acknowledge receipt."
    )


def _accepted_summary(target: Agent, mode: A2AMode) -> str:
    if mode == "notify":
        return f"✅ Notification delivered to {target.name} for asynchronous processing."
    if mode == "consult":
        return f"✅ Consultation sent to {target.name}; this Run will resume with the answer."
    return f"✅ Task delegated to {target.name}; this Run will resume with the result."


class RuntimeA2AService:
    """Create target Runs and settle the source tool receipt in one transaction."""

    def __init__(
        self,
        *,
        session_factory: RuntimeSessionFactory,
        settings: Settings | None = None,
        cycle_guard: AgentCycleGuard | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._settings = settings or get_settings()
        self._cycle_guard = cycle_guard or AgentCycleGuard()

    async def _mark_rejected(
        self,
        *,
        tenant_id: uuid.UUID,
        reservation: ToolExecutionReservation,
        lease_owner: str,
        error: A2ARuntimeError,
    ) -> A2ARuntimeToolResult:
        summary = f"[A2A:{error.code}] {error}"
        async with self._session_factory() as db:
            async with db.begin():
                execution = await mark_tool_execution_failed(
                    db,
                    tenant_id=tenant_id,
                    execution_id=reservation.execution.id,
                    lease_owner=lease_owner,
                    result_summary=summary,
                )
        return A2ARuntimeToolResult(
            outcome=ToolExecutionOutcome(
                status="failed",
                result_summary=execution.result_summary,
                result_ref=execution.result_ref,
            ),
            target_run_id=None,
        )

    async def execute(
        self,
        *,
        tenant_id: uuid.UUID,
        source_run_id: uuid.UUID,
        source_agent_id: uuid.UUID,
        tool_call_id: str,
        arguments: dict,
        reservation: ToolExecutionReservation,
        lease_owner: str,
        actor_user_id: uuid.UUID | None,
    ) -> A2ARuntimeToolResult:
        """Persist every native or OpenClaw A2A side effect behind one receipt."""
        try:
            request = _request(arguments)
            async with self._session_factory() as db:
                async with db.begin():
                    source_run = await _load_source_run(
                        db,
                        tenant_id=tenant_id,
                        source_run_id=source_run_id,
                        source_agent_id=source_agent_id,
                    )
                    source_result = await db.execute(
                        select(Agent).where(
                            Agent.tenant_id == tenant_id,
                            Agent.id == source_agent_id,
                        )
                    )
                    source_agent = source_result.scalar_one_or_none()
                    if source_agent is None:
                        raise A2ARuntimeError(
                            "a2a_source_agent_missing",
                            "A2A source Agent is unavailable",
                        )
                    owner_user_id = (
                        source_run.origin_user_id
                        or actor_user_id
                        or source_agent.creator_id
                    )
                    target = await _resolve_target(
                        db,
                        source_agent=source_agent,
                        target_agent_id=request.target_agent_id,
                        target_name=request.target_name,
                        actor_user_id=owner_user_id,
                    )
                    is_openclaw = target.agent_type == "openclaw"
                    if not is_openclaw:
                        decision = decide_runtime_v2(
                            agent_id=target.id,
                            source_type="a2a",
                            settings=self._settings,
                        )
                        if not decision.use_v2:
                            raise A2ARuntimeError(
                                "runtime_disabled",
                                "Durable Runtime is required for native A2A execution",
                            )
                    if not is_openclaw and target.primary_model_id is None:
                        raise A2ARuntimeError(
                            "a2a_target_model_missing",
                            f"Agent {target.name} has no primary model",
                        )

                    await self._cycle_guard.ensure_delegation_allowed(
                        db,
                        tenant_id=tenant_id,
                        source_run_id=source_run_id,
                        source_agent_id=source_agent.id,
                        target_agent_id=target.id,
                    )
                    session, source_participant_id, _ = await ensure_a2a_session(
                        db,
                        tenant_id=tenant_id,
                        source_agent=source_agent,
                        target_agent=target,
                        owner_user_id=owner_user_id,
                    )
                    message_id = _input_message_id(source_run_id, tool_call_id)
                    message = await db.get(ChatMessage, message_id)
                    if message is None:
                        db.add(
                            ChatMessage(
                                id=message_id,
                                agent_id=session.agent_id,
                                user_id=owner_user_id,
                                role="user",
                                content=request.message,
                                conversation_id=str(session.id),
                                participant_id=source_participant_id,
                                mentions=[],
                            )
                        )
                    elif (
                        message.conversation_id != str(session.id)
                        or message.content != request.message
                        or message.participant_id != source_participant_id
                    ):
                        raise A2ARuntimeError(
                            "a2a_input_mismatch",
                            "Deterministic A2A input message has different content",
                        )

                    target_run_id: uuid.UUID | None = None
                    if is_openclaw:
                        gateway_message_id = _gateway_message_id(
                            source_run_id,
                            tool_call_id,
                        )
                        gateway_message = await db.get(
                            GatewayMessage,
                            gateway_message_id,
                        )
                        if gateway_message is None:
                            db.add(
                                GatewayMessage(
                                    id=gateway_message_id,
                                    agent_id=target.id,
                                    sender_agent_id=source_agent.id,
                                    sender_user_id=owner_user_id,
                                    content=request.message,
                                    status="pending",
                                    conversation_id=str(session.id),
                                )
                            )
                        elif (
                            gateway_message.agent_id != target.id
                            or gateway_message.sender_agent_id != source_agent.id
                            or gateway_message.content != request.message
                            or gateway_message.conversation_id != str(session.id)
                        ):
                            raise A2ARuntimeError(
                                "a2a_gateway_message_mismatch",
                                "Gateway A2A receipt has different immutable input",
                            )
                        result_ref = f"gateway-message:{gateway_message_id}"
                    else:
                        correlation_id = _correlation_id(
                            source_run_id,
                            tool_call_id,
                            request.mode,
                        )
                        source_execution_id = _source_execution_id(
                            source_run_id,
                            tool_call_id,
                        )
                        handle = await RuntimeCommandIntake(
                            db,
                            settings=self._settings,
                        ).start_run(
                            StartRunCommand(
                                tenant_id=tenant_id,
                                agent_id=target.id,
                                session_id=session.id,
                                source_type="a2a",
                                source_id=str(session.id),
                                source_execution_id=source_execution_id,
                                correlation_id=correlation_id,
                                goal=_target_goal(source_agent, request),
                                run_kind="delegated",
                                model_id=target.primary_model_id,
                                origin_user_id=owner_user_id,
                                origin_agent_id=source_agent.id,
                                parent_run_id=source_run.id,
                                root_run_id=source_run.root_run_id or source_run.id,
                                delivery_status="not_required",
                                idempotency_key=f"start:{source_execution_id}",
                                payload={
                                    "message_id": str(message_id),
                                    "input_content": request.message,
                                    "a2a_mode": request.mode,
                                    "runtime_instruction": _target_runtime_instruction(
                                        request.mode
                                    ),
                                    "source_agent_id": str(source_agent.id),
                                    "source_agent_name": source_agent.name,
                                    "source_run_id": str(source_run.id),
                                    "correlation_id": correlation_id,
                                },
                                actor_user_id=owner_user_id,
                                actor_agent_id=source_agent.id,
                            )
                        )
                        target_run_id = handle.run_id
                        result_ref = f"agent-run:{handle.run_id}"
                    summary = _accepted_summary(target, request.mode)
                    execution = await mark_tool_execution_succeeded(
                        db,
                        tenant_id=tenant_id,
                        execution_id=reservation.execution.id,
                        lease_owner=lease_owner,
                        result_summary=summary,
                        result_ref=result_ref,
                    )
                    session.last_message_at = datetime.now(UTC)

            waiting_request = a2a_waiting_request(
                source_run_id=source_run_id,
                tool_call_id=tool_call_id,
                arguments=arguments,
                result_ref=execution.result_ref,
            )
            return A2ARuntimeToolResult(
                outcome=ToolExecutionOutcome(
                    status="succeeded",
                    result_summary=execution.result_summary,
                    result_ref=execution.result_ref,
                ),
                target_run_id=target_run_id,
                waiting_request=waiting_request,
            )
        except AgentCycleGuardError as exc:
            return await self._mark_rejected(
                tenant_id=tenant_id,
                reservation=reservation,
                lease_owner=lease_owner,
                error=A2ARuntimeError(exc.code, str(exc)),
            )
        except A2ARuntimeError as exc:
            return await self._mark_rejected(
                tenant_id=tenant_id,
                reservation=reservation,
                lease_owner=lease_owner,
                error=exc,
            )


__all__ = [
    "A2ARuntimeError",
    "A2ARuntimeToolResult",
    "GatewayA2ARuntimeCompletion",
    "GatewayA2ARuntimeIntake",
    "RuntimeA2AService",
    "a2a_mode_from_correlation",
    "a2a_waiting_request",
    "complete_gateway_a2a_runtime",
    "enqueue_gateway_a2a_runtime",
    "ensure_a2a_session",
]
