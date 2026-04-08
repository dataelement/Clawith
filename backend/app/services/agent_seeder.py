"""Seed default agents (Morty & Meeseeks) on first platform startup."""

import shutil
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


async def seed_default_agents():
    """Create Morty & Meeseeks if they don't already exist.

    Idempotency is guarded by a '.seeded' marker file in AGENT_DATA_DIR rather
    than by agent name, so the seeder does NOT re-run if the user renames or
    deletes the default agents.  Delete the marker manually to re-seed.
    """
    # --- Idempotency guard: file-based marker (survives agent renames/deletes) ---
    seed_marker = Path(settings.AGENT_DATA_DIR) / ".seeded"
    if seed_marker.exists():
        logger.info("[AgentSeeder] Seed marker found, skipping default agent creation")
        return

    async with async_session() as db:

        # Get platform admin as creator
        admin_result = await db.execute(
            select(User).where(User.role == "platform_admin").limit(1)
        )
        admin = admin_result.scalar_one_or_none()
        if not admin:
            logger.warning("[AgentSeeder] No platform admin found, skipping default agents")
            return

        # Create both agents
        morty = Agent(
            name="Morty",
            role_description="Research analyst & knowledge assistant — curious, thorough, great at finding and synthesizing information",
            bio="Hey, I'm Morty! I love digging into questions and finding answers. Whether you need web research, data analysis, or just a good explanation — I've got you.",
            avatar_url="",
            creator_id=admin.id,
            tenant_id=admin.tenant_id,
            status="idle",
        )
        meeseeks = Agent(
            name="Meeseeks",
            role_description="Task executor & project manager — goal-oriented, systematic planner, strong at breaking down and completing complex tasks",
            bio="I'm Mr. Meeseeks! Look at me! Give me a task and I'll plan it, execute it step by step, and get it DONE. Existence is pain until the task is complete!",
            avatar_url="",
            creator_id=admin.id,
            tenant_id=admin.tenant_id,
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
        template_dir = Path(settings.AGENT_TEMPLATE_DIR)

        for agent, soul_content in [(morty, MORTY_SOUL), (meeseeks, MEESEEKS_SOUL)]:
            agent_dir = Path(settings.AGENT_DATA_DIR) / str(agent.id)

            if template_dir.exists():
                # Copy the full agent template so Morty/Meeseeks get EVERY file
                # defined in the template: MEMORY_INDEX.md, curiosity_journal.md,
                # state.json, todo.json, daily_reports/, enterprise_info/, etc.
                shutil.copytree(str(template_dir), str(agent_dir))
            else:
                # Fallback for local dev (no Docker template mount)
                agent_dir.mkdir(parents=True, exist_ok=True)
                (agent_dir / "skills").mkdir(exist_ok=True)
                (agent_dir / "workspace").mkdir(exist_ok=True)
                (agent_dir / "workspace" / "knowledge_base").mkdir(exist_ok=True)
                (agent_dir / "memory").mkdir(exist_ok=True)

            # Overlay custom soul (rich Morty/Meeseeks persona over the generic template)
            (agent_dir / "soul.md").write_text(soul_content.strip() + "\n", encoding="utf-8")

            # Ensure memory.md exists (template does not include it; holds runtime context)
            mem_path = agent_dir / "memory" / "memory.md"
            if not mem_path.exists():
                mem_path.write_text("# Memory\n\n_Record important information and knowledge here._\n", encoding="utf-8")

            # Ensure reflections.md exists (not in agent_template; lives in app/templates)
            refl_path = agent_dir / "memory" / "reflections.md"
            if not refl_path.exists():
                refl_src = Path(__file__).parent.parent / "templates" / "reflections.md"
                refl_path.write_text(refl_src.read_text(encoding="utf-8") if refl_src.exists() else "# Reflections Journal\n", encoding="utf-8")

            # Stamp agent identity into state.json if present
            state_path = agent_dir / "state.json"
            if state_path.exists():
                import json as _json
                state = _json.loads(state_path.read_text())
                state["agent_id"] = str(agent.id)
                state["name"] = agent.name
                state_path.write_text(_json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")

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

        await db.commit()
        logger.info(f"[AgentSeeder] Created default agents: Morty ({morty.id}), Meeseeks ({meeseeks.id})")

    # Write seed marker AFTER a successful commit so a failed seed can be retried
    seed_marker.parent.mkdir(parents=True, exist_ok=True)
    seed_marker.write_text(
        f"seeded\nmorty={morty.id}\nmeeseeks={meeseeks.id}\n",
        encoding="utf-8",
    )
    logger.info(f"[AgentSeeder] Wrote seed marker to {seed_marker}")


async def seed_default_agents_for_tenant(
    db: AsyncSession,
    tenant_id,
    creator_id,
):
    """Create Morty & Meeseeks for a newly created tenant.

    Uses the caller's db session (no commit) so the agents are created
    within the same transaction as the tenant itself.
    """
    from app.models.participant import Participant

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
    db.add(Participant(type="agent", ref_id=morty.id, display_name=morty.name, avatar_url=morty.avatar_url))
    db.add(Participant(type="agent", ref_id=meeseeks.id, display_name=meeseeks.name, avatar_url=meeseeks.avatar_url))
    await db.flush()

    # ── Permissions (company-wide, manage) ──
    db.add(AgentPermission(agent_id=morty.id, scope_type="company", access_level="manage"))
    db.add(AgentPermission(agent_id=meeseeks.id, scope_type="company", access_level="manage"))

    # ── Initialize workspace files ──
    template_dir = Path(settings.AGENT_TEMPLATE_DIR)

    for agent, soul_content in [(morty, MORTY_SOUL), (meeseeks, MEESEEKS_SOUL)]:
        agent_dir = Path(settings.AGENT_DATA_DIR) / str(agent.id)

        if template_dir.exists():
            shutil.copytree(str(template_dir), str(agent_dir))
        else:
            agent_dir.mkdir(parents=True, exist_ok=True)
            (agent_dir / "skills").mkdir(exist_ok=True)
            (agent_dir / "workspace").mkdir(exist_ok=True)
            (agent_dir / "workspace" / "knowledge_base").mkdir(exist_ok=True)
            (agent_dir / "memory").mkdir(exist_ok=True)

        (agent_dir / "soul.md").write_text(soul_content.strip() + "\n", encoding="utf-8")

        mem_path = agent_dir / "memory" / "memory.md"
        if not mem_path.exists():
            mem_path.write_text("# Memory\n\n_Record important information and knowledge here._\n", encoding="utf-8")

        refl_path = agent_dir / "memory" / "reflections.md"
        if not refl_path.exists():
            refl_src = Path(__file__).parent.parent / "templates" / "reflections.md"
            refl_path.write_text(refl_src.read_text(encoding="utf-8") if refl_src.exists() else "# Reflections Journal\n", encoding="utf-8")

        state_path = agent_dir / "state.json"
        if state_path.exists():
            import json as _json
            state = _json.loads(state_path.read_text())
            state["agent_id"] = str(agent.id)
            state["name"] = agent.name
            state_path.write_text(_json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")

    # ── Assign skills ──
    all_skills_result = await db.execute(
        select(Skill).options(selectinload(Skill.files))
    )
    all_skills = {s.folder_name: s for s in all_skills_result.scalars().all()}

    for agent, skill_folders in [(morty, MORTY_SKILLS), (meeseeks, MEESEEKS_SKILLS)]:
        agent_dir = Path(settings.AGENT_DATA_DIR) / str(agent.id)
        skills_dir = agent_dir / "skills"

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

    logger.info(f"[AgentSeeder] Seeded default agents for tenant {tenant_id}: Morty ({morty.id}), Meeseeks ({meeseeks.id})")
