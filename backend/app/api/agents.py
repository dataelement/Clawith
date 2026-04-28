"""Agent (Digital Employee) API routes."""

import hashlib
import json
import secrets
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import cast, func, select, String
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.permissions import build_visible_agents_query, check_agent_access, is_agent_creator
from app.core.security import get_current_user
from app.database import get_db
from app.models.agent import Agent, AgentPermission, AgentTemplate
from app.models.audit import ChatMessage
from app.models.chat_session import ChatSession
from app.models.user import User
from app.schemas.schemas import AgentCreate, AgentOut, AgentUpdate

router = APIRouter(prefix="/agents", tags=["agents"])


def _serialize_dt(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


async def _archive_agent_task_history(db: AsyncSession, agent_id: uuid.UUID, archive_dir: Path) -> Path | None:
    """Persist task and task-log history into the agent archive directory before DB cleanup."""
    from app.models.task import Task, TaskLog

    task_result = await db.execute(select(Task).where(Task.agent_id == agent_id).order_by(Task.created_at.asc()))
    tasks = task_result.scalars().all()
    if not tasks:
        return None

    archive_dir.mkdir(parents=True, exist_ok=True)

    payload = {
        "agent_id": str(agent_id),
        "archived_at": datetime.now(timezone.utc).isoformat(),
        "tasks": [],
    }

    for task in tasks:
        log_result = await db.execute(select(TaskLog).where(TaskLog.task_id == task.id).order_by(TaskLog.created_at.asc()))
        logs = log_result.scalars().all()
        payload["tasks"].append(
            {
                "id": str(task.id),
                "title": task.title,
                "description": task.description,
                "type": task.type,
                "status": task.status,
                "priority": task.priority,
                "assignee": task.assignee,
                "created_by": str(task.created_by),
                "due_date": _serialize_dt(task.due_date),
                "supervision_target_user_id": (
                    str(task.supervision_target_user_id) if task.supervision_target_user_id else None
                ),
                "supervision_target_name": task.supervision_target_name,
                "supervision_channel": task.supervision_channel,
                "remind_schedule": task.remind_schedule,
                "created_at": _serialize_dt(task.created_at),
                "updated_at": _serialize_dt(task.updated_at),
                "completed_at": _serialize_dt(task.completed_at),
                "logs": [
                    {
                        "id": str(log.id),
                        "content": log.content,
                        "created_at": _serialize_dt(log.created_at),
                    }
                    for log in logs
                ],
            }
        )

    archive_path = archive_dir / "task_history.json"
    archive_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return archive_path


async def _lazy_reset_token_counters(agent: Agent, db: AsyncSession) -> bool:
    """Reset daily/monthly token counters if the day or month has changed.

    Returns True if any counter was reset (caller should commit/flush).
    """
    from datetime import datetime, timezone as tz
    now = datetime.now(tz.utc)
    changed = False

    last_daily = agent.last_daily_reset
    if last_daily is None or last_daily.date() < now.date():
        agent.tokens_used_today = 0
        agent.last_daily_reset = now
        changed = True

    last_monthly = agent.last_monthly_reset
    if last_monthly is None or (last_monthly.year, last_monthly.month) < (now.year, now.month):
        agent.tokens_used_month = 0
        agent.last_monthly_reset = now
        changed = True

    return changed


async def _build_unread_count_by_agent(
    db: AsyncSession,
    agents: list[Agent],
    current_user: User,
) -> dict[str, int]:
    """Return unread assistant/system/tool message counts for the current user per agent.

    The sidebar only needs user-facing unread state, so we scope strictly to sessions owned by
    the current platform user and ignore agent-to-agent / trigger-only threads.
    """

    if not agents:
        return {}

    agent_ids = [agent.id for agent in agents]
    result = await db.execute(
        select(ChatSession.agent_id, func.count(ChatMessage.id))
        .join(ChatMessage, ChatMessage.conversation_id == cast(ChatSession.id, String))
        .where(
            ChatSession.agent_id.in_(agent_ids),
            ChatSession.user_id == current_user.id,
            ChatSession.is_group == False,
            ChatSession.source_channel.notin_(["agent", "trigger"]),
            ChatMessage.role.in_(["assistant", "system", "tool_call"]),
            ChatMessage.created_at > func.coalesce(
                ChatSession.last_read_at_by_user,
                datetime(1970, 1, 1, tzinfo=timezone.utc),
            ),
        )
        .group_by(ChatSession.agent_id)
    )
    return {str(row[0]): int(row[1] or 0) for row in result.all()}


def _serialize_agent_out(agent: Agent, unread_count: int = 0) -> AgentOut:
    payload = AgentOut.model_validate(agent).model_dump()
    payload["unread_count"] = unread_count
    return AgentOut.model_validate(payload)


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
            "capability_bullets": t.capability_bullets or [],
            "has_bootstrap": bool(t.bootstrap_content),
        }
        for t in templates
    ]


async def _agent_to_out(
    db: AsyncSession,
    agent: Agent,
    viewer_id: uuid.UUID,
) -> AgentOut:
    """Serialize one agent with ``onboarded_for_me`` for the given viewer."""
    from app.services.onboarding import is_onboarded
    model = AgentOut.model_validate(agent)
    model.onboarded_for_me = await is_onboarded(db, agent.id, viewer_id)
    return model


async def _agents_to_out(
    db: AsyncSession,
    agents: list[Agent],
    viewer_id: uuid.UUID,
) -> list[AgentOut]:
    """List variant that fetches all junction rows in one query."""
    from app.services.onboarding import onboarded_agent_ids
    onboarded = await onboarded_agent_ids(db, viewer_id, [a.id for a in agents])
    out: list[AgentOut] = []
    for a in agents:
        model = AgentOut.model_validate(a)
        model.onboarded_for_me = a.id in onboarded
        out.append(model)
    return out


@router.get("/", response_model=list[AgentOut])
async def list_agents(
    tenant_id: uuid.UUID | None = None,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List all agents the current user has access to."""
    if tenant_id and tenant_id != current_user.tenant_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Can only list agents in your own company",
        )

    requested_tenant_id = current_user.tenant_id

    stmt = build_visible_agents_query(
        current_user,
        tenant_id=requested_tenant_id,
    ).order_by(Agent.created_at.desc())

    result = await db.execute(stmt)
    agents = result.scalars().all()
    # Lazy reset token counters
    needs_flush = False
    for a in agents:
        if await _lazy_reset_token_counters(a, db):
            needs_flush = True
    if needs_flush:
        await db.commit()
    unread_by_agent = await _build_unread_count_by_agent(db, agents, current_user)
    from app.services.onboarding import onboarded_agent_ids
    onboarded = await onboarded_agent_ids(db, current_user.id, [a.id for a in agents])
    out: list[AgentOut] = []
    for a in agents:
        model = _serialize_agent_out(a, unread_by_agent.get(str(a.id), 0))
        model.onboarded_for_me = a.id in onboarded
        out.append(model)
    return out


@router.post("/", status_code=status.HTTP_201_CREATED)
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

    # Determine target tenant: normally user's tenant; admins can override via payload
    target_tenant_id = current_user.tenant_id
    if current_user.role in ("platform_admin", "org_admin") and data.tenant_id:
        target_tenant_id = data.tenant_id

    # Get default limits from target tenant
    max_llm_calls = 100
    default_max_triggers = 20
    default_min_poll = 5
    default_webhook_rate = 5
    default_heartbeat_interval = 240  # model default
    tenant_default_model_id = None
    if target_tenant_id:
        from app.models.tenant import Tenant
        tenant_result = await db.execute(select(Tenant).where(Tenant.id == target_tenant_id))
        tenant = tenant_result.scalar_one_or_none()
        if tenant:
            max_llm_calls = tenant.default_max_llm_calls_per_day or 100
            default_max_triggers = tenant.default_max_triggers or 20
            default_min_poll = tenant.min_poll_interval_floor or 5
            default_webhook_rate = tenant.max_webhook_rate_ceiling or 5
            tenant_default_model_id = tenant.default_model_id
            # Enforce heartbeat floor: new agents must respect company minimum
            if tenant.min_heartbeat_interval_minutes and tenant.min_heartbeat_interval_minutes > default_heartbeat_interval:
                default_heartbeat_interval = tenant.min_heartbeat_interval_minutes

    # If the caller didn't pick a model, fall back to the tenant's default.
    effective_primary_model_id = data.primary_model_id or tenant_default_model_id

    agent = Agent(
        name=data.name,
        role_description=data.role_description,
        bio=data.bio,
        avatar_url=data.avatar_url,
        creator_id=current_user.id,
        tenant_id=target_tenant_id,
        agent_type=data.agent_type or "native",
        primary_model_id=effective_primary_model_id,
        fallback_model_id=data.fallback_model_id,
        max_tokens_per_day=data.max_tokens_per_day,
        max_tokens_per_month=data.max_tokens_per_month,
        template_id=data.template_id,
        status="creating" if data.agent_type != "openclaw" else "idle",
        expires_at=expires_at,
        max_llm_calls_per_day=max_llm_calls,
        max_triggers=default_max_triggers,
        min_poll_interval_min=default_min_poll,
        webhook_rate_limit=default_webhook_rate,
        heartbeat_interval_minutes=default_heartbeat_interval,
    )
    if data.autonomy_policy:
        agent.autonomy_policy = data.autonomy_policy

    db.add(agent)
    await db.flush()

    # Auto-create Participant identity for the new agent
    from app.models.participant import Participant
    db.add(Participant(
        type="agent", ref_id=agent.id,
        display_name=agent.name, avatar_url=agent.avatar_url,
    ))
    await db.flush()

    # Set permissions
    access_level = data.permission_access_level if data.permission_access_level in ("use", "manage") else "use"
    if data.permission_scope_type not in ("company", "user"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unsupported permission_scope_type")
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

    # For OpenClaw agents: skip file system and container setup, generate API key
    if agent.agent_type == "openclaw":
        raw_key = f"oc-{secrets.token_urlsafe(32)}"
        agent.api_key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
        agent.status = "idle"
        await db.commit()

        from app.services.okr_agent_hook import hook_new_agent
        if agent.tenant_id:
            await hook_new_agent(db, agent.id, agent.tenant_id)
            await db.commit()

        out_model = await _agent_to_out(db, agent, current_user.id)
        out = out_model.model_dump()
        out["api_key"] = raw_key  # Return once on creation
        return out

    # Initialize agent file system from template
    from app.services.agent_manager import agent_manager
    await agent_manager.initialize_agent_files(
        db, agent,
        personality=data.personality,
        boundaries=data.boundaries,
    )

    # Copy selected skills + mandatory default skills into agent workspace
    from app.models.skill import Skill
    from sqlalchemy.orm import selectinload

    # Always include global default skills (mcp-installer, skill-creator,
    # complex-task-executor)
    default_result = await db.execute(
        select(Skill).where(Skill.is_default)
    )
    default_ids = {s.id for s in default_result.scalars().all()}

    # Include the template's declared default skills (e.g. trading templates
    # ship with `market-data` / `financial-calendar` in their meta.yaml).
    # Without this, the SKILL.md never reaches `<agent_dir>/skills/<folder>/`,
    # so the agent has no idea those MCP-backed skills exist and silently
    # falls back to web search.
    template_skill_ids: set = set()
    if data.template_id:
        tpl_r = await db.execute(
            select(AgentTemplate).where(AgentTemplate.id == data.template_id)
        )
        tpl = tpl_r.scalar_one_or_none()
        folder_names = list((tpl.default_skills if tpl else None) or [])
        if folder_names:
            tpl_skills_r = await db.execute(
                select(Skill).where(Skill.folder_name.in_(folder_names))
            )
            template_skill_ids = {s.id for s in tpl_skills_r.scalars().all()}

    # Merge user-selected + global default + template-default skill IDs
    all_skill_ids = set(data.skill_ids or []) | default_ids | template_skill_ids

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
                file_path.write_text(sf.content, encoding="utf-8")

    # Auto-install template-declared MCP servers using the system Smithery key.
    # For trading agents, this means shibui/finance lands in the agent's tool
    # list at creation time rather than relying on the agent to install it on
    # first use via the MCP_INSTALLER skill (which depends on LLM compliance).
    # Failures are logged and swallowed — agent creation must not fail because
    # an external Smithery call did.
    template_mcp_servers = list((tpl.default_mcp_servers if data.template_id and tpl else None) or [])
    if template_mcp_servers:
        # Commit the in-flight transaction first so the agent row exists in
        # the database when import_mcp_from_smithery opens its own session
        # to insert AgentTool rows. Without this commit the FK to agents.id
        # is invisible to the parallel session and we get a FK violation.
        await db.commit()
        await db.refresh(agent)

        from loguru import logger
        from app.services.resource_discovery import import_mcp_from_smithery
        for server_id in template_mcp_servers:
            try:
                result_msg = await import_mcp_from_smithery(
                    server_id=server_id,
                    agent_id=agent.id,
                    config={},  # falls back to system Smithery key
                )
                if result_msg.startswith("❌"):
                    logger.warning(
                        f"[create_agent] MCP pre-install for '{server_id}' "
                        f"on agent {agent.id} reported error: {result_msg[:200]}"
                    )
                else:
                    logger.info(
                        f"[create_agent] MCP pre-install '{server_id}' "
                        f"succeeded for agent {agent.id}"
                    )
            except Exception as e:
                logger.warning(
                    f"[create_agent] MCP pre-install for '{server_id}' "
                    f"on agent {agent.id} raised: {e}"
                )

    # Start container
    await agent_manager.start_container(db, agent)
    await db.flush()

    from app.services.okr_agent_hook import hook_new_agent
    if agent.tenant_id:
        await hook_new_agent(db, agent.id, agent.tenant_id)
        await db.commit()

    return await _agent_to_out(db, agent, current_user.id)


@router.get("/{agent_id}")
async def get_agent(
    agent_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get agent details."""
    agent, access_level = await check_agent_access(db, current_user, agent_id)
    # Lazy reset token counters
    if await _lazy_reset_token_counters(agent, db):
        await db.commit()
    out_model = await _agent_to_out(db, agent, current_user.id)
    out = out_model.model_dump()
    out["access_level"] = access_level

    # Resolve creator username (one extra query, only on detail page).
    # IMPORTANT: User.username is an association_proxy to User.identity.username.
    # We must eagerly load the identity relationship (selectinload) to avoid
    # async lazy-loading errors (SQLAlchemy raises MissingGreenlet in async context).
    if agent.creator_id:
        from sqlalchemy.orm import selectinload
        from app.models.user import Identity  # noqa: F401
        creator_result = await db.execute(
            select(User)
            .where(User.id == agent.creator_id)
            .options(selectinload(User.identity))
        )
        creator = creator_result.scalar_one_or_none()
        out["creator_username"] = creator.username if creator else None

    # Resolve effective timezone (agent → tenant → UTC)
    effective_tz = agent.timezone
    if not effective_tz and agent.tenant_id:
        from app.models.tenant import Tenant
        t_result = await db.execute(select(Tenant).where(Tenant.id == agent.tenant_id))
        tenant = t_result.scalar_one_or_none()
        if tenant:
            effective_tz = tenant.timezone or "UTC"
    out["effective_timezone"] = effective_tz or "UTC"

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
    if scope_type not in ("company", "user"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unsupported scope_type")

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
        from datetime import datetime, timezone as tz
        new_expires = update_data["expires_at"]
        # Allow any value: extend, shorten, or null (permanent).
        # Re-activate the agent if new expiry is in the future or cleared.
        if new_expires is None or new_expires > datetime.now(tz.utc):
            if agent.is_expired:
                agent.is_expired = False
                agent.status = "idle"

    # Enforce heartbeat floor from tenant
    clamped_fields = []  # track fields adjusted by tenant floor
    if "heartbeat_interval_minutes" in update_data and current_user.tenant_id:
        from app.models.tenant import Tenant
        t_result = await db.execute(select(Tenant).where(Tenant.id == current_user.tenant_id))
        tenant = t_result.scalar_one_or_none()
        if tenant and update_data["heartbeat_interval_minutes"] < tenant.min_heartbeat_interval_minutes:
            update_data["heartbeat_interval_minutes"] = tenant.min_heartbeat_interval_minutes
            clamped_fields.append({
                "field": "heartbeat_interval_minutes",
                "requested": update_data["heartbeat_interval_minutes"],
                "applied": tenant.min_heartbeat_interval_minutes,
                "reason": "company_floor",
            })

    # Enforce trigger limit floors from tenant
    trigger_fields = {"min_poll_interval_min", "webhook_rate_limit", "max_triggers"}
    if trigger_fields & set(update_data.keys()) and current_user.tenant_id:
        from app.models.tenant import Tenant
        t_result = await db.execute(select(Tenant).where(Tenant.id == current_user.tenant_id))
        tenant = t_result.scalar_one_or_none()
        if tenant:
            if "min_poll_interval_min" in update_data:
                original = update_data["min_poll_interval_min"]
                update_data["min_poll_interval_min"] = max(original, tenant.min_poll_interval_floor)
                if update_data["min_poll_interval_min"] != original:
                    clamped_fields.append({
                        "field": "min_poll_interval_min",
                        "requested": original,
                        "applied": update_data["min_poll_interval_min"],
                        "reason": "company_floor",
                    })
            if "webhook_rate_limit" in update_data:
                original = update_data["webhook_rate_limit"]
                update_data["webhook_rate_limit"] = min(original, tenant.max_webhook_rate_ceiling)
                if update_data["webhook_rate_limit"] != original:
                    clamped_fields.append({
                        "field": "webhook_rate_limit",
                        "requested": original,
                        "applied": update_data["webhook_rate_limit"],
                        "reason": "company_ceiling",
                    })

    for field, value in update_data.items():
        setattr(agent, field, value)
    await db.flush()

    # Sync Participant display_name / avatar if changed
    if "name" in update_data or "avatar_url" in update_data:
        from app.models.participant import Participant
        p_r = await db.execute(select(Participant).where(Participant.type == "agent", Participant.ref_id == agent_id))
        p = p_r.scalar_one_or_none()
        if p:
            if "name" in update_data:
                p.display_name = agent.name
            if "avatar_url" in update_data:
                p.avatar_url = agent.avatar_url
            await db.flush()

    out_model = await _agent_to_out(db, agent, current_user.id)
    out = out_model.model_dump()
    if clamped_fields:
        out["_clamped_fields"] = clamped_fields
    return out


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

    # System agents (OKR Agent, etc.) cannot be deleted — they are seeded by the
    # platform and required for core features. Disable them via settings instead.
    if agent.is_system:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="System agents cannot be deleted. Disable the related feature (e.g. OKR) in Company Settings instead.",
        )

    # Stop container and archive files (best effort)
    from app.services.agent_manager import agent_manager
    archive_dir: Path | None = None
    try:
        await agent_manager.remove_container(agent)
    except Exception:
        pass
    try:
        archive_dir = await agent_manager.archive_agent_files(agent.id)
    except Exception:
        pass
    if archive_dir is not None:
        try:
            await _archive_agent_task_history(db, agent.id, archive_dir)
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
        "chat_sessions",
        "agent_schedules",
        "agent_triggers",
        "channel_configs",
        "agent_permissions",
        "agent_tools",
        "agent_relationships",
        "gateway_messages",
        "published_pages",
        "notifications",
        "daily_token_usage",
    ]

    for table in cleanup_tables:
        try:
            async with db.begin_nested():
                await db.execute(text(f"DELETE FROM {table} WHERE agent_id = :aid"), {"aid": agent_id})
        except Exception:
            pass

    # Clean up secondary FK columns that also reference agents table
    secondary_fk_cleanups = [
        "DELETE FROM task_logs WHERE task_id IN (SELECT id FROM tasks WHERE agent_id = :aid)",
        "DELETE FROM tasks WHERE agent_id = :aid",
        "DELETE FROM chat_sessions WHERE peer_agent_id = :aid",
        "DELETE FROM gateway_messages WHERE sender_agent_id = :aid",
        "UPDATE chat_messages SET sender_agent_id = NULL WHERE sender_agent_id = :aid",
    ]
    for sql in secondary_fk_cleanups:
        try:
            async with db.begin_nested():
                await db.execute(text(sql), {"aid": agent_id})
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

    # Clean up Participant identity
    try:
        async with db.begin_nested():
            await db.execute(
                text("DELETE FROM participants WHERE type = 'agent' AND ref_id = :aid"),
                {"aid": agent_id},
            )
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
    return await _agent_to_out(db, agent, current_user.id)


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
    return await _agent_to_out(db, agent, current_user.id)


# ─── Agent-Level Approvals ──────────────────────────────


@router.get("/{agent_id}/approvals")
async def list_agent_approvals(
    agent_id: uuid.UUID,
    status_filter: str | None = None,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List approval requests for a specific agent. Only creator or admin can view."""
    agent, _access = await check_agent_access(db, current_user, agent_id)
    if not is_agent_creator(current_user, agent) and current_user.role not in ("platform_admin", "org_admin"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only agent creator or admin can view approvals")

    from app.models.audit import ApprovalRequest
    query = select(ApprovalRequest).where(ApprovalRequest.agent_id == agent_id)
    if status_filter:
        query = query.where(ApprovalRequest.status == status_filter)
    query = query.order_by(ApprovalRequest.created_at.desc())
    result = await db.execute(query)
    approvals = result.scalars().all()

    return [
        {
            "id": str(a.id),
            "agent_id": str(a.agent_id),
            "action_type": a.action_type,
            "details": a.details,
            "status": a.status,
            "created_at": a.created_at.isoformat() if a.created_at else None,
            "resolved_at": a.resolved_at.isoformat() if a.resolved_at else None,
            "resolved_by": str(a.resolved_by) if a.resolved_by else None,
        }
        for a in approvals
    ]


@router.post("/{agent_id}/approvals/{approval_id}/resolve")
async def resolve_agent_approval(
    agent_id: uuid.UUID,
    approval_id: uuid.UUID,
    data: dict,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Approve or reject a pending approval for a specific agent."""
    agent, _access = await check_agent_access(db, current_user, agent_id)

    from app.services.autonomy_service import autonomy_service
    action = data.get("action", "reject")
    try:
        approval = await autonomy_service.resolve_approval(db, approval_id, current_user, action)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    await db.commit()
    return {
        "id": str(approval.id),
        "status": approval.status,
        "resolved_at": approval.resolved_at.isoformat() if approval.resolved_at else None,
    }


# ─── OpenClaw API Key Management ────────────────────────


@router.post("/{agent_id}/api-key")
async def generate_or_reset_api_key(
    agent_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Generate or regenerate API key for an OpenClaw agent."""
    agent, _access = await check_agent_access(db, current_user, agent_id)
    if not is_agent_creator(current_user, agent) and current_user.role not in ("platform_admin", "org_admin"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only creator or admin can manage API keys")
    if getattr(agent, "agent_type", "native") != "openclaw":
        raise HTTPException(status_code=400, detail="API keys are only available for OpenClaw agents")

    raw_key = f"oc-{secrets.token_urlsafe(32)}"
    agent.api_key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
    await db.commit()

    return {"api_key": raw_key, "message": "Key configured successfully."}


@router.get("/{agent_id}/gateway-messages")
async def list_gateway_messages(
    agent_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List recent gateway messages for an OpenClaw agent."""
    agent, _access = await check_agent_access(db, current_user, agent_id)

    from app.models.gateway_message import GatewayMessage
    result = await db.execute(
        select(GatewayMessage)
        .where(GatewayMessage.agent_id == agent_id)
        .order_by(GatewayMessage.created_at.desc())
        .limit(50)
    )
    messages = result.scalars().all()

    out = []
    for m in messages:
        sender_name = None
        if m.sender_agent_id:
            r = await db.execute(select(Agent.name).where(Agent.id == m.sender_agent_id))
            sender_name = r.scalar_one_or_none()
        out.append({
            "id": str(m.id),
            "sender_agent_name": sender_name,
            "content": m.content,
            "status": m.status,
            "result": m.result,
            "created_at": m.created_at.isoformat() if m.created_at else None,
            "delivered_at": m.delivered_at.isoformat() if m.delivered_at else None,
            "completed_at": m.completed_at.isoformat() if m.completed_at else None,
        })
    return out
