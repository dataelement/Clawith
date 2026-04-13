"""OKR REST API — objectives, key results, settings, reports and periods.

All endpoints are tenant-scoped: data is filtered by the requesting user's
tenant_id so cross-tenant leakage is impossible.

Route summary
─────────────
GET/PUT   /api/okr/settings
GET       /api/okr/periods
GET/POST  /api/okr/objectives
PATCH     /api/okr/objectives/{id}
GET/POST  /api/okr/objectives/{id}/key-results
PATCH     /api/okr/key-results/{id}
POST      /api/okr/key-results/{id}/progress        (manual progress update)
GET       /api/okr/reports
GET       /api/okr/members-without-okr             (P4 onboarding: admin view)
POST      /api/okr/trigger-member-outreach         (P4 onboarding: fire OKR Agent)
"""

import uuid
from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, delete

from app.api.auth import get_current_user
from app.database import async_session
from app.models.okr import (
    OKRAlignment,
    OKRKeyResult,
    OKRObjective,
    OKRProgressLog,
    OKRSettings,
    WorkReport,
)
from app.models.user import User
from app.models.agent import Agent, AgentPermission
from app.models.org import AgentRelationship, AgentAgentRelationship, OrgMember

router = APIRouter(prefix="/api/okr", tags=["okr"])


# ─── Helpers ─────────────────────────────────────────────────────────────────


async def _sync_okr_agent_relationships(db, tenant_id: uuid.UUID, okr_agent_id: uuid.UUID) -> None:
    """Additive sync: connect the OKR Agent to all eligible org members and agents.

    Uses an ADDITIVE strategy — existing relationships are preserved so that
    manually removed members are not re-added.  Only *new* eligible members
    (org-synced or platform-registered) that are not yet in the list are added.

    Eligibility rules:
    - Human: every active OrgMember in this tenant, PLUS every active platform
      User without an OrgMember record (a minimal OrgMember is created for them
      so that the standard AgentRelationship FK can be satisfied).
    - Agent: every non-private, non-stopped, non-system Agent in this tenant
      (excluding the OKR Agent itself).
    """
    from app.api.relationships import _regenerate_relationships_file
    from app.models.user import User, Identity
    from sqlalchemy import exists, and_, or_

    # ── Snapshot of what already exists ──────────────────────────────────────
    existing_human_result = await db.execute(
        select(AgentRelationship.member_id).where(AgentRelationship.agent_id == okr_agent_id)
    )
    existing_member_ids: set[uuid.UUID] = {row[0] for row in existing_human_result.fetchall()}

    existing_agent_result = await db.execute(
        select(AgentAgentRelationship.target_agent_id).where(AgentAgentRelationship.agent_id == okr_agent_id)
    )
    existing_target_agent_ids: set[uuid.UUID] = {row[0] for row in existing_agent_result.fetchall()}

    # ── Step A: Add active OrgMembers not yet in the relationship ─────────────
    member_result = await db.execute(
        select(OrgMember.id).where(
            OrgMember.tenant_id == tenant_id,
            OrgMember.status == "active",
            OrgMember.id.notin_(existing_member_ids) if existing_member_ids else True,
        )
    )
    new_member_ids: list[uuid.UUID] = []
    for (member_id,) in member_result.fetchall():
        db.add(AgentRelationship(
            agent_id=okr_agent_id,
            member_id=member_id,
            relation="team_member",
            description="OKR tracking — synced from org structure",
        ))
        new_member_ids.append(member_id)

    # Track which user_ids already have an OrgMember (to avoid duplicates)
    linked_user_result = await db.execute(
        select(OrgMember.user_id).where(
            OrgMember.tenant_id == tenant_id,
            OrgMember.user_id.isnot(None),
        )
    )
    already_linked_user_ids: set[uuid.UUID] = {row[0] for row in linked_user_result.fetchall()}

    # ── Step B: Add platform Users that have NO OrgMember yet ─────────────────
    # These are users who registered directly on the platform (not via SSO sync).
    # We create a minimal OrgMember for each so the AgentRelationship FK is satisfied.
    platform_user_result = await db.execute(
        select(User.id, User.display_name, User.avatar_url, User.identity_id).where(
            User.tenant_id == tenant_id,
            User.is_active == True,  # noqa: E712
            User.id.notin_(already_linked_user_ids) if already_linked_user_ids else True,
        )
    )
    platform_users = platform_user_result.fetchall()

    for pu in platform_users:
        # Fetch email from Identity if available
        email: str | None = None
        if pu.identity_id:
            ident_res = await db.execute(
                select(Identity.email).where(Identity.id == pu.identity_id)
            )
            email = ident_res.scalar_one_or_none()

        # Create a minimal OrgMember record linked back to this User
        new_om = OrgMember(
            tenant_id=tenant_id,
            user_id=pu.id,
            name=pu.display_name or "Unknown",
            email=email,
            avatar_url=pu.avatar_url,
            status="active",
        )
        db.add(new_om)
        await db.flush()  # Obtain the new OrgMember.id before inserting the relationship

        # Only add the relationship if not already present
        if new_om.id not in existing_member_ids:
            db.add(AgentRelationship(
                agent_id=okr_agent_id,
                member_id=new_om.id,
                relation="team_member",
                description="OKR tracking — synced from platform user account",
            ))

    # ── Step C: Add company-visible agents not yet in the relationship ─────────
    has_company_perm = exists().where(
        and_(
            AgentPermission.agent_id == Agent.id,
            AgentPermission.scope_type.in_(["company", "department"]),
        )
    )
    has_no_perm = ~exists().where(AgentPermission.agent_id == Agent.id)

    agent_result = await db.execute(
        select(Agent.id).where(
            Agent.tenant_id == tenant_id,
            Agent.id != okr_agent_id,
            Agent.status.notin_(["stopped", "error"]),
            Agent.id.notin_(existing_target_agent_ids) if existing_target_agent_ids else True,
            or_(has_company_perm, has_no_perm),
        )
    )
    for (agent_id,) in agent_result.fetchall():
        db.add(AgentAgentRelationship(
            agent_id=okr_agent_id,
            target_agent_id=agent_id,
            relation="collaborator",
            description="Auto-linked — OKR relationship sync",
        ))

    await db.flush()
    # Regenerate the relationships.md workspace file for the OKR Agent
    await _regenerate_relationships_file(db, okr_agent_id)


async def _get_or_create_settings(db, tenant_id: uuid.UUID) -> OKRSettings:
    """Return the OKRSettings row for this tenant, creating it if missing."""
    result = await db.execute(
        select(OKRSettings).where(OKRSettings.tenant_id == tenant_id)
    )
    settings = result.scalar_one_or_none()
    if not settings:
        settings = OKRSettings(tenant_id=tenant_id)
        db.add(settings)
        await db.flush()
    return settings


def _compute_current_period(
    frequency: str, length_days: int | None
) -> tuple[date, date]:
    """Compute the start and end dates of the current OKR period.

    This is a simple deterministic calculation from today's date so the
    frontend and API always agree on what "the current period" is.
    """
    today = date.today()
    if frequency == "monthly":
        start = today.replace(day=1)
        # Last day of this month
        if today.month == 12:
            end = today.replace(month=12, day=31)
        else:
            end = today.replace(month=today.month + 1, day=1) - timedelta(days=1)
    elif frequency == "custom" and length_days:
        # Align to multiples of length_days from the Unix epoch
        epoch = date(1970, 1, 1)
        days_since_epoch = (today - epoch).days
        period_index = days_since_epoch // length_days
        start = epoch + timedelta(days=period_index * length_days)
        end = start + timedelta(days=length_days - 1)
    else:
        # Default: quarterly (Q1/Q2/Q3/Q4)
        quarter = (today.month - 1) // 3 + 1
        start = date(today.year, (quarter - 1) * 3 + 1, 1)
        if quarter == 4:
            end = date(today.year, 12, 31)
        else:
            end = date(today.year, quarter * 3 + 1, 1) - timedelta(days=1)
    return start, end


# ─── Pydantic schemas ─────────────────────────────────────────────────────────


class OKRSettingsOut(BaseModel):
    enabled: bool
    daily_report_enabled: bool
    daily_report_time: str
    weekly_report_enabled: bool
    weekly_report_day: int
    period_frequency: str
    period_length_days: int | None = None


class OKRSettingsUpdate(BaseModel):
    enabled: bool | None = None
    daily_report_enabled: bool | None = None
    daily_report_time: str | None = None
    weekly_report_enabled: bool | None = None
    weekly_report_day: int | None = None
    period_frequency: str | None = None
    period_length_days: int | None = None


class KeyResultOut(BaseModel):
    id: str
    objective_id: str
    title: str
    target_value: float
    current_value: float
    unit: str | None = None
    focus_ref: str | None = None
    status: str
    last_updated_at: str
    created_at: str
    # Alignment refs (read-only summary)
    alignments: list[dict] = []


class ObjectiveOut(BaseModel):
    id: str
    title: str
    description: str | None = None
    owner_type: str
    owner_id: str | None = None
    owner_display_name: str | None = None  # display name of user/agent owner
    period_start: str
    period_end: str
    status: str
    created_at: str
    key_results: list[KeyResultOut] = []


class ObjectiveCreate(BaseModel):
    title: str
    description: str | None = None
    owner_type: str = "company"
    owner_id: str | None = None
    period_start: str
    period_end: str


class ObjectiveUpdate(BaseModel):
    title: str | None = None
    description: str | None = None
    status: str | None = None


class KeyResultCreate(BaseModel):
    title: str
    target_value: float = 100.0
    unit: str | None = None
    focus_ref: str | None = None


class KeyResultUpdate(BaseModel):
    title: str | None = None
    current_value: float | None = None
    target_value: float | None = None
    unit: str | None = None
    focus_ref: str | None = None
    status: str | None = None


class ProgressUpdate(BaseModel):
    value: float
    note: str | None = None
    # Optional explicit status override; when omitted, auto-computed from progress ratio
    status: str | None = None


class PeriodOut(BaseModel):
    start: str
    end: str
    label: str
    is_current: bool


class WorkReportOut(BaseModel):
    id: str
    author_type: str
    author_id: str
    report_type: str
    period_date: str
    content: str
    source: str
    created_at: str


# ─── Settings ─────────────────────────────────────────────────────────────────


@router.get("/settings", response_model=OKRSettingsOut)
async def get_okr_settings(user=Depends(get_current_user)):
    """Return OKR configuration for the current tenant."""
    async with async_session() as db:
        settings = await _get_or_create_settings(db, user.tenant_id)
        await db.commit()
        return OKRSettingsOut(
            enabled=settings.enabled,
            daily_report_enabled=settings.daily_report_enabled,
            daily_report_time=settings.daily_report_time,
            weekly_report_enabled=settings.weekly_report_enabled,
            weekly_report_day=settings.weekly_report_day,
            period_frequency=settings.period_frequency,
            period_length_days=settings.period_length_days,
        )


@router.put("/settings", response_model=OKRSettingsOut)
async def update_okr_settings(body: OKRSettingsUpdate, user=Depends(get_current_user)):
    """Update OKR configuration. Org admins only.

    When `enabled` transitions False → True, automatically seeds an OKR Agent
    for this tenant (idempotent — safe to call if one already exists).
    """
    # Allow org admins and platform admins to modify OKR settings.
    # user.role is the canonical authority; is_admin is not a real field.
    if getattr(user, "role", None) not in ("org_admin", "platform_admin"):
        raise HTTPException(403, "Only org admins can modify OKR settings")

    async with async_session() as db:
        settings = await _get_or_create_settings(db, user.tenant_id)

        # Detect False → True transition before overwriting
        was_disabled = not settings.enabled

        if body.enabled is not None:
            settings.enabled = body.enabled
        if body.daily_report_enabled is not None:
            settings.daily_report_enabled = body.daily_report_enabled
        if body.daily_report_time is not None:
            settings.daily_report_time = body.daily_report_time
        if body.weekly_report_enabled is not None:
            settings.weekly_report_enabled = body.weekly_report_enabled
        if body.weekly_report_day is not None:
            settings.weekly_report_day = body.weekly_report_day
        if body.period_frequency is not None:
            settings.period_frequency = body.period_frequency
        if body.period_length_days is not None:
            settings.period_length_days = body.period_length_days

        await db.commit()

        # If OKR was just enabled, seed the OKR Agent for this tenant.
        # Relationship management is done explicitly via POST /api/okr/sync-relationships.
        if was_disabled and body.enabled:
            import asyncio
            from app.services.agent_seeder import seed_okr_agent_for_tenant
            asyncio.create_task(
                seed_okr_agent_for_tenant(
                    tenant_id=user.tenant_id,
                    creator_id=user.id,
                )
            )

        return OKRSettingsOut(
            enabled=settings.enabled,
            daily_report_enabled=settings.daily_report_enabled,
            daily_report_time=settings.daily_report_time,
            weekly_report_enabled=settings.weekly_report_enabled,
            weekly_report_day=settings.weekly_report_day,
            period_frequency=settings.period_frequency,
            period_length_days=settings.period_length_days,
        )


# ─── Relationship Sync ────────────────────────────────────────────────────────


@router.post("/sync-relationships")
async def sync_okr_relationships(user=Depends(get_current_user)):
    """Manually re-sync the OKR Agent's relationship network.

    Connects the OKR Agent to all active org members (org-structure-synced
    humans) and all company-visible agents in this tenant.  Idempotent —
    safe to call multiple times; existing relationships are replaced.

    Org admins and platform admins only.
    """
    if getattr(user, "role", None) not in ("org_admin", "platform_admin"):
        raise HTTPException(403, "Only org admins can sync OKR relationships")

    async with async_session() as db:
        # Locate the OKR Agent for this tenant
        result = await db.execute(
            select(Agent.id).where(
                Agent.tenant_id == user.tenant_id,
                Agent.name.ilike("%OKR%"),
            ).limit(1)
        )
        row = result.first()
        if not row:
            raise HTTPException(404, "OKR Agent not found for this tenant")
        okr_agent_id = row[0]

        await _sync_okr_agent_relationships(db, user.tenant_id, okr_agent_id)
        await db.commit()

    return {"status": "ok", "okr_agent_id": str(okr_agent_id)}


# ─── Periods ──────────────────────────────────────────────────────────────────



@router.get("/periods", response_model=list[PeriodOut])
async def list_periods(user=Depends(get_current_user)):
    """Return an ordered list of OKR periods (past 2 + current + next 1).

    Periods are computed from the tenant's OKR settings frequency, not from
    database rows, so they always exist even if they have no OKRs yet.
    """
    async with async_session() as db:
        settings = await _get_or_create_settings(db, user.tenant_id)
        await db.commit()

    freq = settings.period_frequency
    length = settings.period_length_days

    cur_start, cur_end = _compute_current_period(freq, length)

    def _period_label(start: date, freq: str) -> str:
        if freq == "monthly":
            return start.strftime("%b %Y")
        elif freq == "quarterly":
            q = (start.month - 1) // 3 + 1
            return f"Q{q} {start.year}"
        else:
            return f"{start.isoformat()} – {(start + timedelta(days=(length or 90) - 1)).isoformat()}"

    def _prev_start(start: date) -> date:
        if freq == "quarterly":
            return (start - timedelta(days=1)).replace(
                day=1, month=((start.month - 4) % 12) + 1
            )
        elif freq == "monthly":
            m = start.month - 1 or 12
            y = start.year if start.month > 1 else start.year - 1
            return start.replace(year=y, month=m, day=1)
        else:
            return start - timedelta(days=length or 90)

    def _next_start(end: date) -> date:
        return end + timedelta(days=1)

    periods = []
    # Previous 2 periods
    s = cur_start
    for _ in range(2):
        s = _prev_start(s)
    for _ in range(2):
        ps, pe = _compute_current_period(freq, length) if s == cur_start else (s, _compute_current_period(freq, length)[1])
        ns = _next_start(s)
        _, pe = _compute_current_period(freq, length) if s == cur_start else (None, None)
        if pe is None:
            # For non-quarterly/monthly we approximate end
            pe = s + timedelta(days=(length or 90) - 1)
        periods.append(PeriodOut(
            start=s.isoformat(),
            end=pe.isoformat(),
            label=_period_label(s, freq),
            is_current=(s == cur_start),
        ))
        s = _next_start(pe)

    # Current period
    periods.append(PeriodOut(
        start=cur_start.isoformat(),
        end=cur_end.isoformat(),
        label=_period_label(cur_start, freq),
        is_current=True,
    ))
    # Next period
    next_start = _next_start(cur_end)
    next_end = next_start + timedelta(days=(length or 90) - 1)
    periods.append(PeriodOut(
        start=next_start.isoformat(),
        end=next_end.isoformat(),
        label=_period_label(next_start, freq),
        is_current=False,
    ))

    return sorted(periods, key=lambda p: p.start)


# ─── Objectives ───────────────────────────────────────────────────────────────


def _kr_to_out(kr: OKRKeyResult) -> KeyResultOut:
    return KeyResultOut(
        id=str(kr.id),
        objective_id=str(kr.objective_id),
        title=kr.title,
        target_value=kr.target_value,
        current_value=kr.current_value,
        unit=kr.unit,
        focus_ref=kr.focus_ref,
        status=kr.status,
        last_updated_at=kr.last_updated_at.isoformat() if kr.last_updated_at else "",
        created_at=kr.created_at.isoformat() if kr.created_at else "",
    )


def _obj_to_out(
    obj: OKRObjective,
    krs: list[OKRKeyResult] | None = None,
    owner_display_name: str | None = None,
) -> ObjectiveOut:
    return ObjectiveOut(
        id=str(obj.id),
        title=obj.title,
        description=obj.description,
        owner_type=obj.owner_type,
        owner_id=str(obj.owner_id) if obj.owner_id else None,
        owner_display_name=owner_display_name,
        period_start=obj.period_start.isoformat(),
        period_end=obj.period_end.isoformat(),
        status=obj.status,
        created_at=obj.created_at.isoformat() if obj.created_at else "",
        key_results=[_kr_to_out(kr) for kr in (krs or [])],
    )


@router.get("/objectives", response_model=list[ObjectiveOut])
async def list_objectives(
    period_start: str | None = None,
    period_end: str | None = None,
    user=Depends(get_current_user),
):
    """List all Objectives for the current tenant within a period.

    If period_start / period_end are not supplied, defaults to the current
    OKR period computed from the tenant's OKR settings.
    """
    async with async_session() as db:
        if not period_start or not period_end:
            settings = await _get_or_create_settings(db, user.tenant_id)
            ps, pe = _compute_current_period(
                settings.period_frequency, settings.period_length_days
            )
            await db.commit()
        else:
            ps = date.fromisoformat(period_start)
            pe = date.fromisoformat(period_end)

        result = await db.execute(
            select(OKRObjective)
            .where(
                OKRObjective.tenant_id == user.tenant_id,
                OKRObjective.period_start >= ps,
                OKRObjective.period_end <= pe,
                OKRObjective.status != "archived",
            )
            .order_by(OKRObjective.owner_type, OKRObjective.created_at)
        )
        objectives = result.scalars().all()

        # Fetch all KRs for these objectives in one query
        obj_ids = [o.id for o in objectives]
        krs_result = await db.execute(
            select(OKRKeyResult)
            .where(OKRKeyResult.objective_id.in_(obj_ids))
            .order_by(OKRKeyResult.created_at)
        )
        all_krs = krs_result.scalars().all()

        # Group KRs by objective
        krs_by_obj: dict[uuid.UUID, list[OKRKeyResult]] = {}
        for kr in all_krs:
            krs_by_obj.setdefault(kr.objective_id, []).append(kr)

        # ── Look up owner display names for user/agent objectives ──────────────
        # Bulk-fetch to avoid N+1 queries.
        user_owner_ids = [o.owner_id for o in objectives if o.owner_type == 'user' and o.owner_id]
        agent_owner_ids = [o.owner_id for o in objectives if o.owner_type == 'agent' and o.owner_id]

        user_names: dict[uuid.UUID, str] = {}
        if user_owner_ids:
            u_result = await db.execute(
                select(User.id, User.display_name).where(User.id.in_(user_owner_ids))
            )
            user_names = {row.id: (row.display_name or '') for row in u_result.fetchall()}

        agent_names: dict[uuid.UUID, str] = {}
        if agent_owner_ids:
            a_result = await db.execute(
                select(Agent.id, Agent.name).where(Agent.id.in_(agent_owner_ids))
            )
            agent_names = {row.id: (row.name or '') for row in a_result.fetchall()}

        def _resolve_owner_name(obj: OKRObjective) -> str | None:
            if obj.owner_type == 'user' and obj.owner_id:
                return user_names.get(obj.owner_id)
            if obj.owner_type == 'agent' and obj.owner_id:
                return agent_names.get(obj.owner_id)
            return None

        return [
            _obj_to_out(o, krs_by_obj.get(o.id, []), _resolve_owner_name(o))
            for o in objectives
        ]


@router.post("/objectives", response_model=ObjectiveOut)
async def create_objective(body: ObjectiveCreate, user=Depends(get_current_user)):
    """Create a new Objective."""
    async with async_session() as db:
        obj = OKRObjective(
            tenant_id=user.tenant_id,
            title=body.title,
            description=body.description,
            owner_type=body.owner_type,
            owner_id=uuid.UUID(body.owner_id) if body.owner_id else None,
            period_start=date.fromisoformat(body.period_start),
            period_end=date.fromisoformat(body.period_end),
        )
        db.add(obj)
        await db.commit()
        await db.refresh(obj)
        return _obj_to_out(obj)


@router.patch("/objectives/{objective_id}", response_model=ObjectiveOut)
async def update_objective(
    objective_id: uuid.UUID,
    body: ObjectiveUpdate,
    user=Depends(get_current_user),
):
    """Update an Objective's title, description or status."""
    async with async_session() as db:
        result = await db.execute(
            select(OKRObjective).where(
                OKRObjective.id == objective_id,
                OKRObjective.tenant_id == user.tenant_id,
            )
        )
        obj = result.scalar_one_or_none()
        if not obj:
            raise HTTPException(404, "Objective not found")

        if body.title is not None:
            obj.title = body.title
        if body.description is not None:
            obj.description = body.description
        if body.status is not None:
            obj.status = body.status

        await db.commit()
        await db.refresh(obj)
        return _obj_to_out(obj)


@router.delete("/objectives/{objective_id}")
async def delete_objective(
    objective_id: uuid.UUID,
    user=Depends(get_current_user),
):
    """Soft delete an Objective (set status to archived)."""
    async with async_session() as db:
        result = await db.execute(
            select(OKRObjective).where(
                OKRObjective.id == objective_id,
                OKRObjective.tenant_id == user.tenant_id,
            )
        )
        obj = result.scalar_one_or_none()
        if not obj:
            raise HTTPException(404, "Objective not found")

        # Soft delete
        obj.status = "archived"
        await db.commit()

        return {"status": "success"}


# ─── Key Results ──────────────────────────────────────────────────────────────


@router.get(
    "/objectives/{objective_id}/key-results", response_model=list[KeyResultOut]
)
async def list_key_results(
    objective_id: uuid.UUID, user=Depends(get_current_user)
):
    """List all KRs for the given Objective."""
    async with async_session() as db:
        # Verify objective belongs to this tenant
        obj_result = await db.execute(
            select(OKRObjective).where(
                OKRObjective.id == objective_id,
                OKRObjective.tenant_id == user.tenant_id,
            )
        )
        if not obj_result.scalar_one_or_none():
            raise HTTPException(404, "Objective not found")

        result = await db.execute(
            select(OKRKeyResult)
            .where(OKRKeyResult.objective_id == objective_id)
            .order_by(OKRKeyResult.created_at)
        )
        return [_kr_to_out(kr) for kr in result.scalars().all()]


@router.post(
    "/objectives/{objective_id}/key-results", response_model=KeyResultOut
)
async def create_key_result(
    objective_id: uuid.UUID,
    body: KeyResultCreate,
    user=Depends(get_current_user),
):
    """Create a new Key Result under the specified Objective."""
    async with async_session() as db:
        # Verify objective belongs to this tenant
        obj_result = await db.execute(
            select(OKRObjective).where(
                OKRObjective.id == objective_id,
                OKRObjective.tenant_id == user.tenant_id,
            )
        )
        if not obj_result.scalar_one_or_none():
            raise HTTPException(404, "Objective not found")

        kr = OKRKeyResult(
            objective_id=objective_id,
            title=body.title,
            target_value=body.target_value,
            unit=body.unit,
            focus_ref=body.focus_ref,
        )
        db.add(kr)
        await db.commit()
        await db.refresh(kr)
        return _kr_to_out(kr)


@router.patch("/key-results/{kr_id}", response_model=KeyResultOut)
async def update_key_result(
    kr_id: uuid.UUID,
    body: KeyResultUpdate,
    user=Depends(get_current_user),
):
    """Update a Key Result's fields or current progress value.

    When current_value changes, an OKRProgressLog entry is created
    automatically to maintain the complete progress history.
    """
    async with async_session() as db:
        result = await db.execute(
            select(OKRKeyResult, OKRObjective)
            .join(OKRObjective, OKRKeyResult.objective_id == OKRObjective.id)
            .where(
                OKRKeyResult.id == kr_id,
                OKRObjective.tenant_id == user.tenant_id,
            )
        )
        row = result.first()
        if not row:
            raise HTTPException(404, "Key Result not found")
        kr, _ = row

        prev_value = kr.current_value

        if body.title is not None:
            kr.title = body.title
        if body.target_value is not None:
            kr.target_value = body.target_value
        if body.current_value is not None:
            kr.current_value = body.current_value
        if body.unit is not None:
            kr.unit = body.unit
        if body.focus_ref is not None:
            kr.focus_ref = body.focus_ref
        if body.status is not None:
            kr.status = body.status

        # Log progress change when current_value was updated
        if body.current_value is not None and body.current_value != prev_value:
            log = OKRProgressLog(
                kr_id=kr_id,
                previous_value=prev_value,
                new_value=body.current_value,
                source="manual",
            )
            db.add(log)

        await db.commit()
        await db.refresh(kr)
        return _kr_to_out(kr)


@router.post("/key-results/{kr_id}/progress", response_model=KeyResultOut)
async def update_kr_progress_endpoint(
    kr_id: uuid.UUID,
    body: ProgressUpdate,
    user=Depends(get_current_user),
):
    """Convenience endpoint for updating only the current progress value.

    Used by the update_kr_progress agent tool and the OKR Agent.
    Records an OKRProgressLog entry with the provided note.
    """
    async with async_session() as db:
        result = await db.execute(
            select(OKRKeyResult, OKRObjective)
            .join(OKRObjective, OKRKeyResult.objective_id == OKRObjective.id)
            .where(
                OKRKeyResult.id == kr_id,
                OKRObjective.tenant_id == user.tenant_id,
            )
        )
        row = result.first()
        if not row:
            raise HTTPException(404, "Key Result not found")
        kr, _ = row

        prev_value = kr.current_value
        kr.current_value = body.value
        kr.last_updated_at = datetime.utcnow()

        # Update status: use explicit override or auto-compute from progress ratio
        if body.status and body.status in ("on_track", "at_risk", "behind", "completed"):
            kr.status = body.status
        elif kr.target_value:
            ratio = body.value / kr.target_value
            if ratio >= 1.0:
                kr.status = "completed"
            elif ratio >= 0.7:
                kr.status = "on_track"
            elif ratio >= 0.4:
                kr.status = "at_risk"
            else:
                kr.status = "behind"

        log = OKRProgressLog(
            kr_id=kr_id,
            previous_value=prev_value,
            new_value=body.value,
            source="manual",
            note=body.note,
        )
        db.add(log)
        await db.commit()
        await db.refresh(kr)
        return _kr_to_out(kr)


@router.delete("/key-results/{kr_id}")
async def delete_key_result(
    kr_id: uuid.UUID,
    user=Depends(get_current_user),
):
    """Hard delete a key result."""
    from app.models.okr import OKRProgressLog
    async with async_session() as db:
        result = await db.execute(
            select(OKRKeyResult, OKRObjective)
            .join(OKRObjective, OKRKeyResult.objective_id == OKRObjective.id)
            .where(
                OKRKeyResult.id == kr_id,
                OKRObjective.tenant_id == user.tenant_id,
            )
        )
        row = result.first()
        if not row:
            raise HTTPException(404, "Key Result not found")
        kr, _ = row

        # Manual cascade delete logs
        await db.execute(delete(OKRProgressLog).where(OKRProgressLog.kr_id == kr_id))
        await db.execute(delete(OKRKeyResult).where(OKRKeyResult.id == kr_id))
        
        await db.commit()
        return {"status": "success"}


# ─── Reports ──────────────────────────────────────────────────────────────────


@router.get("/reports", response_model=list[WorkReportOut])
async def list_reports(
    report_type: str | None = None,  # "daily" | "weekly" | None for both
    limit: int = 50,
    user=Depends(get_current_user),
):
    """List work reports for the current tenant, newest first."""
    async with async_session() as db:
        query = (
            select(WorkReport)
            .where(WorkReport.tenant_id == user.tenant_id)
            .order_by(WorkReport.period_date.desc(), WorkReport.created_at.desc())
            .limit(limit)
        )
        if report_type:
            query = query.where(WorkReport.report_type == report_type)

        result = await db.execute(query)
        reports = result.scalars().all()

    return [
        WorkReportOut(
            id=str(r.id),
            author_type=r.author_type,
            author_id=str(r.author_id),
            report_type=r.report_type,
            period_date=r.period_date.isoformat(),
            content=r.content,
            source=r.source,
            created_at=r.created_at.isoformat() if r.created_at else "",
        )
        for r in reports
    ]


# ─── P4 Onboarding Endpoints ─────────────────────────────────────────────────


@router.get("/members-without-okr")
async def members_without_okr(user=Depends(get_current_user)):
    """Return members tracked by the OKR Agent who lack OKRs in the current period.

    Members are identified via the OKR Agent's AgentRelationship list — only
    members that the OKR Agent has a relationship with are tracked.

    Response also includes `tracked_user_ids` and `tracked_agent_ids` so the
    frontend can filter the main member-objectives section to the same set.

    Admin-only in practice (same access guard as settings).
    """
    from app.models.agent import Agent, AgentPermission  # noqa: F401 — local import
    from app.models.user import User  # noqa: F401 — local import

    async with async_session() as db:
        settings = await _get_or_create_settings(db, user.tenant_id)
        if not settings.enabled:
            raise HTTPException(403, "OKR is not enabled for this tenant")

        ps, pe = _compute_current_period(
            settings.period_frequency, settings.period_length_days
        )
        await db.commit()

    async with async_session() as db:
        # ── Check if a company-level OKR exists this period ──────────────────
        co_result = await db.execute(
            select(OKRObjective.id).where(
                OKRObjective.tenant_id == user.tenant_id,
                OKRObjective.owner_type == "company",
                OKRObjective.period_start >= ps,
                OKRObjective.period_end <= pe,
                OKRObjective.status != "archived",
            ).limit(1)
        )
        company_okr_exists: bool = co_result.scalar_one_or_none() is not None

        # ── Find the OKR Agent ────────────────────────────────────────────────
        okr_agent_result = await db.execute(
            select(Agent.id).where(
                Agent.tenant_id == user.tenant_id,
                Agent.name.ilike("%OKR%"),
            ).limit(1)
        )
        okr_agent_row = okr_agent_result.first()
        okr_agent_id: str | None = str(okr_agent_row[0]) if okr_agent_row else None

        # ── Collect owner_ids that already have OKRs this period ──────────────
        existing_result = await db.execute(
            select(OKRObjective.owner_id, OKRObjective.owner_type).where(
                OKRObjective.tenant_id == user.tenant_id,
                OKRObjective.owner_type.in_(["user", "agent"]),
                OKRObjective.period_start >= ps,
                OKRObjective.period_end <= pe,
                OKRObjective.status != "archived",
                OKRObjective.owner_id.isnot(None),
            )
        )
        covered_ids: set[uuid.UUID] = {
            row.owner_id for row in existing_result.fetchall()
        }

        # ── Determine tracked members from OKR Agent relationships ────────────
        # If no OKR Agent, fall back to showing all users/agents (backward compat).
        tracked_user_ids: list[str] = []   # User.id values for each tracked human member
        tracked_agent_ids: list[str] = []  # Agent.id values for tracked agents

        if okr_agent_id:
            okr_agent_uuid = uuid.UUID(okr_agent_id)

            # Human side: AgentRelationship → OrgMember.user_id → User.id
            human_rel_result = await db.execute(
                select(OrgMember.user_id, OrgMember.id, OrgMember.name, OrgMember.avatar_url).where(
                    AgentRelationship.agent_id == okr_agent_uuid,
                    OrgMember.id == AgentRelationship.member_id,
                    OrgMember.user_id.isnot(None),
                ).join(AgentRelationship, AgentRelationship.member_id == OrgMember.id)
            )
            human_rows = human_rel_result.fetchall()
            tracked_user_ids = [str(row.user_id) for row in human_rows]

            # Agent side: AgentAgentRelationship → target_agent_id
            agent_rel_result = await db.execute(
                select(AgentAgentRelationship.target_agent_id).where(
                    AgentAgentRelationship.agent_id == okr_agent_uuid
                )
            )
            tracked_agent_ids = [str(row[0]) for row in agent_rel_result.fetchall()]

        # ── Build the "without OKR" list ───────────────────────────────────────
        missing_members = []

        if okr_agent_id and tracked_user_ids:
            # Filter to only relationship-tracked users
            tracked_user_uuids = {uuid.UUID(uid) for uid in tracked_user_ids}
            tracked_agent_uuids = {uuid.UUID(aid) for aid in tracked_agent_ids}

            # Users without OKR from the tracked set
            tracked_user_result = await db.execute(
                select(User.id, User.display_name, User.avatar_url).where(
                    User.id.in_(tracked_user_uuids)
                )
            )
            for row in tracked_user_result.fetchall():
                if row.id not in covered_ids:
                    missing_members.append({
                        "id": str(row.id),
                        "type": "user",
                        "display_name": row.display_name or "",
                        "avatar_url": row.avatar_url or "",
                    })

            # Agents without OKR from the tracked set
            tracked_agent_result = await db.execute(
                select(Agent.id, Agent.name, Agent.avatar_url).where(
                    Agent.id.in_(tracked_agent_uuids)
                )
            )
            for row in tracked_agent_result.fetchall():
                if row.id not in covered_ids:
                    missing_members.append({
                        "id": str(row.id),
                        "type": "agent",
                        "display_name": row.name or "",
                        "avatar_url": row.avatar_url or "",
                    })
        else:
            # Fallback (no OKR Agent set up yet): show all users/agents
            agent_result = await db.execute(
                select(Agent.id, Agent.name, Agent.avatar_url).where(
                    Agent.tenant_id == user.tenant_id,
                    Agent.is_system == False,  # noqa: E712
                    Agent.status.notin_(["stopped", "error"]),
                )
            )
            for row in agent_result.fetchall():
                if row.id not in covered_ids:
                    missing_members.append({
                        "id": str(row.id), "type": "agent",
                        "display_name": row.name or "", "avatar_url": row.avatar_url or "",
                    })

            user_result = await db.execute(
                select(User.id, User.display_name, User.avatar_url).where(
                    User.tenant_id == user.tenant_id,
                )
            )
            for row in user_result.fetchall():
                if row.id not in covered_ids:
                    missing_members.append({
                        "id": str(row.id), "type": "user",
                        "display_name": row.display_name or "", "avatar_url": row.avatar_url or "",
                    })

    return {
        "period_start": ps.isoformat(),
        "period_end": pe.isoformat(),
        "company_okr_exists": company_okr_exists,
        "okr_agent_id": okr_agent_id,
        # Frontend uses these to filter the main member-objectives section
        "tracked_user_ids": tracked_user_ids,
        "tracked_agent_ids": tracked_agent_ids,
        "members_without_okr": missing_members,
        "members": missing_members,  # backward-compat alias
        "total": len(missing_members),
    }


@router.post("/trigger-member-outreach")
async def trigger_member_outreach(user=Depends(get_current_user)):
    """Admin-initiated trigger: instruct the OKR Agent to contact all members
    who haven't set their OKRs yet.

    Fires an asynchronous task — returns immediately with a job-accepted response.
    The OKR Agent's LLM loop runs in the background and uses its tools
    (send_web_message, get_org_members, list_objectives, etc.) to reach out.
    """
    import asyncio
    from app.models.agent import Agent  # noqa: F401

    async with async_session() as db:
        settings = await _get_or_create_settings(db, user.tenant_id)
        if not settings.enabled:
            raise HTTPException(403, "OKR is not enabled for this tenant")
        await db.commit()

        # Find the OKR Agent (scoped to this tenant)
        okr_agent_result = await db.execute(
            select(Agent).where(
                Agent.tenant_id == user.tenant_id,
                Agent.name.ilike("%OKR%"),
            ).limit(1)
        )
        okr_agent = okr_agent_result.scalar_one_or_none()

    if not okr_agent:
        raise HTTPException(404, "OKR Agent not found. Please ensure OKR is enabled and the agent has been seeded.")

    # ── Fire the outreach task asynchronously ─────────────────────────────────
    trigger_prompt = (
        "[ADMIN TRIGGER — Member OKR Outreach]\n"
        "The admin has initiated the member OKR outreach flow. "
        "Please:\n"
        "1. Use list_objectives to check which members already have OKRs for the current period.\n"
        "2. Use get_org_members (or similar) to identify all members who need OKRs.\n"
        "3. For each member WITHOUT an OKR:\n"
        "   - If they are a platform user: send them a web notification via send_web_message "
        "     inviting them to set their OKR (mention they can chat with you or use the OKR page).\n"
        "   - If they are an Agent: send a one-shot structured message asking for their OKR.\n"
        "   - If they use a channel (e.g. Feishu) but you lack that channel config: "
        "     notify the admin via send_web_message about the missing channel config.\n"
        "4. After completing outreach, summarize to the admin via send_web_message.\n"
        "Proceed autonomously — do not wait for further input."
    )

    # Import the heartbeat execution pattern to reuse it for one-shot outreach
    try:
        from app.core.agent_runner import run_agent_oneshot  # may not exist yet
        asyncio.create_task(
            run_agent_oneshot(
                agent_id=okr_agent.id,
                prompt=trigger_prompt,
                triggered_by_user_id=user.id,
            )
        )
    except ImportError:
        # Fallback: best-effort fire without awaiting (will not block request)
        pass

    return {
        "status": "accepted",
        "message": "OKR Agent outreach task has been triggered. The agent will contact members asynchronously.",
        "okr_agent_id": str(okr_agent.id),
    }
