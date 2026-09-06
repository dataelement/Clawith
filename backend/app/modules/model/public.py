"""Public Tenant Model configuration contracts."""

import json
import math
import re
from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal, cast
from urllib.parse import parse_qsl, urlsplit
from uuid import UUID, uuid4

from sqlalchemy.exc import IntegrityError

from app.infrastructure.errors import Conflict, InvalidInput, NotFound
from app.infrastructure.transactions import TransactionContext
from app.modules.credential.public import CredentialService
from app.modules.identity_tenant.public import TenantPrincipal, require_admin
from app.modules.model.models import ModelRecord, TenantModelDefaultRecord
from app.modules.model.repository import ModelRepository

CapabilitySource = Literal["provider_metadata", "builtin_catalog", "administrator"]
MAX_PAGE_SIZE = 100
MODEL_CONFIG_VERSION = 1
MAX_CONFIG_BYTES = 16_384
MAX_CONFIG_DEPTH = 8
MAX_CONFIG_ITEMS = 100
_SECRET_KEYS = frozenset(
    {
        "access_token",
        "accesstoken",
        "api_key",
        "apikey",
        "authorization",
        "client_secret",
        "clientsecret",
        "cookie",
        "credential",
        "credentials",
        "encrypted_payload",
        "password",
        "private_key",
        "privatekey",
        "refresh_token",
        "refreshtoken",
        "secret",
        "token",
    }
)


@dataclass(frozen=True, slots=True)
class ModelView:
    id: UUID
    tenant_id: UUID
    credential_id: UUID
    provider: str
    model_name: str
    endpoint: str
    context_limit: int
    output_limit: int
    capability_source: CapabilitySource
    capabilities: Mapping[str, Any]
    settings_version: int
    settings: Mapping[str, Any]
    enabled: bool
    archived_at: datetime | None
    created_at: datetime
    updated_at: datetime


class ModelService:
    """Manage Model configuration without executing Provider requests."""

    def __init__(self, transaction: TransactionContext) -> None:
        self._repository = ModelRepository(transaction.session)
        self._credentials = CredentialService(transaction)

    async def create(
        self,
        principal: TenantPrincipal,
        *,
        credential_id: UUID,
        provider: str,
        model_name: str,
        endpoint: str,
        context_limit: int,
        output_limit: int,
        capability_source: CapabilitySource,
        capabilities: Mapping[str, Any],
        settings_version: int,
        settings: Mapping[str, Any],
        model_id: UUID | None = None,
        enabled: bool = True,
    ) -> ModelView:
        require_admin(principal)
        credential = await self._credentials.require_tenant_owned_metadata(principal, credential_id=credential_id)
        normalized_provider = _required_text(provider, field_name="provider", max_length=128)
        if credential.provider != normalized_provider:
            raise InvalidInput("Model Provider must match its Credential Provider")
        normalized_capabilities = _validate_json_object(capabilities, field_name="capabilities", reject_secrets=True)
        normalized_settings = _validate_json_object(settings, field_name="settings", reject_secrets=True)
        _validate_configuration(
            context_limit=context_limit,
            output_limit=output_limit,
            capability_source=capability_source,
            capabilities=normalized_capabilities,
            settings_version=settings_version,
            settings=normalized_settings,
            enabled=enabled,
        )
        now = datetime.now(UTC)
        record = ModelRecord(
            id=model_id or uuid4(),
            tenant_id=principal.tenant_id,
            credential_id=credential.id,
            credential_owner_kind="tenant",
            provider=normalized_provider,
            model_name=_required_text(model_name, field_name="model_name", max_length=200),
            endpoint=_endpoint(endpoint),
            context_limit=context_limit,
            output_limit=output_limit,
            capability_source=capability_source,
            capabilities=normalized_capabilities,
            settings_version=settings_version,
            settings=normalized_settings,
            enabled=enabled,
            archived_at=None,
            created_at=now,
            updated_at=now,
        )
        self._repository.add_model(record)
        await self._flush_or_conflict("Model conflicts with existing data")
        return _view(record)

    async def get(self, principal: TenantPrincipal, *, model_id: UUID) -> ModelView:
        require_admin(principal)
        return _view(await self._require(principal.tenant_id, model_id))

    async def list(
        self, principal: TenantPrincipal, *, limit: int = MAX_PAGE_SIZE, offset: int = 0
    ) -> tuple[ModelView, ...]:
        require_admin(principal)
        _page(limit=limit, offset=offset)
        records = await self._repository.list_models(principal.tenant_id, limit=limit, offset=offset)
        return tuple(_view(record) for record in records)

    async def update(
        self,
        principal: TenantPrincipal,
        *,
        model_id: UUID,
        credential_id: UUID | None = None,
        provider: str | None = None,
        model_name: str | None = None,
        endpoint: str | None = None,
        context_limit: int | None = None,
        output_limit: int | None = None,
        capability_source: CapabilitySource | None = None,
        capabilities: Mapping[str, Any] | None = None,
        settings_version: int | None = None,
        settings: Mapping[str, Any] | None = None,
    ) -> ModelView:
        require_admin(principal)
        if all(
            value is None
            for value in (
                credential_id,
                provider,
                model_name,
                endpoint,
                context_limit,
                output_limit,
                capability_source,
                capabilities,
                settings_version,
                settings,
            )
        ):
            raise InvalidInput("at least one Model field must be provided")
        record = await self._require(principal.tenant_id, model_id)
        next_provider = (
            _required_text(provider, field_name="provider", max_length=128) if provider is not None else record.provider
        )
        if credential_id is not None:
            credential = await self._credentials.require_tenant_owned_metadata(principal, credential_id=credential_id)
            if credential.provider != next_provider:
                raise InvalidInput("Model Provider must match its Credential Provider")
        elif provider is not None:
            credential = await self._credentials.require_tenant_owned_metadata(
                principal, credential_id=record.credential_id
            )
            if credential.provider != next_provider:
                raise InvalidInput("Model Provider must match its Credential Provider")
        next_capabilities = (
            _validate_json_object(capabilities, field_name="capabilities", reject_secrets=True)
            if capabilities is not None
            else deepcopy(record.capabilities)
        )
        next_settings = (
            _validate_json_object(settings, field_name="settings", reject_secrets=True)
            if settings is not None
            else deepcopy(record.settings)
        )
        next_model_name = (
            _required_text(model_name, field_name="model_name", max_length=200)
            if model_name is not None
            else record.model_name
        )
        next_endpoint = _endpoint(endpoint) if endpoint is not None else record.endpoint
        _validate_configuration(
            context_limit=context_limit if context_limit is not None else record.context_limit,
            output_limit=output_limit if output_limit is not None else record.output_limit,
            capability_source=capability_source or cast(CapabilitySource, record.capability_source),
            capabilities=next_capabilities,
            settings_version=settings_version if settings_version is not None else record.settings_version,
            settings=next_settings,
            enabled=record.enabled,
        )
        record.provider = next_provider
        if credential_id is not None:
            record.credential_id = credential_id
        record.model_name = next_model_name
        record.endpoint = next_endpoint
        if context_limit is not None:
            record.context_limit = context_limit
        if output_limit is not None:
            record.output_limit = output_limit
        if capability_source is not None:
            record.capability_source = capability_source
        record.capabilities = next_capabilities
        if settings_version is not None:
            record.settings_version = settings_version
        record.settings = next_settings
        record.updated_at = datetime.now(UTC)
        await self._flush_or_conflict("Model update conflicts with existing data")
        return _view(record)

    async def set_enabled(self, principal: TenantPrincipal, *, model_id: UUID, enabled: bool) -> ModelView:
        require_admin(principal)
        record = await self._require(principal.tenant_id, model_id)
        if record.archived_at is not None and enabled:
            raise InvalidInput("an archived Model cannot be enabled")
        _validate_configuration(
            context_limit=record.context_limit,
            output_limit=record.output_limit,
            capability_source=cast(CapabilitySource, record.capability_source),
            capabilities=record.capabilities,
            settings_version=record.settings_version,
            settings=record.settings,
            enabled=enabled,
        )
        record.enabled = enabled
        record.updated_at = datetime.now(UTC)
        await self._repository.flush()
        return _view(record)

    async def archive(self, principal: TenantPrincipal, *, model_id: UUID) -> ModelView:
        require_admin(principal)
        record = await self._require(principal.tenant_id, model_id)
        if record.archived_at is None:
            now = datetime.now(UTC)
            record.archived_at = now
            record.enabled = False
            record.updated_at = now
            await self._repository.flush()
        return _view(record)

    async def set_default(self, principal: TenantPrincipal, *, model_id: UUID) -> ModelView:
        require_admin(principal)
        model = await self._require_selectable(principal.tenant_id, model_id)
        default = await self._repository.get_default(principal.tenant_id)
        now = datetime.now(UTC)
        if default is None:
            self._repository.add_default(
                TenantModelDefaultRecord(
                    id=uuid4(),
                    tenant_id=principal.tenant_id,
                    model_id=model.id,
                    created_at=now,
                    updated_at=now,
                )
            )
        else:
            default.model_id = model.id
            default.updated_at = now
        await self._flush_or_conflict("Tenant default Model conflicts with existing data")
        return _view(model)

    async def get_default(self, principal: TenantPrincipal) -> ModelView:
        require_admin(principal)
        default = await self._repository.get_default(principal.tenant_id)
        if default is None:
            raise NotFound("Tenant default Model is not configured")
        return _view(await self._require(principal.tenant_id, default.model_id))

    async def resolve_for_agent_creation(self, principal: TenantPrincipal, *, model_id: UUID | None) -> ModelView:
        """Resolve explicit/default Model once; callers persist the returned ID."""
        require_admin(principal)
        selected_id = model_id
        if selected_id is None:
            default = await self._repository.get_default(principal.tenant_id)
            if default is None:
                raise InvalidInput("model_id is required when no Tenant default Model exists")
            selected_id = default.model_id
        return _view(await self._require_selectable(principal.tenant_id, selected_id))

    async def _require(self, tenant_id: UUID, model_id: UUID) -> ModelRecord:
        record = await self._repository.get_model(tenant_id, model_id)
        if record is None:
            raise NotFound("Model does not exist in this Tenant")
        return record

    async def _require_selectable(self, tenant_id: UUID, model_id: UUID) -> ModelRecord:
        record = await self._require(tenant_id, model_id)
        if not record.enabled or record.archived_at is not None:
            raise InvalidInput("Model is not available for Agent selection")
        return record

    async def _flush_or_conflict(self, message: str) -> None:
        try:
            await self._repository.flush()
        except IntegrityError:
            raise Conflict(message) from None


def _validate_configuration(
    *,
    context_limit: int,
    output_limit: int,
    capability_source: str,
    capabilities: Mapping[str, Any],
    settings_version: int,
    settings: Mapping[str, Any],
    enabled: bool,
) -> None:
    if context_limit <= 0:
        raise InvalidInput("context_limit must be positive")
    if output_limit <= 0 or output_limit > context_limit:
        raise InvalidInput("output_limit must be positive and no greater than context_limit")
    if capability_source not in {"provider_metadata", "builtin_catalog", "administrator"}:
        raise InvalidInput("unsupported capability_source")
    if settings_version != MODEL_CONFIG_VERSION:
        raise InvalidInput(f"unsupported Model configuration version: {settings_version}")
    if enabled and capabilities.get("supports_tool_calling") is not True:
        raise InvalidInput("enabled Agent Models must explicitly support tool calling")


def _validate_json_object(value: Mapping[str, Any], *, field_name: str, reject_secrets: bool) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise InvalidInput(f"{field_name} must be an object")
    item_count = [0]
    validated = _validate_json_mapping(
        value,
        field_name=field_name,
        reject_secrets=reject_secrets,
        depth=1,
        item_count=item_count,
    )
    try:
        encoded = json.dumps(
            validated,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError):
        raise InvalidInput(f"{field_name} must contain finite JSON values") from None
    if len(encoded) > MAX_CONFIG_BYTES:
        raise InvalidInput(f"{field_name} must not exceed {MAX_CONFIG_BYTES} UTF-8 bytes")
    return validated


def _validate_json_mapping(
    value: Mapping[str, Any],
    *,
    field_name: str,
    reject_secrets: bool,
    depth: int,
    item_count: list[int],
) -> dict[str, Any]:
    _check_depth(field_name, depth)
    result: dict[str, Any] = {}
    for key, nested in value.items():
        if not isinstance(key, str) or not key:
            raise InvalidInput(f"{field_name} object keys must be non-empty strings")
        if reject_secrets and _is_secret_key(key):
            raise InvalidInput(f"{field_name} must not contain Credential or Secret fields")
        _increment_items(field_name, item_count)
        result[key] = _validate_json_value(
            nested,
            field_name=field_name,
            reject_secrets=reject_secrets,
            depth=depth + 1,
            item_count=item_count,
        )
    return result


def _validate_json_value(
    value: object,
    *,
    field_name: str,
    reject_secrets: bool,
    depth: int,
    item_count: list[int],
) -> Any:
    if isinstance(value, Mapping):
        return _validate_json_mapping(
            value,
            field_name=field_name,
            reject_secrets=reject_secrets,
            depth=depth,
            item_count=item_count,
        )
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        _check_depth(field_name, depth)
        result: list[Any] = []
        for nested in value:
            _increment_items(field_name, item_count)
            result.append(
                _validate_json_value(
                    nested,
                    field_name=field_name,
                    reject_secrets=reject_secrets,
                    depth=depth + 1,
                    item_count=item_count,
                )
            )
        return result
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float) and math.isfinite(value):
        return value
    raise InvalidInput(f"{field_name} must contain finite JSON values")


def _check_depth(field_name: str, depth: int) -> None:
    if depth > MAX_CONFIG_DEPTH:
        raise InvalidInput(f"{field_name} must not exceed {MAX_CONFIG_DEPTH} levels")


def _increment_items(field_name: str, item_count: list[int]) -> None:
    item_count[0] += 1
    if item_count[0] > MAX_CONFIG_ITEMS:
        raise InvalidInput(f"{field_name} must not contain more than {MAX_CONFIG_ITEMS} items")


def _is_secret_key(key: str) -> bool:
    normalized = re.sub(r"[^a-z0-9]+", "_", key.casefold()).strip("_")
    return normalized in _SECRET_KEYS or normalized.endswith(
        (
            "_access_token",
            "_api_key",
            "_client_secret",
            "_cookie",
            "_credential",
            "_password",
            "_private_key",
            "_refresh_token",
            "_secret",
            "_token",
        )
    )


def _endpoint(value: str) -> str:
    endpoint = _required_text(value, field_name="endpoint", max_length=2048)
    try:
        parsed = urlsplit(endpoint)
        query_keys = {key for key, _ in parse_qsl(parsed.query, keep_blank_values=True)}
    except ValueError:
        raise InvalidInput("endpoint is invalid") from None
    if parsed.username is not None or parsed.password is not None:
        raise InvalidInput("endpoint must not contain user information")
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise InvalidInput("endpoint must use HTTP(S) with a host")
    if any(_is_secret_key(key) for key in query_keys):
        raise InvalidInput("endpoint must not contain explicit Secret query parameters")
    return endpoint


def _required_text(value: str, *, field_name: str, max_length: int) -> str:
    normalized = value.strip()
    if not normalized or len(normalized) > max_length:
        raise InvalidInput(f"{field_name} must contain 1 to {max_length} characters")
    return normalized


def _page(*, limit: int, offset: int) -> None:
    if not 1 <= limit <= MAX_PAGE_SIZE:
        raise InvalidInput(f"limit must be between 1 and {MAX_PAGE_SIZE}")
    if offset < 0:
        raise InvalidInput("offset must be non-negative")


def _view(record: ModelRecord) -> ModelView:
    return ModelView(
        id=record.id,
        tenant_id=record.tenant_id,
        credential_id=record.credential_id,
        provider=record.provider,
        model_name=record.model_name,
        endpoint=record.endpoint,
        context_limit=record.context_limit,
        output_limit=record.output_limit,
        capability_source=cast(CapabilitySource, record.capability_source),
        capabilities=deepcopy(record.capabilities),
        settings_version=record.settings_version,
        settings=deepcopy(record.settings),
        enabled=record.enabled,
        archived_at=record.archived_at,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )
