"""Trusted scoped Workspace operations; storage owns visible file revisions."""

import hashlib
import json
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID, uuid4

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.infrastructure.errors import AccessDenied, Conflict, InvalidInput, NotFound
from app.infrastructure.object_storage.base import StorageBackend, StorageEntry, WriteCondition
from app.infrastructure.transactions import transaction
from app.modules.audit.public import AgentActor, AuditObservation, AuditSink
from app.modules.identity_tenant.public import TenantPrincipal
from app.modules.permission.public import PermissionService
from app.modules.workspace.files import (
    MAX_FILE_BYTES,
    MAX_INDEX_BYTES,
    FileMutationUncertain,
    WorkspaceUnavailable,
    ordinary_path,
    page_limit,
)
from app.modules.workspace.models import WorkspaceRecord
from app.modules.workspace.repository import WorkspaceRepository
from app.modules.workspace.skills import (
    EnabledSkillSources,
    PreparedSkillPackage,
    SharedSkillView,
    SkillBindingView,
    SkillContent,
    SkillDiscovery,
    SkillInstallScope,
    SkillOperations,
    SkillRemoval,
)

__all__ = [
    "ContentSearch",
    "DirectoryMember",
    "DirectoryMutationResult",
    "DirectoryPage",
    "DirectorySnapshot",
    "EnabledSkillSources",
    "FileConflict",
    "FileMutationUncertain",
    "FileView",
    "MemoryIndex",
    "MoveResult",
    "PreparedSkillPackage",
    "SharedSkillView",
    "SkillBindingView",
    "SkillContent",
    "SkillDiscovery",
    "SkillInstallScope",
    "SkillRemoval",
    "WorkspaceScope",
    "WorkspaceService",
    "WorkspaceSubject",
    "WorkspaceUnavailable",
    "WorkspaceView",
]

WorkspaceKind = Literal["membership", "agent", "group"]


@dataclass(frozen=True, slots=True)
class WorkspaceSubject:
    kind: WorkspaceKind
    id: UUID


@dataclass(frozen=True, slots=True)
class WorkspaceScope:
    """Intake-owned authorization, never parsed from model or HTTP JSON.

    Group intake supplies its captured Group subject. A2A constructs the target's
    own scope; it must not copy this object from the sender.
    """

    tenant_id: UUID
    agent_id: UUID
    output: WorkspaceSubject
    run_id: UUID | None = None
    main: bool = True
    preview_only: bool = False

    def for_subagent(self, run_id: UUID) -> "WorkspaceScope":
        return replace(self, run_id=run_id, main=False)


@dataclass(frozen=True, slots=True)
class WorkspaceView:
    id: UUID
    tenant_id: UUID
    subject: WorkspaceSubject


@dataclass(frozen=True, slots=True)
class FileView:
    path: str
    content: bytes
    revision: str


@dataclass(frozen=True, slots=True)
class DirectoryPage:
    entries: tuple[StorageEntry, ...]
    cursor: str | None


@dataclass(frozen=True, slots=True)
class DirectoryMember:
    path: str
    is_dir: bool
    revision: str | None
    size: int = 0


@dataclass(frozen=True, slots=True)
class DirectorySnapshot:
    """Bounded observed manifest, not an atomic multi-file snapshot."""

    path: str
    revision: str
    members: tuple[DirectoryMember, ...]


@dataclass(frozen=True, slots=True)
class DirectoryMutationResult:
    completed: bool
    copied_paths: tuple[str, ...] = ()
    deleted_paths: tuple[str, ...] = ()
    remaining_paths: tuple[str, ...] = ()
    uncertain_path: str | None = None
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class MemoryIndex:
    source: WorkspaceSubject
    path: str
    guide: str
    truncated: bool
    revision: str


@dataclass(frozen=True, slots=True)
class MoveResult:
    destination_revision: str
    source_deleted: bool
    source_current_revision: str | None = None
    source_error: str | None = None


@dataclass(frozen=True, slots=True)
class ContentSearch:
    path: str
    revision: str
    matches: tuple[tuple[int, str], ...]
    next_line: int | None


class FileConflict(Conflict):
    def __init__(self, current_revision: str | None) -> None:
        super().__init__("file changed; read the current content before retrying")
        self.current_revision = current_revision


class WorkspaceService(SkillOperations):
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        storage: StorageBackend,
        audit: AuditSink,
        *,
        enabled_skill_sources: EnabledSkillSources | None = None,
    ) -> None:
        self._sessions = sessions
        self._storage = storage
        self._audit = audit
        self._enabled_skill_sources = enabled_skill_sources

    async def direct_scope(
        self, principal: TenantPrincipal, *, agent_id: UUID, run_id: UUID | None = None
    ) -> WorkspaceScope:
        async with transaction(self._sessions) as tx:
            await PermissionService(tx).require_principal_access(principal, agent_id=agent_id)
        return WorkspaceScope(
            principal.tenant_id, agent_id, WorkspaceSubject("membership", principal.membership_id), run_id
        )

    def _authorize(self, scope: WorkspaceScope, subject: WorkspaceSubject, *, write: bool = False) -> None:
        own_agent = WorkspaceSubject("agent", scope.agent_id)
        if scope.output.kind == "agent" and scope.output != own_agent:
            raise AccessDenied("Agent-owned scope must refer to the executing Agent")
        if subject not in {scope.output, own_agent}:
            raise AccessDenied("Workspace is outside the resolved execution scope")
        if write and (scope.preview_only or subject != scope.output):
            raise AccessDenied("ordinary output belongs to the current destination Workspace")

    async def ensure(self, scope: WorkspaceScope, subject: WorkspaceSubject) -> WorkspaceView:
        self._authorize(scope, subject)
        now = datetime.now(UTC)
        async with transaction(self._sessions) as tx:
            repository = WorkspaceRepository(tx.session)
            await tx.session.execute(
                insert(WorkspaceRecord)
                .values(
                    id=uuid4(),
                    tenant_id=scope.tenant_id,
                    created_at=now,
                    updated_at=now,
                    membership_id=subject.id if subject.kind == "membership" else None,
                    agent_id=subject.id if subject.kind == "agent" else None,
                    group_id=subject.id if subject.kind == "group" else None,
                )
                .on_conflict_do_nothing()
            )
            record = await repository.workspace(scope.tenant_id, subject.kind, subject.id)
            if record is None:
                raise NotFound("Workspace could not be resolved")
            return WorkspaceView(record.id, scope.tenant_id, subject)

    async def _key(
        self,
        scope: WorkspaceScope,
        subject: WorkspaceSubject,
        path: str,
        *,
        write: bool = False,
        directory: bool = False,
    ) -> str:
        self._authorize(scope, subject, write=write)
        ordinary_path(path, directory=directory)
        async with transaction(self._sessions) as tx:
            record = await WorkspaceRepository(tx.session).workspace(scope.tenant_id, subject.kind, subject.id)
            if record is None:
                raise NotFound("Workspace has not been created")
            return f"workspaces/{scope.tenant_id}/{record.id}/{path}"

    async def read(self, scope: WorkspaceScope, subject: WorkspaceSubject, path: str) -> FileView:
        key = await self._key(scope, subject, path)
        try:
            content, version = await self._storage.read_versioned(key, max_bytes=MAX_FILE_BYTES)
        except FileNotFoundError:
            raise NotFound("file does not exist") from None
        except (IsADirectoryError, ValueError):
            raise InvalidInput("read requires a regular file within the 4 MiB bound") from None
        except OSError:
            raise WorkspaceUnavailable("file could not be read") from None
        return FileView(path, content, version.token)

    async def list(
        self,
        scope: WorkspaceScope,
        subject: WorkspaceSubject,
        path: str,
        *,
        limit: int = 100,
        cursor: str | None = None,
    ) -> DirectoryPage:
        page_limit(limit)
        key = await self._key(scope, subject, path, directory=True)
        try:
            entries, next_cursor = await self._storage.list_dir_page(key, limit=limit, cursor=cursor)
        except ValueError:
            raise InvalidInput("directory listing exceeds its bound or has an invalid cursor") from None
        except OSError:
            raise WorkspaceUnavailable("directory could not be listed") from None
        # Object keys are private adapter locations, not reusable authorization handles.
        relative = tuple(replace(entry, key=f"{path}/{entry.name}") for entry in entries)
        return DirectoryPage(relative, next_cursor)

    async def search(
        self,
        scope: WorkspaceScope,
        subject: WorkspaceSubject,
        path: str,
        *,
        query: str,
        limit: int = 100,
        cursor: str | None = None,
    ) -> DirectoryPage:
        if not query or len(query.encode()) > 256:
            raise InvalidInput("search query must contain 1 to 256 bytes")
        page = await self.list(scope, subject, path, limit=limit, cursor=cursor)
        return DirectoryPage(
            tuple(entry for entry in page.entries if query.casefold() in entry.name.casefold()), page.cursor
        )

    async def search_content(
        self,
        scope: WorkspaceScope,
        subject: WorkspaceSubject,
        path: str,
        *,
        query: str,
        start_line: int = 0,
        limit: int = 100,
    ) -> ContentSearch:
        page_limit(limit)
        if not query or len(query.encode()) > 256 or start_line < 0:
            raise InvalidInput("invalid content search bounds")
        file = await self.read(scope, subject, path)
        matches: list[tuple[int, str]] = []
        size = 0
        lines = file.content.decode("utf-8", errors="replace").splitlines()
        for index in range(start_line, len(lines)):
            line = lines[index]
            if query.casefold() not in line.casefold():
                continue
            excerpt = line.encode()[:1024].decode("utf-8", errors="ignore")
            entry_bytes = len(excerpt.encode()) + 32
            if len(matches) == limit or size + entry_bytes > MAX_INDEX_BYTES:
                return ContentSearch(path, file.revision, tuple(matches), index)
            matches.append((index, excerpt))
            size += entry_bytes
        return ContentSearch(path, file.revision, tuple(matches), None)

    async def write(
        self,
        scope: WorkspaceScope,
        subject: WorkspaceSubject,
        path: str,
        content: bytes,
        *,
        expected_revision: str | None,
    ) -> str:
        if len(content) > MAX_FILE_BYTES:
            raise InvalidInput("file exceeds the 4 MiB write bound")
        key = await self._key(scope, subject, path, write=True)
        try:
            result = await self._storage.write_bytes_if_match(
                key,
                content,
                condition=WriteCondition(version_token=expected_revision, require_absent=expected_revision is None),
            )
        except OSError:
            raise FileMutationUncertain(path) from None
        if not result.ok:
            raise FileConflict(
                result.current_version.token if result.current_version and result.current_version.exists else None
            )
        if result.current_version is None:
            raise RuntimeError("storage omitted the committed revision")
        self._observe(scope, "workspace.write", subject, path)
        return result.current_version.token

    async def delete(
        self, scope: WorkspaceScope, subject: WorkspaceSubject, path: str, *, expected_revision: str
    ) -> None:
        key = await self._key(scope, subject, path, write=True)
        if not expected_revision:
            raise InvalidInput("deletion requires the current revision")
        current = await self.read(scope, subject, path)
        if current.revision != expected_revision:
            raise FileConflict(current.revision)
        try:
            result = await self._storage.delete_if_match(key, condition=WriteCondition(version_token=expected_revision))
        except OSError:
            raise FileMutationUncertain(path) from None
        if not result.ok:
            raise FileConflict(
                result.current_version.token if result.current_version and result.current_version.exists else None
            )
        self._observe(scope, "workspace.delete", subject, path)

    async def mkdir(self, scope: WorkspaceScope, subject: WorkspaceSubject, path: str) -> None:
        key = await self._key(scope, subject, path, write=True, directory=True)
        if not path.startswith("files/"):
            raise AccessDenied("only ordinary file directories may be created")
        try:
            await self._storage.mkdir(key)
        except OSError:
            raise FileMutationUncertain(path) from None
        self._observe(scope, "workspace.mkdir", subject, path)

    async def inspect_directory(self, scope: WorkspaceScope, subject: WorkspaceSubject, path: str) -> DirectorySnapshot:
        """Inspect at most 128 members and 16 MiB; each file revision is coherent.

        Concurrent namespace changes may produce an observed manifest spanning
        several instants. Mutations still compare every captured file revision.
        """
        key = await self._key(scope, subject, path, directory=True)
        if not path.startswith("files/"):
            raise AccessDenied("directory operations require an ordinary files subdirectory")
        try:
            if not await self._storage.is_dir(key):
                raise NotFound("directory does not exist")
            members: list[DirectoryMember] = []
            pending = [""]
            seen: set[str] = set()
            total_bytes = 0
            scans = 0
            while pending:
                relative = pending.pop()
                directory_key = f"{key}/{relative}" if relative else key
                cursor = None
                while True:
                    scans += 1
                    if scans > 256:
                        raise InvalidInput("directory inspection exceeds its scan bound")
                    entries, cursor = await self._storage.list_dir_page(directory_key, limit=100, cursor=cursor)
                    for entry in entries:
                        child = f"{relative}/{entry.name}" if relative else entry.name
                        ordinary_path(f"{path}/{child}")
                        if child in seen:
                            raise FileConflict(None)
                        seen.add(child)
                        if len(seen) > 128 or child.count("/") > 16:
                            raise InvalidInput("directory inspection exceeds 128 members or 16 levels")
                        if entry.is_dir:
                            members.append(DirectoryMember(child, True, None))
                            pending.append(child)
                        else:
                            available = 16 * 1024 * 1024 - total_bytes
                            if available < 1:
                                raise InvalidInput("directory contents exceed 16 MiB")
                            content, version = await self._storage.read_versioned(
                                f"{key}/{child}", max_bytes=min(MAX_FILE_BYTES, available)
                            )
                            total_bytes += len(content)
                            members.append(DirectoryMember(child, False, version.token, len(content)))
                    if cursor is None:
                        break
            ordered = tuple(sorted(members, key=lambda member: member.path))
            encoded = json.dumps(
                [(member.path, member.is_dir, member.revision) for member in ordered], separators=(",", ":")
            ).encode()
            return DirectorySnapshot(path, hashlib.sha256(encoded).hexdigest(), ordered)
        except FileNotFoundError:
            raise FileConflict(None) from None
        except (ValueError, IsADirectoryError):
            raise InvalidInput("directory changed shape or exceeds its inspection bound") from None
        except OSError:
            raise WorkspaceUnavailable("directory could not be inspected") from None

    async def delete_directory(
        self, scope: WorkspaceScope, subject: WorkspaceSubject, path: str, *, expected_revision: str
    ) -> DirectoryMutationResult:
        self._authorize(scope, subject, write=True)
        snapshot = await self.inspect_directory(scope, subject, path)
        if snapshot.revision != expected_revision:
            raise FileConflict(snapshot.revision)
        key = await self._key(scope, subject, path, write=True, directory=True)
        return await self._delete_captured_directory(scope, subject, snapshot, key)

    async def _delete_captured_directory(
        self,
        scope: WorkspaceScope,
        subject: WorkspaceSubject,
        snapshot: DirectorySnapshot,
        key: str,
        *,
        copied_paths: tuple[str, ...] = (),
    ) -> DirectoryMutationResult:
        removed: list[str] = []
        remaining: list[str] = []
        files = [member for member in snapshot.members if not member.is_dir]
        for index, member in enumerate(files):
            try:
                result = await self._storage.delete_if_match(
                    f"{key}/{member.path}", condition=WriteCondition(version_token=member.revision)
                )
            except OSError:
                return DirectoryMutationResult(
                    False,
                    copied_paths,
                    tuple(removed),
                    tuple(remaining + [entry.path for entry in files[index:]]),
                    member.path,
                    "source deletion outcome is uncertain; inspect before retrying",
                )
            if result.ok or (result.current_version is not None and not result.current_version.exists):
                removed.append(member.path)
            else:
                remaining.append(member.path)
        directories = sorted(
            (member.path for member in snapshot.members if member.is_dir),
            key=lambda path: (path.count("/"), path),
            reverse=True,
        )
        for directory in [*directories, ""]:
            target = f"{key}/{directory}" if directory else key
            try:
                empty = await self._storage.rmdir_if_empty(target)
            except OSError:
                return DirectoryMutationResult(
                    False,
                    copied_paths,
                    tuple(removed),
                    tuple(remaining),
                    directory or snapshot.path,
                    "directory removal outcome is uncertain; inspect before retrying",
                )
            if not empty:
                remaining.append(directory or snapshot.path)
        completed = not remaining
        if completed:
            self._observe(scope, "workspace.delete_directory", subject, snapshot.path)
        return DirectoryMutationResult(
            completed,
            copied_paths,
            tuple(removed),
            tuple(remaining),
            reason=None if completed else "changed or newly created entries remain in the source directory",
        )

    async def move_directory(
        self,
        scope: WorkspaceScope,
        subject: WorkspaceSubject,
        source_path: str,
        destination_path: str,
        *,
        expected_revision: str,
    ) -> DirectoryMutationResult:
        """Move to an absent destination without overwrites; failures retain explicit partial facts."""
        self._authorize(scope, subject, write=True)
        if (
            source_path == destination_path
            or destination_path.startswith(source_path + "/")
            or source_path.startswith(destination_path + "/")
        ):
            raise InvalidInput("directory move paths must not overlap")
        source = await self.inspect_directory(scope, subject, source_path)
        if source.revision != expected_revision:
            raise FileConflict(source.revision)
        source_key = await self._key(scope, subject, source_path, write=True, directory=True)
        destination_key = await self._key(scope, subject, destination_path, write=True, directory=True)
        if not destination_path.startswith("files/"):
            raise AccessDenied("directory move destination must be an ordinary subdirectory")
        copied: list[str] = []
        written_revisions: dict[str, str] = {}
        try:
            if await self._storage.exists(destination_key):
                raise FileConflict(None)
            await self._storage.mkdir(destination_key)
            for member in source.members:
                if member.is_dir:
                    await self._storage.mkdir(f"{destination_key}/{member.path}")
                    continue
                content, version = await self._storage.read_versioned(
                    f"{source_key}/{member.path}", max_bytes=MAX_FILE_BYTES
                )
                if version.token != member.revision:
                    return DirectoryMutationResult(
                        False,
                        tuple(copied),
                        remaining_paths=tuple(entry.path for entry in source.members),
                        reason="source changed before copy; no source files removed",
                    )
                result = await self._storage.write_bytes_if_match(
                    f"{destination_key}/{member.path}", content, condition=WriteCondition(require_absent=True)
                )
                if not result.ok:
                    return DirectoryMutationResult(
                        False,
                        tuple(copied),
                        remaining_paths=tuple(entry.path for entry in source.members),
                        reason="destination changed during copy; no source files removed",
                    )
                copied.append(member.path)
                if result.current_version is None:
                    return DirectoryMutationResult(
                        False,
                        tuple(copied),
                        uncertain_path=member.path,
                        reason="destination revision was not confirmed; no source files removed",
                    )
                written_revisions[member.path] = result.current_version.token
        except (OSError, ValueError):
            return DirectoryMutationResult(
                False,
                tuple(copied),
                remaining_paths=tuple(entry.path for entry in source.members),
                uncertain_path=destination_path,
                reason="copy did not finish; inspect the destination before retrying; no source files removed",
            )
        try:
            destination = await self.inspect_directory(scope, subject, destination_path)
        except (NotFound, FileConflict, InvalidInput, WorkspaceUnavailable):
            return DirectoryMutationResult(
                False, tuple(copied), reason="destination could not be verified; no source files removed"
            )
        if {(entry.path, entry.is_dir) for entry in destination.members} != {
            (entry.path, entry.is_dir) for entry in source.members
        } or any(
            entry.revision != written_revisions.get(entry.path) for entry in destination.members if not entry.is_dir
        ):
            return DirectoryMutationResult(
                False, tuple(copied), reason="destination changed after copy; no source files removed"
            )
        return await self._delete_captured_directory(scope, subject, source, source_key, copied_paths=tuple(copied))

    async def copy(
        self,
        scope: WorkspaceScope,
        source: WorkspaceSubject,
        source_path: str,
        destination: WorkspaceSubject,
        destination_path: str,
        *,
        source_revision: str,
        destination_revision: str | None,
    ) -> str:
        self._authorize(scope, destination, write=True)
        content = await self.read(scope, source, source_path)
        if content.revision != source_revision:
            raise FileConflict(content.revision)
        return await self.write(
            scope, destination, destination_path, content.content, expected_revision=destination_revision
        )

    async def move(
        self,
        scope: WorkspaceScope,
        subject: WorkspaceSubject,
        source_path: str,
        destination_path: str,
        *,
        source_revision: str,
        destination_revision: str | None,
    ) -> MoveResult:
        self._authorize(scope, subject, write=True)
        if source_path == destination_path:
            raise InvalidInput("move source and destination must differ")
        revision = await self.copy(
            scope,
            subject,
            source_path,
            subject,
            destination_path,
            source_revision=source_revision,
            destination_revision=destination_revision,
        )
        try:
            await self.delete(scope, subject, source_path, expected_revision=source_revision)
        except FileConflict as error:
            return MoveResult(revision, False, error.current_revision)
        except NotFound:
            return MoveResult(revision, True)
        except (FileMutationUncertain, WorkspaceUnavailable):
            return MoveResult(
                revision, False, source_error="source deletion outcome is uncertain; inspect source before retrying"
            )
        return MoveResult(revision, True)

    async def memory_index(self, scope: WorkspaceScope, subject: WorkspaceSubject) -> MemoryIndex | None:
        try:
            file = await self.read(scope, subject, "memory/MEMORY.md")
        except NotFound:
            return None
        guide = file.content[:MAX_INDEX_BYTES].decode("utf-8", errors="ignore")
        return MemoryIndex(subject, file.path, guide, len(file.content) > MAX_INDEX_BYTES, file.revision)

    async def distill_memory(self, scope: WorkspaceScope, content: bytes, *, expected_revision: str | None) -> str:
        if not scope.main or scope.preview_only:
            raise AccessDenied("only Main may explicitly distill generalized Agent memory")
        agent = WorkspaceSubject("agent", scope.agent_id)
        revision = await self.write(
            replace(scope, output=agent), agent, "memory/MEMORY.md", content, expected_revision=expected_revision
        )
        self._observe(scope, "workspace.distill", agent, "memory/MEMORY.md")
        return revision

    def _observe(self, scope: WorkspaceScope, action: str, subject: WorkspaceSubject, path: str) -> None:
        self._audit.emit(
            AuditObservation(
                scope.tenant_id,
                AgentActor(scope.agent_id, scope.run_id),
                action,
                "workspace",
                str(subject.id),
                "succeeded",
                1,
                {"path": path, "source_kind": scope.output.kind, "source_id": str(scope.output.id)},
                datetime.now(UTC),
            )
        )
