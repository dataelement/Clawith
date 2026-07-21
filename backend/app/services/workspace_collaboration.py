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
from sqlalchemy import delete, desc, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.workspace import WorkspaceEditLock, WorkspaceFileRevision
from app.services.storage import get_storage_backend, normalize_storage_key
from app.services.storage_runtime.base import WriteCondition
from app.services.storage_runtime.local import LocalStorageBackend
from app.services.workspace_locking import workspace_locks

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
GROUP_RUNTIME_OPERATION_KEY_PREFIX = "runtime-operation:"
GROUP_RUNTIME_PREPARED_OPERATIONS = frozenset(
    {"prepared_write", "prepared_delete"}
)


@dataclass
class WorkspaceWriteResult:
    ok: bool
    path: str
    message: str
    revision_id: str | None = None
    locked_by_user_id: str | None = None


def _should_mirror_to_local_filesystem(storage) -> bool:
    """Only mirror writes into AGENT_DATA_DIR when the filesystem is the primary store."""
    return isinstance(storage, LocalStorageBackend)


def content_hash(content: str | None) -> str:
    """Return a stable hash for text content."""
    return hashlib.sha256((content or "").encode("utf-8")).hexdigest()


def group_runtime_operation_key(operation_id: uuid.UUID) -> str:
    """Map one Tool Ledger identity to its Group revision saga key."""
    if not isinstance(operation_id, uuid.UUID):
        raise ValueError("operation_id must be a UUID")
    return f"{GROUP_RUNTIME_OPERATION_KEY_PREFIX}{operation_id}"


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
            scope_type="agent",
            scope_id=agent_id,
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


async def _record_scoped_revision(
    db: AsyncSession,
    *,
    scope_type: str,
    scope_id: uuid.UUID,
    agent_id: uuid.UUID | None,
    path: str,
    operation: str,
    actor_type: str,
    actor_id: uuid.UUID | None,
    before_content: str | None,
    after_content: str | None,
    session_id: str | None = None,
    merge_user_autosave: bool = False,
) -> WorkspaceFileRevision | None:
    """Record one revision under the shared Agent/group workspace contract."""
    if scope_type == "agent":
        if agent_id is None or scope_id != agent_id:
            raise ValueError("agent workspace scope must match agent_id")
    elif scope_type == "group":
        if agent_id is not None:
            raise ValueError("group workspace scope cannot set agent_id")
    else:
        raise ValueError("scope_type must be 'agent' or 'group'")

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
        group_key = (
            f"user-autosave:{scope_id}:{normalized}:{actor_id}"
            if scope_type == "agent"
            else f"user-autosave:group:{scope_id}:{normalized}:{actor_id}"
        )
        cutoff = datetime.now(timezone.utc) - timedelta(seconds=USER_AUTOSAVE_MERGE_SECONDS)
        existing_result = await db.execute(
            select(WorkspaceFileRevision)
            .where(
                WorkspaceFileRevision.scope_type == scope_type,
                WorkspaceFileRevision.scope_id == scope_id,
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
        scope_type=scope_type,
        scope_id=scope_id,
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
    """Record a backward-compatible Agent workspace revision."""
    return await _record_scoped_revision(
        db,
        scope_type="agent",
        scope_id=agent_id,
        agent_id=agent_id,
        path=path,
        operation=operation,
        actor_type=actor_type,
        actor_id=actor_id,
        before_content=before_content,
        after_content=after_content,
        session_id=session_id,
        merge_user_autosave=merge_user_autosave,
    )


async def record_group_revision(
    db: AsyncSession,
    *,
    group_id: uuid.UUID,
    path: str,
    operation: str,
    actor_type: str,
    actor_id: uuid.UUID | None,
    before_content: str | None,
    after_content: str | None,
    session_id: str | None = None,
) -> WorkspaceFileRevision | None:
    """Record a group-scoped file revision without creating a second history table."""
    return await _record_scoped_revision(
        db,
        scope_type="group",
        scope_id=group_id,
        agent_id=None,
        path=path,
        operation=operation,
        actor_type=actor_type,
        actor_id=actor_id,
        before_content=before_content,
        after_content=after_content,
        session_id=session_id,
    )


async def get_group_runtime_revision(
    db: AsyncSession,
    *,
    group_id: uuid.UUID,
    operation_id: uuid.UUID,
    lock: bool = False,
) -> WorkspaceFileRevision | None:
    """Read the one revision saga owned by an AgentToolExecution identity."""
    statement = select(WorkspaceFileRevision).where(
        WorkspaceFileRevision.scope_type == "group",
        WorkspaceFileRevision.scope_id == group_id,
        WorkspaceFileRevision.group_key
        == group_runtime_operation_key(operation_id),
    )
    if lock:
        statement = statement.with_for_update()
    result = await db.execute(statement)
    return result.scalar_one_or_none()


async def prepare_group_runtime_revision(
    db: AsyncSession,
    *,
    group_id: uuid.UUID,
    operation_id: uuid.UUID,
    path: str,
    operation: str,
    actor_type: str,
    actor_id: uuid.UUID | None,
    before_content: str | None,
    after_content: str | None,
    session_id: str | None = None,
) -> WorkspaceFileRevision:
    """Persist the intent needed to reconcile one Group storage mutation.

    The prepared row is deliberately not a visible history event.  Its stable
    ``group_key`` is the Tool Ledger execution ID, so a process restart can
    prove the exact storage operation without issuing it again.
    """
    if operation not in {"write", "delete"}:
        raise ValueError("group runtime revision operation must be write or delete")
    normalized = normalize_workspace_path(path)
    if not normalized:
        raise ValueError("group runtime revision path must not be empty")
    prepared_operation = f"prepared_{operation}"

    def existing_or_conflict(
        existing: WorkspaceFileRevision | None,
    ) -> WorkspaceFileRevision | None:
        if existing is None:
            return None
        expected_operations = {prepared_operation, operation}
        exact = (
            existing.id == operation_id
            and existing.scope_type == "group"
            and existing.scope_id == group_id
            and existing.path == normalized
            and existing.operation in expected_operations
            and existing.actor_type == actor_type
            and existing.actor_id == actor_id
            and existing.before_content == before_content
            and existing.after_content == after_content
            and existing.content_hash == content_hash(after_content)
            and existing.session_id == session_id
        )
        if not exact:
            raise ValueError(
                "operation_id already belongs to a different Group revision"
            )
        return existing

    existing = existing_or_conflict(
        await get_group_runtime_revision(
            db,
            group_id=group_id,
            operation_id=operation_id,
            lock=True,
        )
    )
    if existing is not None:
        return existing

    revision = WorkspaceFileRevision(
        # AgentToolExecution.id is globally unique. Reusing it as the revision
        # primary key gives concurrent prepare calls a database-enforced gate
        # without another table, unique index, or migration.
        id=operation_id,
        agent_id=None,
        scope_type="group",
        scope_id=group_id,
        path=normalized,
        operation=prepared_operation,
        actor_type=actor_type,
        actor_id=actor_id,
        session_id=session_id,
        before_content=before_content,
        after_content=after_content,
        content_hash=content_hash(after_content),
        group_key=group_runtime_operation_key(operation_id),
    )
    try:
        async with db.begin_nested():
            db.add(revision)
            await db.flush()
        return revision
    except IntegrityError:
        concurrent = existing_or_conflict(
            await get_group_runtime_revision(
                db,
                group_id=group_id,
                operation_id=operation_id,
                lock=True,
            )
        )
        if concurrent is None:
            raise
        return concurrent


async def finalize_group_runtime_revision(
    db: AsyncSession,
    *,
    group_id: uuid.UUID,
    operation_id: uuid.UUID,
    operation: str,
) -> WorkspaceFileRevision:
    """Promote a proven prepared mutation to one visible history revision."""
    if operation not in {"write", "delete"}:
        raise ValueError("group runtime revision operation must be write or delete")
    revision = await get_group_runtime_revision(
        db,
        group_id=group_id,
        operation_id=operation_id,
        lock=True,
    )
    if revision is None:
        raise ValueError("group runtime revision is not prepared")
    if revision.operation == operation:
        return revision
    if revision.operation != f"prepared_{operation}":
        raise ValueError("group runtime revision has a conflicting operation")
    revision.operation = operation
    await db.flush()
    return revision


async def list_group_revisions(
    db: AsyncSession,
    *,
    group_id: uuid.UUID,
    path: str,
    limit: int = 50,
) -> list[WorkspaceFileRevision]:
    """List committed Group history without exposing prepared saga rows."""
    result = await db.execute(
        select(WorkspaceFileRevision)
        .where(
            WorkspaceFileRevision.scope_type == "group",
            WorkspaceFileRevision.scope_id == group_id,
            WorkspaceFileRevision.path == normalize_workspace_path(path),
            WorkspaceFileRevision.operation.not_in(
                GROUP_RUNTIME_PREPARED_OPERATIONS
            ),
        )
        .order_by(desc(WorkspaceFileRevision.created_at))
        .limit(min(max(limit, 1), 100))
    )
    return list(result.scalars().all())


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
    expected_version_token: str | None = None,
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

    storage = get_storage_backend()
    storage_key = normalize_storage_key(f"{agent_id}/{normalized}")
    local_base_available = _should_mirror_to_local_filesystem(storage)
    try:
        target = safe_agent_path(base_dir, normalized)
    except Exception:
        target = None
        local_base_available = False
    before = await storage.read_text(storage_key, encoding="utf-8", errors="replace") if await storage.exists(storage_key) else None
    write_result = await storage.write_bytes_if_match(
        storage_key,
        content.encode("utf-8"),
        condition=WriteCondition(version_token=expected_version_token) if expected_version_token is not None else None,
        content_type="text/plain; charset=utf-8",
    )
    if not write_result.ok:
        return WorkspaceWriteResult(False, normalized, f"Conflict detected while writing {normalized}")
    if local_base_available and target is not None:
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
    expected_version_token: str | None = None,
) -> WorkspaceWriteResult:
    """Delete a workspace file and record the deleted content."""
    normalized = normalize_workspace_path(path)
    storage = get_storage_backend()
    storage_key = normalize_storage_key(f"{agent_id}/{normalized}")
    target = None
    if _should_mirror_to_local_filesystem(storage):
        try:
            target = safe_agent_path(base_dir, normalized)
        except Exception:
            target = None
    if enforce_human_lock and actor_type != "user":
        lock = await get_active_lock(db, agent_id=agent_id, path=normalized)
        if lock:
            return WorkspaceWriteResult(
                False,
                normalized,
                f"Human is currently editing {normalized}. Do not delete it now.",
                locked_by_user_id=str(lock.user_id),
            )
    storage_exists = await storage.exists(storage_key)
    storage_is_dir = await storage.is_dir(storage_key)
    if not storage_exists and not storage_is_dir:
        return WorkspaceWriteResult(False, normalized, f"File not found: {normalized}")
    before = await storage.read_text(storage_key, encoding="utf-8", errors="replace") if storage_exists and await storage.is_file(storage_key) else None
    async with workspace_locks(agent_id, [normalized]):
        if storage_is_dir:
            entries = await _collect_storage_tree_versions(storage, storage_key)
            for entry_key, version_token in reversed(entries):
                delete_result = await storage.delete_if_match(
                    entry_key,
                    condition=WriteCondition(version_token=version_token),
                )
                if not delete_result.ok:
                    return WorkspaceWriteResult(False, normalized, f"Conflict detected while deleting {normalized}")
        else:
            delete_result = await storage.delete_if_match(
                storage_key,
                condition=WriteCondition(version_token=expected_version_token) if expected_version_token is not None else None,
            )
            if not delete_result.ok:
                return WorkspaceWriteResult(False, normalized, f"Conflict detected while deleting {normalized}")
    if target is not None and target.exists():
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
    expected_source_version_token: str | None = None,
    expected_destination_version_token: str | None = None,
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

    storage = get_storage_backend()
    source_key = normalize_storage_key(f"{agent_id}/{source_normalized}")
    source_exists = await storage.exists(source_key)
    source_is_dir = await storage.is_dir(source_key)
    if not source_exists and not source_is_dir:
        return WorkspaceWriteResult(False, source_normalized, f"File not found: {source_normalized}")

    destination_key = normalize_storage_key(f"{agent_id}/{destination_normalized}")
    destination_is_dir = await storage.is_dir(destination_key)
    if destination_path.replace("\\", "/").strip().endswith("/") or destination_is_dir:
        destination_normalized = normalize_workspace_path(f"{destination_normalized}/{Path(source_normalized).name}")
        destination_key = normalize_storage_key(f"{agent_id}/{destination_normalized}")

    if source_normalized == destination_normalized:
        return WorkspaceWriteResult(False, source_normalized, "Source and destination are the same")
    if source_is_dir and (destination_normalized == source_normalized or destination_normalized.startswith(source_normalized + "/")):
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

    destination_exists = await storage.exists(destination_key)
    destination_is_dir = await storage.is_dir(destination_key)
    async with workspace_locks(agent_id, [source_normalized, destination_normalized]):
        if destination_exists or destination_is_dir:
            if not overwrite:
                return WorkspaceWriteResult(
                    False,
                    destination_normalized,
                    f"Destination already exists: {destination_normalized}. Set overwrite=true to replace it.",
                )
            if destination_is_dir:
                await storage.delete_tree(destination_key)
            else:
                delete_result = await storage.delete_if_match(
                    destination_key,
                    condition=WriteCondition(version_token=expected_destination_version_token) if expected_destination_version_token is not None else None,
                )
                if not delete_result.ok:
                    return WorkspaceWriteResult(False, destination_normalized, f"Conflict detected while replacing {destination_normalized}")

        source = destination = None
        if _should_mirror_to_local_filesystem(storage):
            source = safe_agent_path(base_dir, source_normalized)
            destination = safe_agent_path(base_dir, destination_normalized)
        source_before = await storage.read_text(source_key, encoding="utf-8", errors="replace") if source_exists else None
        destination_before = await storage.read_text(destination_key, encoding="utf-8", errors="replace") if destination_exists else None

        if source_is_dir:
            entries = await _collect_storage_tree_versions(storage, source_key)
            for entry_key, version_token in entries:
                rel = entry_key.removeprefix(source_key.rstrip("/") + "/")
                target_key = normalize_storage_key(f"{agent_id}/{destination_normalized}/{rel}")
                current_version = await storage.get_version(entry_key)
                if current_version.token != version_token:
                    return WorkspaceWriteResult(False, source_normalized, f"Conflict detected while moving {source_normalized}")
                await storage.write_bytes(target_key, await storage.read_bytes(entry_key))
            for entry_key, version_token in reversed(entries):
                delete_result = await storage.delete_if_match(
                    entry_key,
                    condition=WriteCondition(version_token=version_token),
                )
                if not delete_result.ok:
                    return WorkspaceWriteResult(False, source_normalized, f"Conflict detected while finalizing move for {source_normalized}")
        else:
            source_version = await storage.get_version(source_key)
            if expected_source_version_token is not None and source_version.token != expected_source_version_token:
                return WorkspaceWriteResult(False, source_normalized, f"Conflict detected while moving {source_normalized}")
            await storage.write_bytes(destination_key, await storage.read_bytes(source_key))
            delete_result = await storage.delete_if_match(
                source_key,
                condition=WriteCondition(version_token=source_version.token),
            )
            if not delete_result.ok:
                return WorkspaceWriteResult(False, source_normalized, f"Conflict detected while finalizing move for {source_normalized}")

        destination_after = await storage.read_text(destination_key, encoding="utf-8", errors="replace") if await storage.is_file(destination_key) else None

        if source is not None and source.exists():
            if source.is_dir():
                shutil.rmtree(source)
            else:
                source.unlink()
        if destination is not None and await storage.is_file(destination_key):
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(await storage.read_bytes(destination_key))

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


async def _collect_storage_tree_versions(storage, root_key: str) -> list[tuple[str, str]]:
    keys: list[tuple[str, str]] = []
    for entry in await storage.list_dir(root_key):
        if entry.is_dir:
            keys.extend(await _collect_storage_tree_versions(storage, entry.key))
        else:
            version = await storage.get_version(entry.key)
            keys.append((entry.key, version.token))
    return keys


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
