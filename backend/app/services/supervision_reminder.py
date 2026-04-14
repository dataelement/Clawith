"""Supervision reminder service — periodically sends reminders for supervision tasks.

Checks all supervision-type tasks that are not done and sends Feishu reminders
to the target person based on the configured schedule preset.

Schedule presets: daily, every_2_days, every_3_days, weekly

Runs as a background task inside the FastAPI process.
"""

import asyncio
from datetime import datetime, timezone, timedelta

from loguru import logger
from sqlalchemy import select

from app.database import async_session
from app.models.task import Task, TaskLog
from app.models.agent import Agent

# Schedule JSON format:
# {"freq": "daily"|"weekly", "interval": N, "time": "HH:MM", "weekdays": [0-6]}
# weekdays: 0=Sun, 1=Mon, ..., 6=Sat


def _parse_schedule(remind_schedule: str) -> dict | None:
    """Parse remind_schedule — supports JSON format or legacy simple presets."""
    import json
    if not remind_schedule:
        return None
    try:
        sched = json.loads(remind_schedule)
        if isinstance(sched, dict) and "freq" in sched:
            return sched
    except (json.JSONDecodeError, TypeError):
        pass
    # Legacy simple preset fallback
    legacy_map = {
        "daily": {"freq": "daily", "interval": 1, "time": "09:00"},
        "every_2_days": {"freq": "daily", "interval": 2, "time": "09:00"},
        "every_3_days": {"freq": "daily", "interval": 3, "time": "09:00"},
        "weekly": {"freq": "weekly", "interval": 1, "time": "09:00", "weekdays": [1, 2, 3, 4, 5]},
    }
    return legacy_map.get(remind_schedule)


def _is_reminder_due(remind_schedule: str, last_reminded_at: datetime | None, now_utc: datetime) -> bool:
    """Check if a reminder is due based on the schedule config.
    
    All time calculations are anchored to now_utc (provided by tick loop).
    Default behavior is to use UTC for hour/minute checks unless a timezone is specified.
    """
    sched = _parse_schedule(remind_schedule)
    if not sched:
        return False

    freq = sched.get("freq", "daily")
    interval = sched.get("interval", 1)
    time_str = sched.get("time", "09:00")

    # Parse target hour/minute
    try:
        th, tm = map(int, time_str.split(":"))
    except Exception:
        th, tm = 9, 0

    # For now, we use UTC for the hour/minute check.
    # In the future, we should load agent.timezone and convert now_utc.
    current_time = now_utc

    # Not yet time today
    if current_time.hour < th or (current_time.hour == th and current_time.minute < tm):
        return False

    # Already past the time window (allow 60-min window)
    if current_time.hour > th or (current_time.hour == th and current_time.minute > tm + 59):
        return False

    # Weekly: check if today is a selected weekday
    if freq == "weekly":
        weekdays = sched.get("weekdays", [1, 2, 3, 4, 5])
        # Python: Monday=0, Sunday=6 → convert to our format: Sunday=0, Monday=1, ...
        py_weekday = current_time.weekday()  # Mon=0
        our_weekday = (py_weekday + 1) % 7  # Sun=0
        if our_weekday not in weekdays:
            return False

    # Check interval since last reminder
    if last_reminded_at is None:
        return True

    # Ensure both are timezone-aware for comparison
    if last_reminded_at.tzinfo is None:
        last_reminded_at = last_reminded_at.replace(tzinfo=timezone.utc)

    elapsed = now_utc - last_reminded_at
    min_interval = timedelta(days=interval) - timedelta(hours=2)  # tolerance
    return elapsed >= min_interval


async def _get_agent_reply(target_agent, message: str, db) -> str | None:
    """Call target agent's LLM to generate a reply to a supervision reminder.

    Returns the reply text, or None if the agent can't respond.
    """
    from app.models.llm import LLMModel
    from app.services.agent_context import build_agent_context
    from app.services.llm import (
        get_provider_base_url,
        create_llm_client,
        LLMMessage,
        get_model_api_key,
    )

    )

    model_id = target_agent.primary_model_id or target_agent.fallback_model_id
    if not model_id:
        return None

    from sqlalchemy import select as _select
    model_result = await db.execute(_select(LLMModel).where(LLMModel.id == model_id))
    model = model_result.scalar_one_or_none()
    if not model:
        return None

    base_url = get_provider_base_url(model.provider, model.base_url)
    if not base_url:
        return None

    static_prompt, dynamic_prompt = await build_agent_context(
        target_agent.id, target_agent.name, target_agent.role_description or ""
    )

    messages = [
        LLMMessage(role="system", content=static_prompt, dynamic_content=dynamic_prompt),
        LLMMessage(role="user", content=message),
    ]

    client = create_llm_client(
        provider=model.provider,
        api_key=get_model_api_key(model),
        model=model.model,
        base_url=base_url,
        timeout=float(getattr(model, 'request_timeout', None) or 60.0),
    )
    try:
        response = await client.complete(
            messages=messages,
            temperature=model.temperature,
            max_tokens=512,
        )
        content = (response.content or "").strip()
        return content if content else None
    except LLMError as e:
        logger.error(f"_get_agent_reply LLM error: {e}")
    except Exception as e:
        logger.error(f"_get_agent_reply LLM call failed: {e}")
    finally:
        await client.close()
    return None


async def _send_supervision_reminder(task: Task, agent_name: str):
    """Send a single supervision reminder. Target can be an Agent or a Member."""
    try:
        from app.models.agent import Agent
        from app.models.org import AgentRelationship
        from app.models.channel_config import ChannelConfig
        from app.models.activity_log import AgentActivityLog
        from app.services.feishu_service import feishu_service
        from sqlalchemy.orm import selectinload
        import json as _json

        target_name = task.supervision_target_name
        if not target_name:
            logger.warning(f"Supervision task {task.id} has no target name")
            return

        days_since = (datetime.now(timezone.utc) - task.created_at).days
        reminder_msg = (
            f"📋 督办提醒 — 来自 {agent_name}\n\n"
            f"事项：{task.title}\n"
        )
        if task.description:
            reminder_msg += f"说明：{task.description}\n"
        reminder_msg += f"创建于：{days_since} 天前\n"
        if task.due_date:
            reminder_msg += f"截止日期：{task.due_date.strftime('%Y-%m-%d')}\n"
        reminder_msg += f"\n请及时处理，谢谢！"

        async with async_session() as db:
            sent = False
            send_method = ""

            # 1. Try to find target as an Agent
            agent_result = await db.execute(
                select(Agent).where(Agent.name == target_name)
            )
            target_agent = agent_result.scalar_one_or_none()

            if target_agent:
                # Send agent-to-agent message via ChatSession + ChatMessage
                from app.models.audit import ChatMessage
                from app.models.chat_session import ChatSession
                from app.models.participant import Participant

                # Get participant for sender agent
                src_part_r = await db.execute(
                    select(Participant).where(Participant.type == "agent", Participant.ref_id == task.agent_id)
                )
                src_part = src_part_r.scalar_one_or_none()
                tgt_part_r = await db.execute(
                    select(Participant).where(Participant.type == "agent", Participant.ref_id == target_agent.id)
                )
                tgt_part = tgt_part_r.scalar_one_or_none()

                # Find or create ChatSession
                session_agent_id = min(task.agent_id, target_agent.id, key=str)
                session_peer_id = max(task.agent_id, target_agent.id, key=str)
                sess_r = await db.execute(
                    select(ChatSession).where(
                        ChatSession.agent_id == session_agent_id,
                        ChatSession.peer_agent_id == session_peer_id,
                        ChatSession.source_channel == "agent",
                    )
                )
                chat_session = sess_r.scalar_one_or_none()
                if not chat_session:
                    # Get creator for user_id
                    src_agent_r = await db.execute(select(Agent).where(Agent.id == task.agent_id))
                    src_agent = src_agent_r.scalar_one_or_none()
                    owner_id = src_agent.creator_id if src_agent else task.agent_id
                    chat_session = ChatSession(
                        agent_id=session_agent_id,
                        user_id=owner_id,
                        title=f"{agent_name} ↔ {target_agent.name}",
                        source_channel="agent",
                        participant_id=src_part.id if src_part else None,
                        peer_agent_id=session_peer_id,
                    )
                    db.add(chat_session)
                    await db.flush()

                session_id = str(chat_session.id)
                src_agent_r2 = await db.execute(select(Agent).where(Agent.id == task.agent_id))
                src_agent2 = src_agent_r2.scalar_one_or_none()
                owner_id = src_agent2.creator_id if src_agent2 else task.agent_id

                # Save reminder message
                db.add(ChatMessage(
                    agent_id=session_agent_id, user_id=owner_id,
                    role="user", content=reminder_msg,
                    conversation_id=session_id,
                    participant_id=src_part.id if src_part else None,
                ))
                await db.flush()
                chat_session.last_message_at = datetime.now(timezone.utc)
                sent = True
                send_method = "agent消息"

                # Trigger target agent's LLM to generate a reply
                try:
                    reply = await _get_agent_reply(target_agent, reminder_msg, db)
                    if reply:
                        db.add(ChatMessage(
                            agent_id=session_agent_id, user_id=owner_id,
                            role="assistant", content=reply,
                            conversation_id=session_id,
                            participant_id=tgt_part.id if tgt_part else None,
                        ))
                        send_method = f"agent消息+回复({reply[:40]})"
                        logger.info(f"📋 Target agent {target_agent.name} replied: {reply[:80]}")
                except Exception as e:
                    logger.warning(f"Target agent reply failed: {e}")
            else:
                # 2. Fallback: find target as a Member in relationships
                rel_result = await db.execute(
                    select(AgentRelationship)
                    .where(AgentRelationship.agent_id == task.agent_id)
                    .options(selectinload(AgentRelationship.member))
                )
                rels = rel_result.scalars().all()
                target_member = None
                for r in rels:
                    if r.member and r.member.name == target_name:
                        target_member = r.member
                        break

                if target_member:
                    # Try Feishu
                    config_r = await db.execute(
                        select(ChannelConfig).where(
                            ChannelConfig.agent_id == task.agent_id,
                            ChannelConfig.channel_type == "feishu",
                        )
                    )
                    config = config_r.scalar_one_or_none()
                    if config and (target_member.email or target_member.phone):
                        try:
                            resolved = await feishu_service.resolve_open_id(
                                config.app_id, config.app_secret,
                                email=target_member.email, mobile=target_member.phone,
                            )
                            if resolved:
                                content = _json.dumps({"text": reminder_msg}, ensure_ascii=False)
                                resp = await feishu_service.send_message(
                                    config.app_id, config.app_secret,
                                    receive_id=resolved, msg_type="text",
                                    content=content, receive_id_type="open_id",
                                )
                                if resp.get("code") == 0:
                                    sent = True
                                    send_method = "飞书"
                        except Exception:
                            pass

            # Log result to TaskLog
            if sent:
                log = TaskLog(task_id=task.id, content=f"✅ 已向 {target_name} 发送督办提醒（{send_method}）")
            elif target_agent or target_name:
                log = TaskLog(task_id=task.id, content=f"📋 督办提醒已触发，目标：{target_name}")
            else:
                log = TaskLog(task_id=task.id, content=f"⚠️ 提醒失败：未找到联系人 '{target_name}'")
            db.add(log)

            # Log to AgentActivityLog for Activity tab visibility
            activity = AgentActivityLog(
                agent_id=task.agent_id,
                action_type="schedule_run",
                summary=f"📋 督办提醒：{task.title} → {target_name}" + (f"（{send_method}已发送）" if sent else ""),
                detail_json={"task_id": str(task.id), "target": target_name, "sent": sent},
                related_id=task.id,
            )
            db.add(activity)
            await db.commit()

            logger.info(f"📋 Supervision reminder for '{task.title}' -> {target_name}, sent={sent}")

    except Exception as e:
        logger.exception(f"Supervision reminder error for task {task.id}: {e}")


async def _supervision_tick():
    """One tick: check all supervision tasks and send due reminders."""
    logger.info("[supervision] tick running...")
    from app.services.audit_logger import write_audit_log

    try:
        now = datetime.now(timezone.utc)

        async with async_session() as db:
            # Find active supervision tasks
            result = await db.execute(
                select(Task, Agent.name).join(Agent, Agent.id == Task.agent_id).where(
                    Task.type == "supervision",
                    Task.status.in_(["pending", "doing"]),
                    Task.remind_schedule.isnot(None),
                )
            )
            rows = result.all()
            logger.info(f"[supervision] found {len(rows)} supervision tasks")

            await write_audit_log("supervision_tick", {"tasks_found": len(rows)})

            for task, agent_name in rows:
                try:
                    # Get last reminder log for this task
                    log_result = await db.execute(
                        select(TaskLog)
                        .where(TaskLog.task_id == task.id)
                        .order_by(TaskLog.created_at.desc())
                        .limit(1)
                    )
                    last_log = log_result.scalar_one_or_none()
                    last_reminded = last_log.created_at if last_log else None

                    if _is_reminder_due(task.remind_schedule, last_reminded, now):
                        logger.info(f"[supervision] FIRING reminder for '{task.title}' -> {task.supervision_target_name}")
                        await write_audit_log(
                            "supervision_fire",
                            {"task_id": str(task.id), "title": task.title, "target": task.supervision_target_name},
                            agent_id=task.agent_id,
                        )
                        await _send_supervision_reminder(task, agent_name)

                except Exception as e:
                    logger.error(f"Error checking supervision task {task.id}: {e}")

    except Exception as e:
        logger.exception(f"Supervision tick error: {e}")
        await write_audit_log("supervision_error", {"error": str(e)[:300]})


async def start_supervision_reminder():
    """Start the background supervision reminder loop. Call from FastAPI startup."""
    logger.info("📋 [supervision] Reminder service started (60s tick)")
    logger.info("📋 Supervision reminder service started (60s tick)")
    while True:
        await _supervision_tick()
        await asyncio.sleep(60)
