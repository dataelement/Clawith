"""Enterprise management API routes: LLM pool, enterprise info, approvals, audit logs."""

import uuid
import logging

logger = logging.getLogger(__name__)

from fastapi import APIRouter, Depends, HTTPException, status, BackgroundTasks, Request
from pydantic import BaseModel
from sqlalchemy import select, func, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core.security import get_current_admin, get_current_user, require_role, encrypt_data
from app.database import async_session, get_db
from app.models.org import OrgDepartment, OrgMember
from app.models.identity import IdentityProvider
from app.models.user import User
from app.services.org_sync_adapter import derive_member_department_paths
from app.models.agent import Agent
from app.models.llm import LLMModel
from app.models.audit import AuditLog, ApprovalRequest, EnterpriseInfo
from app.schemas.schemas import (
    ApprovalAction, ApprovalRequestOut, AuditLogOut, EnterpriseInfoOut,
    EnterpriseInfoUpdate, LLMModelCreate, LLMModelOut, LLMModelUpdate,
    IdentityProviderOut, UserInviteRequest
)
from app.services.autonomy_service import autonomy_service
from app.services.enterprise_sync import enterprise_sync_service
from app.services.llm import get_provider_manifest, get_model_api_key, create_llm_client, LLMMessage
from app.services.platform_service import platform_service
from app.services.sso_service import sso_service

router = APIRouter(prefix="/enterprise", tags=["enterprise"])
settings = get_settings()


# ─── Public: Check Email Exists ────────────────────────

class CheckEmailRequest(BaseModel):
    email: str


@router.post("/check-email-exists")
async def check_email_exists(
    data: CheckEmailRequest,
    db: AsyncSession = Depends(get_db),
):
    """Public endpoint — check if an email address is already registered on this platform.

    Used by the invitation flow to decide whether to show the login or register form.
    Only returns a boolean; does not expose any user data.
    """
    from app.models.user import Identity
    result = await db.execute(
        select(Identity).where(Identity.email == data.email.strip().lower())
    )
    exists = result.scalar_one_or_none() is not None
    return {"exists": exists}



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


async def _load_llm_test_api_key(model_id: str | None) -> str | None:
    """Load the stored API key for llm-test using a short-lived independent session."""
    if not model_id:
        return None

    async with async_session() as session:
        result = await session.execute(select(LLMModel).where(LLMModel.id == model_id))
        existing = result.scalar_one_or_none()
        return get_model_api_key(existing) if existing else None


@router.post("/llm-test")
async def test_llm_model(
    data: LLMTestRequest,
    current_user: User = Depends(get_current_admin),
):
    """Test an LLM model configuration by making a simple API call."""
    import time

    # Resolve API key: use provided key, or look up from stored model
    api_key = data.api_key if data.api_key and not data.api_key.startswith('****') else None
    if not api_key and data.model_id:
        api_key = await _load_llm_test_api_key(data.model_id)
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
    # Authorization: non-platform admins can only see their own tenant's models
    if tenant_id and current_user.role != "platform_admin":
        if str(current_user.tenant_id) != tenant_id:
            raise HTTPException(status_code=403, detail="Cannot access other tenant's models")

    tid = tenant_id or str(current_user.tenant_id) if current_user.tenant_id else None
    query = select(LLMModel).order_by(LLMModel.created_at.desc())
    if tid:
        query = query.where(LLMModel.tenant_id == uuid.UUID(tid))
    result = await db.execute(query)
    models = []
    for m in result.scalars().all():
        out = LLMModelOut.model_validate(m)
        # Mask API key: show last 4 chars
        key = get_model_api_key(m)
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
        api_key_encrypted=encrypt_data(data.api_key, settings.SECRET_KEY),
        base_url=data.base_url,
        label=data.label,
        temperature=data.temperature,
        max_tokens_per_day=data.max_tokens_per_day,
        enabled=data.enabled,
        supports_vision=data.supports_vision,
        max_output_tokens=data.max_output_tokens,
        request_timeout=data.request_timeout,
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
    from sqlalchemy import or_
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
            model.api_key_encrypted = encrypt_data(data.api_key.strip(), settings.SECRET_KEY)
        if data.temperature is not None:
            model.temperature = data.temperature
        if data.max_tokens_per_day is not None:
            model.max_tokens_per_day = data.max_tokens_per_day
        if data.enabled is not None:
            model.enabled = data.enabled
        if hasattr(data, 'supports_vision') and data.supports_vision is not None:
            model.supports_vision = data.supports_vision
        if hasattr(data, 'max_output_tokens') and data.max_output_tokens is not None:
            model.max_output_tokens = data.max_output_tokens
        if hasattr(data, 'request_timeout') and data.request_timeout is not None:
            model.request_timeout = data.request_timeout

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
    tid = tenant_id
    if tid and isinstance(tid, str):
        tid = uuid.UUID(tid)
    elif not tid:
        tid = current_user.tenant_id

    # Base queries
    agent_q = select(func.count(Agent.id))
    user_q = select(func.count(User.id)).where(User.is_active == True)
    approval_q = select(func.count(ApprovalRequest.id))

    if tid:
        agent_q = agent_q.where(Agent.tenant_id == tid)
        user_q = user_q.where(User.tenant_id == tid)
        # For approvals, we only see requests for agents in this tenant
        approval_q = approval_q.where(ApprovalRequest.agent_id.in_(
            select(Agent.id).where(Agent.tenant_id == tid)
        ))

    total_agents = await db.execute(agent_q)
    running_agents = await db.execute(
        agent_q.where(Agent.status == "running")
    )
    total_users = await db.execute(user_q)
    pending_approvals = await db.execute(
        approval_q.where(ApprovalRequest.status == "pending")
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


# ── System Email: Test & Templates ──────────────────────


class TestEmailRequest(BaseModel):
    email: str


@router.post("/system-email/test")
async def send_test_email_endpoint(
    data: TestEmailRequest,
    current_user: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    """Send a test email to verify SMTP configuration (admin only)."""
    from app.services.system_email_service import send_test_email

    try:
        await send_test_email(data.email, db=db)
        return {"success": True, "message": f"Test email sent to {data.email}"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/email-templates")
async def get_email_templates_endpoint(
    current_user: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    """Get email templates (current values + available variables per scenario)."""
    from app.services.system_email_service import (
        get_email_templates,
        EMAIL_TEMPLATE_VARIABLES,
        DEFAULT_EMAIL_TEMPLATES,
    )

    templates = await get_email_templates(db=db)
    return {
        "templates": templates,
        "variables": EMAIL_TEMPLATE_VARIABLES,
        "defaults": DEFAULT_EMAIL_TEMPLATES,
    }


class EmailTemplatesUpdate(BaseModel):
    templates: dict


@router.put("/email-templates")
async def update_email_templates_endpoint(
    data: EmailTemplatesUpdate,
    current_user: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    """Save email templates (admin only)."""
    from app.services.system_email_service import EMAIL_TEMPLATE_VARIABLES

    # Validate that only known scenario keys are provided
    for key in data.templates:
        if key not in EMAIL_TEMPLATE_VARIABLES:
            raise HTTPException(
                status_code=400,
                detail=f"Unknown email template scenario: {key}"
            )

    result = await db.execute(
        select(SystemSetting).where(SystemSetting.key == "email_templates")
    )
    setting = result.scalar_one_or_none()
    if setting:
        setting.value = data.templates
    else:
        setting = SystemSetting(key="email_templates", value=data.templates)
        db.add(setting)
    await db.commit()
    return {"success": True, "message": "Email templates saved"}


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

    # When public_base_url changes, regenerate sso_domain for all SSO-enabled tenants
    if key == "platform" and data.value.get("public_base_url"):
        await _regenerate_all_sso_domains(db)

    return {"key": setting.key, "value": setting.value}


# ─── SSO Derived State Helper ───────────────────────────

async def _sync_tenant_sso_state(db: AsyncSession, tenant_id: uuid.UUID):
    """Recompute tenant.sso_enabled based on channel-level sso_login_enabled flags.

    When any identity provider has sso_login_enabled=True, the tenant's
    sso_enabled is set to True and sso_domain is auto-assigned if empty.
    When all providers have sso_login_enabled=False, sso_enabled becomes False
    but sso_domain is preserved for potential re-enablement.

    Raises HTTPException(400) if IP mode and another tenant already owns the sso_domain.
    """
    from app.models.tenant import Tenant
    count_result = await db.execute(
        select(func.count(IdentityProvider.id)).where(
            IdentityProvider.tenant_id == tenant_id,
            IdentityProvider.sso_login_enabled == True,
            IdentityProvider.is_active == True,
        )
    )
    active_sso_count = count_result.scalar() or 0

    tenant_result = await db.execute(select(Tenant).where(Tenant.id == tenant_id))
    tenant = tenant_result.scalar_one_or_none()
    if not tenant:
        return

    tenant.sso_enabled = active_sso_count > 0

    # Auto-assign subdomain on first SSO enablement based on Platform rules
    if tenant.sso_enabled and not tenant.sso_domain:
        sso_base = await platform_service.get_tenant_sso_base_url(db, tenant)
        host = sso_base.split("://")[-1].split(":")[0].split("/")[0]
        is_ip = platform_service.is_ip_address(host)

        if is_ip:
            # IP mode: first clear ALL other tenants' sso_domain, then set for this tenant
            # (unique constraint - only one tenant can hold the IP domain)
            await db.execute(
                update(Tenant)
                .where(Tenant.id != tenant_id)
                .values(sso_domain=None, sso_enabled=False)
            )
            logger.info(f"[SSO] IP mode: cleared sso_domain for all other tenants, setting for tenant_id={tenant_id}")

        tenant.sso_domain = sso_base

    await db.commit()


async def _regenerate_all_sso_domains(db: AsyncSession):
    """Regenerate sso_domain for ALL tenants when public_base_url changes.

    - Domain mode: every tenant gets {slug}.{domain}, regardless of SSO status.
    - IP mode: only ONE tenant can hold the IP domain (unique constraint).
      The first SSO-enabled tenant keeps it; all others get sso_domain=None.
      If no SSO-enabled tenant exists, the first tenant in the list gets it.
    """
    base_url = await platform_service.get_public_base_url(db)
    host = base_url.split("://")[-1].split(":")[0].split("/")[0]
    is_ip = platform_service.is_ip_address(host)

    # Fetch all tenants; put SSO-enabled ones first so they win the IP slot
    all_tenants_result = await db.execute(
        select(Tenant).order_by(Tenant.sso_enabled.desc(), Tenant.created_at.asc())
    )
    tenants = all_tenants_result.scalars().all()

    for i, tenant in enumerate(tenants):
        if is_ip:
            # IP mode: only one tenant can have SSO domain
            if i == 0:
                sso_base = await platform_service.get_tenant_sso_base_url(db, tenant)
                tenant.sso_domain = sso_base
            else:
                tenant.sso_domain = None
        else:
            # Domain mode: each tenant gets their own subdomain
            sso_base = await platform_service.get_tenant_sso_base_url(db, tenant)
            tenant.sso_domain = sso_base
        logger.info(f"[SSO regen] tenant={tenant.slug} sso_domain={tenant.sso_domain}")

    if tenants:
        await db.commit()


# ─── Identity Providers ─────────────────────────────────

@router.get("/identity-providers", response_model=list[IdentityProviderOut])
async def list_identity_providers(
    tenant_id: str | None = None,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List identity providers configured for the tenant."""
    # Authorization: non-platform admins can only see their own tenant's providers
    if tenant_id and current_user.role != "platform_admin":
        if str(current_user.tenant_id) != tenant_id:
            raise HTTPException(status_code=403, detail="Cannot access other tenant's providers")

    query = select(IdentityProvider).order_by(IdentityProvider.created_at.desc())
    tid = tenant_id or (str(current_user.tenant_id) if current_user.tenant_id else None)

    # Require tenant context
    if not tid:
        if current_user.role == "platform_admin":
            # Admin without tenant_id filter sees all
            pass
        else:
            raise HTTPException(status_code=400, detail="tenant_id is required for identity providers")
    else:
        import uuid as _uuid
        query = query.where(IdentityProvider.tenant_id == _uuid.UUID(tid))

    result = await db.execute(query)
    providers = []
    for p in result.scalars().all():
        providers.append(_identity_provider_response(p))
    return providers


class IdentityProviderCreate(BaseModel):
    provider_type: str
    name: str
    is_active: bool = True
    sso_login_enabled: bool = False
    config: dict = {}
    tenant_id: uuid.UUID | None = None


class OAuth2Config(BaseModel):
    """OAuth2 provider configuration with friendly field names."""
    app_id: str | None = None          # Alias for client_id
    app_secret: str | None = None       # Alias for client_secret
    authorize_url: str | None = None    # OAuth2 authorize endpoint
    token_url: str | None = None        # OAuth2 token endpoint
    user_info_url: str | None = None    # OAuth2 user info endpoint
    scope: str | None = "openid profile email"

    def to_config_dict(self) -> dict:
        """Convert to config dict with both naming conventions for compatibility."""
        config = {}
        if self.app_id:
            config["app_id"] = self.app_id
            config["client_id"] = self.app_id
        if self.app_secret:
            config["app_secret"] = self.app_secret
            config["client_secret"] = self.app_secret
        if self.authorize_url:
            config["authorize_url"] = self.authorize_url
        if self.token_url:
            config["token_url"] = self.token_url
        if self.user_info_url:
            config["user_info_url"] = self.user_info_url
        if self.scope:
            config["scope"] = self.scope
        return config

    @classmethod
    def from_config_dict(cls, config: dict) -> "OAuth2Config":
        """Create from config dict, supporting both naming conventions."""
        return cls(
            app_id=config.get("app_id") or config.get("client_id"),
            app_secret=config.get("app_secret") or config.get("client_secret"),
            authorize_url=config.get("authorize_url"),
            token_url=config.get("token_url"),
            user_info_url=config.get("user_info_url"),
            scope=config.get("scope"),
        )


class IdentityProviderOAuth2Create(BaseModel):
    """Simplified OAuth2 provider creation with dedicated fields."""
    provider_type: str = "oauth2"
    name: str
    is_active: bool = True
    app_id: str
    app_secret: str
    authorize_url: str
    token_url: str
    user_info_url: str
    scope: str | None = "openid profile email"
    tenant_id: uuid.UUID | None = None


def normalize_oauth2_config(config: dict) -> dict:
    """Normalize OAuth2 config to use both naming conventions for compatibility."""
    if "app_id" in config or "app_secret" in config or "authorize_url" in config:
        # Mix of naming conventions - normalize
        normalized = {}
        if "app_id" in config:
            normalized["app_id"] = config["app_id"]
            normalized["client_id"] = config["app_id"]
        elif "client_id" in config:
            normalized["app_id"] = config["client_id"]
            normalized["client_id"] = config["client_id"]

        if "app_secret" in config:
            normalized["app_secret"] = config["app_secret"]
            normalized["client_secret"] = config["app_secret"]
        elif "client_secret" in config:
            normalized["app_secret"] = config["client_secret"]
            normalized["client_secret"] = config["client_secret"]

        # Copy URLs if present
        for key in ["authorize_url", "token_url", "user_info_url", "scope"]:
            if key in config:
                normalized[key] = config[key]

        return normalized
    return config

def validate_provider_config(provider_type: str, config: dict):
    """Validate identity provider config. Specific field checks are handled by the frontend."""
    if not isinstance(config, dict):
        raise HTTPException(status_code=422, detail="Configuration must be a JSON object")
    return


def _sanitize_identity_provider_config(provider_type: str, config: dict | None) -> dict | None:
    if config is None:
        return None
    sanitized = dict(config)
    if provider_type == "google_workspace":
        sanitized.pop("google_admin_refresh_token", None)
        sanitized.pop("google_admin_refresh_token_encrypted", None)
    return sanitized


def _identity_provider_response(provider: IdentityProvider, sso_domain: str | None = None) -> dict:
    data = IdentityProviderOut.model_validate(provider).model_dump()
    data["config"] = _sanitize_identity_provider_config(provider.provider_type, provider.config)
    data["last_synced_at"] = (provider.config or {}).get("last_synced_at")
    if sso_domain is not None:
        data["sso_domain"] = sso_domain
    return data


@router.post("/identity-providers", response_model=IdentityProviderOut)
async def create_identity_provider(
    data: IdentityProviderCreate,
    current_user: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    """Create a new identity provider (Admin only)."""
    from app.services.auth_registry import auth_provider_registry

    # Validate config
    validate_provider_config(data.provider_type, data.config)
    
    # Validate and determine tenant_id
    tid = data.tenant_id
    if current_user.role == "platform_admin":
        # Platform admins can use any tenant_id (including None for global providers)
        pass
    else:
        # Non-platform admins: use request tenant_id if provided, else fall back to user's tenant
        if tid is None:
            tid = current_user.tenant_id
        elif str(tid) != str(current_user.tenant_id):
            # Validate they can only manage their own tenant
            raise HTTPException(status_code=403, detail="Can only create providers for your own tenant")

    if not tid:
        raise HTTPException(status_code=400, detail="tenant_id is required to create an identity provider")
        
    if data.sso_login_enabled:
        if not await sso_service.validate_sso_enablement(db, tid):
             raise HTTPException(
                status_code=400,
                detail="IP address does not support multi-tenant SSO. Another tenant already has SSO enabled."
            )

    provider = IdentityProvider(
        provider_type=data.provider_type,
        name=data.name,
        is_active=data.is_active,
        sso_login_enabled=data.sso_login_enabled,
        config=data.config,
        tenant_id=tid
    )
    db.add(provider)
    await db.commit()
    await db.refresh(provider)
    auth_provider_registry._clear_cache(provider.provider_type)
    return _identity_provider_response(provider)


@router.post("/identity-providers/oauth2", response_model=IdentityProviderOut)
async def create_oauth2_provider(
    data: IdentityProviderOAuth2Create,
    current_user: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    """Create a new OAuth2 identity provider with simplified fields (app_id, app_secret, authorize_url, etc.)."""
    from app.services.auth_registry import auth_provider_registry

    # Convert to config dict
    oauth_config = OAuth2Config(
        app_id=data.app_id,
        app_secret=data.app_secret,
        authorize_url=data.authorize_url,
        token_url=data.token_url,
        user_info_url=data.user_info_url,
        scope=data.scope,
    )
    config = oauth_config.to_config_dict()

    # Validate
    validate_provider_config("oauth2", config)

    # Validate and determine tenant_id
    tid = data.tenant_id
    if current_user.role == "platform_admin":
        # Platform admins can use any tenant_id (including None for global providers)
        pass
    else:
        # Non-platform admins: use request tenant_id if provided, else fall back to user's tenant
        if tid is None:
            tid = current_user.tenant_id
        elif str(tid) != str(current_user.tenant_id):
            # Validate they can only manage their own tenant
            raise HTTPException(status_code=403, detail="Can only create providers for your own tenant")

    if not tid:
        raise HTTPException(status_code=400, detail="tenant_id is required to create an identity provider")

    provider = IdentityProvider(
        provider_type="oauth2",
        name=data.name,
        is_active=data.is_active,
        config=config,
        tenant_id=tid
    )
    db.add(provider)
    await db.commit()
    await db.refresh(provider)
    auth_provider_registry._clear_cache(provider.provider_type)
    return _identity_provider_response(provider)


class OAuth2ConfigUpdate(BaseModel):
    """OAuth2 provider configuration update with dedicated fields."""
    name: str | None = None
    is_active: bool | None = None
    app_id: str | None = None
    app_secret: str | None = None  # Set to None to keep existing, empty to clear
    authorize_url: str | None = None
    token_url: str | None = None
    user_info_url: str | None = None
    scope: str | None = None


@router.patch("/identity-providers/{provider_id}/oauth2", response_model=IdentityProviderOut)
async def update_oauth2_provider(
    provider_id: uuid.UUID,
    data: OAuth2ConfigUpdate,
    current_user: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    """Update an OAuth2 identity provider with simplified fields."""
    from app.services.auth_registry import auth_provider_registry

    result = await db.execute(select(IdentityProvider).where(IdentityProvider.id == provider_id))
    provider = result.scalar_one_or_none()
    if not provider:
        raise HTTPException(status_code=404, detail="Provider not found")

    if provider.provider_type != "oauth2":
        raise HTTPException(status_code=400, detail="Provider is not an OAuth2 provider")

    if current_user.role != "platform_admin" and provider.tenant_id != current_user.tenant_id:
        raise HTTPException(status_code=403, detail="Not authorized to update this provider")

    # Update name and is_active
    if data.name is not None:
        provider.name = data.name
    if data.is_active is not None:
        provider.is_active = data.is_active

    # Update config fields
    if any([data.app_id, data.app_secret is not None, data.authorize_url, data.token_url, data.user_info_url, data.scope]):
        current_config = provider.config.copy()

        if data.app_id is not None:
            current_config["app_id"] = data.app_id
            current_config["client_id"] = data.app_id
        if data.app_secret is not None:
            # Only update if explicitly set (not None) - allows clearing
            if data.app_secret:
                current_config["app_secret"] = data.app_secret
                current_config["client_secret"] = data.app_secret
            else:
                current_config.pop("app_secret", None)
                current_config.pop("client_secret", None)
        if data.authorize_url is not None:
            current_config["authorize_url"] = data.authorize_url
        if data.token_url is not None:
            current_config["token_url"] = data.token_url
        if data.user_info_url is not None:
            current_config["user_info_url"] = data.user_info_url
        if data.scope is not None:
            current_config["scope"] = data.scope

        # Validate the updated config
        validate_provider_config("oauth2", current_config)
        provider.config = current_config

    await db.commit()
    await db.refresh(provider)
    auth_provider_registry._clear_cache(provider.provider_type)
    return _identity_provider_response(provider)


class IdentityProviderUpdate(BaseModel):
    name: str | None = None
    is_active: bool | None = None
    sso_login_enabled: bool | None = None
    config: dict | None = None


@router.put("/identity-providers/{provider_id}", response_model=IdentityProviderOut)
async def update_identity_provider(
    provider_id: uuid.UUID,
    data: IdentityProviderUpdate,
    current_user: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    """Update an existing identity provider."""
    from app.services.auth_registry import auth_provider_registry

    result = await db.execute(select(IdentityProvider).where(IdentityProvider.id == provider_id))
    provider = result.scalar_one_or_none()
    if not provider:
        raise HTTPException(status_code=404, detail="Provider not found")
        
    if current_user.role != "platform_admin" and provider.tenant_id != current_user.tenant_id:
        raise HTTPException(status_code=403, detail="Not authorized to update this provider")
        
    if data.name is not None:
        provider.name = data.name
    if data.is_active is not None:
        provider.is_active = data.is_active
    if data.sso_login_enabled is not None:
        if data.sso_login_enabled is True and not provider.sso_login_enabled:
            # Pre-check IP restriction before writing anything
            if not await sso_service.validate_sso_enablement(db, provider.tenant_id):
                raise HTTPException(
                    status_code=400,
                    detail="IP address does not support multi-tenant SSO. Another tenant already has SSO enabled."
                )
        provider.sso_login_enabled = data.sso_login_enabled
    if data.config is not None:
        # Merge config
        new_config = provider.config.copy()
        new_config.update(data.config)
        
        # Validate merged config
        validate_provider_config(provider.provider_type, new_config)
        
        provider.config = new_config
        
    await db.commit()
    await db.refresh(provider)
    auth_provider_registry._clear_cache(provider.provider_type)

    # Recompute tenant.sso_enabled derived state whenever sso_login_enabled changes
    sso_domain = None
    if data.sso_login_enabled is not None and provider.tenant_id:
        await _sync_tenant_sso_state(db, provider.tenant_id)
        from app.models.tenant import Tenant
        tenant_result = await db.execute(select(Tenant).where(Tenant.id == provider.tenant_id))
        t = tenant_result.scalar_one_or_none()
        if t:
            sso_domain = t.sso_domain

    return _identity_provider_response(provider, sso_domain=sso_domain)


@router.delete("/identity-providers/{provider_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_identity_provider(
    provider_id: uuid.UUID,
    current_user: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    """Delete an identity provider."""
    result = await db.execute(select(IdentityProvider).where(IdentityProvider.id == provider_id))
    provider = result.scalar_one_or_none()
    if not provider:
        raise HTTPException(status_code=404, detail="Provider not found")
        
    if current_user.role != "platform_admin" and provider.tenant_id != current_user.tenant_id:
        raise HTTPException(status_code=403, detail="Not authorized to delete this provider")
        
    try:
        # Nullify references in synced org data before deleting the provider
        from sqlalchemy import update
        await db.execute(
            update(OrgMember).where(OrgMember.provider_id == provider_id).values(provider_id=None)
        )
        await db.execute(
            update(OrgDepartment).where(OrgDepartment.provider_id == provider_id).values(provider_id=None)
        )
        
        await db.delete(provider)
        await db.commit()
    except SQLAlchemyError as e:
        await db.rollback()
        logger.error(f"Failed to delete identity provider {provider_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to delete identity provider due to database constraints")


# ─── Org Structure ──────────────────────────────────────

from app.models.org import OrgDepartment, OrgMember


@router.get("/org/departments")
async def list_org_departments(
    tenant_id: str | None = None,
    provider_id: str | None = None,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List all departments, optionally filtered by tenant or provider."""
    # Tenant isolation rules:
    # 1. If tenant_id param is explicitly provided:
    #    - non-platform-admins: must match their own tenant_id
    #    - platform_admin with a tenant in token: must match that tenant
    #    - platform_admin without a tenant (global view): any tenant allowed
    # 2. If tenant_id param is NOT provided:
    #    - auto-scope to current_user.tenant_id when it is set (applies to ALL roles)
    #    - only a platform_admin with NO tenant_id in token can query unrestricted
    effective_tenant_id = str(current_user.tenant_id) if current_user.tenant_id else None
    is_global_admin = (current_user.role == "platform_admin" and not effective_tenant_id)

    if tenant_id:
        # Validate requested tenant against user context
        if not is_global_admin and effective_tenant_id and effective_tenant_id != tenant_id:
            raise HTTPException(status_code=403, detail="Cannot access other tenant's data")
    else:
        # Auto-scope: use the user's own tenant when available
        tenant_id = effective_tenant_id  # None only for true global admin

    query = select(OrgDepartment, IdentityProvider.name.label("provider_name"), IdentityProvider.provider_type).outerjoin(
        IdentityProvider, OrgDepartment.provider_id == IdentityProvider.id
    ).where(OrgDepartment.status == "active")
    if tenant_id:
        query = query.where(OrgDepartment.tenant_id == uuid.UUID(tenant_id))
    if provider_id:
        query = query.where(OrgDepartment.provider_id == uuid.UUID(provider_id))
    result = await db.execute(query.order_by(OrgDepartment.name))
    rows = result.all()
    # Calculate total members for this scope (for the "All" entry in frontend)
    total_q = select(func.count(OrgMember.id)).where(OrgMember.status == "active")
    if tenant_id:
        total_q = total_q.where(OrgMember.tenant_id == uuid.UUID(tenant_id))
    if provider_id:
        total_q = total_q.where(OrgMember.provider_id == uuid.UUID(provider_id))
    total_result = await db.execute(total_q)
    total_member = total_result.scalar() or 0

    return {
        "items": [
            {
                "id": str(d.id),
                "external_id": d.external_id,
                "provider_id": str(d.provider_id) if d.provider_id else None,
                "provider_name": provider_name if d.provider_id else None,
                "provider_type": provider_type if d.provider_id else None,
                "name": d.name,
                "parent_id": str(d.parent_id) if d.parent_id else None,
                "path": d.path,
                "member_count": d.member_count,
            }
            for d, provider_name, provider_type in rows
        ],
        "total_member": total_member,
    }



from sqlalchemy import or_

@router.get("/org/members")
async def list_org_members(
    department_id: str | None = None,
    search: str | None = None,
    tenant_id: str | None = None,
    provider_id: str | None = None,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List org members, optionally filtered by department, search, tenant, or provider."""
    # Tenant isolation rules:
    # 1. If tenant_id param is explicitly provided:
    #    - non-platform-admins: must match their own tenant_id
    #    - platform_admin with a tenant in token: must match that tenant
    #    - platform_admin without a tenant (global view): any tenant allowed
    # 2. If tenant_id param is NOT provided:
    #    - auto-scope to current_user.tenant_id when it is set (applies to ALL roles)
    #    - only a platform_admin with NO tenant_id in token can query unrestricted
    effective_tenant_id = str(current_user.tenant_id) if current_user.tenant_id else None
    is_global_admin = (current_user.role == "platform_admin" and not effective_tenant_id)

    if tenant_id:
        # Validate requested tenant against user context
        if not is_global_admin and effective_tenant_id and effective_tenant_id != tenant_id:
            raise HTTPException(status_code=403, detail="Cannot access other tenant's data")
    else:
        # Auto-scope: use the user's own tenant when available
        tenant_id = effective_tenant_id  # None only for true global admin

    query = select(OrgMember, IdentityProvider.name.label("provider_name"), IdentityProvider.provider_type).outerjoin(
        IdentityProvider, OrgMember.provider_id == IdentityProvider.id
    ).where(OrgMember.status == "active")
    if tenant_id:
        query = query.where(OrgMember.tenant_id == uuid.UUID(tenant_id))
    if department_id:
        # Get the department to find its path and then include all sub-departments
        dept_result = await db.execute(select(OrgDepartment).where(OrgDepartment.id == uuid.UUID(department_id)))
        target_dept = dept_result.scalar_one_or_none()
        if target_dept:
            # Build sub-department query: the selected dept itself, plus any dept whose path
            # starts with its path followed by a "/" (i.e., all descendants).
            sub_dept_conditions = [OrgDepartment.id == target_dept.id]
            if target_dept.path:
                # Use SQL LIKE to find all descendants based on path prefix
                sub_dept_conditions.append(OrgDepartment.path.like(f"{target_dept.path}/%"))
            sub_depts_query = select(OrgDepartment.id).where(or_(*sub_dept_conditions))
            sub_dept_ids_result = await db.execute(sub_depts_query)
            sub_dept_ids = [row[0] for row in sub_dept_ids_result.all()]
            query = query.where(OrgMember.department_id.in_(sub_dept_ids))
        else:
            # Fallback: exact match
            query = query.where(OrgMember.department_id == uuid.UUID(department_id))
    if provider_id:
        query = query.where(OrgMember.provider_id == uuid.UUID(provider_id))
    if search:
        query = query.where(
            or_(
                OrgMember.name.ilike(f"%{search}%"),
                OrgMember.name_translit_full.ilike(f"%{search}%"),
                OrgMember.name_translit_initial.ilike(f"%{search}%"),
            )
        )
    query = query.order_by(OrgMember.name).limit(100)
    result = await db.execute(query)
    rows = result.all()
    member_paths = await derive_member_department_paths(
        db,
        [m for m, _provider_name, _provider_type in rows],
    )
    return [
        {
            "id": str(m.id),
            "name": m.name,
            "email": m.email,
            "title": m.title,
            "department_path": member_paths.get(m.id, m.department_path),
            "avatar_url": m.avatar_url,
            "external_id": m.external_id,
            "provider_id": str(m.provider_id) if m.provider_id else None,
            "provider_name": provider_name if m.provider_id else None,
            "provider_type": provider_type if m.provider_id else None,
        }
        for m, provider_name, provider_type in rows
    ]


@router.post("/org/sync")
async def trigger_org_sync(
    provider_id: str | None = None,
    current_user: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    """Manually trigger org structure sync from a specific identity provider."""
    from app.services.org_sync_service import org_sync_service

    if not provider_id:
        raise HTTPException(status_code=400, detail="provider_id is required")

    try:
        pid = uuid.UUID(provider_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid provider_id")

    result = await db.execute(select(IdentityProvider).where(IdentityProvider.id == pid))
    provider = result.scalar_one_or_none()
    if not provider:
        raise HTTPException(status_code=404, detail="Provider not found")

    if not provider.tenant_id:
        raise HTTPException(status_code=400, detail="Provider must be bound to a tenant")

    if current_user.role != "platform_admin" and provider.tenant_id != current_user.tenant_id:
        raise HTTPException(status_code=403, detail="Cannot sync other tenant's provider")

    return await org_sync_service.sync_provider(db, provider_id)


@router.get("/org/wecom-verify/{provider_id}")
async def wecom_org_sync_verify(
    provider_id: uuid.UUID,
    msg_signature: str = "",
    timestamp: str = "",
    nonce: str = "",
    echostr: str = "",
    db: AsyncSession = Depends(get_db),
):
    """Handle WeCom receive-message-server URL verification for the org sync app.

    WeCom sends a GET request with msg_signature, timestamp, nonce, echostr when
    the admin first saves the receive message server URL in the app settings.
    This endpoint decrypts and returns the echostr to complete the handshake.

    After this verification succeeds, the WeCom app's trusted IP whitelist becomes
    configurable, which is the prerequisite for using App-level credentials (AgentID +
    Secret) that have full contact read permission.

    Configure URL in WeCom: {BASE_URL}/api/enterprise/org/wecom-verify/{provider_id}

    Required provider config keys (set via Clawith WeCom config page):
      - verify_token:   the Token string set in both WeCom and Clawith
      - verify_aes_key: the EncodingAESKey provided by WeCom (43 chars, base64url)
    """
    from fastapi.responses import Response as _Response
    from app.api.wecom import _decrypt_msg, _verify_signature

    result = await db.execute(select(IdentityProvider).where(IdentityProvider.id == provider_id))
    provider = result.scalar_one_or_none()
    if not provider:
        return _Response(status_code=404)

    config = provider.config or {}
    token = config.get("verify_token", "")
    aes_key = config.get("verify_aes_key", "")

    if not token or not aes_key:
        logger.warning(
            f"[WeCom Verify] Provider {provider_id} is missing verify_token or verify_aes_key in config. "
            "Please configure them in the WeCom provider settings."
        )
        return _Response(status_code=400)

    # Verify signature to authenticate the request from WeCom
    expected_sig = _verify_signature(token, timestamp, nonce, echostr)
    if expected_sig != msg_signature:
        logger.warning(f"[WeCom Verify] Signature mismatch for provider {provider_id}")
        return _Response(status_code=403)

    # Decrypt echostr and return plaintext (WeCom confirms URL ownership)
    try:
        decrypted, _ = _decrypt_msg(aes_key, echostr)
        logger.info(f"[WeCom Verify] Successfully verified org sync callback for provider {provider_id}")
        return _Response(content=decrypted, media_type="text/plain")
    except Exception as e:
        logger.error(f"[WeCom Verify] Failed to decrypt echostr for provider {provider_id}: {e}")
        return _Response(status_code=500)


@router.get("/org/wecom-callback/{token}", include_in_schema=False)
async def wecom_callback_verify_universal(
    token: str,
    aes_key: str = "",
    msg_signature: str = "",
    timestamp: str = "",
    nonce: str = "",
    echostr: str = "",
):
    """Universal WeCom callback URL verification endpoint (no database lookup required).

    Used to unlock the 企业可信IP configuration in the WeCom admin console.
    Unlike the provider-based endpoint, this accepts the verify_token in the URL
    path and the EncodingAESKey as a query parameter, so any tenant can use the
    publicly accessible server (e.g. try.clawith.ai) regardless of which server
    the WeCom provider is actually configured on.

    URL format to configure in WeCom App → 接收消息服务器URL:
      https://{public_host}/api/enterprise/org/wecom-callback/{verify_token}?aes_key={encoding_aes_key}

    WeCom will append msg_signature, timestamp, nonce, echostr to this URL automatically.
    Once WeCom verifies this URL, the app's 企业可信IP whitelist becomes configurable and
    the user can add their API server IPs to allow App-level user/get calls.
    """
    from fastapi.responses import Response as _Response
    from app.api.wecom import _decrypt_msg, _verify_signature

    if not token:
        return _Response(status_code=400, content="verify_token is required in URL path")

    if not aes_key:
        logger.warning("[WeCom Callback] Missing aes_key query param in universal callback URL")
        return _Response(status_code=400, content="aes_key query param is required")

    # Verify signature to authenticate the request as coming from WeCom servers
    expected_sig = _verify_signature(token, timestamp, nonce, echostr)
    if expected_sig != msg_signature:
        logger.warning(
            f"[WeCom Callback] Signature mismatch: token={token[:8]}... "
            f"expected={expected_sig[:16]}... got={msg_signature[:16]}..."
        )
        return _Response(status_code=403)

    # Decrypt echostr and return plaintext to complete WeCom URL verification
    try:
        decrypted, _ = _decrypt_msg(aes_key, echostr)
        logger.info(f"[WeCom Callback] Universal callback verified successfully for token={token[:8]}...")
        return _Response(content=decrypted, media_type="text/plain")
    except Exception as e:
        logger.error(f"[WeCom Callback] Failed to decrypt echostr: {e}")
        return _Response(status_code=500)


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


@router.post("/invite-users")
async def invite_users(
    request: Request,
    data: UserInviteRequest,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Batch-invite users via email to the current user's company."""
    _require_tenant_admin(current_user)
    if not data.emails:
        raise HTTPException(status_code=400, detail="No emails provided")
        
    import random
    import string
    from app.services.system_email_service import send_company_invitation_email
    from app.services.platform_service import platform_service
    from app.models.tenant import Tenant
    
    tenant_result = await db.execute(select(Tenant).where(Tenant.id == current_user.tenant_id))
    tenant = tenant_result.scalar_one_or_none()
    if not tenant:
        raise HTTPException(status_code=404, detail="Company not found")

    base_url = await platform_service.get_public_base_url(db, request=request)
    
    invited_count = 0
    codes = []
    
    for email in data.emails:
        email = email.lower().strip()
        if not email:
            continue
            
        code_str = ''.join(random.choices(string.ascii_uppercase + string.digits, k=8))
        code = InvitationCode(
            code=code_str,
            tenant_id=current_user.tenant_id,
            max_uses=1,
            created_by=current_user.id,
        )
        db.add(code)
        codes.append(code)
        
        invite_url = f"{base_url}/login?code={code_str}&email={email}"
        
        inviter_name = current_user.display_name or current_user.username
        
        # Use background task to send email
        background_tasks.add_task(
            send_company_invitation_email,
            to=email,
            inviter_name=inviter_name,
            company_name=tenant.name,
            invite_url=invite_url,
        )
        invited_count += 1

    if invited_count > 0:
        await db.commit()
        
    return {"invited": invited_count, "message": "Invitations sent successfully"}


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
