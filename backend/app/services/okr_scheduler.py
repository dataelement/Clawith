"""OKR Scheduler — batch progress collection and report generation.

Provides functions called by OKR Agent tools:
  - collect_all_focus_updates(): read all Agent focus.md files and sync progress
  - generate_daily_report():     build and store a daily OKR report
  - generate_weekly_report():    build and store a weekly OKR report

Design decisions:
  - Direct DB writes (no HTTP round-trips) for efficiency
  - focus.md is parsed with regex, not LLM, to avoid token cost for simple extraction
  - Reports are stored in WorkReport table AND returned as strings to the caller
    so the OKR Agent LLM can post to plaza / send to channels as it sees fit
  - All errors are caught per-agent so one bad focus.md doesn't block the batch
"""

import re
import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Optional

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import async_session
from app.models.agent import Agent
from app.models.okr import (
    OKRKeyResult,
    OKRObjective,
    OKRProgressLog,
    OKRSettings,
    WorkReport,
)
from app.services.storage import agent_storage_key, get_storage_backend, store_agent_bytes


# ─── Focus File Parsing ───────────────────────────────────────────────────────

# Matches lines like:
#   - **KR ID**: 3f35a1cc-1234-5678-abcd-ef1234567890
_KR_ID_RE = re.compile(
    r"\*\*KR ID\*\*[:\s]+([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})",
    re.IGNORECASE,
)

# Matches lines like:
#   - **Current Progress**: 4.2 / 5.0 NPS
#   - **Current Progress**: 42%
#   - **当前进度**: 4.2
_PROGRESS_RE = re.compile(
    r"\*\*(?:Current Progress|当前进度)\*\*[:\s]+([\d.]+)",
    re.IGNORECASE,
)

# Matches lines like:
#   - **This Week**: Completed 3 user interviews
#   - **本期工作**: 本周完成了 3 个用户反馈
_NOTE_RE = re.compile(
    r"\*\*(?:This Week|本期工作)\*\*[:\s]+(.+)",
    re.IGNORECASE,
)


def _parse_focus_md(content: str) -> list[tuple[str, float, str]]:
    """Parse a focus.md file and extract KR updates.

    Returns a list of (kr_id, current_value, note) tuples.
    Each tuple represents one KR that has a reported progress value.

    The parser works section-by-section: a KR ID anchor must appear before
    the progress value for the association to be made. This matches the
    standard focus.md format defined in HEARTBEAT.md.
    """
    results: list[tuple[str, float, str]] = []

    # Split into sections by '## KR:' headers
    # Each section owns one KR ID, one progress value, one note
    sections = re.split(r"(?m)^##\s+KR:", content)

    for section in sections[1:]:  # Skip the preamble before the first ## KR:
        kr_id_match = _KR_ID_RE.search(section)
        progress_match = _PROGRESS_RE.search(section)

        if not kr_id_match or not progress_match:
            continue  # Incomplete section — skip

        kr_id_str = kr_id_match.group(1).lower()
        try:
            value = float(progress_match.group(1))
        except ValueError:
            continue

        note_match = _NOTE_RE.search(section)
        note = note_match.group(1).strip() if note_match else ""

        results.append((kr_id_str, value, note))

    return results


# ─── Progress Collection ───────────────────────────────────────────────────────


async def collect_all_focus_updates(
    tenant_id: uuid.UUID,
    okr_agent_id: uuid.UUID,
) -> dict:
    """Read every Agent's focus.md and sync KR progress to the database.

    This is the core of the Focus File mechanism. Each Agent can maintain a
    focus.md in their workspace root. On every call, we:
      1. Enumerate all agents in the tenant
      2. Read their focus.md (skip if missing)
      3. Parse KR ID + current value pairs
      4. Update OKRKeyResult.current_value and write an OKRProgressLog

    Only writes a new log if the value actually changed (idempotent).
    """
    operation_id = str(uuid.uuid4())
    updated_count = 0
    skipped_count = 0
    error_count = 0
    updated_refs: list[str] = []
    commit_started = False

    try:
        async with async_session() as db:
            agents_result = await db.execute(
                select(Agent).where(
                    Agent.tenant_id == tenant_id,
                    Agent.id != okr_agent_id,
                )
            )
            agents = agents_result.scalars().all()
            storage = get_storage_backend()

            for agent in agents:
                focus_key = agent_storage_key(agent.id, "focus.md")
                try:
                    if not await storage.exists(focus_key):
                        skipped_count += 1
                        continue
                    content = await storage.read_text(
                        focus_key,
                        encoding="utf-8",
                        errors="replace",
                    )
                    updates = _parse_focus_md(content)
                    if not updates:
                        skipped_count += 1
                        continue

                    agent_changed = False
                    for kr_id_str, value, note in updates:
                        kr_uuid = uuid.UUID(kr_id_str)
                        kr_result = await db.execute(
                            select(OKRKeyResult, OKRObjective)
                            .join(
                                OKRObjective,
                                OKRKeyResult.objective_id == OKRObjective.id,
                            )
                            .where(
                                OKRKeyResult.id == kr_uuid,
                                OKRObjective.tenant_id == tenant_id,
                            )
                        )
                        row = kr_result.first()
                        if row is None:
                            skipped_count += 1
                            continue
                        key_result, _objective = row
                        if abs(key_result.current_value - value) < 0.001:
                            skipped_count += 1
                            continue

                        previous_value = key_result.current_value
                        key_result.current_value = value
                        key_result.last_updated_at = datetime.now(timezone.utc)
                        if key_result.target_value == 0:
                            key_result.status = (
                                "completed" if value >= 0 else "behind"
                            )
                        else:
                            ratio = value / key_result.target_value
                            if ratio >= 1.0:
                                key_result.status = "completed"
                            elif ratio >= 0.7:
                                key_result.status = "on_track"
                            elif ratio >= 0.4:
                                key_result.status = "at_risk"
                            else:
                                key_result.status = "behind"

                        progress_log_id = uuid.uuid4()
                        db.add(
                            OKRProgressLog(
                                id=progress_log_id,
                                kr_id=kr_uuid,
                                previous_value=previous_value,
                                new_value=value,
                                source="okr_agent",
                                note=(
                                    f"[focus.md] {note}"
                                    if note
                                    else "[focus.md] Auto-collected"
                                ),
                            )
                        )
                        updated_count += 1
                        agent_changed = True
                        updated_refs.append(
                            f"okr-progress-log://{progress_log_id}"
                        )
                    if not agent_changed and updates:
                        logger.debug(
                            "[OKRScheduler] No changed KR values for agent {}",
                            agent.id,
                        )
                except Exception:
                    logger.exception(
                        "[OKRScheduler] Failed to process focus.md for agent {}",
                        agent.id,
                    )
                    error_count += 1

            if updated_count:
                commit_started = True
                await db.commit()
    except Exception as exc:
        if commit_started:
            return {
                "status": "unknown",
                "error_code": "okr_collection_commit_outcome_unknown",
                "operation_id": operation_id,
                "updated_count": updated_count,
                "skipped_count": skipped_count,
                "error_count": error_count,
                "updated_refs": updated_refs,
            }
        logger.exception("[OKRScheduler] Focus collection failed before commit")
        return {
            "status": "failed",
            "error_code": "okr_collection_failed",
            "operation_id": operation_id,
            "updated_count": updated_count,
            "skipped_count": skipped_count,
            "error_count": error_count + 1,
            "updated_refs": updated_refs,
            "error_class": type(exc).__name__,
        }

    return {
        "status": "partial" if error_count else "succeeded",
        "operation_id": operation_id,
        "updated_count": updated_count,
        "skipped_count": skipped_count,
        "error_count": error_count,
        "updated_refs": updated_refs,
    }


# ─── Report Generation ────────────────────────────────────────────────────────


def _compute_period(
    frequency: str,
    length_days: Optional[int],
    target_date: Optional[date] = None,
) -> tuple[date, date]:
    """Compute OKR period start/end dates for a target date. Mirrors okr.py logic."""
    today = target_date or date.today()
    if frequency == "monthly":
        start = today.replace(day=1)
        if today.month == 12:
            end = today.replace(month=12, day=31)
        else:
            end = today.replace(month=today.month + 1, day=1) - timedelta(days=1)
    elif frequency == "custom" and length_days:
        epoch = date(1970, 1, 1)
        days_since_epoch = (today - epoch).days
        period_index = days_since_epoch // length_days
        start = epoch + timedelta(days=period_index * length_days)
        end = start + timedelta(days=length_days - 1)
    else:
        quarter = (today.month - 1) // 3 + 1
        start = date(today.year, (quarter - 1) * 3 + 1, 1)
        end = (date(today.year, quarter * 3 + 1, 1) - timedelta(days=1)) if quarter < 4 else date(today.year, 12, 31)
    return start, end


async def _build_okr_snapshot(
    tenant_id: uuid.UUID,
    db: AsyncSession,
    frequency: str,
    length_days: Optional[int],
    target_date: Optional[date] = None,
) -> tuple[list, dict, date, date]:
    """Fetch period objectives and KRs for report building.

    Returns (objectives, krs_by_obj, period_start, period_end).
    """
    ps, pe = _compute_period(frequency, length_days, target_date)

    obj_result = await db.execute(
        select(OKRObjective).where(
            OKRObjective.tenant_id == tenant_id,
            OKRObjective.period_start >= ps,
            OKRObjective.period_end <= pe,
            OKRObjective.status != "archived",
        ).order_by(OKRObjective.owner_type, OKRObjective.created_at)
    )
    objectives = obj_result.scalars().all()

    krs_by_obj: dict = {}
    if objectives:
        obj_ids = [o.id for o in objectives]
        kr_result = await db.execute(
            select(OKRKeyResult)
            .where(OKRKeyResult.objective_id.in_(obj_ids))
            .order_by(OKRKeyResult.created_at)
        )
        for kr in kr_result.scalars().all():
            krs_by_obj.setdefault(str(kr.objective_id), []).append(kr)

    return objectives, krs_by_obj, ps, pe


def _format_report_body(
    objectives: list,
    krs_by_obj: dict,
    period_start: date,
    period_end: date,
    report_type: str,
) -> str:
    """Build a structured Markdown report from OKR data."""
    today = date.today()
    header = (
        f"# OKR {'Daily' if report_type == 'daily' else 'Weekly'} Report\n"
        f"**Date**: {today.isoformat()}  |  "
        f"**Period**: {period_start.isoformat()} – {period_end.isoformat()}\n\n"
    )

    if not objectives:
        return header + "_No active OKRs found for this period._\n"

    # Compute overall health
    all_krs: list[OKRKeyResult] = []
    for krs in krs_by_obj.values():
        all_krs.extend(krs)

    status_counts: dict[str, int] = {}
    for kr in all_krs:
        status_counts[kr.status] = status_counts.get(kr.status, 0) + 1

    total_krs = len(all_krs)
    on_track = status_counts.get("on_track", 0) + status_counts.get("completed", 0)
    at_risk = status_counts.get("at_risk", 0)
    behind = status_counts.get("behind", 0)

    lines = [header]

    # Health summary
    lines.append("## Health Summary\n")
    lines.append("| Status | Count | % |\n|---|---|---|")
    if total_krs:
        lines.append(f"| On Track / Completed | {on_track} | {on_track*100//total_krs}% |")
        lines.append(f"| At Risk | {at_risk} | {at_risk*100//total_krs}% |")
        lines.append(f"| Behind | {behind} | {behind*100//total_krs}% |")
    lines.append("")

    # Items needing attention
    attention_krs = [kr for kr in all_krs if kr.status in ("at_risk", "behind")]
    if attention_krs:
        lines.append("## Needs Attention\n")
        for kr in attention_krs:
            pct = int(kr.current_value / kr.target_value * 100) if kr.target_value else 0
            lines.append(f"- **[{kr.status.upper()}]** {kr.title} — {pct}% ({kr.current_value}/{kr.target_value} {kr.unit or ''})")
        lines.append("")

    # Company objectives section
    company_objs = [o for o in objectives if o.owner_type == "company"]
    if company_objs:
        lines.append("## Company Objectives\n")
        for o in company_objs:
            krs = krs_by_obj.get(str(o.id), [])
            pct = 0
            if krs:
                pct = int(sum(min(k.current_value / k.target_value, 1) for k in krs if k.target_value) / len(krs) * 100)
            lines.append(f"### {o.title} [{pct}%]\n")
            for kr in krs:
                kr_pct = int(kr.current_value / kr.target_value * 100) if kr.target_value else 0
                bar = "█" * (kr_pct // 10) + "░" * (10 - kr_pct // 10)
                lines.append(f"- {bar} {kr.title}")
                lines.append(f"  {kr.current_value}/{kr.target_value} {kr.unit or ''} ({kr_pct}%) — _{kr.status}_")
            lines.append("")

    # Member objectives section
    member_objs = [o for o in objectives if o.owner_type != "company"]
    if member_objs:
        lines.append("## Member Objectives\n")
        for o in member_objs:
            krs = krs_by_obj.get(str(o.id), [])
            lines.append(f"### {o.owner_type}:{o.owner_id} — {o.title}\n")
            for kr in krs:
                kr_pct = int(kr.current_value / kr.target_value * 100) if kr.target_value else 0
                lines.append(f"- {kr.title}: {kr.current_value}/{kr.target_value} {kr.unit or ''} ({kr_pct}%) — _{kr.status}_")
            lines.append("")

    return "\n".join(lines)


async def _store_report(
    tenant_id: uuid.UUID,
    okr_agent_id: uuid.UUID,
    report_type: str,
    period_date: date,
    content: str,
    db: AsyncSession,
) -> dict:
    """Commit one report row and return its durable database receipt."""
    operation_id = str(uuid.uuid4())
    report = WorkReport(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        author_type="agent",
        author_id=okr_agent_id,
        report_type=report_type,
        period_date=period_date,
        content=content,
        source="okr_agent_collected",
    )
    commit_started = False
    try:
        db.add(report)
        commit_started = True
        await db.commit()
    except Exception as exc:
        return {
            "status": "unknown" if commit_started else "failed",
            "error_code": (
                "okr_report_commit_outcome_unknown"
                if commit_started
                else "okr_report_store_failed"
            ),
            "operation_id": operation_id,
            "report_id": str(report.id),
            "report_type": report_type,
            "error_class": type(exc).__name__,
        }
    return {
        "status": "succeeded",
        "operation_id": operation_id,
        "report_id": str(report.id),
        "report_type": report_type,
    }


async def _safe_write_report(
    okr_agent_id: uuid.UUID,
    filename: str,
    content: str,
) -> dict:
    """Project a committed report and return an explicit projection fact."""
    workspace_path = f"workspace/reports/{filename}"
    try:
        await store_agent_bytes(
            okr_agent_id,
            workspace_path,
            content.encode("utf-8"),
            content_type="text/markdown; charset=utf-8",
        )
    except Exception as exc:
        logger.warning(f"[OKRScheduler] Could not write report file {filename}: {exc}")
        return {
            "status": "failed",
            "workspace_path": workspace_path,
            "error_code": "okr_report_projection_failed",
            "error_class": type(exc).__name__,
        }
    return {
        "status": "succeeded",
        "workspace_path": workspace_path,
    }


async def _generate_report(
    tenant_id: uuid.UUID,
    okr_agent_id: uuid.UUID,
    *,
    report_type: str,
) -> dict:
    today = date.today()
    if report_type == "daily":
        target_date = today
        period_date = today
        filename = f"daily_{today.strftime('%Y%m%d')}.md"
    elif report_type == "weekly":
        target_date = today
        period_date = today - timedelta(days=today.weekday())
        filename = f"weekly_{period_date.strftime('%Y-W%V')}.md"
    else:
        target_date = today.replace(day=1) - timedelta(days=1)
        period_date = target_date.replace(day=1)
        filename = f"monthly_{target_date.strftime('%Y-%m')}.md"
    workspace_path = f"workspace/reports/{filename}"

    async with async_session() as db:
        settings_result = await db.execute(
            select(OKRSettings).where(OKRSettings.tenant_id == tenant_id)
        )
        okr_settings = settings_result.scalar_one_or_none()
        if not okr_settings or not okr_settings.enabled:
            return {
                "status": "failed",
                "db_status": "not_started",
                "projection_status": "not_started",
                "error_code": "okr_not_enabled",
                "report_type": report_type,
                "workspace_path": workspace_path,
            }

        objectives, krs_by_obj, period_start, period_end = (
            await _build_okr_snapshot(
                tenant_id,
                db,
                okr_settings.period_frequency,
                okr_settings.period_length_days,
                target_date=target_date,
            )
        )
        if report_type == "monthly":
            content = _format_monthly_report_body(
                objectives,
                krs_by_obj,
                period_start,
                period_end,
            )
        else:
            content = _format_report_body(
                objectives,
                krs_by_obj,
                period_start,
                period_end,
                report_type,
            )

        db_receipt = await _store_report(
            tenant_id,
            okr_agent_id,
            report_type,
            period_date,
            content,
            db,
        )

    receipt = {
        **db_receipt,
        "db_status": db_receipt.get("status", "succeeded"),
        "report_type": report_type,
        "period_start": period_start.isoformat(),
        "period_end": period_end.isoformat(),
        "workspace_path": workspace_path,
        "projection_status": "not_started",
        "content": content,
    }
    if receipt["db_status"] != "succeeded":
        receipt["status"] = receipt["db_status"]
        return receipt

    try:
        projection_receipt = await _safe_write_report(
            okr_agent_id,
            filename,
            content,
        )
    except Exception as exc:
        projection_receipt = {
            "status": "failed",
            "error_code": "okr_report_projection_failed",
            "error_class": type(exc).__name__,
        }
    projection_status = projection_receipt.get("status", "failed")
    receipt["projection_status"] = projection_status
    receipt["status"] = (
        "succeeded" if projection_status == "succeeded" else "partial"
    )
    if projection_status != "succeeded":
        receipt["error_code"] = "okr_report_projection_failed"
    return receipt


async def generate_daily_report(
    tenant_id: uuid.UUID,
    okr_agent_id: uuid.UUID,
) -> dict:
    """Generate and store a daily OKR report.

    Reads the current period's objectives, builds a structured Markdown
    summary, persists it to the WorkReport table, and also writes a file
    to the OKR Agent's workspace/reports/ directory.

    Returns the report content as a string so the OKR Agent can post it.
    """
    receipt = await _generate_report(
        tenant_id,
        okr_agent_id,
        report_type="daily",
    )
    logger.info(f"[OKRScheduler] Daily report generated for tenant {tenant_id}")
    return receipt


async def generate_weekly_report(
    tenant_id: uuid.UUID,
    okr_agent_id: uuid.UUID,
) -> dict:
    """Generate and store a weekly OKR report.

    The 'week' reference date is the most recent Monday.
    """
    receipt = await _generate_report(
        tenant_id,
        okr_agent_id,
        report_type="weekly",
    )
    logger.info(f"[OKRScheduler] Weekly report generated for tenant {tenant_id}")
    return receipt


# ─── OKR Settings Reader ──────────────────────────────────────────────────────


async def get_okr_settings_for_agent(tenant_id: uuid.UUID) -> dict:
    """Return OKR configuration for the tenant as a plain dict.

    Called by the get_okr_settings agent tool. Returns a dict the Agent can
    read to determine report schedule, period length, etc.
    """
    async with async_session() as db:
        result = await db.execute(
            select(OKRSettings).where(OKRSettings.tenant_id == tenant_id)
        )
        s = result.scalar_one_or_none()
        if not s:
            return {"enabled": False}

        return {
            "enabled": s.enabled,
            "daily_report_enabled": s.daily_report_enabled,
            "daily_report_time": s.daily_report_time,
            "daily_report_skip_non_workdays": s.daily_report_skip_non_workdays,
            "weekly_report_enabled": s.weekly_report_enabled,
            "weekly_report_day": s.weekly_report_day,
            "period_frequency": s.period_frequency,
            "period_length_days": s.period_length_days,
        }


# ─── Monthly Report (P3) ──────────────────────────────────────────────────────


async def generate_monthly_report(
    tenant_id: uuid.UUID,
    okr_agent_id: uuid.UUID,
) -> dict:
    """Generate and store a monthly OKR progress report.

    Triggered on the 1st of every month at 08:00 by the monthly_okr_report
    system cron trigger. The report covers:
      - Overall health summary (on_track / at_risk / behind counts)
      - Company objectives with KR progress bars
      - Member objectives with aggregated progress
      - Next-month guidance note (for OKR Agent to personalise)

    It summarizes the OKR period containing the last day of the previous month,
    so monthly OKR cadence reports the cycle that just ended.

    Stores a WorkReport row with report_type="monthly" and also writes the
    file to workspace/reports/monthly_YYYY-MM.md.
    Returns the Markdown content so the calling OKR Agent tool can send it
    to admins via send_platform_message.
    """
    receipt = await _generate_report(
        tenant_id,
        okr_agent_id,
        report_type="monthly",
    )
    logger.info(f"[OKRScheduler] Monthly report generated for tenant {tenant_id}")
    return receipt


def _format_monthly_report_body(
    objectives: list,
    krs_by_obj: dict,
    period_start: date,
    period_end: date,
) -> str:
    """Build a monthly OKR report in structured Markdown.

    Monthly reports are richer than daily/weekly ones:
      - Explicit month title and period range
      - Aggregated health percentages with trend emoji
      - Completed KRs highlighted
      - Items still behind listed for follow-up
      - A closing note prompting OKR Agent to set next-month agenda
    """
    from datetime import date as _date
    today = _date.today()
    month_label = period_start.strftime("%B %Y")

    header = (
        f"# Monthly OKR Report — {month_label}\n"
        f"**Generated**: {today.isoformat()}  "
        f"| **Period**: {period_start.isoformat()} – {period_end.isoformat()}\n\n"
    )

    if not objectives:
        return header + "_No active OKRs found for this period._\n"

    # Collect all KRs
    all_krs: list = []
    for krs in krs_by_obj.values():
        all_krs.extend(krs)

    total_krs = len(all_krs)
    completed = sum(1 for kr in all_krs if kr.status == "completed")
    on_track  = sum(1 for kr in all_krs if kr.status == "on_track")
    at_risk   = sum(1 for kr in all_krs if kr.status == "at_risk")
    behind    = sum(1 for kr in all_krs if kr.status == "behind")

    lines = [header]

    # ── Health summary ────────────────────────────────────────────────
    lines.append("## Monthly Health Summary\n")
    if total_krs:
        lines.append("| Status | Count | Ratio |")
        lines.append("|---|---|---|")
        lines.append(f"| Completed   | {completed} | {completed*100//total_krs}% |")
        lines.append(f"| On Track    | {on_track}  | {on_track*100//total_krs}% |")
        lines.append(f"| At Risk     | {at_risk}   | {at_risk*100//total_krs}% |")
        lines.append(f"| Behind      | {behind}    | {behind*100//total_krs}% |")
    else:
        lines.append("_No Key Results tracked this month._")
    lines.append("")

    # ── Company objectives ────────────────────────────────────────────
    company_objs = [o for o in objectives if o.owner_type == "company"]
    if company_objs:
        lines.append("## Company Objectives\n")
        for o in company_objs:
            krs = krs_by_obj.get(str(o.id), [])
            pct = 0
            if krs:
                pct = int(
                    sum(min(k.current_value / k.target_value, 1) for k in krs if k.target_value)
                    / len(krs) * 100
                )
            lines.append(f"### {o.title}  —  {pct}% overall\n")
            for kr in krs:
                kr_pct = int(kr.current_value / kr.target_value * 100) if kr.target_value else 0
                bar = "█" * (kr_pct // 10) + "░" * (10 - kr_pct // 10)
                status_badge = {
                    "completed": "DONE",
                    "on_track": "OK",
                    "at_risk": "RISK",
                    "behind": "BEHIND",
                }.get(kr.status, kr.status.upper())
                lines.append(f"- [{status_badge}] {bar} {kr.title}")
                lines.append(
                    f"  {kr.current_value} / {kr.target_value} {kr.unit or ''} ({kr_pct}%)"
                )
            lines.append("")

    # ── Member objectives ─────────────────────────────────────────────
    member_objs = [o for o in objectives if o.owner_type != "company"]
    if member_objs:
        lines.append("## Member Objectives\n")
        for o in member_objs:
            krs = krs_by_obj.get(str(o.id), [])
            lines.append(f"### {o.owner_type}: {o.title}\n")
            for kr in krs:
                kr_pct = int(kr.current_value / kr.target_value * 100) if kr.target_value else 0
                lines.append(
                    f"- {kr.title}: {kr.current_value}/{kr.target_value} "
                    f"{kr.unit or ''} ({kr_pct}%) — _{kr.status}_"
                )
            lines.append("")

    # ── Items that need follow-up ────────────────────────────────────
    attention_krs = [kr for kr in all_krs if kr.status in ("at_risk", "behind")]
    if attention_krs:
        lines.append("## Action Required\n")
        lines.append("The following Key Results need attention heading into next month:\n")
        for kr in attention_krs:
            kr_pct = int(kr.current_value / kr.target_value * 100) if kr.target_value else 0
            lines.append(f"- **{kr.status.upper()}** — {kr.title} ({kr_pct}%)")
        lines.append("")

    # ── Closing note ─────────────────────────────────────────────────
    lines.append("---")
    lines.append(
        "_This report was auto-generated by the OKR Agent. "
        "Please review the items needing attention and align with team members "
        "before the next check-in._"
    )

    return "\n".join(lines)
