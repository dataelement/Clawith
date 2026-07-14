"""Agent collaboration and template market API routes."""

import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.dao import query_dao
from app.core.permissions import check_agent_access
from app.core.security import get_current_user, get_current_admin
from app.dao import agent_metrics_dao, agent_template_dao, user_dao
from app.database import get_db
from app.models.user import User
from app.services.collaboration import collaboration_service

router = APIRouter(tags=["advanced"])


# ─── Collaboration ──────────────────────────────────────

class DelegateRequest(BaseModel):
    to_agent_id: uuid.UUID
    task_title: str
    task_description: str = ""


class InterAgentMessage(BaseModel):
    to_agent_id: uuid.UUID
    message: str
    msg_type: str = "notify"  # notify | consult


@router.get("/agents/{agent_id}/collaborators")
async def list_collaborators(
    agent_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List agents that can collaborate with this agent."""
    await check_agent_access(db, current_user, agent_id)
    return await collaboration_service.list_collaborators(db, agent_id)


@router.post("/agents/{agent_id}/collaborate/delegate")
async def delegate_task(
    agent_id: uuid.UUID,
    data: DelegateRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Delegate a task from one agent to another."""
    await check_agent_access(db, current_user, agent_id)
    try:
        result = await collaboration_service.delegate_task(
            db, agent_id, data.to_agent_id, data.task_title, data.task_description
        )
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/agents/{agent_id}/collaborate/message")
async def send_inter_agent_message(
    agent_id: uuid.UUID,
    data: InterAgentMessage,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Send a message between agents."""
    await check_agent_access(db, current_user, agent_id)
    return await collaboration_service.send_message_between_agents(
        db, agent_id, data.to_agent_id, data.message, data.msg_type
    )


# ─── Template Market ────────────────────────────────────

class TemplateCreate(BaseModel):
    name: str
    description: str = ""
    icon: str = "🤖"
    category: str = "general"
    soul_template: str = ""
    default_skills: list[str] = []
    default_autonomy_policy: dict = {}


class TemplateOut(BaseModel):
    id: uuid.UUID
    name: str
    description: str
    icon: str
    category: str
    soul_template: str
    default_skills: list
    default_autonomy_policy: dict
    is_builtin: bool
    created_at: str | None = None

    model_config = {"from_attributes": True}


@router.get("/templates", response_model=list[TemplateOut])
async def list_templates(
    category: str | None = None,
):
    """List available agent templates."""
    templates = await agent_template_dao.list_templates(category=category)
    return [TemplateOut.model_validate(t) for t in templates]


@router.get("/templates/{template_id}", response_model=TemplateOut)
async def get_template(template_id: uuid.UUID):
    """Get template details."""
    template = await agent_template_dao.get(template_id)
    if not template:
        raise HTTPException(status_code=404, detail="Template not found")
    return TemplateOut.model_validate(template)


@router.post("/templates", response_model=TemplateOut, status_code=status.HTTP_201_CREATED)
async def create_template(
    data: TemplateCreate,
    current_user: User = Depends(get_current_user),
):
    """Create a new agent template (share to template market)."""
    template = await agent_template_dao.create_template(
        obj_in={
            "name": data.name,
            "description": data.description,
            "icon": data.icon,
            "category": data.category,
            "soul_template": data.soul_template,
            "default_skills": data.default_skills,
            "default_autonomy_policy": data.default_autonomy_policy,
            "created_by": current_user.id,
        }
    )
    return TemplateOut.model_validate(template)


@router.delete("/templates/{template_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_template(
    template_id: uuid.UUID,
    current_user: User = Depends(get_current_admin),
):
    """Delete a template (admin or creator)."""
    deleted = await agent_template_dao.delete(id=template_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Template not found")


# ─── Agent Handover ─────────────────────────────────────

class HandoverRequest(BaseModel):
    new_creator_id: uuid.UUID


@router.post("/agents/{agent_id}/handover")
async def handover_agent(
    agent_id: uuid.UUID,
    data: HandoverRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Transfer ownership of a digital employee to another user."""
    from app.models.audit import AuditLog
    from app.core.permissions import is_agent_creator

    agent, _access = await check_agent_access(db, current_user, agent_id)
    if not is_agent_creator(current_user, agent):
        raise HTTPException(status_code=403, detail="Only creator can handover agent")

    # Verify new creator exists
    new_creator = await user_dao.get(data.new_creator_id)
    if not new_creator:
        raise HTTPException(status_code=404, detail="Target user not found")

    old_creator_id = agent.creator_id
    agent.creator_id = data.new_creator_id

    query_dao.add(db, AuditLog(
        user_id=current_user.id,
        agent_id=agent_id,
        action="agent:handover",
        details={
            "from_creator": str(old_creator_id),
            "to_creator": str(data.new_creator_id),
        },
    ))
    await query_dao.flush(db)

    return {
        "status": "transferred",
        "agent_name": agent.name,
        "new_creator": new_creator.display_name,
    }


# ─── Observability ──────────────────────────────────────

@router.get("/agents/{agent_id}/metrics")
async def get_agent_metrics(
    agent_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get observability metrics for an agent."""
    agent, _access = await check_agent_access(db, current_user, agent_id)
    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
    counts = await agent_metrics_dao.get_agent_metrics_counts(agent_id=agent_id, recent_cutoff=cutoff)

    # Container status
    from app.services.agent_manager import agent_manager
    container_status = agent_manager.get_container_status(agent)

    _total_tasks = counts["total_tasks"]
    _done_tasks = counts["done_tasks"]
    _pending_tasks = counts["pending_tasks"]

    return {
        "agent_id": str(agent_id),
        "agent_name": agent.name,
        "status": agent.status,
        "container": container_status,
        "tokens": {
            "used_today": agent.tokens_used_today,
            "used_month": agent.tokens_used_month,
            "used_total": agent.tokens_used_total,
            "cache_read_today": agent.cache_read_tokens_today,
            "cache_read_month": agent.cache_read_tokens_month,
            "cache_read_total": agent.cache_read_tokens_total,
            "cache_creation_today": agent.cache_creation_tokens_today,
            "cache_creation_month": agent.cache_creation_tokens_month,
            "cache_creation_total": agent.cache_creation_tokens_total,
            "cache_hit_rate_today": round((agent.cache_read_tokens_today or 0) / max(agent.tokens_used_today or 0, 1), 4),
            "cache_hit_rate_month": round((agent.cache_read_tokens_month or 0) / max(agent.tokens_used_month or 0, 1), 4),
            "cache_hit_rate_total": round((agent.cache_read_tokens_total or 0) / max(agent.tokens_used_total or 0, 1), 4),
            "limit_day": agent.max_tokens_per_day,
            "limit_month": agent.max_tokens_per_month,
        },
        "tasks": {
            "total": _total_tasks,
            "done": _done_tasks,
            "pending": _pending_tasks,
            "completion_rate": round(
                _done_tasks / max(_total_tasks, 1) * 100, 1
            ),
        },
        "approvals": {
            "total": counts["total_approvals"],
            "pending": counts["pending_approvals"],
        },
        "activity": {
            "actions_last_24h": counts["recent_actions"],
        },
    }
