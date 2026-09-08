"""Complete Skill publication and reader-safe current package loads."""

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.infrastructure.errors import AccessDenied, Conflict, InvalidInput, NotFound
from app.infrastructure.object_storage.base import StorageBackend, WriteCondition
from app.infrastructure.transactions import TransactionContext, transaction
from app.modules.agent.public import AgentService
from app.modules.audit.public import AgentActor, AuditObservation, AuditSink, MembershipActor
from app.modules.identity_tenant.public import TenantPrincipal, require_admin
from app.modules.permission.public import PermissionService
from app.modules.workspace.files import (
    MAX_FILE_BYTES,
    MAX_PACKAGE_BYTES,
    MAX_PACKAGE_MEMBERS,
    WorkspaceUnavailable,
    relative_path,
)
from app.modules.workspace.models import AgentSkillBindingRecord, SkillPackageRecord
from app.modules.workspace.repository import WorkspaceRepository


@dataclass(frozen=True, slots=True)
class PreparedSkillPackage:
    """Opaque preparation handle returned only by controlled package validation."""

    storage_key: str
    content_hash: str
    revision: str


@dataclass(frozen=True, slots=True)
class SkillInstallScope:
    """Trusted self-install capability injected by the controlled executor."""

    tenant_id: UUID
    agent_id: UUID


@dataclass(frozen=True, slots=True)
class SkillBindingView:
    agent_id: UUID
    skill_name: str
    package_id: UUID
    shared: bool
    revision: str
    cleanup_pending: bool = False


@dataclass(frozen=True, slots=True)
class SkillDiscovery:
    """Run-fixed identities; package content is resolved at each explicit load."""

    tenant_id: UUID
    agent_id: UUID
    skills: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class SkillContent:
    name: str
    revision: str
    members: Mapping[str, bytes]


@dataclass(frozen=True, slots=True)
class SkillRemoval:
    cleanup_pending: bool = False


@dataclass(frozen=True, slots=True)
class SharedSkillView:
    package_id: UUID
    revision: str
    cleanup_pending: bool = False


class EnabledSkillSources(Protocol):
    async def __call__(
        self, *, transaction_context: TransactionContext, tenant_id: UUID, requested_ids: frozenset[UUID]
    ) -> frozenset[UUID]: ...


class SkillPublicationGuard(Protocol):
    async def __call__(self, transaction_context: TransactionContext, *, tenant_id: UUID,
        catalog_item_id: UUID) -> None: ...


async def _check_publication_source(guard: SkillPublicationGuard | None, tx: TransactionContext,
        tenant_id: UUID, catalog_item_id: UUID | None) -> None:
    if catalog_item_id is not None:
        if guard is None:
            raise InvalidInput("Catalog-backed Skill publication requires its source guard")
        await guard(tx, tenant_id=tenant_id, catalog_item_id=catalog_item_id)


class SkillOperations:
    _sessions: async_sessionmaker[AsyncSession]
    _storage: StorageBackend
    _audit: AuditSink
    _enabled_skill_sources: EnabledSkillSources | None

    async def prepare_skill_package(self, members: Mapping[str, bytes]) -> PreparedSkillPackage:
        """Validate the whole bounded package before writing an unpublished prefix."""
        if not 1 <= len(members) <= MAX_PACKAGE_MEMBERS or "SKILL.md" not in members:
            raise InvalidInput("Skill requires SKILL.md and at most 128 package members")
        validated: dict[str, bytes] = {}
        size = 0
        for name, content in members.items():
            relative_path(name)
            if name == ".manifest.json" or name.startswith("."):
                raise InvalidInput("reserved Skill member path")
            if len(content) > MAX_FILE_BYTES:
                raise InvalidInput("Skill member exceeds 4 MiB")
            size += len(content) + len(name.encode())
            if size > MAX_PACKAGE_BYTES:
                raise InvalidInput("Skill package exceeds 16 MiB")
            validated[name] = bytes(content)
        for name in validated:
            if any(name.startswith(other + "/") for other in validated):
                raise InvalidInput("Skill member conflicts with a directory")
        try:
            instructions = validated["SKILL.md"].decode("utf-8")
        except UnicodeDecodeError:
            raise InvalidInput("SKILL.md must be UTF-8") from None
        if not instructions.strip():
            raise InvalidInput("SKILL.md must contain instructions")
        hashes = {name: hashlib.sha256(content).hexdigest() for name, content in sorted(validated.items())}
        manifest = json.dumps({"version": 1, "members": hashes}, sort_keys=True, separators=(",", ":")).encode()
        if size + len(manifest) > MAX_PACKAGE_BYTES:
            raise InvalidInput("complete Skill package exceeds 16 MiB")
        revision = uuid4().hex
        prefix = f"skill-packages/prepared/{revision}"
        try:
            for name, content in validated.items():
                result = await self._storage.write_bytes_if_match(
                    f"{prefix}/{name}", content, condition=WriteCondition(require_absent=True)
                )
                if not result.ok:
                    raise Conflict("Skill preparation location already exists")
            result = await self._storage.write_bytes_if_match(
                f"{prefix}/.manifest.json", manifest, condition=WriteCondition(require_absent=True)
            )
            if not result.ok:
                raise Conflict("Skill manifest preparation conflict")
        except BaseException:
            await self._storage.delete_tree(prefix)
            raise
        return PreparedSkillPackage(prefix, hashlib.sha256(manifest).hexdigest(), revision)

    async def discard_prepared_skill(self, prepared: PreparedSkillPackage) -> None:
        self._validate_handle(prepared)
        async with self._storage.resource_lock(f"skill-preparation/{prepared.revision}"):
            async with transaction(self._sessions) as tx:
                published = await tx.session.scalar(
                    select(SkillPackageRecord.id).where(SkillPackageRecord.storage_key == prepared.storage_key).limit(1)
                )
            if published is not None:
                raise Conflict("published Skill content cannot be discarded as preparation")
            await self._storage.delete_tree(prepared.storage_key)

    async def publish_skill(
        self,
        principal: TenantPrincipal | SkillInstallScope,
        *,
        agent_id: UUID,
        skill_name: str,
        prepared: PreparedSkillPackage,
        shared: bool,
        package_id: UUID | None = None,
        expected_revision: str | None = None,
        catalog_item_id: UUID | None = None,
        publication_guard: SkillPublicationGuard | None = None,
    ) -> SkillBindingView:
        self._validate_name(skill_name)
        if shared and catalog_item_id is not None and package_id is None and expected_revision is None:
            async with self._storage.resource_lock(f"skill-catalog/{principal.tenant_id}/{catalog_item_id}"):
                current = await self.lookup_shared_skill(principal, catalog_item_id=catalog_item_id)
                if current:
                    result = await self.bind_skill(
                        principal, agent_id=agent_id, skill_name=skill_name, package_id=current.package_id,
                        publication_guard=publication_guard,
                    )
                    try:
                        await self.discard_prepared_skill(prepared)
                    except Conflict:
                        # A published handle is not temporary data; keep the committed binding.
                        pass
                    except OSError:
                        result = SkillBindingView(
                            result.agent_id,
                            result.skill_name,
                            result.package_id,
                            result.shared,
                            result.revision,
                            cleanup_pending=True,
                        )
                    return result
                return await self._publish_binding(
                    principal,
                    agent_id=agent_id,
                    skill_name=skill_name,
                    prepared=prepared,
                    shared=shared,
                    package_id=package_id,
                    expected_revision=expected_revision,
                    catalog_item_id=catalog_item_id,
                    publication_guard=publication_guard,
                )
        return await self._publish_binding(
            principal,
            agent_id=agent_id,
            skill_name=skill_name,
            prepared=prepared,
            shared=shared,
            package_id=package_id,
            expected_revision=expected_revision,
            catalog_item_id=catalog_item_id,
            publication_guard=publication_guard,
        )

    async def lookup_shared_skill(
        self, principal: TenantPrincipal | SkillInstallScope, *, catalog_item_id: UUID
    ) -> SharedSkillView | None:
        async with transaction(self._sessions) as tx:
            package = await tx.session.scalar(
                select(SkillPackageRecord)
                .where(
                    SkillPackageRecord.tenant_id == principal.tenant_id,
                    SkillPackageRecord.catalog_item_id == catalog_item_id,
                    SkillPackageRecord.owner_agent_id.is_(None),
                )
                .limit(1)
            )
            return SharedSkillView(package.id, package.revision) if package else None

    async def refresh_shared_skill(
        self, principal: TenantPrincipal, *, package_id: UUID, prepared: PreparedSkillPackage, expected_revision: str,
        publication_guard: SkillPublicationGuard | None = None,
    ) -> SharedSkillView:
        """Refresh Tenant-shared content without installing or rebinding any Agent."""
        require_admin(principal)
        self._validate_handle(prepared)
        async with (
            self._storage.resource_lock(f"skill-preparation/{prepared.revision}"),
            self._storage.resource_lock(f"skill-package/{package_id}"),
        ):
            async with transaction(self._sessions) as tx:
                used = await tx.session.scalar(
                    select(SkillPackageRecord.id).where(SkillPackageRecord.storage_key == prepared.storage_key).limit(1)
                )
                if used is not None:
                    raise Conflict("Skill preparation is already published")
            await self._read_package(prepared.storage_key, prepared.content_hash)
            async with transaction(self._sessions) as tx:
                package = await WorkspaceRepository(tx.session).package(principal.tenant_id, package_id)
                if package is None:
                    raise NotFound("shared Skill package does not exist")
                if package.owner_agent_id is not None:
                    raise AccessDenied("shared refresh cannot change a private package")
                if package.revision != expected_revision:
                    raise Conflict("shared Skill changed; reload before refreshing")
                await _check_publication_source(publication_guard, tx, principal.tenant_id, package.catalog_item_id)
                old_key = package.storage_key
                package.storage_key = prepared.storage_key
                package.content_hash = prepared.content_hash
                package.revision = prepared.revision
                package.updated_at = datetime.now(UTC)
            cleanup_pending = False
            try:
                await self._storage.delete_tree(old_key)
            except OSError:
                cleanup_pending = True
        self._audit.emit(
            AuditObservation(
                principal.tenant_id,
                MembershipActor(principal.membership_id),
                "workspace.skill.refresh_shared",
                "skill_package",
                str(package_id),
                "succeeded",
                1,
                {},
                datetime.now(UTC),
            )
        )
        return SharedSkillView(package_id, prepared.revision, cleanup_pending)

    async def _publish_binding(
        self,
        principal: TenantPrincipal | SkillInstallScope,
        *,
        agent_id: UUID,
        skill_name: str,
        prepared: PreparedSkillPackage,
        shared: bool,
        package_id: UUID | None,
        expected_revision: str | None,
        catalog_item_id: UUID | None,
        publication_guard: SkillPublicationGuard | None,
    ) -> SkillBindingView:
        async with self._storage.resource_lock(f"skill-binding/{principal.tenant_id}/{agent_id}/{skill_name}"):
            try:
                return await self._publish_skill(
                    principal,
                    agent_id=agent_id,
                    skill_name=skill_name,
                    prepared=prepared,
                    shared=shared,
                    package_id=package_id,
                    expected_revision=expected_revision,
                    catalog_item_id=catalog_item_id,
                    publication_guard=publication_guard,
                )
            except IntegrityError:
                raise Conflict("Skill installation references changed or are incompatible") from None

    async def _publish_skill(
        self,
        principal: TenantPrincipal | SkillInstallScope,
        *,
        agent_id: UUID,
        skill_name: str,
        prepared: PreparedSkillPackage,
        shared: bool,
        package_id: UUID | None,
        expected_revision: str | None,
        catalog_item_id: UUID | None,
        publication_guard: SkillPublicationGuard | None,
    ) -> SkillBindingView:
        self._validate_handle(prepared)
        self._validate_name(skill_name)
        async with transaction(self._sessions) as tx:
            await self._require_install(tx, principal, agent_id)
            binding = await WorkspaceRepository(tx.session).binding(principal.tenant_id, agent_id, skill_name)
            selected_id = package_id or (binding.package_id if binding else uuid4())
        async with self._storage.resource_lock(f"skill-preparation/{prepared.revision}"):
            async with transaction(self._sessions) as tx:
                used = await tx.session.scalar(
                    select(SkillPackageRecord.id).where(SkillPackageRecord.storage_key == prepared.storage_key).limit(1)
                )
                if used is not None:
                    raise Conflict("Skill preparation is already published")
            # Validate again after preparation/discard exclusion, before database publication.
            await self._read_package(prepared.storage_key, prepared.content_hash)
            async with self._storage.resource_lock(f"skill-package/{selected_id}"):
                old_key: str | None = None
                now = datetime.now(UTC)
                async with transaction(self._sessions) as tx:
                    repository = WorkspaceRepository(tx.session)
                    package = await repository.package(principal.tenant_id, selected_id)
                    binding = await repository.binding(principal.tenant_id, agent_id, skill_name)
                    if binding and package_id is not None and package_id != binding.package_id:
                        raise Conflict("installation points to another package; shared refresh must not rebind it")
                    if package and package.owner_agent_id not in {None, agent_id}:
                        raise AccessDenied("private Skill belongs to another Agent")
                    if shared and package and isinstance(principal, SkillInstallScope):
                        raise AccessDenied("Agent self-install cannot refresh a shared package")
                    if shared and package and isinstance(principal, TenantPrincipal):
                        require_admin(principal)
                    if package and package.revision != expected_revision:
                        raise Conflict("Skill changed; reload before updating")
                    if package is None and expected_revision is not None:
                        raise Conflict("Skill no longer exists")
                    if shared and package and package.owner_agent_id is not None:
                        raise InvalidInput("private Skill cannot replace a shared package")
                    if package and catalog_item_id is not None and package.catalog_item_id != catalog_item_id:
                        raise Conflict("Skill update cannot replace its source registration")
                    actual_source = package.catalog_item_id if package else catalog_item_id
                    await _check_publication_source(publication_guard, tx, principal.tenant_id, actual_source)
                    if package and not shared and package.owner_agent_id is None:
                        package = None
                        selected_id = uuid4()
                    if package is None:
                        package = SkillPackageRecord(
                            id=selected_id,
                            tenant_id=principal.tenant_id,
                            owner_agent_id=None if shared else agent_id,
                            catalog_item_id=actual_source,
                            storage_key=prepared.storage_key,
                            content_hash=prepared.content_hash,
                            format_version=1,
                            revision=prepared.revision,
                            created_at=now,
                            updated_at=now,
                        )
                        tx.session.add(package)
                    else:
                        old_key = package.storage_key
                        package.storage_key = prepared.storage_key
                        package.content_hash = prepared.content_hash
                        package.revision = prepared.revision
                        package.updated_at = now
                    await tx.session.flush()
                    if binding is None:
                        binding = AgentSkillBindingRecord(
                            id=uuid4(),
                            tenant_id=principal.tenant_id,
                            agent_id=agent_id,
                            skill_name=skill_name,
                            package_id=package.id,
                            package_scope="shared" if shared else "private",
                            created_at=now,
                            updated_at=now,
                        )
                        tx.session.add(binding)
                    else:
                        binding.package_id = package.id
                        binding.package_scope = "shared" if shared else "private"
                        binding.updated_at = now
                    await tx.session.flush()
                    result = SkillBindingView(agent_id, skill_name, package.id, shared, prepared.revision)
                if old_key and old_key != prepared.storage_key:
                    try:
                        await self._storage.delete_tree(old_key)
                    except OSError:
                        # Publication already committed; old content cleanup is independent.
                        result = SkillBindingView(
                            result.agent_id,
                            result.skill_name,
                            result.package_id,
                            result.shared,
                            result.revision,
                            cleanup_pending=True,
                        )
        self._audit.emit(
            AuditObservation(
                principal.tenant_id,
                MembershipActor(principal.membership_id)
                if isinstance(principal, TenantPrincipal)
                else AgentActor(principal.agent_id),
                "workspace.skill.publish",
                "skill_package",
                str(result.package_id),
                "succeeded",
                1,
                {"shared": shared},
                datetime.now(UTC),
            )
        )
        return result

    async def bind_skill(
        self, principal: TenantPrincipal | SkillInstallScope, *, agent_id: UUID, skill_name: str, package_id: UUID,
        publication_guard: SkillPublicationGuard | None = None,
    ) -> SkillBindingView:
        self._validate_name(skill_name)
        async with (
            self._storage.resource_lock(f"skill-binding/{principal.tenant_id}/{agent_id}/{skill_name}"),
            self._storage.resource_lock(f"skill-package/{package_id}"),
            transaction(self._sessions) as tx,
        ):
            await self._require_install(tx, principal, agent_id)
            repository = WorkspaceRepository(tx.session)
            package = await repository.package(principal.tenant_id, package_id)
            if package is None:
                raise NotFound("Skill package does not exist")
            if package.owner_agent_id not in {None, agent_id}:
                raise AccessDenied("private Skill belongs to another Agent")
            await _check_publication_source(publication_guard, tx, principal.tenant_id, package.catalog_item_id)
            if await repository.binding(principal.tenant_id, agent_id, skill_name):
                raise Conflict("Skill name is already installed")
            now = datetime.now(UTC)
            tx.session.add(
                AgentSkillBindingRecord(
                    id=uuid4(),
                    tenant_id=principal.tenant_id,
                    agent_id=agent_id,
                    skill_name=skill_name,
                    package_id=package_id,
                    package_scope="shared" if package.owner_agent_id is None else "private",
                    created_at=now,
                    updated_at=now,
                )
            )
            return SkillBindingView(agent_id, skill_name, package_id, package.owner_agent_id is None, package.revision)

    async def remove_skill(self, principal: TenantPrincipal, *, agent_id: UUID, skill_name: str) -> SkillRemoval:
        self._validate_name(skill_name)
        async with self._storage.resource_lock(f"skill-binding/{principal.tenant_id}/{agent_id}/{skill_name}"):
            async with transaction(self._sessions) as tx:
                await PermissionService(tx).require_principal_access(principal, agent_id=agent_id)
                binding = await WorkspaceRepository(tx.session).binding(principal.tenant_id, agent_id, skill_name)
                if binding is None:
                    raise NotFound("Skill is not installed")
                package_id = binding.package_id
            async with self._storage.resource_lock(f"skill-package/{package_id}"):
                cleanup_key: str | None = None
                async with transaction(self._sessions) as tx:
                    repository = WorkspaceRepository(tx.session)
                    binding = await repository.binding(principal.tenant_id, agent_id, skill_name)
                    if binding is None:
                        raise NotFound("Skill is not installed")
                    await tx.session.delete(binding)
                    await tx.session.flush()
                    package = await repository.package(principal.tenant_id, package_id)
                    if package and package.owner_agent_id is not None:
                        remaining = await tx.session.scalar(
                            select(AgentSkillBindingRecord.id)
                            .where(
                                AgentSkillBindingRecord.tenant_id == principal.tenant_id,
                                AgentSkillBindingRecord.package_id == package_id,
                            )
                            .limit(1)
                        )
                        if remaining is None:
                            cleanup_key = package.storage_key
                            await tx.session.delete(package)
                if cleanup_key:
                    try:
                        await self._storage.delete_tree(cleanup_key)
                    except OSError:
                        return SkillRemoval(cleanup_pending=True)
        return SkillRemoval()

    async def discover_skills(self, *, tenant_id: UUID, agent_id: UUID) -> SkillDiscovery:
        """Called only with trusted Run intake identities, after Agent authorization."""
        async with transaction(self._sessions) as tx:
            records = await WorkspaceRepository(tx.session).discovery_sources(tenant_id, agent_id, 101)
            if len(records) > 100:
                raise InvalidInput("Agent exceeds the 100-Skill discovery bound")
            requested = frozenset(source for _, source in records if source is not None)
            enabled: frozenset[UUID] = frozenset()
            if requested:
                if self._enabled_skill_sources is None:
                    raise InvalidInput("catalog-backed Skill discovery requires the Catalog source resolver")
                enabled = await self._enabled_skill_sources(
                    transaction_context=tx, tenant_id=tenant_id, requested_ids=requested
                )
            return SkillDiscovery(
                tenant_id, agent_id, tuple(name for name, source in records if source is None or source in enabled)
            )

    async def load_skill(self, discovery: SkillDiscovery, name: str) -> SkillContent:
        if name not in discovery.skills:
            raise AccessDenied("Skill is absent from this Run's discovery")
        # Binding lock excludes removal/rebinding; package lock excludes publication cleanup.
        async with self._storage.resource_lock(f"skill-binding/{discovery.tenant_id}/{discovery.agent_id}/{name}"):
            async with transaction(self._sessions) as tx:
                binding = await WorkspaceRepository(tx.session).binding(discovery.tenant_id, discovery.agent_id, name)
                if binding is None:
                    raise NotFound("discovered Skill was removed")
                package_id = binding.package_id
            async with self._storage.resource_lock(f"skill-package/{package_id}"):
                async with transaction(self._sessions) as tx:
                    package = await WorkspaceRepository(tx.session).package(discovery.tenant_id, package_id)
                    if package is None:
                        raise NotFound("Skill package no longer exists")
                    if package.format_version != 1:
                        raise InvalidInput("unsupported Skill package format")
                    key, expected_hash, revision = package.storage_key, package.content_hash, package.revision
                return SkillContent(name, revision, await self._read_package(key, expected_hash))

    async def _read_package(self, prefix: str, expected_hash: str) -> Mapping[str, bytes]:
        raw = await self._read_member(f"{prefix}/.manifest.json", max_bytes=128 * 1024)
        if hashlib.sha256(raw).hexdigest() != expected_hash:
            raise InvalidInput("Skill manifest hash mismatch")
        try:
            manifest = json.loads(raw)
            if manifest["version"] != 1 or not isinstance(manifest["members"], dict):
                raise ValueError
            hashes = manifest["members"]
            if not 1 <= len(hashes) <= MAX_PACKAGE_MEMBERS or "SKILL.md" not in hashes:
                raise ValueError
        except (ValueError, TypeError, KeyError):
            raise InvalidInput("invalid Skill manifest") from None
        result: dict[str, bytes] = {}
        total = len(raw)
        for name, digest in hashes.items():
            relative_path(name)
            remaining = MAX_PACKAGE_BYTES - total - len(name.encode())
            if remaining < 1:
                raise InvalidInput("Skill package exceeds its complete bound")
            content = await self._read_member(f"{prefix}/{name}", max_bytes=min(MAX_FILE_BYTES, remaining))
            total += len(content) + len(name.encode())
            if total > MAX_PACKAGE_BYTES or hashlib.sha256(content).hexdigest() != digest:
                raise InvalidInput("Skill package exceeds its bound or has invalid content")
            result[name] = content
        return result

    async def _read_member(self, key: str, *, max_bytes: int) -> bytes:
        try:
            content, _ = await self._storage.read_versioned(key, max_bytes=max_bytes)
        except FileNotFoundError:
            raise NotFound("published Skill content is missing") from None
        except (IsADirectoryError, ValueError):
            raise InvalidInput("Skill member violates its content bound") from None
        except OSError:
            raise WorkspaceUnavailable("Skill member could not be read") from None
        return content

    @staticmethod
    async def _require_install(
        tx: TransactionContext, principal: TenantPrincipal | SkillInstallScope, agent_id: UUID
    ) -> None:
        if isinstance(principal, TenantPrincipal):
            await PermissionService(tx).require_principal_access(principal, agent_id=agent_id)
        else:
            if principal.agent_id != agent_id:
                raise AccessDenied("self-install is limited to the executing Agent")
            await AgentService(tx).get_metadata(tenant_id=principal.tenant_id, agent_id=agent_id)

    @staticmethod
    def _validate_handle(prepared: PreparedSkillPackage) -> None:
        if (
            not re.fullmatch(r"[a-f0-9]{32}", prepared.revision)
            or prepared.storage_key != f"skill-packages/prepared/{prepared.revision}"
        ):
            raise InvalidInput("invalid Skill preparation handle")

    @staticmethod
    def _validate_name(name: str) -> None:
        if not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", name):
            raise InvalidInput("invalid canonical Skill name")
