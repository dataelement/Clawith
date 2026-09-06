import os
from datetime import UTC, datetime
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
from app.modules.permission.models import AgentVisibilityGrantRecord, AgentVisibilityRecord
from app.modules.permission.public import MAX_CAPTURED_AGENT_IDS, PermissionService


async def _tenant(transaction, name: str):
    identities = IdentityService(transaction)
    admin_account = await identities.create_account()
    member_account = await identities.create_account()
    tenant = await identities.create_tenant(name=name)
    admin = await identities.create_membership(
        tenant_id=tenant.id,
        account_id=admin_account.id,
        display_name=f"{name} admin",
        role="tenant_admin",
    )
    member = await identities.create_membership(
        tenant_id=tenant.id,
        account_id=member_account.id,
        display_name=f"{name} member",
        role="member",
    )
    admin_principal = TenantPrincipal(admin_account.id, admin.id, tenant.id, "tenant_admin")
    member_principal = TenantPrincipal(member_account.id, member.id, tenant.id, "member")
    keyring = CredentialKeyring(active_key_version="v1", keys={"v1": os.urandom(32)})
    credential = await CredentialService(transaction, keyring).create(
        admin_principal,
        kind="api_key",
        provider="openai",
        label="Credential",
        secret=Secret("test-only-secret"),
        owner_kind="tenant",
    )
    model = await ModelService(transaction).create(
        admin_principal,
        credential_id=credential.id,
        provider="openai",
        model_name="model",
        endpoint="https://provider.invalid/v1",
        context_limit=8192,
        output_limit=2048,
        capability_source="administrator",
        capabilities={"supports_tool_calling": True},
        settings_version=1,
        settings={},
    )
    agent = await AgentService(transaction).create(
        admin_principal,
        name=f"{name} Agent",
        soul="Be useful",
        timezone="UTC",
        model_id=model.id,
    )
    return admin_principal, member_principal, member, agent


@pytest.mark.asyncio
async def test_frozen_principal_does_not_change_after_grant_edits(transaction_factory) -> None:
    async with transaction_factory() as transaction:
        admin, member_principal, member, agent = await _tenant(transaction, "Tenant")
        permissions = PermissionService(transaction)
        await permissions.set_visibility(admin, agent_id=agent.id, visibility="restricted")
        await permissions.grant_membership(admin, agent_id=agent.id, membership_id=member.id)
        captured = await permissions.freeze_principal(member_principal)
        assert captured.allowed_agent_ids == frozenset({agent.id})

    async with transaction_factory() as transaction:
        permissions = PermissionService(transaction)
        await permissions.revoke_membership_grant(admin, agent_id=agent.id, membership_id=member.id)
        assert await transaction.session.scalar(select(func.count()).select_from(AgentVisibilityGrantRecord)) == 1
        assert await permissions.resolve_principal(captured, agent_id=agent.id) == "use"
        refreshed = await permissions.freeze_principal(member_principal)
        assert refreshed.allowed_agent_ids == frozenset()
        with pytest.raises(AccessDenied):
            await permissions.require_principal_access(refreshed, agent_id=agent.id)


@pytest.mark.asyncio
async def test_admin_scope_is_role_derived_without_agent_enumeration(transaction_factory) -> None:
    async with transaction_factory() as transaction:
        admin, _, _, agent = await _tenant(transaction, "Tenant")
        frozen = await PermissionService(transaction).freeze_principal(admin)
        assert frozen.allowed_agent_ids == frozenset()
        assert await PermissionService(transaction).resolve_principal(frozen, agent_id=agent.id) == "manage"


@pytest.mark.asyncio
async def test_visibility_grants_and_autonomous_intake_cannot_cross_tenants(
    transaction_factory,
) -> None:
    async with transaction_factory() as transaction:
        first_admin, _, _, first_agent = await _tenant(transaction, "First")
        _, _, second_member, second_agent = await _tenant(transaction, "Second")
        permissions = PermissionService(transaction)
        await permissions.set_visibility(first_admin, agent_id=first_agent.id, visibility="restricted")
        with pytest.raises(NotFound):
            await permissions.grant_membership(
                first_admin,
                agent_id=first_agent.id,
                membership_id=second_member.id,
            )
        with pytest.raises(NotFound):
            await permissions.grant_agent(
                first_admin,
                agent_id=first_agent.id,
                source_agent_id=second_agent.id,
            )
        with pytest.raises(NotFound):
            await permissions.resolve_autonomous(
                tenant_id=first_admin.tenant_id,
                source_agent_id=second_agent.id,
                target_agent_id=first_agent.id,
            )


@pytest.mark.asyncio
async def test_agent_grant_controls_autonomous_intake(transaction_factory) -> None:
    async with transaction_factory() as transaction:
        admin, _, _, target = await _tenant(transaction, "Tenant")
        source = await AgentService(transaction).create(
            admin,
            name="Source",
            soul="Be useful",
            timezone="UTC",
            model_id=target.model_id,
        )
        permissions = PermissionService(transaction)
        await permissions.set_visibility(admin, agent_id=target.id, visibility="restricted")
        assert (
            await permissions.resolve_autonomous(
                tenant_id=admin.tenant_id,
                source_agent_id=source.id,
                target_agent_id=target.id,
            )
            == "none"
        )
        await permissions.grant_agent(admin, agent_id=target.id, source_agent_id=source.id)
        assert (
            await permissions.resolve_autonomous(
                tenant_id=admin.tenant_id,
                source_agent_id=source.id,
                target_agent_id=target.id,
            )
            == "use"
        )


@pytest.mark.asyncio
async def test_member_visibility_above_capture_bound_fails_without_truncation(
    transaction_factory,
) -> None:
    async with transaction_factory() as transaction:
        admin, member_principal, _, seed = await _tenant(transaction, "Bounded")
        now = datetime.now(UTC)
        agents = [
            AgentRecord(
                id=uuid4(),
                tenant_id=admin.tenant_id,
                model_id=seed.model_id,
                created_by_membership_id=admin.membership_id,
                name=f"Agent {index}",
                avatar=None,
                description=None,
                greeting=None,
                soul="Be useful",
                timezone="UTC",
                enabled=True,
                archived_at=None,
                created_at=now,
                updated_at=now,
            )
            for index in range(MAX_CAPTURED_AGENT_IDS + 1)
        ]
        transaction.session.add_all(agents)
        await transaction.session.flush()
        transaction.session.add_all(
            AgentVisibilityRecord(
                id=uuid4(),
                tenant_id=admin.tenant_id,
                agent_id=agent.id,
                visibility="tenant",
                created_at=now,
                updated_at=now,
            )
            for agent in agents
        )
        await transaction.session.flush()

        with pytest.raises(InvalidInput, match="1000-Agent bound"):
            await PermissionService(transaction).freeze_principal(member_principal)
