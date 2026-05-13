"""Workspace collaboration helpers.

All user and agent writes should pass through this module so file history,
rollback, and human edit locks remain consistent across REST APIs and tools.
"""

from __future__ import annotations

import hashlib
import shutil
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import aiofiles
from sqlalchemy import and_, delete, desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.workspace import WorkspaceEditLock, WorkspaceFileRevision

USER_AUTOSAVE_MERGE_SECONDS = 60
EDIT_LOCK_TTL_SECONDS = 90
MAX_REVISION_TEXT_BYTES = 512 * 1024
BINARY_REVISION_EXTENSIONS = {
    ".7z",
    ".avif",
    ".bin",
    ".bmp",
    ".doc",
    ".docx",
    ".exe",
    ".gif",
    ".gz",
    ".ico",
    ".jpeg",
    ".jpg",
    ".mov",
    ".mp3",
    ".mp4",
    ".odp",
    ".ods",
    ".odt",
    ".pdf",
    ".png",
    ".ppt",
    ".pptx",
    ".rar",
    ".tar",
    ".webp",
    ".xls",
    ".xlsx",
    ".zip",
}


@dataclass
class WorkspaceWriteResult:
    ok: bool
    path: str
    message: str
    revision_id: str | None = None
    locked_by_user_id: str | None = None


def content_hash(content: str | None) -> str:
    """Return a stable hash for text content."""
    return hashlib.sha256((content or "").encode("utf-8")).hexdigest()


def normalize_workspace_path(path: str) -> str:
    """Normalize a workspace path without allowing absolute traversal."""
    clean = (path or "").replace("\\", "/").strip().lstrip("/")
    parts: list[str] = []
    for part in clean.split("/"):
        if part in ("", "."):
            continue
        if part == "..":
            if parts:
                parts.pop()
            continue
        parts.append(part)
    return "/".join(parts)


def safe_agent_path(base: Path, path: str) -> Path:
    """Resolve a path under an agent directory and reject traversal."""
    rel = normalize_workspace_path(path)
    target = (base / rel).resolve()
    if not str(target).startswith(str(base.resolve())):
        raise ValueError("Path traversal not allowed")
    return target


async def read_text_if_exists(path: Path) -> str | None:
    """Read a UTF-8 text file if it exists; return None for missing/binary files."""
    if not path.exists() or not path.is_file():
        return None
    if path.suffix.lower() in BINARY_REVISION_EXTENSIONS:
        return None
    try:
        if path.stat().st_size > MAX_REVISION_TEXT_BYTES:
            return None
    except OSError:
        return None
    async with aiofiles.open(path, "rb") as f:
        data = await f.read()
    if b"\x00" in data:
        return None
    return data.decode("utf-8", errors="replace")


async def cleanup_expired_locks(db: AsyncSession) -> None:
    """Remove stale edit locks."""
    now = datetime.now(timezone.utc)
    await db.execute(delete(WorkspaceEditLock).where(WorkspaceEditLock.expires_at <= now))


async def acquire_edit_lock(
    db: AsyncSession,
    *,
    agent_id: uuid.UUID,
    path: str,
    user_id: uuid.UUID,
    session_id: str | None = None,
) -> WorkspaceEditLock:
    """Acquire or refresh a human edit lock."""
    await cleanup_expired_locks(db)
    normalized = normalize_workspace_path(path)
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(seconds=EDIT_LOCK_TTL_SECONDS)

    result = await db.execute(
        select(WorkspaceEditLock).where(
            WorkspaceEditLock.agent_id == agent_id,
            WorkspaceEditLock.path == normalized,
        )
    )
    lock = result.scalar_one_or_none()
    if lock:
        lock.user_id = user_id
        lock.session_id = session_id
        lock.expires_at = expires_at
        lock.heartbeat_count = (lock.heartbeat_count or 0) + 1
    else:
        lock = WorkspaceEditLock(
            agent_id=agent_id,
            path=normalized,
            user_id=user_id,
            session_id=session_id,
            expires_at=expires_at,
            heartbeat_count=1,
        )
        db.add(lock)
    await db.flush()
    return lock


async def release_edit_lock(
    db: AsyncSession,
    *,
    agent_id: uuid.UUID,
    path: str,
    user_id: uuid.UUID,
) -> None:
    """Release a human edit lock owned by a user."""
    await db.execute(
        delete(WorkspaceEditLock).where(
            WorkspaceEditLock.agent_id == agent_id,
            WorkspaceEditLock.path == normalize_workspace_path(path),
            WorkspaceEditLock.user_id == user_id,
        )
    )


async def get_active_lock(
    db: AsyncSession,
    *,
    agent_id: uuid.UUID,
    path: str,
) -> WorkspaceEditLock | None:
    """Return an active lock for a file, if present."""
    await cleanup_expired_locks(db)
    result = await db.execute(
        select(WorkspaceEditLock).where(
            WorkspaceEditLock.agent_id == agent_id,
            WorkspaceEditLock.path == normalize_workspace_path(path),
        )
    )
    return result.scalar_one_or_none()


async def record_revision(
    db: AsyncSession,
    *,
    agent_id: uuid.UUID,
    path: str,
    operation: str,
    actor_type: str,
    actor_id: uuid.UUID | None,
    before_content: str | None,
    after_content: str | None,
    session_id: str | None = None,
    merge_user_autosave: bool = False,
) -> WorkspaceFileRevision | None:
    """Record a revision, optionally merging rapid user autosaves."""
    normalized = normalize_workspace_path(path)
    # PostgreSQL text columns cannot store NUL bytes. Treat such content as
    # non-text revision data so binary files can still be moved/deleted safely.
    before_content = before_content.replace("\x00", "") if before_content is not None else None
    after_content = after_content.replace("\x00", "") if after_content is not None else None
    before = before_content or ""
    after = after_content or ""
    if before == after and operation not in {"delete", "move_source", "move_destination"}:
        return None

    group_key = None
    if merge_user_autosave and actor_type == "user" and actor_id:
        group_key = f"user-autosave:{agent_id}:{normalized}:{actor_id}"
        cutoff = datetime.now(timezone.utc) - timedelta(seconds=USER_AUTOSAVE_MERGE_SECONDS)
        existing_result = await db.execute(
            select(WorkspaceFileRevision)
            .where(
                WorkspaceFileRevision.agent_id == agent_id,
                WorkspaceFileRevision.path == normalized,
                WorkspaceFileRevision.actor_type == "user",
                WorkspaceFileRevision.actor_id == actor_id,
                WorkspaceFileRevision.group_key == group_key,
                WorkspaceFileRevision.operation == "autosave",
                WorkspaceFileRevision.updated_at >= cutoff,
            )
            .order_by(desc(WorkspaceFileRevision.updated_at))
            .limit(1)
        )
        existing = existing_result.scalar_one_or_none()
        if existing:
            existing.after_content = after
            existing.content_hash = content_hash(after)
            existing.session_id = session_id or existing.session_id
            await db.flush()
            return existing

    revision = WorkspaceFileRevision(
        agent_id=agent_id,
        path=normalized,
        operation=operation,
        actor_type=actor_type,
        actor_id=actor_id,
        session_id=session_id,
        before_content=before_content,
        after_content=after_content,
        content_hash=content_hash(after_content),
        group_key=group_key,
    )
    db.add(revision)
    await db.flush()
    return revision


async def write_workspace_file(
    db: AsyncSession,
    *,
    agent_id: uuid.UUID,
    base_dir: Path,
    path: str,
    content: str,
    actor_type: str,
    actor_id: uuid.UUID | None,
    operation: str = "write",
    session_id: str | None = None,
    enforce_human_lock: bool = True,
    merge_user_autosave: bool = False,
) -> WorkspaceWriteResult:
    """Write text content, enforcing human locks for agent/system actors."""
    normalized = normalize_workspace_path(path)
    if not normalized:
        return WorkspaceWriteResult(False, normalized, "Missing file path")

    if enforce_human_lock and actor_type != "user":
        lock = await get_active_lock(db, agent_id=agent_id, path=normalized)
        if lock:
            return WorkspaceWriteResult(
                False,
                normalized,
                (
                    f"Human is currently editing {normalized}. Do not modify it now. "
                    "Ask the user to finish editing, or work on another file."
                ),
                locked_by_user_id=str(lock.user_id),
            )

    target = safe_agent_path(base_dir, normalized)
    before = await read_text_if_exists(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    async with aiofiles.open(target, "w", encoding="utf-8") as f:
        await f.write(content)

    revision = await record_revision(
        db,
        agent_id=agent_id,
        path=normalized,
        operation=operation,
        actor_type=actor_type,
        actor_id=actor_id,
        before_content=before,
        after_content=content,
        session_id=session_id,
        merge_user_autosave=merge_user_autosave,
    )
    return WorkspaceWriteResult(
        True,
        normalized,
        f"Written to {normalized} ({len(content)} chars)",
        revision_id=str(revision.id) if revision else None,
    )


async def delete_workspace_file(
    db: AsyncSession,
    *,
    agent_id: uuid.UUID,
    base_dir: Path,
    path: str,
    actor_type: str,
    actor_id: uuid.UUID | None,
    session_id: str | None = None,
    enforce_human_lock: bool = True,
) -> WorkspaceWriteResult:
    """Delete a workspace file and record the deleted content."""
    normalized = normalize_workspace_path(path)
    target = safe_agent_path(base_dir, normalized)
    if enforce_human_lock and actor_type != "user":
        lock = await get_active_lock(db, agent_id=agent_id, path=normalized)
        if lock:
            return WorkspaceWriteResult(
                False,
                normalized,
                f"Human is currently editing {normalized}. Do not delete it now.",
                locked_by_user_id=str(lock.user_id),
            )
    if not target.exists():
        return WorkspaceWriteResult(False, normalized, f"File not found: {normalized}")
    before = await read_text_if_exists(target)
    if target.is_dir():
        import shutil

        shutil.rmtree(target)
    else:
        target.unlink()
    revision = await record_revision(
        db,
        agent_id=agent_id,
        path=normalized,
        operation="delete",
        actor_type=actor_type,
        actor_id=actor_id,
        before_content=before,
        after_content=None,
        session_id=session_id,
    )
    return WorkspaceWriteResult(
        True,
        normalized,
        f"Deleted {normalized}",
        revision_id=str(revision.id) if revision else None,
    )


async def move_workspace_path(
    db: AsyncSession,
    *,
    agent_id: uuid.UUID,
    base_dir: Path,
    source_path: str,
    destination_path: str,
    actor_type: str,
    actor_id: uuid.UUID | None,
    session_id: str | None = None,
    enforce_human_lock: bool = True,
    overwrite: bool = False,
) -> WorkspaceWriteResult:
    """Move or rename a workspace file/folder while respecting edit locks."""
    source_normalized = normalize_workspace_path(source_path)
    destination_normalized = normalize_workspace_path(destination_path)
    if not source_normalized:
        return WorkspaceWriteResult(False, source_normalized, "Missing source path")
    if not destination_normalized:
        return WorkspaceWriteResult(False, destination_normalized, "Missing destination path")
    if source_normalized in {"tasks.json", "soul.md"}:
        return WorkspaceWriteResult(False, source_normalized, f"{source_normalized} cannot be moved (protected)")

    source = safe_agent_path(base_dir, source_normalized)
    if not source.exists():
        return WorkspaceWriteResult(False, source_normalized, f"File not found: {source_normalized}")

    destination = safe_agent_path(base_dir, destination_normalized)
    if destination_path.replace("\\", "/").strip().endswith("/") or destination.is_dir():
        destination = (destination / source.name).resolve()
        destination_normalized = normalize_workspace_path(str(destination.relative_to(base_dir.resolve())))
        destination = safe_agent_path(base_dir, destination_normalized)

    if source == destination:
        return WorkspaceWriteResult(False, source_normalized, "Source and destination are the same")
    if source.is_dir() and str(destination).startswith(str(source) + "/"):
        return WorkspaceWriteResult(False, source_normalized, "Cannot move a folder into itself")

    if enforce_human_lock and actor_type != "user":
        for locked_path in (source_normalized, destination_normalized):
            lock = await get_active_lock(db, agent_id=agent_id, path=locked_path)
            if lock:
                return WorkspaceWriteResult(
                    False,
                    locked_path,
                    (
                        f"Human is currently editing {locked_path}. Do not move it now. "
                        "Ask the user to finish editing, or choose another path."
                    ),
                    locked_by_user_id=str(lock.user_id),
                )

    if destination.exists():
        if not overwrite:
            return WorkspaceWriteResult(
                False,
                destination_normalized,
                f"Destination already exists: {destination_normalized}. Set overwrite=true to replace it.",
            )
        if destination.is_dir():
            shutil.rmtree(destination)
        else:
            destination.unlink()

    source_before = await read_text_if_exists(source)
    destination_before = await read_text_if_exists(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(source), str(destination))
    destination_after = await read_text_if_exists(destination)

    source_revision = await record_revision(
        db,
        agent_id=agent_id,
        path=source_normalized,
        operation="move_source",
        actor_type=actor_type,
        actor_id=actor_id,
        before_content=source_before,
        after_content=None,
        session_id=session_id,
    )
    destination_revision = await record_revision(
        db,
        agent_id=agent_id,
        path=destination_normalized,
        operation="move_destination",
        actor_type=actor_type,
        actor_id=actor_id,
        before_content=destination_before,
        after_content=destination_after,
        session_id=session_id,
    )
    revision = destination_revision or source_revision
    return WorkspaceWriteResult(
        True,
        destination_normalized,
        f"Moved {source_normalized} to {destination_normalized}",
        revision_id=str(revision.id) if revision else None,
    )


async def list_revisions(
    db: AsyncSession,
    *,
    agent_id: uuid.UUID,
    path: str,
    limit: int = 50,
) -> list[WorkspaceFileRevision]:
    """List recent revisions for one file."""
    result = await db.execute(
        select(WorkspaceFileRevision)
        .where(
            WorkspaceFileRevision.agent_id == agent_id,
            WorkspaceFileRevision.path == normalize_workspace_path(path),
        )
        .order_by(desc(WorkspaceFileRevision.created_at))
        .limit(min(max(limit, 1), 100))
    )
    return list(result.scalars().all())
