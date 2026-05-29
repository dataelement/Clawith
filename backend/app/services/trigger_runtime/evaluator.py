"""Trigger evaluation and deterministic special-case handlers."""

from __future__ import annotations

import ipaddress
import uuid
from datetime import datetime, timezone, timedelta
from urllib.parse import urlparse

from croniter import croniter
from loguru import logger
from sqlalchemy import select

from app.database import async_session
from app.models.agent import Agent
from app.models.trigger import AgentTrigger

MIN_POLL_INTERVAL_MINUTES = 5


async def should_skip_non_workday(trigger: AgentTrigger, local_now: datetime) -> bool:
    if trigger.name != "daily_okr_collection":
        return False

    from app.models.okr import OKRSettings
    from app.models.tenant import Tenant
    from app.services.business_calendar import is_non_workday

    async with async_session() as db:
        result = await db.execute(
            select(Agent.tenant_id).where(Agent.id == trigger.agent_id)
        )
        tenant_id = result.scalar_one_or_none()
        if not tenant_id:
            return False

        settings_result = await db.execute(
            select(OKRSettings.daily_report_skip_non_workdays).where(OKRSettings.tenant_id == tenant_id)
        )
        skip_enabled = settings_result.scalar_one_or_none()
        if skip_enabled is False:
            return False

        tenant_result = await db.execute(
            select(Tenant.country_region).where(Tenant.id == tenant_id)
        )
        country_region = tenant_result.scalar_one_or_none()

    return is_non_workday(local_now.date(), country_region)


async def mark_trigger_skipped(trigger_id: uuid.UUID, now: datetime) -> None:
    try:
        async with async_session() as db:
            result = await db.execute(select(AgentTrigger).where(AgentTrigger.id == trigger_id))
            trigger = result.scalar_one_or_none()
            if trigger:
                trigger.last_fired_at = now
                await db.commit()
    except Exception as e:
        logger.warning(f"Failed to mark skipped trigger {trigger_id}: {e}")


async def mark_trigger_fired(trigger_id: uuid.UUID, now: datetime) -> None:
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


async def handle_okr_report_trigger(trigger: AgentTrigger, now: datetime) -> bool:
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

    await mark_trigger_fired(trigger.id, now)
    logger.info(f"[Trigger] Auto-generated OKR report for trigger {trigger.name}")
    return True


async def handle_okr_collection_trigger(trigger: AgentTrigger, now: datetime) -> bool:
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
    await mark_trigger_fired(trigger.id, now)
    logger.info(f"[Trigger] Deterministic OKR collection sent for trigger {trigger.name}")
    return True


def is_private_url(url: str) -> bool:
    try:
        parsed = urlparse(url)
        hostname = parsed.hostname
        if not hostname:
            return True
        if hostname in ("localhost", "127.0.0.1", "::1", "0.0.0.0"):
            return True
        import socket
        try:
            infos = socket.getaddrinfo(hostname, None)
            for info in infos:
                ip = ipaddress.ip_address(info[4][0])
                if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
                    return True
        except (socket.gaierror, ValueError):
            return True
        return False
    except Exception:
        return True


async def evaluate_trigger(trigger: AgentTrigger, now: datetime) -> bool:
    if not trigger.is_enabled:
        return False
    if trigger.expires_at and now >= trigger.expires_at:
        return False
    if trigger.max_fires is not None and trigger.fire_count >= trigger.max_fires:
        return False

    if trigger.last_fired_at:
        cooldown = timedelta(seconds=trigger.cooldown_seconds)
        if (now - trigger.last_fired_at) < cooldown:
            return False

    cfg = trigger.config or {}
    if isinstance(cfg, str):
        import json
        try:
            cfg = json.loads(cfg)
        except (json.JSONDecodeError, TypeError):
            cfg = {}
    t = trigger.type

    if t == "cron":
        expr = cfg.get("expr", "* * * * *")
        base = trigger.last_fired_at or trigger.created_at
        try:
            tz_name = cfg.get("timezone")
            if not tz_name:
                from app.services.timezone_utils import get_agent_timezone
                tz_name = await get_agent_timezone(trigger.agent_id)
            from zoneinfo import ZoneInfo
            try:
                tz = ZoneInfo(tz_name)
            except (KeyError, Exception):
                tz = ZoneInfo("UTC")
            local_now = now.astimezone(tz)
            local_base = base.astimezone(tz) if base.tzinfo else base.replace(tzinfo=tz)
            cron = croniter(expr, local_base)
            next_run = cron.get_next(datetime)
            if local_now >= next_run:
                if await should_skip_non_workday(trigger, local_now):
                    await mark_trigger_skipped(trigger.id, now)
                    logger.info(f"[Trigger] Skipped {trigger.name} on non-workday {local_now.date()}")
                    return False
                return True
            return False
        except Exception as e:
            logger.warning(f"Invalid cron expr '{expr}' for trigger {trigger.name}: {e}")
            return False

    if t == "once":
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

    if t == "interval":
        minutes = cfg.get("minutes", 30)
        base = trigger.last_fired_at or trigger.created_at
        return (now - base) >= timedelta(minutes=minutes)

    if t == "poll":
        interval_min = max(cfg.get("interval_min", 5), MIN_POLL_INTERVAL_MINUTES)
        base = trigger.last_fired_at or trigger.created_at
        if (now - base) < timedelta(minutes=interval_min):
            return False
        return await poll_check(trigger)

    if t == "on_message":
        return await check_new_agent_messages(trigger)

    if t == "webhook":
        return False

    return False


async def poll_check(trigger: AgentTrigger) -> bool:
    import httpx

    cfg = trigger.config or {}
    if isinstance(cfg, str):
        import json
        try:
            cfg = json.loads(cfg)
        except (json.JSONDecodeError, TypeError):
            cfg = {}
    url = cfg.get("url")
    if not url:
        return False
    if is_private_url(url):
        logger.warning(f"Poll blocked for trigger {trigger.name}: private/internal URL '{url}'")
        return False
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.request(cfg.get("method", "GET"), url, headers=cfg.get("headers", {}))
            resp.raise_for_status()

        data = resp.json()
        json_path = cfg.get("json_path", "$")
        current_value = extract_json_path(data, json_path)
        current_str = str(current_value)
        fire_on = cfg.get("fire_on", "change")
        should_fire = False
        if fire_on == "match":
            should_fire = current_str == str(cfg.get("match_value", ""))
        else:
            last_value = cfg.get("_last_value")
            should_fire = last_value is not None and current_str != last_value

        cfg["_last_value"] = current_str
        try:
            from sqlalchemy import update
            async with async_session() as db:
                await db.execute(
                    update(AgentTrigger).where(AgentTrigger.id == trigger.id).values(config=cfg)
                )
                await db.commit()
        except Exception as e:
            logger.warning(f"Failed to persist poll _last_value for {trigger.name}: {e}")

        return should_fire
    except Exception as e:
        logger.warning(f"Poll failed for trigger {trigger.name}: {e}")
        return False


def extract_json_path(data, path: str):
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


async def check_new_agent_messages(trigger: AgentTrigger) -> bool:
    from app.models.audit import ChatMessage
    from app.models.chat_session import ChatSession

    cfg = trigger.config or {}
    if isinstance(cfg, str):
        import json
        try:
            cfg = json.loads(cfg)
        except (json.JSONDecodeError, TypeError):
            cfg = {}
    from_agent_name = cfg.get("from_agent_name")
    from_user_name = cfg.get("from_user_name")
    if not from_agent_name and not from_user_name:
        return False

    since = trigger.last_fired_at or trigger.created_at
    if trigger.fire_count == 0 and not trigger.last_fired_at:
        since_ts_str = cfg.get("_since_ts")
        if since_ts_str:
            try:
                since = datetime.fromisoformat(since_ts_str)
            except Exception:
                since = trigger.created_at

    try:
        async with async_session() as db:
            if from_agent_name:
                from app.models.participant import Participant
                from app.models.agent import Agent as AgentModel
                if isinstance(from_agent_name, list):
                    from_agent_name = from_agent_name[0] if from_agent_name else ""
                if not isinstance(from_agent_name, str):
                    return False
                safe_agent_name = from_agent_name.replace("%", "").replace("_", r"\_")
                agent_r = await db.execute(select(AgentModel).where(AgentModel.name.ilike(f"%{safe_agent_name}%")))
                source_agent = agent_r.scalars().first()
                if not source_agent:
                    return False
                result = await db.execute(
                    select(Participant.id).where(Participant.type == "agent", Participant.ref_id == source_agent.id)
                )
                from_participant = result.scalar_one_or_none()
                if not from_participant:
                    return False
                from sqlalchemy import String as SaString, cast as sa_cast
                result = await db.execute(
                    select(ChatMessage)
                    .join(ChatSession, ChatMessage.conversation_id == sa_cast(ChatSession.id, SaString))
                    .where(
                        ChatMessage.participant_id == from_participant,
                        ChatMessage.created_at > since,
                    )
                    .order_by(ChatMessage.created_at.desc())
                    .limit(1)
                )
                msg = result.scalar_one_or_none()
                if not msg:
                    return False
                cfg["_matched_message"] = (msg.content or "")[:2000]
                cfg["_matched_from"] = from_agent_name
                return True

            if from_user_name:
                from sqlalchemy import or_
                from sqlalchemy import String as SaString, cast as sa_cast
                from app.models.agent import Agent as AgentModel
                from app.models.user import Identity, User

                agent_r = await db.execute(select(AgentModel).where(AgentModel.id == trigger.agent_id))
                agent = agent_r.scalar_one_or_none()
                if isinstance(from_user_name, list):
                    from_user_name = from_user_name[0] if from_user_name else ""
                if not isinstance(from_user_name, str):
                    return False
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
                    result = await db.execute(
                        select(ChatMessage)
                        .join(ChatSession, ChatMessage.conversation_id == sa_cast(ChatSession.id, SaString))
                        .where(
                            ChatSession.agent_id == trigger.agent_id,
                            ChatSession.user_id == target_user.id,
                            ChatSession.source_channel.in_(["feishu", "slack", "discord", "web"]),
                            ChatMessage.role == "user",
                            ChatMessage.created_at > since,
                        )
                        .order_by(ChatMessage.created_at.desc())
                        .limit(1)
                    )
                else:
                    result = await db.execute(
                        select(ChatMessage)
                        .join(ChatSession, ChatMessage.conversation_id == sa_cast(ChatSession.id, SaString))
                        .where(
                            ChatSession.agent_id == trigger.agent_id,
                            ChatSession.source_channel.in_(["feishu", "slack", "discord", "web"]),
                            ChatMessage.role == "user",
                            ChatMessage.created_at > since,
                            or_(
                                ChatSession.title.ilike(f"%{safe_user_name}%"),
                                ChatMessage.content.ilike(f"%{safe_user_name}%"),
                            ),
                        )
                        .order_by(ChatMessage.created_at.desc())
                        .limit(1)
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

    return False
