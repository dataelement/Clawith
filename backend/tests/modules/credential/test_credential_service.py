from datetime import datetime

import pytest
from sqlalchemy import select

from app.infrastructure.errors import AccessDenied, InvalidInput, NotFound
from app.modules.credential.crypto import CredentialKeyring, Secret
from app.modules.credential.models import CredentialRecord
from app.modules.credential.public import CredentialService
from app.modules.identity_tenant.public import IdentityService, TenantPrincipal


@pytest.mark.asyncio
async def test_tenant_credential_roundtrip_rotation_and_cross_tenant_isolation(transaction_factory) -> None:
    async with transaction_factory() as tx:
        identity = IdentityService(tx)
        account = await identity.create_account()
        tenant = await identity.create_tenant(name="tenant")
        membership = await identity.create_membership(
            tenant_id=tenant.id,
            account_id=account.id,
            display_name="admin",
            role="tenant_admin",
        )
        other_tenant = await identity.create_tenant(name="other")
        other_membership = await identity.create_membership(
            tenant_id=other_tenant.id,
            account_id=account.id,
            display_name="other admin",
            role="tenant_admin",
        )

    principal = TenantPrincipal(account.id, membership.id, tenant.id, "tenant_admin")
    other_principal = TenantPrincipal(account.id, other_membership.id, other_tenant.id, "tenant_admin")
    keyring = CredentialKeyring(active_key_version="k1", keys={"k1": b"a" * 32})

    async with transaction_factory() as tx:
        service = CredentialService(tx, keyring)
        metadata = await service.create(
            principal,
            kind="api_token",
            provider="example",
            label="primary",
            secret=Secret("first"),
            owner_kind="tenant",
        )
        assert not hasattr(metadata, "encrypted_payload")
        assert not hasattr(metadata, "secret")

    async with transaction_factory() as tx:
        service = CredentialService(tx, keyring)
        assert await service.reveal_secret_for_owner(
            tenant_id=tenant.id,
            credential_id=metadata.id,
            owner_kind="tenant",
            owner_id=tenant.id,
        ) == Secret("first")
        await service.rotate_secret(principal, credential_id=metadata.id, secret=Secret("second"))

    async with transaction_factory() as tx:
        service = CredentialService(tx, keyring)
        assert await service.reveal_secret_for_owner(
            tenant_id=tenant.id,
            credential_id=metadata.id,
            owner_kind="tenant",
            owner_id=tenant.id,
        ) == Secret("second")
        with pytest.raises(NotFound):
            await service.get_metadata(other_principal, credential_id=metadata.id)
        with pytest.raises(NotFound):
            await service.reveal_secret_for_owner(
                tenant_id=other_tenant.id,
                credential_id=metadata.id,
                owner_kind="tenant",
                owner_id=other_tenant.id,
            )

    wrong_keyring = CredentialKeyring(active_key_version="k1", keys={"k1": b"b" * 32})
    async with transaction_factory() as tx:
        with pytest.raises(InvalidInput, match="authentication failed"):
            await CredentialService(tx, wrong_keyring).reveal_secret_for_owner(
                tenant_id=tenant.id,
                credential_id=metadata.id,
                owner_kind="tenant",
                owner_id=tenant.id,
            )

    async with transaction_factory() as tx:
        record = (
            await tx.session.scalars(
                select(CredentialRecord).where(CredentialRecord.id == metadata.id)
            )
        ).one()
        record.encrypted_payload = record.encrypted_payload[:-1] + bytes(
            [record.encrypted_payload[-1] ^ 1]
        )
        await tx.session.flush()

    async with transaction_factory() as tx:
        with pytest.raises(InvalidInput, match="authentication failed"):
            await CredentialService(tx, keyring).reveal_secret_for_owner(
                tenant_id=tenant.id,
                credential_id=metadata.id,
                owner_kind="tenant",
                owner_id=tenant.id,
            )


@pytest.mark.asyncio
async def test_tenant_owner_validator_rejects_membership_owner_and_revocation(transaction_factory) -> None:
    async with transaction_factory() as tx:
        identity = IdentityService(tx)
        account = await identity.create_account()
        tenant = await identity.create_tenant(name="tenant")
        membership = await identity.create_membership(
            tenant_id=tenant.id, account_id=account.id, display_name="admin", role="tenant_admin"
        )
    principal = TenantPrincipal(account.id, membership.id, tenant.id, "tenant_admin")
    keyring = CredentialKeyring(active_key_version="k1", keys={"k1": b"a" * 32})

    async with transaction_factory() as tx:
        service = CredentialService(tx, keyring)
        personal = await service.create(
            principal,
            kind="api_token",
            provider="example",
            label="personal",
            secret=Secret("value"),
            owner_kind="membership",
            owner_id=membership.id,
        )
        tenant_owned = await service.create(
            principal,
            kind="api_token",
            provider="example",
            label="tenant",
            secret=Secret("value"),
            owner_kind="tenant",
        )

    async with transaction_factory() as tx:
        service = CredentialService(tx)
        with pytest.raises(AccessDenied):
            await service.require_tenant_owned_metadata(principal, credential_id=personal.id)
        await service.require_tenant_owned_metadata(principal, credential_id=tenant_owned.id)
        await CredentialService(tx, keyring).revoke(principal, credential_id=tenant_owned.id)

    async with transaction_factory() as tx:
        with pytest.raises(NotFound):
            await CredentialService(tx).require_tenant_owned_metadata(
                principal, credential_id=tenant_owned.id
            )


@pytest.mark.asyncio
async def test_list_filters_authorized_scope_before_pagination(transaction_factory) -> None:
    async with transaction_factory() as tx:
        identity = IdentityService(tx)
        account = await identity.create_account()
        tenant = await identity.create_tenant(name="tenant")
        membership = await identity.create_membership(
            tenant_id=tenant.id, account_id=account.id, display_name="member", role="member"
        )
        admin_account = await identity.create_account()
        admin_membership = await identity.create_membership(
            tenant_id=tenant.id,
            account_id=admin_account.id,
            display_name="admin",
            role="tenant_admin",
        )
    member = TenantPrincipal(account.id, membership.id, tenant.id, "member")
    admin = TenantPrincipal(admin_account.id, admin_membership.id, tenant.id, "tenant_admin")
    keyring = CredentialKeyring(active_key_version="k1", keys={"k1": b"a" * 32})

    async with transaction_factory() as tx:
        service = CredentialService(tx, keyring)
        await service.create(
            admin,
            kind="api_token",
            provider="example",
            label="inaccessible-first",
            secret=Secret("tenant secret"),
            owner_kind="tenant",
        )
        accessible = await service.create(
            member,
            kind="api_token",
            provider="example",
            label="accessible-second",
            secret=Secret("member secret"),
            owner_kind="membership",
        )

    async with transaction_factory() as tx:
        page = await CredentialService(tx).list_metadata(member, limit=1)
        assert tuple(item.id for item in page) == (accessible.id,)


@pytest.mark.asyncio
async def test_naive_credential_expiry_is_rejected_on_create_and_update(transaction_factory) -> None:
    async with transaction_factory() as tx:
        identity = IdentityService(tx)
        account = await identity.create_account()
        tenant = await identity.create_tenant(name="tenant")
        membership = await identity.create_membership(
            tenant_id=tenant.id,
            account_id=account.id,
            display_name="admin",
            role="tenant_admin",
        )
    admin = TenantPrincipal(account.id, membership.id, tenant.id, "tenant_admin")
    keyring = CredentialKeyring(active_key_version="k1", keys={"k1": b"a" * 32})

    async with transaction_factory() as tx:
        service = CredentialService(tx, keyring)
        with pytest.raises(InvalidInput, match="timezone-aware"):
            await service.create(
                admin,
                kind="api_token",
                provider="example",
                label="invalid",
                secret=Secret("value"),
                owner_kind="tenant",
                expires_at=datetime(2030, 1, 1),  # noqa: DTZ001 - rejection input
            )
        credential = await service.create(
            admin,
            kind="api_token",
            provider="example",
            label="valid",
            secret=Secret("value"),
            owner_kind="tenant",
        )
        with pytest.raises(InvalidInput, match="timezone-aware"):
            await service.update_metadata(
                admin,
                credential_id=credential.id,
                expires_at=datetime(2030, 1, 1),  # noqa: DTZ001 - rejection input
            )
