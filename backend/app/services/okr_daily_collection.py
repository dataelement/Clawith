"""Daily OKR collection service.

Handles reminder outreach to the OKR Agent's tracked relationship network.
Human members and tracked digital employees are both expected to reply back to
the OKR Agent, which then records the report through the standard tool path.
"""

from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import or_, select

from app.database import async_session
from app.models.agent import Agent
from app.models.chat_session import ChatSession
from app.models.okr import OKRSettings
from app.models.org import AgentAgentRelationship, AgentRelationship, OrgMember
from app.models.user import User
from app.services.agent_tools import (
    _send_channel_message,
    _send_platform_message,
)


def _human_request_message(target_name: str, report_day: date) -> str:
    return (
        f"你好，{target_name}！我是 OKR Agent，需要收集你今天的日报（{report_day.isoformat()}）。请回复以下内容：\n"
        "- 今天取得的进展\n"
        "- 遇到的风险或阻碍\n"
        "- 下一步计划\n\n"
        "我收到后会帮你整理并记入 OKR 日报。谢谢！"
    )


def _agent_request_message(target_name: str, report_day: date) -> str:
    return (
        f"Hi {target_name}, this is OKR Agent collecting your daily report for {report_day.isoformat()}.\n"
        "Please review today's progress and reply to me with:\n"
        "- progress made today\n"
        "- risks or blockers\n"
        "- next step\n\n"
        "Please keep the final reply concise so I can record it directly."
    )


def _agent_collection_prompt(agent_member: Agent, report_day: date) -> str:
    request = _agent_request_message(agent_member.name, report_day)
    return f"""[SYSTEM TASK — DAILY OKR COLLECTION]

Collect and store the final daily report from digital employee {agent_member.name}.

1. Call send_message_to_agent with exactly:
   - target_agent_id: {agent_member.id}
   - msg_type: task_delegate
   - message: {request}
2. Wait for the durable A2A result.
3. Distill the returned result into no more than 2000 characters.
4. Call upsert_member_daily_report with exactly:
   - report_date: {report_day.isoformat()}
   - member_type: agent
   - member_id: {agent_member.id}
   - content: the distilled final report
   - source: okr_agent_daily_collection
5. Finish only after the report has been stored. If either tool reports a failure,
   finish with a concise explanation and do not invent a report.
"""


async def _enqueue_agent_daily_collection(
    okr_agent: Agent,
    agent_member: Agent,
    report_day: date,
) -> bool:
    """Register one source Run so A2A wait/resume remains checkpointed."""
    from app.services.heartbeat import run_agent_oneshot

    run_id = await run_agent_oneshot(
        agent_id=okr_agent.id,
        prompt=_agent_collection_prompt(agent_member, report_day),
        triggered_by_user_id=okr_agent.creator_id,
        max_rounds=12,
    )
    return bool(run_id)


async def _cleanup_legacy_daily_reply_triggers(okr_agent_id: uuid.UUID) -> None:
    """Disable legacy daily reply triggers from previous implementations."""
    async with async_session() as db:
        from app.models.trigger import AgentTrigger

        trigger_rows = await db.execute(
            select(AgentTrigger).where(
                AgentTrigger.agent_id == okr_agent_id,
                (
                    AgentTrigger.name.like("daily_reply_%")
                    | AgentTrigger.name.like("wait\\_%daily\\_reply", escape="\\")
                ),
            )
        )
        for trigger in trigger_rows.scalars().all():
            trigger.is_enabled = False
        await db.commit()


async def trigger_daily_collection_for_tenant(tenant_id: uuid.UUID) -> dict:
    """Send daily collection requests to tracked relationships."""
    async with async_session() as db:
        settings_result = await db.execute(select(OKRSettings).where(OKRSettings.tenant_id == tenant_id))
        settings = settings_result.scalar_one_or_none()
        if not settings or not settings.enabled:
            raise ValueError("OKR is not enabled for this tenant")
        if not settings.daily_report_enabled:
            raise ValueError("Daily report collection is not enabled for this tenant")
        if not settings.okr_agent_id:
            raise ValueError("OKR Agent not found for this tenant")

        okr_agent_result = await db.execute(select(Agent).where(Agent.id == settings.okr_agent_id))
        okr_agent = okr_agent_result.scalar_one_or_none()
        if not okr_agent:
            raise ValueError("OKR Agent not found for this tenant")

        await db.commit()

    await _cleanup_legacy_daily_reply_triggers(okr_agent.id)

    async with async_session() as db:
        # OKR still uses legacy relationship rows as an explicit tracking list.
        # Directory visibility is intentionally not the source of truth here.
        rel_result = await db.execute(
            select(AgentRelationship, OrgMember)
            .join(OrgMember, AgentRelationship.member_id == OrgMember.id)
            .where(
                AgentRelationship.agent_id == okr_agent.id,
                OrgMember.status == "active",
            )
        )
        rel_rows = rel_result.all()

        agent_rel_result = await db.execute(
            select(Agent)
            .join(
                AgentAgentRelationship,
                AgentAgentRelationship.target_agent_id == Agent.id,
            )
            .where(
                AgentAgentRelationship.agent_id == okr_agent.id,
                Agent.is_system == False,  # noqa: E712
                Agent.status.notin_(["stopped", "error"]),
            )
        )
        tracked_agents = agent_rel_result.scalars().all()

        member_user_display_names: dict[uuid.UUID, str] = {}
        for _, org_member in rel_rows:
            if org_member.user_id:
                user_result = await db.execute(
                    select(User.display_name).where(User.id == org_member.user_id)
                )
                user_display_name = user_result.scalar_one_or_none()
                if user_display_name:
                    member_user_display_names[org_member.id] = user_display_name

            if not org_member.user_id:
                patterns = []
                if org_member.open_id:
                    patterns.append(f"feishu_p2p_{org_member.open_id}")
                if org_member.external_id:
                    patterns.append(f"feishu_p2p_{org_member.external_id}")
                    patterns.append(f"dingtalk_p2p_{org_member.external_id}")
                if patterns:
                    sess_result = await db.execute(
                        select(ChatSession.user_id).where(
                            ChatSession.agent_id == okr_agent.id,
                            or_(*[ChatSession.external_conv_id == p for p in patterns]),
                        ).limit(1)
                    )
                    found = sess_result.scalar_one_or_none()
                    if found:
                        user_result = await db.execute(
                            select(User.display_name).where(User.id == found)
                        )
                        user_display_name = user_result.scalar_one_or_none()
                        if user_display_name:
                            member_user_display_names[org_member.id] = user_display_name
    report_day = date.today()
    sent_humans = 0
    sent_agents = 0

    for _, org_member in rel_rows:
        platform_name = member_user_display_names.get(org_member.id)
        message_text = _human_request_message(org_member.name, report_day)
        has_external_channel = bool(org_member.open_id or org_member.external_id)

        send_result = ""
        if has_external_channel:
            send_result = await _send_channel_message(
                okr_agent.id,
                {"target_member_id": str(org_member.id), "message": message_text},
            )
        elif platform_name:
            send_result = await _send_platform_message(
                okr_agent.id,
                {"target_member_id": str(org_member.id), "message": message_text},
            )

        if send_result.startswith("✅"):
            sent_humans += 1

    for agent_member in tracked_agents:
        accepted = await _enqueue_agent_daily_collection(
            okr_agent,
            agent_member,
            report_day,
        )
        if accepted:
            sent_agents += 1

    return {
        "okr_agent_id": str(okr_agent.id),
        "human_targets": len(rel_rows),
        "agent_targets": len(tracked_agents),
        "sent_humans": sent_humans,
        "sent_agents": sent_agents,
        "total_targets": len(rel_rows) + len(tracked_agents),
        "report_date": report_day.isoformat(),
    }
