"""Project management API endpoints."""

import uuid
from datetime import datetime, timezone as tz
from typing import Optional, List

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import select, func, delete as sql_delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import get_current_user
from app.database import get_db
from app.models.agent import Agent
from app.models.org import AgentAgentRelationship
from app.models.project import Project, ProjectAgent, ProjectTag, ProjectTagLink
from app.models.user import User

router = APIRouter(prefix="/api/projects", tags=["projects"])
tags_router = APIRouter(prefix="/api/project-tags", tags=["project-tags"])

# ─── Valid transitions ──────────────────────────────────────────────────────

_TRANSITIONS = {
    "start":    {"from": {"draft"},               "to": "active"},
    "pause":    {"from": {"active"},              "to": "on_hold"},
    "resume":   {"from": {"on_hold"},             "to": "active"},
    "complete": {"from": {"active"},              "to": "completed"},
    "archive":  {"from": {"draft", "active", "on_hold", "completed"}, "to": "archived"},
}


# ─── Permission helpers ─────────────────────────────────────────────────────

def _is_admin(user: User) -> bool:
    return user.role in ("platform_admin", "org_admin")


def _can_write(user: User, project: Project) -> bool:
    return str(project.created_by) == str(user.id) or _is_admin(user)


async def _get_project_or_404(db: AsyncSession, project_id: uuid.UUID, tenant_id: uuid.UUID) -> Project:
    result = await db.execute(
        select(Project).where(Project.id == project_id, Project.tenant_id == tenant_id)
    )
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return project


# ─── Pydantic schemas ───────────────────────────────────────────────────────

class TagOut(BaseModel):
    id: str
    name: str
    color: Optional[str] = None

    class Config:
        from_attributes = True


class AgentInProjectOut(BaseModel):
    agent_id: str
    name: str
    avatar_url: Optional[str] = None
    role: str
    added_at: str

    class Config:
        from_attributes = True


class ProjectOut(BaseModel):
    id: str
    name: str
    description: Optional[str] = None
    brief: Optional[str] = None
    folder: Optional[str] = None
    status: str
    collab_mode: str
    target_completion_at: Optional[str] = None
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    created_by: str
    created_at: str
    updated_at: str
    tags: List[TagOut] = []
    agents: List[AgentInProjectOut] = []
    agent_count: int = 0

    class Config:
        from_attributes = True


class CreateProjectIn(BaseModel):
    name: str
    description: Optional[str] = None
    brief: Optional[str] = None
    folder: Optional[str] = None
    target_completion_at: Optional[str] = None
    tag_ids: Optional[List[str]] = None


class PatchProjectIn(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    brief: Optional[str] = None
    folder: Optional[str] = None
    collab_mode: Optional[str] = None
    target_completion_at: Optional[str] = None
    tag_ids: Optional[List[str]] = None


class TransitionIn(BaseModel):
    action: str
    force: bool = False


class AddAgentIn(BaseModel):
    agent_id: str
    role: str = "member"


class PatchAgentRoleIn(BaseModel):
    role: str


class SetTagsIn(BaseModel):
    tag_ids: List[str]


class CreateTagIn(BaseModel):
    name: str
    color: Optional[str] = None


class A2AGrantIn(BaseModel):
    source_agent_id: str
    target_agent_id: str


# ─── Helpers ────────────────────────────────────────────────────────────────

async def _build_project_out(db: AsyncSession, project: Project) -> ProjectOut:
    """Build ProjectOut with tags and agents."""
    # tags
    tag_links = await db.execute(
        select(ProjectTagLink.tag_id).where(ProjectTagLink.project_id == project.id)
    )
    tag_ids = [r[0] for r in tag_links.all()]
    tags: List[TagOut] = []
    if tag_ids:
        tags_res = await db.execute(select(ProjectTag).where(ProjectTag.id.in_(tag_ids)))
        tags = [TagOut(id=str(t.id), name=t.name, color=t.color) for t in tags_res.scalars().all()]

    # agents
    pa_res = await db.execute(
        select(ProjectAgent).where(ProjectAgent.project_id == project.id)
    )
    pas = pa_res.scalars().all()
    agents: List[AgentInProjectOut] = []
    for pa in pas:
        agent_res = await db.execute(select(Agent).where(Agent.id == pa.agent_id))
        agent = agent_res.scalar_one_or_none()
        if agent:
            agents.append(AgentInProjectOut(
                agent_id=str(agent.id),
                name=agent.name,
                avatar_url=agent.avatar_url,
                role=pa.role,
                added_at=pa.added_at.isoformat(),
            ))

    return ProjectOut(
        id=str(project.id),
        name=project.name,
        description=project.description,
        brief=project.brief,
        folder=project.folder,
        status=project.status,
        collab_mode=project.collab_mode,
        target_completion_at=project.target_completion_at.isoformat() if project.target_completion_at else None,
        started_at=project.started_at.isoformat() if project.started_at else None,
        completed_at=project.completed_at.isoformat() if project.completed_at else None,
        created_by=str(project.created_by),
        created_at=project.created_at.isoformat(),
        updated_at=project.updated_at.isoformat(),
        tags=tags,
        agents=agents,
        agent_count=len(agents),
    )


async def _apply_tags(db: AsyncSession, project_id: uuid.UUID, tenant_id: uuid.UUID, tag_ids: List[str]) -> None:
    """Replace all tag links for a project."""
    await db.execute(sql_delete(ProjectTagLink).where(ProjectTagLink.project_id == project_id))
    for tid_str in tag_ids:
        try:
            tid = uuid.UUID(tid_str)
        except ValueError:
            continue
        # Verify tag belongs to this tenant
        tag_res = await db.execute(select(ProjectTag).where(ProjectTag.id == tid, ProjectTag.tenant_id == tenant_id))
        if tag_res.scalar_one_or_none():
            db.add(ProjectTagLink(project_id=project_id, tag_id=tid))


# ─── Project CRUD ───────────────────────────────────────────────────────────

@router.get("")
async def list_projects(
    status: Optional[str] = Query(None),
    folder: Optional[str] = Query(None),
    tag: Optional[str] = Query(None),
    q: Optional[str] = Query(None),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List all projects visible to the current user's tenant."""
    query = select(Project).where(Project.tenant_id == current_user.tenant_id)

    if status and status != "all":
        query = query.where(Project.status == status)
    else:
        # default: hide archived
        query = query.where(Project.status != "archived")

    if folder:
        query = query.where(Project.folder == folder)

    if q:
        query = query.where(Project.name.ilike(f"%{q}%"))

    query = query.order_by(Project.updated_at.desc())

    result = await db.execute(query)
    projects = result.scalars().all()

    # Filter by tag if requested
    if tag:
        try:
            tag_uuid = uuid.UUID(tag)
        except ValueError:
            tag_uuid = None

        if tag_uuid:
            filtered = []
            for p in projects:
                link_res = await db.execute(
                    select(ProjectTagLink).where(
                        ProjectTagLink.project_id == p.id,
                        ProjectTagLink.tag_id == tag_uuid
                    )
                )
                if link_res.scalar_one_or_none():
                    filtered.append(p)
            projects = filtered

    out = []
    for p in projects:
        out.append(await _build_project_out(db, p))
    return out


@router.post("", status_code=201)
async def create_project(
    body: CreateProjectIn,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Create a new project in the current user's tenant."""
    # Check name uniqueness within tenant
    existing = await db.execute(
        select(Project).where(
            Project.tenant_id == current_user.tenant_id,
            Project.name == body.name,
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="A project with this name already exists in your workspace")

    target_dt = None
    if body.target_completion_at:
        try:
            target_dt = datetime.fromisoformat(body.target_completion_at.replace("Z", "+00:00"))
        except ValueError:
            raise HTTPException(status_code=422, detail="Invalid target_completion_at format")

    project = Project(
        id=uuid.uuid4(),
        tenant_id=current_user.tenant_id,
        created_by=current_user.id,
        name=body.name,
        description=body.description,
        brief=body.brief,
        folder=body.folder,
        status="draft",
        collab_mode="isolated",
        target_completion_at=target_dt,
    )
    db.add(project)
    await db.flush()  # get project.id

    if body.tag_ids:
        await _apply_tags(db, project.id, current_user.tenant_id, body.tag_ids)

    await db.commit()
    await db.refresh(project)
    return await _build_project_out(db, project)


@router.get("/folders")
async def list_folders(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Return distinct folder names used in this tenant's projects."""
    result = await db.execute(
        select(Project.folder)
        .where(Project.tenant_id == current_user.tenant_id, Project.folder.isnot(None))
        .distinct()
        .order_by(Project.folder)
    )
    folders = [r[0] for r in result.all() if r[0]]
    return folders


@router.get("/{project_id}")
async def get_project(
    project_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    project = await _get_project_or_404(db, project_id, current_user.tenant_id)
    return await _build_project_out(db, project)


@router.patch("/{project_id}")
async def update_project(
    project_id: uuid.UUID,
    body: PatchProjectIn,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    project = await _get_project_or_404(db, project_id, current_user.tenant_id)
    if not _can_write(current_user, project):
        raise HTTPException(status_code=403, detail="Only the project creator or admin can edit this project")

    if body.name is not None:
        # Check uniqueness
        dup = await db.execute(
            select(Project).where(
                Project.tenant_id == current_user.tenant_id,
                Project.name == body.name,
                Project.id != project_id,
            )
        )
        if dup.scalar_one_or_none():
            raise HTTPException(status_code=409, detail="A project with this name already exists")
        project.name = body.name

    if body.description is not None:
        project.description = body.description
    if body.brief is not None:
        project.brief = body.brief
    if body.folder is not None:
        project.folder = body.folder or None
    if body.collab_mode is not None:
        if body.collab_mode not in ("isolated", "group_chat", "lead_helper"):
            raise HTTPException(status_code=422, detail="Invalid collab_mode")
        project.collab_mode = body.collab_mode
    if body.target_completion_at is not None:
        if body.target_completion_at == "":
            project.target_completion_at = None
        else:
            try:
                project.target_completion_at = datetime.fromisoformat(
                    body.target_completion_at.replace("Z", "+00:00")
                )
            except ValueError:
                raise HTTPException(status_code=422, detail="Invalid target_completion_at format")

    if body.tag_ids is not None:
        await _apply_tags(db, project.id, current_user.tenant_id, body.tag_ids)

    await db.commit()
    await db.refresh(project)
    return await _build_project_out(db, project)


@router.delete("/{project_id}", status_code=204)
async def delete_project(
    project_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Delete a project. Only archived projects can be deleted."""
    project = await _get_project_or_404(db, project_id, current_user.tenant_id)
    if not _can_write(current_user, project):
        raise HTTPException(status_code=403, detail="Only the project creator or admin can delete this project")
    if project.status != "archived":
        raise HTTPException(status_code=409, detail="Only archived projects can be deleted. Archive it first.")

    # Cascade deletes project_agents, project_tag_links via FK
    await db.execute(sql_delete(ProjectTagLink).where(ProjectTagLink.project_id == project_id))
    await db.execute(sql_delete(ProjectAgent).where(ProjectAgent.project_id == project_id))
    await db.delete(project)
    await db.commit()
    return None


@router.post("/{project_id}/transition")
async def transition_project(
    project_id: uuid.UUID,
    body: TransitionIn,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Push a project through its state machine."""
    project = await _get_project_or_404(db, project_id, current_user.tenant_id)
    if not _can_write(current_user, project):
        raise HTTPException(status_code=403, detail="Not authorized to change project status")

    rule = _TRANSITIONS.get(body.action)
    if not rule:
        raise HTTPException(status_code=422, detail=f"Unknown action '{body.action}'")
    if project.status not in rule["from"]:
        raise HTTPException(
            status_code=409,
            detail=f"Cannot '{body.action}' a project that is '{project.status}'"
        )

    now = datetime.now(tz.utc)
    new_status = rule["to"]

    project.status = new_status
    if new_status == "active" and not project.started_at:
        project.started_at = now
    if new_status == "completed":
        project.completed_at = now

    await db.commit()
    await db.refresh(project)
    return await _build_project_out(db, project)


# ─── Agents in project ──────────────────────────────────────────────────────

@router.get("/{project_id}/agents")
async def list_project_agents(
    project_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await _get_project_or_404(db, project_id, current_user.tenant_id)
    pa_res = await db.execute(select(ProjectAgent).where(ProjectAgent.project_id == project_id))
    pas = pa_res.scalars().all()
    out = []
    for pa in pas:
        agent_res = await db.execute(select(Agent).where(Agent.id == pa.agent_id))
        agent = agent_res.scalar_one_or_none()
        if agent:
            out.append(AgentInProjectOut(
                agent_id=str(agent.id),
                name=agent.name,
                avatar_url=agent.avatar_url,
                role=pa.role,
                added_at=pa.added_at.isoformat(),
            ))
    return out


@router.post("/{project_id}/agents", status_code=201)
async def add_agent_to_project(
    project_id: uuid.UUID,
    body: AddAgentIn,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    project = await _get_project_or_404(db, project_id, current_user.tenant_id)
    if not _can_write(current_user, project):
        raise HTTPException(status_code=403, detail="Not authorized")

    try:
        agent_uuid = uuid.UUID(body.agent_id)
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid agent_id")

    # Verify agent exists and belongs to same tenant
    agent_res = await db.execute(
        select(Agent).where(Agent.id == agent_uuid, Agent.tenant_id == current_user.tenant_id)
    )
    agent = agent_res.scalar_one_or_none()
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")

    # Check not already in project
    existing = await db.execute(
        select(ProjectAgent).where(
            ProjectAgent.project_id == project_id,
            ProjectAgent.agent_id == agent_uuid,
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Agent is already in this project")

    if body.role not in ("lead", "member", "observer"):
        raise HTTPException(status_code=422, detail="role must be 'lead', 'member', or 'observer'")

    pa = ProjectAgent(
        project_id=project_id,
        agent_id=agent_uuid,
        role=body.role,
        added_by=current_user.id,
    )
    db.add(pa)
    await db.commit()
    return AgentInProjectOut(
        agent_id=str(agent.id),
        name=agent.name,
        avatar_url=agent.avatar_url,
        role=pa.role,
        added_at=pa.added_at.isoformat() if pa.added_at else datetime.now(tz.utc).isoformat(),
    )


@router.patch("/{project_id}/agents/{agent_id}")
async def update_agent_role(
    project_id: uuid.UUID,
    agent_id: uuid.UUID,
    body: PatchAgentRoleIn,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    project = await _get_project_or_404(db, project_id, current_user.tenant_id)
    if not _can_write(current_user, project):
        raise HTTPException(status_code=403, detail="Not authorized")
    if body.role not in ("lead", "member", "observer"):
        raise HTTPException(status_code=422, detail="Invalid role")

    pa_res = await db.execute(
        select(ProjectAgent).where(
            ProjectAgent.project_id == project_id,
            ProjectAgent.agent_id == agent_id,
        )
    )
    pa = pa_res.scalar_one_or_none()
    if not pa:
        raise HTTPException(status_code=404, detail="Agent not in this project")

    pa.role = body.role
    await db.commit()
    return {"agent_id": str(agent_id), "role": pa.role}


@router.delete("/{project_id}/agents/{agent_id}", status_code=204)
async def remove_agent_from_project(
    project_id: uuid.UUID,
    agent_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    project = await _get_project_or_404(db, project_id, current_user.tenant_id)
    if not _can_write(current_user, project):
        raise HTTPException(status_code=403, detail="Not authorized")

    pa_res = await db.execute(
        select(ProjectAgent).where(
            ProjectAgent.project_id == project_id,
            ProjectAgent.agent_id == agent_id,
        )
    )
    pa = pa_res.scalar_one_or_none()
    if not pa:
        raise HTTPException(status_code=404, detail="Agent not in this project")

    await db.delete(pa)
    await db.commit()
    return None


# ─── A2A matrix ─────────────────────────────────────────────────────────────

@router.get("/{project_id}/a2a-matrix")
async def get_a2a_matrix(
    project_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Return authorisation status for every agent pair in this project."""
    await _get_project_or_404(db, project_id, current_user.tenant_id)

    pa_res = await db.execute(select(ProjectAgent).where(ProjectAgent.project_id == project_id))
    agent_ids = [pa.agent_id for pa in pa_res.scalars().all()]

    matrix = []
    for i, src in enumerate(agent_ids):
        for dst in agent_ids[i + 1:]:
            fwd = await db.execute(
                select(AgentAgentRelationship).where(
                    AgentAgentRelationship.agent_id == src,
                    AgentAgentRelationship.target_agent_id == dst,
                )
            )
            rev = await db.execute(
                select(AgentAgentRelationship).where(
                    AgentAgentRelationship.agent_id == dst,
                    AgentAgentRelationship.target_agent_id == src,
                )
            )
            matrix.append({
                "source_agent_id": str(src),
                "target_agent_id": str(dst),
                "forward_authorized": fwd.scalar_one_or_none() is not None,
                "reverse_authorized": rev.scalar_one_or_none() is not None,
            })
    return matrix


@router.post("/{project_id}/a2a-grant", status_code=201)
async def grant_a2a(
    project_id: uuid.UUID,
    body: A2AGrantIn,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Grant A2A communication between two agents in this project."""
    project = await _get_project_or_404(db, project_id, current_user.tenant_id)
    if not _can_write(current_user, project):
        raise HTTPException(status_code=403, detail="Not authorized")

    try:
        src = uuid.UUID(body.source_agent_id)
        dst = uuid.UUID(body.target_agent_id)
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid agent IDs")

    for a_id, b_id in [(src, dst), (dst, src)]:
        existing = await db.execute(
            select(AgentAgentRelationship).where(
                AgentAgentRelationship.agent_id == a_id,
                AgentAgentRelationship.target_agent_id == b_id,
            )
        )
        if not existing.scalar_one_or_none():
            db.add(AgentAgentRelationship(
                agent_id=a_id,
                target_agent_id=b_id,
                relation="collaborator",
                description=f"Granted via project {project_id}",
            ))

    await db.commit()
    return {"granted": True, "source_agent_id": str(src), "target_agent_id": str(dst)}


# ─── Tags CRUD ───────────────────────────────────────────────────────────────

@tags_router.get("")
async def list_tags(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(ProjectTag)
        .where(ProjectTag.tenant_id == current_user.tenant_id)
        .order_by(ProjectTag.name)
    )
    tags = result.scalars().all()
    return [TagOut(id=str(t.id), name=t.name, color=t.color) for t in tags]


@tags_router.post("", status_code=201)
async def create_tag(
    body: CreateTagIn,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    existing = await db.execute(
        select(ProjectTag).where(
            ProjectTag.tenant_id == current_user.tenant_id,
            ProjectTag.name == body.name,
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Tag already exists")

    tag = ProjectTag(
        id=uuid.uuid4(),
        tenant_id=current_user.tenant_id,
        name=body.name,
        color=body.color,
    )
    db.add(tag)
    await db.commit()
    await db.refresh(tag)
    return TagOut(id=str(tag.id), name=tag.name, color=tag.color)


@tags_router.delete("/{tag_id}", status_code=204)
async def delete_tag(
    tag_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(ProjectTag).where(
            ProjectTag.id == tag_id,
            ProjectTag.tenant_id == current_user.tenant_id,
        )
    )
    tag = result.scalar_one_or_none()
    if not tag:
        raise HTTPException(status_code=404, detail="Tag not found")

    await db.execute(sql_delete(ProjectTagLink).where(ProjectTagLink.tag_id == tag_id))
    await db.delete(tag)
    await db.commit()
    return None


# ─── Update tags on a project ─────────────────────────────────────────────

@router.post("/{project_id}/tags")
async def set_project_tags(
    project_id: uuid.UUID,
    body: SetTagsIn,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    project = await _get_project_or_404(db, project_id, current_user.tenant_id)
    if not _can_write(current_user, project):
        raise HTTPException(status_code=403, detail="Not authorized")

    await _apply_tags(db, project.id, current_user.tenant_id, body.tag_ids)
    await db.commit()
    return {"project_id": str(project_id), "tag_ids": body.tag_ids}
