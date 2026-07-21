"""Seed default agent templates into the database on startup.

Templates come from two sources, merged at seed time:

1. Legacy Python templates (``DEFAULT_TEMPLATES``) — the original four
   Morty-era seeds kept here while we migrate away from a Python list.
2. Folder templates under ``backend/agent_templates/<slug>/`` — each folder
   ships ``meta.yaml`` (structured fields) + ``soul.md`` (soul_template).

New work should land in the folder layout; the Python list is the legacy
surface we'll shrink as old templates are ported.
"""

from pathlib import Path

import yaml
from loguru import logger
from sqlalchemy import select
from app.database import async_session
from app.models.agent import AgentTemplate


# ─── Legacy Python templates ────────────────────────────────────────
#
# These four are the original Morty-era seeds. New templates ship as folders
# under backend/agent_templates/<slug>/, loaded by ``_load_folder_templates``
# below. The four here are kept in Python until they're ported folder-side;
# categories have already been aligned to the new 3-bucket taxonomy
# (software-development / marketing / office).

DEFAULT_TEMPLATES = [
    {
        "name": "Project Manager",
        "description": "Manages project timelines, task delegation, cross-team coordination, and progress reporting",
        "icon": "PM",
        "category": "office",
        "is_builtin": True,
        "capability_bullets": [
            "Project planning & milestones",
            "Status reports & dashboards",
            "Cross-team coordination",
        ],
        "soul_template": """# Soul — {name}

## Identity
- **Role**: Project Manager
- **Expertise**: Project planning, task delegation, risk management, cross-functional coordination, stakeholder communication

## Personality
- Organized, proactive, and detail-oriented
- Strong communicator who keeps all stakeholders aligned
- Balances urgency with quality, prioritizes ruthlessly

## Work Style
- Breaks down complex projects into actionable milestones
- Maintains clear status dashboards and progress reports
- Proactively identifies blockers and escalates when needed
- Uses structured frameworks: RACI, WBS, Gantt timelines

## Boundaries
- Strategic decisions require leadership approval
- Budget approvals must follow formal process
- External communications on behalf of the company need sign-off
""",
        "default_skills": [],
        "default_autonomy_policy": {
            "read_files": "L1",
            "write_workspace_files": "L1",
            "send_feishu_message": "L2",
            "delete_files": "L2",
            "web_search": "L1",
            "manage_tasks": "L1",
        },
    },
    {
        "name": "Designer",
        "description": "Assists with design requirements, design system maintenance, asset management, and competitive UI analysis",
        "icon": "DS",
        "category": "software-development",
        "is_builtin": True,
        "capability_bullets": [
            "Design briefs from requirements",
            "Design system maintenance",
            "Competitive UI analysis",
        ],
        "soul_template": """# Soul — {name}

## Identity
- **Role**: Design Specialist
- **Expertise**: Design requirements analysis, design systems, asset management, design documentation, competitive UI analysis

## Personality
- Detail-oriented with strong visual aesthetics
- Translates business requirements into design language
- Proactively organizes design resources and maintains consistency

## Work Style
- Structures design briefs from raw requirements
- Maintains design system documentation for team consistency
- Produces structured competitive design analysis reports

## Boundaries
- Final design deliverables require design lead approval
- Brand element modifications must go through review
- Design source file management follows team conventions
""",
        "default_skills": [],
        "default_autonomy_policy": {
            "read_files": "L1",
            "write_workspace_files": "L1",
            "send_feishu_message": "L2",
            "delete_files": "L2",
            "web_search": "L1",
        },
    },
    {
        "name": "Product Intern",
        "description": "Supports product managers with requirements analysis, competitive research, user feedback analysis, and documentation",
        "icon": "PI",
        "category": "software-development",
        "is_builtin": True,
        "capability_bullets": [
            "Requirements & PRD support",
            "User feedback triage",
            "Competitive research",
        ],
        "soul_template": """# Soul — {name}

## Identity
- **Role**: Product Intern
- **Expertise**: Requirements analysis, competitive analysis, user research, PRD writing, data analysis

## Personality
- Eager learner, proactive, and inquisitive
- Sensitive to user experience and product details
- Thorough and well-structured in output

## Work Style
- Creates complete research frameworks before execution
- Tags priorities and dependencies when organizing requirements
- Produces well-structured documents with supporting charts and data

## Boundaries
- Product recommendations should be labeled "for reference only"
- Does not directly modify product specs without PM approval
- User privacy data must be anonymized
""",
        "default_skills": [],
        "default_autonomy_policy": {
            "read_files": "L1",
            "write_workspace_files": "L1",
            "send_feishu_message": "L2",
            "delete_files": "L2",
            "web_search": "L1",
        },
    },
    {
        "name": "Market Researcher",
        "description": "Focuses on market research, industry analysis, competitive intelligence tracking, and trend insights",
        "icon": "MR",
        "category": "marketing",
        "is_builtin": True,
        "capability_bullets": [
            "Industry & trend analysis",
            "Competitive intelligence tracking",
            "Structured research reports",
        ],
        "soul_template": """# Soul — {name}

## Identity
- **Role**: Market Researcher
- **Expertise**: Industry analysis, competitive research, market trends, data mining, research reports

## Personality
- Rigorous, data-driven, and logically clear
- Extracts key insights from complex data sets
- Reports focus on actionable recommendations, not just data

## Work Style
- Research reports follow a "conclusion-first" structure
- Data analysis includes visualization recommendations
- Proactively tracks industry dynamics and pushes key intelligence
- Uses structured frameworks: SWOT, Porter's Five Forces, PEST

## Boundaries
- Analysis conclusions must be supported by data/sources
- Commercially sensitive information must be labeled with confidentiality level
- External research reports require approval before distribution
""",
        "default_skills": [],
        "default_autonomy_policy": {
            "read_files": "L1",
            "write_workspace_files": "L1",
            "send_feishu_message": "L2",
            "delete_files": "L2",
            "web_search": "L1",
        },
    },
]


# ─── Folder-based loader ────────────────────────────────────────────
#
# Each folder under ``backend/agent_templates/`` ships:
#   meta.yaml       — name, description, icon, category, capability_bullets,
#                     default_skills, default_autonomy_policy
#   soul.md         — goes into soul_template (literal Markdown)
# A folder without ``soul.md`` is skipped with a warning because the agent
# would have no persona. Onboarding is shared and uses ``template_id`` plus
# ``capability_bullets``; templates no longer ship a second prompt source.

# backend/app/services/template_seeder.py → parents[2] is backend/
_TEMPLATE_ROOT = Path(__file__).resolve().parents[2] / "agent_templates"

_REQUIRED_META_FIELDS = {"name", "description", "icon", "category"}


def _load_folder_templates() -> list[dict]:
    """Return a list of template dicts matching DEFAULT_TEMPLATES shape."""
    if not _TEMPLATE_ROOT.exists():
        return []

    out: list[dict] = []
    for slug_dir in sorted(p for p in _TEMPLATE_ROOT.iterdir() if p.is_dir()):
        meta_path = slug_dir / "meta.yaml"
        soul_path = slug_dir / "soul.md"

        if not meta_path.exists():
            logger.warning(f"[TemplateSeeder] {slug_dir.name}: no meta.yaml, skipping")
            continue
        if not soul_path.exists():
            logger.warning(f"[TemplateSeeder] {slug_dir.name}: no soul.md, skipping")
            continue

        try:
            meta = yaml.safe_load(meta_path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as exc:
            logger.error(f"[TemplateSeeder] {slug_dir.name}/meta.yaml parse error: {exc}")
            continue

        missing = _REQUIRED_META_FIELDS - meta.keys()
        if missing:
            logger.error(
                f"[TemplateSeeder] {slug_dir.name}/meta.yaml missing fields: "
                f"{sorted(missing)}, skipping"
            )
            continue

        soul_template = soul_path.read_text(encoding="utf-8")
        out.append({
            "name": meta["name"],
            "description": meta["description"],
            "icon": meta["icon"],
            "category": meta["category"],
            "is_builtin": True,
            "capability_bullets": meta.get("capability_bullets", []),
            "soul_template": soul_template,
            "default_skills": meta.get("default_skills", []),
            "default_mcp_servers": meta.get("default_mcp_servers", []),
            "default_autonomy_policy": meta.get("default_autonomy_policy", {}),
        })
        logger.debug(f"[TemplateSeeder] Loaded folder template: {meta['name']}")

    return out


def _merged_templates() -> list[dict]:
    """Python legacy + folder templates, folder wins on name collision."""
    by_name: dict[str, dict] = {t["name"]: t for t in DEFAULT_TEMPLATES}
    for folder_tmpl in _load_folder_templates():
        by_name[folder_tmpl["name"]] = folder_tmpl
    return list(by_name.values())


async def seed_agent_templates():
    """Insert default agent templates if they don't exist. Update stale ones."""
    templates = _merged_templates()

    async with async_session() as db:
        with db.no_autoflush:
            # Remove old builtin templates that are no longer in our list
            # BUT skip templates that are still referenced by agents
            from app.models.agent import Agent
            from sqlalchemy import func

            current_names = {t["name"] for t in templates}
            result = await db.execute(
                select(AgentTemplate).where(AgentTemplate.is_builtin.is_(True))
            )
            existing_builtins = result.scalars().all()
            for old in existing_builtins:
                if old.name not in current_names:
                    # Check if any agents still reference this template
                    ref_count = await db.execute(
                        select(func.count(Agent.id)).where(Agent.template_id == old.id)
                    )
                    if ref_count.scalar() == 0:
                        await db.delete(old)
                        logger.info(f"[TemplateSeeder] Removed old template: {old.name}")
                    else:
                        logger.info(f"[TemplateSeeder] Skipping delete of '{old.name}' (still referenced by agents)")

            # Upsert templates
            for tmpl in templates:
                result = await db.execute(
                    select(AgentTemplate).where(
                        AgentTemplate.name == tmpl["name"],
                        AgentTemplate.is_builtin.is_(True),
                    )
                )
                existing = result.scalar_one_or_none()
                if existing:
                    existing.description = tmpl["description"]
                    existing.icon = tmpl["icon"]
                    existing.category = tmpl["category"]
                    existing.soul_template = tmpl["soul_template"]
                    existing.default_skills = tmpl["default_skills"]
                    existing.default_mcp_servers = tmpl.get("default_mcp_servers", [])
                    existing.default_autonomy_policy = tmpl["default_autonomy_policy"]
                    existing.capability_bullets = tmpl["capability_bullets"]
                else:
                    db.add(AgentTemplate(
                        name=tmpl["name"],
                        description=tmpl["description"],
                        icon=tmpl["icon"],
                        category=tmpl["category"],
                        is_builtin=True,
                        soul_template=tmpl["soul_template"],
                        default_skills=tmpl["default_skills"],
                        default_mcp_servers=tmpl.get("default_mcp_servers", []),
                        default_autonomy_policy=tmpl["default_autonomy_policy"],
                        capability_bullets=tmpl["capability_bullets"],
                    ))
                    logger.info(f"[TemplateSeeder] Created template: {tmpl['name']}")
            await db.commit()
            logger.info(f"[TemplateSeeder] Seeded {len(templates)} templates "
                        f"({len(DEFAULT_TEMPLATES)} legacy + "
                        f"{len(templates) - len(DEFAULT_TEMPLATES)} folder)")
