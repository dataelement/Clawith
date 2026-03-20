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
    EnterpriseInfoUpdate, LLMModelCreate, LLMModelOut, LLMModelUpdate
)
from app.services.autonomy_service import autonomy_service
from app.services.enterprise_sync import enterprise_sync_service
from app.services.llm_utils import get_provider_manifest

router = APIRouter(prefix="/enterprise", tags=["enterprise"])


# ─── LLM Model Pool ────────────────────────────────────

@router.get("/llm-providers")
async def list_llm_providers(
    current_user: User = Depends(get_current_user),
):
    """List supported LLM providers and capabilities from registry."""
    return get_provider_manifest()


class LLMTestRequest(BaseModel):
    provider: str
    model: str
    api_key: str | None = None
    base_url: str | None = None
    model_id: str | None = None  # existing model ID to use stored API key


@router.post("/llm-test")
async def test_llm_model(
    data: LLMTestRequest,
    current_user: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    """Test an LLM model configuration by making a simple API call."""
    import time
    from app.services.llm_client import create_llm_client

    # Resolve API key: use provided key, or look up from stored model
    api_key = data.api_key if data.api_key and not data.api_key.startswith('****') else None
    if not api_key and data.model_id:
        result = await db.execute(select(LLMModel).where(LLMModel.id == data.model_id))
        existing = result.scalar_one_or_none()
        if existing:
            api_key = existing.api_key_encrypted
    if not api_key:
        return {"success": False, "latency_ms": 0, "error": "API Key is required"}

    start = time.time()
    try:
        client = create_llm_client(
            provider=data.provider,
            model=data.model,
            api_key=api_key,
            base_url=data.base_url or None,
        )
        # Simple test: ask model to say "ok"
        from app.services.llm_client import LLMMessage
        response = await client.complete(
            messages=[LLMMessage(role="user", content="Say 'ok' and nothing else.")],
            max_tokens=16,
        )
        latency_ms = int((time.time() - start) * 1000)
        reply = (response.content or "")[:100] if response else ""
        return {"success": True, "latency_ms": latency_ms, "reply": reply}
    except Exception as e:
        latency_ms = int((time.time() - start) * 1000)
        return {"success": False, "latency_ms": latency_ms, "error": str(e)[:500]}



@router.get("/llm-models", response_model=list[LLMModelOut])
async def list_llm_models(
    tenant_id: str | None = None,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List LLM models scoped to the selected tenant."""
    tid = tenant_id or str(current_user.tenant_id) if current_user.tenant_id else None
    query = select(LLMModel).order_by(LLMModel.created_at.desc())
    if tid:
        query = query.where(LLMModel.tenant_id == uuid.UUID(tid))
    result = await db.execute(query)
    models = []
    for m in result.scalars().all():
        out = LLMModelOut.model_validate(m)
        # Mask API key: show last 4 chars
        key = m.api_key_encrypted or ""
        out.api_key_masked = f"****{key[-4:]}" if len(key) > 4 else "****"
        models.append(out)
    return models


@router.post("/llm-models", response_model=LLMModelOut, status_code=status.HTTP_201_CREATED)
async def add_llm_model(
    data: LLMModelCreate,
    tenant_id: str | None = None,
    current_user: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    """Add a new LLM model to the tenant's pool (admin)."""
    tid = tenant_id or (str(current_user.tenant_id) if current_user.tenant_id else None)
    model = LLMModel(
        provider=data.provider,
        model=data.model,
        api_key_encrypted=data.api_key,  # TODO: encrypt
        base_url=data.base_url,
        label=data.label,
        max_tokens_per_day=data.max_tokens_per_day,
        enabled=data.enabled,
        supports_vision=data.supports_vision,
        tenant_id=uuid.UUID(tid) if tid else None,
    )
    db.add(model)
    await db.flush()
    return LLMModelOut.model_validate(model)


@router.delete("/llm-models/{model_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_llm_model(
    model_id: uuid.UUID,
    force: bool = False,
    current_user: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    """Remove an LLM model from the pool."""
    result = await db.execute(select(LLMModel).where(LLMModel.id == model_id))
    model = result.scalar_one_or_none()
    if not model:
        raise HTTPException(status_code=404, detail="Model not found")

    # Check if any agents reference this model
    from sqlalchemy import or_, update
    ref_result = await db.execute(
        select(Agent.name).where(
            or_(Agent.primary_model_id == model_id, Agent.fallback_model_id == model_id)
        )
    )
    agent_names = [row[0] for row in ref_result.all()]

    if agent_names and not force:
        raise HTTPException(
            status_code=409,
            detail={
                "message": f"This model is used by {len(agent_names)} agent(s)",
                "agents": agent_names,
            },
        )

    # Nullify FK references in agents before deleting
    if agent_names:
        await db.execute(
            update(Agent).where(Agent.primary_model_id == model_id).values(primary_model_id=None)
        )
        await db.execute(
            update(Agent).where(Agent.fallback_model_id == model_id).values(fallback_model_id=None)
        )
    await db.delete(model)
    await db.commit()


@router.put("/llm-models/{model_id}", response_model=LLMModelOut)
async def update_llm_model(
    model_id: uuid.UUID,
    data: LLMModelUpdate,
    current_user: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    """Update an existing LLM model in the pool (admin)."""
    result = await db.execute(select(LLMModel).where(LLMModel.id == model_id))
    model = result.scalar_one_or_none()
    if not model:
        raise HTTPException(status_code=404, detail="Model not found")

    try:
        if data.provider:
            model.provider = data.provider
        if data.model:
            model.model = data.model
        if data.label is not None:
            model.label = data.label
        if hasattr(data, 'base_url') and data.base_url is not None:
            model.base_url = data.base_url
        if data.api_key and data.api_key.strip() and not data.api_key.startswith('****'):  # Skip masked values
            model.api_key_encrypted = data.api_key.strip()
        if data.max_tokens_per_day is not None:
            model.max_tokens_per_day = data.max_tokens_per_day
        if data.enabled is not None:
            model.enabled = data.enabled
        if hasattr(data, 'supports_vision') and data.supports_vision is not None:
            model.supports_vision = data.supports_vision

        await db.commit()
        await db.refresh(model)
        return LLMModelOut.model_validate(model)
    except SQLAlchemyError as e:
        await db.rollback()
        raise HTTPException(status_code=500, detail="Failed to update model")


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
    tenant_id: str | None = None,
    status_filter: str | None = None,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List approval requests scoped to a tenant."""
    query = select(ApprovalRequest)
    # Scope by tenant: only show approvals for agents belonging to this tenant
    tid = tenant_id or (str(current_user.tenant_id) if current_user.tenant_id else None)
    if tid:
        tenant_agent_ids = select(Agent.id).where(Agent.tenant_id == tid)
        query = query.where(ApprovalRequest.agent_id.in_(tenant_agent_ids))
    # Non-admins further restricted to their own agents
    if current_user.role != "platform_admin":
        query = query.where(ApprovalRequest.agent_id.in_(
            select(Agent.id).where(Agent.creator_id == current_user.id)
        ))
    if status_filter:
        query = query.where(ApprovalRequest.status == status_filter)
    query = query.order_by(ApprovalRequest.created_at.desc())

    result = await db.execute(query)
    approvals = result.scalars().all()

    # Batch-load agent names
    agent_ids_set = {a.agent_id for a in approvals}
    agent_names: dict[uuid.UUID, str] = {}
    if agent_ids_set:
        agents_r = await db.execute(select(Agent.id, Agent.name).where(Agent.id.in_(agent_ids_set)))
        agent_names = {row.id: row.name for row in agents_r.all()}

    out = []
    for a in approvals:
        d = ApprovalRequestOut.model_validate(a)
        d.agent_name = agent_names.get(a.agent_id)
        out.append(d)
    return out


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
    tenant_id: str | None = None,
    limit: int = 50,
    current_user: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    """List audit logs scoped to a tenant (admin only)."""
    query = select(AuditLog).order_by(AuditLog.created_at.desc()).limit(limit)
    # Scope by tenant: only show logs for agents belonging to this tenant
    tid = tenant_id or (str(current_user.tenant_id) if current_user.tenant_id else None)
    if tid:
        tenant_agent_ids = select(Agent.id).where(Agent.tenant_id == tid)
        query = query.where(AuditLog.agent_id.in_(tenant_agent_ids))
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
    default_max_triggers: int | None = None
    min_poll_interval_floor: int | None = None
    max_webhook_rate_ceiling: int | None = None


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
        "default_max_triggers": tenant.default_max_triggers,
        "min_poll_interval_floor": tenant.min_poll_interval_floor,
        "max_webhook_rate_ceiling": tenant.max_webhook_rate_ceiling,
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
        adjusted_count = await enforce_heartbeat_floor(
            tenant.id, floor=data.min_heartbeat_interval_minutes, db=db
        )

    # Handle trigger limit fields
    if data.default_max_triggers is not None:
        tenant.default_max_triggers = data.default_max_triggers
    if data.min_poll_interval_floor is not None:
        tenant.min_poll_interval_floor = data.min_poll_interval_floor
    if data.max_webhook_rate_ceiling is not None:
        tenant.max_webhook_rate_ceiling = data.max_webhook_rate_ceiling

    await db.commit()
    return {
        "message": "Tenant quotas updated",
        "heartbeat_agents_adjusted": adjusted_count,
    }


# ─── System Settings ───────────────────────────────────

from app.models.system_settings import SystemSetting


class SettingUpdate(BaseModel):
    value: dict


@router.get("/system-settings/notification_bar/public")
async def get_notification_bar_public(
    db: AsyncSession = Depends(get_db),
):
    """Public (no auth) endpoint to read the notification bar config."""
    result = await db.execute(
        select(SystemSetting).where(SystemSetting.key == "notification_bar")
    )
    setting = result.scalar_one_or_none()
    if not setting or not setting.value:
        return {"enabled": False, "text": ""}
    return {
        "enabled": setting.value.get("enabled", False),
        "text": setting.value.get("text", ""),
    }


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
    # Platform-level settings (e.g. PUBLIC_BASE_URL) require platform_admin
    if key == "platform" and current_user.role != "platform_admin":
        raise HTTPException(status_code=403, detail="Only platform admin can modify platform settings")
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


def _require_tenant_admin(current_user: User) -> None:
    """Check that the user is org_admin or platform_admin with a tenant."""
    if current_user.role not in ("platform_admin", "org_admin"):
        raise HTTPException(status_code=403, detail="Requires admin privileges")
    if not current_user.tenant_id:
        raise HTTPException(status_code=400, detail="No company assigned")


@router.post("/invitation-codes")
async def create_invitation_codes(
    data: InvitationCodeCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Batch-create invitation codes for the current user's company."""
    _require_tenant_admin(current_user)
    import random
    import string

    codes_created = []
    for _ in range(min(data.count, 100)):  # cap at 100 per batch
        code_str = ''.join(random.choices(string.ascii_uppercase + string.digits, k=8))
        code = InvitationCode(
            code=code_str,
            tenant_id=current_user.tenant_id,
            max_uses=data.max_uses,
            created_by=current_user.id,
        )
        db.add(code)
        codes_created.append(code_str)

    await db.commit()
    return {"created": len(codes_created), "codes": codes_created}


@router.get("/invitation-codes")
async def list_invitation_codes(
    page: int = 1,
    page_size: int = 20,
    search: str = "",
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List invitation codes for the current user's company."""
    _require_tenant_admin(current_user)
    from sqlalchemy import func as sqla_func

    base_filter = InvitationCode.tenant_id == current_user.tenant_id
    stmt = select(InvitationCode).where(base_filter)
    count_stmt = select(sqla_func.count()).select_from(InvitationCode).where(base_filter)

    if search:
        stmt = stmt.where(InvitationCode.code.ilike(f"%{search}%"))
        count_stmt = count_stmt.where(InvitationCode.code.ilike(f"%{search}%"))

    total_result = await db.execute(count_stmt)
    total = total_result.scalar() or 0

    offset = (max(page, 1) - 1) * page_size
    result = await db.execute(
        stmt.order_by(InvitationCode.created_at.desc()).offset(offset).limit(page_size)
    )
    codes = result.scalars().all()
    return {
        "items": [
            {
                "id": str(c.id),
                "code": c.code,
                "max_uses": c.max_uses,
                "used_count": c.used_count,
                "is_active": c.is_active,
                "created_at": c.created_at.isoformat() if c.created_at else None,
            }
            for c in codes
        ],
        "total": total,
        "page": page,
        "page_size": page_size,
    }



@router.get("/invitation-codes/export")
async def export_invitation_codes_csv(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Export invitation codes for the current user's company as CSV."""
    _require_tenant_admin(current_user)
    import csv
    import io
    from fastapi.responses import StreamingResponse

    result = await db.execute(
        select(InvitationCode)
        .where(InvitationCode.tenant_id == current_user.tenant_id)
        .order_by(InvitationCode.created_at.asc())
    )
    codes = result.scalars().all()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Code", "Max Uses", "Used Count", "Active", "Created At"])
    for c in codes:
        writer.writerow([
            c.code,
            c.max_uses,
            c.used_count,
            "Yes" if c.is_active else "No",
            c.created_at.strftime("%Y-%m-%d %H:%M:%S") if c.created_at else "",
        ])

    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=invitation_codes.csv"},
    )


@router.delete("/invitation-codes/{code_id}")
async def deactivate_invitation_code(
    code_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Deactivate an invitation code (must belong to current user's company)."""
    _require_tenant_admin(current_user)
    import uuid as _uuid
    result = await db.execute(
        select(InvitationCode).where(
            InvitationCode.id == _uuid.UUID(code_id),
            InvitationCode.tenant_id == current_user.tenant_id,
        )
    )
    code = result.scalar_one_or_none()
    if not code:
        raise HTTPException(status_code=404, detail="Code not found")
    code.is_active = False
    await db.commit()
    return {"status": "deactivated"}
