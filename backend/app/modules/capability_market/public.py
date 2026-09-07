"""Shared discovery and explicit Agent installation orchestration."""

import hashlib
import re
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Literal, cast
from urllib.parse import urlsplit, urlunsplit
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.infrastructure.errors import AccessDenied, Conflict, DomainError, InvalidInput, NotFound
from app.infrastructure.transactions import TransactionContext, transaction
from app.modules.agent.public import AgentService
from app.modules.audit.public import AgentActor, AuditObservation, AuditSink, MembershipActor
from app.modules.capability_market.models import CapabilityCatalogItemRecord
from app.modules.capability_market.repository import CatalogRepository
from app.modules.identity_tenant.public import PlatformPrincipal, TenantPrincipal, require_admin
from app.modules.tool.public import AgentInstallScope, DefinitionSpec, MCPInstallSpec, MCPTool, ToolService
from app.modules.workspace.public import SharedSkillView, SkillBindingView, SkillInstallScope

if TYPE_CHECKING:
    from app.modules.workspace.public import PreparedSkillPackage, WorkspaceService

CapabilityKind = Literal["tool", "mcp", "skill"]
MAX_PAGE_SIZE = 100
MAX_SOURCE_RESOLUTION_IDS = 256


def _source_key(value: str) -> str:
    if not value or len(value.encode()) > 512 or any(character.isspace() for character in value):
        raise InvalidInput("Capability source identity is invalid")
    if "://" not in value:
        if not re.fullmatch(r"[A-Za-z0-9_./@:+-]+", value):
            raise InvalidInput("Capability package identity is invalid")
        return value
    try:
        parsed = urlsplit(value)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError
        port = parsed.port
    except ValueError:
        raise InvalidInput("Capability source must be HTTPS without embedded credentials") from None
    host = parsed.hostname.lower()
    if ":" in host:
        host = f"[{host}]"
    authority = host if port in (None, 443) else f"{host}:{port}"
    return urlunsplit(("https", authority, parsed.path or "/", "", ""))


@dataclass(frozen=True, slots=True)
class CatalogSpec:
    """Schema v1 discovery metadata; no executor, package bytes or credentials."""

    kind: CapabilityKind
    source: str
    source_key: str
    name: str
    description: str
    version: str

    def __post_init__(self) -> None:
        if self.kind not in ("tool", "mcp", "skill") or not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", self.source):
            raise InvalidInput("Capability kind or source is invalid")
        for value, maximum in ((self.name, 200), (self.description, 4096), (self.version, 128)):
            if not isinstance(value, str) or len(value.encode()) > maximum:
                raise InvalidInput("Capability metadata exceeds its limit")
        if not self.name.strip() or not self.version.strip():
            raise InvalidInput("Capability name and version are required")
        object.__setattr__(self, "source_key", _source_key(self.source_key))


@dataclass(frozen=True, slots=True)
class CatalogItem:
    id: UUID
    tenant_id: UUID | None
    spec: CatalogSpec
    origin_platform_item_id: UUID | None
    definition_revision: int
    enabled: bool


@dataclass(frozen=True, slots=True)
class RegistrationResult:
    item: CatalogItem
    created: bool


@dataclass(frozen=True, slots=True)
class InstallationResult:
    """Source persistence survives a later ordinary Agent activation failure."""

    source: RegistrationResult
    agent_id: UUID
    activated: bool
    activation_error: str | None = None
    skill_binding: SkillBindingView | None = None


def _view(row: CapabilityCatalogItemRecord) -> CatalogItem:
    if row.manifest_schema_version != 1 or row.manifest != {"schema_version": 1}:
        raise InvalidInput("Capability manifest version or shape is unsupported")
    return CatalogItem(
        row.id,
        row.tenant_id,
        CatalogSpec(cast(CapabilityKind, row.kind), row.source, row.source_key, row.name, row.description, row.version),
        row.origin_platform_item_id,
        row.definition_revision,
        row.enabled,
    )


def _record(
    spec: CatalogSpec,
    *,
    tenant_id: UUID | None,
    membership_id: UUID | None,
    origin: UUID | None = None,
    agent_id: UUID | None = None,
) -> CapabilityCatalogItemRecord:
    now = datetime.now(UTC)
    return CapabilityCatalogItemRecord(
        id=uuid4(),
        tenant_id=tenant_id,
        kind=spec.kind,
        source=spec.source,
        source_key=spec.source_key,
        name=spec.name,
        description=spec.description,
        version=spec.version,
        manifest_schema_version=1,
        manifest={"schema_version": 1},
        definition_revision=1,
        enabled=True,
        installed_by_membership_id=membership_id,
        installed_by_agent_id=agent_id,
        origin_platform_item_id=origin,
        created_at=now,
        updated_at=now,
    )


class CapabilityMarketService:
    """All database phases are short; callers finish external discovery before install.

    Catalog registration never grants access. Only each successful owner mutation
    establishes activation. Unexpected defects and cancellation remain exceptions.
    """

    def __init__(
        self, sessions: async_sessionmaker[AsyncSession], audit: AuditSink, workspace: "WorkspaceService | None" = None
    ) -> None:
        self._sessions = sessions
        self._audit = audit
        self._workspace = workspace

    async def enabled_source_ids(
        self, *, transaction_context: TransactionContext, tenant_id: UUID, requested_ids: frozenset[UUID]
    ) -> frozenset[UUID]:
        """Resolve current Catalog admission for trusted Tool/Skill discovery scope.

        This is not an execution-time revocation check. Already resolved Run
        bindings remain fixed; missing, disabled and foreign sources are omitted.
        Reuse the caller's transaction rather than acquiring a nested connection.
        """
        if len(requested_ids) > MAX_SOURCE_RESOLUTION_IDS:
            raise InvalidInput("Capability source resolution exceeds 256 identities")
        if not requested_ids:
            return frozenset()
        rows = await CatalogRepository(transaction_context.session).enabled_sources(tenant_id, requested_ids)
        return frozenset(_view(row).id for row in rows)

    async def search(
        self,
        principal: TenantPrincipal | AgentInstallScope,
        *,
        query: str = "",
        kind: CapabilityKind | None = None,
        limit: int = MAX_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[CatalogItem, ...]:
        if not isinstance(principal, (TenantPrincipal, AgentInstallScope)):
            raise AccessDenied("Tenant login or granted Agent installation scope is required")
        if len(query.encode()) > 256 or not 1 <= limit <= MAX_PAGE_SIZE or not 0 <= offset <= 10000:
            raise InvalidInput("Capability search bounds are invalid")
        if kind is not None and kind not in ("tool", "mcp", "skill"):
            raise InvalidInput("Capability kind is invalid")
        async with transaction(self._sessions) as tx:
            if isinstance(principal, AgentInstallScope):
                await AgentService(tx).get_metadata(tenant_id=principal.tenant_id, agent_id=principal.agent_id)
            return tuple(
                _view(row)
                for row in await CatalogRepository(tx.session).search(principal.tenant_id, query, kind, limit, offset)
            )

    async def register(self, principal: TenantPrincipal, *, spec: CatalogSpec) -> RegistrationResult:
        require_admin(principal)
        async with transaction(self._sessions) as tx:
            row, created = await CatalogRepository(tx.session).register(
                _record(spec, tenant_id=principal.tenant_id, membership_id=principal.membership_id)
            )
            result = RegistrationResult(_view(row), created)
        if created:
            self._observe(principal, result.item.id, "capability.register")
        return result

    async def register_platform(self, principal: PlatformPrincipal, *, spec: CatalogSpec) -> RegistrationResult:
        if not isinstance(principal, PlatformPrincipal) or principal.platform_role != "platform_admin":
            raise AccessDenied("Platform administrator is required")
        async with transaction(self._sessions) as tx:
            row, created = await CatalogRepository(tx.session).register(
                _record(spec, tenant_id=None, membership_id=None)
            )
            return RegistrationResult(_view(row), created)

    async def register_for_agent(self, scope: AgentInstallScope, *, spec: CatalogSpec) -> RegistrationResult:
        """Granted install executor supplies scope; registration grants no capability."""
        async with transaction(self._sessions) as tx:
            await AgentService(tx).get_metadata(tenant_id=scope.tenant_id, agent_id=scope.agent_id)
            row, created = await CatalogRepository(tx.session).register(
                _record(spec, tenant_id=scope.tenant_id, membership_id=None, agent_id=scope.agent_id)
            )
            result = RegistrationResult(_view(row), created)
        if created:
            self._observe(scope, result.item.id, "capability.register")
        return result

    async def _materialize_for_agent(self, scope: AgentInstallScope, item_id: UUID) -> RegistrationResult:
        async with transaction(self._sessions) as tx:
            await AgentService(tx).get_metadata(tenant_id=scope.tenant_id, agent_id=scope.agent_id)
            repo = CatalogRepository(tx.session)
            row = await repo.get(scope.tenant_id, item_id)
            if row is None or not row.enabled:
                raise NotFound("Capability source is unavailable")
            item = _view(row)
            if item.tenant_id is not None:
                return RegistrationResult(item, False)
            row, created = await repo.register(
                _record(
                    item.spec, tenant_id=scope.tenant_id, membership_id=None, agent_id=scope.agent_id, origin=item.id
                )
            )
            result = RegistrationResult(_view(row), created)
        if created:
            self._observe(scope, result.item.id, "capability.register")
        return result

    async def install_tool_for_agent(
        self,
        scope: AgentInstallScope,
        *,
        item_id: UUID,
        definition: DefinitionSpec,
        connection: MCPInstallSpec | None = None,
    ) -> InstallationResult:
        source = await self._materialize_for_agent(scope, item_id)
        try:
            async with transaction(self._sessions) as tx:
                await self._activation_source(
                    CatalogRepository(tx.session), scope, source.item.id, "mcp" if connection is not None else "tool"
                )
                await ToolService(tx).install_for_agent(
                    scope, definition=replace(definition, catalog_item_id=source.item.id), connection=connection
                )
        except DomainError as error:
            return InstallationResult(source, scope.agent_id, False, error.code)
        self._observe(scope, source.item.id, "capability.install")
        return InstallationResult(source, scope.agent_id, True)

    async def install_skill_for_agent(
        self,
        scope: AgentInstallScope,
        *,
        item_id: UUID,
        skill_name: str,
        prepared: "PreparedSkillPackage",
        shared: bool,
        package_id: UUID | None = None,
        expected_revision: str | None = None,
    ) -> InstallationResult:
        if self._workspace is None:
            raise InvalidInput("Workspace installation service is not configured")
        published = False
        try:
            source = await self._materialize_for_agent(scope, item_id)
            try:
                if source.item.spec.kind != "skill":
                    raise InvalidInput("Capability is not a Skill")
                binding = await self._workspace.publish_skill(
                    SkillInstallScope(scope.tenant_id, scope.agent_id),
                    agent_id=scope.agent_id,
                    skill_name=skill_name,
                    prepared=prepared,
                    shared=shared,
                    package_id=package_id,
                    expected_revision=expected_revision,
                    catalog_item_id=source.item.id,
                )
                published = True
            except DomainError as error:
                return InstallationResult(source, scope.agent_id, False, error.code)
        finally:
            if not published:
                await self._workspace.discard_prepared_skill(prepared)
        self._observe(scope, source.item.id, "capability.install")
        return InstallationResult(source, scope.agent_id, True, skill_binding=binding)

    async def materialize(self, principal: TenantPrincipal, *, item_id: UUID) -> RegistrationResult:
        require_admin(principal)
        async with transaction(self._sessions) as tx:
            repo = CatalogRepository(tx.session)
            source = await repo.get(principal.tenant_id, item_id)
            if source is None or not source.enabled:
                raise NotFound("Capability source is unavailable")
            item = _view(source)
            if item.tenant_id is not None:
                return RegistrationResult(item, False)
            row, created = await repo.register(
                _record(item.spec, tenant_id=principal.tenant_id, membership_id=principal.membership_id, origin=item.id)
            )
            result = RegistrationResult(_view(row), created)
        if created:
            self._observe(principal, result.item.id, "capability.register")
        return result

    async def set_enabled(self, principal: TenantPrincipal, *, item_id: UUID, enabled: bool) -> CatalogItem:
        require_admin(principal)
        async with transaction(self._sessions) as tx:
            row = await CatalogRepository(tx.session).get(principal.tenant_id, item_id, lock=True)
            if row is None or row.tenant_id != principal.tenant_id:
                raise NotFound("Tenant capability source is unavailable")
            row.enabled = enabled
            row.updated_at = datetime.now(UTC)
            await tx.session.flush()
            result = _view(row)
        self._observe(principal, item_id, "capability.configure")
        return result

    async def refresh_source(
        self, principal: TenantPrincipal, *, item_id: UUID, spec: CatalogSpec, expected_revision: int
    ) -> CatalogItem:
        """Refresh discovery metadata without modifying executable owner bindings."""
        require_admin(principal)
        async with transaction(self._sessions) as tx:
            row = await CatalogRepository(tx.session).get(principal.tenant_id, item_id, lock=True)
            if row is None or row.tenant_id != principal.tenant_id:
                raise NotFound("Tenant capability source is unavailable")
            if row.definition_revision != expected_revision:
                raise Conflict("Capability source changed; reload before refreshing")
            if (row.kind, row.source, row.source_key) != (spec.kind, spec.source, spec.source_key):
                raise InvalidInput("Capability refresh cannot change its source identity")
            row.name, row.description, row.version = spec.name, spec.description, spec.version
            row.definition_revision += 1
            row.updated_at = datetime.now(UTC)
            await tx.session.flush()
            result = _view(row)
        self._observe(principal, item_id, "capability.refresh")
        return result

    async def install_tool(
        self, principal: TenantPrincipal, *, agent_id: UUID, item_id: UUID, definition: DefinitionSpec
    ) -> InstallationResult:
        source = await self.materialize(principal, item_id=item_id)
        try:
            async with transaction(self._sessions) as tx:
                await self._activation_source(CatalogRepository(tx.session), principal, source.item.id, "tool")
                tools = ToolService(tx)
                registered = await tools.register_definition(
                    principal, definition=replace(definition, catalog_item_id=source.item.id)
                )
                await tools.grant(principal, agent_id=agent_id, definition_id=registered.id)
        except DomainError as error:
            return InstallationResult(source, agent_id, False, error.code)
        self._observe(principal, source.item.id, "capability.install")
        return InstallationResult(source, agent_id, True)

    async def install_mcp(
        self,
        principal: TenantPrincipal,
        *,
        agent_id: UUID,
        item_id: UUID,
        endpoint: str,
        auth_required: bool,
        discovered: tuple[MCPTool, ...],
        credential_id: UUID | None = None,
        transport: Literal["streamable_http", "sse"] = "streamable_http",
    ) -> InstallationResult:
        source = await self.materialize(principal, item_id=item_id)
        try:
            async with transaction(self._sessions) as tx:
                await self._activation_source(CatalogRepository(tx.session), principal, source.item.id, "mcp")
                tools = ToolService(tx)
                connection = await tools.connect_mcp(
                    principal,
                    agent_id=agent_id,
                    catalog_item_id=source.item.id,
                    endpoint=endpoint,
                    auth_required=auth_required,
                    credential_id=credential_id,
                    discovered=discovered,
                    transport=transport,
                )
                for tool in discovered:
                    suffix = hashlib.sha256(f"{source.item.id}:{tool.name}".encode()).hexdigest()[:24]
                    definition = await tools.register_definition(
                        principal,
                        definition=DefinitionSpec(
                            name=f"mcp_{suffix}",
                            description=tool.description,
                            input_schema_json=tool.input_schema_json,
                            executor_key="mcp.v1",
                            source="mcp",
                            catalog_item_id=source.item.id,
                            upstream_name=tool.name,
                        ),
                    )
                    await tools.grant(
                        principal, agent_id=agent_id, definition_id=definition.id, mcp_connection_id=connection.id
                    )
        except DomainError as error:
            return InstallationResult(source, agent_id, False, error.code)
        self._observe(principal, source.item.id, "capability.install")
        return InstallationResult(source, agent_id, True)

    async def install_skill(
        self,
        principal: TenantPrincipal,
        *,
        agent_id: UUID,
        item_id: UUID,
        skill_name: str,
        prepared: "PreparedSkillPackage",
        shared: bool,
        package_id: UUID | None = None,
        expected_revision: str | None = None,
    ) -> InstallationResult:
        if self._workspace is None:
            raise InvalidInput("Workspace installation service is not configured")
        published = False
        try:
            source = await self.materialize(principal, item_id=item_id)
            try:
                if source.item.spec.kind != "skill":
                    raise InvalidInput("Capability is not a Skill")
                binding = await self._workspace.publish_skill(
                    principal,
                    agent_id=agent_id,
                    skill_name=skill_name,
                    prepared=prepared,
                    shared=shared,
                    package_id=package_id,
                    expected_revision=expected_revision,
                    catalog_item_id=source.item.id,
                )
                published = True
            except DomainError as error:
                return InstallationResult(source, agent_id, False, error.code)
        finally:
            if not published:
                await self._workspace.discard_prepared_skill(prepared)
        self._observe(principal, source.item.id, "capability.install")
        return InstallationResult(source, agent_id, True, skill_binding=binding)

    async def refresh_shared_skill(
        self,
        principal: TenantPrincipal,
        *,
        item_id: UUID,
        prepared: "PreparedSkillPackage",
        expected_revision: str,
    ) -> SharedSkillView:
        """Update the shared package without installing or rebinding any Agent."""
        if self._workspace is None:
            raise InvalidInput("Workspace installation service is not configured")
        published = False
        try:
            require_admin(principal)
            source = await self.materialize(principal, item_id=item_id)
            if source.item.spec.kind != "skill":
                raise InvalidInput("Capability is not a Skill")
            package = await self._workspace.lookup_shared_skill(principal, catalog_item_id=source.item.id)
            if package is None:
                raise NotFound("Shared Skill package is not installed")
            result = await self._workspace.refresh_shared_skill(
                principal,
                package_id=package.package_id,
                prepared=prepared,
                expected_revision=expected_revision,
            )
            published = True
        finally:
            if not published:
                await self._workspace.discard_prepared_skill(prepared)
        self._observe(principal, source.item.id, "capability.refresh")
        return result

    async def _activation_source(
        self,
        repo: CatalogRepository,
        principal: TenantPrincipal | AgentInstallScope,
        item_id: UUID,
        kind: CapabilityKind,
    ) -> None:
        # Serialize shared definitions without holding a transaction during discovery.
        row = await repo.get(principal.tenant_id, item_id, lock=True)
        if row is None or row.tenant_id != principal.tenant_id or not row.enabled:
            raise NotFound("Tenant capability source is unavailable")
        if row.kind != kind:
            raise Conflict("Capability source kind does not match installation")

    def _observe(self, principal: TenantPrincipal | AgentInstallScope, item_id: UUID, action: str) -> None:
        self._audit.emit(
            AuditObservation(
                principal.tenant_id,
                MembershipActor(principal.membership_id)
                if isinstance(principal, TenantPrincipal)
                else AgentActor(principal.agent_id),
                action,
                "capability",
                str(item_id),
                "succeeded",
                1,
                {},
                datetime.now(UTC),
            )
        )
