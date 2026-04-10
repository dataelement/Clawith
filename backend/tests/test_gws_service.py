import base64
import json
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config import get_settings
from app.core.security import decrypt_data, encrypt_data
from app.services import gws_service


settings = get_settings()


TENANT_ID = uuid.uuid4()
AGENT_ID = uuid.uuid4()
USER_ID = uuid.uuid4()
OTHER_USER_ID = uuid.uuid4()


class FakeScalarResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value

    def scalars(self):
        return self

    def all(self):
        return self._value if isinstance(self._value, list) else []


class FakeSession:
    def __init__(self, *, token=None, tokens=None, setting=None):
        self.token = token
        self.tokens = tokens or []
        self.setting = setting
        self.added = []
        self.committed = False

    async def execute(self, _query):
        if self.token or self.tokens:
            return FakeScalarResult(self.token or self.tokens)
        if self.setting:
            return FakeScalarResult(self.setting)
        return FakeScalarResult(None)

    def add(self, value):
        self.added.append(value)

    async def commit(self):
        self.committed = True

    async def flush(self):
        pass

    async def refresh(self, obj):
        pass

    async def delete(self, obj):
        pass


def _make_token_record(
    agent_id=AGENT_ID,
    user_id=USER_ID,
    tenant_id=TENANT_ID,
    status="active",
    expired=False,
):
    access_token = encrypt_data("test_access_token", settings.SECRET_KEY)
    refresh_token = encrypt_data("test_refresh_token", settings.SECRET_KEY)
    expiry = datetime.now(timezone.utc) - timedelta(minutes=10) if expired else datetime.now(timezone.utc) + timedelta(hours=1)

    return SimpleNamespace(
        id=uuid.uuid4(),
        agent_id=agent_id,
        user_id=user_id,
        tenant_id=tenant_id,
        google_email="test@gmail.com",
        google_user_id="google_sub_123",
        access_token=access_token,
        refresh_token=refresh_token,
        token_expiry=expiry,
        scopes=["openid", "email"],
        status=status,
        last_used_at=None,
    )


class TestOAuthStateEncryption:
    def test_generate_and_decrypt_roundtrip(self):
        state = gws_service.generate_oauth_state(AGENT_ID, USER_ID, TENANT_ID)
        assert isinstance(state, str)
        assert len(state) > 10

        decrypted = gws_service.decrypt_oauth_state(state)
        assert decrypted["agent_id"] == str(AGENT_ID)
        assert decrypted["user_id"] == str(USER_ID)
        assert decrypted["tenant_id"] == str(TENANT_ID)

    def test_decrypt_tampered_state_fails(self):
        state = gws_service.generate_oauth_state(AGENT_ID, USER_ID, TENANT_ID)
        tampered = state[:-5] + "XXXXX"
        with pytest.raises(Exception):
            gws_service.decrypt_oauth_state(tampered)

    def test_decrypt_invalid_base64_fails(self):
        with pytest.raises(Exception):
            gws_service.decrypt_oauth_state("not-valid-base64!!!")


class TestTenantGwsConfig:
    @pytest.mark.asyncio
    async def test_get_config_returns_decrypted_values(self):
        encrypted_secret = encrypt_data("my_secret", settings.SECRET_KEY)
        setting = SimpleNamespace(
            value={
                "client_id": "my_client_id",
                "client_secret": encrypted_secret,
                "project_id": "my_project",
            }
        )
        db = FakeSession(setting=setting)

        with patch("app.services.gws_service.async_session") as mock_session:
            mock_session.return_value.__aenter__ = AsyncMock(return_value=db)
            mock_session.return_value.__aexit__ = AsyncMock(return_value=False)
            result = await gws_service.get_tenant_gws_config(TENANT_ID)

        assert result["client_id"] == "my_client_id"
        assert result["client_secret"] == "my_secret"
        assert result["project_id"] == "my_project"

    @pytest.mark.asyncio
    async def test_get_config_missing_returns_empty(self):
        db = FakeSession()

        with patch("app.services.gws_service.async_session") as mock_session:
            mock_session.return_value.__aenter__ = AsyncMock(return_value=db)
            mock_session.return_value.__aexit__ = AsyncMock(return_value=False)
            result = await gws_service.get_tenant_gws_config(TENANT_ID)

        assert result == {}


class TestTokenRefresh:
    @pytest.mark.asyncio
    async def test_get_user_token_valid_returns_decrypted(self):
        token = _make_token_record(expired=False)

        db = FakeSession(token=token)
        with patch("app.services.gws_service.async_session") as mock_session:
            mock_session.return_value.__aenter__ = AsyncMock(return_value=db)
            mock_session.return_value.__aexit__ = AsyncMock(return_value=False)
            access_token = await gws_service.get_user_token_for_agent(AGENT_ID, USER_ID)

        assert access_token == "test_access_token"

    @pytest.mark.asyncio
    async def test_get_user_token_no_record_returns_none(self):
        db = FakeSession()

        with patch("app.services.gws_service.async_session") as mock_session:
            mock_session.return_value.__aenter__ = AsyncMock(return_value=db)
            mock_session.return_value.__aexit__ = AsyncMock(return_value=False)
            result = await gws_service.get_user_token_for_agent(AGENT_ID, USER_ID)

        assert result is None

    @pytest.mark.asyncio
    async def test_get_user_token_expired_triggers_refresh(self):
        token = _make_token_record(expired=True)

        db = FakeSession(token=token)
        new_access = encrypt_data("new_access_token", settings.SECRET_KEY)

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "access_token": "new_access_token",
            "expires_in": 3600,
        }

        with patch("app.services.gws_service.async_session") as mock_session, \
             patch("httpx.AsyncClient") as mock_client:
            mock_session.return_value.__aenter__ = AsyncMock(return_value=db)
            mock_session.return_value.__aexit__ = AsyncMock(return_value=False)

            client_instance = AsyncMock()
            client_instance.post.return_value = mock_response
            client_instance.__aenter__ = AsyncMock(return_value=client_instance)
            client_instance.__aexit__ = AsyncMock(return_value=False)
            mock_client.return_value = client_instance

            access_token = await gws_service.get_user_token_for_agent(AGENT_ID, USER_ID)

        assert access_token == "new_access_token"


class TestTokenRevocation:
    @pytest.mark.asyncio
    async def test_revoke_marks_token_revoked(self):
        token = _make_token_record()

        db = FakeSession(token=token)
        with patch("app.services.gws_service.async_session") as mock_session, \
             patch("httpx.AsyncClient") as mock_client:
            mock_session.return_value.__aenter__ = AsyncMock(return_value=db)
            mock_session.return_value.__aexit__ = AsyncMock(return_value=False)

            client_instance = AsyncMock()
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            client_instance.post.return_value = mock_resp
            client_instance.__aenter__ = AsyncMock(return_value=client_instance)
            client_instance.__aexit__ = AsyncMock(return_value=False)
            mock_client.return_value = client_instance

            await gws_service.revoke_oauth_token(AGENT_ID, USER_ID)

        assert token.status == "revoked"
        assert db.committed


class TestMultiUserIsolation:
    @pytest.mark.asyncio
    async def test_different_users_get_different_tokens(self):
        token_a = _make_token_record(
            user_id=USER_ID,
            access_token=encrypt_data("user_a_token", settings.SECRET_KEY),
        )
        token_b = _make_token_record(
            user_id=OTHER_USER_ID,
            access_token=encrypt_data("user_b_token", settings.SECRET_KEY),
        )

        db_a = FakeSession(token=token_a)
        db_b = FakeSession(token=token_b)

        with patch("app.services.gws_service.async_session") as mock_session:
            mock_session.return_value.__aenter__ = AsyncMock(return_value=db_a)
            mock_session.return_value.__aexit__ = AsyncMock(return_value=False)
            token_for_a = await gws_service.get_user_token_for_agent(AGENT_ID, USER_ID)

        with patch("app.services.gws_service.async_session") as mock_session:
            mock_session.return_value.__aenter__ = AsyncMock(return_value=db_b)
            mock_session.return_value.__aexit__ = AsyncMock(return_value=False)
            token_for_b = await gws_service.get_user_token_for_agent(AGENT_ID, OTHER_USER_ID)

        assert token_for_a == "user_a_token"
        assert token_for_b == "user_b_token"
        assert token_for_a != token_for_b

    @pytest.mark.asyncio
    async def test_revoking_user_a_does_not_affect_user_b(self):
        token_a = _make_token_record(user_id=USER_ID)
        token_b = _make_token_record(user_id=OTHER_USER_ID)

        db_a = FakeSession(token=token_a)
        with patch("app.services.gws_service.async_session") as mock_session, \
             patch("httpx.AsyncClient") as mock_client:
            mock_session.return_value.__aenter__ = AsyncMock(return_value=db_a)
            mock_session.return_value.__aexit__ = AsyncMock(return_value=False)

            client_instance = AsyncMock()
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            client_instance.post.return_value = mock_resp
            client_instance.__aenter__ = AsyncMock(return_value=client_instance)
            client_instance.__aexit__ = AsyncMock(return_value=False)
            mock_client.return_value = client_instance

            await gws_service.revoke_oauth_token(AGENT_ID, USER_ID)

        assert token_a.status == "revoked"
        assert token_b.status == "active"


class TestParseIdTokenEmail:
    def test_extracts_email_from_valid_id_token(self):
        header = base64.urlsafe_b64encode(json.dumps({"alg": "RS256"}).encode()).rstrip(b"=").decode()
        payload = base64.urlsafe_b64encode(json.dumps({"email": "user@gmail.com", "sub": "123"}).encode()).rstrip(b"=").decode()
        id_token = f"{header}.{payload}.signature"

        email = gws_service.parse_id_token_email(id_token)
        assert email == "user@gmail.com"

    def test_returns_none_for_invalid_token(self):
        assert gws_service.parse_id_token_email("not-a-jwt") is None

    def test_returns_none_for_missing_email(self):
        header = base64.urlsafe_b64encode(json.dumps({"alg": "RS256"}).encode()).rstrip(b"=").decode()
        payload = base64.urlsafe_b64encode(json.dumps({"sub": "123"}).encode()).rstrip(b"=").decode()
        id_token = f"{header}.{payload}.signature"

        assert gws_service.parse_id_token_email(id_token) is None
