"""Platform Admin company management API.

Provides endpoints for platform admins to manage companies, view stats,
and control platform-level settings.
"""

import secrets
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from loguru import logger
from pydantic import BaseModel, Field
from sqlalchemy import func as sqla_func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import require_role
from app.database import get_db
from app.models.agent import Agent
from app.models.invitation_code import InvitationCode
from app.models.system_settings import SystemSetting
from app.models.tenant import Tenant
from app.models.user import User

router = APIRouter(prefix="/admin", tags=["admin"])


# ─── Schemas ────────────────────────────────────────────

class CompanyStats(BaseModel):
    id: uuid.UUID
    name: str
    slug: str
    is_active: bool
    is_default: bool = False
    sso_enabled: bool = False
    sso_domain: str | None = None
    subdomain_prefix: str | None = None
    created_at: datetime | None = None
    user_count: int = 0
    agent_count: int = 0
    agent_running_count: int = 0
    total_tokens: int = 0
    org_admin_email: str | None = None


class CompanyCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)


class CompanyCreateResponse(BaseModel):
    company: CompanyStats
    admin_invitation_code: str


class PlatformSettingsOut(BaseModel):
    allow_self_create_company: bool = True
    invitation_code_enabled: bool = False


class PlatformSettingsUpdate(BaseModel):
    allow_self_create_company: bool | None = None
    invitation_code_enabled: bool | None = None


# ─── Company Management ────────────────────────────────

@router.get("/companies", response_model=list[CompanyStats])
async def list_companies(
    current_user: User = Depends(require_role("platform_admin")),
    db: AsyncSession = Depends(get_db),
):
    """List all companies with stats."""
    tenants = await db.execute(select(Tenant).order_by(Tenant.created_at.desc()))
    result = []

    for tenant in tenants.scalars().all():
        tid = tenant.id

        # User count
        uc = await db.execute(
            select(sqla_func.count()).select_from(User).where(User.tenant_id == tid)
        )
        user_count = uc.scalar() or 0

        # Agent count
        ac = await db.execute(
            select(sqla_func.count()).select_from(Agent).where(Agent.tenant_id == tid)
        )
        agent_count = ac.scalar() or 0

        # Running agents
        rc = await db.execute(
            select(sqla_func.count()).select_from(Agent).where(
                Agent.tenant_id == tid, Agent.status == "running"
            )
        )
        agent_running = rc.scalar() or 0

        # Total tokens
        tc = await db.execute(
            select(sqla_func.coalesce(sqla_func.sum(Agent.tokens_used_total), 0)).where(
                Agent.tenant_id == tid
            )
        )
        total_tokens = tc.scalar() or 0

        # Org Admin Email (first found if multiple)
        admin_q = await db.execute(
            select(User.email).where(User.tenant_id == tid, User.role == "org_admin").order_by(User.created_at.asc()).limit(1)
        )
        org_admin_email = admin_q.scalar()

        result.append(CompanyStats(
            id=tenant.id,
            name=tenant.name,
            slug=tenant.slug,
            is_active=tenant.is_active,
            is_default=tenant.is_default,
            sso_enabled=tenant.sso_enabled,
            sso_domain=tenant.sso_domain,
            subdomain_prefix=tenant.subdomain_prefix,
            created_at=tenant.created_at,
            user_count=user_count,
            agent_count=agent_count,
            agent_running_count=agent_running,
            total_tokens=total_tokens,
            org_admin_email=org_admin_email,
        ))

    return result


@router.post("/companies", response_model=CompanyCreateResponse, status_code=201)
async def create_company(
    data: CompanyCreateRequest,
    current_user: User = Depends(require_role("platform_admin")),
    db: AsyncSession = Depends(get_db),
):
    """Create a new company and generate an admin invitation code (max_uses=1)."""
    import re

    slug = re.sub(r"[^a-z0-9]+", "-", data.name.lower().strip()).strip("-")[:40]
    if not slug:
        slug = "company"
    slug = f"{slug}-{secrets.token_hex(3)}"

    tenant = Tenant(name=data.name, slug=slug, im_provider="web_only")
    db.add(tenant)
    await db.flush()

    # Generate admin invitation code (single-use)
    code_str = secrets.token_urlsafe(12)[:16].upper()
    invite = InvitationCode(
        code=code_str,
        tenant_id=tenant.id,
        max_uses=1,
        created_by=current_user.id,
    )
    db.add(invite)
    await db.flush()

    # Seed default agents (Morty & Meeseeks) for the new company
    try:
        from app.services.agent_seeder import seed_default_agents
        await seed_default_agents(tenant_id=tenant.id, creator_id=current_user.id, db=db)
    except Exception as e:
        logger.warning(f"[create_company] Failed to seed default agents: {e}")

    return CompanyCreateResponse(
        company=CompanyStats(
            id=tenant.id,
            name=tenant.name,
            slug=tenant.slug,
            is_active=tenant.is_active,
            created_at=tenant.created_at,
        ),
        admin_invitation_code=code_str,
    )


@router.put("/companies/{company_id}/toggle")
async def toggle_company(
    company_id: uuid.UUID,
    current_user: User = Depends(require_role("platform_admin")),
    db: AsyncSession = Depends(get_db),
):
    """Enable or disable a company."""
    result = await db.execute(select(Tenant).where(Tenant.id == company_id))
    tenant = result.scalar_one_or_none()
    if not tenant:
        raise HTTPException(status_code=404, detail="Company not found")

    new_state = not tenant.is_active
    tenant.is_active = new_state

    # When disabling: pause all running agents
    if not new_state:
        agents = await db.execute(
            select(Agent).where(Agent.tenant_id == company_id, Agent.status == "running")
        )
        for agent in agents.scalars().all():
            agent.status = "paused"

    await db.flush()
    return {"ok": True, "is_active": new_state}



@router.get("/companies/{company_id}/invitation-codes")
async def list_company_invitation_codes(
    company_id: uuid.UUID,
    current_user: User = Depends(require_role("platform_admin")),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(InvitationCode)
        .where(InvitationCode.tenant_id == company_id)
        .where(InvitationCode.is_active == True)
        .order_by(InvitationCode.created_at.desc())
    )
    codes = result.scalars().all()
    return {
        "codes": [
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
    }


@router.post("/companies/{company_id}/invitation-codes")
async def create_company_invitation_code(
    company_id: uuid.UUID,
    current_user: User = Depends(require_role("platform_admin")),
    db: AsyncSession = Depends(get_db),
):
    t_result = await db.execute(select(Tenant).where(Tenant.id == company_id))
    if not t_result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Company not found")

    code_str = secrets.token_urlsafe(12)[:16].upper()
    invite = InvitationCode(
        code=code_str,
        tenant_id=company_id,
        max_uses=1,
        created_by=current_user.id,
    )
    db.add(invite)
    await db.flush()
    return {"code": code_str}

# ─── Platform Metrics Dashboard ─────────────────────────

from typing import Any
from fastapi import Query

@router.get("/metrics/timeseries", response_model=list[dict[str, Any]])
async def get_platform_timeseries(
    start_date: datetime,
    end_date: datetime,
    current_user: User = Depends(require_role("platform_admin")),
    db: AsyncSession = Depends(get_db),
):
    """Get daily new companies, users, and tokens consumed within a date range."""
    # Ensure naive datetimes are treated as UTC or passed as aware
    # Group by DATE(created_at)
    from app.models.activity_log import DailyTokenUsage
    from sqlalchemy import cast, Date

    # 1. New Companies per day
    companies_q = await db.execute(
        select(
            cast(Tenant.created_at, Date).label('d'),
            sqla_func.count().label('c')
        ).where(
            Tenant.created_at >= start_date,
            Tenant.created_at <= end_date
        ).group_by('d')
    )
    companies_by_day = {row.d: row.c for row in companies_q.all()}

    # 2. New Users per day
    users_q = await db.execute(
        select(
            cast(User.created_at, Date).label('d'),
            sqla_func.count().label('c')
        ).where(
            User.created_at >= start_date,
            User.created_at <= end_date
        ).group_by('d')
    )
    users_by_day = {row.d: row.c for row in users_q.all()}

    # 3. Tokens consumed per day
    tokens_q = await db.execute(
        select(
            cast(DailyTokenUsage.date, Date).label('d'),
            sqla_func.sum(DailyTokenUsage.tokens_used).label('c')
        ).where(
            DailyTokenUsage.date >= start_date,
            DailyTokenUsage.date <= end_date
        ).group_by('d')
    )
    tokens_by_day = {row.d: row.c for row in tokens_q.all()}

    # Generate date range list
    from datetime import timedelta
    result = []
    current_d = start_date.date()
    end_d = end_date.date()
    
    # Calculate cumulative totals up to start_date
    total_companies = (await db.execute(select(sqla_func.count()).select_from(Tenant).where(Tenant.created_at < start_date))).scalar() or 0
    total_users = (await db.execute(select(sqla_func.count()).select_from(User).where(User.created_at < start_date))).scalar() or 0
    total_tokens = (await db.execute(select(sqla_func.coalesce(sqla_func.sum(Agent.tokens_used_total), 0)).where(Agent.created_at < start_date))).scalar() or 0

    while current_d <= end_d:
        nc = companies_by_day.get(current_d, 0)
        nu = users_by_day.get(current_d, 0)
        nt = tokens_by_day.get(current_d, 0)
        
        total_companies += nc
        total_users += nu
        total_tokens += nt
        
        result.append({
            "date": current_d.isoformat(),
            "new_companies": nc,
            "total_companies": total_companies,
            "new_users": nu,
            "total_users": total_users,
            "new_tokens": nt,
            "total_tokens": total_tokens,
        })
        current_d += timedelta(days=1)
        
    return result


@router.get("/metrics/leaderboards")
async def get_platform_leaderboards(
    current_user: User = Depends(require_role("platform_admin")),
    db: AsyncSession = Depends(get_db),
):
    """Get Top 20 token consuming companies and agents."""
    # Top 20 Companies by total tokens
    top_companies_q = await db.execute(
        select(Tenant.name, sqla_func.coalesce(sqla_func.sum(Agent.tokens_used_total), 0).label('total'))
        .join(Agent, Agent.tenant_id == Tenant.id)
        .group_by(Tenant.id)
        .order_by(sqla_func.sum(Agent.tokens_used_total).desc())
        .limit(20)
    )
    top_companies = [{"name": row.name, "tokens": row.total} for row in top_companies_q.all()]

    # Top 20 Agents by total tokens
    top_agents_q = await db.execute(
        select(Agent.name, Tenant.name.label('tenant_name'), Agent.tokens_used_total)
        .join(Tenant, Tenant.id == Agent.tenant_id)
        .order_by(Agent.tokens_used_total.desc())
        .limit(20)
    )
    top_agents = [{"name": row.name, "company": row.tenant_name, "tokens": row.tokens_used_total} for row in top_agents_q.all()]

    return {
        "top_companies": top_companies,
        "top_agents": top_agents
    }


# ─── Platform Settings ─────────────────────────────────

@router.get("/platform-settings", response_model=PlatformSettingsOut)
async def get_platform_settings(
    current_user: User = Depends(require_role("platform_admin")),
    db: AsyncSession = Depends(get_db),
):
    """Get platform-level settings."""
    settings: dict[str, bool] = {}

    for key, default in [
        ("allow_self_create_company", True),
        ("invitation_code_enabled", False),
    ]:
        r = await db.execute(select(SystemSetting).where(SystemSetting.key == key))
        s = r.scalar_one_or_none()
        settings[key] = s.value.get("enabled", default) if s else default

    return PlatformSettingsOut(**settings)


@router.put("/platform-settings", response_model=PlatformSettingsOut)
async def update_platform_settings(
    data: PlatformSettingsUpdate,
    current_user: User = Depends(require_role("platform_admin")),
    db: AsyncSession = Depends(get_db),
):
    """Update platform-level settings."""
    updates = data.model_dump(exclude_unset=True)

    for key, value in updates.items():
        r = await db.execute(select(SystemSetting).where(SystemSetting.key == key))
        s = r.scalar_one_or_none()
        if s:
            s.value = {"enabled": value}
        else:
            db.add(SystemSetting(key=key, value={"enabled": value}))

    await db.flush()
    return await get_platform_settings(current_user=current_user, db=db)


@router.delete("/companies/{company_id}", status_code=204)
async def delete_company(
    company_id: uuid.UUID,
    current_user: User = Depends(require_role("platform_admin")),
    db: AsyncSession = Depends(get_db),
):
    """Permanently delete a company and all associated data.

    Cannot delete the default company.
    Cascade-deletes all related records in dependency order.
    """
    # 1. Find the tenant
    result = await db.execute(select(Tenant).where(Tenant.id == company_id))
    tenant = result.scalar_one_or_none()
    if not tenant:
        raise HTTPException(status_code=404, detail="Company not found")

    # 2. Cannot delete default company
    if tenant.is_default:
        raise HTTPException(
            status_code=400,
            detail="Cannot delete the default company. Please set another company as default first."
        )

    # 3. Cascade delete all associated data
    from sqlalchemy import delete as sa_delete
    from app.models.activity_log import AgentActivityLog, DailyTokenUsage
    from app.models.audit import AuditLog, ApprovalRequest, ChatMessage
    from app.models.channel_config import ChannelConfig
    from app.models.chat_session import ChatSession
    from app.models.gateway_message import GatewayMessage
    from app.models.llm import LLMModel
    from app.models.notification import Notification
    from app.models.org import OrgMember, OrgDepartment, AgentRelationship, AgentAgentRelationship
    from app.models.published_page import PublishedPage
    from app.models.schedule import AgentSchedule
    from app.models.skill import Skill
    from app.models.task import Task
    from app.models.tenant_setting import TenantSetting
    from app.models.trigger import AgentTrigger
    from app.models.tool import AgentTool
    from app.models.identity import IdentityProvider, SSOScanSession

    # 3.1 Collect agent_ids and user_ids for this tenant
    agent_ids_result = await db.execute(
        select(Agent.id).where(Agent.tenant_id == company_id)
    )
    agent_ids = [row[0] for row in agent_ids_result.all()]

    user_ids_result = await db.execute(
        select(User.id).where(User.tenant_id == company_id)
    )
    user_ids = [row[0] for row in user_ids_result.all()]

    # 3.2 Delete tables that reference agents (via agent_id)
    if agent_ids:
        await db.execute(sa_delete(AgentTrigger).where(AgentTrigger.agent_id.in_(agent_ids)))
        await db.execute(sa_delete(AgentSchedule).where(AgentSchedule.agent_id.in_(agent_ids)))
        await db.execute(sa_delete(AgentActivityLog).where(AgentActivityLog.agent_id.in_(agent_ids)))
        await db.execute(sa_delete(ChannelConfig).where(ChannelConfig.agent_id.in_(agent_ids)))
        await db.execute(sa_delete(AgentTool).where(AgentTool.agent_id.in_(agent_ids)))
        await db.execute(sa_delete(Notification).where(Notification.agent_id.in_(agent_ids)))
        # Delete TaskLog before Task (FK: task_id -> tasks.id, no cascade)
        from app.models.task import TaskLog
        task_ids_r = await db.execute(select(Task.id).where(Task.agent_id.in_(agent_ids)))
        task_ids = [row[0] for row in task_ids_r.all()]
        if task_ids:
            await db.execute(sa_delete(TaskLog).where(TaskLog.task_id.in_(task_ids)))
        await db.execute(sa_delete(Task).where(Task.agent_id.in_(agent_ids)))
        await db.execute(sa_delete(AuditLog).where(AuditLog.agent_id.in_(agent_ids)))
        await db.execute(sa_delete(ApprovalRequest).where(ApprovalRequest.agent_id.in_(agent_ids)))
        await db.execute(sa_delete(ChatMessage).where(ChatMessage.agent_id.in_(agent_ids)))
        await db.execute(sa_delete(ChatSession).where(ChatSession.agent_id.in_(agent_ids)))
        await db.execute(sa_delete(GatewayMessage).where(GatewayMessage.agent_id.in_(agent_ids)))
        from app.models.agent import AgentPermission
        await db.execute(sa_delete(AgentPermission).where(AgentPermission.agent_id.in_(agent_ids)))
        await db.execute(sa_delete(AgentAgentRelationship).where(AgentAgentRelationship.agent_id.in_(agent_ids)))
        await db.execute(sa_delete(AgentAgentRelationship).where(AgentAgentRelationship.target_agent_id.in_(agent_ids)))
        await db.execute(sa_delete(AgentRelationship).where(AgentRelationship.agent_id.in_(agent_ids)))

        # Null out cross-tenant FK references (other tenants' records pointing to our agents)
        from sqlalchemy import update as sa_update
        await db.execute(
            sa_update(ChatSession).where(ChatSession.peer_agent_id.in_(agent_ids)).values(peer_agent_id=None)
        )
        await db.execute(
            sa_update(GatewayMessage).where(GatewayMessage.sender_agent_id.in_(agent_ids)).values(sender_agent_id=None)
        )

    # 3.3 Delete tables that reference users but not tenant directly
    if user_ids:
        from app.models.agent import AgentTemplate
        await db.execute(sa_delete(AgentTemplate).where(AgentTemplate.created_by.in_(user_ids)))

    # 3.3b Null out cross-tenant user FK references
    if user_ids:
        from sqlalchemy import update as sa_update
        await db.execute(
            sa_update(GatewayMessage).where(GatewayMessage.sender_user_id.in_(user_ids)).values(sender_user_id=None)
        )

    # 3.4 Delete tables with tenant_id (no agent dependency)
    await db.execute(sa_delete(DailyTokenUsage).where(DailyTokenUsage.tenant_id == company_id))
    await db.execute(sa_delete(PublishedPage).where(PublishedPage.tenant_id == company_id))
    await db.execute(sa_delete(OrgMember).where(OrgMember.tenant_id == company_id))
    await db.execute(sa_delete(OrgDepartment).where(OrgDepartment.tenant_id == company_id))
    await db.execute(sa_delete(InvitationCode).where(InvitationCode.tenant_id == company_id))
    # Delete SkillFile before Skill (FK: skill_id -> skills.id, no cascade)
    from app.models.skill import SkillFile
    skill_ids_r = await db.execute(select(Skill.id).where(Skill.tenant_id == company_id))
    skill_ids = [row[0] for row in skill_ids_r.all()]
    if skill_ids:
        await db.execute(sa_delete(SkillFile).where(SkillFile.skill_id.in_(skill_ids)))
    await db.execute(sa_delete(Skill).where(Skill.tenant_id == company_id))
    await db.execute(sa_delete(LLMModel).where(LLMModel.tenant_id == company_id))
    await db.execute(sa_delete(TenantSetting).where(TenantSetting.tenant_id == company_id))

    # 3.4b Delete identity tables with tenant_id (soft FK)
    await db.execute(sa_delete(IdentityProvider).where(IdentityProvider.tenant_id == company_id))
    await db.execute(sa_delete(SSOScanSession).where(SSOScanSession.tenant_id == company_id))

    # 3.5 Delete agents (after all agent-dependent tables)
    await db.execute(sa_delete(Agent).where(Agent.tenant_id == company_id))

    # 3.6 Delete users (after agents, since agents.creator_id -> users.id)
    await db.execute(sa_delete(User).where(User.tenant_id == company_id))

    # 3.7 Delete the tenant itself
    await db.delete(tenant)
    await db.flush()

    return None
