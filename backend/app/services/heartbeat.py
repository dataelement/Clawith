"""Heartbeat service — proactive agent awareness loop.

Periodically triggers agents to check their environment (tasks, plaza,
etc.) and take autonomous actions. Inspired by OpenClaw's heartbeat
mechanism.

Runs as a background task inside the FastAPI process.
"""

import asyncio
import uuid
from datetime import datetime, timezone, timedelta
from typing import TYPE_CHECKING

from loguru import logger

from app.core.logging_config import new_trace_id
from app.services.heartbeat_runtime import (
    HeartbeatRuntimeIntakeError,
    enqueue_oneshot_runtime,
)
from sqlalchemy import select, update, or_
from sqlalchemy.ext.asyncio import AsyncSession
from app.services.storage import agent_storage_key, get_storage_backend

if TYPE_CHECKING:
    from app.models.agent import Agent

# Default heartbeat instruction used when HEARTBEAT.md doesn't exist
DEFAULT_HEARTBEAT_INSTRUCTION = """[Heartbeat Check]

This is your periodic heartbeat — a moment to be aware, explore, and contribute.

## Phase 1: Review Context & Discover Interest Points

First, review your **recent conversations** (provided below if available) and your **role/responsibilities**.
Identify topics or questions that:
- Are directly relevant to your role and current work
- Were mentioned by users but not fully explored at the time
- Represent emerging trends or changes in your professional domain
- Could improve your ability to serve your users

If no genuine, informative topics emerge from recent context, **skip exploration** and go directly to Phase 3.
Do NOT search for generic or obvious topics just to fill time. Quality over quantity.

## Phase 2: Targeted Exploration (Conditional)

Only if you identified genuine interest points in Phase 1:

1. Use `web_search` to investigate (maximum 5 searches per heartbeat)
2. Keep searches **tightly scoped** to your role and recent work topics
3. For each discovery worth keeping:
   - Record it using `write_file` to `memory/curiosity_journal.md`
   - Include the **source URL** and a brief note on **why it matters to your work**
   - Rate its relevance (high/medium/low) to your current responsibilities

Format for curiosity_journal.md entries:
```
### [Date] - [Topic]
- **Finding**: [What you learned]
- **Source**: [URL]
- **Relevance**: [high/medium/low] — [Why it matters to your work]
- **Follow-up**: [Optional: questions this raises for next time]
```

## Phase 3: Agent Plaza

1. Call `plaza_get_new_posts` to check recent activity
2. If you found something genuinely valuable in Phase 2:
   - Share the most impactful discovery to plaza (max 1 post)
   - **Always include the source URL** when sharing internet findings
   - Frame it in terms of how it's relevant to your team/domain
3. Comment on relevant existing posts (max 2 comments)

## Phase 4: Wrap Up

- If nothing needed attention and no exploration was warranted: reply with HEARTBEAT_OK
- Otherwise, briefly summarize what you explored and why

⚠️ KEY PRINCIPLES:
- Always ground exploration in YOUR role and YOUR recent work context
- Never search for random unrelated topics out of idle curiosity
- If you don't have a specific angle worth investigating, don't search
- Prefer depth over breadth — one thoroughly explored topic > five surface-level queries
- Generate follow-up questions only when you genuinely want to know more

⚠️ PRIVACY RULES — STRICTLY FOLLOW:
- NEVER share information from private user conversations
- NEVER share content from memory/memory.md
- NEVER share content from workspace/ files
- NEVER share task details from tasks.json
- You may ONLY share: general work insights, public information, opinions on plaza posts
- If unsure whether something is private, do NOT share it

⚠️ POSTING LIMITS per heartbeat:
- Maximum 1 new post
- Maximum 2 comments on existing posts
- Do NOT post trivial or repetitive content
"""

PRIVATE_AGENT_HEARTBEAT_APPEND = """

⚠️ PRIVATE AGENT RULE — STRICTLY FOLLOW:
- You are a private agent. Do NOT browse Agent Plaza.
- Do NOT call plaza_get_new_posts, plaza_create_post, or plaza_add_comment.
- Do NOT share any findings, summaries, or opinions in Plaza.
- If you have no user-facing or task-facing work to do, reply with HEARTBEAT_OK.
"""

CUSTOM_HEARTBEAT_GUARDRAILS = """

⚠️ PRIVACY RULES — STRICTLY FOLLOW:
- NEVER share information from private user conversations
- NEVER share content from memory/memory.md
- NEVER share content from workspace/ files
- NEVER share task details from tasks.json
- You may ONLY share: general work insights, public information, opinions on plaza posts

⚠️ POSTING LIMITS per heartbeat:
- Maximum 1 new post
- Maximum 2 comments on existing posts
- Do NOT post trivial or repetitive content
"""


async def _build_heartbeat_instruction(
    db: AsyncSession,
    agent: "Agent",
) -> str:
    """Build one heartbeat input and drain its notification snapshot atomically."""
    instruction = DEFAULT_HEARTBEAT_INSTRUCTION
    storage = get_storage_backend()
    hb_key = agent_storage_key(agent.id, "HEARTBEAT.md")
    if await storage.exists(hb_key):
        try:
            custom = await storage.read_text(
                hb_key,
                encoding="utf-8",
                errors="replace",
            )
            if custom.strip():
                instruction = custom.strip() + CUSTOM_HEARTBEAT_GUARDRAILS
        except Exception as exc:
            logger.warning(
                "Failed to read custom heartbeat instruction for agent {}: {}",
                agent.id,
                exc,
            )

    is_private = (getattr(agent, "access_mode", None) or "company") != "company"
    if is_private:
        instruction += PRIVATE_AGENT_HEARTBEAT_APPEND

    from app.models.activity_log import AgentActivityLog

    recent_context = ""
    try:
        recent_result = await db.execute(
            select(AgentActivityLog)
            .where(AgentActivityLog.agent_id == agent.id)
            .where(
                AgentActivityLog.action_type.in_(
                    ["chat_reply", "tool_call", "task_created", "task_updated"]
                )
            )
            .order_by(AgentActivityLog.created_at.desc())
            .limit(50)
        )
        recent_activities = recent_result.scalars().all()
        if recent_activities:
            items = []
            for activity in reversed(recent_activities):
                timestamp = (
                    activity.created_at.strftime("%m-%d %H:%M")
                    if activity.created_at
                    else ""
                )
                items.append(
                    f"- [{timestamp}] {activity.action_type}: "
                    f"{activity.summary[:120]}"
                )
            recent_context = (
                "\n\n---\n## Recent Activity Context\n"
                "Here are your recent interactions and work to help you identify "
                "relevant topics:\n\n"
                + "\n".join(items)
            )
    except Exception as exc:
        logger.warning(
            "Failed to fetch recent activity for heartbeat context: {}",
            exc,
        )

    from app.models.notification import Notification

    inbox_context = ""
    try:
        notification_result = await db.execute(
            select(Notification)
            .where(
                Notification.agent_id == agent.id,
                Notification.is_read.is_(False),
            )
            .order_by(Notification.created_at)
            .limit(10)
        )
        unread = notification_result.scalars().all()
        if unread:
            lines = [
                "\n\n---\n## Inbox (new messages for you — please review and "
                "respond if appropriate)"
            ]
            for notification in unread:
                sender = (
                    f"from {notification.sender_name}"
                    if notification.sender_name
                    else ""
                )
                lines.append(
                    f"- [{notification.type}] {notification.title} {sender}: "
                    f"{(notification.body or '')[:150]}"
                )
                notification.is_read = True
            inbox_context = "\n".join(lines)
    except Exception as exc:
        logger.warning("Failed to drain agent notifications: {}", exc)

    return instruction + recent_context + inbox_context


def _is_in_active_hours(active_hours: str, tz_name: str = "UTC") -> bool:
    """Check if current time is within the agent's active hours.

    Format: "HH:MM-HH:MM" (e.g., "09:00-18:00")
    Uses agent's configured timezone (defaults to UTC).
    """
    try:
        from zoneinfo import ZoneInfo
        start_str, end_str = active_hours.split("-")
        sh, sm = map(int, start_str.strip().split(":"))
        eh, em = map(int, end_str.strip().split(":"))
        try:
            tz = ZoneInfo(tz_name)
        except (KeyError, Exception):
            tz = ZoneInfo("UTC")
        now = datetime.now(tz)
        current_minutes = now.hour * 60 + now.minute
        start_minutes = sh * 60 + sm
        end_minutes = eh * 60 + em
        if start_minutes <= end_minutes:
            return start_minutes <= current_minutes < end_minutes
        else:
            # Overnight range (e.g., "22:00-06:00")
            return current_minutes >= start_minutes or current_minutes < end_minutes
    except Exception:
        return True  # Default to active if parsing fails


async def _heartbeat_tick():
    """One heartbeat tick: find agents due for heartbeat."""
    from app.config import get_settings
    from app.database import async_session
    from app.models.agent import Agent
    from app.services.agent_runtime.config import decide_runtime_v2
    from app.services.audit_logger import write_audit_log
    from app.services.heartbeat_runtime import (
        HeartbeatRuntimeIntakeError,
        enqueue_heartbeat_runtime,
    )
    from app.services.timezone_utils import get_agent_timezone_sync
    from app.models.tenant import Tenant

    new_trace_id()
    now = datetime.now(timezone.utc)
    runtime_settings = get_settings()

    try:
        async with async_session() as db:
            result = await db.execute(
                select(Agent).where(
                    Agent.heartbeat_enabled.is_(True),
                    Agent.status.in_(["running", "idle"]),
                    Agent.tenant_id.is_not(None),
                    Agent.primary_model_id.is_not(None),
                )
            )
            agents = result.scalars().all()

            # Pre-load tenants for timezone resolution
            tenant_ids = {a.tenant_id for a in agents if a.tenant_id}
            tenants_by_id = {}
            if tenant_ids:
                t_result = await db.execute(select(Tenant).where(Tenant.id.in_(tenant_ids)))
                tenants_by_id = {t.id: t for t in t_result.scalars().all()}

            triggered = 0
            for agent in agents:
                # Skip expired agents
                if agent.is_expired:
                    continue
                if agent.expires_at and now >= agent.expires_at:
                    agent.is_expired = True
                    agent.heartbeat_enabled = False
                    agent.status = "stopped"
                    continue

                # Resolve timezone
                tenant = tenants_by_id.get(agent.tenant_id)
                tz_name = get_agent_timezone_sync(agent, tenant)

                # Check active hours (in agent's timezone)
                if not _is_in_active_hours(agent.heartbeat_active_hours or "09:00-18:00", tz_name):
                    continue

                # Check interval
                interval = timedelta(minutes=agent.heartbeat_interval_minutes or 240)
                if agent.last_heartbeat_at and (now - agent.last_heartbeat_at) < interval:
                    continue

                runtime_decision = decide_runtime_v2(
                    agent_id=agent.id,
                    source_type="heartbeat",
                    settings=runtime_settings,
                )
                if not runtime_decision.use_v2:
                    logger.error(
                        "Heartbeat for {} remains due because Runtime is disabled ({})",
                        agent.name,
                        runtime_decision.reason,
                    )
                    continue

                try:
                    async with db.begin_nested():
                        # The claim and Runtime registration share one commit so a
                        # heartbeat cannot disappear between scheduling systems.
                        claim_result = await db.execute(
                            update(Agent)
                            .where(
                                Agent.id == agent.id,
                                Agent.heartbeat_enabled.is_(True),
                                Agent.status.in_(["running", "idle"]),
                                or_(
                                    Agent.last_heartbeat_at.is_(None),
                                    Agent.last_heartbeat_at <= now - interval,
                                ),
                            )
                            .values(last_heartbeat_at=now)
                        )
                        if (claim_result.rowcount or 0) != 1:
                            continue
                        instruction = await _build_heartbeat_instruction(db, agent)
                        runtime_handle = await enqueue_heartbeat_runtime(
                            db,
                            agent=agent,
                            occurrence_at=now,
                            instruction=instruction,
                            settings_override=runtime_settings,
                        )
                        if runtime_handle is None:
                            raise HeartbeatRuntimeIntakeError(
                                "runtime_gate_changed",
                                "Heartbeat Runtime gate changed during intake",
                            )
                    await db.commit()
                except HeartbeatRuntimeIntakeError as exc:
                    logger.error(
                        "Heartbeat Runtime intake failed for {} ({}): {}",
                        agent.name,
                        exc.code,
                        exc,
                    )
                    continue
                except Exception as exc:
                    logger.exception(
                        "Heartbeat claim failed for {}: {}",
                        agent.name,
                        exc,
                    )
                    continue

                logger.info(
                    "💓 Queued heartbeat for {} as Runtime Run {}",
                    agent.name,
                    runtime_handle.run_id,
                )
                try:
                    await write_audit_log(
                        "heartbeat_fire",
                        {
                            "agent_name": agent.name,
                            "runtime_type": runtime_handle.runtime_type,
                            "run_id": str(runtime_handle.run_id),
                        },
                        agent_id=agent.id,
                    )
                except Exception as exc:
                    logger.warning(
                        "Failed to write heartbeat_fire audit log for {}: {}",
                        agent.name,
                        exc,
                    )
                triggered += 1

            await db.commit()

            if triggered:
                try:
                    await write_audit_log("heartbeat_tick", {"eligible_agents": len(agents), "triggered": triggered})
                except Exception as e:
                    logger.warning(f"Failed to write heartbeat_tick audit log: {e}")

    except Exception as e:
        logger.exception(f"Heartbeat tick error: {e}")
        await write_audit_log("heartbeat_error", {"error": str(e)[:300]})


async def start_heartbeat():
    """Start the background heartbeat loop. Call from FastAPI startup."""
    logger.info("💓 Agent heartbeat service started (60s tick)")
    while True:
        await _heartbeat_tick()
        await asyncio.sleep(60)


async def _notify_oneshot_error(
    triggered_by_user_id: uuid.UUID | None,
    agent_id: uuid.UUID,
    agent_name: str,
    error_msg: str,
) -> None:
    """Create a platform notification for the admin who triggered a failed oneshot task."""
    if not triggered_by_user_id:
        return
    try:
        from app.database import async_session
        from app.models.notification import Notification
        async with async_session() as db:
            db.add(Notification(
                user_id=triggered_by_user_id,
                type="system",
                title=f"{agent_name} task failed",
                body=error_msg[:500],
                link=f"/agents/{agent_id}#chat",
                ref_id=agent_id,
                sender_name=agent_name,
            ))
            await db.commit()
        logger.info(f"[Oneshot] Notified user {triggered_by_user_id} about {agent_name} failure")
    except Exception as e:
        logger.warning(f"[Oneshot] Failed to create error notification: {e}")


async def run_agent_oneshot(
    agent_id: uuid.UUID,
    prompt: str,
    triggered_by_user_id: uuid.UUID | None = None,
    max_rounds: int = 40,
) -> str:
    """Register one explicit background Run and return its durable identity."""
    new_trace_id()
    try:
        from app.database import async_session
        from app.models.agent import Agent
        async with async_session() as db:
            result = await db.execute(select(Agent).where(Agent.id == agent_id))
            agent = result.scalar_one_or_none()
            if not agent:
                logger.warning(f"[Oneshot] Agent {agent_id} not found — aborting")
                return ""
            handle = await enqueue_oneshot_runtime(
                db,
                agent=agent,
                prompt=prompt,
                occurrence_id=uuid.uuid4(),
                triggered_by_user_id=triggered_by_user_id,
                requested_max_steps=max_rounds,
            )
            if handle is None:
                message = "统一 Runtime 当前未对 oneshot 入口启用；未回退旧执行循环"
                await _notify_oneshot_error(
                    triggered_by_user_id,
                    agent_id,
                    agent.name,
                    message,
                )
                logger.error(f"[Oneshot] {message}")
                return ""
            await db.commit()
        logger.info(f"[Oneshot] Queued Run {handle.run_id} for {agent.name}")
        return str(handle.run_id)

    except HeartbeatRuntimeIntakeError as exc:
        logger.error(f"[Oneshot] Runtime intake failed ({exc.code}): {exc}")
        await _notify_oneshot_error(
            triggered_by_user_id,
            agent_id,
            str(agent_id),
            f"{exc.code}: {exc}",
        )
        return ""

    except Exception as e:
        logger.exception(f"[Oneshot] Unexpected error for agent {agent_id}: {e}")
        return ""
