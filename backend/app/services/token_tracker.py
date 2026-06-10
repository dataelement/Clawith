"""Reusable token usage tracking for all LLM call paths.

Provides a single function to record token consumption against an Agent,
used by web chat, heartbeat, triggers, and A2A communication.
"""

import uuid
from dataclasses import dataclass

from loguru import logger


@dataclass
class TokenUsage:
    """Normalized token accounting returned by model providers."""

    total_tokens: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0
    estimated_tokens: int = 0

    def add(self, other: "TokenUsage") -> None:
        self.total_tokens += other.total_tokens
        self.input_tokens += other.input_tokens
        self.output_tokens += other.output_tokens
        self.cache_read_tokens += other.cache_read_tokens
        self.cache_creation_tokens += other.cache_creation_tokens
        self.estimated_tokens += other.estimated_tokens


def estimate_tokens_from_chars(total_chars: int) -> int:
    """Rough token estimate when real usage is unavailable. ~3 chars per token."""
    return max(total_chars // 3, 1)


def estimate_token_usage_from_chars(total_chars: int) -> TokenUsage:
    tokens = estimate_tokens_from_chars(total_chars)
    return TokenUsage(total_tokens=tokens, estimated_tokens=tokens)


def _int_token(value) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _token_counter(source: dict, *keys: str) -> int:
    return sum(_int_token(source.get(key)) for key in keys)


def extract_token_usage(usage: dict | None) -> TokenUsage | None:
    """Extract normalized token usage, including prompt-cache counters when available."""
    if not usage:
        return None

    # OpenAI compatible:
    # {"prompt_tokens": N, "completion_tokens": N, "total_tokens": N,
    #  "prompt_tokens_details": {"cached_tokens": N}}
    if "total_tokens" in usage:
        detail_sources = [
            details
            for details in (
                usage.get("prompt_tokens_details"),
                usage.get("input_tokens_details"),
            )
            if isinstance(details, dict)
        ]
        cached = _token_counter(
            usage,
            "cached_tokens",
            "cache_read_tokens",
            "cache_read_input_tokens",
        )
        cache_creation = _token_counter(
            usage,
            "cache_creation_tokens",
            "cache_creation_input_tokens",
        )
        for details in detail_sources:
            cached += _token_counter(
                details,
                "cached_tokens",
                "cache_read_tokens",
                "cache_read_input_tokens",
            )
            cache_creation += _token_counter(
                details,
                "cache_creation_tokens",
                "cache_creation_input_tokens",
            )
        if cached or cache_creation:
            logger.info(
                f"[Token Cache] API Provider -> Created: {cache_creation} tokens, "
                f"Read: {cached} tokens"
            )
        input_tokens = _int_token(usage.get("prompt_tokens", usage.get("input_tokens", 0)))
        output_tokens = _int_token(usage.get("completion_tokens", usage.get("output_tokens", 0)))
        total_tokens = _int_token(usage.get("total_tokens", input_tokens + output_tokens))
        return TokenUsage(
            total_tokens=total_tokens,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_read_tokens=cached,
            cache_creation_tokens=cache_creation,
        )

    # Anthropic:
    # {"input_tokens": N, "output_tokens": N,
    #  "cache_creation_input_tokens": N, "cache_read_input_tokens": N}
    if "input_tokens" in usage or "output_tokens" in usage:
        cache_creation = _token_counter(usage, "cache_creation_input_tokens", "cache_creation_tokens")
        cache_read = _token_counter(usage, "cache_read_input_tokens", "cache_read_tokens", "cached_tokens")
        details = usage.get("prompt_tokens_details")
        if isinstance(details, dict):
            cache_creation += _token_counter(details, "cache_creation_input_tokens", "cache_creation_tokens")
            cache_read += _token_counter(details, "cached_tokens", "cache_read_input_tokens", "cache_read_tokens")
        if cache_creation or cache_read:
            logger.info(f"[Token Cache] Anthropic Native Hit -> Created: {cache_creation}, Read: {cache_read} tokens")
        input_tokens = _int_token(usage.get("input_tokens", 0))
        output_tokens = _int_token(usage.get("output_tokens", 0))
        return TokenUsage(
            total_tokens=input_tokens + output_tokens,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_read_tokens=cache_read,
            cache_creation_tokens=cache_creation,
        )

    # Gemini usage metadata can be normalized by the client, but keep a direct
    # fallback for providers that pass it through.
    if "promptTokenCount" in usage or "candidatesTokenCount" in usage:
        input_tokens = _int_token(usage.get("promptTokenCount", 0))
        output_tokens = _int_token(usage.get("candidatesTokenCount", 0))
        total_tokens = _int_token(usage.get("totalTokenCount", input_tokens + output_tokens))
        cached = _int_token(usage.get("cachedContentTokenCount", 0))
        return TokenUsage(
            total_tokens=total_tokens,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_read_tokens=cached,
        )

    return None


def extract_usage_tokens(usage: dict | None) -> int | None:
    """Extract total token count from an LLM response usage dict.

    Supports both OpenAI format (prompt_tokens + completion_tokens)
    and Anthropic format (input_tokens + output_tokens).
    Returns None if usage data is not available.
    """
    parsed = extract_token_usage(usage)
    return parsed.total_tokens if parsed else None


async def record_token_usage(
    agent_id: uuid.UUID,
    tokens: int | TokenUsage,
    *,
    input_tokens: int = 0,
    output_tokens: int = 0,
    cache_read_tokens: int = 0,
    cache_creation_tokens: int = 0,
    estimated_tokens: int = 0,
) -> None:
    """Record token consumption for an agent.

    Safely updates tokens_used_today, tokens_used_month, and tokens_used_total.
    Uses an independent DB session to avoid interfering with the caller's transaction.
    """
    usage = tokens if isinstance(tokens, TokenUsage) else TokenUsage(
        total_tokens=tokens,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_read_tokens=cache_read_tokens,
        cache_creation_tokens=cache_creation_tokens,
        estimated_tokens=estimated_tokens,
    )
    if usage.total_tokens <= 0:
        return

    try:
        from app.database import async_session
        from app.models.agent import Agent
        from sqlalchemy import select

        async with async_session() as db:
            result = await db.execute(select(Agent).where(Agent.id == agent_id))
            agent = result.scalar_one_or_none()
            if agent:
                agent.tokens_used_today = (agent.tokens_used_today or 0) + usage.total_tokens
                agent.tokens_used_month = (agent.tokens_used_month or 0) + usage.total_tokens
                agent.tokens_used_total = (agent.tokens_used_total or 0) + usage.total_tokens
                agent.cache_read_tokens_today = (agent.cache_read_tokens_today or 0) + usage.cache_read_tokens
                agent.cache_read_tokens_month = (agent.cache_read_tokens_month or 0) + usage.cache_read_tokens
                agent.cache_read_tokens_total = (agent.cache_read_tokens_total or 0) + usage.cache_read_tokens
                agent.cache_creation_tokens_today = (
                    agent.cache_creation_tokens_today or 0
                ) + usage.cache_creation_tokens
                agent.cache_creation_tokens_month = (
                    agent.cache_creation_tokens_month or 0
                ) + usage.cache_creation_tokens
                agent.cache_creation_tokens_total = (
                    agent.cache_creation_tokens_total or 0
                ) + usage.cache_creation_tokens

                from datetime import datetime, timezone
                from sqlalchemy.dialects.postgresql import insert
                from app.models.activity_log import DailyTokenUsage

                today_date = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
                stmt = insert(DailyTokenUsage).values(
                    tenant_id=agent.tenant_id,
                    agent_id=agent.id,
                    date=today_date,
                    tokens_used=usage.total_tokens,
                    input_tokens=usage.input_tokens,
                    output_tokens=usage.output_tokens,
                    cache_read_tokens=usage.cache_read_tokens,
                    cache_creation_tokens=usage.cache_creation_tokens,
                    estimated_tokens=usage.estimated_tokens,
                ).on_conflict_do_update(
                    index_elements=["agent_id", "date"],
                    set_=dict(
                        tokens_used=DailyTokenUsage.tokens_used + usage.total_tokens,
                        input_tokens=DailyTokenUsage.input_tokens + usage.input_tokens,
                        output_tokens=DailyTokenUsage.output_tokens + usage.output_tokens,
                        cache_read_tokens=DailyTokenUsage.cache_read_tokens + usage.cache_read_tokens,
                        cache_creation_tokens=DailyTokenUsage.cache_creation_tokens + usage.cache_creation_tokens,
                        estimated_tokens=DailyTokenUsage.estimated_tokens + usage.estimated_tokens,
                    )
                )
                await db.execute(stmt)

                await db.commit()
                logger.debug(
                    f"Recorded {usage.total_tokens:,} tokens for agent {agent.name} "
                    f"(cache_read={usage.cache_read_tokens:,})"
                )
    except Exception as e:
        logger.warning(f"Failed to record token usage for agent {agent_id}: {e}")
