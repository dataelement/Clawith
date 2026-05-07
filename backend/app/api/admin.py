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
from app.models.user import User, Identity

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
    cache_read_tokens_total: int = 0
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
            select(
                sqla_func.coalesce(sqla_func.sum(Agent.tokens_used_total), 0),
                sqla_func.coalesce(sqla_func.sum(Agent.cache_read_tokens_total), 0),
            ).where(
                Agent.tenant_id == tid
            )
        )
        total_tokens, cache_read_tokens_total = tc.one()

        # Org Admin Email (first found if multiple)
        admin_q = await db.execute(
            select(Identity.email)
            .join(User, Identity.id == User.identity_id)
            .where(User.tenant_id == tid, User.role == "org_admin")
            .order_by(User.created_at.asc())
            .limit(1)
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
            cache_read_tokens_total=cache_read_tokens_total,
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
    """Get daily platform metrics within a date range.

    Returns per-day: companies, users, tokens (existing) +
    sessions, DAU, WAU, MAU (new).
    """
    from app.models.activity_log import DailyTokenUsage
    from app.models.chat_session import ChatSession
    from sqlalchemy import cast, Date, text
    from datetime import timedelta

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
            sqla_func.sum(DailyTokenUsage.tokens_used).label('c'),
            sqla_func.sum(DailyTokenUsage.cache_read_tokens).label('cache_read'),
        ).where(
            DailyTokenUsage.date >= start_date,
            DailyTokenUsage.date <= end_date
        ).group_by('d')
    )
    tokens_by_day = {row.d: row.c for row in tokens_q.all()}
    tokens_q = await db.execute(
        select(
            cast(DailyTokenUsage.date, Date).label('d'),
            sqla_func.sum(DailyTokenUsage.cache_read_tokens).label('cache_read'),
        ).where(
            DailyTokenUsage.date >= start_date,
            DailyTokenUsage.date <= end_date
        ).group_by('d')
    )
    cache_by_day = {row.d: row.cache_read for row in tokens_q.all()}

    # 4. New Sessions per day (DAU = distinct users with sessions that day)
    sessions_q = await db.execute(
        select(
            cast(ChatSession.created_at, Date).label('d'),
            sqla_func.count().label('sessions'),
            sqla_func.count(sqla_func.distinct(ChatSession.user_id)).label('dau'),
        ).where(
            ChatSession.created_at >= start_date,
            ChatSession.created_at <= end_date
        ).group_by('d')
    )
    sessions_by_day = {}
    dau_by_day = {}
    for row in sessions_q.all():
        sessions_by_day[row.d] = row.sessions
        dau_by_day[row.d] = row.dau

    # 5. WAU/MAU: for each day, count distinct users in rolling 7/30-day window.
    #    Use a single SQL query with window functions for efficiency.
    wau_mau_q = await db.execute(text("""
        WITH daily_users AS (
            SELECT DISTINCT
                DATE(created_at) AS d,
                user_id
            FROM chat_sessions
            WHERE created_at >= CAST(:range_start AS timestamptz)
              AND created_at <= CAST(:range_end AS timestamptz)
        ),
        day_series AS (
            SELECT CAST(generate_series(
                CAST(:series_start AS date),
                CAST(:series_end AS date),
                CAST('1 day' AS interval)
            ) AS date) AS d
        )
        SELECT
            ds.d,
            (SELECT COUNT(DISTINCT du.user_id) FROM daily_users du
             WHERE du.d BETWEEN ds.d - 6 AND ds.d) AS wau,
            (SELECT COUNT(DISTINCT du.user_id) FROM daily_users du
             WHERE du.d BETWEEN ds.d - 29 AND ds.d) AS mau
        FROM day_series ds
        ORDER BY ds.d
    """), {
        "range_start": start_date - timedelta(days=30),
        "range_end": end_date,
        "series_start": start_date.date(),
        "series_end": end_date.date(),
    })
    wau_by_day = {}
    mau_by_day = {}
    for row in wau_mau_q.all():
        wau_by_day[row[0]] = row[1]
        mau_by_day[row[0]] = row[2]

    # Generate date range list with cumulative totals
    result = []
    current_d = start_date.date()
    end_d = end_date.date()

    # Cumulative totals up to start_date
    total_companies = (await db.execute(select(sqla_func.count()).select_from(Tenant).where(Tenant.created_at < start_date))).scalar() or 0
    total_users = (await db.execute(select(sqla_func.count()).select_from(User).where(User.created_at < start_date))).scalar() or 0
    total_tokens = (await db.execute(select(sqla_func.coalesce(sqla_func.sum(Agent.tokens_used_total), 0)).where(Agent.created_at < start_date))).scalar() or 0
    total_cache_read = (await db.execute(select(sqla_func.coalesce(sqla_func.sum(Agent.cache_read_tokens_total), 0)).where(Agent.created_at < start_date))).scalar() or 0
    total_sessions = (await db.execute(select(sqla_func.count()).select_from(ChatSession).where(ChatSession.created_at < start_date))).scalar() or 0

    while current_d <= end_d:
        nc = companies_by_day.get(current_d, 0)
        nu = users_by_day.get(current_d, 0)
        nt = tokens_by_day.get(current_d, 0)
        ncache = cache_by_day.get(current_d, 0)
        ns = sessions_by_day.get(current_d, 0)

        total_companies += nc
        total_users += nu
        total_tokens += nt
        total_cache_read += ncache
        total_sessions += ns

        result.append({
            "date": current_d.isoformat(),
            "new_companies": nc,
            "total_companies": total_companies,
            "new_users": nu,
            "total_users": total_users,
            "new_tokens": nt,
            "total_tokens": total_tokens,
            "new_cache_read_tokens": ncache,
            "total_cache_read_tokens": total_cache_read,
            "cache_hit_rate": round((ncache or 0) / max(nt or 0, 1), 4),
            # New metrics
            "new_sessions": ns,
            "total_sessions": total_sessions,
            "dau": dau_by_day.get(current_d, 0),
            "wau": wau_by_day.get(current_d, 0),
            "mau": mau_by_day.get(current_d, 0),
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
        select(
            Tenant.name,
            sqla_func.coalesce(sqla_func.sum(Agent.tokens_used_total), 0).label('total'),
            sqla_func.coalesce(sqla_func.sum(Agent.cache_read_tokens_total), 0).label('cache_read'),
        )
        .join(Agent, Agent.tenant_id == Tenant.id)
        .group_by(Tenant.id)
        .order_by(sqla_func.sum(Agent.tokens_used_total).desc())
        .limit(20)
    )
    top_companies = [
        {
            "name": row.name,
            "tokens": row.total,
            "cache_read_tokens": row.cache_read,
            "cache_hit_rate": round((row.cache_read or 0) / max(row.total or 0, 1), 4),
        }
        for row in top_companies_q.all()
    ]

    # Top 20 Agents by total tokens
    top_agents_q = await db.execute(
        select(Agent.name, Tenant.name.label('tenant_name'), Agent.tokens_used_total, Agent.cache_read_tokens_total)
        .join(Tenant, Tenant.id == Agent.tenant_id)
        .order_by(Agent.tokens_used_total.desc())
        .limit(20)
    )
    top_agents = [
        {
            "name": row.name,
            "company": row.tenant_name,
            "tokens": row.tokens_used_total,
            "cache_read_tokens": row.cache_read_tokens_total,
            "cache_hit_rate": round((row.cache_read_tokens_total or 0) / max(row.tokens_used_total or 0, 1), 4),
        }
        for row in top_agents_q.all()
    ]

    return {
        "top_companies": top_companies,
        "top_agents": top_agents
    }


@router.get("/metrics/enhanced")
async def get_enhanced_metrics(
    current_user: User = Depends(require_role("platform_admin")),
    db: AsyncSession = Depends(get_db),
):
    """Enhanced platform metrics: retention, avg tokens/session,
    channel distribution, tool categories, and churn warnings.
    """
    from app.models.chat_session import ChatSession
    from app.models.tool import Tool, AgentTool
    from sqlalchemy import text
    from datetime import timedelta

    now = datetime.utcnow()

    # ── 1. Average tokens per session (last 30 days) ──
    # Sum of daily_token_usage / count of chat_sessions in last 30 days
    thirty_days_ago = now - timedelta(days=30)
    from app.models.activity_log import DailyTokenUsage
    total_tok_30d = (await db.execute(
        select(sqla_func.coalesce(sqla_func.sum(DailyTokenUsage.tokens_used), 0))
        .where(DailyTokenUsage.date >= thirty_days_ago)
    )).scalar() or 0
    total_sess_30d = (await db.execute(
        select(sqla_func.count())
        .select_from(ChatSession)
        .where(ChatSession.created_at >= thirty_days_ago)
    )).scalar() or 1  # avoid div by zero
    avg_tokens_per_session = round(total_tok_30d / max(total_sess_30d, 1))

    # ── 2. 7-Day Retention Rate (excluding companies <14 days old) ──
    # Last week = 14..7 days ago, This week = 7..0 days ago
    retention_q = await db.execute(text("""
        WITH established AS (
            SELECT id FROM tenants WHERE created_at < NOW() - INTERVAL '14 days'
        ),
        last_week_active AS (
            SELECT DISTINCT a.tenant_id
            FROM chat_sessions cs
            JOIN agents a ON a.id = cs.agent_id
            WHERE cs.created_at BETWEEN NOW() - INTERVAL '14 days' AND NOW() - INTERVAL '7 days'
            AND a.tenant_id IN (SELECT id FROM established)
        ),
        this_week_active AS (
            SELECT DISTINCT a.tenant_id
            FROM chat_sessions cs
            JOIN agents a ON a.id = cs.agent_id
            WHERE cs.created_at > NOW() - INTERVAL '7 days'
            AND a.tenant_id IN (SELECT id FROM established)
        )
        SELECT
            COUNT(DISTINCT lw.tenant_id) AS last_week_total,
            COUNT(DISTINCT lw.tenant_id) FILTER (
                WHERE lw.tenant_id IN (SELECT tenant_id FROM this_week_active)
            ) AS retained
        FROM last_week_active lw
    """))
    ret_row = retention_q.first()
    last_week_total = ret_row[0] if ret_row else 0
    retained = ret_row[1] if ret_row else 0
    retention_rate = round(retained * 100.0 / max(last_week_total, 1), 1)

    # ── 3. Channel Distribution (last 30 days) ──
    channel_q = await db.execute(
        select(
            ChatSession.source_channel,
            sqla_func.count().label('count')
        ).where(
            ChatSession.created_at >= thirty_days_ago
        ).group_by(ChatSession.source_channel)
        .order_by(sqla_func.count().desc())
    )
    channel_distribution = [
        {"channel": row.source_channel, "count": row.count}
        for row in channel_q.all()
    ]

    # ── 4. Top 10 Tool Categories ──
    # Count enabled agent_tools grouped by tool category
    tool_q = await db.execute(
        select(
            Tool.category,
            sqla_func.count().label('count')
        ).join(AgentTool, AgentTool.tool_id == Tool.id)
        .where(AgentTool.enabled == True)  # noqa: E712
        .group_by(Tool.category)
        .order_by(sqla_func.count().desc())
        .limit(10)
    )
    tool_category_top10 = [
        {"category": row.category or "uncategorized", "count": row.count}
        for row in tool_q.all()
    ]

    # ── 5. Churn Warnings (>10M tokens, 14+ days inactive) ──
    churn_q = await db.execute(text("""
        WITH tenant_token_totals AS (
            SELECT
                tenant_id,
                SUM(tokens_used_total) AS total_tokens
            FROM agents
            GROUP BY tenant_id
        ),
        tenant_last_active AS (
            SELECT
                a.tenant_id,
                MAX(cs.created_at) AS last_active
            FROM agents a
            LEFT JOIN chat_sessions cs ON cs.agent_id = a.id
            GROUP BY a.tenant_id
        )
        SELECT
            t.name,
            tt.total_tokens,
            tla.last_active,
            CASE
                WHEN tla.last_active IS NULL THEN NULL
                ELSE EXTRACT(DAY FROM NOW() - tla.last_active)::int
            END AS days_inactive
        FROM tenants t
        JOIN tenant_token_totals tt ON tt.tenant_id = t.id
        LEFT JOIN tenant_last_active tla ON tla.tenant_id = t.id
        WHERE tt.total_tokens > 10000000
            AND (
                tla.last_active IS NULL
                OR tla.last_active < NOW() - INTERVAL '14 days'
            )
        ORDER BY tt.total_tokens DESC
    """))
    churn_warnings = []
    for row in churn_q.all():
        churn_warnings.append({
            "name": row[0],
            "total_tokens": row[1],
            "last_active": row[2].isoformat() if row[2] else None,
            "days_inactive": row[3] if row[3] else None,
        })

    return {
        "avg_tokens_per_session_30d": avg_tokens_per_session,
        "retention_rate_7d": retention_rate,
        "last_week_active_companies": last_week_total,
        "retained_companies": retained,
        "channel_distribution": channel_distribution,
        "tool_category_top10": tool_category_top10,
        "churn_warnings": churn_warnings,
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
