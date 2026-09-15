from datetime import UTC, datetime, timedelta

import httpx
from execution_dependencies.test_resources import composed_database, configured  # noqa: F401

from app.application import create_app
from app.infrastructure.transactions import transaction
from app.modules.auth.public import AuthService
from app.modules.identity_tenant.public import IdentityService


async def test_fixed_login_expiry_logout_and_no_secret_validation_echo(
        test_database, composed_database, tmp_path):  # noqa: F811
    async with transaction(test_database.sessions) as tx:
        identity = IdentityService(tx)
        account = await identity.create_account()
        tenant = await identity.create_tenant(name="Login test")
        await identity.create_membership(tenant_id=tenant.id, account_id=account.id,
            display_name="Member", role="member")
    app = create_app(configured(tmp_path))
    async with app.router.lifespan_context(app):
        now = [datetime(2026, 9, 9, tzinfo=UTC)]
        auth: AuthService = app.state.auth
        auth._clock = lambda: now[0]
        await auth.provision_trusted_verifier(account_id=account.id, login_name="member", password="secret-password")
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            credentials = {"login_name": "member", "password": "secret-password", "tenant_id": str(tenant.id)}
            logged = await client.post("/api/auth/login", json=credentials)
            assert logged.status_code == 200
            token = logged.json()["token"]
            deadline = datetime.fromisoformat(logged.json()["expires_at"])
            assert deadline == now[0] + timedelta(hours=24)
            headers = {"Authorization": "Bearer " + token}
            now[0] += timedelta(hours=23)
            current = await client.get("/api/auth/me", headers=headers)
            assert current.status_code == 200
            assert datetime.fromisoformat(current.json()["expires_at"]) == deadline
            now[0] = deadline
            assert (await client.get("/api/auth/me", headers=headers)).status_code == 401
            relogged = await client.post("/api/auth/login", json=credentials)
            headers = {"Authorization": "Bearer " + relogged.json()["token"]}
            assert (await client.post("/api/auth/logout", headers=headers)).status_code == 204
            assert (await client.get("/api/auth/me", headers=headers)).status_code == 401
            bad = await client.post("/api/auth/login", json={**credentials, "unknown": "private-secret"})
            assert bad.status_code == 422 and "secret" not in bad.text
            assert (await client.get("/api/auth/me")).status_code == 401
    assert not hasattr(app.state, "auth")
