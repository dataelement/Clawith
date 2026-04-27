"""Workspace collaboration helpers.

All user and agent writes should pass through this module so file history,
rollback, and human edit locks remain consistent across REST APIs and tools.
"""

from __future__ import annotations

import hashlib
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
    """Read a UTF-8 text file if it exists; return None for missing files."""
    if not path.exists() or not path.is_file():
        return None
    async with aiofiles.open(path, "r", encoding="utf-8", errors="replace") as f:
        return await f.read()


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
    before = before_content or ""
    after = after_content or ""
    if before == after and operation != "delete":
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
