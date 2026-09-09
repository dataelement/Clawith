"""Tool configuration and immutable, account-scoped execution inputs."""

from datetime import UTC, datetime
from typing import Literal, cast
from uuid import UUID, uuid4

from sqlalchemy.exc import IntegrityError

from app.infrastructure.errors import AccessDenied, Conflict, InvalidInput, NotFound
from app.infrastructure.transactions import TransactionContext
from app.modules.agent.public import AgentService
from app.modules.credential.public import CredentialMetadataView, CredentialOwnerKind, CredentialService
from app.modules.identity_tenant.public import TenantPrincipal, require_admin
from app.modules.tool.contracts import (
    MAX_DISCOVERY_BYTES,
    MAX_SCHEMA_BYTES,
    MAX_TOOLS,
    AgentInstallScope,
    AgentToolResolutionScope,
    AuthorizedToolSet,
    AvailableToolSet,
    CallScope,
    CredentialBinding,
    DefinitionSpec,
    EnabledSources,
    MCPConnection,
    MCPInstallSpec,
    MCPTool,
    PersonalAccountSelection,
    ResolvedTool,
    RunRole,
    ToolCall,
    ToolDefinition,
    ToolOutputPart,
    ToolResolutionScope,
    ToolResult,
    ToolSource,
    canonical_json,
    decode_personal_selections,
    encode_personal_selections,
    json_object,
    role_eligible,
    tool_result_content,
    validate_endpoint,
)
from app.modules.tool.execution import (
    SEARCH_TOOLS_DEFINITION,
    ExecutorBinding,
    ToolExecutor,
    ToolRegistry,
    ToolScheduler,
    ToolSearchExecutor,
)
from app.modules.tool.mcp import MCPClient, MCPExecutor, MCPFailure
from app.modules.tool.models import (
    AgentMCPConnectionRecord,
    AgentToolGrantRecord,
    MembershipAgentToolConnectionRecord,
    ToolDefinitionRecord,
)
from app.modules.tool.repository import ToolRepository

__all__ = [
    "MAX_DISCOVERY_BYTES",
    "MAX_SCHEMA_BYTES",
    "MAX_TOOLS",
    "SEARCH_TOOLS_DEFINITION",
    "AgentInstallScope",
    "AgentToolResolutionScope",
    "AuthorizedToolSet",
    "AvailableToolSet",
    "CallScope",
    "CredentialBinding",
    "DefinitionSpec",
    "EnabledSources",
    "ExecutorBinding",
    "MCPClient",
    "MCPConnection",
    "MCPExecutor",
    "MCPFailure",
    "MCPInstallSpec",
    "MCPTool",
    "PersonalAccountSelection",
    "ResolvedTool",
    "RunRole",
    "ToolCall",
    "ToolDefinition",
    "ToolExecutor",
    "ToolOutputPart",
    "ToolRegistry",
    "ToolResolutionScope",
    "ToolResult",
    "ToolScheduler",
    "ToolSearchExecutor",
    "ToolService",
    "ToolSource",
    "canonical_json",
    "decode_personal_selections",
    "encode_personal_selections",
    "json_object",
    "role_eligible",
    "tool_result_content",
    "validate_endpoint",
]


class ToolService:
    """Short configuration operations; no network work occurs inside this service."""

    def __init__(self, transaction: TransactionContext, *, enabled_sources: EnabledSources | None = None) -> None:
        self._transaction = transaction
        self._enabled_sources = enabled_sources
        self._repo = ToolRepository(transaction.session)
        self._agents = AgentService(transaction)
        self._credentials = CredentialService(transaction)

    async def validate_personal_selections(self, principal: TenantPrincipal, *,
            selections: tuple[PersonalAccountSelection, ...]) -> tuple[PersonalAccountSelection, ...]:
        """Validate explicit human choices using the same Agent/connection/grant policy as execution capture."""
        encode_personal_selections(selections)
        for selection in selections:
            await self._agents.get_for_execution(principal, agent_id=selection.target_agent_id)
            if not selection.connection_ids:
                continue
            await self.capture_authorized(ToolResolutionScope(principal, selection.target_agent_id, "main",
                frozenset(selection.connection_ids), selection.connection_ids))
        return selections

    async def install_for_agent(
        self, scope: AgentInstallScope, *, definition: DefinitionSpec, connection: MCPInstallSpec | None = None
    ) -> ToolDefinition:
        """Called only by an authorized install-capability executor, never model-supplied scope."""
        agent = await self._agents.get_metadata(tenant_id=scope.tenant_id, agent_id=scope.agent_id)
        if not agent.enabled or agent.archived_at is not None:
            raise NotFound("Installing Agent is unavailable")
        if definition.catalog_item_id is None or definition.source not in ("external", "mcp"):
            raise AccessDenied("Self-install requires a registered external capability")
        if (definition.source == "mcp") != (connection is not None):
            raise InvalidInput("MCP self-install requires its connection")
        if connection and connection.credential_id:
            await self._credentials.require_owner_metadata(
                tenant_id=scope.tenant_id,
                credential_id=connection.credential_id,
                owner_kind="agent",
                owner_id=scope.agent_id,
            )
        installed = await self._register_definition(scope.tenant_id, definition)
        connected = (
            await self._connect_mcp(scope.tenant_id, scope.agent_id, definition.catalog_item_id, connection)
            if connection
            else None
        )
        await self._grant(
            scope.tenant_id, scope.agent_id, installed.id, None, mcp_connection_id=connected.id if connected else None
        )
        return installed

    async def register_definition(self, principal: TenantPrincipal, *, definition: DefinitionSpec) -> ToolDefinition:
        require_admin(principal)
        return await self._register_definition(principal.tenant_id, definition)

    async def _register_definition(self, tenant_id: UUID, definition: DefinitionSpec) -> ToolDefinition:
        existing = await self._repo.definition_named(tenant_id, definition.name)
        if existing is None:
            now = datetime.now(UTC)
            row = ToolDefinitionRecord(
                id=uuid4(),
                tenant_id=tenant_id,
                created_at=now,
                updated_at=now,
                catalog_item_id=definition.catalog_item_id,
                source=definition.source,
                name=definition.name,
                upstream_name=definition.upstream_name,
                description=definition.description,
                input_schema=json_object(definition.input_schema_json),
                schema_version=1,
                executor_key=definition.executor_key,
                configuration_version=1,
                non_secret_config={},
                enabled=True,
            )
            try:
                existing = await self._repo.insert_definition_if_absent(row)
            except IntegrityError:
                raise Conflict("Tool configuration conflicts with existing data") from None
        current = _definition(existing)
        matching_mcp_identity = (
            current.spec.source == definition.source == "mcp"
            and current.spec.catalog_item_id == definition.catalog_item_id
            and current.spec.upstream_name == definition.upstream_name
            and current.spec.executor_key == definition.executor_key
        )
        if current.spec != definition and not matching_mcp_identity:
            raise Conflict("Existing Tool identity has an incompatible definition")
        return current

    async def connect_mcp(
        self,
        principal: TenantPrincipal,
        *,
        agent_id: UUID,
        catalog_item_id: UUID,
        endpoint: str,
        auth_required: bool,
        credential_id: UUID | None = None,
        discovered: tuple[MCPTool, ...] = (),
        transport: Literal["streamable_http", "sse"] = "streamable_http",
    ) -> MCPConnection:
        require_admin(principal)
        await self._agents.get(principal, agent_id=agent_id)
        if credential_id is not None:
            await self._credential(principal, credential_id, "agent", agent_id)
        return await self._connect_mcp(
            principal.tenant_id,
            agent_id,
            catalog_item_id,
            MCPInstallSpec(endpoint, auth_required, credential_id, discovered, transport),
        )

    async def _connect_mcp(
        self, tenant_id: UUID, agent_id: UUID, catalog_item_id: UUID, spec: MCPInstallSpec
    ) -> MCPConnection:
        endpoint, auth_required, credential_id = spec.endpoint, spec.auth_required, spec.credential_id
        discovered, transport = spec.discovered, spec.transport
        if transport not in ("streamable_http", "sse"):
            raise InvalidInput("MCP transport is invalid")
        config = {"endpoint": validate_endpoint(endpoint), "transport": transport}
        discovery = _discovery(discovered)
        existing = await self._repo.connection_for_source(tenant_id, agent_id, catalog_item_id)
        if existing:
            if (
                existing.credential_id != credential_id
                or existing.auth_required != auth_required
                or existing.non_secret_config != config
                or not existing.enabled
            ):
                raise Conflict("MCP connection already exists with different settings")
            return MCPConnection(
                existing.id, agent_id, catalog_item_id, endpoint, auth_required, credential_id, transport
            )
        now = datetime.now(UTC)
        row = AgentMCPConnectionRecord(
            id=uuid4(),
            tenant_id=tenant_id,
            created_at=now,
            updated_at=now,
            agent_id=agent_id,
            catalog_item_id=catalog_item_id,
            credential_id=credential_id,
            credential_owner_kind="agent" if credential_id else None,
            credential_owner_id=agent_id if credential_id else None,
            auth_required=auth_required,
            configuration_version=1,
            non_secret_config=config,
            enabled=True,
            discovery_version=1,
            discovered_tools=discovery,
        )
        self._repo.add(row)
        await self._flush()
        return MCPConnection(row.id, agent_id, catalog_item_id, endpoint, auth_required, credential_id, transport)

    async def grant(
        self,
        principal: TenantPrincipal,
        *,
        agent_id: UUID,
        definition_id: UUID,
        mcp_connection_id: UUID | None = None,
        credential_id: UUID | None = None,
    ) -> UUID:
        require_admin(principal)
        await self._agents.get(principal, agent_id=agent_id)
        credential = None
        if credential_id:
            credential = await self._credentials.get_metadata(principal, credential_id=credential_id)
            if not (
                (credential.owner_kind == "agent" and credential.owner_id == agent_id)
                or (credential.owner_kind == "tenant" and credential.owner_id == principal.tenant_id)
            ):
                raise AccessDenied("Tool grant Credential owner is incompatible")
        return await self._grant(
            principal.tenant_id,
            agent_id,
            definition_id,
            principal.membership_id,
            mcp_connection_id=mcp_connection_id,
            credential=credential,
        )

    async def _grant(
        self,
        tenant_id: UUID,
        agent_id: UUID,
        definition_id: UUID,
        membership_id: UUID | None,
        *,
        mcp_connection_id: UUID | None = None,
        credential: CredentialMetadataView | None = None,
    ) -> UUID:
        credential_id = credential.id if credential else None
        definition = await self._require_definition(tenant_id, definition_id)
        existing = await self._repo.grant_for_tool(tenant_id, agent_id, definition_id)
        if existing:
            return _matching_grant_id(existing, mcp_connection_id, credential_id)
        if definition.source == "mcp":
            connection = await self._repo.connection(tenant_id, mcp_connection_id) if mcp_connection_id else None
            if (
                connection is None
                or connection.agent_id != agent_id
                or connection.catalog_item_id != definition.catalog_item_id
                or credential_id is not None
            ):
                raise AccessDenied("MCP grant requires the matching Agent connection")
        elif mcp_connection_id is not None:
            raise InvalidInput("Only MCP definitions accept MCP connections")
        now = datetime.now(UTC)
        row = AgentToolGrantRecord(
            id=uuid4(),
            tenant_id=tenant_id,
            created_at=now,
            updated_at=now,
            agent_id=agent_id,
            tool_definition_id=definition_id,
            tool_source=definition.source,
            catalog_item_id=definition.catalog_item_id,
            mcp_connection_id=mcp_connection_id,
            credential_id=credential_id,
            credential_owner_kind=credential.owner_kind if credential else None,
            credential_owner_id=credential.owner_id if credential else None,
            configuration_version=1,
            non_secret_config={},
            granted_by_membership_id=membership_id,
            revoked_at=None,
        )
        try:
            current = await self._repo.insert_grant_if_absent(row)
        except IntegrityError:
            raise Conflict("Tool configuration conflicts with existing data") from None
        return _matching_grant_id(current, mcp_connection_id, credential_id)

    async def revoke_grant(self, principal: TenantPrincipal, *, agent_id: UUID, definition_id: UUID) -> None:
        require_admin(principal)
        await self._agents.get(principal, agent_id=agent_id)
        grant = await self._repo.grant_for_tool(principal.tenant_id, agent_id, definition_id)
        if grant is None:
            raise NotFound("Tool grant is unavailable")
        grant.revoked_at = grant.revoked_at or datetime.now(UTC)
        grant.updated_at = datetime.now(UTC)
        await self._flush()

    async def refresh_discovery(
        self,
        principal: TenantPrincipal,
        *,
        connection_id: UUID,
        expected_credential_id: UUID | None,
        discovered: tuple[MCPTool, ...],
    ) -> None:
        """Publish account-local discovery prepared outside this transaction; existing Run views remain fixed."""
        require_admin(principal)
        connection = await self._repo.connection(principal.tenant_id, connection_id)
        if connection is None:
            raise NotFound("MCP connection is unavailable")
        await self._agents.get(principal, agent_id=connection.agent_id)
        if connection.credential_id != expected_credential_id:
            raise Conflict("MCP account changed while discovery was prepared")
        connection.discovered_tools = _discovery(discovered)
        connection.discovery_version = 1
        connection.updated_at = datetime.now(UTC)
        await self._flush()

    async def set_connection_enabled(self, principal: TenantPrincipal, *, connection_id: UUID, enabled: bool) -> None:
        require_admin(principal)
        connection = await self._repo.connection(principal.tenant_id, connection_id)
        if connection is None:
            raise NotFound("MCP connection is unavailable")
        connection.enabled = enabled
        connection.updated_at = datetime.now(UTC)
        await self._flush()

    async def bind_personal_connection(
        self,
        principal: TenantPrincipal,
        *,
        agent_id: UUID,
        definition_id: UUID,
        credential_id: UUID,
        label: str,
        endpoint: str,
        discovered: tuple[MCPTool, ...],
        transport: Literal["streamable_http", "sse"] = "streamable_http",
    ) -> UUID:
        if transport not in ("streamable_http", "sse"):
            raise InvalidInput("MCP transport is invalid")
        await self._agents.get_for_execution(principal, agent_id=agent_id)
        definition = await self._require_definition(principal.tenant_id, definition_id)
        if definition.source != "mcp":
            raise InvalidInput("Personal MCP connection requires an MCP definition")
        await self._credential(principal, credential_id, "membership", principal.membership_id)
        if not label.strip() or len(label) > 200:
            raise InvalidInput("Personal connection label is invalid")
        now = datetime.now(UTC)
        row = MembershipAgentToolConnectionRecord(
            id=uuid4(),
            tenant_id=principal.tenant_id,
            created_at=now,
            updated_at=now,
            membership_id=principal.membership_id,
            agent_id=agent_id,
            tool_definition_id=definition_id,
            credential_id=credential_id,
            credential_owner_kind="membership",
            credential_owner_id=principal.membership_id,
            label=label,
            configuration_version=1,
            non_secret_config={"endpoint": validate_endpoint(endpoint), "transport": transport},
            enabled=True,
            discovery_version=1,
            discovered_tools=_discovery(discovered),
        )
        self._repo.add(row)
        await self._flush()
        return row.id

    async def resolve(
        self, scope: ToolResolutionScope | AgentToolResolutionScope, *, direct_names: frozenset[str] = frozenset()
    ) -> AvailableToolSet:
        captured = await self.capture_authorized(scope)
        return captured.for_role(scope.role, direct_names=direct_names)

    async def capture_authorized(self, scope: ToolResolutionScope | AgentToolResolutionScope) -> AuthorizedToolSet:
        """Capture once for Main/Child derivation; the scope role does not filter bindings."""
        if isinstance(scope, ToolResolutionScope):
            await self._agents.get_for_execution(scope.principal, agent_id=scope.agent_id)
            tenant_id = scope.principal.tenant_id
            membership_id = scope.principal.membership_id
        else:
            tenant_id = scope.tenant_id
            membership_id = None
            agent = await self._agents.get_metadata(tenant_id=tenant_id, agent_id=scope.agent_id)
            if not agent.enabled or agent.archived_at is not None:
                raise NotFound("Executing Agent is unavailable")
        role_eligible("", scope.role)
        if (
            len(scope.selected_personal_connections) > MAX_TOOLS
            or not set(scope.selected_personal_connections) <= scope.authorized_personal_connections
        ):
            raise AccessDenied("Personal account was not authorized for this task")
        grants = await self._repo.grants(tenant_id, scope.agent_id, maximum=MAX_TOOLS)
        if len(grants) > MAX_TOOLS:
            raise InvalidInput("Agent Tool set exceeds its configured limit")
        definitions = await self._repo.definitions(tenant_id, tuple(grant.tool_definition_id for grant in grants))
        source_ids = frozenset(row.catalog_item_id for row in definitions.values() if row.catalog_item_id)
        enabled_ids: frozenset[UUID] = frozenset()
        if source_ids:
            if self._enabled_sources is None:
                raise InvalidInput("Catalog availability resolver is required")
            enabled_ids = await self._enabled_sources(
                transaction_context=self._transaction, tenant_id=tenant_id, requested_ids=source_ids
            )
            if not enabled_ids <= source_ids:
                raise InvalidInput("Catalog resolver returned an unexpected source")
        connections = await self._repo.connections(
            tenant_id, tuple(grant.mcp_connection_id for grant in grants if grant.mcp_connection_id)
        )
        personal: dict[UUID, MembershipAgentToolConnectionRecord] = {}
        selected_connections = await self._repo.personal_connections(tenant_id, scope.selected_personal_connections)
        for selected_id in scope.selected_personal_connections:
            selected = selected_connections.get(selected_id)
            if (
                selected is None
                or not selected.enabled
                or selected.agent_id != scope.agent_id
                or (membership_id is not None and selected.membership_id != membership_id)
            ):
                raise AccessDenied("Personal connection is unavailable for this task")
            if selected.tool_definition_id in personal:
                raise InvalidInput("Select only one personal account per Tool")
            personal[selected.tool_definition_id] = selected
        resolved: list[ResolvedTool] = []
        granted_ids = {grant.tool_definition_id for grant in grants}
        if not personal.keys() <= granted_ids:
            raise AccessDenied("Personal connection cannot grant an ungranted Tool")
        for grant in grants:
            if grant.configuration_version != 1:
                raise InvalidInput("Tool grant version is unsupported")
            row = definitions.get(grant.tool_definition_id)
            if (
                row is None
                or not row.enabled
                or (row.catalog_item_id is not None and row.catalog_item_id not in enabled_ids)
            ):
                continue
            definition = _definition(row)
            if row.source != "mcp":
                credential = (
                    CredentialBinding(
                        grant.credential_id,
                        cast(CredentialOwnerKind, grant.credential_owner_kind),
                        grant.credential_owner_id,
                    )
                    if grant.credential_id and grant.credential_owner_id
                    else None
                )
                resolved.append(ResolvedTool(definition, credential))
                continue
            selected = personal.get(row.id)
            connection = connections.get(grant.mcp_connection_id) if grant.mcp_connection_id else None
            if connection is None or not connection.enabled:
                if selected:
                    raise NotFound("Selected Tool connection is unavailable")
                continue
            if connection.configuration_version != 1 or (selected and selected.configuration_version != 1):
                raise InvalidInput("MCP connection version is unsupported")
            if selected:
                credential = CredentialBinding(selected.credential_id, "membership", selected.membership_id)
                discovered, config, version = (
                    selected.discovered_tools,
                    selected.non_secret_config,
                    selected.discovery_version,
                )
            else:
                if connection.auth_required and connection.credential_id is None:
                    continue
                credential = (
                    CredentialBinding(connection.credential_id, "agent", scope.agent_id)
                    if connection.credential_id
                    else None
                )
                discovered, config, version = (
                    connection.discovered_tools,
                    connection.non_secret_config,
                    connection.discovery_version,
                )
            if version != 1:
                raise InvalidInput("MCP discovery version is unsupported")
            match = next((tool for tool in _parse_discovery(discovered) if tool.name == row.upstream_name), None)
            if match is None:
                if selected:
                    raise NotFound("Selected account does not expose this Tool")
                continue
            # Account-local schema replaces only this immutable view, never the shared Definition.
            spec = DefinitionSpec(
                row.name,
                match.description,
                match.input_schema_json,
                row.executor_key,
                "mcp",
                row.catalog_item_id,
                row.upstream_name,
            )
            endpoint = config.get("endpoint")
            transport = config.get("transport")
            if not isinstance(endpoint, str) or transport not in ("streamable_http", "sse"):
                raise InvalidInput("MCP endpoint configuration is invalid")
            resolved.append(
                ResolvedTool(
                    ToolDefinition(row.id, tenant_id, spec),
                    credential,
                    validate_endpoint(endpoint),
                    cast(Literal["streamable_http", "sse"], transport),
                )
            )
        resolved_personal = {item.definition.id for item in resolved
            if item.credential is not None and item.credential.owner_kind == "membership"}
        if resolved_personal != set(personal):
            raise NotFound("An explicitly selected personal account Tool is unavailable")
        return AuthorizedToolSet(tenant_id, scope.agent_id, tuple(resolved))

    async def _credential(
        self, principal: TenantPrincipal, credential_id: UUID, kind: CredentialOwnerKind, owner_id: UUID
    ) -> None:
        metadata = await self._credentials.get_metadata(principal, credential_id=credential_id)
        if metadata.owner_kind != kind or metadata.owner_id != owner_id:
            raise AccessDenied("Credential owner is incompatible with this connection")
        if metadata.revoked_at is not None or (metadata.expires_at and metadata.expires_at <= datetime.now(UTC)):
            raise NotFound("Credential is unavailable")

    async def _require_definition(self, tenant_id: UUID, definition_id: UUID) -> ToolDefinitionRecord:
        row = await self._repo.definition(tenant_id, definition_id)
        if row is None:
            raise NotFound("Tool definition is unavailable")
        return row

    async def _flush(self) -> None:
        try:
            await self._repo.flush()
        except IntegrityError:
            raise Conflict("Tool configuration conflicts with existing data") from None


def _matching_grant_id(row: AgentToolGrantRecord, connection_id: UUID | None, credential_id: UUID | None) -> UUID:
    if row.configuration_version != 1 or row.non_secret_config != {}:
        raise InvalidInput("Tool grant configuration version or settings are unsupported")
    if row.mcp_connection_id != connection_id or row.credential_id != credential_id or row.revoked_at is not None:
        raise Conflict("Tool grant already exists with different settings")
    return row.id


def _definition(row: ToolDefinitionRecord) -> ToolDefinition:
    if row.schema_version != 1 or row.configuration_version != 1:
        raise InvalidInput("Tool definition version is unsupported")
    return ToolDefinition(
        row.id,
        row.tenant_id,
        DefinitionSpec(
            row.name,
            row.description,
            canonical_json(row.input_schema),
            row.executor_key,
            cast(ToolSource, row.source),
            row.catalog_item_id,
            row.upstream_name,
        ),
    )


def _discovery(tools: tuple[MCPTool, ...]) -> list[dict[str, object]]:
    if len(tools) > MAX_TOOLS or len({tool.name for tool in tools}) != len(tools):
        raise InvalidInput("MCP discovery count or identities are invalid")
    result = [
        {"name": tool.name, "description": tool.description, "inputSchema": json_object(tool.input_schema_json)}
        for tool in tools
    ]
    canonical_json(result, maximum=MAX_DISCOVERY_BYTES)
    return result


def _parse_discovery(value: object) -> tuple[MCPTool, ...]:
    canonical_json(value, maximum=MAX_DISCOVERY_BYTES)
    if not isinstance(value, list) or len(value) > MAX_TOOLS:
        raise InvalidInput("MCP discovery is invalid")
    tools: list[MCPTool] = []
    for item in value:
        if (
            not isinstance(item, dict)
            or not isinstance(item.get("name"), str)
            or not isinstance(item.get("description"), str)
        ):
            raise InvalidInput("MCP discovery entry is invalid")
        tools.append(MCPTool(item["name"], item["description"], canonical_json(item.get("inputSchema"))))
    if len({tool.name for tool in tools}) != len(tools):
        raise InvalidInput("MCP discovery identities are duplicated")
    return tuple(tools)
