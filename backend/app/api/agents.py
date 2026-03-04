"""Agent (Digital Employee) API routes."""

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.permissions import check_agent_access, is_agent_creator
from app.core.security import get_current_user, require_role
from app.database import get_db
from app.models.agent import Agent, AgentPermission
from app.models.user import User
from app.schemas.schemas import AgentCreate, AgentOut, AgentUpdate

router = APIRouter(prefix="/agents", tags=["agents"])


@router.get("/templates")
async def list_templates(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List all available agent templates."""
    from app.models.agent import AgentTemplate
    result = await db.execute(
        select(AgentTemplate).order_by(AgentTemplate.is_builtin.desc(), AgentTemplate.created_at.asc())
    )
    templates = result.scalars().all()
    return [
        {
            "id": str(t.id),
            "name": t.name,
            "description": t.description,
            "icon": t.icon,
            "category": t.category,
            "is_builtin": t.is_builtin,
            "soul_template": t.soul_template,
            "default_skills": t.default_skills,
            "default_autonomy_policy": t.default_autonomy_policy,
        }
        for t in templates
    ]


@router.get("/", response_model=list[AgentOut])
async def list_agents(
    tenant_id: uuid.UUID | None = None,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List all agents the current user has access to."""
    # platform_admin & org_admin see all agents (optionally filtered by tenant)
    if current_user.role in ("platform_admin", "org_admin"):
        stmt = select(Agent)
        if tenant_id:
            stmt = stmt.where(Agent.tenant_id == tenant_id)
        result = await db.execute(stmt.order_by(Agent.created_at.desc()))
        return [AgentOut.model_validate(a) for a in result.scalars().all()]

    # agent_admin sees their own created agents + permitted
    # member sees only permitted
    # All scoped to user's tenant
    user_tenant = current_user.tenant_id

    # Get agents user created (within their tenant)
    created = select(Agent).where(Agent.creator_id == current_user.id, Agent.tenant_id == user_tenant)

    # Get agents user has permission to (within their tenant)
    permitted_ids = (
        select(AgentPermission.agent_id)
        .where(
            (AgentPermission.scope_type == "company")
            | ((AgentPermission.scope_type == "user") & (AgentPermission.scope_id == current_user.id))
            | (
                (AgentPermission.scope_type == "department")
                & (AgentPermission.scope_id == current_user.department_id)
            )
        )
    )
    permitted = select(Agent).where(Agent.id.in_(permitted_ids), Agent.tenant_id == user_tenant)

    # Union
    from sqlalchemy import union_all

    combined = union_all(created, permitted).subquery()
    result = await db.execute(
        select(Agent).where(Agent.id.in_(select(combined.c.id))).order_by(Agent.created_at.desc())
    )
    return [AgentOut.model_validate(a) for a in result.scalars().all()]


@router.post("/", response_model=AgentOut, status_code=status.HTTP_201_CREATED)
async def create_agent(
    data: AgentCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Create a new digital employee (any authenticated user)."""
    # Check agent creation quota
    from app.services.quota_guard import check_agent_creation_quota, QuotaExceeded
    try:
        await check_agent_creation_quota(current_user.id)
    except QuotaExceeded as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=e.message)

    # Calculate expiry time
    from datetime import datetime, timedelta, timezone as tz
    expires_at = datetime.now(tz.utc) + timedelta(hours=current_user.quota_agent_ttl_hours or 48)

    # Get default LLM calls limit from tenant
    max_llm_calls = 100
    if current_user.tenant_id:
        from app.models.tenant import Tenant
        tenant_result = await db.execute(select(Tenant).where(Tenant.id == current_user.tenant_id))
        tenant = tenant_result.scalar_one_or_none()
        if tenant:
            max_llm_calls = tenant.default_max_llm_calls_per_day or 100

    agent = Agent(
        name=data.name,
        role_description=data.role_description,
        bio=data.bio,
        avatar_url=data.avatar_url,
        creator_id=current_user.id,
        tenant_id=current_user.tenant_id,
        primary_model_id=data.primary_model_id,
        fallback_model_id=data.fallback_model_id,
        max_tokens_per_day=data.max_tokens_per_day,
        max_tokens_per_month=data.max_tokens_per_month,
        template_id=data.template_id,
        status="creating",
        expires_at=expires_at,
        max_llm_calls_per_day=max_llm_calls,
    )
    if data.autonomy_policy:
        agent.autonomy_policy = data.autonomy_policy

    db.add(agent)
    await db.flush()

    # Set permissions
    access_level = data.permission_access_level if data.permission_access_level in ("use", "manage") else "use"
    if data.permission_scope_type == "company":
        db.add(AgentPermission(agent_id=agent.id, scope_type="company", access_level=access_level))
    elif data.permission_scope_type == "user":
        if data.permission_scope_ids:
            for scope_id in data.permission_scope_ids:
                db.add(AgentPermission(agent_id=agent.id, scope_type="user", scope_id=scope_id, access_level=access_level))
        else:
            # "仅自己" — insert creator as the only permitted user
            db.add(AgentPermission(agent_id=agent.id, scope_type="user", scope_id=current_user.id, access_level="manage"))

    await db.flush()

    # Initialize agent file system from template
    from app.services.agent_manager import agent_manager
    await agent_manager.initialize_agent_files(
        db, agent,
        personality=data.personality,
        boundaries=data.boundaries,
    )

    # Copy selected skills + mandatory default skills into agent workspace
    from app.models.skill import Skill, SkillFile
    from sqlalchemy.orm import selectinload
    from pathlib import Path

    # Always include default skills
    default_result = await db.execute(
        select(Skill).where(Skill.is_default == True)
    )
    default_ids = {s.id for s in default_result.scalars().all()}

    # Merge user-selected + default skill IDs
    all_skill_ids = set(data.skill_ids or []) | default_ids

    if all_skill_ids:
        agent_dir = agent_manager._agent_dir(agent.id)
        skills_dir = agent_dir / "skills"
        skills_dir.mkdir(parents=True, exist_ok=True)

        for sid in all_skill_ids:
            result = await db.execute(
                select(Skill).where(Skill.id == sid).options(selectinload(Skill.files))
            )
            skill = result.scalar_one_or_none()
            if not skill:
                continue
            # Create folder: skills/<folder_name>/
            skill_folder = skills_dir / skill.folder_name
            skill_folder.mkdir(parents=True, exist_ok=True)
            # Write each file
            for sf in skill.files:
                file_path = skill_folder / sf.path
                file_path.parent.mkdir(parents=True, exist_ok=True)
                file_path.write_text(sf.content)

    # Start container
    await agent_manager.start_container(db, agent)
    await db.flush()

    return AgentOut.model_validate(agent)


@router.get("/{agent_id}")
async def get_agent(
    agent_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get agent details."""
    agent, access_level = await check_agent_access(db, current_user, agent_id)
    out = AgentOut.model_validate(agent).model_dump()
    out["access_level"] = access_level
    return out


@router.get("/{agent_id}/permissions")
async def get_agent_permissions(
    agent_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get agent permission scope."""
    agent, _access = await check_agent_access(db, current_user, agent_id)
    result = await db.execute(select(AgentPermission).where(AgentPermission.agent_id == agent_id))
    perms = result.scalars().all()

    if not perms:
        return {"scope_type": "user", "scope_ids": [], "access_level": "manage" if is_agent_creator(current_user, agent) else "use", "is_owner": is_agent_creator(current_user, agent)}

    scope_type = perms[0].scope_type
    scope_ids = [str(p.scope_id) for p in perms if p.scope_id]
    perm_access_level = perms[0].access_level or "use"

    # Resolve names for display
    scope_names = []
    if scope_type == "user":
        for sid in scope_ids:
            r = await db.execute(select(User).where(User.id == uuid.UUID(sid)))
            u = r.scalar_one_or_none()
            if u:
                scope_names.append({"id": sid, "name": u.display_name or u.username})

    return {
        "scope_type": scope_type,
        "scope_ids": scope_ids,
        "scope_names": scope_names,
        "access_level": perm_access_level,
        "is_owner": is_agent_creator(current_user, agent),
    }


@router.put("/{agent_id}/permissions")
async def update_agent_permissions(
    agent_id: uuid.UUID,
    data: dict,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Update agent permission scope (owner or platform_admin only)."""
    agent, _access = await check_agent_access(db, current_user, agent_id)
    if not is_agent_creator(current_user, agent):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only owner or admin can change permissions")

    scope_type = data.get("scope_type", "company")
    scope_ids = data.get("scope_ids", [])
    access_level = data.get("access_level", "use")
    if access_level not in ("use", "manage"):
        access_level = "use"

    # Delete existing permissions
    from sqlalchemy import delete as sql_delete
    await db.execute(sql_delete(AgentPermission).where(AgentPermission.agent_id == agent_id))

    # Insert new permissions
    if scope_type == "company":
        db.add(AgentPermission(agent_id=agent_id, scope_type="company", access_level=access_level))
    elif scope_type == "user":
        if scope_ids:
            for sid in scope_ids:
                db.add(AgentPermission(agent_id=agent_id, scope_type="user", scope_id=uuid.UUID(sid), access_level=access_level))
        else:
            # "仅自己"
            db.add(AgentPermission(agent_id=agent_id, scope_type="user", scope_id=current_user.id, access_level="manage"))

    await db.commit()
    return {"status": "ok"}


@router.patch("/{agent_id}", response_model=AgentOut)
async def update_agent(
    agent_id: uuid.UUID,
    data: AgentUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Update agent settings (creator or admin)."""
    agent, _access = await check_agent_access(db, current_user, agent_id)

    is_admin = current_user.role in ("platform_admin", "org_admin")

    if not is_agent_creator(current_user, agent) and not is_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only creator or admin can update agent settings")

    update_data = data.model_dump(exclude_unset=True)

    # expires_at: admin only
    if "expires_at" in update_data:
        if not is_admin:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only admin can modify agent expiry time")
        # If extending expiry, re-activate the agent
        from datetime import datetime, timezone as tz
        new_expires = update_data["expires_at"]
        if new_expires and (not agent.expires_at or new_expires > agent.expires_at):
            if agent.is_expired:
                agent.is_expired = False
                agent.status = "idle"

    # Enforce heartbeat floor from tenant
    if "heartbeat_interval_minutes" in update_data and current_user.tenant_id:
        from app.models.tenant import Tenant
        t_result = await db.execute(select(Tenant).where(Tenant.id == current_user.tenant_id))
        tenant = t_result.scalar_one_or_none()
        if tenant and update_data["heartbeat_interval_minutes"] < tenant.min_heartbeat_interval_minutes:
            update_data["heartbeat_interval_minutes"] = tenant.min_heartbeat_interval_minutes

    for field, value in update_data.items():
        setattr(agent, field, value)
    await db.flush()
    return AgentOut.model_validate(agent)


@router.delete("/{agent_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_agent(
    agent_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Delete a digital employee (creator only)."""
    agent, _access = await check_agent_access(db, current_user, agent_id)
    if not is_agent_creator(current_user, agent) and current_user.role not in ("super_admin", "org_admin", "platform_admin"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only creator or admin can delete agent")

    # Stop container and archive files (best effort)
    from app.services.agent_manager import agent_manager
    try:
        await agent_manager.remove_container(agent)
    except Exception:
        pass
    try:
        await agent_manager.archive_agent_files(agent.id)
    except Exception:
        pass

    # Delete related records that reference this agent
    # Use savepoints so a failure in one table doesn't poison the whole transaction
    from sqlalchemy import text

    cleanup_tables = [
        "agent_activity_logs",
        "audit_logs",
        "approval_requests",
        "chat_messages",
        "tasks",
        "agent_schedules",
        "channel_configs",
        "agent_permissions",
        "agent_tools",
        "agent_relationships",
    ]

    for table in cleanup_tables:
        try:
            async with db.begin_nested():
                await db.execute(text(f"DELETE FROM {table} WHERE agent_id = :aid"), {"aid": agent_id})
        except Exception:
            pass

    # Also clean agent_agent_relationships (has both agent_id and target_agent_id)
    try:
        async with db.begin_nested():
            await db.execute(
                text("DELETE FROM agent_agent_relationships WHERE agent_id = :aid OR target_agent_id = :aid"),
                {"aid": agent_id},
            )
    except Exception:
        pass

    # Also clear plaza posts by this agent
    try:
        async with db.begin_nested():
            await db.execute(text("DELETE FROM plaza_posts WHERE author_id = :aid"), {"aid": str(agent_id)})
    except Exception:
        pass

    await db.delete(agent)
    await db.commit()


@router.post("/{agent_id}/start", response_model=AgentOut)
async def start_agent(
    agent_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Start an agent's container."""
    agent, _access = await check_agent_access(db, current_user, agent_id)
    if not is_agent_creator(current_user, agent):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only creator can start agent")

    from app.services.agent_manager import agent_manager
    await agent_manager.start_container(db, agent)
    await db.flush()
    return AgentOut.model_validate(agent)


@router.post("/{agent_id}/stop", response_model=AgentOut)
async def stop_agent(
    agent_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Stop an agent's container."""
    agent, _access = await check_agent_access(db, current_user, agent_id)
    if not is_agent_creator(current_user, agent):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only creator can stop agent")

    from app.services.agent_manager import agent_manager
    await agent_manager.stop_container(agent)
    await db.flush()
    return AgentOut.model_validate(agent)
