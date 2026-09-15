from uuid import uuid4

import pytest

from app.infrastructure.errors import AccessDenied, Conflict, NotFound
from app.modules.identity_tenant.public import IdentityService, TenantPrincipal, require_admin


@pytest.mark.asyncio
async def test_membership_uniqueness_is_tenant_scoped(transaction_factory) -> None:
    account_id = uuid4()
    first_tenant_id = uuid4()
    second_tenant_id = uuid4()
    async with transaction_factory() as transaction:
        service = IdentityService(transaction)
        await service.create_account(account_id=account_id)
        await service.create_tenant(name="First", tenant_id=first_tenant_id)
        await service.create_tenant(name="Second", tenant_id=second_tenant_id)
        await service.create_membership(
            tenant_id=first_tenant_id,
            account_id=account_id,
            display_name="First membership",
            role="member",
        )
        await service.create_membership(
            tenant_id=second_tenant_id,
            account_id=account_id,
            display_name="Second membership",
            role="member",
        )

    with pytest.raises(Conflict):
        async with transaction_factory() as transaction:
            await IdentityService(transaction).create_membership(
                tenant_id=first_tenant_id,
                account_id=account_id,
                display_name="Duplicate",
                role="member",
            )


@pytest.mark.asyncio
async def test_admin_membership_operations_cannot_cross_tenants(
    transaction_factory,
) -> None:
    admin_account_id = uuid4()
    other_account_id = uuid4()
    first_tenant_id = uuid4()
    second_tenant_id = uuid4()
    async with transaction_factory() as transaction:
        service = IdentityService(transaction)
        await service.create_account(account_id=admin_account_id)
        await service.create_account(account_id=other_account_id)
        await service.create_tenant(name="First", tenant_id=first_tenant_id)
        await service.create_tenant(name="Second", tenant_id=second_tenant_id)
        admin = await service.create_membership(
            tenant_id=first_tenant_id,
            account_id=admin_account_id,
            display_name="Admin",
            role="tenant_admin",
        )
        other = await service.create_membership(
            tenant_id=second_tenant_id,
            account_id=other_account_id,
            display_name="Other",
            role="member",
        )

    principal = TenantPrincipal(
        account_id=admin.account_id,
        membership_id=admin.id,
        tenant_id=admin.tenant_id,
        role="tenant_admin",
    )
    async with transaction_factory() as transaction:
        service = IdentityService(transaction)
        assert (
            await service.require_membership(
                tenant_id=first_tenant_id, membership_id=admin.id
            )
        ).id == admin.id
        with pytest.raises(NotFound):
            await service.require_membership(
                tenant_id=first_tenant_id, membership_id=other.id
            )
        assert {membership.id for membership in await service.list_memberships(principal)} == {
            admin.id
        }
        with pytest.raises(NotFound):
            await service.update_membership(
                principal, membership_id=other.id, enabled=False
            )


@pytest.mark.asyncio
async def test_resolved_principal_remains_fixed_after_role_edit(
    transaction_factory,
) -> None:
    admin_account_id = uuid4()
    member_account_id = uuid4()
    tenant_id = uuid4()
    async with transaction_factory() as transaction:
        service = IdentityService(transaction)
        await service.create_account(account_id=admin_account_id)
        await service.create_account(account_id=member_account_id)
        await service.create_tenant(name="Tenant", tenant_id=tenant_id)
        admin = await service.create_membership(
            tenant_id=tenant_id,
            account_id=admin_account_id,
            display_name="Admin",
            role="tenant_admin",
        )
        member = await service.create_membership(
            tenant_id=tenant_id,
            account_id=member_account_id,
            display_name="Member",
            role="member",
        )

    async with transaction_factory() as transaction:
        captured = (
            await IdentityService(transaction).resolve_identity(
                account_id=member_account_id, tenant_id=tenant_id
            )
        ).principal

    admin_principal = TenantPrincipal(
        account_id=admin.account_id,
        membership_id=admin.id,
        tenant_id=tenant_id,
        role="tenant_admin",
    )
    async with transaction_factory() as transaction:
        await IdentityService(transaction).update_membership(
            admin_principal, membership_id=member.id, role="tenant_admin"
        )

    assert captured.role == "member"
    with pytest.raises(AccessDenied):
        require_admin(captured)
    async with transaction_factory() as transaction:
        refreshed = (
            await IdentityService(transaction).resolve_identity(
                account_id=member_account_id, tenant_id=tenant_id
            )
        ).principal
    assert refreshed.role == "tenant_admin"


@pytest.mark.asyncio
@pytest.mark.parametrize("disabled_fact", ["account", "tenant", "membership"])
async def test_disabled_identity_fact_is_denied_at_login_resolution(
    transaction_factory,
    disabled_fact: str,
) -> None:
    account_id = uuid4()
    tenant_id = uuid4()
    async with transaction_factory() as transaction:
        service = IdentityService(transaction)
        await service.create_account(
            account_id=account_id, enabled=disabled_fact != "account"
        )
        await service.create_tenant(
            name="Tenant", tenant_id=tenant_id, enabled=disabled_fact != "tenant"
        )
        await service.create_membership(
            tenant_id=tenant_id,
            account_id=account_id,
            display_name="Member",
            role="tenant_admin",
            enabled=disabled_fact != "membership",
        )

    async with transaction_factory() as transaction:
        with pytest.raises(AccessDenied):
            await IdentityService(transaction).resolve_identity(
                account_id=account_id, tenant_id=tenant_id
            )
