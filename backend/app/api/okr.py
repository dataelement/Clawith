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
"""

import uuid
from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

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
    """Update OKR configuration. Org admins only."""
    if not getattr(user, "is_admin", False):
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


def _obj_to_out(obj: OKRObjective, krs: list[OKRKeyResult] | None = None) -> ObjectiveOut:
    return ObjectiveOut(
        id=str(obj.id),
        title=obj.title,
        description=obj.description,
        owner_type=obj.owner_type,
        owner_id=str(obj.owner_id) if obj.owner_id else None,
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

        return [_obj_to_out(o, krs_by_obj.get(o.id, [])) for o in objectives]


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

        log = OKRProgressLog(
            kr_id=kr_id,
            previous_value=prev_value,
            new_value=body.value,
            source="self_report",
            note=body.note,
        )
        db.add(log)
        await db.commit()
        await db.refresh(kr)
        return _kr_to_out(kr)


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
