"""Seed default agent templates into the database on startup."""

import json
from collections.abc import Sequence
from pathlib import Path

from loguru import logger
from sqlalchemy import func, select

from app.database import async_session
from app.models.agent import Agent, AgentTemplate


DEFAULT_AUTONOMY_POLICY = {
    "read_files": "L1",
    "write_workspace_files": "L1",
    "send_feishu_message": "L2",
    "delete_files": "L2",
    "web_search": "L1",
    "manage_tasks": "L1",
}

TEMPLATE_DATA_FILE = Path(__file__).resolve().parent.parent / "data" / "agency_agents_templates.json"

FALLBACK_TEMPLATES = [
    {
        "name": "Project Manager",
        "description": "Manages project timelines, task delegation, cross-team coordination, and progress reporting",
        "icon": "PM",
        "category": "management",
        "is_builtin": True,
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
        "default_autonomy_policy": dict(DEFAULT_AUTONOMY_POLICY),
        "source_key": "fallback/project-manager.md",
    },
    {
        "name": "Designer",
        "description": "Assists with design requirements, design system maintenance, asset management, and competitive UI analysis",
        "icon": "DS",
        "category": "design",
        "is_builtin": True,
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
        "default_autonomy_policy": dict(DEFAULT_AUTONOMY_POLICY),
        "source_key": "fallback/designer.md",
    },
    {
        "name": "Product Intern",
        "description": "Supports product managers with requirements analysis, competitive research, user feedback analysis, and documentation",
        "icon": "PI",
        "category": "product",
        "is_builtin": True,
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
        "default_autonomy_policy": dict(DEFAULT_AUTONOMY_POLICY),
        "source_key": "fallback/product-intern.md",
    },
    {
        "name": "Market Researcher",
        "description": "Focuses on market research, industry analysis, competitive intelligence tracking, and trend insights",
        "icon": "MR",
        "category": "research",
        "is_builtin": True,
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
        "default_autonomy_policy": dict(DEFAULT_AUTONOMY_POLICY),
        "source_key": "fallback/market-researcher.md",
    },
]


def build_builtin_template_indexes(
    existing_builtins: Sequence[AgentTemplate],
) -> tuple[dict[str, AgentTemplate], dict[str, AgentTemplate]]:
    """Index existing builtin templates by stable source key and legacy name."""
    by_source_key: dict[str, AgentTemplate] = {}
    legacy_by_name: dict[str, AgentTemplate] = {}

    for template in existing_builtins:
        if template.source_key:
            by_source_key[template.source_key] = template
        else:
            legacy_by_name[template.name] = template

    return by_source_key, legacy_by_name


def resolve_existing_builtin_template(
    by_source_key: dict[str, AgentTemplate],
    legacy_by_name: dict[str, AgentTemplate],
    template_payload: dict,
) -> AgentTemplate | None:
    """Match builtins by source_key first, then by name for legacy rows without source_key."""
    source_key = template_payload.get("source_key") or None
    if source_key and source_key in by_source_key:
        return by_source_key[source_key]

    return legacy_by_name.get(template_payload["name"])


def load_default_templates() -> list[dict]:
    """Load imported templates when available, otherwise fall back to the local defaults."""
    if not TEMPLATE_DATA_FILE.exists():
        return FALLBACK_TEMPLATES

    try:
        raw_templates = json.loads(TEMPLATE_DATA_FILE.read_text(encoding="utf-8"))
        templates = []
        for raw in raw_templates:
            templates.append(
                {
                    "name": raw["name"],
                    "description": raw.get("description", ""),
                    "icon": raw.get("icon") or raw["name"][:1],
                    "category": raw.get("category", "general"),
                    "is_builtin": True,
                    "soul_template": raw.get("soul_template", "").strip(),
                    "default_skills": list(raw.get("default_skills", [])),
                    "default_autonomy_policy": dict(raw.get("default_autonomy_policy") or DEFAULT_AUTONOMY_POLICY),
                    "source_key": raw.get("source_file", ""),
                }
            )
        logger.info(f"[TemplateSeeder] Loaded {len(templates)} Agency Agents templates")
        return templates
    except Exception as exc:  # pragma: no cover - startup fallback guard
        logger.warning(f"[TemplateSeeder] Failed to load Agency Agents templates: {exc}")
        return FALLBACK_TEMPLATES


async def seed_agent_templates():
    """Insert default agent templates if they don't exist. Update stale ones."""
    current_templates = load_default_templates()

    async with async_session() as db:
        with db.no_autoflush:
            result = await db.execute(select(AgentTemplate).where(AgentTemplate.is_builtin == True))
            existing_builtins = result.scalars().all()
            source_key_index, legacy_name_index = build_builtin_template_indexes(existing_builtins)
            matched_template_ids: set = set()

            for tmpl in current_templates:
                existing = resolve_existing_builtin_template(source_key_index, legacy_name_index, tmpl)
                if existing:
                    matched_template_ids.add(existing.id)
                    old_name = existing.name
                    existing.description = tmpl["description"]
                    existing.icon = tmpl["icon"]
                    existing.category = tmpl["category"]
                    existing.name = tmpl["name"]
                    existing.soul_template = tmpl["soul_template"]
                    existing.default_skills = tmpl["default_skills"]
                    existing.default_autonomy_policy = tmpl["default_autonomy_policy"]
                    existing.source_key = tmpl.get("source_key") or None
                    source_key = tmpl.get("source_key") or None
                    if source_key:
                        source_key_index[source_key] = existing
                    if old_name in legacy_name_index and legacy_name_index[old_name] is existing:
                        legacy_name_index.pop(old_name)
                else:
                    db.add(
                        AgentTemplate(
                            name=tmpl["name"],
                            description=tmpl["description"],
                            icon=tmpl["icon"],
                            category=tmpl["category"],
                            is_builtin=True,
                            soul_template=tmpl["soul_template"],
                            default_skills=tmpl["default_skills"],
                            default_autonomy_policy=tmpl["default_autonomy_policy"],
                            source_key=tmpl.get("source_key") or None,
                        )
                    )
                    logger.info(f"[TemplateSeeder] Created template: {tmpl['name']}")

            for old in existing_builtins:
                if old.id in matched_template_ids:
                    continue

                ref_count = await db.execute(select(func.count(Agent.id)).where(Agent.template_id == old.id))
                if ref_count.scalar() == 0:
                    await db.delete(old)
                    logger.info(f"[TemplateSeeder] Removed old template: {old.name}")
                else:
                    logger.info(
                        f"[TemplateSeeder] Skipping delete of '{old.name}' (still referenced by agents)"
                    )

            await db.commit()
            logger.info("[TemplateSeeder] Agent templates seeded")
