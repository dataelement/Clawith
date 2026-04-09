"""Trigger Daemon — evaluates all agent triggers in a single background loop.

Replaces the separate heartbeat, scheduler, and supervision reminder services
with a unified trigger evaluation engine. Runs as an asyncio background task.

Every 15 seconds:
  1. Load all enabled triggers from DB
  2. Evaluate each trigger (cron/once/interval/poll/on_message/webhook)
  3. Group fired triggers by agent_id (30s dedup window)
  4. Invoke each agent once with all its fired triggers as context
"""

import asyncio
import ipaddress
import json as _json
import uuid
from datetime import datetime, timezone, timedelta
from urllib.parse import urlparse

from croniter import croniter
from loguru import logger
from sqlalchemy import select

from app.database import async_session
from app.models.trigger import AgentTrigger
from app.models.agent import Agent

TICK_INTERVAL = 15  # seconds
DEDUP_WINDOW = 30   # seconds — same agent won't be invoked twice within this window
MAX_AGENT_CHAIN_DEPTH = 5  # A→B→A→B→A max depth before stopping
MIN_POLL_INTERVAL_MINUTES = 5  # minimum poll interval to prevent abuse

_last_invoke: dict[uuid.UUID, datetime] = {}

_A2A_WAKE_CHAIN: dict[str, int] = {}
_A2A_WAKE_CHAIN_TTL = 300
_A2A_MAX_WAKE_DEPTH = 3

# Webhook rate limiter: token -> list of timestamps
_webhook_hits: dict[str, list[float]] = {}
WEBHOOK_RATE_LIMIT = 5   # max hits per minute per token


# ── SSRF Protection ─────────────────────────────────────────────────

def _is_private_url(url: str) -> bool:
    """Block private/internal URLs to prevent SSRF attacks."""
    try:
        parsed = urlparse(url)
        hostname = parsed.hostname
        if not hostname:
            return True

        # Block obvious private hostnames
        if hostname in ("localhost", "127.0.0.1", "::1", "0.0.0.0"):
            return True

        # Try to resolve hostname and check IP
        import socket
        try:
            infos = socket.getaddrinfo(hostname, None)
            for info in infos:
                ip = ipaddress.ip_address(info[4][0])
                if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
                    return True
        except (socket.gaierror, ValueError):
            return True  # Cannot resolve = block

        return False
    except Exception:
        return True  # Block on any parsing error


# ── Trigger Evaluation ──────────────────────────────────────────────

async def _evaluate_trigger(trigger: AgentTrigger, now: datetime) -> bool:
    """Return True if this trigger should fire right now."""
    if not trigger.is_enabled:
        return False
    if trigger.expires_at and now >= trigger.expires_at:
        # Auto-disable expired triggers
        return False
    if trigger.max_fires is not None and trigger.fire_count >= trigger.max_fires:
        return False

    # Cooldown check
    if trigger.last_fired_at:
        cooldown = timedelta(seconds=trigger.cooldown_seconds)
        if (now - trigger.last_fired_at) < cooldown:
            return False

    cfg = trigger.config or {}
    t = trigger.type

    if t == "cron":
        expr = cfg.get("expr", "* * * * *")
        base = trigger.last_fired_at or trigger.created_at
        try:
            # Resolve timezone: trigger config → agent → tenant → UTC
            tz_name = cfg.get("timezone")
            if not tz_name:
                from app.services.timezone_utils import get_agent_timezone
                tz_name = await get_agent_timezone(trigger.agent_id)
            from zoneinfo import ZoneInfo
            try:
                tz = ZoneInfo(tz_name)
            except (KeyError, Exception):
                tz = ZoneInfo("UTC")
            # Evaluate cron in agent's timezone
            local_now = now.astimezone(tz)
            local_base = base.astimezone(tz) if base.tzinfo else base.replace(tzinfo=tz)
            cron = croniter(expr, local_base)
            next_run = cron.get_next(datetime)
            return local_now >= next_run
        except Exception as e:
            logger.warning(f"Invalid cron expr '{expr}' for trigger {trigger.name}: {e}")
            return False

    elif t == "once":
        at_str = cfg.get("at")
        if not at_str:
            return False
        try:
            at = datetime.fromisoformat(at_str)
            if at.tzinfo is None:
                at = at.replace(tzinfo=timezone.utc)
            return now >= at and trigger.fire_count == 0
        except Exception:
            return False

    elif t == "interval":
        minutes = cfg.get("minutes", 30)
        base = trigger.last_fired_at or trigger.created_at
        return (now - base) >= timedelta(minutes=minutes)

    elif t == "poll":
        interval_min = max(cfg.get("interval_min", 5), MIN_POLL_INTERVAL_MINUTES)
        base = trigger.last_fired_at or trigger.created_at
        if (now - base) < timedelta(minutes=interval_min):
            return False
        # Actual HTTP poll + change detection
        return await _poll_check(trigger)

    elif t == "on_message":
        return await _check_new_agent_messages(trigger)

    elif t == "webhook":
        # Check if a webhook payload is pending
        if cfg.get("_webhook_pending"):
            return True
        return False

    return False


async def _poll_check(trigger: AgentTrigger) -> bool:
    """HTTP poll: fetch URL, extract value via json_path, detect change.
    
    Persists _last_value into the trigger's config JSONB so it survives
    across process restarts.
    """
    import httpx
    cfg = trigger.config or {}
    url = cfg.get("url")
    if not url:
        return False

    # SSRF protection: block private/internal URLs
    if _is_private_url(url):
        logger.warning(f"Poll blocked for trigger {trigger.name}: private/internal URL '{url}'")
        return False

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.request(cfg.get("method", "GET"), url, headers=cfg.get("headers", {}))
            resp.raise_for_status()

        data = resp.json()
        json_path = cfg.get("json_path", "$")
        current_value = _extract_json_path(data, json_path)
        current_str = str(current_value)

        fire_on = cfg.get("fire_on", "change")
        should_fire = False

        if fire_on == "match":
            should_fire = current_str == str(cfg.get("match_value", ""))
        else:  # "change"
            last_value = cfg.get("_last_value")
            # First poll — don't fire, just record baseline
            if last_value is None:
                should_fire = False
            else:
                should_fire = current_str != last_value

        # Persist _last_value to DB so it survives restarts
        cfg["_last_value"] = current_str
        try:
            from sqlalchemy import update
            async with async_session() as db:
                await db.execute(
                    update(AgentTrigger)
                    .where(AgentTrigger.id == trigger.id)
                    .values(config=cfg)
                )
                await db.commit()
        except Exception as e:
            logger.warning(f"Failed to persist poll _last_value for {trigger.name}: {e}")

        return should_fire

    except Exception as e:
        logger.warning(f"Poll failed for trigger {trigger.name}: {e}")
        return False


def _extract_json_path(data, path: str):
    """Simple JSONPath extraction: $.key.subkey → data['key']['subkey']."""
    if path == "$" or not path:
        return data
    parts = path.lstrip("$.").split(".")
    current = data
    for part in parts:
        if isinstance(current, dict):
            current = current.get(part)
        elif isinstance(current, list) and part.isdigit():
            current = current[int(part)]
        else:
            return None
    return current


async def _check_new_agent_messages(trigger: AgentTrigger) -> bool:
    """Check if there are new messages matching this trigger.
    
    Supports two modes:
    - from_agent_name: check for agent-to-agent messages
    - from_user_name: check for human user messages (Feishu/Slack/Discord)
    
    Stores the actual message content in trigger.config['_matched_message']
    so the invocation context can include it.
    """
    from app.models.audit import ChatMessage
    from app.models.chat_session import ChatSession

    cfg = trigger.config or {}
    from_agent_name = cfg.get("from_agent_name")
    from_user_name = cfg.get("from_user_name")

    if not from_agent_name and not from_user_name:
        return False

    since = trigger.last_fired_at or trigger.created_at
    # Use _since_ts snapshot from trigger creation (set by _handle_set_trigger)
    # This is more precise than the old 5-minute lookback which caused false positives
    if trigger.fire_count == 0 and not trigger.last_fired_at:
        since_ts_str = cfg.get("_since_ts")
        if since_ts_str:
            try:
                since = datetime.fromisoformat(since_ts_str)
            except Exception:
                since = trigger.created_at
        # No _since_ts and no last_fired_at → use trigger.created_at (no lookback)

    try:
        async with async_session() as db:
            if from_agent_name:
                # --- Agent-to-agent message check (existing logic) ---
                from app.models.participant import Participant
                from app.models.agent import Agent as AgentModel
                agent_r = await db.execute(
                    select(AgentModel).where(AgentModel.name.ilike(f"%{from_agent_name}%"))
                )
                source_agent = agent_r.scalars().first()
                if not source_agent:
                    return False

                result = await db.execute(
                    select(Participant.id).where(
                        Participant.type == "agent",
                        Participant.ref_id == source_agent.id,
                    )
                )
                from_participant = result.scalar_one_or_none()
                if not from_participant:
                    return False

                from sqlalchemy import cast as sa_cast, String as SaString
                result = await db.execute(
                    select(ChatMessage).join(
                        ChatSession, ChatMessage.conversation_id == sa_cast(ChatSession.id, SaString)
                    ).where(
                        ChatMessage.participant_id == from_participant,
                        ChatMessage.created_at > since,
                        ChatMessage.role == "assistant",
                    ).order_by(ChatMessage.created_at.desc()).limit(1)
                )
                msg = result.scalar_one_or_none()
                if not msg:
                    return False
                cfg["_matched_message"] = (msg.content or "")[:2000]
                cfg["_matched_from"] = from_agent_name
                return True

            elif from_user_name:
                # --- Human user message check (Feishu/Slack/Discord) ---
                # Find sessions for this agent from external channels
                from sqlalchemy import cast as sa_cast, String as SaString
                from app.models.user import User
                from app.models.agent import Agent as AgentModel

                # 0. Get agent for tenant scoping
                agent_r = await db.execute(select(AgentModel).where(AgentModel.id == trigger.agent_id))
                agent = agent_r.scalar_one_or_none()

                # Look up user by display name or username within tenant
                from sqlalchemy import or_
                from app.models.user import User, Identity
                query = (
                    select(User)
                    .join(User.identity)
                    .where(
                        or_(
                            User.display_name.ilike(f"%{from_user_name}%"),
                            Identity.username.ilike(f"%{from_user_name}%"),
                        )
                    )
                )
                if agent and agent.tenant_id:
                    query = query.where(User.tenant_id == agent.tenant_id)
                
                user_r = await db.execute(query)
                target_user = user_r.scalars().first()

                if target_user:
                    # Find channel sessions for this user with this agent
                    result = await db.execute(
                        select(ChatMessage).join(
                            ChatSession, ChatMessage.conversation_id == sa_cast(ChatSession.id, SaString)
                        ).where(
                            ChatSession.agent_id == trigger.agent_id,
                            ChatSession.user_id == target_user.id,
                            ChatSession.source_channel.in_(["feishu", "slack", "discord"]),
                            ChatMessage.role == "user",
                            ChatMessage.created_at > since,
                        ).order_by(ChatMessage.created_at.desc()).limit(1)
                    )
                else:
                    # Fallback: search by message content or session title containing the name
                    result = await db.execute(
                        select(ChatMessage).join(
                            ChatSession, ChatMessage.conversation_id == sa_cast(ChatSession.id, SaString)
                        ).where(
                            ChatSession.agent_id == trigger.agent_id,
                            ChatSession.source_channel.in_(["feishu", "slack", "discord"]),
                            ChatMessage.role == "user",
                            ChatMessage.created_at > since,
                        ).order_by(ChatMessage.created_at.desc()).limit(1)
                    )

                msg = result.scalar_one_or_none()
                if not msg:
                    return False
                cfg["_matched_message"] = (msg.content or "")[:2000]
                cfg["_matched_from"] = from_user_name
                return True

    except Exception as e:
        logger.warning(f"on_message check failed for trigger {trigger.name}: {e}")
        return False


# ── Agent Invocation ────────────────────────────────────────────────

async def _invoke_agent_for_triggers(agent_id: uuid.UUID, triggers: list[AgentTrigger]):
    """Invoke an agent with context from one or more fired triggers.

    Creates a Reflection Session and calls the LLM.
    """
    from app.api.websocket import call_llm
    from app.services.agent_context import build_agent_context
    from app.models.llm import LLMModel
    from app.models.audit import ChatMessage
    from app.models.chat_session import ChatSession
    from app.models.participant import Participant
    from app.services.audit_logger import write_audit_log

    try:
        async with async_session() as db:
            # Load agent
            result = await db.execute(select(Agent).where(Agent.id == agent_id))
            agent = result.scalar_one_or_none()
            if not agent or agent.is_expired:
                return

            # Load LLM model
            if not agent.primary_model_id:
                logger.warning(f"Agent {agent.name} has no LLM model, skipping trigger invocation")
                return
            result = await db.execute(select(LLMModel).where(LLMModel.id == agent.primary_model_id))
            model = result.scalar_one_or_none()
            if not model:
                return
            # Skip invocation if model is disabled by admin
            if not model.enabled:
                logger.warning(f"Agent {agent.name}'s model {model.model} is disabled, skipping trigger invocation")
                return

            # Build trigger context
            context_parts = []
            trigger_names = []
            for t in triggers:
                part = f"触发器：{t.name} ({t.type})\n原因：{t.reason}"
                if t.focus_ref:
                    part += f"\n关联 Focus：{t.focus_ref}"
                # Include matched message for on_message triggers
                cfg = t.config or {}
                if t.type == "on_message" and cfg.get("_matched_message"):
                    part += f"\n收到来自 {cfg.get('_matched_from', '?')} 的消息：\n\"{cfg['_matched_message'][:500]}\""
                # Include webhook payload
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

            # Create Reflection Session
            title = f"🤖 内心独白：{', '.join(trigger_names)}"
            # Find agent's participant
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

            # Messages: trigger context only (call_llm builds system prompt internally)
            messages = [
                {"role": "user", "content": trigger_context},
            ]

            # Store trigger context as a message in the session
            db.add(ChatMessage(
                agent_id=agent_id,
                conversation_id=str(session_id),
                role="user",
                content=trigger_context,
                user_id=agent.creator_id,
                participant_id=agent_participant.id if agent_participant else None,
            ))
            await db.commit()
            # Cache participant ID for callbacks
            agent_participant_id = agent_participant.id if agent_participant else None

        # Call LLM (outside the DB session to avoid long transactions)
        collected_content = []

        async def on_chunk(text):
            collected_content.append(text)

        # Persist tool calls into Reflection Session for Reflections visibility
        async def on_tool_call(data):
            try:
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

        reply = await call_llm(
            model=model,
            messages=messages,
            agent_name=agent.name,
            role_description=agent.role_description or "",
            agent_id=agent_id,
            user_id=agent.creator_id,
            on_chunk=on_chunk,
            on_tool_call=on_tool_call,
        )

        # Save assistant reply to Reflection session
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

            # NOTE: trigger state (last_fired_at, fire_count, auto-disable)
            # is already updated in _tick() BEFORE this task was launched,
            # to prevent race-condition duplicate fires.

            await db.commit()

        # Push trigger result to user's active WebSocket connections
        final_reply = reply or "".join(collected_content)

        is_a2a_internal = all(t.name == "a2a_wake" for t in triggers)

        if final_reply and not is_a2a_internal:
            try:
                from app.api.websocket import manager as ws_manager
                agent_id_str = str(agent_id)

                # Build notification message with trigger badge
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

                import re as _re
                cleaned = final_reply
                _internal_patterns = [
                    r'\b(a2a_wait_\w+|a2a_wake)\b',
                    r'\bwait_?\w+_?(task|reply|followup|meeting|sync|api_key)\w*\b',
                    r'\bresolve_\w+\b',
                    r'\bfocus[_ ]?item\b',
                    r'\btask_delegate\b',
                    r'\bfocus_ref\b',
                    r'✅\s*(a2a\w+|wait\w+|触发器\w*|focus\w*).*(?:已取消|已为|保持|活跃|完成状态)[^\n]*',
                    r'[\-•]\s*(?:触发器|trigger|focus|wait_\w+|a2a\w+).*[^\n]*',
                    r'(?:触发器|trigger)\s+\S+\s*(?:已取消|保持活跃|已为完成状态|fired)',
                    r'已静默清理触发器',
                    r'已静默处理完毕',
                    r'继续待命[。，]?\s*',
                    r'，?\s*(?:继续)?待命。',
                ]
                for _pat in _internal_patterns:
                    cleaned = _re.sub(_pat, '', cleaned, flags=_re.IGNORECASE)
                cleaned = _re.sub(r'\n{3,}', '\n\n', cleaned).strip()
                cleaned = _re.sub(r'[。，]\s*$', '', cleaned).strip()
                if not cleaned:
                    cleaned = final_reply

                notification = f"⚡ {summary}\n\n{cleaned}"

                # Save to user's active chat session(s) for persistence
                async with async_session() as db:
                    from app.models.chat_session import ChatSession
                    from sqlalchemy import func

                    # Prefer the session the user currently has open (via WS)
                    active_session_ids = ws_manager.get_active_session_ids(agent_id_str)
                    target_session_ids = []

                    if active_session_ids:
                        target_session_ids = active_session_ids
                        logger.info(f"[Trigger] Saving notification to {len(active_session_ids)} active session(s)")
                    else:
                        # Fallback: most recent web session for this agent
                        _sr = await db.execute(
                            select(ChatSession.id)
                            .where(
                                ChatSession.agent_id == agent_id,
                                ChatSession.user_id == agent.creator_id,
                                ChatSession.source_channel.notin_(["trigger"]),
                            )
                            .order_by(
                                func.coalesce(ChatSession.last_message_at, ChatSession.created_at).desc()
                            )
                            .limit(1)
                        )
                        row = _sr.scalar_one_or_none()
                        if row:
                            target_session_ids = [str(row)]
                            logger.info(f"[Trigger] No active WS, saving to most recent session {row}")
                        else:
                            logger.warning(f"[Trigger] No web session found for agent {agent.name}")

                    for sid in target_session_ids:
                        db.add(ChatMessage(
                            agent_id=agent_id,
                            conversation_id=sid,
                            role="assistant",
                            content=notification,
                            user_id=agent.creator_id,
                        ))
                    if target_session_ids:
                        await db.commit()

                # Push to all active WebSocket connections for this agent
                if agent_id_str in ws_manager.active_connections:
                    for ws, _sid in list(ws_manager.active_connections[agent_id_str]):
                        try:
                            await ws.send_json({
                                "type": "trigger_notification",
                                "content": notification,
                                "triggers": [t.name for t in triggers],
                            })
                        except Exception:
                            pass  # Connection may have closed
            except Exception as e:
                logger.error(f"Failed to push trigger result to WebSocket: {e}")
                import traceback
                traceback.print_exc()

        # Audit log
        await write_audit_log("trigger_fired", {
            "agent_name": agent.name,
            "triggers": [{"name": t.name, "type": t.type} for t in triggers],
        }, agent_id=agent_id)

        logger.info(f"⚡ Triggers fired for {agent.name}: {[t.name for t in triggers]}")

    except Exception as e:
        logger.error(f"Failed to invoke agent {agent_id} for triggers: {e}")
        import traceback
        traceback.print_exc()


# ── Main Tick Loop ──────────────────────────────────────────────────

async def _tick():
    """One daemon tick: evaluate all triggers, group by agent, invoke."""
    now = datetime.now(timezone.utc)

    async with async_session() as db:
        result = await db.execute(
            select(AgentTrigger).where(AgentTrigger.is_enabled == True)
        )
        all_triggers = result.scalars().all()

    if not all_triggers:
        return


    # Evaluate and group fired triggers by agent
    fired_by_agent: dict[uuid.UUID, list[AgentTrigger]] = {}
    for trigger in all_triggers:
        # Auto-disable expired triggers
        if trigger.expires_at and now >= trigger.expires_at:
            async with async_session() as db:
                result = await db.execute(select(AgentTrigger).where(AgentTrigger.id == trigger.id))
                t = result.scalar_one_or_none()
                if t:
                    t.is_enabled = False
                    await db.commit()
            continue

        try:
            if await _evaluate_trigger(trigger, now):
                fired_by_agent.setdefault(trigger.agent_id, []).append(trigger)
        except Exception as e:
            logger.warning(f"Error evaluating trigger {trigger.name}: {e}")

    # Invoke each agent (with dedup window)
    for agent_id, agent_triggers in fired_by_agent.items():
        last = _last_invoke.get(agent_id)
        if last and (now - last).total_seconds() < DEDUP_WINDOW:
            continue  # Skip — invoked too recently
        _last_invoke[agent_id] = now

        # ── Immediately update trigger state BEFORE launching async task ──
        # This prevents the next tick from re-evaluating the same trigger as
        # "should fire" while the LLM call is still running (which can take
        # minutes). Without this, the 15s tick interval + 30s dedup window
        # would cause repeated invocations for long-running triggers.
        try:
            async with async_session() as db:
                for t in agent_triggers:
                    result = await db.execute(
                        select(AgentTrigger).where(AgentTrigger.id == t.id)
                    )
                    trigger = result.scalar_one_or_none()
                    if trigger:
                        trigger.last_fired_at = now
                        trigger.fire_count += 1
                        # Auto-disable single-shot types only
                        if trigger.type == "once":
                            trigger.is_enabled = False
                        if trigger.type == "webhook" and trigger.config:
                            trigger.config = {
                                **trigger.config,
                                "_webhook_pending": False,
                                "_webhook_payload": None,
                            }
                        if trigger.max_fires and trigger.fire_count >= trigger.max_fires:
                            trigger.is_enabled = False
                await db.commit()
        except Exception as e:
            logger.warning(f"Failed to pre-update trigger state: {e}")

        asyncio.create_task(_invoke_agent_for_triggers(agent_id, agent_triggers))


async def wake_agent_with_context(agent_id: uuid.UUID, message_context: str, *, from_agent_id: uuid.UUID | None = None, skip_dedup: bool = False) -> None:
    """Public API: wake an agent asynchronously with a message context.

    Creates a synthetic trigger invocation so the agent processes the
    message in a Reflection Session via the standard trigger path.
    Safe to call from any async context.

    Args:
        agent_id: The agent to wake.
        message_context: The message to deliver.
        from_agent_id: The agent that initiated this wake (for chain depth tracking).
        skip_dedup: If True, bypass the dedup window check. Use this for
                    genuine message deliveries (e.g. a task_delegate callback)
                    where skipping the wake would lose a real message.
    """
    import time as _time

    now = datetime.now(timezone.utc)

    if from_agent_id:
        chain_key = f"{from_agent_id}->{agent_id}"
        current_depth = _A2A_WAKE_CHAIN.get(chain_key, 0)
        if current_depth >= _A2A_MAX_WAKE_DEPTH:
            logger.warning(
                f"[A2A] Wake chain depth {current_depth} reached for {chain_key}, "
                f"stopping to prevent wake storm"
            )
            return

        _A2A_WAKE_CHAIN[chain_key] = current_depth + 1

        def _decay_chain():
            _A2A_WAKE_CHAIN.pop(chain_key, None)
        asyncio.get_event_loop().call_later(_A2A_WAKE_CHAIN_TTL, _decay_chain)

    if not skip_dedup and agent_id in _last_invoke:
        elapsed = (now - _last_invoke[agent_id]).total_seconds()
        if elapsed < DEDUP_WINDOW:
            logger.info(
                f"[A2A] Skipping wake for agent {agent_id} — "
                f"invoked {elapsed:.0f}s ago (dedup window {DEDUP_WINDOW}s)"
            )
            return

    _last_invoke[agent_id] = now

    dummy_trigger = AgentTrigger(
        id=uuid.uuid4(),
        agent_id=agent_id,
        name="a2a_wake",
        type="on_message",
        config={"from_agent_name": "", "_matched_message": message_context[:2000], "_matched_from": "agent"},
        reason=(
            "You received a notification from another agent. "
            "Read the message content above, update your focus and memory if needed, "
            "and take any action you deem necessary. "
            "Do NOT reply back to the sender unless you have a genuine question — "
            "this was a notification, not a request for response."
        ),
        is_enabled=True,
        last_fired_at=now,
        fire_count=0,
    )
    asyncio.create_task(_invoke_agent_for_triggers(agent_id, [dummy_trigger]))


async def start_trigger_daemon():
    """Start the background trigger daemon loop. Called from FastAPI startup."""
    logger.info("⚡ Trigger Daemon started (15s tick, heartbeat every ~60s)")
    _heartbeat_counter = 0
    while True:
        try:
            await _tick()
        except Exception as e:
            logger.error(f"Trigger Daemon error: {e}")
            import traceback
            traceback.print_exc()

        # Run heartbeat check every 4th tick (~60 seconds)
        _heartbeat_counter += 1
        if _heartbeat_counter >= 4:
            _heartbeat_counter = 0
            try:
                from app.services.heartbeat import _heartbeat_tick
                await _heartbeat_tick()
            except Exception as e:
                logger.error(f"Heartbeat tick error: {e}")

        await asyncio.sleep(TICK_INTERVAL)
