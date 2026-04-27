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
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import async_session
from app.models.agent import Agent
from app.models.okr import (
    OKRKeyResult,
    OKRObjective,
    OKRProgressLog,
    OKRSettings,
    WorkReport,
)

_settings = get_settings()
WORKSPACE_ROOT = Path(_settings.AGENT_DATA_DIR)


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
) -> str:
    """Read every Agent's focus.md and sync KR progress to the database.

    This is the core of the Focus File mechanism. Each Agent can maintain a
    focus.md in their workspace root. On every call, we:
      1. Enumerate all agents in the tenant
      2. Read their focus.md (skip if missing)
      3. Parse KR ID + current value pairs
      4. Update OKRKeyResult.current_value and write an OKRProgressLog

    Only writes a new log if the value actually changed (idempotent).
    """
    async with async_session() as db:

        # Enumerate all agents in this tenant (except the OKR Agent itself)
        agents_result = await db.execute(
            select(Agent).where(
                Agent.tenant_id == tenant_id,
                Agent.id != okr_agent_id,
            )
        )
        agents = agents_result.scalars().all()

        if not agents:
            return "No team members found. No focus files to collect."

        updated_count = 0
        skipped_count = 0
        error_count = 0
        lines: list[str] = []

        for agent in agents:
            agent_dir = WORKSPACE_ROOT / str(agent.id)
            focus_path = agent_dir / "focus.md"

            if not focus_path.exists():
                skipped_count += 1
                continue

            try:
                content = focus_path.read_text(encoding="utf-8")
                updates = _parse_focus_md(content)

                if not updates:
                    skipped_count += 1
                    continue

                for kr_id_str, value, note in updates:
                    try:
                        kr_uuid = uuid.UUID(kr_id_str)
                    except ValueError:
                        logger.warning(f"[OKRScheduler] Invalid KR UUID '{kr_id_str}' in {focus_path}")
                        continue

                    # Fetch the KR and verify it belongs to this tenant
                    kr_result = await db.execute(
                        select(OKRKeyResult, OKRObjective)
                        .join(OKRObjective, OKRKeyResult.objective_id == OKRObjective.id)
                        .where(
                            OKRKeyResult.id == kr_uuid,
                            OKRObjective.tenant_id == tenant_id,
                        )
                    )
                    row = kr_result.first()
                    if not row:
                        logger.warning(f"[OKRScheduler] KR {kr_uuid} not found or wrong tenant, skipping")
                        continue

                    kr, _ = row

                    # Skip if value hasn't changed (avoid duplicate log entries)
                    if abs(kr.current_value - value) < 0.001:
                        continue

                    prev_value = kr.current_value
                    kr.current_value = value
                    kr.last_updated_at = datetime.utcnow()

                    # Auto-compute status from progress ratio
                    if kr.target_value:
                        ratio = value / kr.target_value
                        if ratio >= 1.0:
                            kr.status = "completed"
                        elif ratio >= 0.7:
                            kr.status = "on_track"
                        elif ratio >= 0.4:
                            kr.status = "at_risk"
                        else:
                            kr.status = "behind"

                    # Write progress log entry
                    log = OKRProgressLog(
                        kr_id=kr_uuid,
                        previous_value=prev_value,
                        new_value=value,
                        source="okr_agent",
                        note=f"[focus.md] {note}" if note else "[focus.md] Auto-collected",
                    )
                    db.add(log)
                    updated_count += 1

                    lines.append(
                        f"  - {agent.name} / {kr.title}: {prev_value} → {value} ({kr.status})"
                    )

            except Exception as exc:
                logger.exception(f"[OKRScheduler] Failed to process focus.md for agent {agent.id}")
                error_count += 1

        await db.commit()

    summary = (
        f"Focus file collection complete.\n"
        f"  KRs updated: {updated_count}\n"
        f"  Agents without focus.md: {skipped_count}\n"
        f"  Errors: {error_count}\n"
    )
    if lines:
        summary += "\nChanges:\n" + "\n".join(lines)

    return summary


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
    lines.append(f"| Status | Count | % |\n|---|---|---|")
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
) -> None:
    """Write a report to the WorkReport table."""
    report = WorkReport(
        tenant_id=tenant_id,
        author_type="agent",
        author_id=okr_agent_id,
        report_type=report_type,
        period_date=period_date,
        content=content,
        source="okr_agent_collected",
    )
    db.add(report)
    await db.commit()


def _safe_write_report(agent_dir: Path, filename: str, content: str) -> None:
    """Write report to OKR Agent's workspace/reports/ directory."""
    try:
        reports_dir = agent_dir / "workspace" / "reports"
        reports_dir.mkdir(parents=True, exist_ok=True)
        (reports_dir / filename).write_text(content, encoding="utf-8")
    except Exception as exc:
        logger.warning(f"[OKRScheduler] Could not write report file {filename}: {exc}")


async def generate_daily_report(
    tenant_id: uuid.UUID,
    okr_agent_id: uuid.UUID,
) -> str:
    """Generate and store a daily OKR report.

    Reads the current period's objectives, builds a structured Markdown
    summary, persists it to the WorkReport table, and also writes a file
    to the OKR Agent's workspace/reports/ directory.

    Returns the report content as a string so the OKR Agent can post it.
    """
    async with async_session() as db:
        # Load settings for period frequency
        settings_result = await db.execute(
            select(OKRSettings).where(OKRSettings.tenant_id == tenant_id)
        )
        okr_settings = settings_result.scalar_one_or_none()

        if not okr_settings or not okr_settings.enabled:
            return "OKR is not enabled for this tenant."

        objectives, krs_by_obj, ps, pe = await _build_okr_snapshot(
            tenant_id, db, okr_settings.period_frequency, okr_settings.period_length_days
        )

        content = _format_report_body(objectives, krs_by_obj, ps, pe, "daily")

        today = date.today()
        await _store_report(tenant_id, okr_agent_id, "daily", today, content, db)

    # Write file to workspace
    agent_dir = WORKSPACE_ROOT / str(okr_agent_id)
    _safe_write_report(agent_dir, f"daily_{today.strftime('%Y%m%d')}.md", content)

    logger.info(f"[OKRScheduler] Daily report generated for tenant {tenant_id}")
    return content


async def generate_weekly_report(
    tenant_id: uuid.UUID,
    okr_agent_id: uuid.UUID,
) -> str:
    """Generate and store a weekly OKR report.

    The 'week' reference date is the most recent Monday.
    """
    async with async_session() as db:
        settings_result = await db.execute(
            select(OKRSettings).where(OKRSettings.tenant_id == tenant_id)
        )
        okr_settings = settings_result.scalar_one_or_none()

        if not okr_settings or not okr_settings.enabled:
            return "OKR is not enabled for this tenant."

        previous_month_ref = date.today().replace(day=1) - timedelta(days=1)
        objectives, krs_by_obj, ps, pe = await _build_okr_snapshot(
            tenant_id,
            db,
            okr_settings.period_frequency,
            okr_settings.period_length_days,
            target_date=previous_month_ref,
        )

        content = _format_report_body(objectives, krs_by_obj, ps, pe, "weekly")

        today = date.today()
        # Use Monday of this week as the canonical period_date
        monday = today - timedelta(days=today.weekday())
        await _store_report(tenant_id, okr_agent_id, "weekly", monday, content, db)

    agent_dir = WORKSPACE_ROOT / str(okr_agent_id)
    week_label = monday.strftime("%Y-W%V")
    _safe_write_report(agent_dir, f"weekly_{week_label}.md", content)

    logger.info(f"[OKRScheduler] Weekly report generated for tenant {tenant_id}")
    return content


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
) -> str:
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
    async with async_session() as db:
        settings_result = await db.execute(
            select(OKRSettings).where(OKRSettings.tenant_id == tenant_id)
        )
        okr_settings = settings_result.scalar_one_or_none()

        if not okr_settings or not okr_settings.enabled:
            return "OKR is not enabled for this tenant."

        objectives, krs_by_obj, ps, pe = await _build_okr_snapshot(
            tenant_id, db, okr_settings.period_frequency, okr_settings.period_length_days
        )

        content = _format_monthly_report_body(objectives, krs_by_obj, ps, pe)

        month_start = ps
        await _store_report(tenant_id, okr_agent_id, "monthly", month_start, content, db)

    # Write file to OKR Agent workspace
    agent_dir = WORKSPACE_ROOT / str(okr_agent_id)
    month_label = month_start.strftime("%Y-%m")
    _safe_write_report(agent_dir, f"monthly_{month_label}.md", content)

    logger.info(f"[OKRScheduler] Monthly report generated for tenant {tenant_id}")
    return content


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
        lines.append(f"| Status | Count | Ratio |")
        lines.append(f"|---|---|---|")
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
