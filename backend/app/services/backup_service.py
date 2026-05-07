"""Daily backup of Project workspaces (and agent workspaces) to a side directory.

Scope: tar+gzip the contents of PROJECT_WORKSPACE_DIR and AGENT_DATA_DIR into
/data/backups/{kind}-YYYYMMDD.tar.gz once a day, and prune beyond the retention
window.

Why not rely on host cron: we want the backup to be part of the app deployment —
if the team forgets to set up host cron the data is still protected. The backup
dir should itself be bind-mounted to the host so backups survive a container
rebuild (`/data/backups -> ./backend/backups`).

Ops note: this is a last-ditch snapshot, not a replacement for object storage
or PITR. See deploy/README.md for the durability tiers.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.config import get_settings

logger = logging.getLogger(__name__)

BACKUP_DIR = Path("/data/backups")
RETENTION_DAYS = 30
DAILY_INTERVAL_SECONDS = 24 * 60 * 60


def _tar_directory(src: Path, archive: Path) -> bool:
    """Tar+gzip `src` into `archive`. Returns True on success."""
    if not src.exists():
        logger.info(f"[backup] skipping {src} (does not exist)")
        return False
    archive.parent.mkdir(parents=True, exist_ok=True)
    tmp = archive.with_suffix(archive.suffix + ".partial")
    try:
        # Use system tar for speed + streaming; avoid loading files into memory.
        subprocess.run(
            ["tar", "czf", str(tmp), "-C", str(src.parent), src.name],
            check=True,
            capture_output=True,
        )
        tmp.replace(archive)  # atomic rename on success
        return True
    except subprocess.CalledProcessError as e:
        logger.warning(f"[backup] tar failed for {src}: {e.stderr.decode(errors='replace')[:300]}")
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        return False
    except FileNotFoundError:
        # No tar in container — fall back to Python
        try:
            import tarfile
            with tarfile.open(tmp, "w:gz") as tf:
                tf.add(str(src), arcname=src.name)
            tmp.replace(archive)
            return True
        except Exception as e:
            logger.warning(f"[backup] python tar fallback failed for {src}: {e}")
            return False


def _prune_old(prefix: str, retention_days: int = RETENTION_DAYS) -> int:
    """Delete backup archives older than retention_days. Returns count deleted."""
    if not BACKUP_DIR.exists():
        return 0
    cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
    deleted = 0
    for f in BACKUP_DIR.glob(f"{prefix}-*.tar.gz"):
        try:
            mtime = datetime.fromtimestamp(f.stat().st_mtime, timezone.utc)
            if mtime < cutoff:
                f.unlink()
                deleted += 1
        except OSError:
            continue
    return deleted


def run_backup_once() -> None:
    """Produce a dated archive for project_workspaces and agent_data.

    Idempotent in the sense that running it twice on the same day overwrites the
    day's archive — we keep one archive per day per kind.
    """
    settings = get_settings()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d")

    targets = [
        ("project_workspaces", Path(settings.PROJECT_WORKSPACE_DIR)),
        ("agent_data", Path(settings.AGENT_DATA_DIR)),
    ]

    for kind, src in targets:
        archive = BACKUP_DIR / f"{kind}-{stamp}.tar.gz"
        ok = _tar_directory(src, archive)
        if ok:
            size_mb = archive.stat().st_size / (1024 * 1024)
            logger.info(f"[backup] wrote {archive.name} ({size_mb:.1f} MB)")
        deleted = _prune_old(kind)
        if deleted:
            logger.info(f"[backup] pruned {deleted} archive(s) for {kind}")


async def backup_daemon() -> None:
    """Long-running task: run once on startup, then every 24h."""
    # Initial run after a short delay so app startup isn't blocked.
    await asyncio.sleep(60)
    while True:
        try:
            # Run the (blocking) tar in a thread so we don't stall the event loop.
            await asyncio.to_thread(run_backup_once)
        except Exception as e:
            logger.warning(f"[backup] run failed: {e}")
        await asyncio.sleep(DAILY_INTERVAL_SECONDS)
