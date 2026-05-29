"""Trigger invocation and delivery orchestration."""

from __future__ import annotations

import json as _json
import uuid
from datetime import datetime, timezone

from loguru import logger
from sqlalchemy import select

from app.database import async_session
from app.models.agent import Agent
from app.models.trigger import AgentTrigger
from app.services.trigger_runtime import (
    mark_trigger_executions_completed,
    mark_trigger_executions_failed,
)


async def resolve_trigger_delivery_target(agent: Agent, triggers: list[AgentTrigger]) -> dict | None:
    from app.models.chat_session import ChatSession
    from app.services.chat_session_service import ensure_primary_platform_session

    for trigger in triggers:
        cfg = trigger.config or {}
        a2a_sid = cfg.get("_a2a_session_id")
        if a2a_sid:
            try:
                async with async_session() as db:
                    session = await db.get(ChatSession, uuid.UUID(a2a_sid))
                    if not session:
                        return None
                    return {
                        "kind": "session",
                        "session_id": str(session.id),
                        "owner_user_id": str(session.user_id),
                        "source_channel": session.source_channel,
                    }
            except Exception:
                return None

    origin_cfg = None
    for trigger in triggers:
        cfg = trigger.config or {}
        if cfg.get("_origin_session_id") or cfg.get("_origin_user_id"):
            origin_cfg = cfg
            break
    if not origin_cfg:
        return None

    origin_source_channel = origin_cfg.get("_origin_source_channel")
    origin_session_id = origin_cfg.get("_origin_session_id")
    origin_user_id = origin_cfg.get("_origin_user_id")

    if origin_source_channel == "agent" and origin_session_id:
        try:
            async with async_session() as db:
                session = await db.get(ChatSession, uuid.UUID(origin_session_id))
                if not session:
                    return None
                return {
                    "kind": "session",
                    "session_id": str(session.id),
                    "owner_user_id": str(session.user_id),
                    "source_channel": "agent",
                }
        except Exception:
            return None

    if origin_source_channel != "trigger" and origin_user_id:
        try:
            async with async_session() as db:
                primary = await ensure_primary_platform_session(db, agent.id, uuid.UUID(origin_user_id))
                await db.commit()
                return {
                    "kind": "primary_user_session",
                    "session_id": str(primary.id),
                    "owner_user_id": str(primary.user_id),
                    "source_channel": primary.source_channel,
                }
        except Exception:
            return None

    return None


async def invoke_agent_for_triggers(agent_id: uuid.UUID, triggers: list[AgentTrigger]):
    from app.models.audit import ChatMessage
    from app.models.chat_session import ChatSession
    from app.models.llm import LLMModel
    from app.models.participant import Participant
    from app.services.audit_logger import write_audit_log
    from app.services.llm import call_llm

    try:
        execution_ids = [
            uuid.UUID(str((t.config or {}).get("_execution_id")))
            for t in triggers
            if (t.config or {}).get("_execution_id")
        ]
        async with async_session() as db:
            result = await db.execute(select(Agent).where(Agent.id == agent_id))
            agent = result.scalar_one_or_none()
            if not agent or agent.is_expired:
                return

            if not agent.primary_model_id:
                logger.warning(f"Agent {agent.name} has no LLM model, skipping trigger invocation")
                return
            result = await db.execute(select(LLMModel).where(LLMModel.id == agent.primary_model_id))
            model = result.scalar_one_or_none()
            if not model or not model.enabled:
                logger.warning(f"Agent {agent.name}'s model is unavailable, skipping trigger invocation")
                return

            context_parts = []
            trigger_names = []
            for t in triggers:
                part = f"触发器：{t.name} ({t.type})\n原因：{t.reason}"
                if t.name == "daily_okr_collection":
                    part += (
                        "\n执行要求：先调用 get_okr_settings 确认日报收集是否开启。"
                        "如果开启，只能联系你关系网络中的成员和数字员工来收集今天的最终日报，"
                        "并整理成不超过 2000 字的正式日报；"
                        "如果未开启，则说明本次无需执行并停止。"
                    )
                elif t.name in ("daily_okr_report", "weekly_okr_report", "monthly_okr_report"):
                    part += (
                        "\n执行要求：本次公司级报表由系统自动汇总生成。"
                        "如果你被唤醒，仅补充必要说明，不要再次向成员发起收集。"
                    )
                elif t.name == "biweekly_okr_checkin":
                    part += (
                        "\n执行要求：先调用 get_okr_settings 确认 OKR 是否开启。"
                        "如果开启，检查当前周期公司和成员 OKR，主动提醒尚未设置或进展滞后的相关成员；"
                        "如果未开启，则说明本次无需执行并停止。"
                    )
                if t.focus_ref:
                    part += f"\n关联 Focus：{t.focus_ref}"
                cfg = t.config or {}
                if t.type == "on_message" and cfg.get("_matched_message"):
                    part += f"\n收到来自 {cfg.get('_matched_from', '?')} 的消息：\n\"{cfg['_matched_message'][:500]}\""
                if t.type == "on_message" and cfg.get("okr_member_id") and cfg.get("okr_report_date"):
                    part += (
                        "\n执行要求：这是一次日报回复入库事件。"
                        f"\n1. 将对方回复整理成一段不超过 2000 字的最终日报。"
                        f"\n2. 立即调用 upsert_member_daily_report(report_date=\"{cfg['okr_report_date']}\", "
                        f"member_type=\"{cfg.get('okr_member_type', 'user')}\", "
                        f"member_id=\"{cfg['okr_member_id']}\", content=\"<整理后的日报>\")。"
                        "\n3. 工具调用成功后，再发送一句简短确认，明确你已收到并已记录。"
                        "\n4. 不要只回复确认而不调用工具，也不要把原始长对话原样存入日报。"
                    )
                if t.type == "webhook" and cfg.get("_webhook_payload"):
                    payload_str = cfg["_webhook_payload"]
                    if len(payload_str) > 2000:
                        payload_str = payload_str[:2000] + "... (truncated)"
                    part += f"\nWebhook Payload:\n{payload_str}"
                context_parts.append(part)
                trigger_names.append(t.name)

            trigger_context = (
                "===== 本次唤醒上下文 =====\n"
                f"唤醒来源：trigger（{'多个触发器同时触发' if len(triggers) > 1 else '触发器触发'}）\n\n"
                + "\n---\n".join(context_parts)
                + "\n==========================="
            )

            title = f"🤖 内心独白：{', '.join(trigger_names)}"
            result = await db.execute(
                select(Participant).where(Participant.type == "agent", Participant.ref_id == agent_id)
            )
            agent_participant = result.scalar_one_or_none()

            session = ChatSession(
                agent_id=agent_id,
                user_id=agent.creator_id,
                participant_id=agent_participant.id if agent_participant else None,
                source_channel="trigger",
                title=title[:200],
            )
            db.add(session)
            await db.flush()
            session_id = session.id
            messages = [{"role": "user", "content": trigger_context}]
            db.add(ChatMessage(
                agent_id=agent_id,
                conversation_id=str(session_id),
                role="user",
                content=trigger_context,
                user_id=agent.creator_id,
                participant_id=agent_participant.id if agent_participant else None,
            ))
            await db.commit()
            agent_participant_id = agent_participant.id if agent_participant else None

        collected_content: list[str] = []
        delivered_platform_message_via_tool = False

        async def on_chunk(text):
            collected_content.append(text)

        async def on_tool_call(data):
            nonlocal delivered_platform_message_via_tool
            try:
                tool_name = data.get("name")
                tool_status = data.get("status")
                if tool_status == "done" and tool_name == "send_platform_message":
                    result_text = str(data.get("result", ""))
                    if result_text.startswith("✅"):
                        delivered_platform_message_via_tool = True

                async with async_session() as _tc_db:
                    if data["status"] == "running":
                        _tc_db.add(ChatMessage(
                            agent_id=agent_id,
                            conversation_id=str(session_id),
                            role="tool_call",
                            content=_json.dumps({"name": data["name"], "args": data["args"]}, ensure_ascii=False, default=str),
                            user_id=agent.creator_id,
                            participant_id=agent_participant_id,
                        ))
                    elif data["status"] == "done":
                        result_str = str(data.get("result", ""))[:2000]
                        _tc_db.add(ChatMessage(
                            agent_id=agent_id,
                            conversation_id=str(session_id),
                            role="tool_call",
                            content=_json.dumps({"name": data["name"], "result": result_str}, ensure_ascii=False, default=str),
                            user_id=agent.creator_id,
                            participant_id=agent_participant_id,
                        ))
                    await _tc_db.commit()
            except Exception as e:
                logger.warning(f"Failed to persist tool call for trigger session: {e}")

        from_agent_name = None
        for t in triggers:
            cfg = t.config or {}
            if cfg.get("from_agent_name"):
                from_agent_name = cfg.get("from_agent_name")
                break

        reply = await call_llm(
            model=model,
            messages=messages,
            agent_name=agent.name,
            role_description=agent.role_description or "",
            agent_id=agent_id,
            user_id=agent.creator_id,
            session_id=str(session_id),
            on_chunk=on_chunk,
            on_tool_call=on_tool_call,
            current_user_name_override=from_agent_name,
        )

        async with async_session() as db:
            result = await db.execute(
                select(Participant).where(Participant.type == "agent", Participant.ref_id == agent_id)
            )
            agent_participant = result.scalar_one_or_none()
            db.add(ChatMessage(
                agent_id=agent_id,
                conversation_id=str(session_id),
                role="assistant",
                content=reply or "".join(collected_content),
                user_id=agent.creator_id,
                participant_id=agent_participant.id if agent_participant else None,
            ))
            await db.commit()

        final_reply = reply or "".join(collected_content)
        for t in triggers:
            a2a_sid = (t.config or {}).get("_a2a_session_id")
            if a2a_sid and final_reply:
                try:
                    async with async_session() as db:
                        from app.models.participant import Participant as _P
                        _p_r = await db.execute(select(_P).where(_P.type == "agent", _P.ref_id == agent_id))
                        _p = _p_r.scalar_one_or_none()
                        db.add(ChatMessage(
                            agent_id=agent_id,
                            conversation_id=a2a_sid,
                            role="assistant",
                            content=final_reply,
                            user_id=agent.creator_id,
                            participant_id=_p.id if _p else None,
                        ))
                        from app.models.chat_session import ChatSession as _CS
                        _cs_r = await db.execute(select(_CS).where(_CS.id == uuid.UUID(a2a_sid)))
                        _cs = _cs_r.scalar_one_or_none()
                        if _cs:
                            _cs.last_message_at = datetime.now(timezone.utc)
                        await db.commit()
                except Exception as e:
                    logger.warning(f"[A2A] Failed to save reply to A2A session {a2a_sid}: {e}")
                break

        is_a2a_internal = all(t.name == "a2a_wake" for t in triggers)
        delivery_target = None if is_a2a_internal else await resolve_trigger_delivery_target(agent, triggers)

        if final_reply and delivery_target and not delivered_platform_message_via_tool:
            try:
                from app.api.websocket import manager as ws_manager
                agent_id_str = str(agent_id)
                trigger_reasons = []
                for t in triggers:
                    ns = (t.config or {}).get("_notification_summary", "").strip()
                    if ns:
                        trigger_reasons.append(ns)
                    else:
                        r = (t.reason or "").strip()
                        if r and len(r) <= 80:
                            trigger_reasons.append(r)
                        elif r:
                            trigger_reasons.append(r[:77] + "...")
                summary = trigger_reasons[0] if trigger_reasons else "有新的事件需要处理"
                notification = f"⚡ {summary}\n\n{final_reply}"
                target_session_id = delivery_target["session_id"]
                owner_user_id = delivery_target.get("owner_user_id")

                async with async_session() as db:
                    from app.api.websocket import maybe_mark_session_read_for_active_viewer
                    from app.models.chat_session import ChatSession
                    db.add(ChatMessage(
                        agent_id=agent_id,
                        conversation_id=target_session_id,
                        role="assistant",
                        content=notification,
                        user_id=agent.creator_id,
                    ))
                    session_row = await db.get(ChatSession, uuid.UUID(target_session_id))
                    if session_row:
                        session_row.last_message_at = datetime.now(timezone.utc)
                    if owner_user_id:
                        await maybe_mark_session_read_for_active_viewer(
                            db,
                            agent_id=agent_id,
                            session_id=target_session_id,
                            user_id=uuid.UUID(owner_user_id),
                        )
                    await db.commit()

                if owner_user_id:
                    await ws_manager.send_to_user(
                        agent_id_str,
                        owner_user_id,
                        {
                            "type": "trigger_notification",
                            "content": notification,
                            "triggers": [t.name for t in triggers],
                            "session_id": target_session_id,
                        },
                    )
            except Exception as e:
                logger.error(f"Failed to push trigger result to WebSocket: {e}")

        await write_audit_log(
            "trigger_fired",
            {"agent_name": agent.name, "triggers": [{"name": t.name, "type": t.type} for t in triggers]},
            agent_id=agent_id,
        )

        if execution_ids:
            await mark_trigger_executions_completed(execution_ids)
    except Exception as e:
        logger.error(f"Failed to invoke agent {agent_id} for triggers: {e}")
        import traceback
        traceback.print_exc()
        execution_ids = [
            uuid.UUID(str((t.config or {}).get("_execution_id")))
            for t in triggers
            if (t.config or {}).get("_execution_id")
        ]
        if execution_ids:
            await mark_trigger_executions_failed(execution_ids, str(e)[:2000])
