"""Timezone utilities for resolving agent and tenant timezones."""

import uuid
from zoneinfo import ZoneInfo
from datetime import datetime, timezone

from sqlalchemy import select

from app.database import async_session


# Common timezones for frontend dropdown
COMMON_TIMEZONES = [
    "UTC",
    "Asia/Shanghai",
    "Asia/Tokyo",
    "Asia/Seoul",
    "Asia/Singapore",
    "Asia/Kolkata",
    "Asia/Dubai",
    "Europe/London",
    "Europe/Paris",
    "Europe/Berlin",
    "Europe/Moscow",
    "America/New_York",
    "America/Chicago",
    "America/Denver",
    "America/Los_Angeles",
    "America/Sao_Paulo",
    "Australia/Sydney",
    "Pacific/Auckland",
]


async def get_agent_timezone(agent_id: uuid.UUID) -> str:
    """Resolve effective timezone for an agent.

    Priority: agent.timezone → tenant.timezone → 'UTC'
    """
    from app.models.agent import Agent
    from app.models.tenant import Tenant

    async with async_session() as db:
        result = await db.execute(select(Agent).where(Agent.id == agent_id))
        agent = result.scalar_one_or_none()
        if not agent:
            return "UTC"

        # Agent-level override
        if agent.timezone:
            return agent.timezone

        # Tenant-level default
        if agent.tenant_id:
            t_result = await db.execute(select(Tenant).where(Tenant.id == agent.tenant_id))
            tenant = t_result.scalar_one_or_none()
            if tenant and tenant.timezone:
                return tenant.timezone

        return "UTC"


def get_agent_timezone_sync(agent, tenant=None) -> str:
    """Synchronous version — when agent and tenant objects are already loaded.

    Priority: agent.timezone → tenant.timezone → 'UTC'
    """
    if agent.timezone:
        return agent.timezone
    if tenant and hasattr(tenant, 'timezone') and tenant.timezone:
        return tenant.timezone
    return "UTC"


def now_in_timezone(tz_name: str) -> datetime:
    """Get current datetime in the given timezone."""
    try:
        tz = ZoneInfo(tz_name)
    except (KeyError, Exception):
        tz = ZoneInfo("UTC")
    return datetime.now(tz)
