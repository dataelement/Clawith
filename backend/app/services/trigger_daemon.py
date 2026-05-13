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


def _cleanup_stale_invoke_cache():
    now = datetime.now(timezone.utc)
    stale = [k for k, v in _last_invoke.items() if (now - v).total_seconds() > DEDUP_WINDOW * 2]
    for k in stale:
        del _last_invoke[k]


async def _should_skip_non_workday(trigger: AgentTrigger, local_now: datetime) -> bool:
    """Skip OKR daily report triggers on company non-workdays when configured."""
    if trigger.name != "daily_okr_collection":
        return False

    from app.models.okr import OKRSettings
    from app.models.tenant import Tenant
    from app.services.business_calendar import is_non_workday

    async with async_session() as db:
        result = await db.execute(
            select(Agent.tenant_id)
            .where(Agent.id == trigger.agent_id)
        )
        tenant_id = result.scalar_one_or_none()
        if not tenant_id:
            return False

        settings_result = await db.execute(
            select(OKRSettings.daily_report_skip_non_workdays)
            .where(OKRSettings.tenant_id == tenant_id)
        )
        skip_enabled = settings_result.scalar_one_or_none()
        if skip_enabled is False:
            return False

        tenant_result = await db.execute(
            select(Tenant.country_region).where(Tenant.id == tenant_id)
        )
        country_region = tenant_result.scalar_one_or_none()

    return is_non_workday(local_now.date(), country_region)


async def _mark_trigger_skipped(trigger_id: uuid.UUID, now: datetime) -> None:
    """Advance a cron trigger without invoking the agent."""
    try:
        async with async_session() as db:
            result = await db.execute(select(AgentTrigger).where(AgentTrigger.id == trigger_id))
            trigger = result.scalar_one_or_none()
            if trigger:
                trigger.last_fired_at = now
                await db.commit()
    except Exception as e:
        logger.warning(f"Failed to mark skipped trigger {trigger_id}: {e}")


async def _mark_trigger_fired(trigger_id: uuid.UUID, now: datetime) -> None:
    """Persist fire metadata for a trigger that was already handled."""
    try:
        async with async_session() as db:
            result = await db.execute(select(AgentTrigger).where(AgentTrigger.id == trigger_id))
            trigger = result.scalar_one_or_none()
            if trigger:
                trigger.last_fired_at = now
                trigger.fire_count += 1
                if trigger.type == "once":
                    trigger.is_enabled = False
                if trigger.max_fires and trigger.fire_count >= trigger.max_fires:
                    trigger.is_enabled = False
                await db.commit()
    except Exception as e:
        logger.warning(f"Failed to mark fired trigger {trigger_id}: {e}")


async def _handle_okr_report_trigger(trigger: AgentTrigger, now: datetime) -> bool:
    """Handle company-level OKR report generation without waking the agent."""
    if trigger.name not in {"daily_okr_report", "weekly_okr_report", "monthly_okr_report"}:
        return False

    from zoneinfo import ZoneInfo
    from app.models.okr import OKRSettings
    from app.services.okr_reporting import (
        generate_company_daily_report,
        generate_company_monthly_report,
        generate_company_weekly_report,
    )
    from app.services.timezone_utils import get_agent_timezone

    async with async_session() as db:
        agent_result = await db.execute(select(Agent.tenant_id).where(Agent.id == trigger.agent_id))
        tenant_id = agent_result.scalar_one_or_none()
        if not tenant_id:
            return True

        settings_result = await db.execute(select(OKRSettings).where(OKRSettings.tenant_id == tenant_id))
        settings = settings_result.scalar_one_or_none()
        if not settings or not settings.enabled:
            return True

    tz_name = await get_agent_timezone(trigger.agent_id)
    try:
        tz = ZoneInfo(tz_name)
    except Exception:
        tz = ZoneInfo("UTC")
    local_today = now.astimezone(tz).date()

    if trigger.name == "daily_okr_report":
        await generate_company_daily_report(tenant_id, local_today - timedelta(days=1))
    elif trigger.name == "weekly_okr_report":
        previous_week_anchor = local_today - timedelta(days=7)
        week_start = previous_week_anchor - timedelta(days=previous_week_anchor.weekday())
        await generate_company_weekly_report(tenant_id, week_start)
    elif trigger.name == "monthly_okr_report":
        previous_month_end = local_today.replace(day=1) - timedelta(days=1)
        await generate_company_monthly_report(tenant_id, previous_month_end)

    await _mark_trigger_fired(trigger.id, now)
    logger.info(f"[Trigger] Auto-generated OKR report for trigger {trigger.name}")
    return True


async def _handle_okr_collection_trigger(trigger: AgentTrigger, now: datetime) -> bool:
    """Handle deterministic OKR daily collection without relying on a free-form LLM plan."""
    if trigger.name != "daily_okr_collection":
        return False

    from app.models.okr import OKRSettings
    from app.services.okr_daily_collection import trigger_daily_collection_for_tenant

    async with async_session() as db:
        agent_result = await db.execute(select(Agent.tenant_id).where(Agent.id == trigger.agent_id))
        tenant_id = agent_result.scalar_one_or_none()
        if not tenant_id:
            return True

        settings_result = await db.execute(select(OKRSettings).where(OKRSettings.tenant_id == tenant_id))
        settings = settings_result.scalar_one_or_none()
        if not settings or not settings.enabled or not settings.daily_report_enabled:
            return True

    await trigger_daily_collection_for_tenant(tenant_id)
    await _mark_trigger_fired(trigger.id, now)
    logger.info(f"[Trigger] Deterministic OKR collection sent for trigger {trigger.name}")
    return True

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
            if local_now >= next_run:
                if await _should_skip_non_workday(trigger, local_now):
                    await _mark_trigger_skipped(trigger.id, now)
                    logger.info(f"[Trigger] Skipped {trigger.name} on non-workday {local_now.date()}")
                    return False
                return True
            return False
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
                safe_agent_name = from_agent_name.replace("%", "").replace("_", r"\_")
                agent_r = await db.execute(
                    select(AgentModel).where(AgentModel.name.ilike(f"%{safe_agent_name}%"))
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
                safe_user_name = from_user_name.replace("%", "").replace("_", r"\_")
                query = (
                    select(User)
                    .join(User.identity)
                    .where(
                        or_(
                            User.display_name.ilike(f"%{safe_user_name}%"),
                            Identity.username.ilike(f"%{safe_user_name}%"),
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
                            ChatSession.source_channel.in_(["feishu", "slack", "discord", "web"]),
                            ChatMessage.role == "user",
                            ChatMessage.created_at > since,
                        ).order_by(ChatMessage.created_at.desc()).limit(1)
                    )
                else:
                    # Fallback: search by session title or message content containing the target name
                    result = await db.execute(
                        select(ChatMessage).join(
                            ChatSession, ChatMessage.conversation_id == sa_cast(ChatSession.id, SaString)
                        ).where(
                            ChatSession.agent_id == trigger.agent_id,
                            ChatSession.source_channel.in_(["feishu", "slack", "discord", "web"]),
                            ChatMessage.role == "user",
                            ChatMessage.created_at > since,
                            or_(
                                ChatSession.title.ilike(f"%{safe_user_name}%"),
                                ChatMessage.content.ilike(f"%{safe_user_name}%"),
                            ),
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

async def _resolve_trigger_delivery_target(agent: Agent, triggers: list[AgentTrigger]) -> dict | None:
    """Resolve where a trigger result should be delivered.

    Priority:
    1. Explicit A2A callback session
    2. Originating agent-to-agent session
    3. Originating platform user → that user's primary platform session
    4. Pure trigger/reflection context → no user-facing delivery
    """
    from app.models.chat_session import ChatSession
    from app.services.chat_session_service import ensure_primary_platform_session

    # Synthetic A2A wake triggers already carry the callback session explicitly.
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
                primary = await ensure_primary_platform_session(
                    db,
                    agent.id,
                    uuid.UUID(origin_user_id),
                )
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

async def _invoke_agent_for_triggers(agent_id: uuid.UUID, triggers: list[AgentTrigger]):
    """Invoke an agent with context from one or more fired triggers.

    Creates a Reflection Session and calls the LLM.
    """
    from app.services.llm import call_llm
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

            # Build trigger context. Keep this model-facing prompt in English so
            # autonomous wakeups behave consistently across UI locales.
            context_parts = []
            trigger_names = []
            for t in triggers:
                part = f"Trigger: {t.name} ({t.type})\nReason: {t.reason}"
                if t.name == "daily_okr_collection":
                    part += (
                        "\nExecution requirements: First call get_okr_settings to confirm whether daily report collection is enabled. "
                        "If it is enabled, only contact members and digital employees in your relationship network to collect today's final daily reports, "
                        "then organize them into a formal daily report no longer than 2000 characters. "
                        "If it is disabled, state that no action is needed and stop."
                    )
                elif t.name in ("daily_okr_report", "weekly_okr_report", "monthly_okr_report"):
                    part += (
                        "\nExecution requirements: This company-level report is generated automatically by the system. "
                        "If you are awakened, only add necessary clarification. Do not start another member collection round."
                    )
                elif t.name == "biweekly_okr_checkin":
                    part += (
                        "\nExecution requirements: First call get_okr_settings to confirm whether OKR is enabled. "
                        "If enabled, check the current-cycle company and member OKRs, then proactively remind members who have not set OKRs or whose progress is lagging. "
                        "If disabled, state that no action is needed and stop."
                    )
                elif t.name == "monthly_okr_report":
                    part += (
                        "\nExecution requirements: First call get_okr_settings to confirm whether OKR is enabled. "
                        "If enabled, call generate_monthly_okr_report to generate the OKR monthly report for the month that just ended, "
                        "then send it to admins or publish it to Plaza. If disabled, state that no action is needed and stop."
                    )
                if t.focus_ref:
                    part += f"\nRelated Focus: {t.focus_ref}"
                # Include matched message for on_message triggers
                cfg = t.config or {}
                if t.type == "on_message" and cfg.get("_matched_message"):
                    part += f"\nMatched message from {cfg.get('_matched_from', '?')}:\n\"{cfg['_matched_message'][:500]}\""
                if t.type == "on_message" and cfg.get("okr_member_id") and cfg.get("okr_report_date"):
                    part += (
                        "\nExecution requirements: This is a daily-report reply ingestion event."
                        f"\n1. Organize the other party's reply into a final daily report no longer than 2000 characters."
                        f"\n2. Immediately call upsert_member_daily_report(report_date=\"{cfg['okr_report_date']}\", "
                        f"member_type=\"{cfg.get('okr_member_type', 'user')}\", "
                        f"member_id=\"{cfg['okr_member_id']}\", content=\"<organized daily report>\")."
                        "\n3. After the tool call succeeds, send a brief confirmation that you received and recorded it."
                        "\n4. Do not only confirm without calling the tool, and do not store the raw long conversation verbatim as the daily report."
                    )
                # Include webhook payload
                if t.type == "webhook" and cfg.get("_webhook_payload"):
                    payload_str = cfg["_webhook_payload"]
                    if len(payload_str) > 2000:
                        payload_str = payload_str[:2000] + "... (truncated)"
                    part += f"\nWebhook Payload:\n{payload_str}"
                context_parts.append(part)
                trigger_names.append(t.name)

            trigger_context = (
                "===== Wake Context =====\n"
                f"Wake source: trigger ({'multiple triggers fired together' if len(triggers) > 1 else 'trigger fired'})\n\n"
                + "\n---\n".join(context_parts)
                + "\n========================"
            )

            # Create Reflection Session
            title = f"🤖 Reflection: {', '.join(trigger_names)}"
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
        delivered_platform_message_via_tool = False

        async def on_chunk(text):
            collected_content.append(text)

        # Persist tool calls into Reflection Session for Reflections visibility
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
            # A2A wake uses the agent's own max_tool_rounds setting (no override)
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

        # Compute final reply text once
        final_reply = reply or "".join(collected_content)

        # ── Save reply to A2A session if this was an agent-to-agent wake ──
        # This makes the target agent's reply visible in the A2A chat history
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
                        # Update session timestamp
                        from app.models.chat_session import ChatSession as _CS
                        _cs_r = await db.execute(select(_CS).where(_CS.id == uuid.UUID(a2a_sid)))
                        _cs = _cs_r.scalar_one_or_none()
                        if _cs:
                            _cs.last_message_at = datetime.now(timezone.utc)
                        await db.commit()
                        logger.info(f"[A2A] Saved reply to A2A session {a2a_sid}")
                except Exception as e:
                    logger.warning(f"[A2A] Failed to save reply to A2A session {a2a_sid}: {e}")
                break  # Only save once

        # Route trigger results to a single deterministic destination. Pure reflection/system
        # wakes stay inside the reflection session and should not spill into arbitrary user chats.
        is_a2a_internal = all(t.name == "a2a_wake" for t in triggers)
        delivery_target = None if is_a2a_internal else await _resolve_trigger_delivery_target(agent, triggers)

        if final_reply and delivery_target and not delivered_platform_message_via_tool:
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

                _is_a2a_wait = any(t.name.startswith("a2a_wait_") for t in triggers)
                if _is_a2a_wait:
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
                else:
                    cleaned = final_reply

                notification = f"⚡ {summary}\n\n{cleaned}"

                target_session_id = delivery_target["session_id"]
                owner_user_id = delivery_target.get("owner_user_id")

                # Save to the resolved destination session for persistence.
                async with async_session() as db:
                    from app.models.chat_session import ChatSession
                    from app.api.websocket import maybe_mark_session_read_for_active_viewer

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

                payload = {
                    "type": "trigger_notification",
                    "content": notification,
                    "triggers": [t.name for t in triggers],
                    "session_id": target_session_id,
                }

                # Notify only the user who owns the destination session. The frontend will append
                # the message only when that exact session is open; otherwise it just refreshes
                # unread/session state.
                if owner_user_id:
                    await ws_manager.send_to_user(agent_id_str, owner_user_id, payload)
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
                handled = await _handle_okr_report_trigger(trigger, now)
                if not handled:
                    handled = await _handle_okr_collection_trigger(trigger, now)
                if not handled:
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


async def wake_agent_with_context(agent_id: uuid.UUID, message_context: str, *, from_agent_id: uuid.UUID | None = None, skip_dedup: bool = False, a2a_session_id: str | None = None) -> None:
    """Public API: wake an agent asynchronously with a message context.

    Creates a synthetic trigger invocation so the agent processes the
    message in a Reflection Session via the standard trigger path.
    If a2a_session_id is provided, the agent's reply will also be saved
    to the A2A chat session for visibility in the admin chat history.
    Safe to call from any async context.

    Args:
        agent_id: The agent to wake.
        message_context: The message to deliver.
        from_agent_id: The agent that initiated this wake (for chain depth tracking).
        skip_dedup: If True, bypass the dedup window check.
        a2a_session_id: Optional A2A chat session ID to mirror the reply into.
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
        asyncio.get_running_loop().call_later(_A2A_WAKE_CHAIN_TTL, _decay_chain)

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
        config={"from_agent_name": "", "_matched_message": message_context[:2000], "_matched_from": "agent", "_a2a_session_id": a2a_session_id},
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
            _cleanup_stale_invoke_cache()
            try:
                from app.services.heartbeat import _heartbeat_tick
                await _heartbeat_tick()
            except Exception as e:
                logger.error(f"Heartbeat tick error: {e}")

        await asyncio.sleep(TICK_INTERVAL)
