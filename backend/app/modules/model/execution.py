"""Fixed-policy Model execution and provider-neutral request/result values."""

import asyncio
import json
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field, replace
from typing import Any, Literal, cast
from uuid import UUID, uuid4

import httpx
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.infrastructure.errors import DomainError, InvalidInput, NotFound
from app.infrastructure.http import require_stateless_http_client
from app.infrastructure.transactions import TransactionContext
from app.modules.credential.public import CredentialKeyring, CredentialService
from app.modules.model.continuation import ContinuationError, ContinuationStore
from app.modules.model.repository import ModelRepository

ModelProtocol = Literal["openai_chat", "openai_responses", "anthropic", "gemini"]
FinishReason = Literal["stop", "tool_calls", "length", "content_filter", "refusal"]
_ACCEPTED_CONFIGURATION = object()


@dataclass(frozen=True, slots=True)
class ModelHardLimits:
    context_limit: int
    output_limit: int

    def __post_init__(self) -> None:
        if (type(self.context_limit) is not int or type(self.output_limit) is not int
                or not 0 < self.output_limit <= self.context_limit):
            raise InvalidInput("Explicit positive Model hard limits are required")


@dataclass(frozen=True, slots=True)
class ModelCatalogEntry:
    provider: str
    endpoint: str
    model_name: str
    limits: ModelHardLimits


@dataclass(frozen=True, slots=True)
class ModelAcceptance:
    tenant_id: UUID
    credential_id: UUID
    provider: str
    model_name: str
    endpoint: str
    limits: ModelHardLimits
    capability_source: Literal["provider_metadata", "builtin_catalog", "administrator"]
    capabilities_json: str
    settings_json: str
    _proof: object = field(default=None, repr=False, compare=False)

    def matches(
        self, *, tenant_id: UUID, credential_id: UUID, provider: str, model_name: str, endpoint: str,
        context_limit: int, output_limit: int, capability_source: str, capabilities: Mapping[str, Any],
        settings_version: int, settings: Mapping[str, Any],
    ) -> bool:
        return self._proof is _ACCEPTED_CONFIGURATION and (
            self.tenant_id, self.credential_id, self.provider, self.model_name, self.endpoint,
            self.limits.context_limit, self.limits.output_limit, self.capability_source,
            json.loads(self.capabilities_json), json.loads(self.settings_json), 1,
        ) == (tenant_id, credential_id, provider, model_name, endpoint, context_limit, output_limit,
              capability_source, capabilities, settings, settings_version)


@dataclass(frozen=True, slots=True)
class ModelLimits:
    request_bytes: int = 8 * 1024 * 1024
    response_bytes: int = 8 * 1024 * 1024
    event_bytes: int = 1024 * 1024
    continuation_bytes: int = 8 * 1024 * 1024
    max_messages: int = 2048
    max_tools: int = 256
    timeout_seconds: float = 180

    def __post_init__(self) -> None:
        if any(value <= 0 for value in (
            self.request_bytes, self.response_bytes, self.event_bytes, self.continuation_bytes,
            self.max_messages, self.max_tools, self.timeout_seconds,
        )):
            raise ValueError("Model operation bounds must be positive")


@dataclass(frozen=True, slots=True)
class ModelContent:
    kind: Literal["text", "image"]
    value: str


@dataclass(frozen=True, slots=True)
class ModelToolCall:
    call_id: str
    name: str
    arguments_json: str


@dataclass(frozen=True, slots=True)
class ModelToolDefinition:
    name: str
    description: str
    schema_json: str


@dataclass(frozen=True, slots=True)
class ModelMessage:
    role: Literal["system", "user", "assistant", "tool"]
    content: tuple[ModelContent, ...] = ()
    calls: tuple[ModelToolCall, ...] = ()
    call_id: str | None = None
    is_error: bool = False
    interaction_id: str | None = None
    requires_continuation: bool = False
    cache_boundary: bool = False


@dataclass(frozen=True, slots=True)
class PrivateModelPolicy:
    tenant_id: UUID
    model_id: UUID
    provider: str
    protocol: ModelProtocol
    model_name: str
    endpoint: str = field(repr=False)
    credential_id: UUID = field(repr=False)
    context_limit: int
    output_limit: int
    capabilities_json: str
    settings_json: str = field(repr=False)


@dataclass(frozen=True, slots=True)
class ModelContextProfile:
    model_id: UUID
    provider: str
    model_name: str
    context_limit: int
    output_limit: int
    supports_images: bool
    supports_streaming: bool
    supports_prompt_cache: bool


@dataclass(frozen=True, slots=True)
class ResolvedModel:
    policy: PrivateModelPolicy
    profile: ModelContextProfile


@dataclass(frozen=True, slots=True)
class ModelStepRequest:
    run_id: UUID
    step_id: str
    messages: tuple[ModelMessage, ...]
    tools: tuple[ModelToolDefinition, ...]
    input_tokens: int
    output_tokens: int
    stream: bool = True


@dataclass(frozen=True, slots=True)
class ModelUsage:
    input_tokens: int | None = None
    output_tokens: int | None = None
    cache_read_tokens: int | None = None
    cache_write_tokens: int | None = None
    reasoning_tokens: int | None = None


@dataclass(frozen=True, slots=True)
class ModelStreamEvent:
    kind: Literal["text", "reasoning", "tool_arguments"]
    text: str
    index: int = 0
    call_id: str | None = None
    name: str | None = None


@dataclass(frozen=True, slots=True)
class ModelStepResult:
    content: str
    calls: tuple[ModelToolCall, ...]
    finish_reason: FinishReason
    usage: ModelUsage
    interaction_id: str
    requires_continuation: bool


@dataclass(frozen=True, slots=True)
class ModelFailure:
    code: str
    message: str
    unrecoverable: bool = False


ModelStepOutcome = ModelStepResult | ModelFailure
StreamObserver = Callable[[ModelStreamEvent], Awaitable[None]]


class ProviderFailure(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def json_object(encoded: str) -> dict[str, Any]:
    """Only external JSON shapes use Any; adapters validate fields before consumption."""
    try:
        value = json.loads(encoded, parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    except (ValueError, RecursionError):
        raise ProviderFailure("invalid_input", "Model JSON must be a finite object") from None
    if not isinstance(value, dict):
        raise ProviderFailure("invalid_input", "Model JSON must be an object")
    return value


class ModelExecutionService:
    """Application owns injected HTTP client and database factory; calls never close them."""

    def __init__(
        self, sessions: async_sessionmaker[AsyncSession], *, http_client: httpx.AsyncClient,
        credential_keyring: CredentialKeyring, continuation_keys: Mapping[str, bytes],
        active_continuation_key: str, limits: ModelLimits | None = None,
        builtin_catalog: tuple[ModelCatalogEntry, ...] = (),
    ) -> None:
        limits = limits or ModelLimits()
        require_stateless_http_client(http_client)
        self._sessions = sessions
        self._http = http_client
        self._credentials = credential_keyring
        self._limits = limits
        if len(builtin_catalog) > 4096:
            raise InvalidInput("Builtin Model catalog exceeds bound")
        self._catalog = {(entry.provider, entry.endpoint, entry.model_name): entry.limits for entry in builtin_catalog}
        if len(self._catalog) != len(builtin_catalog):
            raise InvalidInput("Builtin Model catalog identities must be unique")
        self._continuation = ContinuationStore(
            sessions, keys=continuation_keys, active_key=active_continuation_key,
            max_bytes=limits.continuation_bytes,
        )

    async def validate_configuration(
        self, *, tenant_id: UUID, credential_id: UUID, provider: str, protocol: ModelProtocol,
        model_name: str, endpoint: str, administrator_limits: ModelHardLimits | None,
        settings: Mapping[str, Any], capabilities: Mapping[str, Any],
    ) -> ModelAcceptance:
        """Configuration intake calls this before opening the business write transaction."""
        from app.modules.model.adapters import execute, metadata_limits
        from app.modules.model.public import _endpoint, _required_text, _validate_json_object

        if protocol not in {"openai_chat", "openai_responses", "anthropic", "gemini"}:
            raise InvalidInput("unsupported Model protocol")
        provider = _required_text(provider, field_name="provider", max_length=128)
        model_name = _required_text(model_name, field_name="model_name", max_length=200)
        endpoint = _endpoint(endpoint)
        settings_data = _validate_json_object(settings, field_name="settings", reject_secrets=True)
        if settings_data.get("protocol", protocol) != protocol:
            raise InvalidInput("Model configuration protocol differs from the validation protocol")
        settings_data["protocol"] = protocol
        settings_data = _validate_json_object(settings_data, field_name="settings", reject_secrets=True)
        capability_data = _validate_json_object(capabilities, field_name="capabilities", reject_secrets=True)
        async with self._sessions() as session:
            secret = await CredentialService(TransactionContext(session), self._credentials).reveal_secret_for_owner(
                tenant_id=tenant_id, credential_id=credential_id, owner_kind="tenant", owner_id=tenant_id,
            )
        async with asyncio.timeout(self._limits.timeout_seconds):
            discovered = await metadata_limits(self._http, protocol, endpoint, model_name, secret.value, self._limits)
            source: Literal["provider_metadata", "builtin_catalog", "administrator"]
            if discovered is not None:
                hard = ModelHardLimits(*discovered)
                source = "provider_metadata"
            elif (provider, endpoint, model_name) in self._catalog:
                hard = self._catalog[provider, endpoint, model_name]
                source = "builtin_catalog"
            elif administrator_limits is not None:
                hard, source = administrator_limits, "administrator"
            else:
                raise InvalidInput("Model hard limits are unavailable; explicit administrator input is required")
            capability_data["supports_tool_calling"] = True
            policy = PrivateModelPolicy(tenant_id, uuid4(), provider, protocol, model_name, endpoint, credential_id,
                hard.context_limit, hard.output_limit, json.dumps(capability_data), json.dumps(settings_data))
            probe = ModelStepRequest(uuid4(), "capability-probe", (
                ModelMessage("user", (ModelContent("text", "Call capability_probe with value ok. Do not answer in text."),)),
            ), (ModelToolDefinition("capability_probe", "Return the requested test value; no side effects.",
                '{"type":"object","properties":{"value":{"type":"string","const":"ok"}},"required":["value"]}'),),
                0, hard.output_limit, False)
            result, _ = await execute(self._http, policy, probe, secret.value, {}, self._limits, None)
            if (result.finish_reason != "tool_calls" or len(result.calls) != 1
                    or result.calls[0].name != "capability_probe"
                    or json_object(result.calls[0].arguments_json) != {"value": "ok"}):
                raise InvalidInput("Model did not demonstrate the required Tool Calling contract")
        return ModelAcceptance(tenant_id, credential_id, provider, model_name, endpoint, hard, source,
                               json.dumps(capability_data), json.dumps(settings_data), _ACCEPTED_CONFIGURATION)

    async def resolve_policy(
        self, *, tenant_id: UUID, model_id: UUID, protocol: ModelProtocol,
    ) -> ResolvedModel:
        # Persistence is a trust boundary, using the same owner validators as configuration writes.
        from app.modules.model.public import _endpoint, _validate_configuration, _validate_json_object

        if protocol not in {"openai_chat", "openai_responses", "anthropic", "gemini"}:
            raise InvalidInput("unsupported Model protocol")
        async with self._sessions() as session:
            model = await ModelRepository(session).get_model(tenant_id, model_id)
            if model is None:
                raise NotFound("Model is unavailable in this Tenant")
            if not model.enabled or model.archived_at is not None:
                raise InvalidInput("Model is disabled")
            capabilities = _validate_json_object(model.capabilities, field_name="capabilities", reject_secrets=True)
            settings = _validate_json_object(model.settings, field_name="settings", reject_secrets=True)
            if settings.get("protocol") != protocol:
                raise InvalidInput("Requested protocol differs from the configured Model protocol")
            _validate_configuration(
                context_limit=model.context_limit, output_limit=model.output_limit,
                capability_source=model.capability_source, capabilities=capabilities,
                settings_version=model.settings_version, settings=settings, enabled=model.enabled,
            )
            endpoint = _endpoint(model.endpoint)
            policy = PrivateModelPolicy(
                model.tenant_id, model.id, model.provider, protocol, model.model_name, endpoint,
                model.credential_id, model.context_limit, model.output_limit,
                json.dumps(capabilities), json.dumps(settings),
            )
            profile = ModelContextProfile(
                model.id, model.provider, model.model_name, model.context_limit, model.output_limit,
                capabilities.get("supports_images") is True,
                capabilities.get("supports_streaming") is True,
                capabilities.get("supports_prompt_cache") is True,
            )
            return ResolvedModel(policy, profile)

    @property
    def operation_limits(self) -> ModelLimits:
        """Physical request limits consumed by Context, not a Run execution quota."""
        return self._limits

    async def execute_step(
        self, policy: PrivateModelPolicy, request: ModelStepRequest, *, on_event: StreamObserver | None = None,
    ) -> ModelStepOutcome:
        return await self._execute_request(policy, request, on_event=on_event, summary=False)

    async def execute_summary(self, policy: PrivateModelPolicy, request: ModelStepRequest) -> ModelStepOutcome:
        """One-shot text utility; never reads, replaces or promises Run continuation."""
        if request.stream or request.tools or any(
                message.role not in ("system", "user") or message.calls or message.call_id
                or message.interaction_id or message.requires_continuation
                or any(content.kind != "text" for content in message.content) for message in request.messages):
            return ModelFailure("invalid_summary_request", "Summary requests require only non-streaming text input")
        result = await self._execute_request(policy, request, on_event=None, summary=True)
        if isinstance(result, ModelFailure):
            return result
        if result.calls or result.finish_reason != "stop":
            return ModelFailure("invalid_summary_result", "Summary did not produce a complete text result")
        return replace(result, requires_continuation=False)

    async def _execute_request(self, policy: PrivateModelPolicy, request: ModelStepRequest, *,
            on_event: StreamObserver | None, summary: bool) -> ModelStepOutcome:
        from app.modules.model.adapters import execute

        try:
            self._validate_request(policy, request)
            async with asyncio.timeout(self._limits.timeout_seconds):
                state = {} if summary else await self._continuation.load(
                    policy.tenant_id, request.run_id, policy.model_id, policy.protocol,
                )
                for message in request.messages:
                    if message.requires_continuation and (
                        message.role != "assistant" or not message.interaction_id
                        or not state.get(message.interaction_id)
                    ):
                        raise ContinuationError("required Model continuation is missing")
                async with self._sessions() as session:
                    secret = await CredentialService(TransactionContext(session), self._credentials).reveal_secret_for_owner(
                        tenant_id=policy.tenant_id, credential_id=policy.credential_id,
                        owner_kind="tenant", owner_id=policy.tenant_id,
                    )
                result, replay = await execute(self._http, policy, request, secret.value, state, self._limits, on_event)
                if replay and not summary:
                    retained = {m.interaction_id for m in request.messages if m.interaction_id}
                    state = {key: value for key, value in state.items() if key in retained}
                    state[request.step_id] = replay
                    await self._continuation.save(
                        policy.tenant_id, request.run_id, policy.model_id, policy.protocol, state,
                    )
                return result
        except asyncio.CancelledError:
            raise
        except ContinuationError:
            return ModelFailure("continuation_unavailable", "Required Model continuation is unavailable", True)
        except SQLAlchemyError:
            return ModelFailure("persistence_failed", "Model persistence failed", True)
        except ProviderFailure as error:
            return ModelFailure(error.code, str(error), error.code not in {"rate_limited", "provider_unavailable"})
        except DomainError:
            return ModelFailure("credential_unavailable", "Configured Model Credential is unavailable", True)
        except (httpx.HTTPError, TimeoutError):
            return ModelFailure("transport_failed", "Model transport failed; partial output is not a complete result")

    async def count_input_tokens(self, policy: PrivateModelPolicy, request: ModelStepRequest) -> int | ModelFailure:
        """Count the fixed request through its Provider; never generate or mutate replay state.

        Callers may pass zero estimated input tokens while measuring. The returned
        Provider estimate still needs Context's normal fixed-window check.
        """
        from app.modules.model.adapters import count_input_tokens

        try:
            self._validate_request(policy, request)
            if (policy.protocol == "openai_chat"
                    and json_object(policy.capabilities_json).get("image_token_counting") != "openai_responses"):
                return ModelFailure("image_budget_unavailable", "This Model has no explicit image token counter", True)
            async with asyncio.timeout(min(10.0, self._limits.timeout_seconds)):
                state = await self._continuation.load(policy.tenant_id, request.run_id, policy.model_id, policy.protocol)
                for message in request.messages:
                    if message.requires_continuation and (
                        message.role != "assistant" or not message.interaction_id or not state.get(message.interaction_id)
                    ):
                        raise ContinuationError("required Model continuation is missing")
                async with self._sessions() as session:
                    secret = await CredentialService(TransactionContext(session), self._credentials).reveal_secret_for_owner(
                        tenant_id=policy.tenant_id, credential_id=policy.credential_id,
                        owner_kind="tenant", owner_id=policy.tenant_id,
                    )
                return await count_input_tokens(self._http, policy, request, secret.value, state, self._limits)
        except asyncio.CancelledError:
            raise
        except ContinuationError:
            return ModelFailure("continuation_unavailable", "Required Model continuation is unavailable", True)
        except SQLAlchemyError:
            return ModelFailure("persistence_failed", "Model persistence failed", True)
        except ProviderFailure as error:
            return ModelFailure(error.code, str(error), error.code not in {"rate_limited", "provider_unavailable"})
        except DomainError:
            return ModelFailure("credential_unavailable", "Configured Model Credential is unavailable", True)
        except (httpx.HTTPError, TimeoutError):
            return ModelFailure("transport_failed", "Model token counting transport failed")

    async def release_continuation(
        self, *, tenant_id: UUID, run_id: UUID, model_id: UUID,
        terminal_status: Literal["Completed", "Failed", "Cancelled", "Interrupted"],
    ) -> None:
        """Caller supplies committed terminal facts; cleanup failures propagate to its housekeeping lane."""
        if terminal_status not in {"Completed", "Failed", "Cancelled", "Interrupted"}:
            raise InvalidInput("continuation cleanup requires a committed terminal fact")
        await self._continuation.remove(tenant_id, run_id, model_id)

    def _validate_request(self, policy: PrivateModelPolicy, request: ModelStepRequest) -> None:
        if policy.protocol not in {"openai_chat", "openai_responses", "anthropic", "gemini"}:
            raise ProviderFailure("invalid_input", "Unsupported fixed Model protocol")
        if not request.step_id or len(request.step_id) > 256:
            raise ProviderFailure("invalid_input", "Model step identity is required and bounded")
        if not 0 <= request.input_tokens or not 0 < request.output_tokens <= policy.output_limit:
            raise ProviderFailure("budget_exceeded", "Model token bounds are invalid")
        if request.input_tokens + request.output_tokens > policy.context_limit:
            raise ProviderFailure("budget_exceeded", "Model context capability is exceeded")
        if not 0 < len(request.messages) <= self._limits.max_messages or len(request.tools) > self._limits.max_tools:
            raise ProviderFailure("input_too_large", "Model input cardinality exceeds bound")
        capabilities = json_object(policy.capabilities_json)
        if request.stream and capabilities.get("supports_streaming") is not True:
            raise ProviderFailure("unsupported_capability", "Model does not declare streaming support")
        pending: set[str] = set()
        identities: set[str] = set()
        names = {tool.name for tool in request.tools}
        if len(names) != len(request.tools):
            raise ProviderFailure("invalid_input", "Tool names must be unique")
        size = len(policy.settings_json.encode()) + len(policy.capabilities_json.encode())
        for tool in request.tools:
            size += len(tool.name.encode()) + len(tool.description.encode()) + len(tool.schema_json.encode())
        for message in request.messages:
            size += sum(len(content.value.encode()) for content in message.content)
            size += sum(len(call.arguments_json.encode()) + len(call.name.encode()) + len(call.call_id.encode())
                        for call in message.calls)
        if size > self._limits.request_bytes:
            raise ProviderFailure("input_too_large", "Model logical input exceeds byte bound")
        for index, message in enumerate(request.messages):
            if message.role == "system" and index != 0:
                raise ProviderFailure("invalid_input", "System instructions must have one leading segment")
            if message.interaction_id:
                if message.interaction_id in identities:
                    raise ProviderFailure("invalid_input", "Interaction identities must be unique")
                identities.add(message.interaction_id)
            if message.calls and message.role != "assistant":
                raise ProviderFailure("invalid_input", "Only assistant messages contain Tool Calls")
            if message.role == "tool":
                if message.call_id not in pending:
                    raise ProviderFailure("invalid_input", "Tool Result has no matching pending call")
                pending.remove(cast(str, message.call_id))
            elif pending:
                raise ProviderFailure("invalid_input", "Pending Tool Calls require matching results")
            for call in message.calls:
                if not call.call_id or call.call_id in pending:
                    raise ProviderFailure("invalid_input", "Tool Call identities must be unique")
                json_object(call.arguments_json)
                pending.add(call.call_id)
            for content in message.content:
                if content.kind == "image" and capabilities.get("supports_images") is not True:
                    raise ProviderFailure("unsupported_capability", "Model does not declare image support")
        if pending:
            raise ProviderFailure("invalid_input", "Pending Tool Calls require matching results")
