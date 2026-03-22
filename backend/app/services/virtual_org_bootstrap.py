"""Bootstrap helpers for the virtual organization layer."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.database import async_session
from app.models.agent import Agent, AgentTemplate
from app.models.user import User
from app.models.virtual_org import AgentVirtualOrg, AgentVirtualTag, VirtualDepartment

VIRTUAL_ORG_SEED_FILE = Path(__file__).resolve().parent.parent / "data" / "virtual_org_seed.json"


@dataclass(slots=True)
class BootstrapResult:
    created_departments: int = 0
    updated_assignments: int = 0
    created_primary_agents: int = 0
    created_tags: int = 0
    warnings: list[str] = field(default_factory=list)


def load_virtual_org_seed_data(seed_path: Path | None = None) -> dict[str, list[dict[str, Any]]]:
    """Load and minimally validate the virtual org seed payload."""
    resolved_path = seed_path or VIRTUAL_ORG_SEED_FILE
    raw_payload = json.loads(resolved_path.read_text(encoding="utf-8"))

    departments = list(raw_payload.get("departments") or [])
    assignments = list(raw_payload.get("assignments") or [])
    if not departments:
        raise ValueError("virtual org seed is missing departments")
    if not assignments:
        raise ValueError("virtual org seed is missing assignments")

    required_department_keys = {"slug", "name"}
    required_assignment_keys = {
        "source_key",
        "template_name",
        "agent_name",
        "department_slug",
        "title",
        "level",
        "org_bucket",
        "tags",
    }

    for department in departments:
        missing_keys = required_department_keys - department.keys()
        if missing_keys:
            raise ValueError(f"virtual org department is missing keys: {sorted(missing_keys)}")

    for assignment in assignments:
        missing_keys = required_assignment_keys - assignment.keys()
        if missing_keys:
            raise ValueError(f"virtual org assignment is missing keys: {sorted(missing_keys)}")

    return validate_virtual_org_seed_data({"departments": departments, "assignments": assignments})


def validate_virtual_org_seed_data(seed_data: dict[str, list[dict[str, Any]]]) -> dict[str, list[dict[str, Any]]]:
    """Validate cross-row seed constraints used by bootstrap startup checks."""
    assignments = list(seed_data.get("assignments") or [])
    resolvable_source_keys = {
        str(row["source_key"])
        for row in assignments
        if row.get("source_key")
    }
    invalid_manager_keys = sorted(
        {
            str(row["manager_source_key"])
            for row in assignments
            if row.get("manager_source_key") and str(row["manager_source_key"]) not in resolvable_source_keys
        }
    )
    if invalid_manager_keys:
        raise ValueError(
            "virtual org seed has unresolved manager_source_key values: "
            + ", ".join(invalid_manager_keys)
        )

    return seed_data


def bootstrap_virtual_org(
    session: Session,
    tenant_id: uuid.UUID,
    *,
    seed_rows: list[dict[str, Any]] | None = None,
    department_rows: list[dict[str, Any]] | None = None,
    force: bool = False,
) -> BootstrapResult:
    """Bootstrap the virtual organization for a single tenant."""
    if seed_rows is None or department_rows is None:
        seed_data = load_virtual_org_seed_data()
        assignment_rows = seed_rows if seed_rows is not None else list(seed_data["assignments"])
        department_seed_rows = department_rows if department_rows is not None else list(seed_data["departments"])
    else:
        assignment_rows = seed_rows
        department_seed_rows = department_rows

    result = BootstrapResult()

    try:
        departments, created_departments = ensure_default_departments(
            session,
            tenant_id,
            department_rows=department_seed_rows,
            assignment_rows=assignment_rows,
        )
        result.created_departments += created_departments

        templates = list(session.execute(select(AgentTemplate)).scalars().all())
        templates_by_id = {template.id: template for template in templates}
        templates_by_source_key = {
            template.source_key: template for template in templates if template.source_key
        }
        templates_by_name = {template.name: template for template in templates}

        creator = _resolve_creator_user(session, tenant_id)
        assignments_by_agent: dict[uuid.UUID, AgentVirtualOrg] = {
            assignment.agent_id: assignment
            for assignment in session.execute(
                select(AgentVirtualOrg).where(
                    AgentVirtualOrg.tenant_id == tenant_id,
                    AgentVirtualOrg.is_primary.is_(True),
                )
            ).scalars()
        }
        primary_agent_by_template: dict[uuid.UUID, uuid.UUID] = {
            assignment.template_id: assignment.agent_id
            for assignment in assignments_by_agent.values()
            if assignment.is_org_primary_instance and assignment.template_id is not None
        }
        assignment_rows_by_agent: dict[uuid.UUID, dict[str, Any]] = {}

        for row in assignment_rows:
            template = _resolve_template(row, templates_by_id, templates_by_source_key, templates_by_name)
            resolved_agent = _find_existing_agent_by_name(session, tenant_id, row, template)
            if template is None and resolved_agent is not None and resolved_agent.template_id is not None:
                template = templates_by_id.get(resolved_agent.template_id)

            if template is None and row.get("source_key"):
                warning = f"missing template for virtual org row: {row.get('source_key') or row.get('template_id')}"
                logger.warning(f"[VirtualOrgBootstrap] {warning}")
                result.warnings.append(warning)
                continue

            if resolved_agent is not None and _can_reuse_agent_for_org_primary(session, resolved_agent.id, template is not None):
                agent = resolved_agent
                created_agent = False
            elif template is not None:
                agent, created_agent = _ensure_org_primary_agent_instance(session, tenant_id, template, row, creator)
            else:
                agent, created_agent = _ensure_named_agent_instance(session, tenant_id, row, creator)
            if created_agent:
                result.created_primary_agents += 1

            assignment, created_assignment, updated_assignment = _upsert_assignment(
                session,
                tenant_id,
                agent,
                template,
                departments,
                row,
                force=force,
                is_org_primary_instance=template is not None,
            )
            if updated_assignment:
                result.updated_assignments += 1

            if assignment.is_locked and not force:
                assignments_by_agent[agent.id] = assignment
                if assignment.template_id is not None and assignment.is_org_primary_instance:
                    primary_agent_by_template[assignment.template_id] = agent.id
                assignment_rows_by_agent[agent.id] = row
                continue

            if created_assignment and not updated_assignment:
                result.updated_assignments += 1

            created_tags = _sync_tags(session, tenant_id, agent.id, row.get("tags") or [])
            result.created_tags += created_tags
            assignments_by_agent[agent.id] = assignment
            if assignment.template_id is not None and assignment.is_org_primary_instance:
                primary_agent_by_template[assignment.template_id] = agent.id
            assignment_rows_by_agent[agent.id] = row

        unmatched_updates, unmatched_tags = _assign_unmatched_existing_agents(
            session,
            tenant_id,
            assignments_by_agent,
            departments,
            templates_by_id,
        )
        result.updated_assignments += unmatched_updates
        result.created_tags += unmatched_tags

        _apply_manager_assignments(
            session,
            tenant_id,
            assignment_rows_by_agent,
            assignments_by_agent,
            primary_agent_by_template,
            templates_by_source_key,
            force=force,
        )
        session.commit()
        return result
    except Exception:
        session.rollback()
        raise


def ensure_default_departments(
    session: Session,
    tenant_id: uuid.UUID,
    *,
    department_rows: list[dict[str, Any]],
    assignment_rows: list[dict[str, Any]],
) -> tuple[dict[str, VirtualDepartment], int]:
    """Ensure the department seed tree exists for the tenant."""
    existing_departments = session.execute(
        select(VirtualDepartment).where(VirtualDepartment.tenant_id == tenant_id)
    ).scalars().all()
    departments_by_slug = {department.slug: department for department in existing_departments}
    created_count = 0

    merged_rows = [dict(row) for row in department_rows]
    known_slugs = {row["slug"] for row in merged_rows}
    for row in assignment_rows:
        slug = str(row["department_slug"])
        if slug in known_slugs:
            continue
        known_slugs.add(slug)
        merged_rows.append(
            {
                "slug": slug,
                "name": slug.replace("-", " ").title(),
                "sort_order": 999,
                "org_level": "department",
                "is_core": row.get("org_bucket") != "expert",
            }
        )

    pending_rows = merged_rows[:]
    while pending_rows:
        progressed = False
        next_pending_rows: list[dict[str, Any]] = []
        for row in pending_rows:
            parent_slug = row.get("parent_slug")
            if parent_slug and parent_slug not in departments_by_slug:
                next_pending_rows.append(row)
                continue

            department = departments_by_slug.get(row["slug"])
            parent_id = departments_by_slug[parent_slug].id if parent_slug else None
            if department is None:
                department = VirtualDepartment(
                    name=str(row["name"]),
                    slug=str(row["slug"]),
                    parent_id=parent_id,
                    sort_order=int(row.get("sort_order") or 0),
                    org_level=str(row.get("org_level") or "department"),
                    is_core=bool(row.get("is_core", True)),
                    tenant_id=tenant_id,
                )
                session.add(department)
                session.flush()
                departments_by_slug[department.slug] = department
                created_count += 1
            else:
                department.name = str(row["name"])
                department.parent_id = parent_id
                department.sort_order = int(row.get("sort_order") or 0)
                department.org_level = str(row.get("org_level") or "department")
                department.is_core = bool(row.get("is_core", True))

            progressed = True

        if not progressed:
            unresolved = ", ".join(str(row.get("slug")) for row in next_pending_rows)
            raise ValueError(f"unresolved virtual org department parents: {unresolved}")
        pending_rows = next_pending_rows

    return departments_by_slug, created_count


def _resolve_creator_user(session: Session, tenant_id: uuid.UUID) -> User:
    users = list(session.execute(select(User).where(User.tenant_id == tenant_id)).scalars().all())
    if not users:
        raise ValueError(f"tenant {tenant_id} has no users to own bootstrap agents")

    role_priority = {"platform_admin": 0, "org_admin": 1, "agent_admin": 2, "member": 3}
    users.sort(key=lambda user: (role_priority.get(user.role, 99), user.created_at is None, str(user.created_at or "")))
    return users[0]


def _resolve_template(
    row: dict[str, Any],
    templates_by_id: dict[uuid.UUID, AgentTemplate],
    templates_by_source_key: dict[str, AgentTemplate],
    templates_by_name: dict[str, AgentTemplate],
) -> AgentTemplate | None:
    raw_template_id = row.get("template_id")
    if raw_template_id:
        template_id = raw_template_id if isinstance(raw_template_id, uuid.UUID) else uuid.UUID(str(raw_template_id))
        template = templates_by_id.get(template_id)
        if template is not None:
            return template

    source_key = row.get("source_key")
    if source_key:
        template = templates_by_source_key.get(str(source_key))
        if template is not None:
            return template

    for name_key in ("template_name", "agent_name"):
        raw_name = row.get(name_key)
        if raw_name:
            template = templates_by_name.get(str(raw_name))
            if template is not None:
                return template

    return None


def _ensure_named_agent_instance(
    session: Session,
    tenant_id: uuid.UUID,
    row: dict[str, Any],
    creator: User,
) -> tuple[Agent, bool]:
    agent_name = str(row.get("agent_name") or row.get("template_name") or "Virtual Org Agent")
    candidate_agents = session.execute(
        select(Agent).where(Agent.tenant_id == tenant_id, Agent.name == agent_name)
    ).scalars().all()
    if candidate_agents:
        candidate_agents.sort(key=lambda agent: (agent.created_at is None, str(agent.created_at or ""), str(agent.id)))
        return candidate_agents[0], False

    agent = Agent(
        name=agent_name,
        role_description=str(row.get("title") or agent_name),
        creator_id=creator.id,
        tenant_id=tenant_id,
        status="idle",
    )
    session.add(agent)
    session.flush()
    return agent, True


def _find_existing_agent_by_name(
    session: Session,
    tenant_id: uuid.UUID,
    row: dict[str, Any],
    template: AgentTemplate | None,
) -> Agent | None:
    agent_name = row.get("agent_name")
    if not agent_name:
        return None

    candidate_agents = list(
        session.execute(
            select(Agent).where(Agent.tenant_id == tenant_id, Agent.name == str(agent_name))
        ).scalars()
    )
    if not candidate_agents:
        return None

    if template is not None:
        candidate_agents = [agent for agent in candidate_agents if agent.template_id == template.id]
        if not candidate_agents:
            return None

    candidate_agents.sort(
        key=lambda agent: (agent.created_at is None, str(agent.created_at or ""), str(agent.id))
    )
    return candidate_agents[0]


def _can_reuse_agent_for_org_primary(session: Session, agent_id: uuid.UUID, requires_org_primary: bool) -> bool:
    primary_assignment = session.execute(
        select(AgentVirtualOrg).where(
            AgentVirtualOrg.agent_id == agent_id,
            AgentVirtualOrg.is_primary.is_(True),
        )
    ).scalar_one_or_none()
    if primary_assignment is None:
        return True
    if not primary_assignment.is_locked:
        return True
    if not requires_org_primary:
        return True
    return primary_assignment.is_org_primary_instance


def _ensure_org_primary_agent_instance(
    session: Session,
    tenant_id: uuid.UUID,
    template: AgentTemplate,
    row: dict[str, Any],
    creator: User,
) -> tuple[Agent, bool]:
    existing_primary_assignment = session.execute(
        select(AgentVirtualOrg).where(
            AgentVirtualOrg.tenant_id == tenant_id,
            AgentVirtualOrg.template_id == template.id,
            AgentVirtualOrg.is_org_primary_instance.is_(True),
        )
    ).scalar_one_or_none()
    if existing_primary_assignment is not None:
        existing_primary_agent = session.get(Agent, existing_primary_assignment.agent_id)
        if existing_primary_agent is None:
            raise ValueError(f"missing agent for org primary assignment {existing_primary_assignment.id}")
        return existing_primary_agent, False

    candidate_agents = list(
        session.execute(
            select(Agent).where(Agent.tenant_id == tenant_id, Agent.template_id == template.id)
        ).scalars().all()
    )
    reusable_candidates = [
        agent for agent in candidate_agents if _can_reuse_agent_for_org_primary(session, agent.id, True)
    ]
    if reusable_candidates:
        preferred_name = str(row.get("agent_name") or template.name)
        reusable_candidates.sort(
            key=lambda agent: (agent.name != preferred_name, agent.created_at is None, str(agent.created_at or ""), str(agent.id))
        )
        return reusable_candidates[0], False

    agent = Agent(
        name=str(row.get("agent_name") or template.name),
        role_description=template.description or "",
        creator_id=creator.id,
        tenant_id=tenant_id,
        template_id=template.id,
        status="idle",
    )
    session.add(agent)
    session.flush()
    return agent, True


def _upsert_assignment(
    session: Session,
    tenant_id: uuid.UUID,
    agent: Agent,
    template: AgentTemplate | None,
    departments_by_slug: dict[str, VirtualDepartment],
    row: dict[str, Any],
    *,
    force: bool,
    is_org_primary_instance: bool,
) -> tuple[AgentVirtualOrg, bool, bool]:
    department_slug = str(row["department_slug"])
    department = departments_by_slug[department_slug]
    assignment = session.execute(
        select(AgentVirtualOrg).where(
            AgentVirtualOrg.agent_id == agent.id,
            AgentVirtualOrg.is_primary.is_(True),
        )
    ).scalar_one_or_none()
    if assignment is not None and assignment.is_locked and not force:
        return assignment, False, False

    created = False
    if assignment is None:
        assignment = AgentVirtualOrg(
            agent_id=agent.id,
            department_id=department.id,
            template_id=template.id if template is not None else None,
            tenant_id=tenant_id,
        )
        session.add(assignment)
        created = True

    changed = created
    desired_title = str(row.get("title") or (template.name if template is not None else agent.name))
    desired_level = str(row.get("level") or "L3")
    desired_bucket = str(row.get("org_bucket") or "core")
    desired_values = {
        "department_id": department.id,
        "template_id": template.id if template is not None else agent.template_id,
        "title": desired_title,
        "level": desired_level,
        "org_bucket": desired_bucket,
        "is_primary": True,
        "is_org_primary_instance": is_org_primary_instance,
        "tenant_id": tenant_id,
    }
    for field_name, desired_value in desired_values.items():
        if getattr(assignment, field_name) != desired_value:
            setattr(assignment, field_name, desired_value)
            changed = True

    session.flush()
    return assignment, created, changed


def _assign_unmatched_existing_agents(
    session: Session,
    tenant_id: uuid.UUID,
    assignments_by_agent: dict[uuid.UUID, AgentVirtualOrg],
    departments_by_slug: dict[str, VirtualDepartment],
    templates_by_id: dict[uuid.UUID, AgentTemplate],
) -> tuple[int, int]:
    updates = 0
    created_tags = 0
    fallback_department = departments_by_slug["expert-unassigned"]
    tenant_agents = session.execute(select(Agent).where(Agent.tenant_id == tenant_id)).scalars().all()
    for agent in tenant_agents:
        if agent.id in assignments_by_agent:
            continue

        template = templates_by_id.get(agent.template_id) if agent.template_id is not None else None
        fallback_row = {
            "template_name": template.name if template is not None else agent.name,
            "agent_name": agent.name,
            "department_slug": fallback_department.slug,
            "title": template.name if template is not None else agent.name,
            "level": "L5",
            "org_bucket": "expert",
            "manager_source_key": None,
            "tags": ["expert-pool", "unclassified"],
        }
        assignment, _, changed = _upsert_assignment(
            session,
            tenant_id,
            agent,
            template,
            departments_by_slug,
            fallback_row,
            force=False,
            is_org_primary_instance=False,
        )
        if changed:
            updates += 1
        created_tags += _sync_tags(session, tenant_id, agent.id, fallback_row["tags"])
        assignments_by_agent[agent.id] = assignment

    return updates, created_tags


def _sync_tags(session: Session, tenant_id: uuid.UUID, agent_id: uuid.UUID, tags: list[Any]) -> int:
    desired_tags = {str(tag) for tag in tags if str(tag).strip()}
    existing_tags = session.execute(
        select(AgentVirtualTag).where(
            AgentVirtualTag.tenant_id == tenant_id,
            AgentVirtualTag.agent_id == agent_id,
        )
    ).scalars().all()
    existing_by_value = {tag.tag: tag for tag in existing_tags}
    created_count = 0

    for tag_value in desired_tags - set(existing_by_value):
        session.add(AgentVirtualTag(agent_id=agent_id, tag=tag_value, tenant_id=tenant_id))
        created_count += 1

    for tag_value in set(existing_by_value) - desired_tags:
        session.delete(existing_by_value[tag_value])

    session.flush()
    return created_count


def _apply_manager_assignments(
    session: Session,
    tenant_id: uuid.UUID,
    assignment_rows_by_agent: dict[uuid.UUID, dict[str, Any]],
    assignments_by_agent: dict[uuid.UUID, AgentVirtualOrg],
    primary_agent_by_template: dict[uuid.UUID, uuid.UUID],
    templates_by_source_key: dict[str, AgentTemplate],
    *,
    force: bool,
) -> None:
    for agent_id, row in assignment_rows_by_agent.items():
        assignment = assignments_by_agent[agent_id]
        if assignment.is_locked and not force:
            continue

        manager_source_key = row.get("manager_source_key")
        if not manager_source_key:
            assignment.manager_agent_id = None
            continue

        manager_template = templates_by_source_key.get(str(manager_source_key))
        if manager_template is None:
            raise ValueError(f"missing manager template for {manager_source_key}")

        manager_agent_id = primary_agent_by_template.get(manager_template.id)
        if manager_agent_id is None:
            raise ValueError(f"missing manager agent for {manager_source_key}")

        assign_manager(
            session,
            tenant_id,
            assignments_by_agent,
            agent_id=agent_id,
            manager_agent_id=manager_agent_id,
        )

    session.flush()


def assign_manager(
    session: Session,
    tenant_id: uuid.UUID,
    assignments_by_agent: dict[uuid.UUID, AgentVirtualOrg],
    *,
    agent_id: uuid.UUID,
    manager_agent_id: uuid.UUID,
) -> None:
    """Assign a manager while enforcing tenant, self, and cycle checks."""
    if agent_id == manager_agent_id:
        raise ValueError("manager cycle detected: self reference is not allowed")

    assignment = assignments_by_agent.get(agent_id)
    manager_assignment = assignments_by_agent.get(manager_agent_id)
    if assignment is None:
        raise ValueError(f"agent {agent_id} does not have a primary virtual org assignment")
    if manager_assignment is None:
        raise ValueError(f"manager {manager_agent_id} does not have a primary virtual org assignment")

    agent = session.get(Agent, agent_id)
    manager = session.get(Agent, manager_agent_id)
    if agent is None or manager is None:
        raise ValueError("manager assignment requires both agents to exist")
    if agent.tenant_id != tenant_id or manager.tenant_id != tenant_id:
        raise ValueError("manager assignment must stay within the same tenant")

    visited: set[uuid.UUID] = set()
    current_agent_id: uuid.UUID | None = manager_agent_id
    while current_agent_id is not None:
        if current_agent_id == agent_id:
            raise ValueError("manager cycle detected")
        if current_agent_id in visited:
            raise ValueError("manager cycle detected")
        visited.add(current_agent_id)
        current_assignment = assignments_by_agent.get(current_agent_id)
        current_agent_id = current_assignment.manager_agent_id if current_assignment is not None else None

    assignment.manager_agent_id = manager_agent_id


async def prepare_virtual_org_bootstrap_startup() -> None:
    """Validate that seed data and stable template keys are ready at startup."""
    seed_data = validate_virtual_org_seed_data(load_virtual_org_seed_data())
    source_keys = {
        str(row["source_key"])
        for row in seed_data["assignments"]
        if row.get("source_key")
    }
    manager_source_keys = {
        str(row["manager_source_key"])
        for row in seed_data["assignments"]
        if row.get("manager_source_key")
    }
    async with async_session() as session:
        await _log_seed_readiness(session, source_keys, manager_source_keys)


async def _log_seed_readiness(
    session: AsyncSession,
    source_keys: set[str],
    manager_source_keys: set[str],
) -> None:
    result = await session.execute(
        select(AgentTemplate.source_key).where(AgentTemplate.source_key.in_(source_keys))
    )
    resolved_source_keys = {row[0] for row in result if row[0]}
    missing_source_keys = sorted(source_keys - resolved_source_keys)
    missing_manager_source_keys = sorted(manager_source_keys - resolved_source_keys)
    if missing_manager_source_keys:
        raise ValueError(
            "virtual org startup prep is missing manager templates for source keys: "
            + ", ".join(missing_manager_source_keys)
        )
    logger.info(
        f"[VirtualOrgBootstrap] Seed readiness: {len(resolved_source_keys)}/{len(source_keys)} template source keys resolved"
    )
    if missing_source_keys:
        logger.warning(
            "[VirtualOrgBootstrap] Unresolved source keys during startup prep: "
            + ", ".join(missing_source_keys[:10])
        )
