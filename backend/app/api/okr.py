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
from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from loguru import logger
from pydantic import BaseModel
from sqlalchemy import select, delete

from app.api.auth import get_current_user
from app.database import async_session
from app.models.identity import IdentityProvider
from app.models.okr import (
    CompanyReport,
    MemberDailyReport,
    OKRAlignment,
    OKRKeyResult,
    OKRObjective,
    OKRProgressLog,
    OKRSettings,
    WorkReport,
)

router = APIRouter(prefix="/api/okr", tags=["okr"])


# ─── Helpers ─────────────────────────────────────────────────────────────────


def _is_okr_admin(user) -> bool:
    return getattr(user, "role", None) in ("org_admin", "platform_admin")


def _dashboard_write_forbidden() -> HTTPException:
    return HTTPException(
        403,
        "Only org admins can modify OKRs in the dashboard. Members should use OKR Agent to manage their own OKRs.",
    )


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

    # 3. Link all company-visible non-system agents as collaborators.
    agent_result = await db.execute(
        select(Agent.id).where(
            Agent.tenant_id == tenant_id,
            Agent.id != okr_agent_id,
            Agent.is_system == False,  # noqa: E712
            Agent.status.notin_(["stopped", "error"]),
            Agent.access_mode == "company",
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


async def _sync_okr_report_triggers(db, settings: OKRSettings) -> None:
    """Keep OKR Agent system triggers aligned with tenant report settings."""
    if not settings.okr_agent_id:
        return

    from app.models.trigger import AgentTrigger
    from app.services.focus_service import ensure_focus_item

    system_focus_ref = await ensure_focus_item(
        settings.okr_agent_id,
        focus_ref="system:okr_reports",
        description="OKR 自动汇总、日报收集与周期报告",
        system=True,
    )

    daily_hour, daily_minute = 18, 0
    try:
        daily_hour_str, daily_minute_str = settings.daily_report_time.split(":", 1)
        daily_hour = max(0, min(23, int(daily_hour_str)))
        daily_minute = max(0, min(59, int(daily_minute_str)))
    except Exception:
        logger.warning(f"[OKR] Invalid daily_report_time {settings.daily_report_time}; using 18:00")

    trigger_result = await db.execute(
        select(AgentTrigger).where(
            AgentTrigger.agent_id == settings.okr_agent_id,
            AgentTrigger.name.in_(
                [
                    "daily_okr_collection",
                    "daily_okr_report",
                    "weekly_okr_report",
                    "biweekly_okr_checkin",
                    "monthly_okr_report",
                ]
            ),
        )
    )
    triggers = {trigger.name: trigger for trigger in trigger_result.scalars().all()}

    def _ensure_trigger(name: str, *, config: dict, reason: str, is_enabled: bool) -> AgentTrigger:
        trigger = triggers.get(name)
        if trigger is None:
            trigger = AgentTrigger(
                agent_id=settings.okr_agent_id,
                name=name,
                type="cron",
                config=config,
                reason=reason,
                cooldown_seconds=3600,
                is_system=True,
                focus_ref=system_focus_ref,
                is_enabled=is_enabled,
            )
            db.add(trigger)
            triggers[name] = trigger
            return trigger
        trigger.config = config
        trigger.reason = reason
        trigger.is_enabled = is_enabled
        trigger.focus_ref = trigger.focus_ref or system_focus_ref
        return trigger

    _ensure_trigger(
        "daily_okr_collection",
        config={"expr": f"{daily_minute} {daily_hour} * * *"},
        is_enabled=bool(settings.enabled and settings.daily_report_enabled),
        reason=(
            "System trigger: daily OKR collection. When daily reporting is enabled, "
            "the OKR Agent should collect today's final daily update only from members "
            "and agents already in its relationship list."
        ),
    )

    _ensure_trigger(
        "daily_okr_report",
        config={"expr": "0 9 * * *"},
        is_enabled=bool(settings.enabled),
        reason=(
            "System trigger: generate the company daily report at 09:00 for the previous day."
        ),
    )

    _ensure_trigger(
        "weekly_okr_report",
        config={"expr": "0 9 * * 1"},
        is_enabled=bool(settings.enabled),
        reason=(
            "System trigger: generate the company weekly report at 09:00 every Monday "
            "for the previous week."
        ),
    )

    biweekly = triggers.get("biweekly_okr_checkin")
    if biweekly:
        biweekly.is_enabled = bool(settings.enabled)
        biweekly.reason = (
            "System trigger: fires on the 1st and 15th of every month at 10:00 "
            "to perform the mandatory bi-weekly OKR check-in."
        )

    _ensure_trigger(
        "monthly_okr_report",
        config={"expr": "0 9 1 * *"},
        is_enabled=bool(settings.enabled),
        reason=(
            "System trigger: generate the company monthly report at 09:00 on the 1st "
            "for the previous month."
        ),
    )


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


def _compute_period_for_date(
    frequency: str, length_days: int | None, target: date
) -> tuple[date, date]:
    """Compute the OKR period containing a specific date."""
    if frequency == "monthly":
        start = target.replace(day=1)
        if target.month == 12:
            end = target.replace(month=12, day=31)
        else:
            end = target.replace(month=target.month + 1, day=1) - timedelta(days=1)
    elif frequency == "custom" and length_days:
        epoch = date(1970, 1, 1)
        days_since_epoch = (target - epoch).days
        period_index = days_since_epoch // length_days
        start = epoch + timedelta(days=period_index * length_days)
        end = start + timedelta(days=length_days - 1)
    else:
        quarter = (target.month - 1) // 3 + 1
        start = date(target.year, (quarter - 1) * 3 + 1, 1)
        if quarter == 4:
            end = date(target.year, 12, 31)
        else:
            end = date(target.year, quarter * 3 + 1, 1) - timedelta(days=1)
    return start, end


def _advance_period(
    start: date, frequency: str, length_days: int | None, steps: int = 1
) -> tuple[date, date]:
    """Move a period start forward by a fixed number of OKR periods."""
    if frequency == "monthly":
        month_index = start.year * 12 + (start.month - 1) + steps
        year = month_index // 12
        month = month_index % 12 + 1
        return _compute_period_for_date(frequency, length_days, date(year, month, 1))
    if frequency == "custom" and length_days:
        next_start = start + timedelta(days=length_days * steps)
        return next_start, next_start + timedelta(days=length_days - 1)
    quarter = (start.month - 1) // 3
    quarter_index = start.year * 4 + quarter + steps
    year = quarter_index // 4
    next_quarter = quarter_index % 4 + 1
    return _compute_period_for_date(frequency, length_days, date(year, (next_quarter - 1) * 3 + 1, 1))


# ─── Pydantic schemas ─────────────────────────────────────────────────────────


class OKRSettingsOut(BaseModel):
    enabled: bool
    first_enabled_at: str | None = None
    daily_report_enabled: bool
    daily_report_time: str
    daily_report_skip_non_workdays: bool = True
    weekly_report_enabled: bool
    weekly_report_day: int
    period_frequency: str
    period_length_days: int | None = None
    period_frequency_locked: bool = False
    # OKR Agent UUID for the chat-link button in the UI
    okr_agent_id: str | None = None


class OKRSettingsUpdate(BaseModel):
    enabled: bool | None = None
    daily_report_enabled: bool | None = None
    daily_report_time: str | None = None
    daily_report_skip_non_workdays: bool | None = None
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


class MemberDailyReportOut(BaseModel):
    id: str
    member_type: str
    member_id: str
    display_name: str
    avatar_url: str | None = None
    group_label: str
    report_date: str
    content: str
    status: str
    submitted_at: str | None = None
    updated_at: str | None = None


class MemberDailyReportUpsert(BaseModel):
    report_date: str
    content: str
    member_type: str | None = None
    member_id: str | None = None
    source: str = "manual"


class CompanyReportOut(BaseModel):
    id: str
    report_type: str
    period_start: str
    period_end: str
    period_label: str
    content: str
    submitted_count: int
    missing_count: int
    needs_refresh: bool
    generated_at: str
    updated_at: str


class CompanyReportRegenerate(BaseModel):
    report_type: str
    period_start: str


# ─── Settings ─────────────────────────────────────────────────────────────────


@router.get("/settings", response_model=OKRSettingsOut)
async def get_okr_settings(user=Depends(get_current_user)):
    """Return OKR configuration for the current tenant."""
    async with async_session() as db:
        settings = await _get_or_create_settings(db, user.tenant_id)

        # Also resolve the OKR Agent ID so the UI can show the chat button
        okr_agent_id_str = str(settings.okr_agent_id) if settings.okr_agent_id else None

        await db.commit()
        return OKRSettingsOut(
            enabled=settings.enabled,
            first_enabled_at=settings.first_enabled_at.isoformat() if settings.first_enabled_at else None,
            daily_report_enabled=settings.daily_report_enabled,
            daily_report_time=settings.daily_report_time,
            daily_report_skip_non_workdays=settings.daily_report_skip_non_workdays,
            weekly_report_enabled=False,
            weekly_report_day=0,
            period_frequency=settings.period_frequency,
            period_length_days=settings.period_length_days,
            period_frequency_locked=settings.first_enabled_at is not None,
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
        period_is_locked = settings.first_enabled_at is not None

        if period_is_locked:
            if body.period_frequency is not None and body.period_frequency != settings.period_frequency:
                raise HTTPException(
                    400,
                    "OKR period frequency is locked after OKR is first enabled.",
                )
            if body.period_length_days is not None and body.period_length_days != settings.period_length_days:
                raise HTTPException(
                    400,
                    "OKR period length is locked after OKR is first enabled.",
                )

        if body.enabled is not None:
            settings.enabled = body.enabled
        if body.daily_report_enabled is not None:
            settings.daily_report_enabled = body.daily_report_enabled
        if body.daily_report_time is not None:
            settings.daily_report_time = body.daily_report_time
        if body.daily_report_skip_non_workdays is not None:
            settings.daily_report_skip_non_workdays = body.daily_report_skip_non_workdays
        if body.period_frequency is not None:
            settings.period_frequency = body.period_frequency
        if body.period_length_days is not None:
            settings.period_length_days = body.period_length_days

        # Member reporting is daily-only in the redesigned OKR workflow.
        settings.weekly_report_enabled = False
        settings.weekly_report_day = 0

        if body.enabled is True and settings.first_enabled_at is None:
            settings.first_enabled_at = datetime.now(timezone.utc)

        await _sync_okr_report_triggers(db, settings)
        await db.commit()

        # ── Auto-create OKR Agent when first enabled ──────────────────────────
        # If OKR was just turned on and no agent exists yet for this tenant,
        # seed one so the user doesn't see "OKR Agent not found".
        okr_agent_id_str: str | None = str(settings.okr_agent_id) if settings.okr_agent_id else None

        if body.enabled and not settings.okr_agent_id:
            from app.services.agent_seeder import seed_okr_agent_for_tenant
            logger.info(f"[OKR] OKR enabled for tenant {user.tenant_id} — auto-seeding OKR Agent")
            await seed_okr_agent_for_tenant(user.tenant_id, user.id)

            # Re-read settings to pick up the newly written okr_agent_id
            async with async_session() as db2:
                refreshed = await _get_or_create_settings(db2, user.tenant_id)
                await _sync_okr_report_triggers(db2, refreshed)
                await db2.commit()
                okr_agent_id_str = str(refreshed.okr_agent_id) if refreshed.okr_agent_id else None

        return OKRSettingsOut(
            enabled=settings.enabled,
            first_enabled_at=settings.first_enabled_at.isoformat() if settings.first_enabled_at else None,
            daily_report_enabled=settings.daily_report_enabled,
            daily_report_time=settings.daily_report_time,
            daily_report_skip_non_workdays=settings.daily_report_skip_non_workdays,
            weekly_report_enabled=False,
            weekly_report_day=0,
            period_frequency=settings.period_frequency,
            period_length_days=settings.period_length_days,
            period_frequency_locked=settings.first_enabled_at is not None,
            okr_agent_id=okr_agent_id_str,
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
        # Locate the OKR Agent from settings
        settings = await _get_or_create_settings(db, user.tenant_id)
        if not settings.okr_agent_id:
            raise HTTPException(404, "OKR Agent not found for this tenant. Enable OKR in Company Settings first.")
        okr_agent_id = settings.okr_agent_id

        await _sync_okr_agent_relationships(db, user.tenant_id, okr_agent_id)
        await db.commit()

    return {"status": "ok", "okr_agent_id": str(okr_agent_id)}


# ─── Periods ──────────────────────────────────────────────────────────────────


@router.get("/periods", response_model=list[PeriodOut])
async def list_periods(user=Depends(get_current_user)):
    """Return OKR periods from first enablement through the next period.

    Periods are computed from the tenant's locked OKR cadence. Once OKR has
    been enabled for a tenant, the first enabled period remains the start of
    the selectable history even if OKR is later disabled and re-enabled.
    """
    async with async_session() as db:
        settings = await _get_or_create_settings(db, user.tenant_id)
        first_enabled_at = settings.first_enabled_at
        if first_enabled_at is None and settings.enabled:
            earliest_result = await db.execute(
                select(OKRObjective.period_start)
                .where(OKRObjective.tenant_id == user.tenant_id)
                .order_by(OKRObjective.period_start.asc())
                .limit(1)
            )
            earliest_period_start = earliest_result.scalar_one_or_none()
            if earliest_period_start:
                first_enabled_at = datetime.combine(
                    earliest_period_start,
                    datetime.min.time(),
                    tzinfo=timezone.utc,
                )
            else:
                first_enabled_at = datetime.now(timezone.utc)
            settings.first_enabled_at = first_enabled_at
        await db.commit()

    freq = settings.period_frequency
    length = settings.period_length_days

    def _period_label(start: date, freq: str) -> str:
        if freq == "monthly":
            return start.strftime("%b %Y")
        elif freq == "quarterly":
            q = (start.month - 1) // 3 + 1
            return f"Q{q} {start.year}"
        else:
            end = start + timedelta(days=(length or 90) - 1)
            return f"{start.isoformat()} – {end.isoformat()}"

    cur_start, _ = _compute_current_period(freq, length)
    first_anchor = (first_enabled_at.date() if first_enabled_at else date.today())
    start, _ = _compute_period_for_date(freq, length, first_anchor)
    final_start, _ = _advance_period(cur_start, freq, length, 1)

    all_periods: list[tuple[date, date]] = []
    cursor_start = start
    guard = 0
    while cursor_start <= final_start and guard < 600:
        period_start, period_end = _compute_period_for_date(freq, length, cursor_start)
        all_periods.append((period_start, period_end))
        cursor_start, _ = _advance_period(period_start, freq, length, 1)
        guard += 1

    return [
        PeriodOut(
            start=s.isoformat(),
            end=e.isoformat(),
            label=_period_label(s, freq),
            is_current=(s == cur_start),
        )
        for s, e in all_periods
    ]


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

        # Batch-resolve owner names: collect distinct user/agent IDs
        user_owner_ids = [
            o.owner_id for o in objectives
            if o.owner_type == "user" and o.owner_id
        ]
        agent_owner_ids = [
            o.owner_id for o in objectives
            if o.owner_type == "agent" and o.owner_id
        ]

        user_names: dict[uuid.UUID, str] = {}
        if user_owner_ids:
            u_result = await db.execute(
                select(User.id, User.display_name).where(User.id.in_(user_owner_ids))
            )
            user_names = {row.id: (row.display_name or "") for row in u_result.fetchall()}

            # Fallback: owner_id might be an OrgMember.id (e.g. OKR Agent passed
            # OrgMember.id instead of User.id). Look them up in org_members table.
            from app.models.org import OrgMember
            unresolved_ids = [oid for oid in user_owner_ids if oid not in user_names]
            if unresolved_ids:
                m_result = await db.execute(
                    select(OrgMember.id, OrgMember.name).where(
                        OrgMember.id.in_(unresolved_ids)
                    )
                )
                for row in m_result.fetchall():
                    user_names[row.id] = row.name or ""

        agent_names: dict[uuid.UUID, str] = {}
        if agent_owner_ids:
            a_result = await db.execute(
                select(Agent.id, Agent.name).where(Agent.id.in_(agent_owner_ids))
            )
            agent_names = {row.id: (row.name or "") for row in a_result.fetchall()}

        def _resolve_name(obj: OKRObjective) -> str | None:
            if not obj.owner_id:
                return None
            if obj.owner_type == "user":
                return user_names.get(obj.owner_id)
            if obj.owner_type == "agent":
                return agent_names.get(obj.owner_id)
            return None

        return [
            _obj_to_out(o, krs_by_obj.get(o.id, []), owner_name=_resolve_name(o))
            for o in objectives
        ]



@router.post("/objectives", response_model=ObjectiveOut)
async def create_objective(body: ObjectiveCreate, user=Depends(get_current_user)):
    """Create a new Objective."""
    from app.models.org import OrgMember

    if not _is_okr_admin(user):
        raise _dashboard_write_forbidden()

    async with async_session() as db:
        resolved_owner_id: uuid.UUID | None = None

        if body.owner_id:
            candidate = uuid.UUID(body.owner_id)

            if body.owner_type == "user":
                # Verify the UUID is a real User.id — if not, check if it's an
                # OrgMember.id and transparently resolve to the linked user_id.
                # This guards against OKR Agent accidentally passing OrgMember.id.
                user_check = await db.execute(select(User.id).where(User.id == candidate))
                if user_check.scalar_one_or_none():
                    resolved_owner_id = candidate
                else:
                    # Fallback: maybe agent sent OrgMember.id — resolve to user_id
                    member_check = await db.execute(
                        select(OrgMember.id, OrgMember.user_id).where(
                            OrgMember.id == candidate,
                        )
                    )
                    member_row = member_check.first()
                    if member_row:
                        if member_row.user_id:
                            # Linked member: use the platform user_id
                            resolved_owner_id = member_row.user_id
                            logger.info(
                                f"[create_objective] Resolved OrgMember.id {candidate} "
                                f"→ user_id {resolved_owner_id}"
                            )
                        else:
                            # Channel-only member with no platform account yet.
                            # Store OrgMember.id directly as owner_id so the OKR
                            # can be matched back in members_without_okr checks.
                            resolved_owner_id = candidate
                            logger.info(
                                f"[create_objective] Channel-only OrgMember {candidate} "
                                f"has no user_id — storing OrgMember.id as owner_id"
                            )
                    else:
                        raise HTTPException(
                            422,
                            f"owner_id '{body.owner_id}' does not match any User or OrgMember in this tenant",
                        )
            else:
                resolved_owner_id = candidate

        obj = OKRObjective(
            tenant_id=user.tenant_id,
            title=body.title,
            description=body.description,
            owner_type=body.owner_type,
            owner_id=resolved_owner_id,
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
    if not _is_okr_admin(user):
        raise _dashboard_write_forbidden()

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
    if not _is_okr_admin(user):
        raise _dashboard_write_forbidden()

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
    if not _is_okr_admin(user):
        raise _dashboard_write_forbidden()

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
    if not _is_okr_admin(user):
        raise _dashboard_write_forbidden()

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
    if not _is_okr_admin(user):
        raise _dashboard_write_forbidden()

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

    if not _is_okr_admin(user):
        raise _dashboard_write_forbidden()

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


def _serialize_company_report(report: CompanyReport) -> CompanyReportOut:
    return CompanyReportOut(
        id=str(report.id),
        report_type=report.report_type,
        period_start=report.period_start.isoformat(),
        period_end=report.period_end.isoformat(),
        period_label=report.period_label,
        content=report.content,
        submitted_count=report.submitted_count,
        missing_count=report.missing_count,
        needs_refresh=report.needs_refresh,
        generated_at=report.generated_at.isoformat() if report.generated_at else "",
        updated_at=report.updated_at.isoformat() if report.updated_at else "",
    )


@router.get("/member-daily-reports", response_model=list[MemberDailyReportOut])
async def list_member_daily_reports(
    report_date: str | None = None,
    user=Depends(get_current_user),
):
    """List all member daily reports for a specific date plus missing members."""
    from app.services.okr_reporting import list_member_daily_reports_for_date

    target_day = date.fromisoformat(report_date) if report_date else date.today()
    items = await list_member_daily_reports_for_date(user.tenant_id, target_day)
    return [
        MemberDailyReportOut(
            id=f"{item['member_type']}:{item['member_id']}:{target_day.isoformat()}",
            member_type=item["member_type"],
            member_id=item["member_id"],
            display_name=item["display_name"],
            avatar_url=item["avatar_url"],
            group_label=item["group_label"],
            report_date=target_day.isoformat(),
            content=item["content"],
            status=item["status"],
            submitted_at=item["submitted_at"],
            updated_at=item["updated_at"],
        )
        for item in items
    ]


@router.post("/member-daily-reports", response_model=MemberDailyReportOut)
async def upsert_member_daily_report(
    body: MemberDailyReportUpsert,
    user=Depends(get_current_user),
):
    """Create or update a member daily report.

    Regular members can only edit their own user report.
    Org admins and platform admins may specify a tenant member explicitly.
    """
    from app.services.okr_reporting import (
        list_tracked_okr_members,
        upsert_member_daily_report as _upsert,
    )

    target_member_type = body.member_type or "user"
    if body.member_id:
        target_member_id = uuid.UUID(body.member_id)
    else:
        target_member_id = user.id

    if getattr(user, "role", None) not in ("org_admin", "platform_admin"):
        if target_member_type != "user" or target_member_id != user.id:
            raise HTTPException(403, "You can only submit your own daily report")

    report_date = date.fromisoformat(body.report_date)
    report = await _upsert(
        tenant_id=user.tenant_id,
        member_type=target_member_type,
        member_id=target_member_id,
        report_date=report_date,
        content=body.content,
        source=body.source,
    )
    member_map = {
        (member.member_type, str(member.member_id)): member
        for member in await list_tracked_okr_members(user.tenant_id)
    }
    member_meta = member_map.get((report.member_type, str(report.member_id)))
    return MemberDailyReportOut(
        id=str(report.id),
        member_type=report.member_type,
        member_id=str(report.member_id),
        display_name=member_meta.display_name if member_meta else str(report.member_id),
        avatar_url=member_meta.avatar_url if member_meta else None,
        group_label=member_meta.group_label if member_meta else "Members",
        report_date=report.report_date.isoformat(),
        content=report.content,
        status=report.status,
        submitted_at=report.submitted_at.isoformat() if report.submitted_at else None,
        updated_at=report.updated_at.isoformat() if report.updated_at else None,
    )


@router.get("/company-reports", response_model=list[CompanyReportOut])
async def list_company_reports_api(
    report_type: str | None = None,
    limit: int = 50,
    user=Depends(get_current_user),
):
    """List company-level reports from the new reporting pipeline."""
    from app.services.okr_reporting import list_company_reports

    reports = await list_company_reports(user.tenant_id, report_type=report_type, limit=limit)
    return [_serialize_company_report(report) for report in reports]


@router.post("/company-reports/regenerate", response_model=CompanyReportOut)
async def regenerate_company_report(
    body: CompanyReportRegenerate,
    user=Depends(get_current_user),
):
    """Rebuild a single company report for a target period."""
    if getattr(user, "role", None) not in ("org_admin", "platform_admin"):
        raise HTTPException(403, "Only org admins can regenerate company reports")

    from app.services.okr_reporting import (
        generate_company_daily_report,
        generate_company_monthly_report,
        generate_company_weekly_report,
    )

    period_start = date.fromisoformat(body.period_start)
    if body.report_type == "daily":
        report = await generate_company_daily_report(user.tenant_id, period_start)
    elif body.report_type == "weekly":
        report = await generate_company_weekly_report(user.tenant_id, period_start)
    elif body.report_type == "monthly":
        report = await generate_company_monthly_report(user.tenant_id, period_start)
    else:
        raise HTTPException(400, "Invalid report_type")

    return _serialize_company_report(report)


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

        # ── Get the OKR Agent from Settings ──────────────────────────────────
        settings = await _get_or_create_settings(db, user.tenant_id)
        okr_agent_id_val: uuid.UUID | None = settings.okr_agent_id
        okr_agent_id_str: str | None = str(okr_agent_id_val) if okr_agent_id_val else None

        # ── Fetch tracked members from OKR Agent's relationship list ──────────
        tracked_user_ids: list[str] = []
        tracked_agent_ids: list[str] = []
        members_without_okr: list[dict] = []

        if okr_agent_id_val:
            # ── Human members ─────────────────────────────────────────────────
            # Fetch ALL OrgMembers in OKR Agent's relationships, regardless of
            # whether they have a platform account (user_id) or not.
            # This includes members from any channel (Feishu, Slack, etc.) and
            # members who haven't joined the platform yet (user_id=NULL).
            all_member_rows = (await db.execute(
                select(
                    OrgMember.id,
                    OrgMember.name,
                    OrgMember.user_id,
                    OrgMember.external_id,
                    OrgMember.avatar_url,
                    IdentityProvider.name.label("provider_name"),
                )
                .join(AgentRelationship, AgentRelationship.member_id == OrgMember.id)
                .outerjoin(IdentityProvider, OrgMember.provider_id == IdentityProvider.id)
                .where(
                    AgentRelationship.agent_id == okr_agent_id_val,
                    OrgMember.status == "active",
                )
            )).fetchall()

            # ── Canonicalize: one record per logical person ───────────────────
            # A "logical person" may have multiple OrgMember rows:
            #   a) Multiple channels (Feishu + Slack) — both may have user_id set
            #   b) Historical duplicates from channel ID changes
            #   c) A shell record (user_id=NULL) + a linked record (user_id!=NULL)
            #      with the same external_id
            #
            # Resolution rules (applied in order):
            #   1. Group by external_id → prefer user_id-linked over shell
            #      (handles case b/c: stale shell rows from the same channel identity)
            #   2. Group by user_id → keep one row per platform account
            #      (handles case a: same person has accounts on different channels)

            # Rule 1 — best OrgMember per external_id (prefer user_id != NULL)
            best_by_ext: dict[str, object] = {}
            unkeyed: list[object] = []  # rows with no external_id
            for row in all_member_rows:
                if not row.external_id:
                    unkeyed.append(row)
                    continue
                existing = best_by_ext.get(row.external_id)
                if existing is None:
                    best_by_ext[row.external_id] = row
                elif existing.user_id is None and row.user_id is not None:
                    # Upgrade shell to linked
                    best_by_ext[row.external_id] = row

            candidates = list(best_by_ext.values()) + unkeyed

            # Rule 2 — deduplicate by user_id (one entry per platform account)
            seen_user_ids: set[uuid.UUID] = set()
            canonical_members: list[object] = []
            for row in candidates:
                if row.user_id is not None:
                    if row.user_id in seen_user_ids:
                        continue  # already represented via another channel
                    seen_user_ids.add(row.user_id)
                canonical_members.append(row)

            # ── Classify canonical members ─────────────────────────────────────
            for row in canonical_members:
                if row.user_id is not None:
                    tracked_user_ids.append(str(row.user_id))
                    # Check both User.id and OrgMember.id — OKR Agent may store
                    # OrgMember.id as owner_id instead of the linked User.id.
                    if row.user_id not in covered_ids and row.id not in covered_ids:
                        members_without_okr.append({
                            "id": str(row.id),
                            "type": "user",
                            "display_name": row.name or "",
                            "avatar_url": row.avatar_url or "",
                            "channel": row.provider_name or None,
                            "channel_user_id": None,
                            "source_label": row.provider_name or "Platform User",
                        })
                else:
                    # Channel-only member (no platform account yet).
                    # Check if an OKR was created with OrgMember.id as owner_id
                    # (e.g. OKR Agent used OrgMember.id when no User.id was available).
                    if row.id not in covered_ids:
                        members_without_okr.append({
                            "id": str(row.id),
                            "type": "user",
                            "display_name": row.name or "",
                            "avatar_url": row.avatar_url or "",
                            "channel": row.provider_name or None,
                            "channel_user_id": None,
                            "source_label": row.provider_name or "Platform User",
                        })

            # ── Agent members via AgentAgentRelationship ───────────────────────
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
                        "source_label": None,
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

    # ── Check for recent oneshot failure notifications ──────────────────────
    last_outreach_error = None
    if okr_agent_id_val:
        from app.models.notification import Notification
        async with async_session() as db2:
            notif_result = await db2.execute(
                select(Notification)
                .where(
                    Notification.user_id == user.id,
                    Notification.ref_id == okr_agent_id_val,
                    Notification.type == "system",
                    Notification.title.contains("task failed"),
                )
                .order_by(Notification.created_at.desc())
                .limit(1)
            )
            notif = notif_result.scalar_one_or_none()
            if notif:
                last_outreach_error = {
                    "message": notif.body,
                    "timestamp": notif.created_at.isoformat() if notif.created_at else "",
                    "is_read": notif.is_read,
                }

    # ── Check for channel members whose channel is not configured on the OKR Agent ──
    channel_warnings: list[dict] = []
    if okr_agent_id_val and members_without_okr:
        # Collect unique channel types referenced by members without OKR
        from app.models.channel_config import ChannelConfig as _CC
        member_channels: dict[str, list[str]] = {}  # channel_name -> [member_names]
        for m in members_without_okr:
            ch = m.get("channel") or m.get("source_label")
            if ch and ch not in ("Platform User", "Web"):
                member_channels.setdefault(ch, []).append(m.get("display_name", "?"))

        if member_channels:
            # Map display channel names to channel_type enum values
            _channel_name_to_type = {
                "feishu": "feishu", "Feishu": "feishu",
                "dingtalk": "dingtalk", "DingTalk": "dingtalk",
                "wecom": "wecom", "WeCom": "wecom",
                "slack": "slack", "Slack": "slack",
                "discord": "discord", "Discord": "discord",
                "wechat": "wechat", "WeChat": "wechat",
            }
            needed_types = set()
            for ch_name in member_channels:
                ct = _channel_name_to_type.get(ch_name)
                if ct:
                    needed_types.add(ct)

            if needed_types:
                async with async_session() as db3:
                    configured_result = await db3.execute(
                        select(_CC.channel_type).where(
                            _CC.agent_id == okr_agent_id_val,
                            _CC.channel_type.in_(list(needed_types)),
                            _CC.is_configured == True,  # noqa: E712
                        )
                    )
                    configured_types = {row[0] for row in configured_result.fetchall()}

                missing_types = needed_types - configured_types
                # Build warnings for each missing channel
                _type_to_display = {v: k for k, v in _channel_name_to_type.items() if k[0].isupper()}
                for mt in missing_types:
                    display_name = _type_to_display.get(mt, mt)
                    # Find member names on this channel
                    affected = []
                    for ch_name, names in member_channels.items():
                        if _channel_name_to_type.get(ch_name) == mt:
                            affected.extend(names)
                    channel_warnings.append({
                        "channel_type": mt,
                        "channel_display": display_name,
                        "affected_members": affected,
                        "count": len(affected),
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
        "last_outreach_error": last_outreach_error,
        "channel_warnings": channel_warnings,
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

        # ── Find the OKR Agent from Settings ─────────────────────────────────
        if not settings.okr_agent_id:
            raise HTTPException(
                404,
                "OKR Agent not found. Please ensure OKR is enabled and the agent has been seeded.",
            )
        okr_agent_result = await db.execute(select(Agent).where(Agent.id == settings.okr_agent_id))
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

        # ── Fetch company OKRs + KRs for this period to share as context ─────
        company_okr_result = await db.execute(
            select(OKRObjective).where(
                OKRObjective.tenant_id == user.tenant_id,
                OKRObjective.owner_type == "company",
                OKRObjective.period_start >= ps,
                OKRObjective.period_end <= pe,
                OKRObjective.status != "archived",
            ).order_by(OKRObjective.created_at)
        )
        company_okrs = company_okr_result.scalars().all()

        # Fetch KRs for each company OKR
        company_okr_krs: dict[uuid.UUID, list] = {}
        for co in company_okrs:
            kr_result = await db.execute(
                select(OKRKeyResult)
                .where(OKRKeyResult.objective_id == co.id)
                .order_by(OKRKeyResult.created_at)
            )
            company_okr_krs[co.id] = kr_result.scalars().all()

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
        has_channel = bool(org_member.open_id or org_member.external_id)
        if has_channel:
            channel_hint = f'send_channel_message(member_name="{org_member.name}", message=...)'
            if platform_uid:
                channel_hint += "  (They also have a Platform account, but prefer channel message here)"
        elif platform_uid:
            channel_hint = 'send_platform_message(username="<their_username>", message=...)'
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
                    f"  (use this as the recipient identifier in send_platform_message)"
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
            f"  STEP 1 → send_message_to_agent(agent_name=\"{agent_member.name}\",\n"
            f"             message=\"[OKR Agent] 请根据公司 OKR，描述您在本周期（{ps.isoformat()} ~ {pe.isoformat()}）"
            f"的主要目标（Objective）和关键结果（Key Results）。\")\n"
            f"  STEP 2 → Read the reply carefully from the tool result.\n"
            f"  STEP 3 → Call this EXACTLY (use the UUID below verbatim, do NOT invent one):\n"
            f"    create_objective(title=\"<their objective>\", owner_type=\"agent\",\n"
            f"                    owner_id=\"{agent_member.id}\",\n"
            f"                    period_start=\"{ps.isoformat()}\", period_end=\"{pe.isoformat()}\")\n"
            f"  STEP 4 → For EACH Key Result they mentioned:\n"
            f"    create_key_result(objective_id=\"<id from STEP 3 result>\",\n"
            f"                     title=\"<KR title>\", target_value=<number>, unit=\"<unit if stated>\")"
        )
        members_to_contact.append(member_block)
        index += 1

    if not members_to_contact:
        return {
            "status": "no_action",
            "message": "All tracked members already have OKRs set for this period. No outreach needed.",
            "okr_agent_id": str(okr_agent.id),
        }

    # ── Compose the final task prompt ─────────────────────────────────────────
    period_label = f"{ps.strftime('%Y-%m-%d')} to {pe.strftime('%Y-%m-%d')}"
    members_block = "\n\n".join(members_to_contact)

    # Build company OKR + KR context summary
    if company_okrs:
        company_okr_lines = []
        for i, co in enumerate(company_okrs, 1):
            company_okr_lines.append(f"  {i}. **{co.title}**")
            if co.description:
                company_okr_lines.append(f"     说明: {co.description[:120]}")
            krs = company_okr_krs.get(co.id, [])
            for j, kr in enumerate(krs, 1):
                target_str = f"（目标值: {kr.target_value} {kr.unit or ''}）" if kr.target_value else ""
                company_okr_lines.append(f"     KR{j}: {kr.title}{target_str}")
        company_okrs_block = "\n".join(company_okr_lines)
    else:
        company_okrs_block = "  (No company OKRs set yet for this period)"

    # Count agent vs human members for adaptive max_rounds
    n_agents = sum(1 for m in members_to_contact if "[Agent]" in m)
    n_humans = len(members_to_contact) - n_agents
    # human: 2 rounds (compose + send); agent: 6 rounds (send + reply + objective + 3 KRs)
    safe_max_rounds = n_humans * 2 + n_agents * 6 + 3

    task_prompt = f"""[ADMIN TRIGGER — OKR Member Outreach — ONE-SHOT TASK]

Current OKR period: {period_label}
Admin who triggered this: {admin_username}

━━━ COMPANY OBJECTIVES (share this context with each member) ━━━
{company_okrs_block}

━━━ YOUR TASK ━━━
Contact the {len(members_to_contact)} member(s) below who have NOT set their OKRs for this period.
• For [Agent] members: collect their OKR and record it immediately (see STEP 1-4 per member).
• For human members: send a warm reminder that includes the company OKR context above.

━━━ TOOL RULES (MANDATORY — DO NOT DEVIATE) ━━━
• For members tagged [Agent]:
  → Follow the STEP 1-4 sequence in their block exactly.
  → Use ONLY send_message_to_agent — never channel tools for agents.
• For human members:
  → If Platform account shown: send_platform_message(username="<display_name>", message="...")
  → If Feishu/DingTalk channel: send_channel_message(member_name="<name>", message="...")
  → If neither: skip and note in summary.
  → Humans are fire-and-forget — do NOT wait for their reply.

━━━ STEP-BY-STEP ━━━
1. Process each member in order, following per-member instructions.
2. If a send or create fails: log the failure and continue.
3. STOP completely after processing all members — do not respond further.

━━━ MEMBERS TO CONTACT ({len(members_to_contact)} total) ━━━

{members_block}

━━━ BEGIN NOW ━━━
"""

    # ── Launch background task ────────────────────────────────────────────────
    from app.services.heartbeat import run_agent_oneshot

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
            "You can check the conversation details in the OKR Agent's chat history."
        ),
        "okr_agent_id": str(okr_agent.id),
        "members_count": len(members_to_contact),
    }


@router.post("/trigger-daily-collection")
async def trigger_daily_collection(user=Depends(get_current_user)):
    """Admin-triggered daily collection for tracked OKR relationships only."""
    if getattr(user, "role", None) not in ("org_admin", "platform_admin"):
        raise HTTPException(403, "Only org admins can trigger daily collection")
    from app.services.okr_daily_collection import trigger_daily_collection_for_tenant

    try:
        result = await trigger_daily_collection_for_tenant(user.tenant_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc

    if result["total_targets"] == 0:
        return {
            "status": "no_action",
            "message": "OKR Agent has no tracked relationships to collect from.",
            "okr_agent_id": result["okr_agent_id"],
            "member_count": 0,
        }

    return {
        "status": "accepted",
        "message": (
            f"Daily OKR collection sent to {result['sent_humans']} human target(s) and "
            f"{result['sent_agents']} agent target(s). Reply triggers are now active."
        ),
        "okr_agent_id": result["okr_agent_id"],
        "member_count": result["total_targets"],
    }
