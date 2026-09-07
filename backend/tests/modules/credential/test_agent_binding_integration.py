import pytest

from app.infrastructure.errors import AccessDenied
from app.modules.agent.public import AgentService
from app.modules.credential.crypto import CredentialKeyring, Secret
from app.modules.credential.public import CredentialService
from app.modules.identity_tenant.public import IdentityService, TenantPrincipal
from app.modules.model.public import ModelService


@pytest.mark.asyncio
async def test_agent_use_scope_cannot_read_or_manage_agent_credential(transaction_factory, model_acceptance) -> None:
    async with transaction_factory() as tx:
        identity = IdentityService(tx)
        admin_account = await identity.create_account()
        member_account = await identity.create_account()
        tenant = await identity.create_tenant(name="tenant")
        admin_membership = await identity.create_membership(
            tenant_id=tenant.id,
            account_id=admin_account.id,
            display_name="admin",
            role="tenant_admin",
        )
        member_membership = await identity.create_membership(
            tenant_id=tenant.id,
            account_id=member_account.id,
            display_name="member",
            role="member",
        )
    admin = TenantPrincipal(
        admin_account.id, admin_membership.id, tenant.id, "tenant_admin"
    )
    keyring = CredentialKeyring(active_key_version="k1", keys={"k1": b"a" * 32})

    async with transaction_factory() as tx:
        credentials = CredentialService(tx, keyring)
        model_credential = await credentials.create(
            admin,
            kind="api_token",
            provider="example",
            label="model",
            secret=Secret("model secret"),
            owner_kind="tenant",
        )
        model = await ModelService(tx).create(
            admin,
            credential_id=model_credential.id,
            provider="example",
            model_name="model",
            endpoint="https://example.test/v1",
            context_limit=4096,
            output_limit=1024,
            capability_source="administrator",
            capabilities={"supports_tool_calling": True},
            settings_version=1,
            settings={"protocol": "openai_chat"},
            enabled=False,
        )
    accepted = await model_acceptance(admin, model, keyring)
    async with transaction_factory() as tx:
        credentials = CredentialService(tx, keyring)
        await ModelService(tx).set_enabled(admin, model_id=model.id, enabled=True, acceptance=accepted)
        agent = await AgentService(tx).create(
            admin,
            name="agent",
            soul="help",
            timezone="UTC",
            model_id=model.id,
        )
        agent_credential = await credentials.create(
            admin,
            kind="api_token",
            provider="example",
            label="agent",
            secret=Secret("agent secret"),
            owner_kind="agent",
            owner_id=agent.id,
        )

    member = TenantPrincipal(
        member_account.id,
        member_membership.id,
        tenant.id,
        "member",
        frozenset({agent.id}),
    )
    async with transaction_factory() as tx:
        service = CredentialService(tx, keyring)
        with pytest.raises(AccessDenied):
            await service.create(
                member,
                kind="api_token",
                provider="example",
                label="forbidden",
                secret=Secret("value"),
                owner_kind="agent",
                owner_id=agent.id,
            )
        with pytest.raises(AccessDenied):
            await service.get_metadata(member, credential_id=agent_credential.id)
        assert await service.list_metadata(member) == ()
        with pytest.raises(AccessDenied):
            await service.update_metadata(
                member, credential_id=agent_credential.id, label="forbidden"
            )
        with pytest.raises(AccessDenied):
            await service.rotate_secret(
                member, credential_id=agent_credential.id, secret=Secret("forbidden")
            )
        with pytest.raises(AccessDenied):
            await service.revoke(member, credential_id=agent_credential.id)
