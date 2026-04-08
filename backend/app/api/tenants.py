"""Tenant (Company) management API.

Public endpoints for self-service company creation and joining.
Admin endpoints for platform-level company management.
"""

import re
import secrets
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import func as sqla_func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import get_current_user, require_role, get_authenticated_user
from app.database import get_db
from app.models.tenant import Tenant
from app.models.user import User

router = APIRouter(prefix="/tenants", tags=["tenants"])


# ─── Schemas ────────────────────────────────────────────

class TenantCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    target_tenant_id: uuid.UUID | None = None

class TenantOut(BaseModel):
    id: uuid.UUID
    name: str
    slug: str
    im_provider: str
    timezone: str = "UTC"
    is_active: bool
    sso_enabled: bool = False
    sso_domain: str | None = None
    created_at: datetime | None = None

    model_config = {"from_attributes": True}


class TenantUpdate(BaseModel):
    name: str | None = None
    im_provider: str | None = None
    timezone: str | None = None
    is_active: bool | None = None
    sso_enabled: bool | None = None
    sso_domain: str | None = None


# ─── Helpers ────────────────────────────────────────────

def _slugify(name: str) -> str:
    """Generate a URL-friendly slug from a company name.

    Uses a layered transliteration strategy so non-Latin company names produce
    meaningful, readable slugs instead of collapsing to the generic 'company'
    placeholder:

      1. pypinyin   — CJK/Chinese characters → pinyin (e.g. '公司' → 'gongsi')
      2. anyascii   — remaining non-ASCII scripts → closest ASCII approximation
                      (Korean '안녕' → 'annyeong', Japanese 'ひらがな' → 'hiragana',
                       Arabic 'مرحبا' → 'mrhb', Cyrillic 'Привет' → 'Privet', …)
      3. NFKD norm  — accented Latin chars stripped of diacritics (é → e)

    A short random hex suffix is always appended to guarantee global uniqueness
    even when two tenants choose the same company name.
    """
    import unicodedata
    from pypinyin import lazy_pinyin
    from anyascii import anyascii

    # Step 1: Convert CJK characters to pinyin; non-CJK chars pass through unchanged.
    # lazy_pinyin with errors='default' keeps non-CJK chars as-is so they are
    # handled by the subsequent anyascii pass rather than being silently dropped.
    parts = lazy_pinyin(name, errors="default")
    text = "".join(parts)

    # Step 2: Convert remaining non-ASCII characters using anyascii.
    # anyascii is a no-op on ASCII input, so it is safe to apply to the whole
    # string after pypinyin has already processed the CJK portion.
    text = anyascii(text)

    # Step 3: Normalize any remaining accented Latin chars (é → e, ü → u, etc.)
    # and drop anything that still cannot be represented in ASCII.
    text = unicodedata.normalize("NFKD", text)
    text = text.encode("ascii", "ignore").decode("ascii")

    # Step 4: Lowercase, collapse non-alphanumeric runs to hyphens, trim to 40 chars.
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower().strip())
    slug = slug.strip("-")[:40]

    if not slug:
        # Extremely unlikely after anyascii, but keep as a safety net
        # for inputs that are entirely punctuation or whitespace.
        slug = "company"

    # Add a short random hex suffix to ensure global uniqueness.
    slug = f"{slug}-{secrets.token_hex(3)}"
    return slug


class SelfCreateResponse(BaseModel):
    """Response for self-create company, includes token for context switching."""
    tenant: TenantOut
    access_token: str | None = None  # Non-null when a new User record was created (multi-tenant switch)


@router.post("/self-create", response_model=SelfCreateResponse, status_code=status.HTTP_201_CREATED)
async def self_create_company(
    data: TenantCreate,
    current_user: User = Depends(get_authenticated_user),
    db: AsyncSession = Depends(get_db),
):
    """Create a new company (self-service). The creator becomes org_admin.

    Supports both:
    - Registration flow (user has no tenant yet): assigns tenant directly
    - Switch-org flow (user already has a tenant): creates a new User record for the new tenant
    """
    # Block self-creation if locked to a specific tenant (Dedicated Link flow)
    if data.target_tenant_id is not None:
        raise HTTPException(status_code=403, detail="Company creation is not allowed via this link. Please join your assigned organization.")

    # Check if self-creation is allowed
    from app.models.system_settings import SystemSetting
    setting = await db.execute(
        select(SystemSetting).where(SystemSetting.key == "allow_self_create_company")
    )
    s = setting.scalar_one_or_none()
    allowed = s.value.get("enabled", True) if s else True
    if not allowed and current_user.role != "platform_admin":
        raise HTTPException(status_code=403, detail="Company self-creation is currently disabled")

    slug = _slugify(data.name)
    tenant = Tenant(name=data.name, slug=slug, im_provider="web_only")
    db.add(tenant)
    await db.flush()

    access_token = None

    if current_user.tenant_id is not None:
        # Multi-tenant: user already belongs to a company.
        # Create a NEW User record for the new tenant instead of overwriting.
        from app.core.security import create_access_token
        from app.models.participant import Participant

        new_user = User(
            identity_id=current_user.identity_id,
            tenant_id=tenant.id,
            display_name=current_user.display_name,
            role="org_admin",
            registration_source="web",
            is_active=current_user.is_active,
            quota_message_limit=tenant.default_message_limit,
            quota_message_period=tenant.default_message_period,
            quota_max_agents=tenant.default_max_agents,
            quota_agent_ttl_hours=tenant.default_agent_ttl_hours,
        )
        db.add(new_user)
        await db.flush()

        # Create Participant for the new user record
        db.add(Participant(
            type="user",
            ref_id=new_user.id,
            display_name=new_user.display_name,
            avatar_url=new_user.avatar_url,
        ))
        await db.flush()

        # Generate token scoped to the new user so frontend can switch context
        access_token = create_access_token(str(new_user.id), new_user.role)
    else:
        # Registration flow: user has no tenant yet, assign directly
        current_user.tenant_id = tenant.id
        current_user.role = "org_admin" if current_user.role == "member" else current_user.role
        # Inherit quota defaults from new tenant
        current_user.quota_message_limit = tenant.default_message_limit
        current_user.quota_message_period = tenant.default_message_period
        current_user.quota_max_agents = tenant.default_max_agents
        current_user.quota_agent_ttl_hours = tenant.default_agent_ttl_hours
        await db.flush()

    await db.commit()

    return SelfCreateResponse(
        tenant=TenantOut.model_validate(tenant),
        access_token=access_token,
    )


# ─── Self-Service: Join Company via Invite Code ─────────

class JoinRequest(BaseModel):
    invitation_code: str = Field(min_length=1, max_length=32)
    target_tenant_id: uuid.UUID | None = None


class JoinResponse(BaseModel):
    tenant: TenantOut
    role: str
    access_token: str | None = None  # Non-null when a new User record was created (multi-tenant switch)


@router.post("/join", response_model=JoinResponse)
async def join_company(
    data: JoinRequest,
    current_user: User = Depends(get_authenticated_user),
    db: AsyncSession = Depends(get_db),
):
    """Join an existing company using an invitation code.

    Supports both:
    - Registration flow (user has no tenant yet): assigns tenant directly
    - Switch-org flow (user already has a tenant): creates a new User record"""
    from app.models.invitation_code import InvitationCode
    ic_result = await db.execute(
        select(InvitationCode).where(
            InvitationCode.code == data.invitation_code,
            InvitationCode.is_active == True,
            InvitationCode.tenant_id.is_not(None),
        )
    )
    code_obj = ic_result.scalar_one_or_none()
    if not code_obj:
        raise HTTPException(status_code=400, detail="Invalid invitation code")

    # Verify matching tenant if locked (Dedicated Link flow)
    if data.target_tenant_id and str(code_obj.tenant_id) != str(data.target_tenant_id):
        raise HTTPException(status_code=403, detail="This invitation code does not belong to the required organization.")

    if code_obj.used_count >= code_obj.max_uses:
        raise HTTPException(status_code=400, detail="Invitation code has reached its usage limit")

    # Find the company
    t_result = await db.execute(select(Tenant).where(Tenant.id == code_obj.tenant_id))
    tenant = t_result.scalar_one_or_none()
    if not tenant or not tenant.is_active:
        raise HTTPException(status_code=400, detail="Company not found or is disabled")

    # Check if user already belongs to this specific tenant
    existing_membership = await db.execute(
        select(User).where(
            User.identity_id == current_user.identity_id,
            User.tenant_id == tenant.id,
        )
    )
    if existing_membership.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="You already belong to this company")

    # Check if this company has an org_admin already
    admin_check = await db.execute(
        select(sqla_func.count()).select_from(User).where(
            User.tenant_id == tenant.id,
            User.role.in_(["org_admin", "platform_admin"]),
        )
    )
    has_admin = admin_check.scalar() > 0

    # First joiner of an empty company becomes org_admin
    assigned_role = "member" if has_admin else "org_admin"

    access_token = None

    if current_user.tenant_id is not None:
        # Multi-tenant: user already belongs to a company.
        # Create a NEW User record for the new tenant.
        from app.core.security import create_access_token
        from app.models.participant import Participant

        new_user = User(
            identity_id=current_user.identity_id,
            tenant_id=tenant.id,
            display_name=current_user.display_name,
            role=assigned_role,
            registration_source="web",
            is_active=current_user.is_active,
            quota_message_limit=tenant.default_message_limit,
            quota_message_period=tenant.default_message_period,
            quota_max_agents=tenant.default_max_agents,
            quota_agent_ttl_hours=tenant.default_agent_ttl_hours,
        )
        db.add(new_user)
        await db.flush()

        # Create Participant for the new user record
        db.add(Participant(
            type="user",
            ref_id=new_user.id,
            display_name=new_user.display_name,
            avatar_url=new_user.avatar_url,
        ))
        await db.flush()

        # Generate token scoped to the new user so frontend can switch context
        access_token = create_access_token(str(new_user.id), new_user.role)
        final_role = new_user.role
    else:
        # Registration flow: user has no tenant yet, assign directly
        current_user.tenant_id = tenant.id
        if current_user.role == "member":
            current_user.role = assigned_role
        # Inherit quota defaults from tenant
        current_user.quota_message_limit = tenant.default_message_limit
        current_user.quota_message_period = tenant.default_message_period
        current_user.quota_max_agents = tenant.default_max_agents
        current_user.quota_agent_ttl_hours = tenant.default_agent_ttl_hours
        final_role = current_user.role

    # Increment invitation code usage
    code_obj.used_count += 1
    await db.flush()

    await db.commit()

    return JoinResponse(
        tenant=TenantOut.model_validate(tenant),
        role=final_role,
        access_token=access_token,
    )


# ─── Registration Config ───────────────────────────────

@router.get("/registration-config")
async def get_registration_config(db: AsyncSession = Depends(get_db)):
    """Public — returns whether self-creation of companies is allowed."""
    from app.models.system_settings import SystemSetting
    result = await db.execute(
        select(SystemSetting).where(SystemSetting.key == "allow_self_create_company")
    )
    s = result.scalar_one_or_none()
    allowed = s.value.get("enabled", True) if s else True
    return {"allow_self_create_company": allowed}


# ─── Public: Resolve Tenant by Domain ───────────────────

@router.get("/resolve-by-domain")
async def resolve_tenant_by_domain(
    domain: str,
    db: AsyncSession = Depends(get_db),
):
    """Resolve a tenant by its sso_domain or subdomain slug.

    sso_domain is stored as a full URL (e.g. "https://acme.clawith.ai" or "http://1.2.3.4:3009").
    The incoming `domain` parameter is the host (without protocol).

    Lookup precedence:
    1. Exact match on tenant.sso_domain ending with the host (strips protocol)
    2. Extract slug from "{slug}.clawith.ai" and match tenant.slug
    """
    tenant = None

    # 1. Match by stripping protocol from stored sso_domain
    # sso_domain = "https://acme.clawith.ai" → compare against "acme.clawith.ai"
    for proto in ("https://", "http://"):
        result = await db.execute(
            select(Tenant).where(Tenant.sso_domain == f"{proto}{domain}")
        )
        tenant = result.scalar_one_or_none()
        if tenant:
            break

    # 2. Try without port (e.g. domain = "1.2.3.4:3009" → try "1.2.3.4")
    if not tenant and ":" in domain:
        domain_no_port = domain.split(":")[0]
        for proto in ("https://", "http://"):
            result = await db.execute(
                select(Tenant).where(Tenant.sso_domain.like(f"{proto}{domain_no_port}%"))
            )
            tenant = result.scalar_one_or_none()
            if tenant:
                break

    # 3. Fallback: extract slug from subdomain pattern
    if not tenant:
        import re
        m = re.match(r"^([a-z0-9][a-z0-9\-]*[a-z0-9])\.clawith\.ai$", domain.lower())
        if m:
            slug = m.group(1)
            result = await db.execute(select(Tenant).where(Tenant.slug == slug))
            tenant = result.scalar_one_or_none()

    if not tenant or not tenant.is_active or not tenant.sso_enabled:
        raise HTTPException(status_code=404, detail="Tenant not found or not active or SSO not enabled")

    return {
        "id": tenant.id,
        "name": tenant.name,
        "slug": tenant.slug,
        "sso_enabled": tenant.sso_enabled,
        "sso_domain": tenant.sso_domain,
        "is_active": tenant.is_active,
    }

# ─── Authenticated: List / Get ──────────────────────────

@router.get("/", response_model=list[TenantOut])
async def list_tenants(
    current_user: User = Depends(require_role("platform_admin")),
    db: AsyncSession = Depends(get_db),
):
    """List all tenants (platform_admin only)."""
    result = await db.execute(select(Tenant).order_by(Tenant.created_at.desc()))
    return [TenantOut.model_validate(t) for t in result.scalars().all()]


@router.get("/{tenant_id}", response_model=TenantOut)
async def get_tenant(
    tenant_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get tenant details. Platform admins can view any; org_admins only their own."""
    if current_user.role not in ("platform_admin", "org_admin"):
        raise HTTPException(status_code=403, detail="Admin access required")
    if current_user.role == "org_admin" and str(current_user.tenant_id) != str(tenant_id):
        raise HTTPException(status_code=403, detail="Access denied")
    result = await db.execute(select(Tenant).where(Tenant.id == tenant_id))
    tenant = result.scalar_one_or_none()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")
    return TenantOut.model_validate(tenant)


@router.put("/{tenant_id}", response_model=TenantOut)
async def update_tenant(
    tenant_id: uuid.UUID,
    data: TenantUpdate,
    current_user: User = Depends(require_role("org_admin", "platform_admin")),
    db: AsyncSession = Depends(get_db),
):
    """Update tenant settings. Platform admins can update any; org_admins only their own."""
    if current_user.role == "org_admin" and str(current_user.tenant_id) != str(tenant_id):
        raise HTTPException(status_code=403, detail="Can only update your own company")
    result = await db.execute(select(Tenant).where(Tenant.id == tenant_id))
    tenant = result.scalar_one_or_none()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")

    update_data = data.model_dump(exclude_unset=True)
    
    # SSO configuration is managed exclusively by the company's own org_admin
    # via the Enterprise Settings page. Platform admins should not override it here.
    if current_user.role == "platform_admin":
        update_data.pop("sso_enabled", None)
        update_data.pop("sso_domain", None)

    for field, value in update_data.items():
        setattr(tenant, field, value)
    await db.flush()
    return TenantOut.model_validate(tenant)


@router.put("/{tenant_id}/assign-user/{user_id}")
async def assign_user_to_tenant(
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    role: str = "member",
    current_user: User = Depends(require_role("platform_admin")),
    db: AsyncSession = Depends(get_db),
):
    """Assign a user to a tenant with a specific role."""
    # Verify tenant
    t_result = await db.execute(select(Tenant).where(Tenant.id == tenant_id))
    if not t_result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Tenant not found")

    # Verify user
    u_result = await db.execute(select(User).where(User.id == user_id))
    user = u_result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    if role not in ("org_admin", "agent_admin", "member"):
        raise HTTPException(status_code=400, detail="Invalid role")

    user.tenant_id = tenant_id
    user.role = role
    await db.flush()
    return {"status": "ok", "user_id": str(user_id), "tenant_id": str(tenant_id), "role": role}


# ─── Platform Admin: Delete Tenant (Cascade) ─────────

@router.delete("/{tenant_id}", status_code=204)
async def delete_tenant(
    tenant_id: uuid.UUID,
    current_user: User = Depends(require_role("platform_admin")),
    db: AsyncSession = Depends(get_db),
):
    """Permanently delete a tenant and all associated data.

    Platform admin only. Cannot delete the first (default) tenant.
    Cascade-deletes all related records in dependency order within a single transaction.
    """
    import logging
    from sqlalchemy import delete as sa_delete, update as sa_update

    from app.models.activity_log import AgentActivityLog, DailyTokenUsage
    from app.models.agent import Agent, AgentPermission, AgentTemplate
    from app.models.agent_credential import AgentCredential
    from app.models.audit import AuditLog, ApprovalRequest, ChatMessage
    from app.models.channel_config import ChannelConfig
    from app.models.chat_session import ChatSession
    from app.models.gateway_message import GatewayMessage
    from app.models.identity import IdentityProvider, SSOScanSession
    from app.models.invitation_code import InvitationCode
    from app.models.llm import LLMModel
    from app.models.notification import Notification
    from app.models.org import OrgMember, OrgDepartment, AgentRelationship, AgentAgentRelationship
    from app.models.participant import Participant
    from app.models.plaza import PlazaPost, PlazaComment, PlazaLike
    from app.models.published_page import PublishedPage
    from app.models.schedule import AgentSchedule
    from app.models.skill import Skill, SkillFile
    from app.models.task import Task, TaskLog
    from app.models.tenant_setting import TenantSetting
    from app.models.tool import AgentTool
    from app.models.trigger import AgentTrigger

    logger = logging.getLogger(__name__)

    # 1. Find the tenant
    result = await db.execute(select(Tenant).where(Tenant.id == tenant_id))
    tenant = result.scalar_one_or_none()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")

    # 2. Cannot delete the first tenant (acts as the default/primary tenant)
    first_tenant = await db.execute(
        select(Tenant).order_by(Tenant.created_at.asc()).limit(1)
    )
    first = first_tenant.scalar_one_or_none()
    if first and first.id == tenant.id:
        raise HTTPException(
            status_code=400,
            detail="Cannot delete the default tenant.",
        )

    logger.info("Deleting tenant %s (%s) and all associated data", tenant.id, tenant.name)

    # 3. Collect agent_ids and user_ids for this tenant
    agent_ids = [
        row[0]
        for row in (await db.execute(select(Agent.id).where(Agent.tenant_id == tenant_id))).all()
    ]
    user_ids = [
        row[0]
        for row in (await db.execute(select(User.id).where(User.tenant_id == tenant_id))).all()
    ]

    # 4. Delete tables that reference agents (via agent_id FK)
    if agent_ids:
        # Task logs before tasks (task_logs.task_id -> tasks.id -> agents.id)
        task_ids = [
            row[0]
            for row in (await db.execute(select(Task.id).where(Task.agent_id.in_(agent_ids)))).all()
        ]
        if task_ids:
            await db.execute(sa_delete(TaskLog).where(TaskLog.task_id.in_(task_ids)))

        await db.execute(sa_delete(AgentTrigger).where(AgentTrigger.agent_id.in_(agent_ids)))
        await db.execute(sa_delete(AgentSchedule).where(AgentSchedule.agent_id.in_(agent_ids)))
        await db.execute(sa_delete(AgentActivityLog).where(AgentActivityLog.agent_id.in_(agent_ids)))
        await db.execute(sa_delete(AgentCredential).where(AgentCredential.agent_id.in_(agent_ids)))
        await db.execute(sa_delete(ChannelConfig).where(ChannelConfig.agent_id.in_(agent_ids)))
        await db.execute(sa_delete(AgentTool).where(AgentTool.agent_id.in_(agent_ids)))
        await db.execute(sa_delete(Notification).where(Notification.agent_id.in_(agent_ids)))
        await db.execute(sa_delete(Task).where(Task.agent_id.in_(agent_ids)))
        await db.execute(sa_delete(AuditLog).where(AuditLog.agent_id.in_(agent_ids)))
        await db.execute(sa_delete(ApprovalRequest).where(ApprovalRequest.agent_id.in_(agent_ids)))
        await db.execute(sa_delete(ChatMessage).where(ChatMessage.agent_id.in_(agent_ids)))
        await db.execute(sa_delete(GatewayMessage).where(GatewayMessage.agent_id.in_(agent_ids)))
        await db.execute(sa_delete(ChatSession).where(ChatSession.agent_id.in_(agent_ids)))
        await db.execute(sa_delete(AgentPermission).where(AgentPermission.agent_id.in_(agent_ids)))
        await db.execute(sa_delete(AgentAgentRelationship).where(AgentAgentRelationship.agent_id.in_(agent_ids)))
        await db.execute(sa_delete(AgentAgentRelationship).where(AgentAgentRelationship.target_agent_id.in_(agent_ids)))
        await db.execute(sa_delete(AgentRelationship).where(AgentRelationship.agent_id.in_(agent_ids)))
        await db.execute(sa_delete(PublishedPage).where(PublishedPage.agent_id.in_(agent_ids)))

        # Null out cross-tenant FK references (other tenants' records pointing to our agents)
        await db.execute(
            sa_update(ChatSession).where(ChatSession.peer_agent_id.in_(agent_ids)).values(peer_agent_id=None)
        )
        await db.execute(
            sa_update(GatewayMessage).where(GatewayMessage.sender_agent_id.in_(agent_ids)).values(sender_agent_id=None)
        )

        # Delete Participant records for agents
        await db.execute(
            sa_delete(Participant).where(Participant.type == "agent", Participant.ref_id.in_(agent_ids))
        )

    # 5. Delete tables that reference users
    if user_ids:
        await db.execute(sa_delete(AgentTemplate).where(AgentTemplate.created_by.in_(user_ids)))

        # Delete audit/notification rows that reference our users (with null or other-tenant agent_id)
        await db.execute(sa_delete(AuditLog).where(AuditLog.user_id.in_(user_ids)))
        await db.execute(sa_delete(Notification).where(Notification.user_id.in_(user_ids)))

        # Null out cross-tenant user FK references
        await db.execute(
            sa_update(ApprovalRequest).where(ApprovalRequest.resolved_by.in_(user_ids)).values(resolved_by=None)
        )
        await db.execute(
            sa_update(GatewayMessage).where(GatewayMessage.sender_user_id.in_(user_ids)).values(sender_user_id=None)
        )

        # Delete Participant records for users
        await db.execute(
            sa_delete(Participant).where(Participant.type == "user", Participant.ref_id.in_(user_ids))
        )

        # Plaza posts (and their comments/likes) authored by tenant users
        plaza_post_ids = [
            row[0]
            for row in (await db.execute(
                select(PlazaPost.id).where(PlazaPost.author_type == "user", PlazaPost.author_id.in_(user_ids))
            )).all()
        ]
        if plaza_post_ids:
            await db.execute(sa_delete(PlazaComment).where(PlazaComment.post_id.in_(plaza_post_ids)))
            await db.execute(sa_delete(PlazaLike).where(PlazaLike.post_id.in_(plaza_post_ids)))
            await db.execute(sa_delete(PlazaPost).where(PlazaPost.id.in_(plaza_post_ids)))

    # Plaza posts authored by tenant agents
    if agent_ids:
        agent_plaza_post_ids = [
            row[0]
            for row in (await db.execute(
                select(PlazaPost.id).where(PlazaPost.author_type == "agent", PlazaPost.author_id.in_(agent_ids))
            )).all()
        ]
        if agent_plaza_post_ids:
            await db.execute(sa_delete(PlazaComment).where(PlazaComment.post_id.in_(agent_plaza_post_ids)))
            await db.execute(sa_delete(PlazaLike).where(PlazaLike.post_id.in_(agent_plaza_post_ids)))
            await db.execute(sa_delete(PlazaPost).where(PlazaPost.id.in_(agent_plaza_post_ids)))

    # 6. Delete tables with tenant_id (no agent/user dependency)
    await db.execute(sa_delete(DailyTokenUsage).where(DailyTokenUsage.tenant_id == tenant_id))
    await db.execute(sa_delete(OrgMember).where(OrgMember.tenant_id == tenant_id))
    await db.execute(sa_delete(OrgDepartment).where(OrgDepartment.tenant_id == tenant_id))
    await db.execute(sa_delete(InvitationCode).where(InvitationCode.tenant_id == tenant_id))
    await db.execute(sa_delete(LLMModel).where(LLMModel.tenant_id == tenant_id))
    await db.execute(sa_delete(TenantSetting).where(TenantSetting.tenant_id == tenant_id))
    await db.execute(sa_delete(IdentityProvider).where(IdentityProvider.tenant_id == tenant_id))
    await db.execute(sa_delete(SSOScanSession).where(SSOScanSession.tenant_id == tenant_id))

    # SkillFiles before Skills (skill_files.skill_id -> skills.id)
    skill_ids = [
        row[0]
        for row in (await db.execute(select(Skill.id).where(Skill.tenant_id == tenant_id))).all()
    ]
    if skill_ids:
        await db.execute(sa_delete(SkillFile).where(SkillFile.skill_id.in_(skill_ids)))
    await db.execute(sa_delete(Skill).where(Skill.tenant_id == tenant_id))

    # 7. Delete agents (after all agent-dependent tables)
    await db.execute(sa_delete(Agent).where(Agent.tenant_id == tenant_id))

    # 8. Delete users (after agents, since agents.creator_id -> users.id)
    await db.execute(sa_delete(User).where(User.tenant_id == tenant_id))

    # 9. Delete the tenant itself
    await db.delete(tenant)
    await db.flush()

    logger.info("Tenant %s (%s) deleted successfully", tenant_id, tenant.name)

    return None
