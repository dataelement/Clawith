"""Platform Admin company management API.

Provides endpoints for platform admins to manage companies, view stats,
and control platform-level settings.
"""

import secrets
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
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
    sso_enabled: bool = False
    sso_domain: str | None = None
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
            sso_enabled=tenant.sso_enabled,
            sso_domain=tenant.sso_domain,
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
