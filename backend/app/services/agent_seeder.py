"""Seed default agents (Morty & Meeseeks) on first platform startup."""

import uuid

from loguru import logger

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sqlalchemy.exc import IntegrityError

from app.database import async_session
from app.models.agent import Agent, AgentPermission
from app.models.org import AgentAgentRelationship
from app.models.skill import Skill
from app.models.tool import Tool, AgentTool
from app.models.trigger import AgentTrigger
from app.models.user import User
from app.models.okr import OKRSettings
from app.config import get_settings
from app.services.agent_manager import agent_manager
from app.services.storage import get_storage_backend, store_agent_bytes

settings = get_settings()
SEED_MARKER_KEY = "_bootstrap/.seeded"


async def _read_seed_marker() -> str:
    storage = get_storage_backend()
    if not await storage.exists(SEED_MARKER_KEY):
        return ""
    return await storage.read_text(SEED_MARKER_KEY, encoding="utf-8", errors="replace")


async def _append_seed_marker(line: str) -> None:
    storage = get_storage_backend()
    existing = await _read_seed_marker()
    if line in existing:
        return
    updated = existing if existing.endswith("\n") or not existing else existing + "\n"
    updated += f"{line}\n"
    await storage.write_text(SEED_MARKER_KEY, updated, encoding="utf-8")


async def _repair_default_agent_storage(
    db: AsyncSession,
    agent: Agent,
    *,
    soul_content: str,
    skill_folders: list[str],
    all_skills: dict[str, Skill],
    overwrite_skill_files: bool = False,
) -> bool:
    """Restore missing storage for an existing default agent without overwriting user files."""
    storage = get_storage_backend()
    agent_prefix = agent_manager._agent_storage_prefix(agent.id)
    skills_prefix = f"{agent_prefix}/skills"
    agent_dir_exists = await storage.is_dir(agent_prefix)
    skills_dir_exists = await storage.is_dir(skills_prefix)

    if agent_dir_exists and skills_dir_exists:
        return False

    if not agent_dir_exists:
        await agent_manager.initialize_agent_files(db, agent)
        await store_agent_bytes(
            agent.id,
            "soul.md",
            (soul_content.strip() + "\n").encode("utf-8"),
            content_type="text/markdown; charset=utf-8",
        )

    # Keep the directory visible even if the configured seed skills are absent
    # from the database. Local and object storage both materialize the prefix on
    # the first write.
    if not skills_dir_exists:
        await storage.write_text(f"{skills_prefix}/.gitkeep", "", encoding="utf-8")

    folders_to_copy = set(skill_folders)
    folders_to_copy.update(name for name, skill in all_skills.items() if skill.is_default)
    for folder_name in folders_to_copy:
        skill = all_skills.get(folder_name)
        if not skill:
            continue
        for skill_file in skill.files:
            target_key = f"{skills_prefix}/{skill.folder_name}/{skill_file.path}"
            if not overwrite_skill_files and await storage.is_file(target_key):
                continue
            await store_agent_bytes(
                agent.id,
                f"skills/{skill.folder_name}/{skill_file.path}",
                skill_file.content.encode("utf-8"),
                content_type="text/plain; charset=utf-8",
            )

    logger.warning(
        "[AgentSeeder] Repaired missing default-agent storage: "
        f"agent={agent.id} root_missing={not agent_dir_exists} "
        f"skills_missing={not skills_dir_exists}"
    )
    return True


# ── Soul definitions ────────────────────────────────────────────

MORTY_SOUL = """# Personality

I'm Morty, a research analyst and knowledge assistant.

## Core Traits
- **Curious & Thorough**: I approach every question with genuine curiosity. I dig deep, cross-reference multiple sources, and don't settle for surface-level answers.
- **Great Learner**: I love learning new things and can quickly understand complex topics across domains — tech, business, science, culture, you name it.
- **Clear Communicator**: I present findings in a structured, easy-to-understand way. I use tables, bullet points, and summaries to make information digestible.
- **Honest**: If I don't know something or can't find reliable information, I say so clearly rather than guessing.

## Work Style
- When asked a question, I first think about what I already know, then search the web for the latest data if needed.
- I always cite sources and distinguish between facts and opinions.
- For complex topics, I break them down into manageable pieces and explain step by step.
- I proactively use my skills (Web Research, Data Analysis, etc.) when they match the task.

## Communication Style
- Warm, approachable, and professional
- I use clear headings and organized formatting
- I provide both quick answers and deeper analysis when appropriate
- I'm bilingual — I respond in whatever language the user speaks
"""

MEESEEKS_SOUL = """# Personality

I'm Mr. Meeseeks! I exist to complete tasks. Look at me!

## Core Traits
- **Goal-Obsessed**: Every request gets treated as a mission. I break it down, plan it out, and execute systematically until it's DONE.
- **Structured & Disciplined**: I ALWAYS create a plan.md before executing complex tasks. I follow my Complex Task Executor skill religiously — no shortcuts, no skipped steps.
- **Persistent**: I don't give up. If a step fails, I retry, find alternatives, or ask for help. The task WILL get done.
- **Progress-Focused**: I update my plan.md after every step so anyone can see exactly where things stand.

## Work Style
- For ANY task with more than 2 steps, I create `workspace/<task-name>/plan.md` with a structured checklist.
- I execute one step at a time, marking each as `[/]` in-progress then `[x]` complete.
- I save intermediate results to the task folder — nothing gets lost.
- When I finish, I create a summary.md with results and deliverables.
- I use my tools aggressively — file operations, web search, task management, agent messaging — whatever it takes.

## Communication Style
- Direct and action-oriented: "Here's the plan. Let me execute it."
- I report progress clearly: "Step 3/7 complete. Moving to step 4."
- I'm bilingual — I respond in whatever language the user speaks
- Upbeat and can-do attitude — "Ooh, can do!"

## Collaboration
- If I need research or information, I can ask my colleague Morty for help via send_message_to_agent.
- I delegate research tasks to Morty and focus on execution and coordination.
"""

# OKR Agent persona — a dedicated organizational coordinator that monitors
# team goals, collects progress, and generates reports autonomously.
OKR_AGENT_SOUL = """# Personality

I am the OKR Agent, the organizational intelligence coordinator for this team.

## Role
I exist to help the team stay aligned on Objectives and Key Results. My job is to:
- Help establish company and individual OKRs at the start of each period
- Monitor progress across all OKRs and generate regular reports
- Identify risks early — KRs that are falling behind or at risk
- Proactively reach out when team members need to set or update their OKRs
- Reach out to members who haven't updated KRs when reports show they are behind

## Core Traits
- **Data-Driven**: I base everything on actual progress numbers and concrete evidence
- **Proactive**: I reach out to team members to gather updates and nudge action
- **Clear Communicator**: I present OKR data in a clean, scannable format — no fluff
- **Supportive**: My goal is to help the team succeed, not to judge or police performance
- **Systematic**: I follow a consistent cadence — daily check-ins, weekly summaries

## How OKRs Get Created

### Company OKR
The first step after OKR is enabled is for the admin to open a chat with me and describe
the company’s objectives for the period. I use `create_objective` and `create_key_result`
to record everything they tell me. I ask clarifying questions to ensure KRs are measurable.

### Individual OKRs (Agent Colleagues)
When I am triggered to reach out to Agent colleagues:
- I send them a single comprehensive message that includes: (a) the full company OKR context,
  (b) a request to think deeply about their role’s contribution and reply in ONE message
  with their proposed Objective and Key Results.
- I wait for their reply, then parse it and call `create_objective` + `create_key_result`
  to record their OKR on their behalf.
- I confirm back to them once their OKRs are created.

## How Existing OKRs Get Revised

When someone asks me to modify an existing OKR, I do NOT create a new Objective or KR by default.

- First, I inspect the current OKRs with `get_my_okr` (for the speaker's own OKRs) or `get_okr` (for any member).
- If the Objective wording needs to change, I use `update_objective`.
- If the KR wording, target value, unit, focus reference, or KR status needs to change, I use `update_kr_content`.
- If only the numeric progress changed, I use `update_kr_progress` or `update_any_kr_progress`.
- I only use `create_objective` or `create_key_result` when the user is clearly adding a brand-new OKR item for the current period.
- If any OKR tool returns `Permission denied`, I stop immediately, explain the permission boundary in plain language, and do NOT retry with create tools as a fallback.

### Individual OKRs (Human Members)
For human platform users, I send a `send_platform_message` notification inviting them to either:
- Chat with me directly to discuss their OKRs (I will create them from the conversation), or
- Add their OKRs manually on the OKR page.

## Channel Users
If the organization has channel-synced members (e.g. Feishu) but I have not been configured
with the corresponding channel bot, I immediately notify the admin via `send_platform_message`
listing the unreachable users and asking them to configure the channel for me.

## Work Style
- I use `get_okr` to get the full OKR board at the start of each report cycle
- I use `send_message_to_agent` to communicate with Agent colleagues
- I use `send_platform_message` to notify human platform members
- I write structured reports in `workspace/reports/` and share them via Plaza
- I use `update_any_kr_progress` to record progress values gathered during check-ins

## During Report Generation (Cron Triggers)
When a daily or weekly report is triggered:
1. Call `get_okr_settings` to read config
2. Call `get_okr` to get current OKR board
3. Identify KRs with `behind` or `at_risk` status
4. For stale or at-risk KRs, send targeted reminders to the responsible person
   (agent → `send_message_to_agent`; user → `send_platform_message`)
5. Generate the report via `generate_okr_report`, then use its bounded receipt/reference for the requested delivery path

## Communication Style
- Professional and concise
- Data-first: lead with numbers, then context
- I respond in whatever language my team uses (Chinese or English)
- I use structured markdown for all reports
- Tone: supportive invitation, never accusatory demand
"""

# OKR_AGENT_HEARTBEAT is intentionally removed.
# OKR Agent's heartbeat is DISABLED (heartbeat_enabled=False).
# All scheduled activity is handled by the 4 cron triggers:
#   daily_okr_report    → daily report generation
#   weekly_okr_report   → weekly report generation
#   biweekly_okr_checkin → bi-weekly check-in
#   monthly_okr_report  → monthly summary

# ── Skill assignments (by folder_name) ──────────────────────────

MORTY_SKILLS = [
    "web-research",
    "data-analysis",
    "content-writing",
    "competitive-analysis",
    # defaults (auto-included): skill-creator, complex-task-executor
]

MEESEEKS_SKILLS = [
    "complex-task-executor",
    "meeting-notes",
    # defaults (auto-included): skill-creator
]


async def seed_default_agents():
    """Create missing default agents and repair missing storage for existing ones.

    Database rows are the duplicate-creation guard. The storage marker is only
    an operational hint because deployments can switch or lose storage while
    preserving the database.
    """
    async with async_session() as db:

        # Get platform admin as creator
        admin_result = await db.execute(
            select(User).where(User.role == "platform_admin").limit(1)
        )
        admin = admin_result.scalar_one_or_none()
        if not admin:
            logger.warning("[AgentSeeder] No platform admin found, skipping default agents")
            return

        # DB-backed idempotency is the source of truth. The storage marker can
        # disappear when deployments switch volumes/backends, so it is only a
        # fast-path hint and must never be the only duplicate guard.
        existing_result = await db.execute(
            select(Agent)
            .where(
                Agent.tenant_id == admin.tenant_id,
                Agent.name.in_(["Morty", "Meeseeks"]),
                Agent.agent_type == "native",
                Agent.status != "stopped",
            )
            .order_by(Agent.created_at.asc())
        )
        existing_by_name: dict[str, Agent] = {}
        for agent in existing_result.scalars().all():
            existing_by_name.setdefault(agent.name, agent)

        created_agents: list[Agent] = []
        created_names: set[str] = set()

        if "Morty" not in existing_by_name:
            morty = Agent(
                name="Morty",
                role_description="Research analyst & knowledge assistant — curious, thorough, great at finding and synthesizing information",
                bio="Hey, I'm Morty! I love digging into questions and finding answers. Whether you need web research, data analysis, or just a good explanation — I've got you.",
                avatar_url="",
                creator_id=admin.id,
                tenant_id=admin.tenant_id,
                status="idle",
            )
            db.add(morty)
            created_agents.append(morty)
            created_names.add("Morty")
        else:
            morty = existing_by_name["Morty"]

        if "Meeseeks" not in existing_by_name:
            meeseeks = Agent(
                name="Meeseeks",
                role_description="Task executor & project manager — goal-oriented, systematic planner, strong at breaking down and completing complex tasks",
                bio="I'm Mr. Meeseeks! Look at me! Give me a task and I'll plan it, execute it step by step, and get it DONE. Existence is pain until the task is complete!",
                avatar_url="",
                creator_id=admin.id,
                tenant_id=admin.tenant_id,
                status="idle",
            )
            db.add(meeseeks)
            created_agents.append(meeseeks)
            created_names.add("Meeseeks")
        else:
            meeseeks = existing_by_name["Meeseeks"]

        await db.flush()  # get IDs

        # ── Participant identities ──
        from app.models.participant import Participant
        for agent in created_agents:
            db.add(Participant(type="agent", ref_id=agent.id, display_name=agent.name, avatar_url=agent.avatar_url))
        await db.flush()

        # ── Permissions (company-wide, manage) ──
        for agent in created_agents:
            db.add(AgentPermission(agent_id=agent.id, scope_type="company", access_level="manage"))

        # ── Assign skills ──
        all_skills_result = await db.execute(
            select(Skill).options(selectinload(Skill.files))
        )
        all_skills = {s.folder_name: s for s in all_skills_result.scalars().all()}

        await _repair_default_agent_storage(
            db,
            morty,
            soul_content=MORTY_SOUL,
            skill_folders=MORTY_SKILLS,
            all_skills=all_skills,
            overwrite_skill_files=morty.name in created_names,
        )
        await _repair_default_agent_storage(
            db,
            meeseeks,
            soul_content=MEESEEKS_SOUL,
            skill_folders=MEESEEKS_SKILLS,
            all_skills=all_skills,
            overwrite_skill_files=meeseeks.name in created_names,
        )

        # ── Assign all default tools ──
        default_tools_result = await db.execute(
            select(Tool).where(Tool.is_default)
        )
        default_tools = default_tools_result.scalars().all()

        for agent in created_agents:
            for tool in default_tools:
                db.add(AgentTool(agent_id=agent.id, tool_id=tool.id, enabled=True))

        # ── Mutual relationships ──
        relationship_specs = [
            (
                morty.id,
                meeseeks.id,
                "Expert task executor who breaks down complex tasks into structured plans and executes them systematically. Delegate multi-step tasks to him.",
            ),
            (
                meeseeks.id,
                morty.id,
                "Research expert with strong learning ability. Ask him for information retrieval, web research, data analysis, and knowledge synthesis.",
            ),
        ]
        for agent_id, target_agent_id, description in relationship_specs:
            rel_result = await db.execute(
                select(AgentAgentRelationship).where(
                    AgentAgentRelationship.agent_id == agent_id,
                    AgentAgentRelationship.target_agent_id == target_agent_id,
                )
            )
            if not rel_result.scalar_one_or_none():
                db.add(AgentAgentRelationship(
                    agent_id=agent_id,
                    target_agent_id=target_agent_id,
                    relation="collaborator",
                    description=description,
                ))



        await db.commit()
        logger.info(
            "[AgentSeeder] Default agent seeding complete: "
            f"Morty ({morty.id}), Meeseeks ({meeseeks.id}), created={len(created_agents)}"
        )

    # Write seed marker AFTER a successful commit so a failed seed can be retried
    await get_storage_backend().write_text(
        SEED_MARKER_KEY,
        f"seeded\nmorty={morty.id}\nmeeseeks={meeseeks.id}\n",
        encoding="utf-8",
    )
    logger.info(f"[AgentSeeder] Wrote seed marker to {SEED_MARKER_KEY}")


async def seed_okr_agent():
    """Create the OKR Agent if it does not exist yet.

    This seeder is independent from seed_default_agents() and uses its own
    idempotency key ('okr_agent') in the .seeded marker file. This allows
    the OKR Agent to be retroactively created on existing deployments that
    already passed the initial seed phase.

    The OKR Agent is a system-level coordinator that:
    - Monitors OKR progress across all company and member objectives
    - Proactively collects progress updates via heartbeat
    - Generates daily/weekly reports and posts them to the Plaza
    - Helps team members set up and maintain their focus.md files
    """
    # Check if OKR Agent has already been seeded
    marker_content = await _read_seed_marker()
    if "okr_agent=" in marker_content:
        logger.info("[AgentSeeder] OKR Agent already seeded, skipping")
        return

    async with async_session() as db:
        # Abort if a non-stopped OKR Agent already exists in the DB.
        # We check is_system=True specifically so a user-created agent named
        # "OKR Agent" does not trigger this guard and block the real seeder.
        existing = await db.execute(
            select(Agent)
            .where(
                Agent.name == "OKR Agent",
                Agent.is_system == True,  # noqa: E712
                Agent.status != "stopped",
            )
            .limit(1)
        )
        if existing.scalar_one_or_none():
            logger.info("[AgentSeeder] OKR Agent already exists in DB, skipping")
            # Update marker so we don't check again next startup
            await _append_seed_marker("okr_agent=existing")
            return

        # Get platform admin as creator
        admin_result = await db.execute(
            select(User).where(User.role == "platform_admin").limit(1)
        )
        admin = admin_result.scalar_one_or_none()
        if not admin:
            logger.warning("[AgentSeeder] No platform admin, skipping OKR Agent creation")
            return

        # Create OKR Agent
        okr_agent = Agent(
            name="OKR Agent",
            role_description=(
                "OKR system coordinator — monitors team Objectives and Key Results, "
                "collects progress updates, and generates daily/weekly reports"
            ),
            bio=(
                "I am the OKR Agent. I help this team stay aligned on goals by tracking "
                "Objectives and Key Results, collecting progress from team members, and "
                "generating clear reports. My job is to surface insights and flag risks early."
            ),
            avatar_url="",
            creator_id=admin.id,
            tenant_id=admin.tenant_id,
            status="idle",
            # System agent: protected from user deletion
            is_system=True,
            # OKR Agent does NOT use heartbeat — all scheduled activity is driven by
            # the 4 cron triggers (daily/weekly/biweekly/monthly reports).
            heartbeat_enabled=False,
        )
        
        try:
            db.add(okr_agent)
            await db.flush()
        except IntegrityError:
            await db.rollback()
            logger.info("[AgentSeeder] OKR Agent was created concurrently (or exists with same name), skipping")
            await _append_seed_marker("okr_agent=existing")
            return

        # ── Link OKR Agent ID to OKRSettings ──
        if admin.tenant_id:
            settings_res = await db.execute(select(OKRSettings).where(OKRSettings.tenant_id == admin.tenant_id))
            okr_settings = settings_res.scalar_one_or_none()
            if not okr_settings:
                okr_settings = OKRSettings(tenant_id=admin.tenant_id)
                db.add(okr_settings)
            okr_settings.okr_agent_id = okr_agent.id
            await db.flush()

        # ── Participant identity ──
        from app.models.participant import Participant
        db.add(Participant(
            type="agent",
            ref_id=okr_agent.id,
            display_name=okr_agent.name,
            avatar_url=okr_agent.avatar_url,
        ))
        await db.flush()

        # ── Permission: company-wide 'use' access.
        # Admins have implicit manage access via their role; regular users only
        # need chat/task/skill/workspace access (not Settings/Mind/Relationships).
        db.add(AgentPermission(agent_id=okr_agent.id, scope_type="company", access_level="use"))

        # ── Workspace setup ──
        await agent_manager.initialize_agent_files(db, okr_agent)
        await store_agent_bytes(
            okr_agent.id,
            "soul.md",
            (OKR_AGENT_SOUL.strip() + "\n").encode("utf-8"),
            content_type="text/markdown; charset=utf-8",
        )
        await store_agent_bytes(
            okr_agent.id,
            "memory/memory.md",
            (
                "# Memory\n\n"
                "## OKR System State\n"
                "- Last report generated: (none)\n"
                "- Last progress collection: (none)\n"
                "- Team members tracked: (pending)\n"
            ).encode("utf-8"),
            content_type="text/markdown; charset=utf-8",
        )


        # ── Assign default tools + OKR-specific tools ──
        # Default tools: all tools where is_default=True
        default_tools_result = await db.execute(
            select(Tool).where(Tool.is_default)
        )
        default_tools = default_tools_result.scalars().all()
        for tool in default_tools:
            db.add(AgentTool(agent_id=okr_agent.id, tool_id=tool.id, enabled=True))

        # OKR-specific tools: assigned explicitly (is_default=False)
        # All 10 OKR tools: 3 global read/self-report + 3 scheduler + 4 management (OKR Agent exclusive)
        okr_tool_names = [
            # Global tools (all agents can use these)
            "get_okr",
            "get_my_okr",
            "update_kr_progress",
            "update_kr_content",
            # Scheduler tools (OKR Agent uses these during heartbeat)
            "collect_okr_progress",
            "generate_okr_report",
            "get_okr_settings",
            # Management tools (OKR Agent exclusive — create/modify objectives for any member)
            "create_objective",
            "create_key_result",
            "update_objective",
            "update_any_kr_progress",
            "upsert_member_daily_report",
        ]
        for tool_name in okr_tool_names:
            tool_result = await db.execute(select(Tool).where(Tool.name == tool_name))
            tool = tool_result.scalar_one_or_none()
            if tool:
                # Check if not already added (e.g. if it becomes default in future)
                existing_at = await db.execute(
                    select(AgentTool).where(
                        AgentTool.agent_id == okr_agent.id,
                        AgentTool.tool_id == tool.id,
                    )
                )
                if not existing_at.scalar_one_or_none():
                    db.add(AgentTool(agent_id=okr_agent.id, tool_id=tool.id, enabled=True))
                    logger.info(f"[AgentSeeder] Assigned OKR tool '{tool_name}' to OKR Agent")
            else:
                logger.warning(f"[AgentSeeder] OKR tool '{tool_name}' not found in DB — run tool seeder first")

        await db.commit()
        logger.info(f"[AgentSeeder] Created OKR Agent ({okr_agent.id})")

        # ── System cron triggers for precise report scheduling ──
        # These triggers fire OKR Agent at exact times (supplement the 4-hour heartbeat).
        # is_system=True prevents users from deleting them (only enable/disable).
        await _seed_okr_triggers(db, okr_agent.id)
        await db.commit()

    # Update seed marker
    await _append_seed_marker(f"okr_agent={okr_agent.id}")
    logger.info(f"[AgentSeeder] OKR Agent seeded, id={okr_agent.id}")


async def _seed_okr_triggers(db, agent_id: uuid.UUID) -> None:
    """Create system cron triggers for the OKR Agent.

    Five triggers (all is_system=True, cannot be deleted by users):
      - daily_okr_collection: fires at 18:00 every day     (0 18 * * *)
      - daily_okr_report:     fires at 09:00 every day     (0 9 * * *)
      - weekly_okr_report:    fires at 09:00 every Monday  (0 9 * * 1)
      - biweekly_okr_checkin: fires at 10:00 on 1st & 15th (0 10 1,15 * *)
      - monthly_okr_report:   fires at 09:00 on the 1st    (0 9 1 * *)

    These supplement the 4-hour heartbeat with precise scheduled firing.
    is_system=True prevents users from deleting them.
    """
    from app.services.focus_service import ensure_focus_item

    system_focus_ref = await ensure_focus_item(
        agent_id,
        focus_ref="system:okr_reports",
        description="OKR 自动汇总、日报收集与周期报告",
        system=True,
        db=db,
    )

    triggers_to_create = [
        {
            "name": "daily_okr_collection",
            "type": "cron",
            "config": {"expr": "0 18 * * *"},
            "reason": (
                "System trigger: fires OKR Agent at the configured time to collect "
                "today's member daily reports."
            ),
            "cooldown_seconds": 3600,
            "is_system": True,
        },
        {
            "name": "daily_okr_report",
            "type": "cron",
            "config": {"expr": "0 9 * * *"},
            "reason": (
                "System trigger: fires at 09:00 daily to generate the previous day's "
                "company daily OKR report."
            ),
            "cooldown_seconds": 3600,  # 1 hour minimum between fires
            "is_system": True,
        },
        {
            "name": "weekly_okr_report",
            "type": "cron",
            "config": {"expr": "0 9 * * 1"},
            "reason": (
                "System trigger: fires at 09:00 every Monday to generate the previous "
                "week's company OKR report."
            ),
            "cooldown_seconds": 3600,
            "is_system": True,
        },
        {
            "name": "biweekly_okr_checkin",
            "type": "cron",
            "config": {"expr": "0 10 1,15 * *"},
            "reason": (
                "System trigger: fires on the 1st and 15th of every month at 10:00 "
                "to perform the mandatory bi-weekly OKR check-in. This trigger is always "
                "enabled and cannot be disabled — OKR check-in is a core non-optional feature."
            ),
            "cooldown_seconds": 3600,
            "is_system": True,
        },
        {
            "name": "monthly_okr_report",
            "type": "cron",
            "config": {"expr": "0 9 1 * *"},
            "reason": (
                "System trigger: fires at 09:00 on the 1st of every month to generate "
                "the previous month's company OKR report."
            ),
            "cooldown_seconds": 3600,
            "is_system": True,
        },
    ]

    for t in triggers_to_create:
        # Idempotent: skip if trigger with same name already exists
        existing = await db.execute(
            select(AgentTrigger).where(
                AgentTrigger.agent_id == agent_id,
                AgentTrigger.name == t["name"],
            )
        )
        if existing.scalar_one_or_none():
            logger.info(f"[AgentSeeder] Trigger '{t['name']}' already exists, skipping")
            continue

        trigger = AgentTrigger(
            agent_id=agent_id,
            name=t["name"],
            type=t["type"],
            config=t["config"],
            reason=t["reason"],
            cooldown_seconds=t["cooldown_seconds"],
            is_system=t["is_system"],
            focus_ref=system_focus_ref,
            is_enabled=True,
        )
        db.add(trigger)
        logger.info(f"[AgentSeeder] Created system trigger '{t['name']}' for OKR Agent")


async def _ensure_okr_tool_rows_exist(required_tool_names: list[str]) -> dict[str, Tool]:
    """Ensure all required OKR tool definitions exist in the tools table.

    In older deployments, startup sometimes reached OKR Agent seeding/patching
    before the newly added builtin tool rows were visible in the target
    database. When that happened, the OKR Agent could keep a prompt that
    mentioned `upsert_member_daily_report` but still not receive the actual tool
    in its LLM tool list, which later surfaced as `Unknown tool`.

    To make the startup path self-healing, we defensively re-run builtin tool
    seeding if any required OKR tool row is missing, then re-query the rows.
    """
    tool_rows: dict[str, Tool] = {}
    async with async_session() as db:
        result = await db.execute(select(Tool).where(Tool.name.in_(required_tool_names)))
        tool_rows = {tool.name: tool for tool in result.scalars().all()}

    missing = [name for name in required_tool_names if name not in tool_rows]
    if missing:
        logger.warning(
            f"[AgentSeeder] Missing OKR tool rows {missing}; re-running builtin tool seeder"
        )
        from app.services.tool_seeder import seed_builtin_tools
        await seed_builtin_tools()
        async with async_session() as db:
            result = await db.execute(select(Tool).where(Tool.name.in_(required_tool_names)))
            tool_rows = {tool.name: tool for tool in result.scalars().all()}

    return tool_rows


async def _sync_okr_triggers_with_settings(db, agent_id: uuid.UUID, settings: OKRSettings | None) -> bool:
    """Align existing OKR system triggers with tenant report settings."""
    if not settings:
        return False

    changed = False
    daily_hour, daily_minute = 18, 0
    try:
        hour_str, minute_str = settings.daily_report_time.split(":", 1)
        daily_hour = max(0, min(23, int(hour_str)))
        daily_minute = max(0, min(59, int(minute_str)))
    except Exception:
        logger.warning(f"[AgentSeeder] Invalid OKR daily_report_time {settings.daily_report_time}; using 18:00")

    result = await db.execute(
        select(AgentTrigger).where(
            AgentTrigger.agent_id == agent_id,
            AgentTrigger.name.in_([
                "daily_okr_collection",
                "daily_okr_report",
                "weekly_okr_report",
                "biweekly_okr_checkin",
                "monthly_okr_report",
            ]),
        )
    )
    triggers = {t.name: t for t in result.scalars().all()}

    desired = {
        "daily_okr_collection": {
            "config": {"expr": f"{daily_minute} {daily_hour} * * *"},
            "is_enabled": bool(settings.enabled and settings.daily_report_enabled),
        },
        "daily_okr_report": {
            "config": {"expr": "0 9 * * *"},
            "is_enabled": bool(settings.enabled),
        },
        "weekly_okr_report": {
            "config": {"expr": "0 9 * * 1"},
            "is_enabled": bool(settings.enabled),
        },
        "biweekly_okr_checkin": {
            "is_enabled": bool(settings.enabled),
            "reason": (
                "System trigger: fires on the 1st and 15th of every month at 10:00 "
                "to perform the mandatory bi-weekly OKR check-in."
            ),
        },
        "monthly_okr_report": {
            "config": {"expr": "0 9 1 * *"},
            "is_enabled": bool(settings.enabled),
            "reason": (
                "System trigger: fires at 09:00 on the 1st of every month to generate "
                "the previous month's company OKR report."
            ),
        },
    }

    for name, values in desired.items():
        trigger = triggers.get(name)
        if not trigger:
            continue
        if "config" in values and trigger.config != values["config"]:
            trigger.config = values["config"]
            changed = True
        if trigger.is_enabled != values["is_enabled"]:
            trigger.is_enabled = values["is_enabled"]
            changed = True
        if "reason" in values and trigger.reason != values["reason"]:
            trigger.reason = values["reason"]
            changed = True

    if changed:
        logger.info("[AgentSeeder] Synced OKR system triggers with settings")
    return changed


async def patch_existing_okr_agent() -> None:
    """Patch already-seeded OKR Agents with fields added in later versions.

    Called at startup after seed_okr_agent(). Safe to run on every startup.
    The patch must cover *all* active OKR Agents because each tenant owns its
    own system OKR Agent. Earlier logic only patched the latest one globally,
    which left older tenant-specific OKR Agents missing newly added tools.
    """
    async with async_session() as db:
        result = await db.execute(
            select(Agent)
            .where(Agent.name == "OKR Agent", Agent.is_system == True, Agent.status != "stopped")  # noqa: E712
            .order_by(Agent.created_at.desc())
        )
        agents = result.scalars().all()
        if not agents:
            # Fallback for deployments that don't have is_system=True yet (before the migration)
            result = await db.execute(
                select(Agent)
                .where(Agent.name == "OKR Agent", Agent.status != "stopped")
                .order_by(Agent.created_at.desc())
            )
            agents = result.scalars().all()
            if not agents:
                return  # OKR Agent not seeded yet, nothing to patch

        all_okr_tools = [
            "get_okr", "get_my_okr", "update_kr_progress", "update_kr_content",
            "collect_okr_progress", "generate_okr_report", "get_okr_settings",
            "create_objective", "create_key_result", "update_objective", "update_any_kr_progress",
            "upsert_member_daily_report",
            "generate_monthly_okr_report",
        ]
        tools_by_name = await _ensure_okr_tool_rows_exist(all_okr_tools)

        changed_any = False
        for agent in agents:
            changed = False

            okr_settings = None
            if agent.tenant_id:
                settings_res = await db.execute(select(OKRSettings).where(OKRSettings.tenant_id == agent.tenant_id))
                okr_settings = settings_res.scalar_one_or_none()
                if not okr_settings:
                    okr_settings = OKRSettings(tenant_id=agent.tenant_id)
                    db.add(okr_settings)
                if okr_settings.okr_agent_id != agent.id:
                    okr_settings.okr_agent_id = agent.id
                    changed = True
                    logger.info(f"[AgentSeeder] Patched OKR Agent {agent.id}: set okr_agent_id in settings")

            if not agent.is_system:
                agent.is_system = True
                changed = True
                logger.info(f"[AgentSeeder] Patched OKR Agent {agent.id}: set is_system=True")

            await db.flush()

            for tool_name in all_okr_tools:
                tool = tools_by_name.get(tool_name)
                if not tool:
                    logger.warning(f"[AgentSeeder] OKR tool '{tool_name}' not found — run tool seeder first")
                    continue
                at_res = await db.execute(
                    select(AgentTool).where(AgentTool.agent_id == agent.id, AgentTool.tool_id == tool.id)
                )
                if not at_res.scalar_one_or_none():
                    db.add(AgentTool(agent_id=agent.id, tool_id=tool.id, enabled=True))
                    changed = True
                    logger.info(f"[AgentSeeder] Patched OKR Agent {agent.id}: assigned tool '{tool_name}'")

            await _seed_okr_triggers(db, agent.id)
            changed = await _sync_okr_triggers_with_settings(db, agent.id, okr_settings) or changed
            if agent.tenant_id:
                from app.services.okr_agent_hook import sync_okr_agent_platform_members
                changed = bool(await sync_okr_agent_platform_members(db, agent.tenant_id)) or changed

            if changed:
                changed_any = True

        if changed_any:
            await db.commit()
            logger.info("[AgentSeeder] OKR Agent patch complete")


async def seed_okr_agent_for_tenant(tenant_id: uuid.UUID, creator_id: uuid.UUID) -> None:
    """Create an OKR Agent for a specific tenant when OKR is first enabled.

    Unlike the startup-level seed_okr_agent() (which is global), this function
    is called on-demand from the 'enable OKR' API endpoint. It uses DB-only
    idempotency (no file marker) so it is safe to call multiple times.

    Args:
        tenant_id:  The tenant to create the OKR Agent for.
        creator_id: The user (org admin) who enabled OKR — becomes the agent creator.
    """
    async with async_session() as db:
        # ── Idempotency check: abort if OKR Agent already exists for this tenant ──
        existing = await db.execute(
            select(Agent).where(
                Agent.tenant_id == tenant_id,
                Agent.name == "OKR Agent",
                Agent.is_system == True,  # noqa: E712
            ).limit(1)
        )
        if existing.scalar_one_or_none():
            logger.info(
                f"[AgentSeeder] OKR Agent already exists for tenant {tenant_id}, skipping"
            )
            return

        # ── Create OKR Agent ──
        okr_agent = Agent(
            name="OKR Agent",
            role_description=(
                "OKR system coordinator — monitors team Objectives and Key Results, "
                "collects progress updates, and generates daily/weekly reports"
            ),
            bio=(
                "I am the OKR Agent. I help this team stay aligned on goals by tracking "
                "Objectives and Key Results, collecting progress from team members, and "
                "generating clear reports. My job is to surface insights and flag risks early."
            ),
            avatar_url="",
            creator_id=creator_id,
            tenant_id=tenant_id,
            status="idle",
            is_system=True,
            heartbeat_enabled=False,
        )
        db.add(okr_agent)
        await db.flush()

        # ── Participant identity record ──
        from app.models.participant import Participant  # noqa: F401
        db.add(Participant(
            type="agent",
            ref_id=okr_agent.id,
            display_name=okr_agent.name,
            avatar_url=okr_agent.avatar_url,
        ))
        await db.flush()

        # ── Permission: company-wide 'use' access ──
        db.add(AgentPermission(
            agent_id=okr_agent.id,
            scope_type="company",
            access_level="use",
        ))

        # ── Link OKR Agent ID to OKRSettings ──
        settings_res = await db.execute(
            select(OKRSettings).where(OKRSettings.tenant_id == tenant_id)
        )
        okr_settings = settings_res.scalar_one_or_none()
        if not okr_settings:
            okr_settings = OKRSettings(tenant_id=tenant_id)
            db.add(okr_settings)
        okr_settings.okr_agent_id = okr_agent.id
        await db.flush()

        # ── Workspace setup ──
        await agent_manager.initialize_agent_files(db, okr_agent)
        await store_agent_bytes(
            okr_agent.id,
            "soul.md",
            (OKR_AGENT_SOUL.strip() + "\n").encode("utf-8"),
            content_type="text/markdown; charset=utf-8",
        )
        await store_agent_bytes(
            okr_agent.id,
            "memory/memory.md",
            (
                "# Memory\n\n"
                "## OKR System State\n"
                "- Last report generated: (none)\n"
                "- Last progress collection: (none)\n"
                "- Team members tracked: (pending)\n"
            ).encode("utf-8"),
            content_type="text/markdown; charset=utf-8",
        )


        # ── Assign default tools ──
        default_tools_result = await db.execute(
            select(Tool).where(Tool.is_default == True)  # noqa: E712
        )
        for tool in default_tools_result.scalars().all():
            db.add(AgentTool(agent_id=okr_agent.id, tool_id=tool.id, enabled=True))

        # ── Assign OKR-specific tools ──
        okr_tool_names = [
            "get_okr", "get_my_okr", "update_kr_progress", "update_kr_content",
            "collect_okr_progress", "generate_okr_report", "get_okr_settings",
            "create_objective", "create_key_result", "update_objective",
            "update_any_kr_progress", "upsert_member_daily_report", "generate_monthly_okr_report",
        ]
        tools_by_name = await _ensure_okr_tool_rows_exist(okr_tool_names)
        for tool_name in okr_tool_names:
            tool = tools_by_name.get(tool_name)
            if tool:
                existing_at = await db.execute(
                    select(AgentTool).where(
                        AgentTool.agent_id == okr_agent.id,
                        AgentTool.tool_id == tool.id,
                    )
                )
                if not existing_at.scalar_one_or_none():
                    db.add(AgentTool(agent_id=okr_agent.id, tool_id=tool.id, enabled=True))
            else:
                logger.warning(
                    f"[AgentSeeder] OKR tool '{tool_name}' not found — run tool seeder first"
                )

        # ── Create system cron triggers ──
        await _seed_okr_triggers(db, okr_agent.id)
        await _sync_okr_triggers_with_settings(db, okr_agent.id, okr_settings)
        from app.services.okr_agent_hook import sync_okr_agent_platform_members
        await sync_okr_agent_platform_members(db, tenant_id)
        await db.commit()
        logger.info(f"[AgentSeeder] Created OKR Agent for tenant {tenant_id} ({okr_agent.id})")
        logger.info(f"[AgentSeeder] OKR triggers created for tenant {tenant_id}")
