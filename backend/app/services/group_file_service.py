"""Group-scoped announcement, memory, and workspace file operations.

Business callers use group-relative paths.  This module alone maps those paths
to storage keys so neither HTTP clients nor Runtime tools can address the
physical ``groups/{group_id}/...`` namespace directly.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import uuid

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import add_after_commit_callback, add_after_rollback_callback
from app.models.group import GroupMember
from app.models.participant import Participant
from app.services import group_chat_service
from app.services.storage import (
    get_storage_backend,
    guess_content_type,
    normalize_storage_key,
    sanitize_filename,
)
from app.services.storage_runtime.base import (
    StorageBackend,
    StorageEntry,
    StorageVersion,
    WriteCondition,
    content_hash_bytes,
)
from app.services.workspace_collaboration import (
    BINARY_REVISION_EXTENSIONS,
    MAX_REVISION_TEXT_BYTES,
    normalize_workspace_path,
    record_group_revision,
)
from app.services.workspace_locking import (
    acquire_group_workspace_mutation_lock,
    release_group_workspace_mutation_lock,
)

_GROUP_WORKSPACE_MUTATION_LOCKS_KEY = "group_workspace_mutation_locks"


class GroupFileServiceError(RuntimeError):
    """A group file request failed a stable validation or conflict check."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class GroupTextFile:
    """Business-level view of one group text file."""

    path: str
    content: str
    exists: bool
    version_token: str | None
    modified_at: str | None
    revision_id: uuid.UUID | None = None


@dataclass(frozen=True, slots=True)
class GroupWorkspaceEntry:
    """One immediate child in the group workspace."""

    path: str
    name: str
    is_dir: bool
    size: int
    modified_at: str
    version_token: str | None


@dataclass(frozen=True, slots=True)
class GroupWorkspaceBinaryFile:
    """Authorized storage projection for a binary workspace file."""

    path: str
    storage_key: str
    filename: str
    size: int
    content_type: str
    version_token: str | None
    revision_id: uuid.UUID | None = None


def _group_root(group_id: uuid.UUID) -> str:
    return normalize_storage_key(f"groups/{group_id}")


def _announcement_key(group_id: uuid.UUID) -> str:
    return normalize_storage_key(f"{_group_root(group_id)}/system/announcement.md")


def _memory_key(group_id: uuid.UUID, agent_id: uuid.UUID) -> str:
    return normalize_storage_key(
        f"{_group_root(group_id)}/agents/{agent_id}/memory/memory.md"
    )


def _normalize_workspace_relative(path: str, *, allow_empty: bool) -> str:
    raw = (path or "").replace("\\", "/").strip()
    if raw.startswith("/") or ".." in raw.split("/"):
        raise GroupFileServiceError(
            "group_workspace_path_invalid",
            "Group workspace paths must be relative and cannot contain '..'",
        )
    normalized = normalize_workspace_path(raw)
    if not allow_empty and not normalized:
        raise GroupFileServiceError(
            "group_workspace_path_invalid",
            "A group workspace file path is required",
        )
    return normalized


def _workspace_key(group_id: uuid.UUID, path: str, *, allow_empty: bool) -> tuple[str, str]:
    normalized = _normalize_workspace_relative(path, allow_empty=allow_empty)
    root = normalize_storage_key(f"{_group_root(group_id)}/workspace")
    return normalized, normalize_storage_key(f"{root}/{normalized}" if normalized else root)


def _revision_path(kind: str, path: str) -> str:
    if kind == "announcement":
        return "system/announcement.md"
    if kind == "memory":
        return path
    return f"workspace/{path}"


def _entry_version(entry: StorageEntry) -> str | None:
    return (
        entry.version_id
        or entry.etag
        or entry.content_hash
        or (f"{entry.modified_at}:{entry.size}" if entry.modified_at else None)
    )


def _validate_text(content: str) -> str:
    if "\x00" in content:
        raise GroupFileServiceError(
            "group_file_content_invalid",
            "Group text files cannot contain NUL bytes",
        )
    return content


async def _hold_workspace_mutation_lock(
    db: AsyncSession,
    *,
    group_id: uuid.UUID,
    path: str,
) -> None:
    """Hold one group path lock until this database transaction is finalized."""
    info = getattr(db, "info", None)
    if info is None:
        info = {}
        setattr(db, "info", info)
    lock_identity = (str(group_id), path)
    held_locks: dict[tuple[str, str], str] = info.setdefault(
        _GROUP_WORKSPACE_MUTATION_LOCKS_KEY,
        {},
    )
    if lock_identity in held_locks:
        return

    owner_token = uuid.uuid4().hex
    acquired = await acquire_group_workspace_mutation_lock(
        group_id,
        path,
        owner_token=owner_token,
    )
    if not acquired:
        if not held_locks:
            info.pop(_GROUP_WORKSPACE_MUTATION_LOCKS_KEY, None)
        raise GroupFileServiceError(
            "group_file_conflict",
            "Another group workspace mutation is already in progress",
        )

    held_locks[lock_identity] = owner_token
    released = False

    async def release() -> None:
        nonlocal released
        if released:
            return
        released = True
        try:
            await release_group_workspace_mutation_lock(
                group_id,
                path,
                owner_token=owner_token,
            )
        except Exception as exc:
            # The Redis TTL is the final safety net when an explicit release
            # cannot reach Redis. Do not make a durable DB commit look failed.
            logger.warning(
                "[GroupWorkspace] lock release failed; waiting for TTL: "
                f"group={group_id} path={path} error={exc}"
            )
        finally:
            current_locks = info.get(_GROUP_WORKSPACE_MUTATION_LOCKS_KEY)
            if current_locks is not None:
                if current_locks.get(lock_identity) == owner_token:
                    current_locks.pop(lock_identity, None)
                if not current_locks:
                    info.pop(_GROUP_WORKSPACE_MUTATION_LOCKS_KEY, None)

    # Rollback callbacks are LIFO. Register release first so any later storage
    # compensation completes while the cross-instance mutation lock is held.
    add_after_rollback_callback(db, release)
    add_after_commit_callback(db, release)


def _stage_upload_rollback(
    db: AsyncSession,
    *,
    storage: StorageBackend,
    key: str,
    previous_version: StorageVersion,
    previous_content: bytes | None,
    uploaded_version_token: str,
    previous_content_type: str,
) -> None:
    """Restore an upload only while storage still contains that exact version."""

    async def compensate() -> None:
        condition = WriteCondition(version_token=uploaded_version_token)
        if previous_version.exists:
            result = await storage.write_bytes_if_match(
                key,
                previous_content or b"",
                condition=condition,
                content_type=previous_content_type,
            )
        else:
            result = await storage.delete_if_match(key, condition=condition)
        if not result.ok:
            logger.warning(
                "[GroupWorkspace] skipped upload rollback because storage "
                f"version changed: {key}"
            )

    add_after_rollback_callback(db, compensate)


async def _authorize_actor(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    group_id: uuid.UUID,
    actor_participant_id: uuid.UUID,
    human_only: bool = False,
) -> Participant:
    _, _, participant = await group_chat_service.authorize_group_member(
        db,
        tenant_id=tenant_id,
        group_id=group_id,
        participant_id=actor_participant_id,
        human_only=human_only,
    )
    return participant


async def _active_agent_participant(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    group_id: uuid.UUID,
    agent_id: uuid.UUID,
) -> Participant:
    result = await db.execute(
        select(Participant)
        .join(GroupMember, GroupMember.participant_id == Participant.id)
        .where(
            Participant.type == "agent",
            Participant.ref_id == agent_id,
            GroupMember.group_id == group_id,
            GroupMember.removed_at.is_(None),
        )
    )
    participant = result.scalar_one_or_none()
    if participant is None:
        raise GroupFileServiceError(
            "group_agent_not_found",
            "Agent is not an active member of this group",
        )
    await group_chat_service.authorize_group_member(
        db,
        tenant_id=tenant_id,
        group_id=group_id,
        participant_id=participant.id,
    )
    return participant


async def _read_text(
    *,
    key: str,
    business_path: str,
    missing_is_empty: bool,
) -> GroupTextFile:
    storage = get_storage_backend()
    version = await storage.get_version(key)
    if not version.exists:
        if missing_is_empty:
            return GroupTextFile(
                path=business_path,
                content="",
                exists=False,
                version_token=None,
                modified_at=None,
            )
        raise GroupFileServiceError("group_file_not_found", "Group file not found")
    if version.is_dir:
        raise GroupFileServiceError("group_file_not_readable", "Path is a directory")
    return GroupTextFile(
        path=business_path,
        content=await storage.read_text(key, encoding="utf-8", errors="replace"),
        exists=True,
        version_token=version.token,
        modified_at=version.modified_at or None,
    )


async def _write_text(
    db: AsyncSession,
    *,
    group_id: uuid.UUID,
    key: str,
    business_path: str,
    revision_path: str,
    content: str,
    actor: Participant,
    expected_version_token: str | None,
    session_id: uuid.UUID | None,
) -> GroupTextFile:
    storage = get_storage_backend()
    content = _validate_text(content)
    current = await storage.get_version(key)
    before = (
        await storage.read_text(key, encoding="utf-8", errors="replace")
        if current.exists and not current.is_dir
        else None
    )
    result = await storage.write_bytes_if_match(
        key,
        content.encode("utf-8"),
        condition=(
            WriteCondition(version_token=expected_version_token)
            if expected_version_token is not None
            else None
        ),
        content_type="text/plain; charset=utf-8",
    )
    if not result.ok:
        raise GroupFileServiceError(
            "group_file_conflict",
            "Group file changed before this write completed",
        )
    revision = await record_group_revision(
        db,
        group_id=group_id,
        path=revision_path,
        operation="write",
        actor_type=actor.type,
        actor_id=actor.ref_id,
        before_content=before,
        after_content=content,
        session_id=str(session_id) if session_id is not None else None,
    )
    updated = result.current_version or await storage.get_version(key)
    return GroupTextFile(
        path=business_path,
        content=content,
        exists=True,
        version_token=updated.token,
        modified_at=updated.modified_at or None,
        revision_id=revision.id if revision is not None else None,
    )


async def _delete_text(
    db: AsyncSession,
    *,
    group_id: uuid.UUID,
    key: str,
    revision_path: str,
    actor: Participant,
    expected_version_token: str | None,
    session_id: uuid.UUID | None,
) -> None:
    storage = get_storage_backend()
    current = await storage.get_version(key)
    if not current.exists or current.is_dir:
        raise GroupFileServiceError("group_file_not_found", "Group file not found")
    before = await storage.read_text(key, encoding="utf-8", errors="replace")
    result = await storage.delete_if_match(
        key,
        condition=(
            WriteCondition(version_token=expected_version_token)
            if expected_version_token is not None
            else None
        ),
    )
    if not result.ok:
        raise GroupFileServiceError(
            "group_file_conflict",
            "Group file changed before this delete completed",
        )
    await record_group_revision(
        db,
        group_id=group_id,
        path=revision_path,
        operation="delete",
        actor_type=actor.type,
        actor_id=actor.ref_id,
        before_content=before,
        after_content=None,
        session_id=str(session_id) if session_id is not None else None,
    )


def _safe_revision_text(path: str, content: bytes) -> str | None:
    if (
        Path(path).suffix.lower() in BINARY_REVISION_EXTENSIONS
        or len(content) > MAX_REVISION_TEXT_BYTES
        or b"\x00" in content
    ):
        return None
    try:
        return content.decode("utf-8")
    except UnicodeDecodeError:
        return None


async def read_announcement(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    group_id: uuid.UUID,
    actor_participant_id: uuid.UUID,
) -> GroupTextFile:
    """Read the current announcement as any active group member."""
    await _authorize_actor(
        db,
        tenant_id=tenant_id,
        group_id=group_id,
        actor_participant_id=actor_participant_id,
    )
    return await _read_text(
        key=_announcement_key(group_id),
        business_path="announcement.md",
        missing_is_empty=True,
    )


async def write_announcement(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    group_id: uuid.UUID,
    actor_participant_id: uuid.UUID,
    content: str,
    expected_version_token: str | None = None,
) -> GroupTextFile:
    """Write the announcement as a human member; Agents are always read-only."""
    actor = await _authorize_actor(
        db,
        tenant_id=tenant_id,
        group_id=group_id,
        actor_participant_id=actor_participant_id,
        human_only=True,
    )
    revision_path = _revision_path("announcement", "")
    await _hold_workspace_mutation_lock(
        db,
        group_id=group_id,
        path=revision_path,
    )
    return await _write_text(
        db,
        group_id=group_id,
        key=_announcement_key(group_id),
        business_path="announcement.md",
        revision_path=revision_path,
        content=content,
        actor=actor,
        expected_version_token=expected_version_token,
        session_id=None,
    )


async def read_agent_memory(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    group_id: uuid.UUID,
    actor_participant_id: uuid.UUID,
    agent_id: uuid.UUID,
) -> GroupTextFile:
    """Read one active member Agent's memory as any active group member."""
    await _authorize_actor(
        db,
        tenant_id=tenant_id,
        group_id=group_id,
        actor_participant_id=actor_participant_id,
    )
    await _active_agent_participant(
        db,
        tenant_id=tenant_id,
        group_id=group_id,
        agent_id=agent_id,
    )
    return await _read_text(
        key=_memory_key(group_id, agent_id),
        business_path="memory.md",
        missing_is_empty=True,
    )


async def write_agent_memory(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    group_id: uuid.UUID,
    actor_participant_id: uuid.UUID,
    agent_id: uuid.UUID,
    content: str,
    expected_version_token: str | None = None,
    session_id: uuid.UUID | None = None,
) -> GroupTextFile:
    """Write any Agent memory as a human, or only the actor's own as an Agent."""
    actor = await _authorize_actor(
        db,
        tenant_id=tenant_id,
        group_id=group_id,
        actor_participant_id=actor_participant_id,
    )
    await _active_agent_participant(
        db,
        tenant_id=tenant_id,
        group_id=group_id,
        agent_id=agent_id,
    )
    if actor.type == "agent" and actor.ref_id != agent_id:
        raise GroupFileServiceError(
            "group_memory_write_denied",
            "An Agent can only write its own memory for this group",
        )
    revision_path = _revision_path(
        "memory",
        f"agents/{agent_id}/memory/memory.md",
    )
    await _hold_workspace_mutation_lock(
        db,
        group_id=group_id,
        path=revision_path,
    )
    return await _write_text(
        db,
        group_id=group_id,
        key=_memory_key(group_id, agent_id),
        business_path="memory.md",
        revision_path=revision_path,
        content=content,
        actor=actor,
        expected_version_token=expected_version_token,
        session_id=session_id,
    )


async def delete_agent_memory(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    group_id: uuid.UUID,
    actor_participant_id: uuid.UUID,
    agent_id: uuid.UUID,
    expected_version_token: str | None = None,
) -> None:
    """Delete one Agent memory as a human group member."""
    actor = await _authorize_actor(
        db,
        tenant_id=tenant_id,
        group_id=group_id,
        actor_participant_id=actor_participant_id,
        human_only=True,
    )
    await _active_agent_participant(
        db,
        tenant_id=tenant_id,
        group_id=group_id,
        agent_id=agent_id,
    )
    revision_path = _revision_path(
        "memory",
        f"agents/{agent_id}/memory/memory.md",
    )
    await _hold_workspace_mutation_lock(
        db,
        group_id=group_id,
        path=revision_path,
    )
    await _delete_text(
        db,
        group_id=group_id,
        key=_memory_key(group_id, agent_id),
        revision_path=revision_path,
        actor=actor,
        expected_version_token=expected_version_token,
        session_id=None,
    )


async def list_workspace(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    group_id: uuid.UUID,
    actor_participant_id: uuid.UUID,
    path: str = "",
) -> tuple[GroupWorkspaceEntry, ...]:
    """List immediate children under one group-relative workspace directory."""
    await _authorize_actor(
        db,
        tenant_id=tenant_id,
        group_id=group_id,
        actor_participant_id=actor_participant_id,
    )
    normalized, key = _workspace_key(group_id, path, allow_empty=True)
    storage = get_storage_backend()
    if await storage.is_file(key):
        raise GroupFileServiceError(
            "group_workspace_path_invalid",
            "Workspace list path must be a directory",
        )
    prefix = normalize_storage_key(f"{_group_root(group_id)}/workspace").rstrip("/") + "/"
    output = []
    for entry in await storage.list_dir(key):
        relative = normalize_storage_key(entry.key).removeprefix(prefix)
        output.append(
            GroupWorkspaceEntry(
                path=relative,
                name=entry.name,
                is_dir=entry.is_dir,
                size=entry.size,
                modified_at=entry.modified_at,
                version_token=_entry_version(entry),
            )
        )
    return tuple(output)


async def index_workspace(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    group_id: uuid.UUID,
    actor_participant_id: uuid.UUID,
    limit: int = 100,
) -> tuple[GroupWorkspaceEntry, ...]:
    """Build a bounded recursive workspace index for one immutable Run snapshot."""
    if limit <= 0:
        raise ValueError("limit must be positive")
    await _authorize_actor(
        db,
        tenant_id=tenant_id,
        group_id=group_id,
        actor_participant_id=actor_participant_id,
    )
    storage = get_storage_backend()
    root = normalize_storage_key(f"{_group_root(group_id)}/workspace")
    prefix = root.rstrip("/") + "/"
    pending = [root]
    output: list[GroupWorkspaceEntry] = []
    while pending and len(output) < limit:
        current = pending.pop(0)
        for entry in await storage.list_dir(current):
            relative = normalize_storage_key(entry.key).removeprefix(prefix)
            output.append(
                GroupWorkspaceEntry(
                    path=relative,
                    name=entry.name,
                    is_dir=entry.is_dir,
                    size=entry.size,
                    modified_at=entry.modified_at,
                    version_token=_entry_version(entry),
                )
            )
            if entry.is_dir:
                pending.append(entry.key)
            if len(output) >= limit:
                break
    return tuple(output)


async def read_workspace_file(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    group_id: uuid.UUID,
    actor_participant_id: uuid.UUID,
    path: str,
) -> GroupTextFile:
    """Read one text file from the ordinary group workspace namespace."""
    await _authorize_actor(
        db,
        tenant_id=tenant_id,
        group_id=group_id,
        actor_participant_id=actor_participant_id,
    )
    normalized, key = _workspace_key(group_id, path, allow_empty=False)
    return await _read_text(
        key=key,
        business_path=normalized,
        missing_is_empty=False,
    )


async def write_workspace_file(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    group_id: uuid.UUID,
    actor_participant_id: uuid.UUID,
    path: str,
    content: str,
    expected_version_token: str | None = None,
    session_id: uuid.UUID | None = None,
) -> GroupTextFile:
    """Create or replace one group workspace text file."""
    actor = await _authorize_actor(
        db,
        tenant_id=tenant_id,
        group_id=group_id,
        actor_participant_id=actor_participant_id,
    )
    normalized, key = _workspace_key(group_id, path, allow_empty=False)
    await _hold_workspace_mutation_lock(
        db,
        group_id=group_id,
        path=normalized,
    )
    return await _write_text(
        db,
        group_id=group_id,
        key=key,
        business_path=normalized,
        revision_path=_revision_path("workspace", normalized),
        content=content,
        actor=actor,
        expected_version_token=expected_version_token,
        session_id=session_id,
    )


async def upload_workspace_file(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    group_id: uuid.UUID,
    actor_participant_id: uuid.UUID,
    directory: str,
    filename: str,
    content: bytes,
    content_type: str | None = None,
    session_id: uuid.UUID | None = None,
) -> GroupWorkspaceBinaryFile:
    """Create or replace one binary file in the shared group workspace."""
    actor = await _authorize_actor(
        db,
        tenant_id=tenant_id,
        group_id=group_id,
        actor_participant_id=actor_participant_id,
    )
    normalized_directory = _normalize_workspace_relative(directory, allow_empty=True)
    safe_name = sanitize_filename(filename)
    if safe_name in {".", ".."}:
        raise GroupFileServiceError(
            "group_workspace_path_invalid",
            "A valid upload filename is required",
        )
    business_path = (
        f"{normalized_directory}/{safe_name}" if normalized_directory else safe_name
    )
    normalized, key = _workspace_key(group_id, business_path, allow_empty=False)
    await _hold_workspace_mutation_lock(
        db,
        group_id=group_id,
        path=normalized,
    )
    storage = get_storage_backend()
    media_type = content_type or guess_content_type(safe_name)
    previous_version = await storage.get_version(key)
    if previous_version.exists and previous_version.is_dir:
        raise GroupFileServiceError(
            "group_file_conflict",
            "A directory already exists at the requested upload path",
        )
    previous_content = (
        await storage.read_bytes(key) if previous_version.exists else None
    )
    result = await storage.write_bytes_if_match(
        key,
        content,
        condition=(
            WriteCondition(version_token=previous_version.token)
            if previous_version.exists
            else WriteCondition(require_absent=True)
        ),
        content_type=media_type,
    )
    if not result.ok:
        raise GroupFileServiceError(
            "group_file_conflict",
            "Group file changed before this upload completed",
        )
    updated = result.current_version or await storage.get_version(key)
    _stage_upload_rollback(
        db,
        storage=storage,
        key=key,
        previous_version=previous_version,
        previous_content=previous_content,
        uploaded_version_token=updated.token,
        previous_content_type=guess_content_type(safe_name),
    )
    revision = await record_group_revision(
        db,
        group_id=group_id,
        path=_revision_path("workspace", normalized),
        operation="upload",
        actor_type=actor.type,
        actor_id=actor.ref_id,
        before_content=None,
        after_content=None,
        content_hash_override=content_hash_bytes(content),
        session_id=str(session_id) if session_id is not None else None,
    )
    return GroupWorkspaceBinaryFile(
        path=normalized,
        storage_key=key,
        filename=Path(normalized).name,
        size=len(content),
        content_type=media_type,
        version_token=updated.token,
        revision_id=revision.id if revision is not None else None,
    )


async def prepare_workspace_download(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    group_id: uuid.UUID,
    actor_participant_id: uuid.UUID,
    path: str,
) -> GroupWorkspaceBinaryFile:
    """Authorize and resolve one group file before serving its raw bytes."""
    await _authorize_actor(
        db,
        tenant_id=tenant_id,
        group_id=group_id,
        actor_participant_id=actor_participant_id,
    )
    normalized, key = _workspace_key(group_id, path, allow_empty=False)
    storage = get_storage_backend()
    version = await storage.get_version(key)
    if not version.exists or version.is_dir:
        raise GroupFileServiceError("group_file_not_found", "Group file not found")
    filename = Path(normalized).name
    return GroupWorkspaceBinaryFile(
        path=normalized,
        storage_key=key,
        filename=filename,
        size=version.size,
        content_type=guess_content_type(filename),
        version_token=version.token,
    )


async def delete_workspace_file(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    group_id: uuid.UUID,
    actor_participant_id: uuid.UUID,
    path: str,
    expected_version_token: str | None = None,
    session_id: uuid.UUID | None = None,
) -> None:
    """Delete one ordinary group workspace file."""
    actor = await _authorize_actor(
        db,
        tenant_id=tenant_id,
        group_id=group_id,
        actor_participant_id=actor_participant_id,
    )
    normalized, key = _workspace_key(group_id, path, allow_empty=False)
    await _hold_workspace_mutation_lock(
        db,
        group_id=group_id,
        path=normalized,
    )
    storage = get_storage_backend()
    current = await storage.get_version(key)
    if not current.exists or current.is_dir:
        raise GroupFileServiceError("group_file_not_found", "Group file not found")
    content = await storage.read_bytes(key)
    result = await storage.delete_if_match(
        key,
        condition=(
            WriteCondition(version_token=expected_version_token)
            if expected_version_token is not None
            else None
        ),
    )
    if not result.ok:
        raise GroupFileServiceError(
            "group_file_conflict",
            "Group file changed before this delete completed",
        )
    before_text = _safe_revision_text(normalized, content)
    await record_group_revision(
        db,
        group_id=group_id,
        path=_revision_path("workspace", normalized),
        operation="delete",
        actor_type=actor.type,
        actor_id=actor.ref_id,
        before_content=before_text,
        after_content=None,
        content_hash_override=(
            content_hash_bytes(content) if before_text is None else None
        ),
        session_id=str(session_id) if session_id is not None else None,
    )


__all__ = [
    "GroupFileServiceError",
    "GroupTextFile",
    "GroupWorkspaceBinaryFile",
    "GroupWorkspaceEntry",
    "delete_agent_memory",
    "delete_workspace_file",
    "index_workspace",
    "list_workspace",
    "prepare_workspace_download",
    "read_agent_memory",
    "read_announcement",
    "read_workspace_file",
    "write_agent_memory",
    "write_announcement",
    "write_workspace_file",
    "upload_workspace_file",
]
