"""Transaction-scoped TriggerExecution intake for the durable Agent Runtime."""

from __future__ import annotations

from datetime import UTC, datetime
import json
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.models.agent import Agent
from app.models.audit import ChatMessage
from app.models.chat_session import ChatSession
from app.models.trigger import AgentTrigger
from app.models.trigger_execution import TriggerExecution
from app.services.agent_runtime.adapter import RuntimeCommandIntake
from app.services.agent_runtime.config import decide_runtime_v2
from app.services.agent_runtime.contracts import RunHandle, StartRunCommand
from app.services.chat_session_service import ensure_primary_platform_session
from app.services.participant_identity import get_or_create_agent_participant
from app.services.trigger_runtime.executions import build_execution_runtime_trigger


class TriggerRuntimeIntakeError(RuntimeError):
    """A TriggerExecution selected for Runtime v2 cannot be registered safely."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _trigger_config(trigger: AgentTrigger) -> dict:
    config = trigger.config or {}
    if isinstance(config, dict):
        return config
    if isinstance(config, str):
        try:
            parsed = json.loads(config)
        except (json.JSONDecodeError, TypeError):
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _trigger_event_data(trigger: AgentTrigger) -> dict[str, str]:
    """Extract bounded low-trust event facts from the executable instruction."""
    config = _trigger_config(trigger)
    event_data: dict[str, str] = {}
    if trigger.type == "on_message" and config.get("_matched_message"):
        event_data["matched_message"] = str(config["_matched_message"])[:500]
        event_data["matched_from"] = str(config.get("_matched_from", "?"))[:200]
    if trigger.type == "webhook" and config.get("_webhook_payload"):
        payload = str(config["_webhook_payload"])
        event_data["webhook_payload"] = (
            payload if len(payload) <= 2_000 else payload[:2_000] + "... (truncated)"
        )
    return event_data


def build_trigger_context(triggers: list[AgentTrigger]) -> str:
    """Build the stable user-visible wake input shared by legacy and v2 paths."""
    context_parts: list[str] = []
    for trigger in triggers:
        part = f"触发器：{trigger.name} ({trigger.type})\n原因：{trigger.reason}"
        if trigger.name == "daily_okr_collection":
            part += (
                "\n执行要求：先调用 get_okr_settings 确认日报收集是否开启。"
                "如果开启，只能联系你关系网络中的成员和数字员工来收集今天的最终日报，"
                "并整理成不超过 2000 字的正式日报；"
                "如果未开启，则说明本次无需执行并停止。"
            )
        elif trigger.name in (
            "daily_okr_report",
            "weekly_okr_report",
            "monthly_okr_report",
        ):
            part += (
                "\n执行要求：本次公司级报表由系统自动汇总生成。"
                "如果你被唤醒，仅补充必要说明，不要再次向成员发起收集。"
            )
        elif trigger.name == "biweekly_okr_checkin":
            part += (
                "\n执行要求：先调用 get_okr_settings 确认 OKR 是否开启。"
                "如果开启，检查当前周期公司和成员 OKR，主动提醒尚未设置或进展滞后的相关成员；"
                "如果未开启，则说明本次无需执行并停止。"
            )
        if trigger.focus_ref:
            part += f"\n关联 Focus：{trigger.focus_ref}"

        config = _trigger_config(trigger)
        if (
            trigger.type == "on_message"
            and config.get("okr_member_id")
            and config.get("okr_report_date")
        ):
            part += (
                "\n执行要求：这是一次日报回复入库事件。"
                "\n1. 将对方回复整理成一段不超过 2000 字的最终日报。"
                "\n2. 立即调用 upsert_member_daily_report("
                f'report_date="{config["okr_report_date"]}", '
                f'member_type="{config.get("okr_member_type", "user")}", '
                f'member_id="{config["okr_member_id"]}", content="<整理后的日报>")。'
                "\n3. 工具调用成功后，再发送一句简短确认，明确你已收到并已记录。"
                "\n4. 不要只回复确认而不调用工具，也不要把原始长对话原样存入日报。"
            )
        context_parts.append(part)

    source = "多个触发器同时触发" if len(triggers) > 1 else "触发器触发"
    return (
        "===== 本次唤醒上下文 =====\n"
        f"唤醒来源：trigger（{source}）\n\n"
        + "\n---\n".join(context_parts)
        + "\n==========================="
    )


def _trigger_session_id(execution_id: uuid.UUID) -> uuid.UUID:
    return uuid.uuid5(execution_id, "runtime-trigger-session")


def _trigger_input_message_id(execution_id: uuid.UUID) -> uuid.UUID:
    return uuid.uuid5(execution_id, "runtime-trigger-input")


async def _ensure_trigger_session(
    db: AsyncSession,
    *,
    agent: Agent,
    execution: TriggerExecution,
    trigger: AgentTrigger,
    context: str,
) -> ChatSession:
    if agent.tenant_id is None:
        raise TriggerRuntimeIntakeError(
            "agent_tenant_missing",
            "Runtime Trigger Agent has no tenant",
        )
    participant = await get_or_create_agent_participant(
        db,
        agent.id,
        agent.name,
        agent.avatar_url,
    )
    session_id = _trigger_session_id(execution.id)
    session = await db.get(ChatSession, session_id)
    if session is None:
        now = datetime.now(UTC)
        session = ChatSession(
            id=session_id,
            tenant_id=agent.tenant_id,
            session_type="trigger",
            group_id=None,
            agent_id=agent.id,
            user_id=agent.creator_id,
            created_by_participant_id=participant.id,
            title=f"🤖 内心独白：{trigger.name}"[:200],
            source_channel="trigger",
            is_group=False,
            participant_id=participant.id,
            is_primary=False,
            deleted_at=None,
            created_at=now,
            updated_at=now,
            last_message_at=now,
        )
        db.add(session)
    elif (
        session.tenant_id != agent.tenant_id
        or session.session_type != "trigger"
        or session.agent_id != agent.id
    ):
        raise TriggerRuntimeIntakeError(
            "trigger_session_scope_mismatch",
            "deterministic Trigger session exists outside the execution scope",
        )

    message_id = _trigger_input_message_id(execution.id)
    message = await db.get(ChatMessage, message_id)
    if message is None:
        db.add(
            ChatMessage(
                id=message_id,
                agent_id=agent.id,
                conversation_id=str(session.id),
                role="user",
                content=context,
                user_id=agent.creator_id,
                participant_id=participant.id,
                mentions=[],
            )
        )
    elif message.conversation_id != str(session.id) or message.content != context:
        raise TriggerRuntimeIntakeError(
            "trigger_input_mismatch",
            "deterministic Trigger input message differs from the execution payload",
        )
    await db.flush()
    return session


async def _resolve_trigger_delivery_target(
    db: AsyncSession,
    *,
    agent: Agent,
    trigger: AgentTrigger,
) -> dict[str, str] | None:
    """Resolve only user-facing direct delivery; A2A is migrated separately."""
    config = _trigger_config(trigger)
    if config.get("_a2a_session_id") or config.get("_origin_source_channel") == "agent":
        return None
    origin_user_id = config.get("_origin_user_id")
    if config.get("_origin_source_channel") == "trigger" or not origin_user_id:
        return None
    try:
        user_id = uuid.UUID(str(origin_user_id))
    except ValueError:
        raise TriggerRuntimeIntakeError(
            "invalid_trigger_origin_user",
            "Trigger delivery origin user is not a UUID",
        ) from None
    primary = await ensure_primary_platform_session(db, agent.id, user_id)
    return {
        "kind": "primary_user_session",
        "session_id": str(primary.id),
        "user_id": str(primary.user_id),
    }


async def enqueue_trigger_runtime(
    db: AsyncSession,
    *,
    execution: TriggerExecution,
    trigger: AgentTrigger,
    agent: Agent,
    settings_override: Settings | None = None,
) -> RunHandle | None:
    """Register one Trigger execution in the caller transaction when v2 is selected."""
    runtime_settings = settings_override or get_settings()
    decision = decide_runtime_v2(
        agent_id=agent.id,
        source_type="trigger",
        settings=runtime_settings,
    )
    if not decision.use_v2:
        return None
    if execution.trigger_id != trigger.id or execution.agent_id != agent.id:
        raise TriggerRuntimeIntakeError(
            "trigger_execution_scope_mismatch",
            "TriggerExecution does not belong to the requested Trigger and Agent",
        )
    if trigger.agent_id != agent.id:
        raise TriggerRuntimeIntakeError(
            "trigger_agent_mismatch",
            "Trigger does not belong to the requested Agent",
        )
    if agent.tenant_id is None:
        raise TriggerRuntimeIntakeError(
            "agent_tenant_missing",
            "Runtime Trigger Agent has no tenant",
        )
    if agent.primary_model_id is None:
        raise TriggerRuntimeIntakeError(
            "agent_model_missing",
            "Runtime Trigger Agent has no primary model",
        )
    if agent.is_expired or agent.status not in {"creating", "running", "idle"}:
        raise TriggerRuntimeIntakeError(
            "agent_unavailable",
            "Runtime Trigger Agent is unavailable",
        )

    runtime_trigger = build_execution_runtime_trigger(trigger, execution)
    context = build_trigger_context([runtime_trigger])
    event_data = _trigger_event_data(runtime_trigger)
    message_id = _trigger_input_message_id(execution.id)
    session = await _ensure_trigger_session(
        db,
        agent=agent,
        execution=execution,
        trigger=runtime_trigger,
        context=context,
    )
    delivery_target = await _resolve_trigger_delivery_target(
        db,
        agent=agent,
        trigger=runtime_trigger,
    )
    origin_user_id = (
        uuid.UUID(delivery_target["user_id"])
        if delivery_target is not None
        else agent.creator_id
    )
    execution_id = str(execution.id)
    handle = await RuntimeCommandIntake(
        db,
        settings=runtime_settings,
    ).start_run(
        StartRunCommand(
            tenant_id=agent.tenant_id,
            agent_id=agent.id,
            session_id=session.id,
            source_type="trigger",
            source_id=str(trigger.id),
            source_execution_id=execution_id,
            goal=f"处理触发器 {trigger.name}：{trigger.reason}".strip(),
            run_kind="background",
            model_id=agent.primary_model_id,
            delivery_status="pending" if delivery_target else "not_required",
            delivery_target=delivery_target,
            idempotency_key=f"start:trigger:{execution_id}",
            payload={
                "trigger_execution_id": execution_id,
                "trigger_id": str(trigger.id),
                "trigger_name": trigger.name,
                "trigger_type": trigger.type,
                "message_id": str(message_id),
                "input_content": context,
                **(
                    {"trigger_event_data": event_data}
                    if event_data
                    else {}
                ),
            },
            origin_user_id=origin_user_id,
        )
    )
    now = datetime.now(UTC)
    execution.status = "processing"
    execution.started_at = execution.started_at or now
    execution.finished_at = None
    execution.lease_owner = None
    execution.lease_expires_at = None
    execution.last_error = None
    return handle


async def load_trigger_agent(
    db: AsyncSession,
    *,
    trigger: AgentTrigger,
) -> Agent | None:
    result = await db.execute(
        select(Agent).where(
            Agent.id == trigger.agent_id,
        )
    )
    return result.scalar_one_or_none()


__all__ = [
    "TriggerRuntimeIntakeError",
    "build_trigger_context",
    "enqueue_trigger_runtime",
    "load_trigger_agent",
]
