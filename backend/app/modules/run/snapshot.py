"""Immutable Run startup snapshots with a selected, non-private Context view."""

import hashlib
import json
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, ValidationError
from sqlalchemy import Text, cast, func, select

from app.infrastructure.errors import Conflict, DomainError, NotFound
from app.infrastructure.transactions import TransactionContext
from app.modules.model.public import ModelContextProfile, PrivateModelPolicy, ResolvedModel, validate_resolved_model
from app.modules.run.contracts import MAX_NODES, MAX_RECORD_BYTES, _check_tree
from app.modules.run.models import RunRecord, RunSnapshotRecord
from app.modules.tool.public import (
    AuthorizedToolSet,
    CredentialBinding,
    DefinitionSpec,
    ResolvedTool,
    ToolDefinition,
    validate_endpoint,
)
from app.modules.workspace.public import SkillDiscovery, WorkspaceScope, WorkspaceSubject

SNAPSHOT_VERSION = 1
SNAPSHOT_KIND = "run_snapshot"
MAX_SNAPSHOT_BYTES = MAX_RECORD_BYTES
MAX_SOURCE_BYTES = 256 * 1024


class InvalidSnapshot(ValueError):
    """Authoritative Snapshot is invalid; never rebuild it from current configuration."""


@dataclass(frozen=True, slots=True)
class PlatformInstructions:
    version: str
    text: str


@dataclass(frozen=True, slots=True)
class AgentIdentity:
    name: str
    soul: str
    timezone: str


@dataclass(frozen=True, slots=True)
class SourceSection:
    category: Literal["memory_index", "skill_index"]
    subject: WorkspaceSubject
    reference: str
    content: str


@dataclass(frozen=True, slots=True)
class RunSnapshot:
    tenant_id: UUID
    agent_id: UUID
    role: Literal["main", "sub"]
    platform: PlatformInstructions
    agent: AgentIdentity
    model: ResolvedModel
    tools: AuthorizedToolSet
    initial_direct_names: frozenset[str]
    workspace: WorkspaceScope
    skills: SkillDiscovery
    sources: tuple[SourceSection, ...] = ()
    include_current_time: bool = False


@dataclass(frozen=True, slots=True)
class EncodedSnapshot:
    version: int
    content_hash: str
    payload: dict[str, object]


@dataclass(frozen=True, slots=True)
class VisibleSection:
    category: Literal["platform", "agent", "memory_index", "skill_index"]
    source: str
    content: str


class _V1(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)


class _PlatformV1(_V1):
    version: str
    text: str


class _AgentV1(_V1):
    name: str
    soul: str
    timezone: str


class _PolicyV1(_V1):
    tenant_id: UUID
    model_id: UUID
    provider: str
    protocol: Literal["openai_chat", "openai_responses", "anthropic", "gemini"]
    model_name: str
    endpoint: str
    credential_id: UUID
    context_limit: int
    output_limit: int
    capabilities_json: str
    settings_json: str


class _ProfileV1(_V1):
    model_id: UUID
    provider: str
    model_name: str
    context_limit: int
    output_limit: int
    supports_images: bool
    supports_streaming: bool
    supports_prompt_cache: bool


class _ModelV1(_V1):
    policy: _PolicyV1
    profile: _ProfileV1


class _DefinitionV1(_V1):
    name: str
    description: str
    input_schema_json: str
    executor_key: str
    source: Literal["builtin", "product", "mcp", "external"]
    catalog_item_id: UUID | None
    upstream_name: str | None


class _ToolDefinitionV1(_V1):
    id: UUID
    tenant_id: UUID
    spec: _DefinitionV1


class _CredentialV1(_V1):
    id: UUID
    owner_kind: Literal["tenant", "membership", "agent"]
    owner_id: UUID


class _ToolV1(_V1):
    definition: _ToolDefinitionV1
    credential: _CredentialV1 | None
    endpoint: str | None
    transport: Literal["streamable_http", "sse"]


class _ToolsV1(_V1):
    tenant_id: UUID
    agent_id: UUID
    tools: tuple[_ToolV1, ...]


class _SubjectV1(_V1):
    kind: Literal["membership", "agent", "group"]
    id: UUID


class _WorkspaceV1(_V1):
    tenant_id: UUID
    agent_id: UUID
    output: _SubjectV1
    run_id: UUID
    main: bool
    preview_only: bool


class _SkillsV1(_V1):
    tenant_id: UUID
    agent_id: UUID
    skills: tuple[str, ...]


class _SourceV1(_V1):
    category: Literal["memory_index", "skill_index"]
    subject: _SubjectV1
    reference: str
    content: str


class _SnapshotV1(_V1):
    tenant_id: UUID
    agent_id: UUID
    role: Literal["main", "sub"]
    platform: _PlatformV1
    agent: _AgentV1
    model: _ModelV1
    tools: _ToolsV1
    initial_direct_names: tuple[str, ...]
    workspace: _WorkspaceV1
    skills: _SkillsV1
    sources: tuple[_SourceV1, ...]
    include_current_time: bool


def _to_v1(value: RunSnapshot) -> _SnapshotV1:
    policy, profile, scope = value.model.policy, value.model.profile, value.workspace
    assert scope.run_id is not None
    tools = []
    for tool in value.tools.tools:
        definition, binding = tool.definition.spec, tool.credential
        tools.append(_ToolV1(definition=_ToolDefinitionV1(id=tool.definition.id, tenant_id=tool.definition.tenant_id,
            spec=_DefinitionV1(name=definition.name, description=definition.description,
                input_schema_json=definition.input_schema_json, executor_key=definition.executor_key,
                source=definition.source, catalog_item_id=definition.catalog_item_id, upstream_name=definition.upstream_name)),
            credential=_CredentialV1(id=binding.id, owner_kind=binding.owner_kind, owner_id=binding.owner_id) if binding else None,
            endpoint=tool.endpoint, transport=tool.transport))
    return _SnapshotV1(tenant_id=value.tenant_id, agent_id=value.agent_id, role=value.role,
        platform=_PlatformV1(version=value.platform.version, text=value.platform.text),
        agent=_AgentV1(name=value.agent.name, soul=value.agent.soul, timezone=value.agent.timezone),
        model=_ModelV1(policy=_PolicyV1(tenant_id=policy.tenant_id, model_id=policy.model_id,
            provider=policy.provider, protocol=policy.protocol, model_name=policy.model_name, endpoint=policy.endpoint,
            credential_id=policy.credential_id, context_limit=policy.context_limit, output_limit=policy.output_limit,
            capabilities_json=policy.capabilities_json, settings_json=policy.settings_json),
            profile=_ProfileV1(model_id=profile.model_id, provider=profile.provider, model_name=profile.model_name,
                context_limit=profile.context_limit, output_limit=profile.output_limit, supports_images=profile.supports_images,
                supports_streaming=profile.supports_streaming, supports_prompt_cache=profile.supports_prompt_cache)),
        tools=_ToolsV1(tenant_id=value.tools.tenant_id, agent_id=value.tools.agent_id, tools=tuple(tools)),
        initial_direct_names=tuple(sorted(value.initial_direct_names)),
        workspace=_WorkspaceV1(tenant_id=scope.tenant_id, agent_id=scope.agent_id,
            output=_SubjectV1(kind=scope.output.kind, id=scope.output.id), run_id=scope.run_id,
            main=scope.main, preview_only=scope.preview_only),
        skills=_SkillsV1(tenant_id=value.skills.tenant_id, agent_id=value.skills.agent_id, skills=value.skills.skills),
        sources=tuple(_SourceV1(category=source.category,
            subject=_SubjectV1(kind=source.subject.kind, id=source.subject.id), reference=source.reference,
            content=source.content) for source in value.sources), include_current_time=value.include_current_time)


def _from_v1(value: _SnapshotV1) -> RunSnapshot:
    policy, profile, scope = value.model.policy, value.model.profile, value.workspace
    tools = []
    for tool in value.tools.tools:
        definition, binding = tool.definition.spec, tool.credential
        tools.append(ResolvedTool(ToolDefinition(tool.definition.id, tool.definition.tenant_id,
            DefinitionSpec(definition.name, definition.description, definition.input_schema_json,
                definition.executor_key, definition.source, definition.catalog_item_id, definition.upstream_name)),
            CredentialBinding(binding.id, binding.owner_kind, binding.owner_id) if binding else None,
            tool.endpoint, tool.transport))
    return RunSnapshot(value.tenant_id, value.agent_id, value.role,
        PlatformInstructions(value.platform.version, value.platform.text),
        AgentIdentity(value.agent.name, value.agent.soul, value.agent.timezone),
        ResolvedModel(PrivateModelPolicy(policy.tenant_id, policy.model_id, policy.provider, policy.protocol,
            policy.model_name, policy.endpoint, policy.credential_id, policy.context_limit, policy.output_limit,
            policy.capabilities_json, policy.settings_json),
            ModelContextProfile(profile.model_id, profile.provider, profile.model_name, profile.context_limit,
                profile.output_limit, profile.supports_images, profile.supports_streaming, profile.supports_prompt_cache)),
        AuthorizedToolSet(value.tools.tenant_id, value.tools.agent_id, tuple(tools)), frozenset(value.initial_direct_names),
        WorkspaceScope(scope.tenant_id, scope.agent_id, WorkspaceSubject(scope.output.kind, scope.output.id),
            scope.run_id, scope.main, scope.preview_only),
        SkillDiscovery(value.skills.tenant_id, value.skills.agent_id, value.skills.skills),
        tuple(SourceSection(source.category, WorkspaceSubject(source.subject.kind, source.subject.id),
            source.reference, source.content) for source in value.sources), value.include_current_time)


def _validate(snapshot: RunSnapshot) -> None:
    if (len(snapshot.sources) > 64 or len(snapshot.tools.tools) > 128 or len(snapshot.skills.skills) > 128):
        raise InvalidSnapshot("Snapshot collection exceeds its bound")
    if not snapshot.initial_direct_names <= frozenset(tool.definition.spec.name for tool in snapshot.tools.tools):
        raise InvalidSnapshot("Snapshot initial exposure is outside captured authorization")
    if (not snapshot.platform.version or len(snapshot.platform.version.encode()) > 128
            or not snapshot.platform.text or not snapshot.agent.name or len(snapshot.agent.name.encode()) > 512
            or not snapshot.agent.soul or len(snapshot.agent.timezone) > 128):
        raise InvalidSnapshot("Snapshot instructions or Agent identity are invalid")
    for text in (snapshot.platform.text, snapshot.agent.soul):
        if len(text) > MAX_SNAPSHOT_BYTES or len(text.encode()) > MAX_SNAPSHOT_BYTES:
            raise InvalidSnapshot("Snapshot instructions exceed their byte bound")
    ZoneInfo(snapshot.agent.timezone)
    scope = snapshot.workspace
    own_agent = WorkspaceSubject("agent", snapshot.agent_id)
    if (scope.tenant_id != snapshot.tenant_id or scope.agent_id != snapshot.agent_id or scope.run_id is None
            or scope.main != (snapshot.role == "main") or (scope.output.kind == "agent" and scope.output != own_agent)
            or scope.preview_only):
        raise InvalidSnapshot("Snapshot Workspace authorization is inconsistent")
    if ((snapshot.tools.tenant_id, snapshot.tools.agent_id) != (snapshot.tenant_id, snapshot.agent_id)
            or (snapshot.skills.tenant_id, snapshot.skills.agent_id) != (snapshot.tenant_id, snapshot.agent_id)):
        raise InvalidSnapshot("Snapshot capability scope is inconsistent")
    if len(set(snapshot.skills.skills)) != len(snapshot.skills.skills) or any(
            not name or len(name.encode()) > 256 for name in snapshot.skills.skills):
        raise InvalidSnapshot("Snapshot Skill identities are invalid")
    if snapshot.model.policy.tenant_id != snapshot.tenant_id:
        raise InvalidSnapshot("Snapshot Model Tenant is inconsistent")
    validate_resolved_model(snapshot.model)
    for tool in snapshot.tools.tools:
        if tool.endpoint is not None:
            validate_endpoint(tool.endpoint)
        binding = tool.credential
        if binding is not None and ((binding.owner_kind == "tenant" and binding.owner_id != snapshot.tenant_id)
                or (binding.owner_kind == "agent" and binding.owner_id != snapshot.agent_id)):
            raise InvalidSnapshot("Snapshot Tool Credential owner is inconsistent")
    seen = set()
    for section in snapshot.sources:
        identity = (section.category, section.subject, section.reference)
        if (identity in seen or section.subject not in (scope.output, own_agent)
                or (section.category == "skill_index" and section.subject != own_agent)
                or not section.reference or len(section.reference.encode()) > 4096
                or len(section.content.encode()) > MAX_SOURCE_BYTES):
            raise InvalidSnapshot("Snapshot source section is invalid")
        seen.add(identity)


def _canonical(payload: object) -> str:
    envelope = {"kind": SNAPSHOT_KIND, "version": SNAPSHOT_VERSION, "payload": payload}
    _check_tree(envelope, maximum=MAX_SNAPSHOT_BYTES)
    return json.dumps(envelope, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def encode_snapshot(snapshot: RunSnapshot) -> EncodedSnapshot:
    encoded, _ = _encode_with_dto(snapshot)
    return encoded


def _encode_with_dto(snapshot: RunSnapshot) -> tuple[EncodedSnapshot, _SnapshotV1]:
    try:
        _validate(snapshot)
        dto = _to_v1(snapshot)
        payload = dto.model_dump(mode="json")
        canonical = _canonical(payload)
        if len(canonical.encode()) > MAX_SNAPSHOT_BYTES:
            raise InvalidSnapshot("Snapshot exceeds its byte bound")
        return EncodedSnapshot(SNAPSHOT_VERSION, hashlib.sha256(canonical.encode()).hexdigest(), payload), dto
    except InvalidSnapshot:
        raise
    except (ValueError, TypeError, AttributeError, UnicodeError, RecursionError, ValidationError, DomainError, ZoneInfoNotFoundError):
        raise InvalidSnapshot("Snapshot is invalid") from None


def decode_snapshot(version: int, payload: object, content_hash: str) -> RunSnapshot:
    try:
        _version(version)
        canonical = _canonical(payload)
        if hashlib.sha256(canonical.encode()).hexdigest() != content_hash:
            raise InvalidSnapshot("Snapshot content hash does not match")
        serialized = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
        snapshot = _from_v1(_SnapshotV1.model_validate_json(serialized))
        _validate(snapshot)
        if encode_snapshot(snapshot).content_hash != content_hash:
            raise InvalidSnapshot("Snapshot typed representation is not canonical")
        return snapshot
    except InvalidSnapshot:
        raise
    except (ValueError, TypeError, AttributeError, UnicodeError, RecursionError, ValidationError, DomainError, ZoneInfoNotFoundError):
        raise InvalidSnapshot("Snapshot is invalid") from None


def _version(version: int) -> None:
    if type(version) is not int or version != SNAPSHOT_VERSION:
        raise InvalidSnapshot("Unsupported Snapshot version")


def derive_child(parent: RunSnapshot, *, run_id: UUID) -> RunSnapshot:
    if parent.role != "main" or parent.workspace.run_id == run_id:
        raise InvalidSnapshot("Only Main may derive a distinct Child Snapshot")
    child = replace(parent, role="sub", workspace=parent.workspace.for_subagent(run_id))
    encoded = encode_snapshot(child)
    return decode_snapshot(encoded.version, encoded.payload, encoded.content_hash)


def model_visible_prefix(snapshot: RunSnapshot) -> tuple[VisibleSection, ...]:
    _validate(snapshot)
    identity = json.dumps({"name": snapshot.agent.name, "soul": snapshot.agent.soul,
        "timezone": snapshot.agent.timezone}, ensure_ascii=False, separators=(",", ":"))
    return (VisibleSection("platform", snapshot.platform.version, snapshot.platform.text),
        VisibleSection("agent", "agent", identity), *(VisibleSection(section.category,
            f"{section.subject.kind}:{section.subject.id}:{section.reference}", section.content) for section in snapshot.sources))


def _prepare_snapshot(snapshot: RunSnapshot) -> tuple[EncodedSnapshot, RunSnapshot]:
    encoded, dto = _encode_with_dto(snapshot)
    try:
        validated = _from_v1(dto)
        if _to_v1(validated) != dto:
            raise InvalidSnapshot("Snapshot typed representation is not canonical")
    except InvalidSnapshot:
        raise
    except (ValueError, TypeError, AttributeError, UnicodeError, RecursionError, ValidationError, DomainError):
        raise InvalidSnapshot("Snapshot is invalid") from None
    return encoded, validated


class SnapshotRepository:
    def __init__(self, transaction: TransactionContext) -> None:
        self._session = transaction.session

    async def insert(self, *, run_id: UUID, snapshot: RunSnapshot) -> RunSnapshot:
        encoded, validated = _prepare_snapshot(snapshot)
        run = await self._session.scalar(select(RunRecord).where(RunRecord.tenant_id == snapshot.tenant_id,
            RunRecord.id == run_id).with_for_update().execution_options(populate_existing=True))
        if run is None:
            raise NotFound("Run does not exist in this Tenant")
        if (run.agent_id != snapshot.agent_id or snapshot.workspace.run_id != run_id
                or (run.parent_run_id is None) != (snapshot.role == "main")):
            raise InvalidSnapshot("Snapshot does not match its Run")
        existing = await self._session.scalar(select(RunSnapshotRecord.content_hash).where(
            RunSnapshotRecord.tenant_id == snapshot.tenant_id, RunSnapshotRecord.run_id == run_id))
        if existing is not None:
            if existing != encoded.content_hash:
                raise Conflict("Run Snapshot cannot be replaced")
            return await self.read(tenant_id=snapshot.tenant_id, run_id=run_id)
        self._session.add(RunSnapshotRecord(run_id=run_id, tenant_id=snapshot.tenant_id, payload_kind=SNAPSHOT_KIND,
            schema_version=encoded.version, payload=encoded.payload, content_hash=encoded.content_hash,
            created_at=datetime.now(UTC)))
        await self._session.flush()
        return validated

    async def read(self, *, tenant_id: UUID, run_id: UUID) -> RunSnapshot:
        size = func.octet_length(cast(RunSnapshotRecord.payload, Text))
        metadata = (await self._session.execute(select(RunSnapshotRecord.payload_kind, RunSnapshotRecord.schema_version,
            RunSnapshotRecord.content_hash, size, RunRecord.agent_id, RunRecord.parent_run_id).join(RunRecord,
                (RunRecord.tenant_id == RunSnapshotRecord.tenant_id) & (RunRecord.id == RunSnapshotRecord.run_id))
            .where(RunSnapshotRecord.tenant_id == tenant_id,
                RunSnapshotRecord.run_id == run_id))).one_or_none()
        if metadata is None:
            raise NotFound("Snapshot does not exist in this Tenant")
        kind, version, content_hash, payload_bytes, agent_id, parent_id = metadata
        _version(version)
        if kind != SNAPSHOT_KIND or payload_bytes > MAX_SNAPSHOT_BYTES + MAX_NODES:
            raise InvalidSnapshot("Snapshot kind or stored byte bound is invalid")
        record = await self._session.scalar(select(RunSnapshotRecord).where(
            RunSnapshotRecord.tenant_id == tenant_id, RunSnapshotRecord.run_id == run_id,
            size <= payload_bytes, RunSnapshotRecord.content_hash == content_hash,
            RunSnapshotRecord.payload_kind == kind, RunSnapshotRecord.schema_version == version)
            .execution_options(populate_existing=True))
        if record is None:
            raise InvalidSnapshot("Snapshot changed during its bounded read")
        snapshot = decode_snapshot(record.schema_version, record.payload, record.content_hash)
        if (snapshot.tenant_id != tenant_id or snapshot.workspace.run_id != run_id or snapshot.agent_id != agent_id
                or (snapshot.role == "main") != (parent_id is None)):
            raise InvalidSnapshot("Snapshot identity does not match its storage scope")
        return snapshot
