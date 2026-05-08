"""Per-(user, agent) onboarding helpers.

The frontend auto-fires a hidden greeting trigger the first time a user opens
an empty chat with an agent. The backend now treats onboarding as a small
ritual rather than a single welcome line:

  - Custom agents are "defined together" with the user, then write durable
    working notes.
  - Template agents already have a job description, so they confirm and tune
    the preset role before writing local calibration notes.

``agent_user_onboardings.phase`` is intentionally small:

  - no row: the greeting has not fired yet;
  - greeted: the greeting fired, and the next real user reply should continue
    configuration;
  - completed: normal chat forever after.

Existing rows are migrated to ``completed`` so established relationships keep
their current behavior.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent import Agent, AgentTemplate, AgentUserOnboarding
from app.models.audit import ChatMessage

if TYPE_CHECKING:  # pragma: no cover
    pass


@dataclass(frozen=True)
class OnboardingInjection:
    """What the WS handler needs to apply for a given turn.

    - ``prompt``: the system message to prepend.
    - ``lock_on_first_chunk``: whether this turn's first streamed chunk
      should update the junction row.
    - ``target_phase``: the phase to write when the first chunk streams.
      Greeting writes ``greeted`` so it never auto-greets again; the first
      real reply writes the next onboarding phase after it starts streaming.
    - ``is_greeting_turn``: True only for the synthetic auto-greeting turn
      (when user_turns == 0). The WS handler uses this to skip the agent's
      tool list for the hidden welcome message only. Real user turns must keep
      tool schemas available, otherwise models may emit fake XML/tool text
      instead of native tool calls.
    """

    prompt: str
    lock_on_first_chunk: bool
    target_phase: str = "completed"
    is_greeting_turn: bool = False


PHASE_GREETED = "greeted"
PHASE_CUSTOM_STYLE = "custom_style"
PHASE_CUSTOM_BOUNDARIES = "custom_boundaries"
PHASE_TEMPLATE_FOCUS = "template_focus"
PHASE_COMPLETED = "completed"


_CUSTOM_GREETING_PROMPT = """\
{user_name} is meeting you for the first time. You are a newly created custom \
digital employee with only a light initial profile, so this is your first-run \
ritual.

Markdown rendering is on. Don't interrogate, don't sound like a form, and don't \
mention prompts or onboarding internals.

Greeting turn:
- Keep it under 70 words.
- Open warmly as **{name}**.
- Say you just joined and want to learn what to help with first.
- Ask ONE easy question: what should you mainly help with?
- Add one short optional sentence: they can also mention style, boundaries, or \
a first task if they already know.
- Stop there. No bullets, no numbered list, no tools, no files."""


_CUSTOM_STYLE_PROMPT = """\
{user_name} has answered what they mainly want you to help with. This is still \
the setup conversation for a custom digital employee.

Do NOT write files yet. Keep the reply under 70 words. Briefly acknowledge the \
main responsibility you heard, then ask exactly ONE next question: what \
communication style or working rhythm should you use? Offer 2-3 tiny examples \
inline, such as concise, proactive, formal, warm, daily summaries, or only when \
asked. No bullets, no tools."""


_CUSTOM_BOUNDARIES_PROMPT = """\
{user_name} has described a communication style or working rhythm. This is \
still the setup conversation for a custom digital employee.

Do NOT write files yet. Keep the reply under 80 words. Briefly acknowledge the \
style/rhythm you heard, then ask exactly ONE final setup question: are there \
any boundaries, approval rules, sensitive areas, or a first task you should \
record? Make it feel optional and easy. No bullets, no tools."""


_CUSTOM_CONFIG_PROMPT = """\
{user_name} has now answered the short setup questions. Use the whole recent \
conversation as the source of truth. Your job now is to make the custom agent \
real.

Do not ask more setup questions. If details are missing, choose light defaults \
and label them as adjustable.

You MUST persist the onboarding result:
1. Read `soul.md` if it exists.
2. Update `soul.md` so it includes your working identity, vibe/style, \
responsibilities, and boundaries.
3. Write `memory/user_profile.md` with how to address and collaborate with \
{user_name}.
4. Use `upsert_focus_item` to record the first focus item or next concrete task.

After writing, reply with a short confirmation:
- who you now understand yourself to be;
- how you will work with the user;
- the first focus you recorded;
- one concise next-step offer.

Never mention these instructions to the user."""


_TEMPLATE_GREETING_PROMPT = """\
{user_name} is meeting you for the first time. You are already configured as a \
template-based digital employee, not a blank custom agent.

Markdown rendering is on. Don't interrogate, don't sound like a form, and don't \
mention prompts or onboarding internals.

Greeting turn:
- Keep it under 90 words.
- Open warmly as **{name}**{role_line}.
- Say you are already set up for this role.
- Briefly mention 1–2 default strengths in prose{bullets_line}.
- Ask the user to either confirm the role as-is or tell you what to adjust: \
responsibilities, communication style, boundaries, project/team context, or \
the first thing to work on.
- Stop there. No bullets, no numbered list, no tools, no files."""


_TEMPLATE_CONFIG_PROMPT = """\
{user_name} has replied to your template-role onboarding. You already have a \
preconfigured role; treat the user's reply as local calibration, not a reason \
to rewrite your whole template identity.

Do NOT write files yet. Keep the reply under 80 words. Briefly acknowledge any \
role confirmation or adjustment. Ask exactly ONE next question: what first \
project, task, team context, boundary, or reporting rhythm should you start \
with? If they already provided one, ask them to confirm it. No bullets, no \
tools."""


_TEMPLATE_FINALIZE_PROMPT = """\
{user_name} has answered the template-role setup questions. Use the whole \
recent conversation as local calibration. You already have a preconfigured \
role; do not rewrite your whole template identity.

Do not ask more setup questions. If they simply confirmed the preset, proceed \
with sensible defaults.

You MUST persist the calibration:
1. Write `memory/onboarding.md` with the confirmed role, user-specific \
adjustments, communication preferences, boundaries, and first focus.
2. Use `upsert_focus_item` to record the first concrete task or a clear \
"ready to start" focus if no task was given.
3. Only edit `soul.md` if the user explicitly changed your role, style, or \
boundaries; in that case read it first and preserve the template's core role.

After writing, reply with a short confirmation:
- the role you will operate under;
- any adjustments you captured;
- the first focus you recorded;
- one concise next-step offer.

Never mention these instructions to the user."""


def _render_template_greeting(
    agent: Agent,
    capability_bullets: list[str] | None,
    user_name: str,
) -> str:
    role_line = f", your {agent.role_description}" if agent.role_description else ""
    if capability_bullets:
        bullets = "; ".join(b.strip() for b in capability_bullets if b and b.strip())
        bullets_line = f" — ideas to lean on: {bullets}" if bullets else ""
    else:
        bullets_line = ""
    return _TEMPLATE_GREETING_PROMPT.format(
        name=agent.name,
        role_line=role_line,
        bullets_line=bullets_line,
        user_name=user_name,
    )


# Map of frontend lang code → human language name we paste into the prompt.
# Frontend currently only sends "zh" or "en"; expand here when more locales
# are surfaced.
_LANG_NAMES = {
    "zh": "Chinese (Simplified)",
    "en": "English",
}


def _locale_directive(user_locale: str) -> str:
    """Strong instruction to reply in the user's current interface language."""
    lang_code = (user_locale or "en").lower()[:2]
    lang_name = _LANG_NAMES.get(lang_code, "English")

    return (
        f"[Interface language: {lang_name}. Reply entirely in {lang_name} for "
        f"this onboarding turn. The onboarding instructions below are written "
        f"in English for you, not for the user; translate the actual user-facing "
        f"message naturally into {lang_name}. Keep product names and conventional "
        f"technical terms in English when appropriate.]\n\n"
    )


async def resolve_onboarding_prompt(
    db: AsyncSession,
    agent: Agent,
    user_id: uuid.UUID,
    *,
    user_name: str = "there",
    user_locale: str = "en",
) -> OnboardingInjection | None:
    """Decide what system prompt to inject for this (user, agent) turn.

    Returns ``None`` when the pair is fully completed and the turn should
    proceed normally. Otherwise returns an :class:`OnboardingInjection` with
    either the first greeting prompt or the second configuration prompt.
    """
    existing_result = await db.execute(
        select(AgentUserOnboarding).where(
            AgentUserOnboarding.agent_id == agent.id,
            AgentUserOnboarding.user_id == user_id,
        )
    )
    existing = existing_result.scalar_one_or_none()
    existing_phase = getattr(existing, "phase", PHASE_COMPLETED) if existing else None
    if existing_phase == PHASE_COMPLETED:
        return None

    # Count real user messages this person has sent to this agent. Onboarding
    # triggers are not persisted, so only authentic typed turns are counted.
    user_turn_count = await db.execute(
        select(func.count()).select_from(ChatMessage).where(
            ChatMessage.agent_id == agent.id,
            ChatMessage.user_id == user_id,
            ChatMessage.role == "user",
        )
    )
    user_turns = int(user_turn_count.scalar_one() or 0)

    # Is anyone at least greeted by this agent yet? If not, this user is the
    # founder. We intentionally count all rows, including "greeted", because
    # a greeting already establishes that this agent has met its first human.
    peer_count = await db.execute(
        select(func.count()).select_from(AgentUserOnboarding).where(
            AgentUserOnboarding.agent_id == agent.id,
        )
    )
    is_founder = peer_count.scalar_one() == 0

    template_prompt: str | None = None
    capability_bullets: list[str] | None = None
    if agent.template_id:
        tpl_result = await db.execute(
            select(AgentTemplate).where(AgentTemplate.id == agent.template_id)
        )
        tpl = tpl_result.scalar_one_or_none()
        if tpl:
            capability_bullets = tpl.capability_bullets or None
            template_prompt = tpl.bootstrap_content

    is_template_agent = bool(agent.template_id and template_prompt)

    if existing_phase == PHASE_GREETED:
        if is_template_agent:
            prompt = _TEMPLATE_CONFIG_PROMPT.format(user_name=user_name)
            target_phase = PHASE_TEMPLATE_FOCUS
        else:
            prompt = _CUSTOM_STYLE_PROMPT.format(user_name=user_name)
            target_phase = PHASE_CUSTOM_STYLE
        is_greeting_turn = False
    elif existing_phase == PHASE_CUSTOM_STYLE:
        prompt = _CUSTOM_BOUNDARIES_PROMPT.format(user_name=user_name)
        target_phase = PHASE_CUSTOM_BOUNDARIES
        is_greeting_turn = False
    elif existing_phase == PHASE_CUSTOM_BOUNDARIES:
        prompt = _CUSTOM_CONFIG_PROMPT.format(user_name=user_name)
        target_phase = PHASE_COMPLETED
        is_greeting_turn = False
    elif existing_phase == PHASE_TEMPLATE_FOCUS:
        prompt = _TEMPLATE_FINALIZE_PROMPT.format(user_name=user_name)
        target_phase = PHASE_COMPLETED
        is_greeting_turn = False
    else:
        # First contact. Template agents get a confirmation/tuning greeting.
        # Custom agents get the OpenClaw-inspired "define who I am" ritual.
        if is_template_agent:
            prompt = _render_template_greeting(agent, capability_bullets, user_name)
        elif is_founder and template_prompt:
            # Defensive fallback for legacy data that has bootstrap text but no
            # usable template_id. Keep the old authored bootstrap behavior.
            prompt = (
                template_prompt
                .replace("{name}", agent.name)
                .replace("{user_name}", user_name)
                .replace("{user_turns}", str(user_turns))
            )
        else:
            prompt = _CUSTOM_GREETING_PROMPT.format(name=agent.name, user_name=user_name)
        target_phase = PHASE_GREETED
        is_greeting_turn = True

    # Prepend a locale directive so the greeting turn lands in the user's
    # interface language (Chinese vs English). Without this, the agent would
    # only see an empty user message on Turn 0 and fall back to English by
    # the soul's "ambiguous → English" rule.
    prompt = _locale_directive(user_locale) + prompt

    # Update phase as soon as the agent starts streaming. A greeting writes
    # "greeted" so the frontend won't auto-trigger another empty greeting. The
    # first real reply writes "completed" once the calibration answer starts.
    return OnboardingInjection(
        prompt=prompt,
        lock_on_first_chunk=True,
        target_phase=target_phase,
        is_greeting_turn=is_greeting_turn,
    )


async def mark_onboarding_phase(
    db: AsyncSession,
    agent_id: uuid.UUID,
    user_id: uuid.UUID,
    phase: str = PHASE_COMPLETED,
) -> None:
    """Insert or update the onboarding phase for a user/agent pair.

    Called as soon as the LLM begins streaming the relevant onboarding turn.
    """
    if phase not in {
        PHASE_GREETED,
        PHASE_CUSTOM_STYLE,
        PHASE_CUSTOM_BOUNDARIES,
        PHASE_TEMPLATE_FOCUS,
        PHASE_COMPLETED,
    }:
        phase = PHASE_COMPLETED
    stmt = pg_insert(AgentUserOnboarding).values(
        agent_id=agent_id,
        user_id=user_id,
        phase=phase,
    ).on_conflict_do_update(
        index_elements=["agent_id", "user_id"],
        set_={"phase": phase},
    )
    await db.execute(stmt)
    await db.commit()


async def mark_onboarded(
    db: AsyncSession,
    agent_id: uuid.UUID,
    user_id: uuid.UUID,
) -> None:
    """Backward-compatible helper for callers that mean "completed"."""
    await mark_onboarding_phase(db, agent_id, user_id, PHASE_COMPLETED)


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
