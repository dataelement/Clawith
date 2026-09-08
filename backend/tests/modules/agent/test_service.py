import os
from dataclasses import replace
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from app.infrastructure.errors import AccessDenied, InvalidInput, NotFound
from app.modules.agent.models import AgentRecord
from app.modules.agent.public import AgentService
from app.modules.credential.crypto import CredentialKeyring, Secret
from app.modules.credential.public import CredentialService
from app.modules.identity_tenant.public import IdentityService, TenantPrincipal
from app.modules.model.public import ModelService
from app.modules.permission.public import PermissionService


async def _setup(transaction_factory, model_acceptance):
    async with transaction_factory() as transaction:
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
            settings={"protocol": "openai_chat"},
            enabled=False,
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
            settings={"protocol": "openai_chat"},
            enabled=False,
        )
    first_acceptance = await model_acceptance(principal, first, keyring)
    second_acceptance = await model_acceptance(principal, second, keyring)
    async with transaction_factory() as transaction:
        models = ModelService(transaction)
        await models.set_enabled(principal, model_id=first.id, enabled=True, acceptance=first_acceptance)
        await models.set_enabled(principal, model_id=second.id, enabled=True, acceptance=second_acceptance)
    return principal, first, second


@pytest.mark.asyncio
async def test_default_model_is_resolved_only_when_agent_is_created(transaction_factory, model_acceptance) -> None:
    principal, first, second = await _setup(transaction_factory, model_acceptance)
    async with transaction_factory() as transaction:
        models = ModelService(transaction)
        agents = AgentService(transaction)
        await models.set_default(principal, model_id=first.id)
        first_agent = await agents.create(principal, name="First", soul="Be useful", timezone="Asia/Shanghai")
        await models.set_default(principal, model_id=second.id)
        second_agent = await agents.create(principal, name="Second", soul="Be useful", timezone="UTC")

        assert first_agent.model_id == first.id
        assert second_agent.model_id == second.id
        assert (await agents.get(principal, agent_id=first_agent.id)).model_id == first.id


async def test_member_execution_view_preserves_management_boundary_and_captured_access(transaction_factory, model_acceptance):
    admin, model, _ = await _setup(transaction_factory, model_acceptance)
    async with transaction_factory() as tx:
        agents = AgentService(tx)
        agent = await agents.create(admin, name="Readable", soul="Execution identity", timezone="UTC", model_id=model.id)
        other = await agents.create(admin, name="Not allowed", soul="Private", timezone="UTC", model_id=model.id)
        identity = IdentityService(tx)
        account = await identity.create_account()
        membership = await identity.create_membership(tenant_id=admin.tenant_id, account_id=account.id,
            display_name="Member", role="member")
        permission = PermissionService(tx)
        await permission.set_visibility(admin, agent_id=agent.id, visibility="restricted")
        await permission.grant_membership(admin, agent_id=agent.id, membership_id=membership.id)
        member = await permission.freeze_principal(TenantPrincipal(account.id, membership.id, admin.tenant_id, "member"))
        view = await agents.get_for_execution(member, agent_id=agent.id)
        assert (view.name, view.soul, view.timezone, view.model_id) == ("Readable", "Execution identity", "UTC", model.id)
        with pytest.raises(AccessDenied):
            await agents.get(member, agent_id=agent.id)
        with pytest.raises(AccessDenied):
            await agents.get_for_execution(member, agent_id=other.id)
        with pytest.raises(NotFound):
            await agents.get_for_execution(replace(member, tenant_id=uuid4()), agent_id=agent.id)
        await permission.revoke_membership_grant(admin, agent_id=agent.id, membership_id=membership.id)
        assert await agents.get_for_execution(member, agent_id=agent.id) == view
        refreshed = await permission.freeze_principal(replace(member, allowed_agent_ids=frozenset()))
        with pytest.raises(AccessDenied):
            await agents.get_for_execution(refreshed, agent_id=agent.id)
        await agents.set_enabled(admin, agent_id=agent.id, enabled=False)
        with pytest.raises(NotFound):
            await agents.get_for_execution(member, agent_id=agent.id)


@pytest.mark.asyncio
async def test_agent_validates_soul_and_timezone(transaction_factory, model_acceptance) -> None:
    principal, first, _ = await _setup(transaction_factory, model_acceptance)
    async with transaction_factory() as transaction:
        models = ModelService(transaction)
        await models.set_default(principal, model_id=first.id)
        agents = AgentService(transaction)
        with pytest.raises(InvalidInput):
            await agents.create(principal, name="No soul", soul=" ", timezone="UTC")
        with pytest.raises(InvalidInput):
            await agents.create(principal, name="Bad timezone", soul="Present", timezone="Mars/Olympus")


@pytest.mark.asyncio
async def test_agent_archive_retains_record(transaction_factory, model_acceptance) -> None:
    principal, first, _ = await _setup(transaction_factory, model_acceptance)
    async with transaction_factory() as transaction:
        models = ModelService(transaction)
        await models.set_default(principal, model_id=first.id)
        agents = AgentService(transaction)
        agent = await agents.create(principal, name="Agent", soul="Present", timezone="UTC")
        archived = await agents.archive(principal, agent_id=agent.id)
        assert archived.archived_at is not None
        assert not archived.enabled
        assert await transaction.session.scalar(select(func.count()).select_from(AgentRecord)) == 1


@pytest.mark.asyncio
async def test_agent_update_can_clear_optional_presentation_fields(transaction_factory, model_acceptance) -> None:
    principal, first, _ = await _setup(transaction_factory, model_acceptance)
    async with transaction_factory() as transaction:
        models = ModelService(transaction)
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
