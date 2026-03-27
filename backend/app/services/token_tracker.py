"""Reusable token usage tracking for all LLM call paths.

Provides a single function to record token consumption against an Agent,
used by web chat, heartbeat, triggers, and A2A communication.
"""

import uuid

from loguru import logger


def estimate_tokens_from_chars(total_chars: int) -> int:
    """Rough token estimate when real usage is unavailable. ~3 chars per token."""
    return max(total_chars // 3, 1)


def extract_usage_tokens(usage: dict | None) -> int | None:
    """Extract total token count from an LLM response usage dict.

    Supports both OpenAI format (prompt_tokens + completion_tokens)
    and Anthropic format (input_tokens + output_tokens).
    Returns None if usage data is not available.
    """
    if not usage:
        return None

    # OpenAI: {"prompt_tokens": N, "completion_tokens": N, "total_tokens": N}
    if "total_tokens" in usage:
        return usage["total_tokens"]

    # Anthropic: {"input_tokens": N, "output_tokens": N}
    if "input_tokens" in usage or "output_tokens" in usage:
        return (usage.get("input_tokens", 0) or 0) + (usage.get("output_tokens", 0) or 0)

    return None


async def record_token_usage(agent_id: uuid.UUID, tokens: int) -> None:
    """Record token consumption for an agent.

    Safely updates tokens_used_today, tokens_used_month, and tokens_used_total.
    Uses an independent DB session to avoid interfering with the caller's transaction.
    """
    if tokens <= 0:
        return

    try:
        from app.database import async_session
        from app.models.agent import Agent
        from sqlalchemy import select

        async with async_session() as db:
            result = await db.execute(select(Agent).where(Agent.id == agent_id))
            agent = result.scalar_one_or_none()
            if agent:
                agent.tokens_used_today = (agent.tokens_used_today or 0) + tokens
                agent.tokens_used_month = (agent.tokens_used_month or 0) + tokens
                agent.tokens_used_total = (agent.tokens_used_total or 0) + tokens

                from datetime import datetime, timezone
                from sqlalchemy.dialects.postgresql import insert
                from app.models.activity_log import DailyTokenUsage

                today_date = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
                stmt = insert(DailyTokenUsage).values(
                    tenant_id=agent.tenant_id,
                    agent_id=agent.id,
                    date=today_date,
                    tokens_used=tokens
                ).on_conflict_do_update(
                    index_elements=["agent_id", "date"],
                    set_=dict(tokens_used=DailyTokenUsage.tokens_used + tokens)
                )
                await db.execute(stmt)

                await db.commit()
                logger.debug(f"Recorded {tokens:,} tokens for agent {agent.name}")
    except Exception as e:
        logger.warning(f"Failed to record token usage for agent {agent_id}: {e}")
