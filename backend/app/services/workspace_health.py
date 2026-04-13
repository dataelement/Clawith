"""Periodic health checks for deployed workspace projects."""

import asyncio
import logging
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import select

from app.config import get_settings
from app.database import async_session
from app.models.workspace import WorkspaceBugReport, WorkspaceProject

logger = logging.getLogger(__name__)

settings = get_settings()

CHECK_INTERVAL = 300  # 5 minutes
RETRY_DELAY = 30  # seconds
GRACE_PERIOD = 30  # seconds after deploy before first check
MAX_AUTO_FIX = 3
AUTO_FIX_WINDOW = timedelta(hours=24)


async def run_health_checks(skip_initial_restore: bool = False):
    """Background loop that restores and checks workspace project health every 5 minutes."""
    logger.info("Workspace health check task started")
    first_cycle = True
    while True:
        try:
            if first_cycle and skip_initial_restore:
                logger.info(
                    "Runtime restore skipped before health checks because startup restore already ran"
                )
            else:
                from app.services.runtime_restore import restore_managed_runtimes

                restore_result = await restore_managed_runtimes()
                logger.info(
                    "Runtime restore checked %d managed runtimes before health checks",
                    len(restore_result.items),
                )
            await _check_all_projects()
        except Exception:
            logger.exception("Health check cycle failed")
        first_cycle = False
        await asyncio.sleep(CHECK_INTERVAL)


async def _check_all_projects():
    """Check all deployed projects."""
    async with async_session() as db:
        result = await db.execute(
            select(WorkspaceProject).where(WorkspaceProject.status == "deployed")
        )
        projects = result.scalars().all()

    now = datetime.now(timezone.utc)
    eligible = [
        p for p in projects
        if not p.updated_at or (now - p.updated_at.replace(tzinfo=timezone.utc)).total_seconds() >= GRACE_PERIOD
    ]

    # Check all projects in parallel
    results = await asyncio.gather(
        *[_check_project(p) for p in eligible],
        return_exceptions=True,
    )

    # Retry failed ones (in parallel), then create bug reports
    failed = [p for p, r in zip(eligible, results) if r is not True]
    if failed:
        await asyncio.sleep(RETRY_DELAY)
        retry_results = await asyncio.gather(
            *[_check_project(p) for p in failed],
            return_exceptions=True,
        )
        for p, r in zip(failed, retry_results):
            if r is not True:
                await _create_health_bug_report(p)


async def _check_project(project: WorkspaceProject) -> bool:
    """Check a single project's health. Returns True if healthy."""
    if project.deploy_type == "static":
        url = f"http://{settings.WORKSPACE_GATEWAY_CONTAINER}/workspace/{project.slug}/"
        method = "HEAD"
    else:
        endpoint = project.health_endpoint or f"/workspace/{project.slug}/health"
        url = f"http://{settings.WORKSPACE_GATEWAY_CONTAINER}{endpoint}"
        method = "GET"

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            if method == "HEAD":
                resp = await client.head(url)
            else:
                resp = await client.get(url)
            return resp.status_code == 200
    except Exception as e:
        logger.warning("Health check failed for %s: %s", project.slug, e)
        return False


async def _create_health_bug_report(project: WorkspaceProject):
    """Create a bug report for a failed health check, respecting circuit breaker."""
    now = datetime.now(timezone.utc)

    async with async_session() as db:
        # Re-fetch to get latest state
        proj = await db.get(WorkspaceProject, project.id)
        if not proj or proj.status != "deployed":
            return

        # Check circuit breaker
        if proj.auto_fix_window_start:
            window_start = proj.auto_fix_window_start.replace(tzinfo=timezone.utc)
            if now - window_start < AUTO_FIX_WINDOW:
                if proj.auto_fix_attempts >= MAX_AUTO_FIX:
                    # Exceeded max attempts — escalate
                    proj.status = "failed"
                    report = WorkspaceBugReport(
                        project_id=proj.id,
                        source="health_check",
                        description=f"Health check failed. Auto-fix limit ({MAX_AUTO_FIX}) exceeded in 24h window. Manual intervention required.",
                        status="escalated",
                    )
                    db.add(report)
                    await db.commit()
                    logger.warning("Project '%s' escalated — auto-fix limit exceeded", proj.slug)
                    return
            else:
                # Window expired, reset
                proj.auto_fix_attempts = 0
                proj.auto_fix_window_start = now

        if not proj.auto_fix_window_start:
            proj.auto_fix_window_start = now

        proj.auto_fix_attempts += 1

        # Check if there's already an open health_check report for this project
        existing = await db.execute(
            select(WorkspaceBugReport).where(
                WorkspaceBugReport.project_id == proj.id,
                WorkspaceBugReport.source == "health_check",
                WorkspaceBugReport.status.in_(["open", "investigating"]),
            )
        )
        if existing.scalar_one_or_none():
            await db.commit()  # save the attempt counter
            return  # don't duplicate

        report = WorkspaceBugReport(
            project_id=proj.id,
            source="health_check",
            description=f"Health check failed for /workspace/{proj.slug}/. The site may be down or returning errors.",
        )
        db.add(report)
        await db.commit()
        logger.info("Health check bug report created for '%s' (attempt %d/%d)", proj.slug, proj.auto_fix_attempts, MAX_AUTO_FIX)
