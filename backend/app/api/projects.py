"""Project management API endpoints."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone as tz
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import delete as sql_delete
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import get_current_user
from app.database import get_db
from app.models.agent import Agent
from app.models.org import AgentAgentRelationship
from app.models.project import (
    Project,
    ProjectActivity,
    ProjectAgent,
    ProjectDecision,
    ProjectTag,
    ProjectTagLink,
    ProjectTask,
)
from app.models.user import User
from app.services.project_service import (
    PROJECT_TASK_ALLOWED_PRIORITIES,
    PROJECT_TASK_ALLOWED_STATUSES,
    PROJECT_TASK_TERMINAL_STATUSES,
    create_project_activity,
    get_project_brief_prompt,
)

router = APIRouter(prefix="/api/projects", tags=["projects"])
tags_router = APIRouter(prefix="/api/project-tags", tags=["project-tags"])

_TRANSITIONS = {
    "start": {"from": {"draft"}, "to": "active"},
    "pause": {"from": {"active"}, "to": "on_hold"},
    "resume": {"from": {"on_hold"}, "to": "active"},
    "complete": {"from": {"active"}, "to": "completed"},
    "archive": {"from": {"draft", "active", "on_hold", "completed"}, "to": "archived"},
}


def _is_admin(user: User) -> bool:
    return user.role in ("platform_admin", "org_admin")


def _can_write(user: User, project: Project) -> bool:
    return str(project.created_by) == str(user.id) or _is_admin(user)


def _ensure_not_archived(project: Project) -> None:
    if project.status == "archived":
        raise HTTPException(status_code=409, detail="Archived projects are read-only")


def _parse_optional_datetime(value: Optional[str], field_name: str) -> datetime | None:
    if value in (None, ""):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"Invalid {field_name} format") from exc


async def _get_project_or_404(db: AsyncSession, project_id: uuid.UUID, tenant_id: uuid.UUID) -> Project:
    result = await db.execute(
        select(Project).where(Project.id == project_id, Project.tenant_id == tenant_id)
    )
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return project


async def _get_project_decision_or_404(
    db: AsyncSession,
    *,
    project_id: uuid.UUID,
    decision_id: uuid.UUID,
) -> ProjectDecision:
    result = await db.execute(
        select(ProjectDecision).where(
            ProjectDecision.project_id == project_id,
            ProjectDecision.id == decision_id,
        )
    )
    decision = result.scalar_one_or_none()
    if not decision:
        raise HTTPException(status_code=404, detail="Decision not found")
    return decision


async def _get_actor_name(
    db: AsyncSession,
    *,
    actor_type: str,
    actor_id: uuid.UUID | None,
) -> str | None:
    if not actor_id:
        return None
    if actor_type == "user":
        result = await db.execute(select(User).where(User.id == actor_id))
        user = result.scalar_one_or_none()
        if user:
            return user.display_name or user.username
    if actor_type == "agent":
        result = await db.execute(select(Agent).where(Agent.id == actor_id))
        agent = result.scalar_one_or_none()
        if agent:
            return agent.name
    return None


async def _get_project_agents_lookup(
    db: AsyncSession,
    project_id: uuid.UUID,
) -> dict[uuid.UUID, Agent]:
    pa_result = await db.execute(
        select(ProjectAgent.agent_id).where(ProjectAgent.project_id == project_id)
    )
    agent_ids = [row[0] for row in pa_result.all()]
    if not agent_ids:
        return {}
    result = await db.execute(select(Agent).where(Agent.id.in_(agent_ids)))
    agents = result.scalars().all()
    return {agent.id: agent for agent in agents}


async def _get_project_tags(db: AsyncSession, project_id: uuid.UUID) -> list["TagOut"]:
    tag_links = await db.execute(
        select(ProjectTagLink.tag_id).where(ProjectTagLink.project_id == project_id)
    )
    tag_ids = [row[0] for row in tag_links.all()]
    if not tag_ids:
        return []
    tags_res = await db.execute(
        select(ProjectTag).where(ProjectTag.id.in_(tag_ids)).order_by(ProjectTag.name)
    )
    return [
        TagOut(id=str(tag.id), name=tag.name, color=tag.color)
        for tag in tags_res.scalars().all()
    ]


async def _get_project_agents(
    db: AsyncSession,
    project_id: uuid.UUID,
) -> list["AgentInProjectOut"]:
    lookup = await _get_project_agents_lookup(db, project_id)
    pa_result = await db.execute(
        select(ProjectAgent)
        .where(ProjectAgent.project_id == project_id)
        .order_by(ProjectAgent.added_at.asc())
    )
    items: list[AgentInProjectOut] = []
    for membership in pa_result.scalars().all():
        agent = lookup.get(membership.agent_id)
        if not agent:
            continue
        items.append(
            AgentInProjectOut(
                agent_id=str(agent.id),
                name=agent.name,
                avatar_url=agent.avatar_url,
                role=membership.role,
                added_at=membership.added_at.isoformat() if membership.added_at else datetime.now(tz.utc).isoformat(),
            )
        )
    return items


async def _get_task_stats(
    db: AsyncSession,
    project_id: uuid.UUID,
) -> tuple[int, int, int, float]:
    result = await db.execute(
        select(ProjectTask.status).where(ProjectTask.project_id == project_id)
    )
    statuses = [row[0] for row in result.all()]
    total = len(statuses)
    completed = sum(1 for task_status in statuses if task_status in PROJECT_TASK_TERMINAL_STATUSES)
    open_count = total - completed
    ratio = 0.0 if total == 0 else round(completed / total, 4)
    return total, completed, open_count, ratio


async def _build_project_out(db: AsyncSession, project: Project) -> "ProjectOut":
    tags = await _get_project_tags(db, project.id)
    agents = await _get_project_agents(db, project.id)
    task_count, task_completed_count, task_open_count, completion_ratio = await _get_task_stats(db, project.id)

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
        created_at=project.created_at.isoformat() if project.created_at else None,
        updated_at=project.updated_at.isoformat() if project.updated_at else None,
        tags=tags,
        agents=agents,
        agent_count=len(agents),
        task_count=task_count,
        task_completed_count=task_completed_count,
        task_open_count=task_open_count,
        completion_ratio=completion_ratio,
    )


async def _apply_tags(
    db: AsyncSession,
    *,
    project_id: uuid.UUID,
    tenant_id: uuid.UUID,
    tag_ids: list[str],
) -> list[str]:
    await db.execute(sql_delete(ProjectTagLink).where(ProjectTagLink.project_id == project_id))
    applied: list[str] = []
    for raw_tag_id in tag_ids:
        try:
            tag_id = uuid.UUID(raw_tag_id)
        except ValueError:
            continue
        tag_result = await db.execute(
            select(ProjectTag).where(
                ProjectTag.id == tag_id,
                ProjectTag.tenant_id == tenant_id,
            )
        )
        tag = tag_result.scalar_one_or_none()
        if not tag:
            continue
        db.add(ProjectTagLink(project_id=project_id, tag_id=tag_id))
        applied.append(str(tag_id))
    return applied


async def _validate_task_assignees(
    db: AsyncSession,
    *,
    project_id: uuid.UUID,
    tenant_id: uuid.UUID,
    assignee_agent_ids: list[str] | None,
) -> list[uuid.UUID]:
    if not assignee_agent_ids:
        return []

    parsed_ids: list[uuid.UUID] = []
    for raw_agent_id in assignee_agent_ids:
        try:
            parsed_ids.append(uuid.UUID(raw_agent_id))
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=f"Invalid assignee_agent_id: {raw_agent_id}") from exc

    lookup = await _get_project_agents_lookup(db, project_id)
    missing = [agent_id for agent_id in parsed_ids if agent_id not in lookup]
    if missing:
        raise HTTPException(
            status_code=422,
            detail="Assignees must already be part of this project",
        )

    tenant_result = await db.execute(
        select(func.count(Agent.id)).where(
            Agent.id.in_(parsed_ids),
            Agent.tenant_id == tenant_id,
        )
    )
    if (tenant_result.scalar() or 0) != len(parsed_ids):
        raise HTTPException(status_code=422, detail="One or more assignees do not belong to this workspace")

    return parsed_ids


async def _build_task_out(
    db: AsyncSession,
    task: ProjectTask,
    *,
    agent_lookup: dict[uuid.UUID, Agent] | None = None,
) -> "ProjectTaskOut":
    if agent_lookup is None:
        agent_lookup = await _get_project_agents_lookup(db, task.project_id)

    assignee_ids = [str(agent_id) for agent_id in (task.assignee_agent_ids or [])]
    assignees = []
    for agent_id in task.assignee_agent_ids or []:
        agent = agent_lookup.get(agent_id)
        if agent:
            assignees.append(
                TaskAssigneeOut(
                    agent_id=str(agent.id),
                    name=agent.name,
                    avatar_url=agent.avatar_url,
                )
            )

    return ProjectTaskOut(
        id=str(task.id),
        title=task.title,
        goal=task.goal,
        acceptance_criteria=task.acceptance_criteria,
        due_at=task.due_at.isoformat() if task.due_at else None,
        priority=task.priority,
        status=task.status,
        assignee_agent_ids=assignee_ids,
        assignees=assignees,
        created_by=str(task.created_by),
        sort_order=task.sort_order,
        created_at=task.created_at.isoformat() if task.created_at else None,
        updated_at=task.updated_at.isoformat() if task.updated_at else None,
        completed_at=task.completed_at.isoformat() if task.completed_at else None,
    )


async def _build_activity_out(
    db: AsyncSession,
    activity: ProjectActivity,
) -> "ProjectActivityOut":
    return ProjectActivityOut(
        id=str(activity.id),
        event=activity.event,
        actor_type=activity.actor_type,
        actor_id=str(activity.actor_id) if activity.actor_id else None,
        actor_name=await _get_actor_name(db, actor_type=activity.actor_type, actor_id=activity.actor_id),
        payload=activity.payload or {},
        created_at=activity.created_at.isoformat() if activity.created_at else None,
    )


async def _build_decision_out(
    db: AsyncSession,
    decision: ProjectDecision,
) -> "ProjectDecisionOut":
    return ProjectDecisionOut(
        id=str(decision.id),
        title=decision.title,
        content=decision.content,
        created_by=str(decision.created_by) if decision.created_by else None,
        created_by_name=await _get_actor_name(db, actor_type="user", actor_id=decision.created_by),
        created_at=decision.created_at.isoformat() if decision.created_at else None,
        updated_at=decision.updated_at.isoformat() if decision.updated_at else None,
    )


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


class AvailableProjectAgentOut(BaseModel):
    id: str
    name: str
    avatar_url: Optional[str] = None


class TaskAssigneeOut(BaseModel):
    agent_id: str
    name: str
    avatar_url: Optional[str] = None


class ProjectTaskOut(BaseModel):
    id: str
    title: str
    goal: Optional[str] = None
    acceptance_criteria: Optional[str] = None
    due_at: Optional[str] = None
    priority: str
    status: str
    assignee_agent_ids: list[str] = Field(default_factory=list)
    assignees: list[TaskAssigneeOut] = Field(default_factory=list)
    created_by: str
    sort_order: int
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    completed_at: Optional[str] = None


class ProjectActivityOut(BaseModel):
    id: str
    event: str
    actor_type: str
    actor_id: Optional[str] = None
    actor_name: Optional[str] = None
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: Optional[str] = None


class ProjectDecisionOut(BaseModel):
    id: str
    title: str
    content: Optional[str] = None
    created_by: Optional[str] = None
    created_by_name: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class ProjectBriefPromptOut(BaseModel):
    project_id: str
    prompt: str


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
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    tags: list[TagOut] = Field(default_factory=list)
    agents: list[AgentInProjectOut] = Field(default_factory=list)
    agent_count: int = 0
    task_count: int = 0
    task_completed_count: int = 0
    task_open_count: int = 0
    completion_ratio: float = 0.0

    class Config:
        from_attributes = True


class CreateProjectIn(BaseModel):
    name: str
    description: Optional[str] = None
    brief: Optional[str] = None
    folder: Optional[str] = None
    target_completion_at: Optional[str] = None
    tag_ids: Optional[list[str]] = None


class PatchProjectIn(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    brief: Optional[str] = None
    folder: Optional[str] = None
    collab_mode: Optional[str] = None
    target_completion_at: Optional[str] = None
    tag_ids: Optional[list[str]] = None


class TransitionIn(BaseModel):
    action: str
    force: bool = False


class AddAgentIn(BaseModel):
    agent_id: str
    role: str = "member"


class PatchAgentRoleIn(BaseModel):
    role: str


class SetTagsIn(BaseModel):
    tag_ids: list[str]


class CreateTagIn(BaseModel):
    name: str
    color: Optional[str] = None


class A2AGrantIn(BaseModel):
    source_agent_id: str
    target_agent_id: str


class CreateProjectTaskIn(BaseModel):
    title: str = Field(..., max_length=200)
    goal: Optional[str] = None
    acceptance_criteria: Optional[str] = None
    due_at: Optional[str] = None
    priority: str = "normal"
    status: str = "todo"
    assignee_agent_ids: list[str] = Field(default_factory=list)


class PatchProjectTaskIn(BaseModel):
    title: Optional[str] = Field(None, max_length=200)
    goal: Optional[str] = None
    acceptance_criteria: Optional[str] = None
    due_at: Optional[str] = None
    priority: Optional[str] = None
    status: Optional[str] = None
    assignee_agent_ids: Optional[list[str]] = None


class ReorderProjectTasksIn(BaseModel):
    ordered_ids: list[str]


class CreateProjectDecisionIn(BaseModel):
    title: str = Field(..., max_length=200)
    content: Optional[str] = None


class PatchProjectDecisionIn(BaseModel):
    title: Optional[str] = Field(None, max_length=200)
    content: Optional[str] = None


@router.get("")
async def list_projects(
    status: Optional[str] = Query(None),
    folder: Optional[str] = Query(None),
    tag: Optional[str] = Query(None),
    q: Optional[str] = Query(None),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    query = select(Project).where(Project.tenant_id == current_user.tenant_id)

    if status and status != "all":
        query = query.where(Project.status == status)
    else:
        query = query.where(Project.status != "archived")

    if folder:
        query = query.where(Project.folder == folder)

    if q:
        query = query.where(
            or_(
                Project.name.ilike(f"%{q}%"),
                Project.description.ilike(f"%{q}%"),
            )
        )

    if tag:
        try:
            tag_id = uuid.UUID(tag)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="Invalid tag filter") from exc
        query = query.join(ProjectTagLink, ProjectTagLink.project_id == Project.id).where(ProjectTagLink.tag_id == tag_id)

    query = query.order_by(Project.updated_at.desc())
    result = await db.execute(query)
    projects = result.scalars().unique().all()
    return [await _build_project_out(db, project) for project in projects]


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_project(
    body: CreateProjectIn,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    existing = await db.execute(
        select(Project).where(
            Project.tenant_id == current_user.tenant_id,
            Project.name == body.name,
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="A project with this name already exists in your workspace")

    project = Project(
        tenant_id=current_user.tenant_id,
        created_by=current_user.id,
        name=body.name,
        description=body.description,
        brief=body.brief,
        folder=body.folder,
        status="draft",
        collab_mode="isolated",
        target_completion_at=_parse_optional_datetime(body.target_completion_at, "target_completion_at"),
    )
    db.add(project)
    await db.flush()

    applied_tag_ids: list[str] = []
    if body.tag_ids:
        applied_tag_ids = await _apply_tags(
            db,
            project_id=project.id,
            tenant_id=current_user.tenant_id,
            tag_ids=body.tag_ids,
        )

    await create_project_activity(
        db,
        project_id=project.id,
        event="project.created",
        actor_type="user",
        actor_id=current_user.id,
        payload={
            "project_name": project.name,
            "folder": project.folder,
            "tag_ids": applied_tag_ids,
        },
    )

    await db.commit()
    await db.refresh(project)
    return await _build_project_out(db, project)


@router.get("/folders")
async def list_folders(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Project.folder)
        .where(Project.tenant_id == current_user.tenant_id, Project.folder.isnot(None))
        .distinct()
        .order_by(Project.folder)
    )
    return [row[0] for row in result.all() if row[0]]


@router.get("/{project_id}")
async def get_project(
    project_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    project = await _get_project_or_404(db, project_id, current_user.tenant_id)
    return await _build_project_out(db, project)


@router.get("/{project_id}/brief-prompt")
async def get_project_brief_prompt_endpoint(
    project_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    prompt = await get_project_brief_prompt(
        db,
        project_id=project_id,
        tenant_id=current_user.tenant_id,
    )
    if not prompt:
        raise HTTPException(status_code=404, detail="Project not found")
    return ProjectBriefPromptOut(project_id=str(project_id), prompt=prompt)


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
    _ensure_not_archived(project)

    changed_fields: list[str] = []

    if body.name is not None and body.name != project.name:
        duplicate = await db.execute(
            select(Project).where(
                Project.tenant_id == current_user.tenant_id,
                Project.name == body.name,
                Project.id != project_id,
            )
        )
        if duplicate.scalar_one_or_none():
            raise HTTPException(status_code=409, detail="A project with this name already exists")
        project.name = body.name
        changed_fields.append("name")

    if body.description is not None and body.description != project.description:
        project.description = body.description
        changed_fields.append("description")
    if body.brief is not None and body.brief != project.brief:
        project.brief = body.brief
        changed_fields.append("brief")
    if body.folder is not None and (body.folder or None) != project.folder:
        project.folder = body.folder or None
        changed_fields.append("folder")
    if body.collab_mode is not None:
        if body.collab_mode not in ("isolated", "group_chat", "lead_helper"):
            raise HTTPException(status_code=422, detail="Invalid collab_mode")
        if body.collab_mode != project.collab_mode:
            project.collab_mode = body.collab_mode
            changed_fields.append("collab_mode")
    if body.target_completion_at is not None:
        parsed_target = _parse_optional_datetime(body.target_completion_at, "target_completion_at")
        if parsed_target != project.target_completion_at:
            project.target_completion_at = parsed_target
            changed_fields.append("target_completion_at")

    if body.tag_ids is not None:
        applied_tag_ids = await _apply_tags(
            db,
            project_id=project.id,
            tenant_id=current_user.tenant_id,
            tag_ids=body.tag_ids,
        )
        changed_fields.append("tags")
        await create_project_activity(
            db,
            project_id=project.id,
            event="project.tags_updated",
            actor_type="user",
            actor_id=current_user.id,
            payload={"tag_ids": applied_tag_ids},
        )

    if changed_fields:
        await create_project_activity(
            db,
            project_id=project.id,
            event="project.updated",
            actor_type="user",
            actor_id=current_user.id,
            payload={"changed_fields": changed_fields},
        )

    await db.commit()
    await db.refresh(project)
    return await _build_project_out(db, project)


@router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_project(
    project_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    project = await _get_project_or_404(db, project_id, current_user.tenant_id)
    if not _can_write(current_user, project):
        raise HTTPException(status_code=403, detail="Only the project creator or admin can delete this project")
    if project.status != "archived":
        raise HTTPException(status_code=409, detail="Only archived projects can be deleted. Archive it first.")

    await db.execute(sql_delete(ProjectTagLink).where(ProjectTagLink.project_id == project_id))
    await db.execute(sql_delete(ProjectAgent).where(ProjectAgent.project_id == project_id))
    await db.execute(sql_delete(ProjectTask).where(ProjectTask.project_id == project_id))
    await db.execute(sql_delete(ProjectActivity).where(ProjectActivity.project_id == project_id))
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
    project = await _get_project_or_404(db, project_id, current_user.tenant_id)
    if not _can_write(current_user, project):
        raise HTTPException(status_code=403, detail="Not authorized to change project status")

    rule = _TRANSITIONS.get(body.action)
    if not rule:
        raise HTTPException(status_code=422, detail=f"Unknown action '{body.action}'")
    if project.status not in rule["from"]:
        raise HTTPException(
            status_code=409,
            detail=f"Cannot '{body.action}' a project that is '{project.status}'",
        )

    if body.action == "complete":
        pending_result = await db.execute(
            select(func.count(ProjectTask.id)).where(
                ProjectTask.project_id == project_id,
                ProjectTask.status.notin_(PROJECT_TASK_TERMINAL_STATUSES),
            )
        )
        pending_count = pending_result.scalar() or 0
        if pending_count and not body.force:
            raise HTTPException(
                status_code=409,
                detail={
                    "message": f"This project still has {pending_count} incomplete deliverable(s). Complete or cancel them first, or retry with force=true.",
                    "pending_task_count": pending_count,
                },
            )

    previous_status = project.status
    now = datetime.now(tz.utc)
    new_status = rule["to"]

    project.status = new_status
    if new_status == "active" and not project.started_at:
        project.started_at = now
    if new_status == "completed":
        project.completed_at = now

    await create_project_activity(
        db,
        project_id=project.id,
        event="project.transitioned",
        actor_type="user",
        actor_id=current_user.id,
        payload={
            "action": body.action,
            "from_status": previous_status,
            "to_status": new_status,
            "forced": body.force,
        },
    )

    await db.commit()
    await db.refresh(project)
    return await _build_project_out(db, project)


@router.get("/{project_id}/agents")
async def list_project_agents(
    project_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await _get_project_or_404(db, project_id, current_user.tenant_id)
    return await _get_project_agents(db, project_id)


@router.get("/{project_id}/available-agents")
async def list_available_project_agents(
    project_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    project = await _get_project_or_404(db, project_id, current_user.tenant_id)
    if not _can_write(current_user, project):
        raise HTTPException(status_code=403, detail="Not authorized")

    result = await db.execute(
        select(Agent)
        .where(Agent.tenant_id == current_user.tenant_id)
        .order_by(Agent.created_at.desc())
    )
    agents = result.scalars().all()
    return [
        AvailableProjectAgentOut(
            id=str(agent.id),
            name=agent.name,
            avatar_url=agent.avatar_url,
        )
        for agent in agents
    ]


@router.post("/{project_id}/agents", status_code=status.HTTP_201_CREATED)
async def add_agent_to_project(
    project_id: uuid.UUID,
    body: AddAgentIn,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    project = await _get_project_or_404(db, project_id, current_user.tenant_id)
    if not _can_write(current_user, project):
        raise HTTPException(status_code=403, detail="Not authorized")
    _ensure_not_archived(project)

    if body.role not in ("lead", "member", "observer"):
        raise HTTPException(status_code=422, detail="role must be 'lead', 'member', or 'observer'")

    try:
        agent_id = uuid.UUID(body.agent_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Invalid agent_id") from exc

    agent_result = await db.execute(
        select(Agent).where(Agent.id == agent_id, Agent.tenant_id == current_user.tenant_id)
    )
    agent = agent_result.scalar_one_or_none()
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")

    existing = await db.execute(
        select(ProjectAgent).where(
            ProjectAgent.project_id == project_id,
            ProjectAgent.agent_id == agent_id,
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Agent is already in this project")

    membership = ProjectAgent(
        project_id=project_id,
        agent_id=agent_id,
        role=body.role,
        added_by=current_user.id,
    )
    db.add(membership)
    await db.flush()

    await create_project_activity(
        db,
        project_id=project.id,
        event="agent.added",
        actor_type="user",
        actor_id=current_user.id,
        payload={
            "agent_id": str(agent.id),
            "agent_name": agent.name,
            "role": body.role,
        },
    )

    await db.commit()
    await db.refresh(membership)
    return AgentInProjectOut(
        agent_id=str(agent.id),
        name=agent.name,
        avatar_url=agent.avatar_url,
        role=membership.role,
        added_at=membership.added_at.isoformat() if membership.added_at else datetime.now(tz.utc).isoformat(),
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
    _ensure_not_archived(project)

    if body.role not in ("lead", "member", "observer"):
        raise HTTPException(status_code=422, detail="Invalid role")

    result = await db.execute(
        select(ProjectAgent).where(
            ProjectAgent.project_id == project_id,
            ProjectAgent.agent_id == agent_id,
        )
    )
    membership = result.scalar_one_or_none()
    if not membership:
        raise HTTPException(status_code=404, detail="Agent not in this project")

    if membership.role != body.role:
        previous_role = membership.role
        membership.role = body.role
        agent_result = await db.execute(select(Agent).where(Agent.id == agent_id))
        agent = agent_result.scalar_one_or_none()
        await create_project_activity(
            db,
            project_id=project.id,
            event="agent.role_changed",
            actor_type="user",
            actor_id=current_user.id,
            payload={
                "agent_id": str(agent_id),
                "agent_name": agent.name if agent else None,
                "from_role": previous_role,
                "to_role": body.role,
            },
        )

    await db.commit()
    return {"agent_id": str(agent_id), "role": membership.role}


@router.delete("/{project_id}/agents/{agent_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_agent_from_project(
    project_id: uuid.UUID,
    agent_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    project = await _get_project_or_404(db, project_id, current_user.tenant_id)
    if not _can_write(current_user, project):
        raise HTTPException(status_code=403, detail="Not authorized")
    _ensure_not_archived(project)

    result = await db.execute(
        select(ProjectAgent).where(
            ProjectAgent.project_id == project_id,
            ProjectAgent.agent_id == agent_id,
        )
    )
    membership = result.scalar_one_or_none()
    if not membership:
        raise HTTPException(status_code=404, detail="Agent not in this project")

    agent_result = await db.execute(select(Agent).where(Agent.id == agent_id))
    agent = agent_result.scalar_one_or_none()

    await create_project_activity(
        db,
        project_id=project.id,
        event="agent.removed",
        actor_type="user",
        actor_id=current_user.id,
        payload={
            "agent_id": str(agent_id),
            "agent_name": agent.name if agent else None,
        },
    )
    await db.delete(membership)
    await db.commit()
    return None


@router.get("/{project_id}/a2a-matrix")
async def get_a2a_matrix(
    project_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await _get_project_or_404(db, project_id, current_user.tenant_id)
    pa_result = await db.execute(select(ProjectAgent).where(ProjectAgent.project_id == project_id))
    agent_ids = [membership.agent_id for membership in pa_result.scalars().all()]

    matrix = []
    for index, source_agent_id in enumerate(agent_ids):
        for target_agent_id in agent_ids[index + 1:]:
            forward = await db.execute(
                select(AgentAgentRelationship).where(
                    AgentAgentRelationship.agent_id == source_agent_id,
                    AgentAgentRelationship.target_agent_id == target_agent_id,
                )
            )
            reverse = await db.execute(
                select(AgentAgentRelationship).where(
                    AgentAgentRelationship.agent_id == target_agent_id,
                    AgentAgentRelationship.target_agent_id == source_agent_id,
                )
            )
            matrix.append(
                {
                    "source_agent_id": str(source_agent_id),
                    "target_agent_id": str(target_agent_id),
                    "forward_authorized": forward.scalar_one_or_none() is not None,
                    "reverse_authorized": reverse.scalar_one_or_none() is not None,
                }
            )
    return matrix


@router.post("/{project_id}/a2a-grant", status_code=status.HTTP_201_CREATED)
async def grant_a2a(
    project_id: uuid.UUID,
    body: A2AGrantIn,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    project = await _get_project_or_404(db, project_id, current_user.tenant_id)
    if not _can_write(current_user, project):
        raise HTTPException(status_code=403, detail="Not authorized")
    _ensure_not_archived(project)

    try:
        source_agent_id = uuid.UUID(body.source_agent_id)
        target_agent_id = uuid.UUID(body.target_agent_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="Invalid agent IDs") from exc

    agent_lookup = await _get_project_agents_lookup(db, project.id)
    if source_agent_id not in agent_lookup or target_agent_id not in agent_lookup:
        raise HTTPException(status_code=422, detail="Both agents must belong to this project")

    for left_id, right_id in ((source_agent_id, target_agent_id), (target_agent_id, source_agent_id)):
        existing = await db.execute(
            select(AgentAgentRelationship).where(
                AgentAgentRelationship.agent_id == left_id,
                AgentAgentRelationship.target_agent_id == right_id,
            )
        )
        if existing.scalar_one_or_none():
            continue
        db.add(
            AgentAgentRelationship(
                agent_id=left_id,
                target_agent_id=right_id,
                relation="collaborator",
                description=f"Granted via project {project_id}",
            )
        )

    await create_project_activity(
        db,
        project_id=project.id,
        event="agent.a2a_granted",
        actor_type="user",
        actor_id=current_user.id,
        payload={
            "source_agent_id": str(source_agent_id),
            "source_agent_name": agent_lookup[source_agent_id].name,
            "target_agent_id": str(target_agent_id),
            "target_agent_name": agent_lookup[target_agent_id].name,
        },
    )
    await db.commit()
    return {"granted": True, "source_agent_id": str(source_agent_id), "target_agent_id": str(target_agent_id)}


@router.get("/{project_id}/tasks")
async def list_project_tasks(
    project_id: uuid.UUID,
    status: Optional[str] = Query(None),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await _get_project_or_404(db, project_id, current_user.tenant_id)
    query = select(ProjectTask).where(ProjectTask.project_id == project_id)
    if status and status != "all":
        query = query.where(ProjectTask.status == status)
    query = query.order_by(ProjectTask.sort_order.asc(), ProjectTask.created_at.asc())
    result = await db.execute(query)
    tasks = result.scalars().all()
    agent_lookup = await _get_project_agents_lookup(db, project_id)
    return [await _build_task_out(db, task, agent_lookup=agent_lookup) for task in tasks]


@router.post("/{project_id}/tasks", status_code=status.HTTP_201_CREATED)
async def create_project_task(
    project_id: uuid.UUID,
    body: CreateProjectTaskIn,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    project = await _get_project_or_404(db, project_id, current_user.tenant_id)
    if not _can_write(current_user, project):
        raise HTTPException(status_code=403, detail="Not authorized")
    _ensure_not_archived(project)

    if body.priority not in PROJECT_TASK_ALLOWED_PRIORITIES:
        raise HTTPException(status_code=422, detail="Invalid priority")
    if body.status not in PROJECT_TASK_ALLOWED_STATUSES:
        raise HTTPException(status_code=422, detail="Invalid status")

    assignee_ids = await _validate_task_assignees(
        db,
        project_id=project.id,
        tenant_id=current_user.tenant_id,
        assignee_agent_ids=body.assignee_agent_ids,
    )
    max_sort_result = await db.execute(
        select(func.max(ProjectTask.sort_order)).where(ProjectTask.project_id == project.id)
    )
    next_sort_order = (max_sort_result.scalar() or 0) + 1
    due_at = _parse_optional_datetime(body.due_at, "due_at")
    completed_at = datetime.now(tz.utc) if body.status in PROJECT_TASK_TERMINAL_STATUSES else None

    task = ProjectTask(
        project_id=project.id,
        title=body.title,
        goal=body.goal,
        acceptance_criteria=body.acceptance_criteria,
        due_at=due_at,
        priority=body.priority,
        status=body.status,
        assignee_agent_ids=assignee_ids,
        created_by=current_user.id,
        sort_order=next_sort_order,
        completed_at=completed_at,
    )
    db.add(task)
    await db.flush()

    await create_project_activity(
        db,
        project_id=project.id,
        event="task.created",
        actor_type="user",
        actor_id=current_user.id,
        payload={
            "task_id": str(task.id),
            "title": task.title,
            "status": task.status,
            "priority": task.priority,
            "assignee_agent_ids": [str(agent_id) for agent_id in assignee_ids],
        },
    )

    await db.commit()
    await db.refresh(task)
    agent_lookup = await _get_project_agents_lookup(db, project.id)
    return await _build_task_out(db, task, agent_lookup=agent_lookup)


@router.post("/{project_id}/tasks/reorder")
async def reorder_project_tasks(
    project_id: uuid.UUID,
    body: ReorderProjectTasksIn,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    project = await _get_project_or_404(db, project_id, current_user.tenant_id)
    if not _can_write(current_user, project):
        raise HTTPException(status_code=403, detail="Not authorized")
    _ensure_not_archived(project)

    result = await db.execute(
        select(ProjectTask).where(ProjectTask.project_id == project.id).order_by(ProjectTask.sort_order.asc())
    )
    tasks = result.scalars().all()
    task_map = {str(task.id): task for task in tasks}
    if set(body.ordered_ids) != set(task_map.keys()):
        raise HTTPException(status_code=422, detail="ordered_ids must contain every project task exactly once")

    for index, task_id in enumerate(body.ordered_ids):
        task_map[task_id].sort_order = index + 1

    await create_project_activity(
        db,
        project_id=project.id,
        event="task.reordered",
        actor_type="user",
        actor_id=current_user.id,
        payload={"ordered_ids": body.ordered_ids},
    )
    await db.commit()
    return {"ok": True}


@router.patch("/{project_id}/tasks/{task_id}")
async def update_project_task(
    project_id: uuid.UUID,
    task_id: uuid.UUID,
    body: PatchProjectTaskIn,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    project = await _get_project_or_404(db, project_id, current_user.tenant_id)
    if not _can_write(current_user, project):
        raise HTTPException(status_code=403, detail="Not authorized")
    _ensure_not_archived(project)

    result = await db.execute(
        select(ProjectTask).where(
            ProjectTask.project_id == project.id,
            ProjectTask.id == task_id,
        )
    )
    task = result.scalar_one_or_none()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    changed_fields: list[str] = []

    if body.title is not None and body.title != task.title:
        task.title = body.title
        changed_fields.append("title")
    if body.goal is not None and body.goal != task.goal:
        task.goal = body.goal
        changed_fields.append("goal")
    if body.acceptance_criteria is not None and body.acceptance_criteria != task.acceptance_criteria:
        task.acceptance_criteria = body.acceptance_criteria
        changed_fields.append("acceptance_criteria")
    if body.due_at is not None:
        parsed_due_at = _parse_optional_datetime(body.due_at, "due_at")
        if parsed_due_at != task.due_at:
            task.due_at = parsed_due_at
            changed_fields.append("due_at")
    if body.priority is not None:
        if body.priority not in PROJECT_TASK_ALLOWED_PRIORITIES:
            raise HTTPException(status_code=422, detail="Invalid priority")
        if body.priority != task.priority:
            task.priority = body.priority
            changed_fields.append("priority")
    if body.status is not None:
        if body.status not in PROJECT_TASK_ALLOWED_STATUSES:
            raise HTTPException(status_code=422, detail="Invalid status")
        if body.status != task.status:
            task.status = body.status
            task.completed_at = datetime.now(tz.utc) if body.status in PROJECT_TASK_TERMINAL_STATUSES else None
            changed_fields.append("status")
    if body.assignee_agent_ids is not None:
        assignee_ids = await _validate_task_assignees(
            db,
            project_id=project.id,
            tenant_id=current_user.tenant_id,
            assignee_agent_ids=body.assignee_agent_ids,
        )
        if assignee_ids != (task.assignee_agent_ids or []):
            task.assignee_agent_ids = assignee_ids
            changed_fields.append("assignee_agent_ids")

    if changed_fields:
        await create_project_activity(
            db,
            project_id=project.id,
            event="task.updated",
            actor_type="user",
            actor_id=current_user.id,
            payload={
                "task_id": str(task.id),
                "title": task.title,
                "status": task.status,
                "changed_fields": changed_fields,
            },
        )

    await db.commit()
    await db.refresh(task)
    agent_lookup = await _get_project_agents_lookup(db, project.id)
    return await _build_task_out(db, task, agent_lookup=agent_lookup)


@router.delete("/{project_id}/tasks/{task_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_project_task(
    project_id: uuid.UUID,
    task_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    project = await _get_project_or_404(db, project_id, current_user.tenant_id)
    if not _can_write(current_user, project):
        raise HTTPException(status_code=403, detail="Not authorized")
    _ensure_not_archived(project)

    result = await db.execute(
        select(ProjectTask).where(
            ProjectTask.project_id == project.id,
            ProjectTask.id == task_id,
        )
    )
    task = result.scalar_one_or_none()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    await create_project_activity(
        db,
        project_id=project.id,
        event="task.deleted",
        actor_type="user",
        actor_id=current_user.id,
        payload={
            "task_id": str(task.id),
            "title": task.title,
        },
    )
    await db.delete(task)
    await db.commit()
    return None


@router.get("/{project_id}/activities")
async def list_project_activities(
    project_id: uuid.UUID,
    limit: int = Query(20, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await _get_project_or_404(db, project_id, current_user.tenant_id)
    result = await db.execute(
        select(ProjectActivity)
        .where(ProjectActivity.project_id == project_id)
        .order_by(ProjectActivity.created_at.desc())
        .limit(limit)
    )
    activities = result.scalars().all()
    return [await _build_activity_out(db, activity) for activity in activities]


@router.get("/{project_id}/decisions")
async def list_project_decisions(
    project_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await _get_project_or_404(db, project_id, current_user.tenant_id)
    result = await db.execute(
        select(ProjectDecision)
        .where(ProjectDecision.project_id == project_id)
        .order_by(ProjectDecision.created_at.desc())
    )
    decisions = result.scalars().all()
    return [await _build_decision_out(db, decision) for decision in decisions]


@router.post("/{project_id}/decisions", status_code=status.HTTP_201_CREATED)
async def create_project_decision(
    project_id: uuid.UUID,
    body: CreateProjectDecisionIn,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    project = await _get_project_or_404(db, project_id, current_user.tenant_id)
    if not _can_write(current_user, project):
        raise HTTPException(status_code=403, detail="Not authorized")
    _ensure_not_archived(project)
    if not body.title.strip():
        raise HTTPException(status_code=422, detail="Decision title is required")

    decision = ProjectDecision(
        project_id=project.id,
        title=body.title.strip(),
        content=body.content.strip() if body.content else None,
        created_by=current_user.id,
    )
    db.add(decision)
    await db.flush()

    await create_project_activity(
        db,
        project_id=project.id,
        event="decision.created",
        actor_type="user",
        actor_id=current_user.id,
        payload={
            "decision_id": str(decision.id),
            "title": decision.title,
        },
    )
    await db.commit()
    await db.refresh(decision)
    return await _build_decision_out(db, decision)


@router.patch("/{project_id}/decisions/{decision_id}")
async def update_project_decision(
    project_id: uuid.UUID,
    decision_id: uuid.UUID,
    body: PatchProjectDecisionIn,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    project = await _get_project_or_404(db, project_id, current_user.tenant_id)
    if not _can_write(current_user, project):
        raise HTTPException(status_code=403, detail="Not authorized")
    _ensure_not_archived(project)

    decision = await _get_project_decision_or_404(db, project_id=project.id, decision_id=decision_id)
    changed_fields: list[str] = []

    if body.title is not None and body.title != decision.title:
        if not body.title.strip():
            raise HTTPException(status_code=422, detail="Decision title is required")
        decision.title = body.title.strip()
        changed_fields.append("title")
    if body.content is not None and body.content != decision.content:
        decision.content = body.content.strip() if body.content else None
        changed_fields.append("content")

    if changed_fields:
        await create_project_activity(
            db,
            project_id=project.id,
            event="decision.updated",
            actor_type="user",
            actor_id=current_user.id,
            payload={
                "decision_id": str(decision.id),
                "title": decision.title,
                "changed_fields": changed_fields,
            },
        )

    await db.commit()
    await db.refresh(decision)
    return await _build_decision_out(db, decision)


@router.delete("/{project_id}/decisions/{decision_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_project_decision(
    project_id: uuid.UUID,
    decision_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    project = await _get_project_or_404(db, project_id, current_user.tenant_id)
    if not _can_write(current_user, project):
        raise HTTPException(status_code=403, detail="Not authorized")
    _ensure_not_archived(project)

    decision = await _get_project_decision_or_404(db, project_id=project.id, decision_id=decision_id)
    await create_project_activity(
        db,
        project_id=project.id,
        event="decision.deleted",
        actor_type="user",
        actor_id=current_user.id,
        payload={
            "decision_id": str(decision.id),
            "title": decision.title,
        },
    )
    await db.delete(decision)
    await db.commit()
    return None


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
    return [TagOut(id=str(tag.id), name=tag.name, color=tag.color) for tag in tags]


@tags_router.post("", status_code=status.HTTP_201_CREATED)
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
        tenant_id=current_user.tenant_id,
        name=body.name,
        color=body.color,
    )
    db.add(tag)
    await db.commit()
    await db.refresh(tag)
    return TagOut(id=str(tag.id), name=tag.name, color=tag.color)


@tags_router.delete("/{tag_id}", status_code=status.HTTP_204_NO_CONTENT)
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
    _ensure_not_archived(project)

    applied_tag_ids = await _apply_tags(
        db,
        project_id=project.id,
        tenant_id=current_user.tenant_id,
        tag_ids=body.tag_ids,
    )
    await create_project_activity(
        db,
        project_id=project.id,
        event="project.tags_updated",
        actor_type="user",
        actor_id=current_user.id,
        payload={"tag_ids": applied_tag_ids},
    )
    await db.commit()
    return {"project_id": str(project_id), "tag_ids": applied_tag_ids}
