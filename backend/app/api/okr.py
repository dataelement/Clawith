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

router = APIRouter(prefix="/api/okr", tags=["okr"])


# ─── Helpers ─────────────────────────────────────────────────────────────────


async def _sync_okr_agent_relationships(db, tenant_id: uuid.UUID, okr_agent_id: uuid.UUID) -> None:
    """Auto-connect the OKR Agent to all active org members and company-visible agents.

    Idempotent — clears existing relationships first for a clean re-sync.
    Rules:
      - Human relationships : every active OrgMember in this tenant
      - Agent relationships : every non-system, non-stopped agent in this tenant
                              (excludes the OKR Agent itself)
    """
    from app.models.agent import Agent
    from app.models.org import AgentRelationship, AgentAgentRelationship, OrgMember
    from sqlalchemy import delete as sa_delete

    # 1. Clear existing relationships (clean-slate re-sync)
    await db.execute(sa_delete(AgentRelationship).where(AgentRelationship.agent_id == okr_agent_id))
    await db.execute(sa_delete(AgentAgentRelationship).where(AgentAgentRelationship.agent_id == okr_agent_id))

    # 2. Link all active org members as team_member relationships
    member_result = await db.execute(
        select(OrgMember.id).where(
            OrgMember.tenant_id == tenant_id,
            OrgMember.status == "active",
        )
    )
    for (member_id,) in member_result.fetchall():
        db.add(AgentRelationship(
            agent_id=okr_agent_id,
            member_id=member_id,
            relation="team_member",
            description="OKR tracking — auto-linked via Sync Relationships",
        ))

    # 3. Link all company-visible non-system agents as collaborators
    agent_result = await db.execute(
        select(Agent.id).where(
            Agent.tenant_id == tenant_id,
            Agent.id != okr_agent_id,
            Agent.is_system == False,  # noqa: E712
            Agent.status.notin_(["stopped", "error"]),
        )
    )
    for (agent_id,) in agent_result.fetchall():
        db.add(AgentAgentRelationship(
            agent_id=okr_agent_id,
            target_agent_id=agent_id,
            relation="collaborator",
        ))

    # 4. Regenerate the OKR Agent's relationships file (best-effort)
    try:
        from app.api.relationships import _regenerate_relationships_file
        await _regenerate_relationships_file(db, okr_agent_id)
    except Exception:
        pass  # non-critical; agent picks it up on next heartbeat


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
    # OKR Agent UUID for the chat-link button in the UI
    okr_agent_id: str | None = None


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
    # Resolved human-readable name of the owner (user display_name / agent name).
    # None for company-level objectives.
    owner_name: str | None = None
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
    from app.models.agent import Agent

    async with async_session() as db:
        settings = await _get_or_create_settings(db, user.tenant_id)

        # Also resolve the OKR Agent ID so the UI can show the chat button
        okr_agent_result = await db.execute(
            select(Agent.id).where(
                Agent.tenant_id == user.tenant_id,
                Agent.name.ilike("%OKR%"),
            ).order_by(Agent.is_system.desc()).limit(1)
        )
        okr_agent_row = okr_agent_result.first()
        okr_agent_id_str = str(okr_agent_row[0]) if okr_agent_row else None

        await db.commit()
        return OKRSettingsOut(
            enabled=settings.enabled,
            daily_report_enabled=settings.daily_report_enabled,
            daily_report_time=settings.daily_report_time,
            weekly_report_enabled=settings.weekly_report_enabled,
            weekly_report_day=settings.weekly_report_day,
            period_frequency=settings.period_frequency,
            period_length_days=settings.period_length_days,
            okr_agent_id=okr_agent_id_str,
        )


@router.put("/settings", response_model=OKRSettingsOut)
async def update_okr_settings(body: OKRSettingsUpdate, user=Depends(get_current_user)):
    """Update OKR configuration. Org admins only."""
    # Allow org admins and platform admins to modify OKR settings.
    # user.role is the canonical authority; is_admin is not a real field.
    if getattr(user, "role", None) not in ("org_admin", "platform_admin"):
        raise HTTPException(403, "Only org admins can modify OKR settings")

    async with async_session() as db:
        settings = await _get_or_create_settings(db, user.tenant_id)

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
        return OKRSettingsOut(
            enabled=settings.enabled,
            daily_report_enabled=settings.daily_report_enabled,
            daily_report_time=settings.daily_report_time,
            weekly_report_enabled=settings.weekly_report_enabled,
            weekly_report_day=settings.weekly_report_day,
            period_frequency=settings.period_frequency,
            period_length_days=settings.period_length_days,
        )


# ─── Sync Relationships ───────────────────────────────────────────────────────


@router.post("/sync-relationships")
async def sync_okr_relationships(user=Depends(get_current_user)):
    """Manually re-sync the OKR Agent's relationship network.

    Connects the OKR Agent to all active OrgMembers (org-structure-synced humans)
    and all company-visible agents in this tenant. Idempotent — safe to call
    multiple times; existing relationships are replaced.

    Org admins and platform admins only.
    """
    if getattr(user, "role", None) not in ("org_admin", "platform_admin"):
        raise HTTPException(403, "Only org admins can sync OKR relationships")

    from app.models.agent import Agent

    async with async_session() as db:
        # Locate the OKR Agent (lenient lookup — no is_system requirement)
        result = await db.execute(
            select(Agent.id).where(
                Agent.tenant_id == user.tenant_id,
                Agent.name.ilike("%OKR%"),
            ).order_by(Agent.is_system.desc()).limit(1)
        )
        row = result.first()
        if not row:
            raise HTTPException(404, "OKR Agent not found for this tenant. Enable OKR in Company Settings first.")
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
    owner_name: str | None = None,
) -> ObjectiveOut:
    return ObjectiveOut(
        id=str(obj.id),
        title=obj.title,
        description=obj.description,
        owner_type=obj.owner_type,
        owner_id=str(obj.owner_id) if obj.owner_id else None,
        owner_name=owner_name,
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
    Includes owner_name resolved from User.display_name or Agent.name.
    """
    from app.models.agent import Agent
    from app.models.user import User

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

        # Batch-resolve owner names: collect distinct owner IDs across all types
        all_owner_ids = [str(o.owner_id) for o in objectives if o.owner_id]
        user_owner_ids = [o.owner_id for o in objectives if o.owner_type == "user" and o.owner_id]
        agent_owner_ids = [o.owner_id for o in objectives if o.owner_type == "agent" and o.owner_id]

        # Normalize UUID keys to str to avoid type mismatch between uuid.UUID
        # (from ORM) and str (from asyncpg raw row access).
        user_names: dict[str, str | None] = {}
        if user_owner_ids:
            u_result = await db.execute(
                select(User.id, User.display_name).where(User.id.in_(user_owner_ids))
            )
            user_names = {
                str(row[0]): (row[1] if row[1] else None)
                for row in u_result.fetchall()
            }

        agent_names: dict[str, str | None] = {}
        if agent_owner_ids:
            a_result = await db.execute(
                select(Agent.id, Agent.name).where(Agent.id.in_(agent_owner_ids))
            )
            agent_names = {
                str(row[0]): (row[1] if row[1] else None)
                for row in a_result.fetchall()
            }

        # OrgMember fallback: Feishu/channel users whose owner_id is OrgMember.id
        # rather than User.id (unlinked channel-only members).
        from app.models.org import OrgMember
        org_member_names: dict[str, str | None] = {}
        if all_owner_ids:
            om_result = await db.execute(
                select(OrgMember.id, OrgMember.name)
                .where(OrgMember.id.in_([uuid.UUID(k) for k in all_owner_ids]))
            )
            org_member_names = {
                str(row[0]): (row[1] if row[1] else None)
                for row in om_result.fetchall()
            }

        def _resolve_name(obj: OKRObjective) -> str | None:
            if not obj.owner_id:
                return None
            key = str(obj.owner_id)
            if obj.owner_type == "user":
                # Try User table first; fall back to OrgMember (Feishu-only member)
                return user_names.get(key) or org_member_names.get(key)
            if obj.owner_type == "agent":
                # Try Agent table; defensive fallback to user/org member in case
                # owner_type was miscategorised at creation time
                return agent_names.get(key) or user_names.get(key) or org_member_names.get(key)
            return user_names.get(key) or agent_names.get(key) or org_member_names.get(key)

        return [
            _obj_to_out(o, krs_by_obj.get(o.id, []), owner_name=_resolve_name(o))
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


# ─── P4 Onboarding Endpoints ──────────────────────────────────────────────────


@router.get("/members-without-okr")
async def members_without_okr(user=Depends(get_current_user)):
    """Return tracked members (those in OKR Agent's relationship list) who lack
    OKRs in the current period.  Also returns:
    - okr_agent_id        : UUID of the OKR Agent for the chat-link button
    - company_okr_exists  : bool — whether a company-level objective exists
    - tracked_user_ids    : UUIDs of all tracked platform users (for UI filtering)
    - tracked_agent_ids   : UUIDs of all tracked agents (for UI filtering)
    """
    from app.models.agent import Agent
    from app.models.org import AgentRelationship, AgentAgentRelationship, OrgMember
    from app.models.user import User

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

        # ── Collect owner_ids that already have OKRs this period ──────────────
        existing_result = await db.execute(
            select(OKRObjective.owner_id).where(
                OKRObjective.tenant_id == user.tenant_id,
                OKRObjective.owner_type.in_(["user", "agent"]),
                OKRObjective.period_start >= ps,
                OKRObjective.period_end <= pe,
                OKRObjective.status != "archived",
                OKRObjective.owner_id.isnot(None),
            )
        )
        covered_ids: set[uuid.UUID] = {row[0] for row in existing_result.fetchall()}

        # ── Locate the OKR Agent (lenient: prefer is_system, fallback to any) ─
        okr_agent_result = await db.execute(
            select(Agent.id).where(
                Agent.tenant_id == user.tenant_id,
                Agent.name.ilike("%OKR%"),
            ).order_by(Agent.is_system.desc()).limit(1)
        )
        okr_agent_row = okr_agent_result.first()
        okr_agent_id_val: uuid.UUID | None = okr_agent_row[0] if okr_agent_row else None
        okr_agent_id_str: str | None = str(okr_agent_id_val) if okr_agent_id_val else None

        # ── Fetch tracked members from OKR Agent's relationship list ──────────
        tracked_user_ids: list[str] = []
        tracked_agent_ids: list[str] = []
        members_without_okr: list[dict] = []

        if okr_agent_id_val:
            # Human members via AgentRelationship → OrgMember
            human_rel_result = await db.execute(
                select(OrgMember.id, OrgMember.name, OrgMember.user_id, OrgMember.avatar_url)
                .join(AgentRelationship, AgentRelationship.member_id == OrgMember.id)
                .where(
                    AgentRelationship.agent_id == okr_agent_id_val,
                    OrgMember.status == "active",
                )
            )
            for row in human_rel_result.fetchall():
                if row.user_id:
                    tracked_user_ids.append(str(row.user_id))
                if row.user_id not in covered_ids:
                    members_without_okr.append({
                        "id": str(row.id),
                        "type": "user",
                        "display_name": row.name or "",
                        "avatar_url": row.avatar_url or "",
                        "channel": None,
                        "channel_user_id": None,
                    })

            # Agent members via AgentAgentRelationship
            agent_rel_result = await db.execute(
                select(Agent.id, Agent.name, Agent.avatar_url)
                .join(AgentAgentRelationship, AgentAgentRelationship.target_agent_id == Agent.id)
                .where(
                    AgentAgentRelationship.agent_id == okr_agent_id_val,
                    Agent.is_system == False,  # noqa: E712
                    Agent.status.notin_(["stopped", "error"]),
                )
            )
            for row in agent_rel_result.fetchall():
                tracked_agent_ids.append(str(row.id))
                if row.id not in covered_ids:
                    members_without_okr.append({
                        "id": str(row.id),
                        "type": "agent",
                        "display_name": row.name or "",
                        "avatar_url": row.avatar_url or "",
                        "channel": None,
                        "channel_user_id": None,
                    })

        # Fallback: OKR Agent not seeded, OR no relationships yet (sync not done)
        # In either case show ALL members so the panel is useful before first sync.
        if not okr_agent_id_val or (not tracked_user_ids and not tracked_agent_ids):
            agent_result = await db.execute(
                select(Agent.id, Agent.name, Agent.avatar_url).where(
                    Agent.tenant_id == user.tenant_id,
                    Agent.is_system == False,  # noqa: E712
                    Agent.status.notin_(["stopped", "error"]),
                )
            )
            for row in agent_result.fetchall():
                tracked_agent_ids.append(str(row.id))
                if row.id not in covered_ids:
                    members_without_okr.append({
                        "id": str(row.id), "type": "agent",
                        "display_name": row.name or "",
                        "avatar_url": row.avatar_url or "",
                        "channel": None, "channel_user_id": None,
                    })

            user_result = await db.execute(
                select(User.id, User.display_name, User.avatar_url).where(
                    User.tenant_id == user.tenant_id,
                )
            )
            for row in user_result.fetchall():
                tracked_user_ids.append(str(row.id))
                if row.id not in covered_ids:
                    members_without_okr.append({
                        "id": str(row.id), "type": "user",
                        "display_name": row.display_name or "",
                        "avatar_url": row.avatar_url or "",
                        "channel": None, "channel_user_id": None,
                    })

    return {
        "period_start": ps.isoformat(),
        "period_end": pe.isoformat(),
        "company_okr_exists": company_okr_exists,
        "okr_agent_id": okr_agent_id_str,
        "members_without_okr": members_without_okr,
        "tracked_user_ids": tracked_user_ids,
        "tracked_agent_ids": tracked_agent_ids,
        "total": len(members_without_okr),
    }


@router.post("/trigger-member-outreach")
async def trigger_member_outreach(user=Depends(get_current_user)):
    """Admin-initiated trigger: instruct the OKR Agent to contact all tracked
    members who haven't set their OKRs for the current period.

    Data flow:
      1. Backend queries tracked members (from AgentRelationship) who lack OKRs.
      2. Backend injects up to 3 recent chat messages per member as context.
      3. Builds a structured prompt and fires run_agent_oneshot as a background task.
      4. The OKR Agent LLM loop sends personalised messages via the correct channel,
         then reports success/failure back to the triggering admin.

    Returns immediately with status=accepted.
    """
    import asyncio
    from sqlalchemy import or_
    from app.models.agent import Agent
    from app.models.org import AgentRelationship, AgentAgentRelationship, OrgMember
    from app.models.audit import ChatMessage
    from app.models.chat_session import ChatSession
    from app.models.user import User

    async with async_session() as db:
        settings = await _get_or_create_settings(db, user.tenant_id)
        if not settings.enabled:
            raise HTTPException(403, "OKR is not enabled for this tenant")

        ps, pe = _compute_current_period(settings.period_frequency, settings.period_length_days)

        # ── Find the OKR Agent (lenient — no is_system requirement) ──────────
        okr_agent_result = await db.execute(
            select(Agent).where(
                Agent.tenant_id == user.tenant_id,
                Agent.name.ilike("%OKR%"),
            ).order_by(Agent.is_system.desc()).limit(1)
        )
        okr_agent = okr_agent_result.scalar_one_or_none()
        if not okr_agent:
            raise HTTPException(
                404,
                "OKR Agent not found. Please ensure OKR is enabled and the agent has been seeded.",
            )

        # ── Collect owner_ids that already have OKRs this period ─────────────
        existing_result = await db.execute(
            select(OKRObjective.owner_id).where(
                OKRObjective.tenant_id == user.tenant_id,
                OKRObjective.owner_type.in_(["user", "agent"]),
                OKRObjective.period_start >= ps,
                OKRObjective.period_end <= pe,
                OKRObjective.status != "archived",
                OKRObjective.owner_id.isnot(None),
            )
        )
        covered_ids: set[uuid.UUID] = {row[0] for row in existing_result.fetchall()}

        # ── Fetch tracked human members from AgentRelationship ────────────────
        rel_result = await db.execute(
            select(AgentRelationship, OrgMember)
            .join(OrgMember, AgentRelationship.member_id == OrgMember.id)
            .where(
                AgentRelationship.agent_id == okr_agent.id,
                OrgMember.status == "active",
            )
        )
        rel_rows = rel_result.all()

        # ── Fetch tracked agent members from AgentAgentRelationship ──────────
        agent_rel_result = await db.execute(
            select(Agent).join(
                AgentAgentRelationship,
                AgentAgentRelationship.target_agent_id == Agent.id,
            ).where(
                AgentAgentRelationship.agent_id == okr_agent.id,
                Agent.is_system == False,  # noqa: E712
                Agent.status.notin_(["stopped", "error"]),
            )
        )
        tracked_agents = agent_rel_result.scalars().all()

        # ── Resolve platform user for each OrgMember (for web fallback display)
        member_user_ids: dict[uuid.UUID, uuid.UUID | None] = {}  # org_member.id → user.id
        for _, org_member in rel_rows:
            member_user_ids[org_member.id] = org_member.user_id

            # Level 2: if OrgMember.user_id is null, try chat_sessions by external_conv_id
            if not org_member.user_id:
                patterns = []
                if org_member.open_id:
                    patterns.append(f"feishu_p2p_{org_member.open_id}")
                if org_member.external_id:
                    patterns.append(f"feishu_p2p_{org_member.external_id}")
                    patterns.append(f"dingtalk_p2p_{org_member.external_id}")
                if patterns:
                    sess_result = await db.execute(
                        select(ChatSession.user_id).where(
                            ChatSession.agent_id == okr_agent.id,
                            or_(*[ChatSession.external_conv_id == p for p in patterns]),
                        ).limit(1)
                    )
                    found = sess_result.scalar_one_or_none()
                    if found:
                        member_user_ids[org_member.id] = found

        # ── Fetch recent 3 messages per member (for context) ─────────────────
        async def _recent_msgs(target_user_id: uuid.UUID | None) -> list[tuple]:
            """Return up to 3 recent chat_messages between OKR Agent and user."""
            if not target_user_id:
                return []
            msgs_result = await db.execute(
                select(ChatMessage.role, ChatMessage.content, ChatMessage.created_at)
                .where(
                    ChatMessage.agent_id == okr_agent.id,
                    ChatMessage.user_id == target_user_id,
                )
                .order_by(ChatMessage.created_at.desc())
                .limit(3)
            )
            return list(reversed(msgs_result.all()))  # chronological order

        # ── Build prompt context for each member without OKR ─────────────────
        # Also resolve admin username for the final summary message
        admin_result = await db.execute(
            select(User.display_name).where(User.id == user.id)
        )
        admin_row = admin_result.first()
        admin_username = (admin_row.display_name if admin_row else None) or str(user.id)

        await db.commit()

    # ── Assemble the list of members to contact ───────────────────────────────
    # (DB session is closed — all data fetched above)
    members_to_contact: list[str] = []
    index = 1

    for _, org_member in rel_rows:
        # Skip if they already have an OKR this period
        # (owner_id for human members is their platform user_id)
        platform_uid = member_user_ids.get(org_member.id)
        if platform_uid and platform_uid in covered_ids:
            continue

        msgs = await _recent_msgs(platform_uid) if platform_uid else []

        # Determine channel hint
        # Priority: any external channel (Feishu / DingTalk / WeChat Work / etc.)
        # takes precedence over the in-platform web notification, so members
        # receive a message in the tool they actually use day-to-day.
        has_channel = bool(org_member.open_id or org_member.external_id)
        if has_channel:
            channel_hint = f'send_channel_message(member_name="{org_member.name}", message=...)'
        elif platform_uid:
            channel_hint = (
                'send_web_message(username="<their_username>", message=...)'
            )
        else:
            channel_hint = "No channel available — note this in your summary"

        # Format history
        if msgs:
            history_lines = []
            for role, content, created_at in msgs:
                ts = created_at.strftime("%m-%d %H:%M") if created_at else ""
                speaker = "You" if role == "assistant" else org_member.name
                history_lines.append(f"  [{ts}] {speaker}: {content[:120]}")
            history_str = "\n".join(history_lines)
        else:
            history_str = "  (No previous conversation — treat this as first contact)"

        # Look up username for platform users
        username_hint = ""
        if platform_uid:
            async with async_session() as db2:
                u_res = await db2.execute(
                    select(User.display_name).where(User.id == platform_uid)
                )
                u_row = u_res.first()
            if u_row and u_row.display_name:
                username_hint = (
                    f'\n  Platform account: "{u_row.display_name}"'
                    f"  (use this as the recipient identifier in send_web_message)"
                )

        member_block = (
            f"--- Member {index}: {org_member.name} ---\n"
            f"  Type: Channel member{username_hint}\n"
            f"  How to send: {channel_hint}\n"
            f"  Recent chat history (last 3 messages):\n"
            f"{history_str}"
        )
        members_to_contact.append(member_block)
        index += 1

    for agent_member in tracked_agents:
        if agent_member.id in covered_ids:
            continue
        # Embed the actual create_objective call template with the real UUID so the LLM
        # cannot accidentally substitute a placeholder or nil UUID.
        member_block = (
            f"--- Member {index}: {agent_member.name} [Agent] ---\n"
            f"  STEP 1 → send_message_to_agent(agent_name=\"{agent_member.name}\", "
            f"message=\"[OKR Agent] 请描述您在本周期的主要目标（Objective）和关键结果（Key Results）。\")\n"
            f"  STEP 2 → Read the reply carefully from the tool result.\n"
            f"  STEP 3 → Call this EXACTLY (use the UUID below verbatim):\n"
            f"    create_objective(title=\"<their objective>\", owner_type=\"agent\", "
            f"owner_id=\"{agent_member.id}\", "
            f"period_start=\"{ps.isoformat()}\", period_end=\"{pe.isoformat()}\")\n"
            f"  STEP 4 → For each KR they mentioned:\n"
            f"    create_key_result(objective_id=\"<id from STEP 3 result>\", "
            f"title=\"<KR title>\", target_value=<number>, unit=\"<unit if stated>\")"
        )
        members_to_contact.append(member_block)
        index += 1

    if not members_to_contact:
        return {
            "status": "no_action",
            "message": "All tracked members already have OKRs set for this period. No outreach needed.",
            "okr_agent_id": str(okr_agent.id),
        }

    # ── Compose the final task prompt ────────────────────────────────────────────────────
    period_label = f"{ps.strftime('%Y-%m-%d')} to {pe.strftime('%Y-%m-%d')}"
    members_block = "\n\n".join(members_to_contact)
    task_prompt = f"""[ADMIN TRIGGER — OKR Member Outreach — ONE-SHOT TASK]

Current OKR period: {period_label}
  period_start = "{ps.isoformat()}"  (use this exact value in create_objective)
  period_end   = "{pe.isoformat()}"  (use this exact value in create_objective)
Admin who triggered this: {admin_username}

Your task: Contact the {len(members_to_contact)} member(s) below who have NOT set their OKRs \
for this period. For Agent members, also RECORD their OKR after they reply.

━━━ TOOL RULES (MANDATORY — DO NOT DEVIATE) ━━━
• For members tagged [Agent]:
  → Step 1: Send an OKR request:
      send_message_to_agent(agent_name="<name>", message="[OKR Agent] 请描述您在 {period_label} 期间的主要目标（Objective）和关键结果（Key Results）。")
  → Step 2: Their reply is returned as the tool result. READ it carefully.
  → Step 3: Extract their main Objective. Call:
      create_objective(title="<extracted objective title>", owner_type="agent",
                       owner_id="<UUID from member block>",
                       period_start="{ps.isoformat()}", period_end="{pe.isoformat()}")
  → Step 4: For each Key Result they mentioned, call:
      create_key_result(objective_id="<id returned in step 3>", title="<KR title>",
                        target_value=<number>, unit="<unit if stated>")
  → Move to the next member. Do NOT continue conversing.

• For human members:
  → If they have a channel (Feishu/DingTalk) shown: send_channel_message(member_name="<name>", message="...")
  → If they have a Platform account only: send_web_message(username="<their display_name>", message="...")
  → If neither: skip and note in summary.
  → Human messages are fire-and-forget: their OKR will be captured at a later time.
  → Do NOT wait for or respond to human replies during this task.

━━━ STEP-BY-STEP ━━━
1. For each member in sequence:
   • [Agent] → send_message_to_agent → read the returned reply → create_objective → create_key_result(s)
   • [Human] → send_channel_message or send_web_message (fire-and-forget, do not wait for reply)
2. If any step fails: log the failure and continue to the next member.
3. After ALL members are processed:
   send_web_message(username="{admin_username}", message="Outreach complete. Contacted X member(s), recorded Y agent OKR(s). Failures: [list or 'none'].")
4. STOP. Your job is done.
"""

    # ── Launch background task ────────────────────────────────────────────────
    from app.services.heartbeat import run_agent_oneshot

    # Rounds per member:
    #   Agent: 1 (A2A send/receive) + 1 (create_objective) + up to 3 (create_key_results) = ~5
    #   Human: 1 (channel/web send)
    # Plus 3 for summary + admin message + buffer.
    safe_max_rounds = len(members_to_contact) * 5 + 3

    asyncio.create_task(
        run_agent_oneshot(
            agent_id=okr_agent.id,
            prompt=task_prompt,
            triggered_by_user_id=user.id,
            max_rounds=safe_max_rounds,
        )
    )

    return {
        "status": "accepted",
        "message": (
            f"OKR Agent outreach task triggered for {len(members_to_contact)} member(s). "
            "The agent will contact them asynchronously and report back to you."
        ),
        "okr_agent_id": str(okr_agent.id),
        "members_count": len(members_to_contact),
    }
