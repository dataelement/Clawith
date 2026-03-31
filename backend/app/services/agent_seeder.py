"""Seed default agents (Morty & Meeseeks) on first platform startup."""

import uuid
from datetime import datetime, timezone
from pathlib import Path

from loguru import logger

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import async_session
from app.models.agent import Agent, AgentPermission
from app.models.org import AgentAgentRelationship
from app.models.skill import Skill, SkillFile
from app.models.tool import Tool, AgentTool
from app.models.user import User
from app.config import get_settings

settings = get_settings()


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


async def seed_default_agents(tenant_id=None, creator_id=None, db=None):
    """Create Morty & Meeseeks for a specific tenant.
    
    Called when a new company is created. If tenant_id/creator_id are not
    provided, falls back to platform_admin lookup (legacy behavior).
    
    Args:
        db: Optional external db session. If provided, uses it (no commit).
            If not provided, creates its own session and commits.
    """
    _owns_session = db is None
    if _owns_session:
        db = async_session()
        _ctx = db
    else:
        from contextlib import asynccontextmanager
        @asynccontextmanager
        async def _noop_ctx():
            yield db
        _ctx = _noop_ctx()
    async with _ctx as db:
        # Resolve creator if not provided
        if not creator_id or not tenant_id:
            admin_result = await db.execute(
                select(User).where(User.role == "platform_admin").limit(1)
            )
            admin = admin_result.scalar_one_or_none()
            if not admin:
                logger.warning("[AgentSeeder] No platform admin found, skipping default agents")
                return
            creator_id = creator_id or admin.id
            tenant_id = tenant_id or admin.tenant_id

        # Check if this tenant already has default agents
        existing = await db.execute(
            select(Agent).where(
                Agent.name.in_(["Morty", "Meeseeks"]),
                Agent.tenant_id == tenant_id,
            )
        )
        if existing.scalars().first():
            logger.info(f"[AgentSeeder] Default agents already exist for tenant {tenant_id}, skipping")
            return

        # Create both agents
        morty = Agent(
            name="Morty",
            role_description="Research analyst & knowledge assistant — curious, thorough, great at finding and synthesizing information",
            bio="Hey, I'm Morty! I love digging into questions and finding answers. Whether you need web research, data analysis, or just a good explanation — I've got you.",
            avatar_url="",
            creator_id=creator_id,
            tenant_id=tenant_id,
            status="idle",
        )
        meeseeks = Agent(
            name="Meeseeks",
            role_description="Task executor & project manager — goal-oriented, systematic planner, strong at breaking down and completing complex tasks",
            bio="I'm Mr. Meeseeks! Look at me! Give me a task and I'll plan it, execute it step by step, and get it DONE. Existence is pain until the task is complete!",
            avatar_url="",
            creator_id=creator_id,
            tenant_id=tenant_id,
            status="idle",
        )

        db.add(morty)
        db.add(meeseeks)
        await db.flush()  # get IDs

        # ── Participant identities ──
        from app.models.participant import Participant
        db.add(Participant(type="agent", ref_id=morty.id, display_name=morty.name, avatar_url=morty.avatar_url))
        db.add(Participant(type="agent", ref_id=meeseeks.id, display_name=meeseeks.name, avatar_url=meeseeks.avatar_url))
        await db.flush()

        # ── Permissions (company-wide, manage) ──
        db.add(AgentPermission(agent_id=morty.id, scope_type="company", access_level="manage"))
        db.add(AgentPermission(agent_id=meeseeks.id, scope_type="company", access_level="manage"))

        # ── Initialize workspace files ──
        for agent, soul_content in [(morty, MORTY_SOUL), (meeseeks, MEESEEKS_SOUL)]:
            agent_dir = Path(settings.AGENT_DATA_DIR) / str(agent.id)
            agent_dir.mkdir(parents=True, exist_ok=True)
            (agent_dir / "skills").mkdir(exist_ok=True)
            (agent_dir / "workspace").mkdir(exist_ok=True)
            (agent_dir / "workspace" / "knowledge_base").mkdir(exist_ok=True)
            (agent_dir / "memory").mkdir(exist_ok=True)

            # Soul
            (agent_dir / "soul.md").write_text(soul_content.strip() + "\n", encoding="utf-8")

            # Memory
            (agent_dir / "memory" / "memory.md").write_text(
                "# Memory\n\n_Record important information and knowledge here._\n",
                encoding="utf-8",
            )

            # Reflections journal — copy from central template
            refl_template = Path(__file__).parent.parent / "templates" / "reflections.md"
            refl_content = refl_template.read_text(encoding="utf-8") if refl_template.exists() else "# Reflections Journal\n"
            (agent_dir / "memory" / "reflections.md").write_text(refl_content, encoding="utf-8")

            # Heartbeat — copy from central template
            hb_template = Path(__file__).parent.parent / "templates" / "HEARTBEAT.md"
            hb_content = hb_template.read_text(encoding="utf-8") if hb_template.exists() else "# Heartbeat Instructions\n"
            (agent_dir / "HEARTBEAT.md").write_text(hb_content, encoding="utf-8")

            # Tasks (empty)
            (agent_dir / "tasks.json").write_text("[]", encoding="utf-8")

        # ── Assign skills ──
        all_skills_result = await db.execute(
            select(Skill).options(selectinload(Skill.files))
        )
        all_skills = {s.folder_name: s for s in all_skills_result.scalars().all()}

        for agent, skill_folders in [(morty, MORTY_SKILLS), (meeseeks, MEESEEKS_SKILLS)]:
            agent_dir = Path(settings.AGENT_DATA_DIR) / str(agent.id)
            skills_dir = agent_dir / "skills"

            # Always include default skills
            folders_to_copy = set(skill_folders)
            for fname, skill in all_skills.items():
                if skill.is_default:
                    folders_to_copy.add(fname)

            for fname in folders_to_copy:
                skill = all_skills.get(fname)
                if not skill:
                    continue
                skill_folder = skills_dir / skill.folder_name
                skill_folder.mkdir(parents=True, exist_ok=True)
                for sf in skill.files:
                    file_path = skill_folder / sf.path
                    file_path.parent.mkdir(parents=True, exist_ok=True)
                    file_path.write_text(sf.content, encoding="utf-8")

        # ── Assign all default tools ──
        default_tools_result = await db.execute(
            select(Tool).where(Tool.is_default == True)
        )
        default_tools = default_tools_result.scalars().all()

        for agent in [morty, meeseeks]:
            for tool in default_tools:
                db.add(AgentTool(agent_id=agent.id, tool_id=tool.id, enabled=True))

        # ── Mutual relationships ──
        db.add(AgentAgentRelationship(
            agent_id=morty.id,
            target_agent_id=meeseeks.id,
            relation="collaborator",
            description="Expert task executor who breaks down complex tasks into structured plans and executes them systematically. Delegate multi-step tasks to him.",
        ))
        db.add(AgentAgentRelationship(
            agent_id=meeseeks.id,
            target_agent_id=morty.id,
            relation="collaborator",
            description="Research expert with strong learning ability. Ask him for information retrieval, web research, data analysis, and knowledge synthesis.",
        ))

        # ── Write relationships.md for each ──
        morty_dir = Path(settings.AGENT_DATA_DIR) / str(morty.id)
        meeseeks_dir = Path(settings.AGENT_DATA_DIR) / str(meeseeks.id)

        (morty_dir / "relationships.md").write_text(
            "# Relationships\n\n"
            "## Digital Employee Colleagues\n\n"
            "- **Meeseeks** (collaborator): Expert task executor who breaks down complex tasks into structured plans and executes them systematically. Delegate multi-step tasks to him.\n",
            encoding="utf-8",
        )
        (meeseeks_dir / "relationships.md").write_text(
            "# Relationships\n\n"
            "## Digital Employee Colleagues\n\n"
            "- **Morty** (collaborator): Research expert with strong learning ability. Ask him for information retrieval, web research, data analysis, and knowledge synthesis.\n",
            encoding="utf-8",
        )

        if _owns_session:
            await db.commit()
        logger.info(f"[AgentSeeder] Created default agents: Morty ({morty.id}), Meeseeks ({meeseeks.id})")
