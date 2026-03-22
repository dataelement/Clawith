"""Virtual organization API routes."""

import uuid
from collections import defaultdict

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import get_current_admin, get_current_user
from app.database import get_db
from app.models.agent import Agent, AgentPermission
from app.models.user import User
from app.models.virtual_org import AgentVirtualOrg, AgentVirtualTag, VirtualDepartment
from app.schemas.virtual_org import (
    VirtualOrgAgentListOut,
    VirtualOrgAgentPatch,
    VirtualOrgAgentSummary,
    VirtualOrgBootstrapRequest,
    VirtualOrgBootstrapResponse,
    VirtualOrgDepartmentCreate,
    VirtualOrgDepartmentOut,
    VirtualOrgDepartmentPatch,
    VirtualOrgExpertPoolOut,
    VirtualOrgOverviewOut,
)
from app.services.virtual_org_bootstrap import bootstrap_virtual_org

router = APIRouter(prefix="/virtual-org", tags=["virtual-org"])


def _require_tenant(user: User) -> uuid.UUID:
    if user.tenant_id is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="User is not assigned to a tenant")
    return user.tenant_id


async def _visible_agent_ids(db: AsyncSession, current_user: User) -> set[uuid.UUID]:
    tenant_id = _require_tenant(current_user)
    if current_user.role in ("platform_admin", "org_admin"):
        result = await db.execute(select(Agent.id).where(Agent.tenant_id == tenant_id))
        return set(result.scalars().all())

    visible_ids: set[uuid.UUID] = set()

    created_result = await db.execute(
        select(Agent.id).where(Agent.creator_id == current_user.id, Agent.tenant_id == tenant_id)
    )
    visible_ids.update(created_result.scalars().all())

    permission_stmt = select(AgentPermission.agent_id).join(Agent, Agent.id == AgentPermission.agent_id).where(
        Agent.tenant_id == tenant_id
    )
    permission_result = await db.execute(permission_stmt)
    for agent_id in permission_result.scalars().all():
        perm_result = await db.execute(select(AgentPermission).where(AgentPermission.agent_id == agent_id))
        for permission in perm_result.scalars().all():
            if permission.scope_type == "company":
                visible_ids.add(agent_id)
            elif permission.scope_type == "user" and permission.scope_id == current_user.id:
                visible_ids.add(agent_id)
            elif permission.scope_type == "department" and current_user.department_id and permission.scope_id == current_user.department_id:
                visible_ids.add(agent_id)

    return visible_ids


async def _primary_assignments_with_context(
    db: AsyncSession,
    current_user: User,
    *,
    department_id: uuid.UUID | None = None,
    org_bucket: str | None = None,
) -> list[tuple[AgentVirtualOrg, Agent, VirtualDepartment]]:
    visible_ids = await _visible_agent_ids(db, current_user)
    if not visible_ids:
        return []

    tenant_id = _require_tenant(current_user)
    stmt = (
        select(AgentVirtualOrg, Agent, VirtualDepartment)
        .join(Agent, Agent.id == AgentVirtualOrg.agent_id)
        .join(VirtualDepartment, VirtualDepartment.id == AgentVirtualOrg.department_id)
        .where(
            AgentVirtualOrg.tenant_id == tenant_id,
            AgentVirtualOrg.is_primary.is_(True),
            AgentVirtualOrg.agent_id.in_(visible_ids),
        )
        .order_by(VirtualDepartment.sort_order.asc(), AgentVirtualOrg.level.asc(), Agent.name.asc())
    )
    if department_id is not None:
        stmt = stmt.where(AgentVirtualOrg.department_id == department_id)
    if org_bucket is not None:
        stmt = stmt.where(AgentVirtualOrg.org_bucket == org_bucket)
    result = await db.execute(stmt)
    return list(result.all())


async def _tags_by_agent_id(db: AsyncSession, agent_ids: set[uuid.UUID]) -> dict[uuid.UUID, list[str]]:
    if not agent_ids:
        return {}
    result = await db.execute(select(AgentVirtualTag).where(AgentVirtualTag.agent_id.in_(agent_ids)))
    tags_by_agent: dict[uuid.UUID, list[str]] = defaultdict(list)
    for row in result.scalars().all():
        tags_by_agent[row.agent_id].append(row.tag)
    for tags in tags_by_agent.values():
        tags.sort()
    return dict(tags_by_agent)


def _build_summary(
    assignment: AgentVirtualOrg,
    agent: Agent,
    department: VirtualDepartment,
    tags_by_agent: dict[uuid.UUID, list[str]],
) -> VirtualOrgAgentSummary:
    return VirtualOrgAgentSummary(
        id=agent.id,
        name=agent.name,
        template_id=agent.template_id,
        department_id=department.id,
        department_name=department.name,
        title=assignment.title,
        level=assignment.level,
        org_bucket=assignment.org_bucket,
        manager_agent_id=assignment.manager_agent_id,
        is_locked=assignment.is_locked,
        tags=tags_by_agent.get(agent.id, []),
    )


async def _sync_tags(db: AsyncSession, tenant_id: uuid.UUID, agent_id: uuid.UUID, tags: list[str]) -> None:
    desired = {tag for tag in tags if tag.strip()}
    result = await db.execute(
        select(AgentVirtualTag).where(AgentVirtualTag.tenant_id == tenant_id, AgentVirtualTag.agent_id == agent_id)
    )
    existing = {row.tag: row for row in result.scalars().all()}
    for tag in desired - set(existing):
        db.add(AgentVirtualTag(agent_id=agent_id, tenant_id=tenant_id, tag=tag))
    for tag in set(existing) - desired:
        await db.delete(existing[tag])


async def _validate_manager(db: AsyncSession, tenant_id: uuid.UUID, agent_id: uuid.UUID, manager_agent_id: uuid.UUID) -> None:
    if agent_id == manager_agent_id:
        raise HTTPException(status_code=400, detail="Manager cannot be self")

    agent_result = await db.execute(select(Agent).where(Agent.id == agent_id, Agent.tenant_id == tenant_id))
    manager_result = await db.execute(select(Agent).where(Agent.id == manager_agent_id, Agent.tenant_id == tenant_id))
    agent = agent_result.scalar_one_or_none()
    manager = manager_result.scalar_one_or_none()
    if agent is None or manager is None:
        raise HTTPException(status_code=400, detail="Manager assignment must stay within the same tenant")

    assignments_result = await db.execute(
        select(AgentVirtualOrg).where(AgentVirtualOrg.tenant_id == tenant_id, AgentVirtualOrg.is_primary.is_(True))
    )
    assignments = {row.agent_id: row for row in assignments_result.scalars().all()}
    if manager_agent_id not in assignments:
        raise HTTPException(status_code=400, detail="Manager requires a primary virtual org assignment")

    current = manager_agent_id
    visited: set[uuid.UUID] = set()
    while current in assignments:
        if current == agent_id:
            raise HTTPException(status_code=400, detail="Manager cycle detected")
        if current in visited:
            raise HTTPException(status_code=400, detail="Manager cycle detected")
        visited.add(current)
        next_manager = assignments[current].manager_agent_id
        if next_manager is None:
            break
        current = next_manager


@router.get("/overview", response_model=VirtualOrgOverviewOut)
async def get_virtual_org_overview(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    rows = await _primary_assignments_with_context(db, current_user)
    tags_by_agent = await _tags_by_agent_id(db, {agent.id for _, agent, _ in rows})

    executives: list[VirtualOrgAgentSummary] = []
    departments: dict[uuid.UUID, VirtualOrgDepartmentOut] = {}
    expert_agents: list[VirtualOrgAgentSummary] = []
    cross_functional: list[VirtualOrgAgentSummary] = []

    for assignment, agent, department in rows:
        summary = _build_summary(assignment, agent, department, tags_by_agent)
        if "cross-functional" in summary.tags:
            cross_functional.append(summary)
        if assignment.level == "L1" or department.slug == "executive":
            executives.append(summary)
            continue
        if assignment.org_bucket == "expert" or department.slug in {"expert-pool", "expert-unassigned"}:
            expert_agents.append(summary)
            continue

        department_out = departments.get(department.id)
        if department_out is None:
            department_out = VirtualOrgDepartmentOut(
                id=department.id,
                name=department.name,
                slug=department.slug,
                sort_order=department.sort_order,
                org_level=department.org_level,
                is_core=department.is_core,
            )
            departments[department.id] = department_out
        department_out.core_agents.append(summary)
        if department_out.leader is None and summary.level in {"L1", "L2"}:
            department_out.leader = summary

    for department in departments.values():
        department.expert_count = len(department.expert_agents)

    return VirtualOrgOverviewOut(
        executives=executives,
        departments=sorted(departments.values(), key=lambda item: (item.sort_order, item.name)),
        expert_pool=VirtualOrgExpertPoolOut(count=len(expert_agents), agents=expert_agents),
        cross_functional=cross_functional,
    )


@router.get("/departments", response_model=list[VirtualOrgDepartmentOut])
async def list_virtual_departments(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    tenant_id = _require_tenant(current_user)
    result = await db.execute(
        select(VirtualDepartment).where(VirtualDepartment.tenant_id == tenant_id).order_by(VirtualDepartment.sort_order.asc(), VirtualDepartment.name.asc())
    )
    return [
        VirtualOrgDepartmentOut(
            id=department.id,
            name=department.name,
            slug=department.slug,
            sort_order=department.sort_order,
            org_level=department.org_level,
            is_core=department.is_core,
        )
        for department in result.scalars().all()
    ]


@router.post("/departments", response_model=VirtualOrgDepartmentOut, status_code=status.HTTP_201_CREATED)
async def create_virtual_department(
    data: VirtualOrgDepartmentCreate,
    current_user: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    tenant_id = _require_tenant(current_user)
    department = VirtualDepartment(
        name=data.name,
        slug=data.slug,
        parent_id=data.parent_id,
        sort_order=data.sort_order,
        org_level=data.org_level,
        is_core=data.is_core,
        tenant_id=tenant_id,
    )
    db.add(department)
    await db.flush()
    return VirtualOrgDepartmentOut(
        id=department.id,
        name=department.name,
        slug=department.slug,
        sort_order=department.sort_order,
        org_level=department.org_level,
        is_core=department.is_core,
    )


@router.patch("/departments/{department_id}", response_model=VirtualOrgDepartmentOut)
async def update_virtual_department(
    department_id: uuid.UUID,
    data: VirtualOrgDepartmentPatch,
    current_user: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    tenant_id = _require_tenant(current_user)
    result = await db.execute(select(VirtualDepartment).where(VirtualDepartment.id == department_id, VirtualDepartment.tenant_id == tenant_id))
    department = result.scalar_one_or_none()
    if department is None:
        raise HTTPException(status_code=404, detail="Department not found")
    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(department, field, value)
    await db.flush()
    return VirtualOrgDepartmentOut(
        id=department.id,
        name=department.name,
        slug=department.slug,
        sort_order=department.sort_order,
        org_level=department.org_level,
        is_core=department.is_core,
    )


@router.get("/agents", response_model=VirtualOrgAgentListOut)
async def list_virtual_org_agents(
    department_id: uuid.UUID | None = None,
    org_bucket: str | None = None,
    page: int = 1,
    page_size: int = 50,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    rows = await _primary_assignments_with_context(db, current_user, department_id=department_id, org_bucket=org_bucket)
    total = len(rows)
    start = max(page - 1, 0) * page_size
    end = start + page_size
    page_rows = rows[start:end]
    tags_by_agent = await _tags_by_agent_id(db, {agent.id for _, agent, _ in page_rows})
    items = [_build_summary(assignment, agent, department, tags_by_agent) for assignment, agent, department in page_rows]
    return VirtualOrgAgentListOut(items=items, total=total, page=page, page_size=page_size)


@router.patch("/agents/{agent_id}", response_model=VirtualOrgAgentSummary)
async def patch_virtual_org_agent(
    agent_id: uuid.UUID,
    data: VirtualOrgAgentPatch,
    current_user: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    tenant_id = _require_tenant(current_user)
    agent_result = await db.execute(select(Agent).where(Agent.id == agent_id, Agent.tenant_id == tenant_id))
    agent = agent_result.scalar_one_or_none()
    if agent is None:
        raise HTTPException(status_code=404, detail="Agent not found")

    assignment_result = await db.execute(
        select(AgentVirtualOrg).where(AgentVirtualOrg.agent_id == agent_id, AgentVirtualOrg.is_primary.is_(True))
    )
    assignment = assignment_result.scalar_one_or_none()

    if assignment is None:
        if data.department_id is None:
            raise HTTPException(status_code=400, detail="department_id is required when creating a virtual org assignment")
        assignment = AgentVirtualOrg(
            agent_id=agent.id,
            department_id=data.department_id,
            template_id=agent.template_id,
            title=data.title or agent.name,
            level=data.level or "L3",
            org_bucket=data.org_bucket or "core",
            is_primary=True,
            is_org_primary_instance=False,
            tenant_id=tenant_id,
        )
        db.add(assignment)
    else:
        for field, value in data.model_dump(exclude_unset=True, exclude={"tags", "manager_agent_id"}).items():
            setattr(assignment, field, value)

    if data.manager_agent_id is not None:
        await _validate_manager(db, tenant_id, agent.id, data.manager_agent_id)
        assignment.manager_agent_id = data.manager_agent_id
    if data.is_locked is not None:
        assignment.is_locked = data.is_locked

    await db.flush()

    if data.tags is not None:
        await _sync_tags(db, tenant_id, agent.id, data.tags)

    department = await db.get(VirtualDepartment, assignment.department_id)
    if department is None:
        raise HTTPException(status_code=400, detail="Department not found")
    tags_by_agent = await _tags_by_agent_id(db, {agent.id})
    return _build_summary(assignment, agent, department, tags_by_agent)


@router.post("/bootstrap", response_model=VirtualOrgBootstrapResponse)
async def bootstrap_virtual_org_endpoint(
    data: VirtualOrgBootstrapRequest,
    current_user: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    tenant_id = _require_tenant(current_user)
    result = await db.run_sync(lambda sync_session: bootstrap_virtual_org(sync_session, tenant_id, force=data.force))
    return VirtualOrgBootstrapResponse.from_result(result)
