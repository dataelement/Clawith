import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.infrastructure.errors import AccessDenied
from app.modules.auth.models import LoginSessionRecord
from app.modules.auth.public import AuthService
from app.modules.identity_tenant.public import IdentityService, TenantPrincipal


class Clock:
    def __init__(self) -> None:
        self.value = datetime(2026, 9, 6, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.value


@pytest.mark.asyncio
async def test_login_snapshot_survives_role_edit_and_relogin_receives_new_scope(test_database, transaction_factory) -> None:
    async with transaction_factory() as tx:
        identity = IdentityService(tx)
        account = await identity.create_account()
        tenant = await identity.create_tenant(name="tenant")
        membership = await identity.create_membership(
            tenant_id=tenant.id, account_id=account.id, display_name="admin", role="tenant_admin"
        )

    auth = AuthService(test_database.sessions, session_ttl=timedelta(seconds=86_400))
    await auth.provision_trusted_verifier(
        account_id=account.id, login_name="Person@Example.com", password="password"
    )
    token, original = await auth.login("person@example.com", "password", tenant.id)
    assert original.role == "tenant_admin"

    async with transaction_factory() as tx:
        stored = (await tx.session.scalars(select(LoginSessionRecord))).one()
        assert stored.token_hash != token
        assert token not in stored.token_hash

    async with transaction_factory() as tx:
        await IdentityService(tx).update_membership(original, membership_id=membership.id, role="member")

    assert (await auth.authenticate(token)).role == "tenant_admin"
    _, refreshed = await auth.login("person@example.com", "password", tenant.id)
    assert refreshed.role == "member"


@pytest.mark.asyncio
async def test_wrong_password_cross_tenant_expiry_and_logout_are_denied(test_database, transaction_factory) -> None:
    async with transaction_factory() as tx:
        identity = IdentityService(tx)
        account = await identity.create_account()
        tenant = await identity.create_tenant(name="tenant")
        membership = await identity.create_membership(
            tenant_id=tenant.id, account_id=account.id, display_name="member", role="member"
        )
        other = await identity.create_tenant(name="other")

    clock = Clock()
    auth = AuthService(test_database.sessions, session_ttl=timedelta(seconds=10), clock=clock)
    await auth.provision_trusted_verifier(account_id=account.id, login_name="person", password="password")

    with pytest.raises(AccessDenied, match="invalid login credentials"):
        await auth.login("person", "wrong", tenant.id)
    with pytest.raises(AccessDenied):
        await auth.login("person", "password", other.id)

    token, principal = await auth.login("person", "password", tenant.id)
    assert principal == TenantPrincipal(account.id, membership.id, tenant.id, "member")
    captured = await auth.authenticate_session(token)
    assert captured.principal == principal
    assert captured.expires_at == clock.value + timedelta(seconds=10)
    clock.value += timedelta(seconds=1)
    assert (await auth.authenticate_session(token)).expires_at == captured.expires_at
    await auth.logout(token)
    with pytest.raises(AccessDenied):
        await auth.authenticate(token)

    expiring, _ = await auth.login("person", "password", tenant.id)
    clock.value += timedelta(seconds=10)
    with pytest.raises(AccessDenied):
        await auth.authenticate(expiring)


@pytest.mark.asyncio
async def test_corrupt_or_unknown_authorization_snapshot_is_rejected(test_database, transaction_factory) -> None:
    async with transaction_factory() as tx:
        identity = IdentityService(tx)
        account = await identity.create_account()
        tenant = await identity.create_tenant(name="tenant")
        await identity.create_membership(
            tenant_id=tenant.id, account_id=account.id, display_name="member", role="member"
        )
    auth = AuthService(test_database.sessions, session_ttl=timedelta(seconds=30))
    await auth.provision_trusted_verifier(account_id=account.id, login_name="person", password="password")
    token, _ = await auth.login("person", "password", tenant.id)

    async with transaction_factory() as tx:
        stored = (await tx.session.scalars(select(LoginSessionRecord))).one()
        stored.frozen_authorization = {**stored.frozen_authorization, "unexpected": True}
        await tx.session.flush()

    with pytest.raises(AccessDenied, match="snapshot is invalid"):
        await auth.authenticate(token)


@pytest.mark.asyncio
async def test_password_change_cannot_commit_between_final_verifier_check_and_session(
    test_database, transaction_factory, monkeypatch
) -> None:
    async with transaction_factory() as tx:
        identity = IdentityService(tx)
        account = await identity.create_account()
        tenant = await identity.create_tenant(name="tenant")
        await identity.create_membership(
            tenant_id=tenant.id, account_id=account.id, display_name="member", role="member"
        )
    auth = AuthService(test_database.sessions, session_ttl=timedelta(seconds=30))
    await auth.provision_trusted_verifier(account_id=account.id, login_name="person", password="old-password")

    verifier_locked = asyncio.Event()
    release_login = asyncio.Event()
    original_resolve = IdentityService.resolve_identity

    async def paused_resolve(self, *, account_id, tenant_id):
        verifier_locked.set()
        await release_login.wait()
        return await original_resolve(self, account_id=account_id, tenant_id=tenant_id)

    monkeypatch.setattr(IdentityService, "resolve_identity", paused_resolve)
    login_task = asyncio.create_task(auth.login("person", "old-password", tenant.id))
    await asyncio.wait_for(verifier_locked.wait(), timeout=2)
    password_change = asyncio.create_task(
        auth.provision_trusted_verifier(
            account_id=account.id, login_name="person", password="new-password"
        )
    )
    with pytest.raises(TimeoutError):
        await asyncio.wait_for(asyncio.shield(password_change), timeout=0.1)

    release_login.set()
    token, _ = await asyncio.wait_for(login_task, timeout=2)
    await asyncio.wait_for(password_change, timeout=2)
    assert await auth.authenticate(token)
    with pytest.raises(AccessDenied, match="invalid login credentials"):
        await auth.login("person", "old-password", tenant.id)
    assert await auth.login("person", "new-password", tenant.id)


@pytest.mark.asyncio
async def test_login_authorization_uses_one_repeatable_read_capture_point(
    test_database, transaction_factory, monkeypatch
) -> None:
    async with transaction_factory() as tx:
        identity = IdentityService(tx)
        account = await identity.create_account()
        tenant = await identity.create_tenant(name="tenant")
        membership = await identity.create_membership(
            tenant_id=tenant.id, account_id=account.id, display_name="admin", role="tenant_admin"
        )
    admin = TenantPrincipal(account.id, membership.id, tenant.id, "tenant_admin")
    auth = AuthService(test_database.sessions, session_ttl=timedelta(seconds=30))
    await auth.provision_trusted_verifier(account_id=account.id, login_name="person", password="password")

    capture_started = asyncio.Event()
    release_capture = asyncio.Event()
    original_resolve = IdentityService.resolve_identity

    async def paused_resolve(self, *, account_id, tenant_id):
        capture_started.set()
        await release_capture.wait()
        return await original_resolve(self, account_id=account_id, tenant_id=tenant_id)

    monkeypatch.setattr(IdentityService, "resolve_identity", paused_resolve)
    login_task = asyncio.create_task(auth.login("person", "password", tenant.id))
    await asyncio.wait_for(capture_started.wait(), timeout=2)
    async with transaction_factory() as tx:
        await IdentityService(tx).update_membership(
            admin, membership_id=membership.id, role="member"
        )
    release_capture.set()

    _, captured = await asyncio.wait_for(login_task, timeout=2)
    assert captured.role == "tenant_admin"
    monkeypatch.setattr(IdentityService, "resolve_identity", original_resolve)
    _, next_login = await auth.login("person", "password", tenant.id)
    assert next_login.role == "member"
