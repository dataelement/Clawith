"""Immutable public Tool contracts and bounded boundary codecs."""

import json
import re
from dataclasses import dataclass
from typing import Literal, Protocol, cast
from urllib.parse import parse_qsl, urlsplit
from uuid import UUID

from app.infrastructure.errors import AccessDenied, InvalidInput
from app.infrastructure.transactions import TransactionContext
from app.modules.credential.public import CredentialOwnerKind
from app.modules.identity_tenant.public import TenantPrincipal

ToolSource = Literal["builtin", "product", "mcp", "external"]
RunRole = Literal["main", "sub"]
MAX_TOOLS = 128
MAX_SCHEMA_BYTES = 65536
MAX_DISCOVERY_BYTES = 524288


def json_object(value: str, *, maximum: int = MAX_SCHEMA_BYTES) -> dict[str, object]:
    if len(value.encode("utf-8")) > maximum:
        raise InvalidInput("Tool JSON exceeds its byte limit")
    try:
        decoded = json.loads(value, parse_constant=lambda _: _invalid_number())
    except (ValueError, RecursionError):
        raise InvalidInput("Tool JSON is invalid") from None
    if not isinstance(decoded, dict):
        raise InvalidInput("Tool JSON must be an object")
    return cast(dict[str, object], decoded)


def _invalid_number() -> None:
    raise ValueError("non-finite JSON number")


def canonical_json(value: object, *, maximum: int = MAX_SCHEMA_BYTES) -> str:
    try:
        result = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (ValueError, TypeError, RecursionError):
        raise InvalidInput("Tool JSON is invalid") from None
    if len(result.encode("utf-8")) > maximum:
        raise InvalidInput("Tool JSON exceeds its byte limit")
    return result


def validate_endpoint(endpoint: str) -> str:
    try:
        parsed = urlsplit(endpoint)
        _ = parsed.port
    except ValueError:
        raise InvalidInput("MCP endpoint is invalid") from None
    if (
        len(endpoint) > 2048
        or parsed.scheme not in ("http", "https")
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.fragment
        or any(
            key.casefold().replace("_", "") in {"apikey", "token", "accesstoken", "authorization", "password"}
            for key, _ in parse_qsl(parsed.query)
        )
    ):
        raise InvalidInput("MCP endpoint must be an HTTP URL without embedded credentials")
    return endpoint


@dataclass(frozen=True, slots=True)
class MCPTool:
    name: str
    description: str
    input_schema_json: str

    def __post_init__(self) -> None:
        if not self.name or len(self.name) > 256 or len(self.description.encode()) > 16384:
            raise InvalidInput("MCP tool name or description is invalid")
        schema = json_object(self.input_schema_json)
        if schema.get("type") != "object":
            raise InvalidInput("MCP input schema must describe an object")
        object.__setattr__(self, "input_schema_json", canonical_json(schema))


@dataclass(frozen=True, slots=True)
class DefinitionSpec:
    name: str
    description: str
    input_schema_json: str
    executor_key: str
    source: ToolSource
    catalog_item_id: UUID | None = None
    upstream_name: str | None = None

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", self.name):
            raise InvalidInput("Tool canonical name is invalid")
        if not self.executor_key or len(self.executor_key) > 128 or len(self.description.encode()) > 16384:
            raise InvalidInput("Tool definition is invalid")
        if self.source not in ("builtin", "product", "mcp", "external"):
            raise InvalidInput("Tool source is invalid")
        if self.source == "mcp" and (self.catalog_item_id is None or not self.upstream_name):
            raise InvalidInput("MCP definition requires its Catalog and upstream name")
        if self.source == "mcp" and self.executor_key != "mcp.v1":
            raise InvalidInput("MCP executor version is unsupported")
        schema = json_object(self.input_schema_json)
        if schema.get("type") != "object":
            raise InvalidInput("Tool input schema must describe an object")
        object.__setattr__(self, "input_schema_json", canonical_json(schema))


@dataclass(frozen=True, slots=True)
class ToolDefinition:
    id: UUID
    tenant_id: UUID
    spec: DefinitionSpec


@dataclass(frozen=True, slots=True)
class MCPConnection:
    id: UUID
    agent_id: UUID
    catalog_item_id: UUID
    endpoint: str
    auth_required: bool
    credential_id: UUID | None
    transport: Literal["streamable_http", "sse"] = "streamable_http"


@dataclass(frozen=True, slots=True)
class CredentialBinding:
    id: UUID
    owner_kind: CredentialOwnerKind
    owner_id: UUID


@dataclass(frozen=True, slots=True)
class ResolvedTool:
    definition: ToolDefinition
    credential: CredentialBinding | None
    endpoint: str | None = None
    transport: Literal["streamable_http", "sse"] = "streamable_http"


@dataclass(frozen=True, slots=True)
class ToolResolutionScope:
    principal: TenantPrincipal
    agent_id: UUID
    role: RunRole
    # Authenticated Product intake supplies these exact delegated connection IDs.
    authorized_personal_connections: frozenset[UUID] = frozenset()
    selected_personal_connections: tuple[UUID, ...] = ()


@dataclass(frozen=True, slots=True)
class AgentToolResolutionScope:
    """Authenticated Agent-owned intake, including exact explicit Product delegations."""

    tenant_id: UUID
    agent_id: UUID
    role: RunRole
    authorized_personal_connections: frozenset[UUID] = frozenset()
    selected_personal_connections: tuple[UUID, ...] = ()


class EnabledSources(Protocol):
    async def __call__(
        self, *, transaction_context: TransactionContext, tenant_id: UUID, requested_ids: frozenset[UUID]
    ) -> frozenset[UUID]: ...


@dataclass(frozen=True, slots=True)
class AvailableToolSet:
    tenant_id: UUID
    agent_id: UUID
    tools: tuple[ResolvedTool, ...]
    direct_names: frozenset[str]

    def __post_init__(self) -> None:
        names = [tool.definition.spec.name for tool in self.tools]
        if len(names) > MAX_TOOLS or len(names) != len(set(names)) or not self.direct_names <= set(names):
            raise InvalidInput("Available Tool set is invalid")
        if any(tool.definition.tenant_id != self.tenant_id for tool in self.tools):
            raise AccessDenied("Available Tool set crosses Tenant scope")

    def search(self, query: str, *, limit: int = 10) -> tuple[ToolDefinition, ...]:
        if not 1 <= limit <= 20 or not query.strip() or len(query) > 256:
            raise InvalidInput("Tool search is invalid")
        terms = query.casefold().split()
        return tuple(
            tool.definition
            for tool in self.tools
            if all(
                term in (tool.definition.spec.name + " " + tool.definition.spec.description).casefold()
                for term in terms
            )
        )[:limit]

    def expose(self, names: frozenset[str]) -> "AvailableToolSet":
        return AvailableToolSet(self.tenant_id, self.agent_id, self.tools, self.direct_names | names)

    def visible(self) -> tuple[ToolDefinition, ...]:
        return tuple(tool.definition for tool in self.tools if tool.definition.spec.name in self.direct_names)


def role_eligible(name: str, role: RunRole) -> bool:
    if role not in ("main", "sub"):
        raise InvalidInput("Run role is invalid")
    if name in ("task", "call_agent", "wake_agent", "send_message_to_agent"):
        return role == "main"
    if name == "todo":
        return role == "sub"
    return True


@dataclass(frozen=True, slots=True)
class ToolCall:
    id: str
    name: str
    arguments_json: str

    def __post_init__(self) -> None:
        if not self.id or len(self.id) > 256 or not self.name or len(self.name) > 64:
            raise InvalidInput("Tool Call identity is invalid")
        object.__setattr__(self, "arguments_json", canonical_json(json_object(self.arguments_json)))


@dataclass(frozen=True, slots=True)
class ToolResult:
    call_id: str
    status: Literal["success", "error", "uncertain"]
    content_json: str

    def __post_init__(self) -> None:
        if self.status not in ("success", "error", "uncertain") or not self.call_id or len(self.call_id) > 256:
            raise InvalidInput("Tool Result identity or status is invalid")
        # Whole normalized result, including the correlation wrapper, is bounded.
        json_object(self.content_json, maximum=262144)
        canonical_json(
            {"call_id": self.call_id, "status": self.status, "content": json_object(self.content_json, maximum=262144)},
            maximum=262144,
        )


@dataclass(frozen=True, slots=True)
class CallScope:
    tenant_id: UUID
    agent_id: UUID
    run_id: UUID


@dataclass(frozen=True, slots=True)
class AgentInstallScope:
    """Trusted executor-produced scope; never parsed from model input."""

    tenant_id: UUID
    agent_id: UUID


@dataclass(frozen=True, slots=True)
class MCPInstallSpec:
    endpoint: str
    auth_required: bool
    credential_id: UUID | None = None
    discovered: tuple[MCPTool, ...] = ()
    transport: Literal["streamable_http", "sse"] = "streamable_http"
