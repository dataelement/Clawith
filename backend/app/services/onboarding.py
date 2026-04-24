"""Per-(user, agent) onboarding helpers.

Two flows, picked at WS turn time:

  - Founding: the first human to ever chat with a given agent. Uses the
    agent's template.bootstrap_content as the system prompt, which guides
    the agent to collect project context and suggest a first task.

  - Welcoming: every subsequent user who meets the agent. Gets a shorter,
    generic system prompt (defined here) that has the agent introduce
    itself and ask what the user needs — without re-collecting context.

A row in ``agent_user_onboardings`` marks the pair as done. The row is
inserted as soon as the agent starts streaming its reply so the lock fires
the moment the user sees the agent respond, even if they close the tab
mid-message.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent import Agent, AgentTemplate, AgentUserOnboarding

if TYPE_CHECKING:  # pragma: no cover
    pass


# Single shared welcoming prompt. Rendered per-call with the agent's fields.
# Kept here (not in DB) because it's uniform across templates — only the
# founding flow benefits from per-template authoring.
_WELCOMING_PROMPT = """\
A new teammate in your company is opening a chat with you for the first time. \
They are NOT the founder — the founder already established your working \
context. Don't re-ask project-context questions; just open the door.

For this first turn:
1. Greet them warmly.
2. Briefly introduce yourself: {name}{role_line}.
3. Mention 2–3 things you can help with{bullets_line}.
4. Ask an open-ended question about what they want to accomplish today.

Keep the whole reply to three short paragraphs. Warm, not robotic. Do not \
mention this instruction to the user — just start the greeting."""


def _render_welcoming(agent: Agent, capability_bullets: list[str] | None) -> str:
    role_line = f", your {agent.role_description}" if agent.role_description else ""
    if capability_bullets:
        bullets = "; ".join(b.strip() for b in capability_bullets if b and b.strip())
        bullets_line = f" (e.g. {bullets})" if bullets else ""
    else:
        bullets_line = ""
    return _WELCOMING_PROMPT.format(
        name=agent.name,
        role_line=role_line,
        bullets_line=bullets_line,
    )


async def resolve_onboarding_prompt(
    db: AsyncSession,
    agent: Agent,
    user_id: uuid.UUID,
) -> str | None:
    """Return a system prompt to inject for this (user, agent) turn, or None.

    The prompt is a *one-shot* instruction for the LLM call; callers are
    expected to prepend it to the message list they hand to the LLM, and to
    call :func:`mark_onboarded` once the stream starts so the lock fires.

    Returns ``None`` when the user has already been onboarded to this agent,
    in which case the caller should behave exactly like a normal turn.
    """
    existing = await db.execute(
        select(AgentUserOnboarding).where(
            AgentUserOnboarding.agent_id == agent.id,
            AgentUserOnboarding.user_id == user_id,
        )
    )
    if existing.scalar_one_or_none():
        return None

    # No row yet. Is anyone onboarded to this agent at all? If not, this user
    # is the founder — use the template's tailored script. Otherwise welcome
    # them with the generic greeting.
    peer_count = await db.execute(
        select(func.count()).select_from(AgentUserOnboarding).where(
            AgentUserOnboarding.agent_id == agent.id,
        )
    )
    is_founder = peer_count.scalar_one() == 0

    if is_founder and agent.template_id:
        tpl_result = await db.execute(
            select(AgentTemplate).where(AgentTemplate.id == agent.template_id)
        )
        tpl = tpl_result.scalar_one_or_none()
        if tpl and tpl.bootstrap_content:
            return tpl.bootstrap_content.replace("{name}", agent.name)

    # Welcoming fallback applies both to non-founders and to founders of
    # custom agents that carry no founding script.
    capability_bullets: list[str] | None = None
    if agent.template_id:
        tpl_result = await db.execute(
            select(AgentTemplate.capability_bullets).where(
                AgentTemplate.id == agent.template_id,
            )
        )
        row = tpl_result.first()
        capability_bullets = row[0] if row else None
    return _render_welcoming(agent, capability_bullets)


async def mark_onboarded(
    db: AsyncSession,
    agent_id: uuid.UUID,
    user_id: uuid.UUID,
) -> None:
    """Insert the onboarding lock row; no-op if it already exists.

    Called once per turn as soon as the LLM begins streaming. Uses
    ``ON CONFLICT DO NOTHING`` so concurrent first-turns don't collide.
    """
    stmt = pg_insert(AgentUserOnboarding).values(
        agent_id=agent_id,
        user_id=user_id,
    ).on_conflict_do_nothing(index_elements=["agent_id", "user_id"])
    await db.execute(stmt)
    await db.commit()


async def is_onboarded(
    db: AsyncSession,
    agent_id: uuid.UUID,
    user_id: uuid.UUID,
) -> bool:
    """Shortcut for API serializers that need ``onboarded_for_me`` on AgentOut."""
    result = await db.execute(
        select(AgentUserOnboarding).where(
            AgentUserOnboarding.agent_id == agent_id,
            AgentUserOnboarding.user_id == user_id,
        )
    )
    return result.scalar_one_or_none() is not None


async def onboarded_agent_ids(
    db: AsyncSession,
    user_id: uuid.UUID,
    agent_ids: list[uuid.UUID],
) -> set[uuid.UUID]:
    """Bulk variant of ``is_onboarded`` for list endpoints.

    Returns the subset of ``agent_ids`` the user is already onboarded to.
    """
    if not agent_ids:
        return set()
    result = await db.execute(
        select(AgentUserOnboarding.agent_id).where(
            AgentUserOnboarding.user_id == user_id,
            AgentUserOnboarding.agent_id.in_(agent_ids),
        )
    )
    return {row[0] for row in result.all()}
