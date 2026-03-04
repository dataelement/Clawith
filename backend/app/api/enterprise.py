"""Enterprise management API routes: LLM pool, enterprise info, approvals, audit logs."""

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import get_current_admin, get_current_user, require_role
from app.database import get_db
from app.models.agent import Agent
from app.models.audit import ApprovalRequest, AuditLog, EnterpriseInfo
from app.models.llm import LLMModel
from app.models.user import User
from app.schemas.schemas import (
    ApprovalAction, ApprovalRequestOut, AuditLogOut, EnterpriseInfoOut,
    EnterpriseInfoUpdate, LLMModelCreate, LLMModelOut,
)
from app.services.autonomy_service import autonomy_service
from app.services.enterprise_sync import enterprise_sync_service

router = APIRouter(prefix="/enterprise", tags=["enterprise"])


# ─── LLM Model Pool ────────────────────────────────────

@router.get("/llm-models", response_model=list[LLMModelOut])
async def list_llm_models(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List all LLM models in the pool."""
    result = await db.execute(select(LLMModel).order_by(LLMModel.created_at.desc()))
    return [LLMModelOut.model_validate(m) for m in result.scalars().all()]


@router.post("/llm-models", response_model=LLMModelOut, status_code=status.HTTP_201_CREATED)
async def add_llm_model(
    data: LLMModelCreate,
    current_user: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    """Add a new LLM model to the pool (admin)."""
    model = LLMModel(
        provider=data.provider,
        model=data.model,
        api_key_encrypted=data.api_key,  # TODO: encrypt
        base_url=data.base_url,
        label=data.label,
        max_tokens_per_day=data.max_tokens_per_day,
        enabled=data.enabled,
    )
    db.add(model)
    await db.flush()
    return LLMModelOut.model_validate(model)


@router.delete("/llm-models/{model_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_llm_model(
    model_id: uuid.UUID,
    current_user: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    """Remove an LLM model from the pool."""
    result = await db.execute(select(LLMModel).where(LLMModel.id == model_id))
    model = result.scalar_one_or_none()
    if not model:
        raise HTTPException(status_code=404, detail="Model not found")
    await db.delete(model)


@router.put("/llm-models/{model_id}", response_model=LLMModelOut)
async def update_llm_model(
    model_id: uuid.UUID,
    data: LLMModelCreate,
    current_user: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    """Update an existing LLM model in the pool (admin)."""
    result = await db.execute(select(LLMModel).where(LLMModel.id == model_id))
    model = result.scalar_one_or_none()
    if not model:
        raise HTTPException(status_code=404, detail="Model not found")
    model.provider = data.provider
    model.model = data.model
    model.label = data.label
    model.base_url = data.base_url
    if data.api_key:  # Only update API key if provided (not empty)
        model.api_key_encrypted = data.api_key
    model.enabled = data.enabled
    await db.flush()
    return LLMModelOut.model_validate(model)


# ─── Enterprise Info ────────────────────────────────────

@router.get("/info", response_model=list[EnterpriseInfoOut])
async def list_enterprise_info(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List all enterprise information entries."""
    result = await db.execute(select(EnterpriseInfo).order_by(EnterpriseInfo.info_type))
    return [EnterpriseInfoOut.model_validate(e) for e in result.scalars().all()]


@router.put("/info/{info_type}", response_model=EnterpriseInfoOut)
async def update_enterprise_info(
    info_type: str,
    data: EnterpriseInfoUpdate,
    current_user: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    """Create or update enterprise information. Triggers sync to agents."""
    info = await enterprise_sync_service.update_enterprise_info(
        db, info_type, data.content, data.visible_roles, current_user.id
    )
    # Sync to all running agents
    await enterprise_sync_service.sync_to_all_agents(db)
    return EnterpriseInfoOut.model_validate(info)


# ─── Approvals ──────────────────────────────────────────

@router.get("/approvals", response_model=list[ApprovalRequestOut])
async def list_approvals(
    status_filter: str | None = None,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List approval requests for agents the current user created."""
    # Get agent IDs user is creator of
    agent_ids = (
        select(Agent.id).where(Agent.creator_id == current_user.id)
    )
    query = select(ApprovalRequest).where(ApprovalRequest.agent_id.in_(agent_ids))
    if status_filter:
        query = query.where(ApprovalRequest.status == status_filter)
    query = query.order_by(ApprovalRequest.created_at.desc())

    result = await db.execute(query)
    return [ApprovalRequestOut.model_validate(a) for a in result.scalars().all()]


@router.post("/approvals/{approval_id}/resolve", response_model=ApprovalRequestOut)
async def resolve_approval(
    approval_id: uuid.UUID,
    data: ApprovalAction,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Approve or reject a pending approval request."""
    try:
        approval = await autonomy_service.resolve_approval(
            db, approval_id, current_user, data.action
        )
        return ApprovalRequestOut.model_validate(approval)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# ─── Audit Logs ─────────────────────────────────────────

@router.get("/audit-logs", response_model=list[AuditLogOut])
async def list_audit_logs(
    agent_id: uuid.UUID | None = None,
    limit: int = 50,
    current_user: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    """List audit logs (admin only). Optionally filter by agent."""
    query = select(AuditLog).order_by(AuditLog.created_at.desc()).limit(limit)
    if agent_id:
        query = query.where(AuditLog.agent_id == agent_id)
    result = await db.execute(query)
    return [AuditLogOut.model_validate(log) for log in result.scalars().all()]


# ─── Dashboard Stats ────────────────────────────────────

@router.get("/stats")
async def get_enterprise_stats(
    tenant_id: str | None = None,
    current_user: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    """Get enterprise dashboard statistics, optionally scoped to a tenant."""
    # Determine which tenant to filter by
    tid = tenant_id or str(current_user.tenant_id)

    total_agents = await db.execute(
        select(func.count(Agent.id)).where(Agent.tenant_id == tid)
    )
    running_agents = await db.execute(
        select(func.count(Agent.id)).where(Agent.tenant_id == tid, Agent.status == "running")
    )
    total_users = await db.execute(
        select(func.count(User.id)).where(User.tenant_id == tid, User.is_active == True)
    )
    pending_approvals = await db.execute(
        select(func.count(ApprovalRequest.id)).where(ApprovalRequest.status == "pending")
    )

    return {
        "total_agents": total_agents.scalar() or 0,
        "running_agents": running_agents.scalar() or 0,
        "total_users": total_users.scalar() or 0,
        "pending_approvals": pending_approvals.scalar() or 0,
    }


# ─── Tenant Quota Settings ──────────────────────────────

from app.models.tenant import Tenant


class TenantQuotaUpdate(BaseModel):
    default_message_limit: int | None = None
    default_message_period: str | None = None
    default_max_agents: int | None = None
    default_agent_ttl_hours: int | None = None
    default_max_llm_calls_per_day: int | None = None
    min_heartbeat_interval_minutes: int | None = None


@router.get("/tenant-quotas")
async def get_tenant_quotas(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get tenant quota defaults and heartbeat settings."""
    if not current_user.tenant_id:
        return {}
    result = await db.execute(select(Tenant).where(Tenant.id == current_user.tenant_id))
    tenant = result.scalar_one_or_none()
    if not tenant:
        return {}
    return {
        "default_message_limit": tenant.default_message_limit,
        "default_message_period": tenant.default_message_period,
        "default_max_agents": tenant.default_max_agents,
        "default_agent_ttl_hours": tenant.default_agent_ttl_hours,
        "default_max_llm_calls_per_day": tenant.default_max_llm_calls_per_day,
        "min_heartbeat_interval_minutes": tenant.min_heartbeat_interval_minutes,
    }


@router.patch("/tenant-quotas")
async def update_tenant_quotas(
    data: TenantQuotaUpdate,
    current_user: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    """Update tenant quota defaults (admin only). Enforces heartbeat floor on existing agents."""
    if not current_user.tenant_id:
        raise HTTPException(status_code=400, detail="No tenant assigned")

    result = await db.execute(select(Tenant).where(Tenant.id == current_user.tenant_id))
    tenant = result.scalar_one_or_none()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")

    if data.default_message_limit is not None:
        tenant.default_message_limit = data.default_message_limit
    if data.default_message_period is not None:
        tenant.default_message_period = data.default_message_period
    if data.default_max_agents is not None:
        tenant.default_max_agents = data.default_max_agents
    if data.default_agent_ttl_hours is not None:
        tenant.default_agent_ttl_hours = data.default_agent_ttl_hours
    if data.default_max_llm_calls_per_day is not None:
        tenant.default_max_llm_calls_per_day = data.default_max_llm_calls_per_day

    # Handle heartbeat floor — enforce on existing agents
    adjusted_count = 0
    if data.min_heartbeat_interval_minutes is not None:
        tenant.min_heartbeat_interval_minutes = data.min_heartbeat_interval_minutes
        from app.services.quota_guard import enforce_heartbeat_floor
        adjusted_count = await enforce_heartbeat_floor(tenant.id)

    await db.commit()
    return {
        "message": "Tenant quotas updated",
        "heartbeat_agents_adjusted": adjusted_count,
    }


# ─── System Settings ───────────────────────────────────

from app.models.system_settings import SystemSetting


class SettingUpdate(BaseModel):
    value: dict


@router.get("/system-settings/{key}")
async def get_system_setting(
    key: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get a system setting by key."""
    result = await db.execute(select(SystemSetting).where(SystemSetting.key == key))
    setting = result.scalar_one_or_none()
    if not setting:
        return {"key": key, "value": {}}
    return {"key": setting.key, "value": setting.value, "updated_at": setting.updated_at.isoformat() if setting.updated_at else None}


@router.put("/system-settings/{key}")
async def update_system_setting(
    key: str,
    data: SettingUpdate,
    current_user: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    """Create or update a system setting."""
    result = await db.execute(select(SystemSetting).where(SystemSetting.key == key))
    setting = result.scalar_one_or_none()
    if setting:
        setting.value = data.value
    else:
        setting = SystemSetting(key=key, value=data.value)
        db.add(setting)
    await db.commit()
    return {"key": setting.key, "value": setting.value}


# ─── Org Structure ──────────────────────────────────────

from app.models.org import OrgDepartment, OrgMember


@router.get("/org/departments")
async def list_org_departments(
    tenant_id: str | None = None,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List all departments, optionally filtered by tenant."""
    query = select(OrgDepartment)
    if tenant_id:
        query = query.where(OrgDepartment.tenant_id == uuid.UUID(tenant_id))
    result = await db.execute(query.order_by(OrgDepartment.name))
    depts = result.scalars().all()
    return [
        {
            "id": str(d.id),
            "feishu_id": d.feishu_id,
            "name": d.name,
            "parent_id": str(d.parent_id) if d.parent_id else None,
            "path": d.path,
            "member_count": d.member_count,
        }
        for d in depts
    ]


@router.get("/org/members")
async def list_org_members(
    department_id: str | None = None,
    search: str | None = None,
    tenant_id: str | None = None,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List org members, optionally filtered by department, search, or tenant."""
    query = select(OrgMember).where(OrgMember.status == "active")
    if tenant_id:
        query = query.where(OrgMember.tenant_id == uuid.UUID(tenant_id))
    if department_id:
        query = query.where(OrgMember.department_id == uuid.UUID(department_id))
    if search:
        query = query.where(OrgMember.name.ilike(f"%{search}%"))
    query = query.order_by(OrgMember.name).limit(100)
    result = await db.execute(query)
    members = result.scalars().all()
    return [
        {
            "id": str(m.id),
            "name": m.name,
            "email": m.email,
            "title": m.title,
            "department_path": m.department_path,
            "avatar_url": m.avatar_url,
        }
        for m in members
    ]


@router.post("/org/sync")
async def trigger_org_sync(
    current_user: User = Depends(get_current_admin),
):
    """Manually trigger org structure sync from Feishu."""
    from app.services.org_sync_service import org_sync_service
    result = await org_sync_service.full_sync()
    return result


# ─── Invitation Codes ───────────────────────────────────

from app.models.invitation_code import InvitationCode


class InvitationCodeCreate(BaseModel):
    count: int = 1       # how many codes to generate
    max_uses: int = 1    # max registrations per code


@router.post("/invitation-codes")
async def create_invitation_codes(
    data: InvitationCodeCreate,
    current_user: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    """Batch-create invitation codes."""
    import random
    import string

    codes_created = []
    for _ in range(min(data.count, 100)):  # cap at 100 per batch
        code_str = ''.join(random.choices(string.ascii_uppercase + string.digits, k=8))
        code = InvitationCode(code=code_str, max_uses=data.max_uses, created_by=current_user.id)
        db.add(code)
        codes_created.append(code_str)

    await db.commit()
    return {"created": len(codes_created), "codes": codes_created}


@router.get("/invitation-codes")
async def list_invitation_codes(
    current_user: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    """List all invitation codes with usage stats."""
    result = await db.execute(
        select(InvitationCode).order_by(InvitationCode.created_at.desc())
    )
    codes = result.scalars().all()
    return [
        {
            "id": str(c.id),
            "code": c.code,
            "max_uses": c.max_uses,
            "used_count": c.used_count,
            "is_active": c.is_active,
            "created_at": c.created_at.isoformat() if c.created_at else None,
        }
        for c in codes
    ]


@router.delete("/invitation-codes/{code_id}")
async def deactivate_invitation_code(
    code_id: str,
    current_user: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    """Deactivate an invitation code."""
    import uuid as _uuid
    result = await db.execute(select(InvitationCode).where(InvitationCode.id == _uuid.UUID(code_id)))
    code = result.scalar_one_or_none()
    if not code:
        raise HTTPException(status_code=404, detail="Code not found")
    code.is_active = False
    await db.commit()
    return {"status": "deactivated"}

