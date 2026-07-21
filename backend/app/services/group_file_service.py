"""Group-scoped announcement, memory, and workspace file operations.

Business callers use group-relative paths.  This module alone maps those paths
to storage keys so neither HTTP clients nor Runtime tools can address the
physical ``groups/{group_id}/...`` namespace directly.
"""

from __future__ import annotations

from dataclasses import dataclass
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.group import GroupMember
from app.models.participant import Participant
from app.services import group_chat_service
from app.services.storage import get_storage_backend, normalize_storage_key
from app.services.storage_runtime.base import StorageEntry, StorageVersion, WriteCondition
from app.services.workspace_collaboration import (
    content_hash,
    finalize_group_runtime_revision,
    get_group_runtime_revision,
    normalize_workspace_path,
    prepare_group_runtime_revision,
    record_group_revision,
)


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
class PreparedRuntimeWorkspaceOperation:
    """One committed intent that permits exactly one storage mutation."""

    group_id: uuid.UUID
    operation_id: uuid.UUID
    revision_id: uuid.UUID
    operation: str
    path: str
    storage_key: str
    before_content: str | None
    after_content: str | None
    condition: WriteCondition
    content_hash: str


@dataclass(frozen=True, slots=True)
class RuntimeWorkspaceOperationReceipt:
    """Stable bounded facts returned after mutation settlement or replay."""

    group_id: uuid.UUID
    operation_id: uuid.UUID
    revision_id: uuid.UUID
    operation: str
    path: str
    content_hash: str
    deleted: bool


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


async def _workspace_entry_version(
    storage,
    entry: StorageEntry,
    version: StorageVersion,
) -> str | None:
    """Resolve a stable token for files and marker-backed logical directories."""
    if version.exists:
        return version.token
    if entry.is_dir:
        marker = await storage.get_version(
            normalize_storage_key(f"{entry.key}/.gitkeep")
        )
        if marker.exists and not marker.is_dir:
            return marker.token
    return None


async def _workspace_directory_size(storage, directory_key: str) -> int:
    """Return the total byte size of every file below one logical directory."""
    total = 0
    pending = [directory_key]
    while pending:
        current = pending.pop()
        for entry in await storage.list_dir(current):
            if entry.name == ".gitkeep":
                continue
            if entry.is_dir:
                pending.append(entry.key)
            else:
                total += entry.size
    return total


def _validate_text(content: str) -> str:
    if "\x00" in content:
        raise GroupFileServiceError(
            "group_file_content_invalid",
            "Group text files cannot contain NUL bytes",
        )
    return content


def _runtime_revision_path(path: str) -> str:
    return _revision_path("workspace", path)


def _runtime_receipt(
    revision,
    *,
    operation_id: uuid.UUID,
) -> RuntimeWorkspaceOperationReceipt:
    prefix = "workspace/"
    if (
        revision.scope_type != "group"
        or not revision.path.startswith(prefix)
        or revision.id is None
        or revision.operation not in {"write", "delete"}
    ):
        raise GroupFileServiceError(
            "group_workspace_reconciliation_conflict",
            "Group workspace operation revision is not a committed file mutation",
        )
    return RuntimeWorkspaceOperationReceipt(
        group_id=revision.scope_id,
        operation_id=operation_id,
        revision_id=revision.id,
        operation=revision.operation,
        path=revision.path.removeprefix(prefix),
        content_hash=revision.content_hash,
        deleted=revision.operation == "delete",
    )


async def _prepare_runtime_workspace_operation(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    group_id: uuid.UUID,
    actor_participant_id: uuid.UUID,
    operation_id: uuid.UUID,
    path: str,
    operation: str,
    content: str | None,
    expected_version_token: str | None,
    session_id: uuid.UUID | None,
) -> PreparedRuntimeWorkspaceOperation:
    actor = await _authorize_actor(
        db,
        tenant_id=tenant_id,
        group_id=group_id,
        actor_participant_id=actor_participant_id,
    )
    normalized, key = _workspace_key(group_id, path, allow_empty=False)
    storage = get_storage_backend()
    current = await storage.get_version(key)
    if current.is_dir:
        raise GroupFileServiceError(
            "group_file_not_readable",
            "Group workspace mutation path is a directory",
        )
    if operation == "delete" and not current.exists:
        raise GroupFileServiceError("group_file_not_found", "Group file not found")
    if (
        expected_version_token is not None
        and current.token != expected_version_token
    ):
        raise GroupFileServiceError(
            "group_file_conflict",
            "Group file changed before this operation was prepared",
        )
    before = (
        await storage.read_text(key, encoding="utf-8", errors="replace")
        if current.exists
        else None
    )
    after = _validate_text(content) if operation == "write" and content is not None else None
    revision = await prepare_group_runtime_revision(
        db,
        group_id=group_id,
        operation_id=operation_id,
        path=_runtime_revision_path(normalized),
        operation=operation,
        actor_type=actor.type,
        actor_id=actor.ref_id,
        before_content=before,
        after_content=after,
        session_id=str(session_id) if session_id is not None else None,
    )
    if revision.id is None:  # pragma: no cover - explicit IDs are assigned above
        raise GroupFileServiceError(
            "group_workspace_operation_not_prepared",
            "Group workspace operation has no stable revision identity",
        )
    condition = (
        WriteCondition(version_token=current.token)
        if current.exists
        else WriteCondition(require_absent=True)
    )
    return PreparedRuntimeWorkspaceOperation(
        group_id=group_id,
        operation_id=operation_id,
        revision_id=revision.id,
        operation=operation,
        path=normalized,
        storage_key=key,
        before_content=before,
        after_content=after,
        condition=condition,
        content_hash=revision.content_hash,
    )


async def prepare_runtime_workspace_write(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    group_id: uuid.UUID,
    actor_participant_id: uuid.UUID,
    operation_id: uuid.UUID,
    path: str,
    content: str,
    expected_version_token: str | None = None,
    session_id: uuid.UUID | None = None,
) -> PreparedRuntimeWorkspaceOperation:
    """Commit a write intent before its one permitted storage CAS."""
    return await _prepare_runtime_workspace_operation(
        db,
        tenant_id=tenant_id,
        group_id=group_id,
        actor_participant_id=actor_participant_id,
        operation_id=operation_id,
        path=path,
        operation="write",
        content=content,
        expected_version_token=expected_version_token,
        session_id=session_id,
    )


async def prepare_runtime_workspace_delete(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    group_id: uuid.UUID,
    actor_participant_id: uuid.UUID,
    operation_id: uuid.UUID,
    path: str,
    expected_version_token: str | None = None,
    session_id: uuid.UUID | None = None,
) -> PreparedRuntimeWorkspaceOperation:
    """Commit a delete intent before its one permitted storage CAS."""
    return await _prepare_runtime_workspace_operation(
        db,
        tenant_id=tenant_id,
        group_id=group_id,
        actor_participant_id=actor_participant_id,
        operation_id=operation_id,
        path=path,
        operation="delete",
        content=None,
        expected_version_token=expected_version_token,
        session_id=session_id,
    )


async def apply_runtime_workspace_operation(
    prepared: PreparedRuntimeWorkspaceOperation,
) -> None:
    """Perform the sole CAS authorized by a committed prepared revision."""
    storage = get_storage_backend()
    if prepared.operation == "write":
        if prepared.after_content is None:
            raise GroupFileServiceError(
                "group_workspace_reconciliation_conflict",
                "Prepared Group write has no after-content",
            )
        result = await storage.write_bytes_if_match(
            prepared.storage_key,
            prepared.after_content.encode("utf-8"),
            condition=prepared.condition,
            content_type="text/plain; charset=utf-8",
        )
    elif prepared.operation == "delete":
        result = await storage.delete_if_match(
            prepared.storage_key,
            condition=prepared.condition,
        )
    else:  # pragma: no cover - constructed only by the helpers above
        raise GroupFileServiceError(
            "group_workspace_reconciliation_conflict",
            "Prepared Group workspace operation is unsupported",
        )
    if not result.ok:
        raise GroupFileServiceError(
            "group_file_conflict",
            "Group file changed before this operation completed",
        )


async def reconcile_runtime_workspace_operation(
    db: AsyncSession,
    *,
    group_id: uuid.UUID,
    operation_id: uuid.UUID,
) -> RuntimeWorkspaceOperationReceipt:
    """Forward-finalize proven storage state without repeating the mutation."""
    revision = await get_group_runtime_revision(
        db,
        group_id=group_id,
        operation_id=operation_id,
        lock=True,
    )
    if revision is None:
        raise GroupFileServiceError(
            "group_workspace_operation_not_prepared",
            "No prepared Group workspace operation exists for this Tool receipt",
        )
    if revision.operation in {"write", "delete"}:
        return _runtime_receipt(revision, operation_id=operation_id)
    if revision.operation not in {"prepared_write", "prepared_delete"}:
        raise GroupFileServiceError(
            "group_workspace_reconciliation_conflict",
            "Group workspace operation revision has a conflicting state",
        )
    if not revision.path.startswith("workspace/"):
        raise GroupFileServiceError(
            "group_workspace_reconciliation_conflict",
            "Group workspace operation revision has an invalid path",
        )

    operation = revision.operation.removeprefix("prepared_")
    relative_path = revision.path.removeprefix("workspace/")
    _, key = _workspace_key(group_id, relative_path, allow_empty=False)
    storage = get_storage_backend()
    current = await storage.get_version(key)
    proven = False
    if operation == "write" and current.exists and not current.is_dir:
        current_content = await storage.read_text(
            key,
            encoding="utf-8",
            errors="replace",
        )
        proven = content_hash(current_content) == revision.content_hash
    elif operation == "delete":
        proven = not current.exists
    if not proven:
        raise GroupFileServiceError(
            "group_workspace_reconciliation_conflict",
            "Current Group storage is neither the proven operation result nor a committed revision",
        )

    finalized = await finalize_group_runtime_revision(
        db,
        group_id=group_id,
        operation_id=operation_id,
        operation=operation,
    )
    return _runtime_receipt(finalized, operation_id=operation_id)


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
    require_absent: bool = False,
    session_id: uuid.UUID | None,
) -> GroupTextFile:
    storage = get_storage_backend()
    content = _validate_text(content)
    if require_absent and expected_version_token is not None:
        raise GroupFileServiceError(
            "group_file_write_condition_invalid",
            "A create-only write cannot also provide a version token",
        )
    current = await storage.get_version(key)
    if require_absent and current.exists:
        raise GroupFileServiceError(
            "group_file_conflict",
            "Group file already exists at this path",
        )
    before = (
        await storage.read_text(key, encoding="utf-8", errors="replace")
        if current.exists and not current.is_dir
        else None
    )
    result = await storage.write_bytes_if_match(
        key,
        content.encode("utf-8"),
        condition=(
            WriteCondition(require_absent=True)
            if require_absent
            else (
                WriteCondition(version_token=expected_version_token)
                if expected_version_token is not None
                else None
            )
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
    return await _write_text(
        db,
        group_id=group_id,
        key=_announcement_key(group_id),
        business_path="announcement.md",
        revision_path=_revision_path("announcement", ""),
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
    return await _write_text(
        db,
        group_id=group_id,
        key=_memory_key(group_id, agent_id),
        business_path="memory.md",
        revision_path=_revision_path(
            "memory",
            f"agents/{agent_id}/memory/memory.md",
        ),
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
    await _delete_text(
        db,
        group_id=group_id,
        key=_memory_key(group_id, agent_id),
        revision_path=_revision_path(
            "memory",
            f"agents/{agent_id}/memory/memory.md",
        ),
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
        # Local storage already hides folder markers; keep object-store listings
        # on the same logical workspace contract.
        if entry.name == ".gitkeep":
            continue
        version = await storage.get_version(entry.key)
        relative = normalize_storage_key(entry.key).removeprefix(prefix)
        output.append(
            GroupWorkspaceEntry(
                path=relative,
                name=entry.name,
                is_dir=entry.is_dir,
                size=(
                    await _workspace_directory_size(storage, entry.key)
                    if entry.is_dir
                    else version.size
                ),
                modified_at=version.modified_at,
                version_token=await _workspace_entry_version(storage, entry, version),
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
            if entry.name == ".gitkeep":
                continue
            version = await storage.get_version(entry.key)
            relative = normalize_storage_key(entry.key).removeprefix(prefix)
            output.append(
                GroupWorkspaceEntry(
                    path=relative,
                    name=entry.name,
                    is_dir=entry.is_dir,
                    size=version.size,
                    modified_at=version.modified_at,
                    version_token=await _workspace_entry_version(storage, entry, version),
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
    require_absent: bool = False,
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
    return await _write_text(
        db,
        group_id=group_id,
        key=key,
        business_path=normalized,
        revision_path=_revision_path("workspace", normalized),
        content=content,
        actor=actor,
        expected_version_token=expected_version_token,
        require_absent=require_absent,
        session_id=session_id,
    )


async def _delete_empty_workspace_directory(
    db: AsyncSession,
    *,
    group_id: uuid.UUID,
    key: str,
    normalized_path: str,
    actor: Participant,
    expected_version_token: str | None,
    session_id: uuid.UUID | None,
) -> None:
    """Delete an empty logical directory without recursively discarding files."""
    storage = get_storage_backend()
    entries = await storage.list_dir(key)
    non_marker_entries = [entry for entry in entries if entry.name != ".gitkeep"]
    if non_marker_entries:
        raise GroupFileServiceError(
            "group_workspace_directory_not_empty",
            "Delete the files inside this group workspace folder first",
        )

    current = await storage.get_version(key)
    marker_key = normalize_storage_key(f"{key}/.gitkeep")
    marker = await storage.get_version(marker_key)
    current_token = current.token if current.exists and current.is_dir else (
        marker.token if marker.exists and not marker.is_dir else None
    )
    if (
        expected_version_token is not None
        and current_token != expected_version_token
    ):
        raise GroupFileServiceError(
            "group_file_conflict",
            "Group folder changed before this delete completed",
        )

    if current.exists and current.is_dir:
        result = await storage.delete_if_match(
            key,
            condition=(
                WriteCondition(version_token=current_token)
                if current_token is not None
                else None
            ),
        )
    elif marker.exists and not marker.is_dir:
        # Object stores represent a folder only by its children. Delete only the
        # versioned marker; never call delete_tree, which could erase a file that
        # arrived after the emptiness check.
        result = await storage.delete_if_match(
            marker_key,
            condition=WriteCondition(version_token=marker.token),
        )
    else:
        raise GroupFileServiceError("group_file_not_found", "Group folder not found")
    if not result.ok:
        raise GroupFileServiceError(
            "group_file_conflict",
            "Group folder changed before this delete completed",
        )
    if await storage.is_dir(key):
        # A concurrent object-store write survives the marker delete and turns
        # the operation into a visible conflict instead of a false success.
        raise GroupFileServiceError(
            "group_file_conflict",
            "Group folder changed before this delete completed",
        )

    await record_group_revision(
        db,
        group_id=group_id,
        path=_revision_path("workspace", normalized_path),
        operation="delete",
        actor_type=actor.type,
        actor_id=actor.ref_id,
        before_content=None,
        after_content=None,
        session_id=str(session_id) if session_id is not None else None,
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
    """Delete one group workspace text file or empty directory."""
    actor = await _authorize_actor(
        db,
        tenant_id=tenant_id,
        group_id=group_id,
        actor_participant_id=actor_participant_id,
    )
    normalized, key = _workspace_key(group_id, path, allow_empty=False)
    storage = get_storage_backend()
    if await storage.is_dir(key):
        await _delete_empty_workspace_directory(
            db,
            group_id=group_id,
            key=key,
            normalized_path=normalized,
            actor=actor,
            expected_version_token=expected_version_token,
            session_id=session_id,
        )
        return
    await _delete_text(
        db,
        group_id=group_id,
        key=key,
        revision_path=_revision_path("workspace", normalized),
        actor=actor,
        expected_version_token=expected_version_token,
        session_id=session_id,
    )


__all__ = [
    "GroupFileServiceError",
    "GroupTextFile",
    "GroupWorkspaceEntry",
    "delete_agent_memory",
    "delete_workspace_file",
    "index_workspace",
    "list_workspace",
    "read_agent_memory",
    "read_announcement",
    "read_workspace_file",
    "write_agent_memory",
    "write_announcement",
    "write_workspace_file",
]
