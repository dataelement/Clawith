"""Project-related helpers for activities, context injection, and lightweight checks."""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone as tz
from typing import Any

from loguru import logger
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import async_session
from app.models.project import (
    Project,
    ProjectActivity,
    ProjectAgent,
    ProjectDecision,
    ProjectTask,
)
from app.services.notification_service import send_notification

PROJECT_TASK_ALLOWED_PRIORITIES = {"low", "normal", "high", "urgent"}
PROJECT_TASK_ALLOWED_STATUSES = {"todo", "doing", "review", "done", "cancelled"}
PROJECT_TASK_TERMINAL_STATUSES = {"done", "cancelled"}
PROJECT_FOCUS_PREFIXES = {"project", "project_task"}


def normalize_project_focus_ref(focus_ref: str | None) -> str | None:
    """Normalize project-related focus refs while preserving other focus identifiers."""
    if not focus_ref:
        return None
    value = focus_ref.strip()
    if ":" not in value:
        return value
    prefix, raw_id = value.split(":", 1)
    prefix = prefix.strip().lower()
    raw_id = raw_id.strip()
    if prefix not in PROJECT_FOCUS_PREFIXES:
        return value
    try:
        parsed_id = uuid.UUID(raw_id)
    except ValueError:
        return value
    return f"{prefix}:{parsed_id}"


def parse_project_focus_ref(focus_ref: str | None) -> tuple[str | None, uuid.UUID | None]:
    """Return (`project` | `project_task`, uuid) for recognized refs."""
    normalized = normalize_project_focus_ref(focus_ref)
    if not normalized or ":" not in normalized:
        return None, None
    prefix, raw_id = normalized.split(":", 1)
    if prefix not in PROJECT_FOCUS_PREFIXES:
        return None, None
    try:
        return prefix, uuid.UUID(raw_id)
    except ValueError:
        return None, None


def _ellipsis(value: str | None, limit: int) -> str | None:
    if not value:
        return None
    text = " ".join(value.strip().split())
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def _format_dt(value: datetime | None) -> str | None:
    if not value:
        return None
    try:
        return value.astimezone(tz.utc).strftime("%Y-%m-%d %H:%M UTC")
    except Exception:
        return value.isoformat()


async def create_project_activity(
    db: AsyncSession,
    *,
    project_id: uuid.UUID,
    event: str,
    actor_type: str,
    actor_id: uuid.UUID | None = None,
    payload: dict[str, Any] | None = None,
) -> ProjectActivity:
    """Append a new activity row to the project feed."""
    activity = ProjectActivity(
        project_id=project_id,
        actor_type=actor_type,
        actor_id=actor_id,
        event=event,
        payload=payload or {},
    )
    db.add(activity)
    await db.flush()
    return activity


async def build_project_brief_prompt(
    db: AsyncSession,
    *,
    project: Project,
    focused_task: ProjectTask | None = None,
    max_pending_tasks: int = 8,
    max_decisions: int = 5,
) -> str:
    """Build a concise system-prompt block for project mode."""
    pending_count_result = await db.execute(
        select(ProjectTask.id).where(
            ProjectTask.project_id == project.id,
            ~ProjectTask.status.in_(PROJECT_TASK_TERMINAL_STATUSES),
        )
    )
    pending_count = len(pending_count_result.scalars().all())

    pending_result = await db.execute(
        select(ProjectTask)
        .where(
            ProjectTask.project_id == project.id,
            ~ProjectTask.status.in_(PROJECT_TASK_TERMINAL_STATUSES),
        )
        .order_by(
            ProjectTask.due_at.is_(None),
            ProjectTask.due_at.asc(),
            ProjectTask.sort_order.asc(),
            ProjectTask.created_at.asc(),
        )
        .limit(max_pending_tasks)
    )
    pending_tasks = pending_result.scalars().all()

    decision_result = await db.execute(
        select(ProjectDecision)
        .where(ProjectDecision.project_id == project.id)
        .order_by(ProjectDecision.created_at.desc())
        .limit(max_decisions)
    )
    decisions = decision_result.scalars().all()

    lines = [
        "# Active Project Context",
        "You are currently working in project mode. Use this shared context when planning, prioritizing, and answering.",
        "",
        "## Project",
        f"- Name: {project.name}",
        f"- Status: {project.status}",
    ]

    if project.folder:
        lines.append(f"- Folder: {project.folder}")
    if project.target_completion_at:
        lines.append(f"- Target completion: {_format_dt(project.target_completion_at)}")

    if project.description:
        lines.extend(["", "## Description", project.description.strip()])

    if project.brief:
        lines.extend(["", "## Brief", project.brief.strip()])

    lines.extend(["", f"## Pending Deliverables ({pending_count})"])
    if pending_tasks:
        for index, task in enumerate(pending_tasks, start=1):
            line = f"{index}. {task.title} [{task.status}]"
            if task.due_at:
                line += f" — due {_format_dt(task.due_at)}"
            lines.append(line)
            goal = _ellipsis(task.goal, 180)
            if goal:
                lines.append(f"   Goal: {goal}")
            acceptance = _ellipsis(task.acceptance_criteria, 180)
            if acceptance:
                lines.append(f"   Acceptance: {acceptance}")
    else:
        lines.append("- No pending deliverables.")

    lines.extend(["", f"## Key Decisions ({len(decisions)})"])
    if decisions:
        for decision in decisions:
            summary = _ellipsis(decision.content, 220)
            stamp = _format_dt(decision.created_at)
            label = f"- {decision.title}"
            if stamp:
                label += f" ({stamp})"
            lines.append(label)
            if summary:
                lines.append(f"  {summary}")
    else:
        lines.append("- No recorded decisions yet.")

    if focused_task:
        lines.extend(
            [
                "",
                "## Focused Deliverable",
                f"- Title: {focused_task.title}",
                f"- Status: {focused_task.status}",
            ]
        )
        if focused_task.due_at:
            lines.append(f"- Due: {_format_dt(focused_task.due_at)}")
        goal = _ellipsis(focused_task.goal, 300)
        if goal:
            lines.append(f"- Goal: {goal}")
        acceptance = _ellipsis(focused_task.acceptance_criteria, 300)
        if acceptance:
            lines.append(f"- Acceptance: {acceptance}")

    return "\n".join(lines).strip()


async def get_project_brief_prompt(
    db: AsyncSession,
    *,
    project_id: uuid.UUID,
    tenant_id: uuid.UUID | None = None,
    agent_id: uuid.UUID | None = None,
    focused_task_id: uuid.UUID | None = None,
) -> str | None:
    """Resolve and format a project prompt, optionally scoped to tenant and agent membership."""
    project_result = await db.execute(select(Project).where(Project.id == project_id))
    project = project_result.scalar_one_or_none()
    if not project:
        return None
    if tenant_id and project.tenant_id != tenant_id:
        return None
    if agent_id:
        membership = await db.execute(
            select(ProjectAgent.project_id).where(
                ProjectAgent.project_id == project.id,
                ProjectAgent.agent_id == agent_id,
            )
        )
        if membership.scalar_one_or_none() is None:
            return None

    focused_task: ProjectTask | None = None
    if focused_task_id:
        task_result = await db.execute(
            select(ProjectTask).where(
                ProjectTask.id == focused_task_id,
                ProjectTask.project_id == project.id,
            )
        )
        focused_task = task_result.scalar_one_or_none()
        if not focused_task:
            return None

    return await build_project_brief_prompt(
        db,
        project=project,
        focused_task=focused_task,
    )


async def get_project_prompt_from_focus_ref(
    db: AsyncSession,
    *,
    focus_ref: str | None,
    agent_id: uuid.UUID | None = None,
    tenant_id: uuid.UUID | None = None,
) -> str | None:
    """Resolve `project:` / `project_task:` refs into a reusable project prompt."""
    ref_type, ref_id = parse_project_focus_ref(focus_ref)
    if not ref_type or not ref_id:
        return None

    if ref_type == "project":
        return await get_project_brief_prompt(
            db,
            project_id=ref_id,
            tenant_id=tenant_id,
            agent_id=agent_id,
        )

    task_result = await db.execute(select(ProjectTask).where(ProjectTask.id == ref_id))
    task = task_result.scalar_one_or_none()
    if not task:
        return None

    return await get_project_brief_prompt(
        db,
        project_id=task.project_id,
        tenant_id=tenant_id,
        agent_id=agent_id,
        focused_task_id=task.id,
    )


async def check_overdue_projects_once() -> int:
    """Mark overdue projects once and notify the creator."""
    now = datetime.now(tz.utc)
    created = 0

    async with async_session() as db:
        result = await db.execute(
            select(Project).where(
                Project.target_completion_at.isnot(None),
                Project.target_completion_at < now,
                Project.status.in_(("draft", "active", "on_hold")),
            )
        )
        overdue_projects = result.scalars().all()

        for project in overdue_projects:
            existing = await db.execute(
                select(ProjectActivity.id).where(
                    and_(
                        ProjectActivity.project_id == project.id,
                        ProjectActivity.event == "project.overdue",
                    )
                )
            )
            if existing.scalar_one_or_none():
                continue

            await create_project_activity(
                db,
                project_id=project.id,
                event="project.overdue",
                actor_type="system",
                payload={
                    "project_name": project.name,
                    "target_completion_at": project.target_completion_at.isoformat() if project.target_completion_at else None,
                },
            )
            await send_notification(
                db,
                user_id=project.created_by,
                type="project_overdue",
                title=f"Project overdue: {project.name}",
                body="The project's target completion date has passed.",
                link=f"/projects/{project.id}",
                ref_id=project.id,
                sender_name="Clawith",
            )
            created += 1

        if created:
            await db.commit()

    return created


async def start_project_monitor(interval_seconds: int = 300) -> None:
    """Periodic project checks for due reminders."""
    logger.info(f"[ProjectMonitor] Started ({interval_seconds}s interval)")
    while True:
        try:
            created = await check_overdue_projects_once()
            if created:
                logger.info(f"[ProjectMonitor] Recorded {created} overdue project reminder(s)")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning(f"[ProjectMonitor] iteration failed: {exc}")
        await asyncio.sleep(interval_seconds)
