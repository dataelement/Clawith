import os

import pytest
from sqlalchemy import func, select

from app.infrastructure.errors import InvalidInput
from app.modules.agent.models import AgentRecord
from app.modules.agent.public import AgentService
from app.modules.credential.crypto import CredentialKeyring, Secret
from app.modules.credential.public import CredentialService
from app.modules.identity_tenant.public import IdentityService, TenantPrincipal
from app.modules.model.public import ModelService


async def _setup(transaction):
    identities = IdentityService(transaction)
    account = await identities.create_account()
    tenant = await identities.create_tenant(name="Tenant")
    membership = await identities.create_membership(
        tenant_id=tenant.id,
        account_id=account.id,
        display_name="Admin",
        role="tenant_admin",
    )
    principal = TenantPrincipal(account.id, membership.id, tenant.id, "tenant_admin")
    keyring = CredentialKeyring(active_key_version="v1", keys={"v1": os.urandom(32)})
    credential = await CredentialService(transaction, keyring).create(
        principal,
        kind="api_key",
        provider="openai",
        label="Credential",
        secret=Secret("test-only-secret"),
        owner_kind="tenant",
    )
    models = ModelService(transaction)
    first = await models.create(
        principal,
        credential_id=credential.id,
        provider="openai",
        model_name="first",
        endpoint="https://provider.invalid/v1",
        context_limit=8192,
        output_limit=2048,
        capability_source="administrator",
        capabilities={"supports_tool_calling": True},
        settings_version=1,
        settings={},
    )
    second = await models.create(
        principal,
        credential_id=credential.id,
        provider="openai",
        model_name="second",
        endpoint="https://provider.invalid/v1",
        context_limit=8192,
        output_limit=2048,
        capability_source="administrator",
        capabilities={"supports_tool_calling": True},
        settings_version=1,
        settings={},
    )
    return principal, models, first, second


@pytest.mark.asyncio
async def test_default_model_is_resolved_only_when_agent_is_created(transaction_factory) -> None:
    async with transaction_factory() as transaction:
        principal, models, first, second = await _setup(transaction)
        agents = AgentService(transaction)
        await models.set_default(principal, model_id=first.id)
        first_agent = await agents.create(principal, name="First", soul="Be useful", timezone="Asia/Shanghai")
        await models.set_default(principal, model_id=second.id)
        second_agent = await agents.create(principal, name="Second", soul="Be useful", timezone="UTC")

        assert first_agent.model_id == first.id
        assert second_agent.model_id == second.id
        assert (await agents.get(principal, agent_id=first_agent.id)).model_id == first.id


@pytest.mark.asyncio
async def test_agent_validates_soul_and_timezone(transaction_factory) -> None:
    async with transaction_factory() as transaction:
        principal, models, first, _ = await _setup(transaction)
        await models.set_default(principal, model_id=first.id)
        agents = AgentService(transaction)
        with pytest.raises(InvalidInput):
            await agents.create(principal, name="No soul", soul=" ", timezone="UTC")
        with pytest.raises(InvalidInput):
            await agents.create(principal, name="Bad timezone", soul="Present", timezone="Mars/Olympus")


@pytest.mark.asyncio
async def test_agent_archive_retains_record(transaction_factory) -> None:
    async with transaction_factory() as transaction:
        principal, models, first, _ = await _setup(transaction)
        await models.set_default(principal, model_id=first.id)
        agents = AgentService(transaction)
        agent = await agents.create(principal, name="Agent", soul="Present", timezone="UTC")
        archived = await agents.archive(principal, agent_id=agent.id)
        assert archived.archived_at is not None
        assert not archived.enabled
        assert await transaction.session.scalar(select(func.count()).select_from(AgentRecord)) == 1


@pytest.mark.asyncio
async def test_agent_update_can_clear_optional_presentation_fields(transaction_factory) -> None:
    async with transaction_factory() as transaction:
        principal, models, first, _ = await _setup(transaction)
        await models.set_default(principal, model_id=first.id)
        agents = AgentService(transaction)
        agent = await agents.create(
            principal,
            name="Agent",
            soul="Present",
            timezone="UTC",
            avatar="https://assets.invalid/avatar.png",
            description="Description",
            greeting="Hello",
        )

        cleared = await agents.update(
            principal,
            agent_id=agent.id,
            avatar=None,
            description=None,
            greeting=None,
        )
        assert cleared.avatar is None
        assert cleared.description is None
        assert cleared.greeting is None
