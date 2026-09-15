import json
import os
from uuid import UUID

import pytest
from sqlalchemy import func, select

from app.infrastructure.errors import AccessDenied, InvalidInput, NotFound
from app.modules.credential.crypto import CredentialKeyring, Secret
from app.modules.credential.public import CredentialService
from app.modules.identity_tenant.public import IdentityService, TenantPrincipal
from app.modules.model.models import ModelRecord
from app.modules.model.public import (
    MAX_CONFIG_BYTES,
    MAX_CONFIG_DEPTH,
    MAX_CONFIG_ITEMS,
    ModelService,
)


async def _principal(transaction, *, name: str) -> TenantPrincipal:
    identities = IdentityService(transaction)
    account = await identities.create_account()
    tenant = await identities.create_tenant(name=name)
    membership = await identities.create_membership(
        tenant_id=tenant.id,
        account_id=account.id,
        display_name=f"{name} admin",
        role="tenant_admin",
    )
    return TenantPrincipal(account.id, membership.id, tenant.id, "tenant_admin")


async def _credential(transaction, principal: TenantPrincipal, *, owner_kind: str = "tenant") -> UUID:
    keyring = CredentialKeyring(active_key_version="v1", keys={"v1": os.urandom(32)})
    metadata = await CredentialService(transaction, keyring).create(
        principal,
        kind="api_key",
        provider="openai",
        label="Model credential",
        secret=Secret("test-only-secret"),
        owner_kind=owner_kind,  # type: ignore[arg-type]
        owner_id=principal.membership_id if owner_kind == "membership" else None,
    )
    return metadata.id


async def _create_model(
    service: ModelService,
    principal: TenantPrincipal,
    credential_id: UUID,
    *,
    model_name: str = "gpt-test",
):
    return await service.create(
        principal, enabled=False,
        credential_id=credential_id,
        provider="openai",
        model_name=model_name,
        endpoint="https://provider.invalid/v1",
        context_limit=8192,
        output_limit=2048,
        capability_source="administrator",
        capabilities={"supports_tool_calling": True},
        settings_version=1,
        settings={"temperature": 0},
    )


@pytest.mark.asyncio
async def test_model_binding_enforces_tenant_owned_credential_matrix(transaction_factory) -> None:
    async with transaction_factory() as transaction:
        first = await _principal(transaction, name="First")
        second = await _principal(transaction, name="Second")
        tenant_credential = await _credential(transaction, first)
        member_credential = await _credential(transaction, first, owner_kind="membership")
        other_credential = await _credential(transaction, second)
        service = ModelService(transaction)

        model = await _create_model(service, first, tenant_credential)
        assert model.tenant_id == first.tenant_id
        with pytest.raises(NotFound):
            await ModelService(transaction).get(second, model_id=model.id)

        with pytest.raises(AccessDenied):
            await _create_model(service, first, member_credential, model_name="wrong-owner")
        with pytest.raises(NotFound):
            await _create_model(service, first, other_credential, model_name="cross-tenant")


@pytest.mark.asyncio
async def test_model_requires_explicit_hard_capabilities_and_secret_free_settings(
    transaction_factory,
) -> None:
    async with transaction_factory() as transaction:
        principal = await _principal(transaction, name="Tenant")
        credential_id = await _credential(transaction, principal)
        service = ModelService(transaction)
        common = {
            "credential_id": credential_id,
            "provider": "openai",
            "model_name": "gpt-test",
            "endpoint": "https://provider.invalid/v1",
            "context_limit": 8192,
            "output_limit": 2048,
            "capability_source": "administrator",
            "settings_version": 1,
        }

        with pytest.raises(InvalidInput):
            await service.create(principal, **common, capabilities={}, settings={})
        with pytest.raises(InvalidInput):
            await service.create(
                principal, enabled=False,
                **common,
                capabilities={"supports_tool_calling": True},
                settings={"api_key": "must-not-enter-model-settings"},
            )


@pytest.mark.asyncio
async def test_model_archive_retains_record_and_prevents_selection(transaction_factory) -> None:
    async with transaction_factory() as transaction:
        principal = await _principal(transaction, name="Tenant")
        credential_id = await _credential(transaction, principal)
        service = ModelService(transaction)
        model = await _create_model(service, principal, credential_id)
        archived = await service.archive(principal, model_id=model.id)
        assert archived.archived_at is not None
        assert not archived.enabled
        with pytest.raises(InvalidInput):
            await service.resolve_for_agent_creation(principal, model_id=model.id)
        assert await transaction.session.scalar(select(func.count()).select_from(ModelRecord)) == 1


def _nested_object(depth: int):
    value = {}
    for _ in range(depth - 1):
        value = {"nested": value}
    return value


def _object_with_exact_encoded_size(size: int) -> dict[str, str]:
    empty_size = len(b'{"value":""}')
    remaining = size - empty_size
    value = "界" * (remaining // 3) + "a" * (remaining % 3)
    result = {"value": value}
    encoded = json.dumps(result, allow_nan=False, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode(
        "utf-8"
    )
    assert len(encoded) == size
    return result


@pytest.mark.asyncio
async def test_model_json_bounds_accept_at_limit_and_reject_above(transaction_factory) -> None:
    async with transaction_factory() as transaction:
        principal = await _principal(transaction, name="Bounds")
        credential_id = await _credential(transaction, principal)
        service = ModelService(transaction)
        common = {
            "credential_id": credential_id,
            "provider": "openai",
            "endpoint": "https://provider.invalid/v1",
            "context_limit": 8192,
            "output_limit": 2048,
            "capability_source": "administrator",
            "settings_version": 1,
            "capabilities": {"supports_tool_calling": True},
        }

        at_depth = await service.create(
            principal, enabled=False,
            **common,
            model_name="at-depth",
            settings=_nested_object(MAX_CONFIG_DEPTH),
        )
        assert at_depth.settings == _nested_object(MAX_CONFIG_DEPTH)
        at_items = await service.create(
            principal, enabled=False,
            **common,
            model_name="at-items",
            settings={f"key_{index}": index for index in range(MAX_CONFIG_ITEMS)},
        )
        assert len(at_items.settings) == MAX_CONFIG_ITEMS
        at_bytes = await service.create(
            principal, enabled=False,
            **common,
            model_name="at-bytes",
            settings=_object_with_exact_encoded_size(MAX_CONFIG_BYTES),
        )
        assert (
            len(
                json.dumps(
                    at_bytes.settings,
                    allow_nan=False,
                    ensure_ascii=False,
                    separators=(",", ":"),
                    sort_keys=True,
                ).encode("utf-8")
            )
            == MAX_CONFIG_BYTES
        )

        with pytest.raises(InvalidInput, match="levels"):
            await service.create(
                principal, enabled=False,
                **common,
                model_name="above-depth",
                settings=_nested_object(MAX_CONFIG_DEPTH + 1),
            )
        with pytest.raises(InvalidInput, match="items"):
            await service.create(
                principal, enabled=False,
                **common,
                model_name="above-items",
                settings={f"key_{index}": index for index in range(MAX_CONFIG_ITEMS + 1)},
            )
        oversized = _object_with_exact_encoded_size(MAX_CONFIG_BYTES)
        oversized["value"] += "界"
        with pytest.raises(InvalidInput, match="UTF-8 bytes"):
            await service.create(
                principal, enabled=False,
                **common,
                model_name="above-bytes",
                settings=oversized,
            )


@pytest.mark.asyncio
async def test_model_json_rejects_unknown_version_non_json_and_non_finite_values(
    transaction_factory,
) -> None:
    async with transaction_factory() as transaction:
        principal = await _principal(transaction, name="Formats")
        credential_id = await _credential(transaction, principal)
        service = ModelService(transaction)
        common = {
            "credential_id": credential_id,
            "provider": "openai",
            "model_name": "format",
            "endpoint": "https://provider.invalid/v1",
            "context_limit": 8192,
            "output_limit": 2048,
            "capability_source": "administrator",
            "capabilities": {"supports_tool_calling": True},
        }
        with pytest.raises(InvalidInput, match="unsupported Model configuration version"):
            await service.create(principal, **common, settings_version=2, settings={})
        with pytest.raises(InvalidInput, match="finite JSON values"):
            await service.create(principal, **common, settings_version=1, settings={"temperature": float("nan")})
        with pytest.raises(InvalidInput, match="finite JSON values"):
            await service.create(principal, **common, settings_version=1, settings={"payload": b"not-json"})


@pytest.mark.asyncio
async def test_model_json_is_deep_copied_at_input_and_view_boundaries(transaction_factory) -> None:
    async with transaction_factory() as transaction:
        principal = await _principal(transaction, name="Copies")
        credential_id = await _credential(transaction, principal)
        capabilities = {"supports_tool_calling": True, "features": {"streaming": True}}
        settings = {"sampling": {"temperature": 0.2}}
        service = ModelService(transaction)
        created = await service.create(
            principal, enabled=False,
            credential_id=credential_id,
            provider="openai",
            model_name="copied",
            endpoint="https://provider.invalid/v1",
            context_limit=8192,
            output_limit=2048,
            capability_source="administrator",
            capabilities=capabilities,
            settings_version=1,
            settings=settings,
        )
        capabilities["features"]["streaming"] = False
        settings["sampling"]["temperature"] = 1.0
        created.capabilities["features"]["streaming"] = False
        created.settings["sampling"]["temperature"] = 1.0

        reloaded = await service.get(principal, model_id=created.id)
        assert reloaded.capabilities["features"]["streaming"] is True
        assert reloaded.settings["sampling"]["temperature"] == 0.2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "secret_field",
    ["client-secret", "Private.Key", "access_token", "refreshToken", "session_cookie"],
)
async def test_model_settings_reject_nested_normalized_secret_fields(transaction_factory, secret_field: str) -> None:
    async with transaction_factory() as transaction:
        principal = await _principal(transaction, name=f"Secret {secret_field}")
        credential_id = await _credential(transaction, principal)
        with pytest.raises(InvalidInput, match="Secret fields"):
            await ModelService(transaction).create(
                principal,
                credential_id=credential_id,
                provider="openai",
                model_name="secret",
                endpoint="https://provider.invalid/v1",
                context_limit=8192,
                output_limit=2048,
                capability_source="administrator",
                capabilities={"supports_tool_calling": True},
                settings_version=1,
                settings={"nested": {secret_field: "must-not-persist"}},
            )


@pytest.mark.asyncio
async def test_model_capabilities_reject_secret_fields_on_create_and_update(transaction_factory) -> None:
    async with transaction_factory() as transaction:
        principal = await _principal(transaction, name="Capability secrets")
        credential_id = await _credential(transaction, principal)
        service = ModelService(transaction)
        common = {
            "credential_id": credential_id,
            "provider": "openai",
            "model_name": "capabilities",
            "endpoint": "https://provider.invalid/v1",
            "context_limit": 8192,
            "output_limit": 2048,
            "capability_source": "administrator",
            "settings_version": 1,
            "settings": {},
        }
        with pytest.raises(InvalidInput, match="capabilities.*Secret fields"):
            await service.create(
                principal, enabled=False,
                **common,
                capabilities={"supports_tool_calling": True, "nested": {"Access.Token": "secret"}},
            )
        model = await service.create(
            principal, enabled=False,
            **common,
            capabilities={"supports_tool_calling": True},
        )
        with pytest.raises(InvalidInput, match="capabilities.*Secret fields"):
            await service.update(
                principal,
                model_id=model.id,
                capabilities={"supports_tool_calling": True, "private-key": "secret"},
            )


@pytest.mark.asyncio
async def test_model_endpoint_rejects_explicit_secret_formats_only(transaction_factory) -> None:
    async with transaction_factory() as transaction:
        principal = await _principal(transaction, name="Endpoints")
        credential_id = await _credential(transaction, principal)
        service = ModelService(transaction)
        common = {
            "credential_id": credential_id,
            "provider": "openai",
            "model_name": "endpoint",
            "context_limit": 8192,
            "output_limit": 2048,
            "capability_source": "administrator",
            "capabilities": {"supports_tool_calling": True},
            "settings_version": 1,
            "settings": {},
        }
        safe = await service.create(
            principal, enabled=False,
            **common,
            endpoint="https://provider.invalid/v1?api-version=2026-09-06&organization=tenant",
        )
        assert "api-version" in safe.endpoint

        for endpoint in (
            "https://user:password@provider.invalid/v1",
            "https://provider.invalid/v1?access_token=secret",
            "https://provider.invalid/v1?CLIENT.SECRET=secret",
            "provider.invalid/v1",
            "ftp://provider.invalid/v1",
            "https:///v1",
        ):
            with pytest.raises(InvalidInput, match="user information|Secret query|HTTP"):
                await service.create(principal, enabled=False, **common, endpoint=endpoint)
