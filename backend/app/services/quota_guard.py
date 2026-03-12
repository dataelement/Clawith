"""Usage quota guard — check and enforce usage limits."""

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, func as sa_func

from app.database import async_session


class QuotaExceeded(Exception):
    """Raised when a quota limit is reached."""

    def __init__(self, message: str, quota_type: str = "generic"):
        self.message = message
        self.quota_type = quota_type
        super().__init__(message)


class AgentExpired(Exception):
    """Raised when an agent has expired."""

    def __init__(self, agent_name: str = ""):
        self.message = f"Agent '{agent_name}' has expired and is no longer available."
        super().__init__(self.message)


# ── Conversation quota ──────────────────────────────────────────────

async def check_conversation_quota(user_id: uuid.UUID) -> None:
    """Check if user has remaining conversation quota. Raises QuotaExceeded if not."""
    from app.models.user import User

    async with async_session() as db:
        result = await db.execute(select(User).where(User.id == user_id))
        user = result.scalar_one_or_none()
        if not user:
            return

        # Admin users are exempt
        if user.role in ("platform_admin", "org_admin"):
            return

        # Check period reset
        now = datetime.now(timezone.utc)
        if user.quota_message_period != "permanent" and user.quota_period_start:
            period_duration = _get_period_duration(user.quota_message_period)
            if now - user.quota_period_start >= period_duration:
                # Period expired — reset counter
                user.quota_messages_used = 0
                user.quota_period_start = now
                await db.commit()

        if user.quota_messages_used >= user.quota_message_limit:
            raise QuotaExceeded(
                f"Message quota exceeded ({user.quota_messages_used}/{user.quota_message_limit}). "
                f"Period: {user.quota_message_period}.",
                quota_type="conversation",
            )


async def increment_conversation_usage(user_id: uuid.UUID) -> None:
    """Increment conversation usage counter for a user."""
    from app.models.user import User

    async with async_session() as db:
        result = await db.execute(select(User).where(User.id == user_id))
        user = result.scalar_one_or_none()
        if not user:
            return

        if user.role in ("platform_admin", "org_admin"):
            return

        now = datetime.now(timezone.utc)

        # Initialize period start if needed
        if user.quota_message_period != "permanent" and not user.quota_period_start:
            user.quota_period_start = now

        user.quota_messages_used += 1
        await db.commit()


# ── Agent expiry ────────────────────────────────────────────────────

async def check_agent_expired(agent_id: uuid.UUID) -> None:
    """Check if agent has expired. If so, mark it and raise AgentExpired."""
    from app.models.agent import Agent

    async with async_session() as db:
        result = await db.execute(select(Agent).where(Agent.id == agent_id))
        agent = result.scalar_one_or_none()
        if not agent:
            return

        if agent.is_expired:
            raise AgentExpired(agent.name)

        now = datetime.now(timezone.utc)
        if agent.expires_at and now >= agent.expires_at:
            agent.is_expired = True
            agent.status = "stopped"
            agent.heartbeat_enabled = False
            await db.commit()
            raise AgentExpired(agent.name)


async def get_agent_expiry_reply(agent_name: str) -> str:
    """Return a message for when an expired agent is contacted."""
    return f"I'm sorry, but I ({agent_name}) am currently unavailable. My service period has ended. Please contact the platform administrator for assistance."


# ── Agent LLM call quota ───────────────────────────────────────────

async def check_agent_llm_quota(agent_id: uuid.UUID) -> None:
    """Check if agent has remaining daily LLM calls."""
    from app.models.agent import Agent

    async with async_session() as db:
        result = await db.execute(select(Agent).where(Agent.id == agent_id))
        agent = result.scalar_one_or_none()
        if not agent:
            return

        now = datetime.now(timezone.utc)

        # Daily reset
        if agent.llm_calls_reset_at and now.date() > agent.llm_calls_reset_at.date():
            agent.llm_calls_today = 0
            agent.llm_calls_reset_at = now
            await db.commit()

        if agent.llm_calls_today >= agent.max_llm_calls_per_day:
            raise QuotaExceeded(
                f"Agent '{agent.name}' has reached daily LLM call limit "
                f"({agent.llm_calls_today}/{agent.max_llm_calls_per_day}).",
                quota_type="agent_llm",
            )


async def increment_agent_llm_usage(agent_id: uuid.UUID) -> None:
    """Increment agent's daily LLM call counter."""
    from app.models.agent import Agent

    async with async_session() as db:
        result = await db.execute(select(Agent).where(Agent.id == agent_id))
        agent = result.scalar_one_or_none()
        if not agent:
            return

        now = datetime.now(timezone.utc)
        if not agent.llm_calls_reset_at or now.date() > agent.llm_calls_reset_at.date():
            agent.llm_calls_today = 1
            agent.llm_calls_reset_at = now
        else:
            agent.llm_calls_today += 1
        await db.commit()


# ── Agent creation quota ───────────────────────────────────────────

async def check_agent_creation_quota(user_id: uuid.UUID) -> None:
    """Check if user can create more agents."""
    from app.models.user import User
    from app.models.agent import Agent

    async with async_session() as db:
        result = await db.execute(select(User).where(User.id == user_id))
        user = result.scalar_one_or_none()
        if not user:
            return

        if user.role in ("platform_admin", "org_admin"):
            return

        # Count user's non-expired agents
        count_result = await db.execute(
            select(sa_func.count()).select_from(Agent).where(
                Agent.creator_id == user_id,
                Agent.is_expired == False,
            )
        )
        current_count = count_result.scalar() or 0

        if current_count >= user.quota_max_agents:
            raise QuotaExceeded(
                f"Agent creation limit reached ({current_count}/{user.quota_max_agents}).",
                quota_type="max_agents",
            )


# ── Heartbeat floor enforcement ────────────────────────────────────

async def enforce_heartbeat_floor(tenant_id: uuid.UUID, floor: int | None = None, db=None) -> int:
    """Enforce heartbeat floor on all agents in the tenant.

    Args:
        tenant_id: The tenant to enforce for.
        floor: The minimum interval in minutes. If None, reads from tenant.
        db: Optional existing database session to reuse (avoids session isolation bugs).

    Returns number of agents adjusted.
    """
    from app.models.agent import Agent
    from app.models.tenant import Tenant

    async def _enforce(session, floor_val):
        # If floor not provided, read from tenant
        if floor_val is None:
            result = await session.execute(select(Tenant).where(Tenant.id == tenant_id))
            tenant = result.scalar_one_or_none()
            if not tenant:
                return 0
            floor_val = tenant.min_heartbeat_interval_minutes

        # Find agents with interval below floor
        agents_result = await session.execute(
            select(Agent).where(
                Agent.tenant_id == tenant_id,
                Agent.heartbeat_interval_minutes < floor_val,
            )
        )
        agents = agents_result.scalars().all()
        for agent in agents:
            agent.heartbeat_interval_minutes = floor_val

        if agents:
            await session.commit()
        return len(agents)

    if db is not None:
        return await _enforce(db, floor)
    else:
        async with async_session() as new_db:
            return await _enforce(new_db, floor)


# ── Helper ─────────────────────────────────────────────────────────

def _get_period_duration(period: str) -> timedelta:
    """Convert period string to timedelta."""
    mapping = {
        "daily": timedelta(days=1),
        "weekly": timedelta(weeks=1),
        "monthly": timedelta(days=30),
    }
    return mapping.get(period, timedelta(days=36500))  # permanent = ~100 years
