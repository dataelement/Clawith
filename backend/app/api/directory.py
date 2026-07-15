"""Read-only agent directory API."""

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import delete, exists, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.permissions import check_agent_access
from app.core.security import get_current_user
from app.database import get_db
from app.models.agent import Agent, AgentPermission
from app.models.org import AgentAgentRelationship, OrgMember
from app.models.user import User
from app.services.agent_directory import DirectoryQueryError, query_agent_directory

router = APIRouter(prefix="/agents/{agent_id}/directory", tags=["agent-directory"])


class CustomHumanDirectoryIn(BaseModel):
    user_id: uuid.UUID


class CustomAgentDirectoryIn(BaseModel):
    target_agent_id: uuid.UUID


def _validate_pagination(limit: int, offset: int, max_limit: int = 100) -> None:
    if limit < 1 or limit > max_limit:
        raise HTTPException(status_code=400, detail={"code": "invalid_limit", "message": f"limit must be between 1 and {max_limit}"})
    if offset < 0:
        raise HTTPException(status_code=400, detail={"code": "invalid_offset", "message": "offset must be greater than or equal to 0"})


async def _require_custom_directory_manager(db: AsyncSession, current_user: User, agent_id: uuid.UUID) -> Agent:
    agent, access_level = await check_agent_access(db, current_user, agent_id)
    if access_level != "manage":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only managers can maintain this Directory")
    if (getattr(agent, "access_mode", None) or "company") != "custom":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "not_custom_agent", "message": "Only custom agents have a manually maintained Directory"},
        )
    return agent


@router.get("")
async def get_agent_directory(
    agent_id: uuid.UUID,
    member_type: str = "all",
    query: str = "",
    include_uncontactable: bool = False,
    limit: int = 50,
    offset: int = 0,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Return the people and agents the source agent can currently contact."""
    await check_agent_access(db, current_user, agent_id)
    try:
        return await query_agent_directory(
            db,
            source_agent_id=agent_id,
            query=query,
            member_type=member_type,
            include_uncontactable=include_uncontactable,
            limit=limit,
            offset=offset,
            max_limit=100,
        )
    except DirectoryQueryError as exc:
        raise HTTPException(status_code=exc.status_code, detail={"code": exc.code, "message": exc.message}) from exc


@router.get("/custom/humans")
async def get_custom_directory_humans(
    agent_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Return explicitly authorized human members in a custom Directory."""
    agent = await _require_custom_directory_manager(db, current_user, agent_id)
    result = await db.execute(
        select(AgentPermission, User, OrgMember)
        .join(
            User,
            (AgentPermission.scope_id == User.id)
            & (User.tenant_id == agent.tenant_id),
        )
        .outerjoin(
            OrgMember,
            (OrgMember.user_id == User.id)
            & (OrgMember.tenant_id == agent.tenant_id),
        )
        .where(
            AgentPermission.agent_id == agent_id,
            AgentPermission.scope_type == "user",
            AgentPermission.scope_id.is_not(None),
            AgentPermission.access_level.in_(["use", "manage"]),
        )
        .order_by(User.display_name.asc(), User.id.asc())
    )
    by_permission: dict[uuid.UUID, dict] = {}
    for permission, user, member in result.all():
        if permission.id in by_permission:
            continue
        by_permission[permission.id] = {
            "user_id": str(user.id),
            "member_id": str(member.id) if member else None,
            "display_name": getattr(member, "name", None) or user.display_name or user.username or user.email,
            "email": getattr(member, "email", None) or user.email,
            "title": getattr(member, "title", None) or "",
            "department": getattr(member, "department_path", None) or "",
            "access_level": permission.access_level,
            "removable": permission.access_level != "manage",
        }
    return {"members": list(by_permission.values())}


@router.get("/custom/human-candidates")
async def get_custom_directory_human_candidates(
    agent_id: uuid.UUID,
    query: str = "",
    limit: int = 50,
    offset: int = 0,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Return paginated human candidates that can be added to a custom Directory."""
    _validate_pagination(limit, offset)
    agent = await _require_custom_directory_manager(db, current_user, agent_id)
    query = (query or "").strip()
    conditions = [
        OrgMember.tenant_id == agent.tenant_id,
        OrgMember.status == "active",
        OrgMember.user_id.is_not(None),
        User.is_active.is_(True),
        ~exists().where(
            AgentPermission.agent_id == agent_id,
            AgentPermission.scope_type == "user",
            AgentPermission.scope_id == OrgMember.user_id,
            AgentPermission.access_level.in_(["use", "manage"]),
        ),
    ]
    if query:
        pattern = f"%{query}%"
        conditions.append(or_(
            OrgMember.name.ilike(pattern),
            OrgMember.email.ilike(pattern),
            OrgMember.title.ilike(pattern),
            OrgMember.department_path.ilike(pattern),
            OrgMember.name_translit_full.ilike(pattern),
            OrgMember.name_translit_initial.ilike(pattern),
        ))

    result = await db.execute(
        select(OrgMember, User)
        .join(User, (OrgMember.user_id == User.id) & (User.tenant_id == agent.tenant_id))
        .where(*conditions)
        .order_by(OrgMember.name.asc(), OrgMember.synced_at.asc())
        .offset(offset)
        .limit(limit + 1)
    )
    rows = result.all()
    candidates = [
        {
            "user_id": str(user.id),
            "member_id": str(member.id),
            "display_name": member.name,
            "email": member.email or user.email,
            "title": member.title or "",
            "department": member.department_path or "",
        }
        for member, user in rows[:limit]
    ]
    return {"candidates": candidates, "limit": limit, "offset": offset, "has_more": len(rows) > limit}


@router.post("/custom/humans")
async def add_custom_directory_human(
    agent_id: uuid.UUID,
    payload: CustomHumanDirectoryIn,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Add a human platform user to a custom Directory with use access."""
    agent = await _require_custom_directory_manager(db, current_user, agent_id)
    user = (await db.execute(
        select(User).where(
            User.id == payload.user_id,
            User.tenant_id == agent.tenant_id,
            User.is_active == True,  # noqa: E712
        )
    )).scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail={"code": "user_not_found", "message": "User was not found"})

    existing = (await db.execute(
        select(AgentPermission).where(
            AgentPermission.agent_id == agent_id,
            AgentPermission.scope_type == "user",
            AgentPermission.scope_id == payload.user_id,
        ).limit(1)
    )).scalar_one_or_none()
    if existing is None:
        db.add(AgentPermission(agent_id=agent_id, scope_type="user", scope_id=payload.user_id, access_level="use"))
    await db.commit()
    return {"status": "ok"}


@router.delete("/custom/humans/{user_id}")
async def remove_custom_directory_human(
    agent_id: uuid.UUID,
    user_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Remove a use-level human from a custom Directory."""
    await _require_custom_directory_manager(db, current_user, agent_id)
    permission = (await db.execute(
        select(AgentPermission).where(
            AgentPermission.agent_id == agent_id,
            AgentPermission.scope_type == "user",
            AgentPermission.scope_id == user_id,
        ).limit(1)
    )).scalar_one_or_none()
    if not permission:
        raise HTTPException(status_code=404, detail={"code": "permission_not_found", "message": "Directory member was not found"})
    if permission.access_level == "manage":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "manager_not_removable", "message": "Downgrade manager access in Permissions before removing this member"},
        )
    await db.execute(delete(AgentPermission).where(AgentPermission.id == permission.id))
    await db.commit()
    return {"status": "ok"}


@router.get("/custom/agents")
async def get_custom_directory_agents(
    agent_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Return explicitly linked digital employees in a custom Directory."""
    await _require_custom_directory_manager(db, current_user, agent_id)
    result = await db.execute(
        select(AgentAgentRelationship)
        .where(AgentAgentRelationship.agent_id == agent_id)
        .options(selectinload(AgentAgentRelationship.target_agent))
        .order_by(AgentAgentRelationship.created_at.asc())
    )
    agents = []
    for rel in result.scalars().all():
        target = rel.target_agent
        if not target:
            continue
        agents.append({
            "target_agent_id": str(target.id),
            "display_name": target.name,
            "role_description": target.role_description or "",
            "access_mode": target.access_mode or "company",
            "status": target.status,
        })
    return {"agents": agents}


@router.get("/custom/agent-candidates")
async def get_custom_directory_agent_candidates(
    agent_id: uuid.UUID,
    query: str = "",
    limit: int = 50,
    offset: int = 0,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Return paginated digital employee candidates for a custom Directory."""
    _validate_pagination(limit, offset)
    agent = await _require_custom_directory_manager(db, current_user, agent_id)
    query = (query or "").strip()
    conditions = [
        Agent.tenant_id == agent.tenant_id,
        Agent.id != agent.id,
        Agent.access_mode != "private",
        ~exists().where(
            AgentAgentRelationship.agent_id == agent_id,
            AgentAgentRelationship.target_agent_id == Agent.id,
        ),
    ]
    if query:
        pattern = f"%{query}%"
        conditions.append(or_(Agent.name.ilike(pattern), Agent.role_description.ilike(pattern)))

    result = await db.execute(
        select(Agent)
        .where(*conditions)
        .order_by(Agent.name.asc(), Agent.created_at.asc())
        .offset(offset)
        .limit(limit + 1)
    )
    rows = result.scalars().all()
    candidates = [
        {
            "target_agent_id": str(target.id),
            "display_name": target.name,
            "role_description": target.role_description or "",
            "access_mode": target.access_mode or "company",
            "status": target.status,
        }
        for target in rows[:limit]
    ]
    return {"candidates": candidates, "limit": limit, "offset": offset, "has_more": len(rows) > limit}


@router.post("/custom/agents")
async def add_custom_directory_agent(
    agent_id: uuid.UUID,
    payload: CustomAgentDirectoryIn,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Add a digital employee to a custom Directory."""
    agent = await _require_custom_directory_manager(db, current_user, agent_id)
    target = (await db.execute(
        select(Agent).where(
            Agent.id == payload.target_agent_id,
            Agent.tenant_id == agent.tenant_id,
            Agent.id != agent.id,
            Agent.access_mode != "private",
        )
    )).scalar_one_or_none()
    if not target:
        raise HTTPException(status_code=404, detail={"code": "target_agent_not_found", "message": "Target agent was not found"})

    existing = (await db.execute(
        select(AgentAgentRelationship.id).where(
            AgentAgentRelationship.agent_id == agent_id,
            AgentAgentRelationship.target_agent_id == payload.target_agent_id,
        ).limit(1)
    )).scalar_one_or_none()
    if existing is None:
        db.add(AgentAgentRelationship(
            agent_id=agent_id,
            target_agent_id=payload.target_agent_id,
            relation="collaborator",
            description="",
            created_by_user_id=current_user.id,
            updated_by_user_id=current_user.id,
        ))
    await db.commit()
    return {"status": "ok"}


@router.delete("/custom/agents/{target_agent_id}")
async def remove_custom_directory_agent(
    agent_id: uuid.UUID,
    target_agent_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Remove a digital employee from a custom Directory."""
    await _require_custom_directory_manager(db, current_user, agent_id)
    result = await db.execute(
        delete(AgentAgentRelationship)
        .where(
            AgentAgentRelationship.agent_id == agent_id,
            AgentAgentRelationship.target_agent_id == target_agent_id,
        )
    )
    await db.commit()
    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail={"code": "relationship_not_found", "message": "Directory agent was not found"})
    return {"status": "ok"}
