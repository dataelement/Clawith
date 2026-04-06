import contextlib
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from starlette.background import BackgroundTasks

from app.api import auth as auth_api
from app.api.notification import BroadcastRequest, broadcast_notification
from app.core.security import verify_password
from app.models.user import User
from app.schemas.schemas import ForgotPasswordRequest, ResetPasswordRequest
from app.services import password_reset_service, system_email_service


class DummyScalars:
    def __init__(self, values):
        self._values = list(values)

    def all(self):
        return list(self._values)


class DummyResult:
    def __init__(self, value=None, values=None):
        self._value = value
        self._values = list(values or [])

    def scalar_one_or_none(self):
        return self._value

    def scalars(self):
        return DummyScalars(self._values)


class MockRedis:
    def __init__(self, initial_data=None):
        self._data = initial_data or {}
        self.deleted = []
        self.setex_calls = []

    async def get(self, key):
        return self._data.get(key)

    async def delete(self, key):
        self.deleted.append(key)
        self._data.pop(key, None)

    async def setex(self, key, ttl, value):
        self.setex_calls.append((key, ttl, value))
        self._data[key] = value

    def pipeline(self, transaction=True):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        pass

    async def execute(self):
        pass


class RecordingDB:
    def __init__(self, responses=None):
        self.responses = list(responses or [])
        self.executed = []
        self.added = []
        self.flushed = False
        self.committed = False

    async def execute(self, statement):
        self.executed.append(statement)
        if self.responses:
            return self.responses.pop(0)
        return DummyResult()

    def add(self, obj):
        self.added.append(obj)

    async def flush(self):
        self.flushed = True

    async def commit(self):
        self.committed = True


def make_user(**overrides):
    values = {
        "id": uuid.uuid4(),
        "username": "alice",
        "email": "alice@example.com",
        "password_hash": "old-hash",
        "display_name": "Alice",
        "role": "member",
        "tenant_id": uuid.uuid4(),
        "is_active": True,
    }
    values.update(overrides)
    return User(**values)


@pytest.mark.asyncio
async def test_create_password_reset_token_invalidates_older_tokens(monkeypatch):
    monkeypatch.setattr(
        password_reset_service,
        "get_settings",
        lambda: SimpleNamespace(PASSWORD_RESET_TOKEN_EXPIRE_MINUTES=15, PUBLIC_BASE_URL=""),
    )
    mock_redis = MockRedis(initial_data={"pwd_reset:user:user-id-123": "old-token-hash"})
    async def fake_get_redis(): return mock_redis
    monkeypatch.setattr(password_reset_service, "get_redis", fake_get_redis)

    db = RecordingDB()
    user_id = uuid.uuid4()

    raw_token, expires_at = await password_reset_service.create_password_reset_token(user_id)

    # Verify old token invalidation
    assert "pwd_reset:token:old-token-hash" in mock_redis.deleted

    # Verify new token storage
    assert len(mock_redis.setex_calls) == 2
    # Verify raw token is long
    assert len(raw_token) >= 20
    assert expires_at > datetime.now(timezone.utc)


@pytest.mark.asyncio
async def test_build_password_reset_url_uses_env_public_base_url(monkeypatch):
    monkeypatch.setattr(
        password_reset_service,
        "get_settings",
        lambda: SimpleNamespace(PASSWORD_RESET_TOKEN_EXPIRE_MINUTES=30, PUBLIC_BASE_URL="https://app.example.com/"),
    )
    db = RecordingDB([DummyResult(None)])

    url = await password_reset_service.build_password_reset_url(db, "abc123")

    assert url == "https://app.example.com/reset-password?token=abc123"


@pytest.mark.asyncio
async def test_consume_password_reset_token_works_correctly(monkeypatch):
    user_id = uuid.uuid4()
    raw_token = "raw-token"
    token_hash = password_reset_service._hash_token(raw_token)
    
    initial_data = {
        f"pwd_reset:token:{token_hash}": str(user_id),
        f"pwd_reset:user:{user_id}": token_hash,
    }
    mock_redis = MockRedis(initial_data=initial_data)
    async def fake_get_redis(): return mock_redis
    monkeypatch.setattr(password_reset_service, "get_redis", fake_get_redis)

    db = RecordingDB()
    result = await password_reset_service.consume_password_reset_token(raw_token)

    assert result is not None
    assert result["user_id"] == user_id
    # Should be deleted after consumption
    assert f"pwd_reset:token:{token_hash}" in mock_redis.deleted
    assert f"pwd_reset:user:{user_id}" in mock_redis.deleted


@pytest.mark.asyncio
async def test_forgot_password_returns_generic_response_for_unknown_email():
    db = RecordingDB([DummyResult(None)])
    background_tasks = BackgroundTasks()

    response = await auth_api.forgot_password(
        ForgotPasswordRequest(email="missing@example.com"),
        background_tasks,
        db,
    )

    assert response == {
        "ok": True,
        "message": "If an account with that email exists, a password reset email has been sent.",
    }
    assert background_tasks.tasks == []





@pytest.mark.asyncio
async def test_forgot_password_queues_background_email(monkeypatch):
    user = make_user()
    db = RecordingDB([DummyResult(user)])
    background_tasks = BackgroundTasks()

    async def fake_create_password_reset_token(*_args, **_kwargs):
        return "raw-token", datetime.now(timezone.utc) + timedelta(minutes=30)

    async def fake_build_password_reset_url(*_args, **_kwargs):
        return "https://app.example.com/reset-password?token=raw-token"

    monkeypatch.setattr(password_reset_service, "create_password_reset_token", fake_create_password_reset_token)
    monkeypatch.setattr(password_reset_service, "build_password_reset_url", fake_build_password_reset_url)


    response = await auth_api.forgot_password(ForgotPasswordRequest(email=user.email), background_tasks, db)

    assert response["ok"] is True
    assert db.committed is True
    assert len(background_tasks.tasks) == 1





def test_send_system_email_uses_configured_timeout(monkeypatch):
    captured = {}

    class DummySMTPSSL:
        def __init__(self, host: str, port: int, context=None, timeout: int | None = None):
            captured["host"] = host
            captured["port"] = port
            captured["timeout"] = timeout

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def login(self, username: str, password: str):
            captured["username"] = username
            captured["password"] = password

        def sendmail(self, from_address: str, to_addresses: list[str], message: str):
            captured["from"] = from_address
            captured["to"] = to_addresses
            captured["has_message"] = bool(message)

    config = system_email_service.SystemEmailConfig(
        from_address="bot@example.com",
        from_name="Clawith",
        smtp_host="smtp.example.com",
        smtp_port=465,
        smtp_username="bot@example.com",
        smtp_password="secret",
        smtp_ssl=True,
        smtp_timeout_seconds=27,
    )
    monkeypatch.setattr(system_email_service.smtplib, "SMTP_SSL", DummySMTPSSL)
    monkeypatch.setattr(system_email_service, "force_ipv4", lambda: contextlib.nullcontext())

    system_email_service._send_email_with_config_sync(config, "alice@example.com", "subject", "body")

    assert captured["timeout"] == 27
    assert captured["to"] == ["alice@example.com"]


@pytest.mark.asyncio
async def test_reset_password_updates_user(monkeypatch):
    user = make_user(password_hash=auth_api.hash_password("old-password"))
    db = RecordingDB([DummyResult(user)])

    async def fake_consume_password_reset_token(*_args, **_kwargs):
        return {"user_id": user.id}

    monkeypatch.setattr(password_reset_service, "consume_password_reset_token", fake_consume_password_reset_token)

    response = await auth_api.reset_password(
        ResetPasswordRequest(token="t" * 20, new_password="new-password"),
        db,
    )

    assert response == {"ok": True}
    assert verify_password("new-password", user.password_hash)
    assert db.flushed is True


@pytest.mark.asyncio
async def test_broadcast_notification_rejects_missing_system_email_config(monkeypatch):
    current_user = make_user(role="org_admin")

    async def fake_resolve_email_config_async(db):
        return None

    monkeypatch.setattr(
        "app.services.system_email_service.resolve_email_config_async",
        fake_resolve_email_config_async,
    )

    with pytest.raises(HTTPException) as excinfo:
        await broadcast_notification(
            BroadcastRequest(title="Maintenance", body="Tonight", send_email=True),
            background_tasks=BackgroundTasks(),
            current_user=current_user,
            db=RecordingDB(),
        )

    assert excinfo.value.status_code == 400
    assert "System email is not configured" in excinfo.value.detail


@pytest.mark.asyncio
async def test_broadcast_notification_queues_email_delivery(monkeypatch):
    current_user = make_user(role="org_admin")
    target_user = make_user(email="bob@example.com", tenant_id=current_user.tenant_id)
    db = RecordingDB([
        DummyResult(values=[target_user]),
        DummyResult(values=[]),
    ])
    background_tasks = BackgroundTasks()

    async def fake_resolve_email_config_async(db):
        return system_email_service.SystemEmailConfig(
            from_address="bot@example.com",
            from_name="Clawith",
            smtp_host="smtp.example.com",
            smtp_port=465,
            smtp_username="bot@example.com",
            smtp_password="secret",
            smtp_ssl=True,
            smtp_timeout_seconds=15,
        )
    monkeypatch.setattr(
        "app.services.system_email_service.resolve_email_config_async",
        fake_resolve_email_config_async,
    )
    notifications = []

    async def fake_send_notification(*_args, **kwargs):
        notifications.append(kwargs)

    monkeypatch.setattr("app.services.notification_service.send_notification", fake_send_notification)

    response = await broadcast_notification(
        BroadcastRequest(title="Maintenance", body="Tonight", send_email=True),
        background_tasks=background_tasks,
        current_user=current_user,
        db=db,
    )

    assert response["ok"] is True
    assert response["emails_sent"] == 1
    assert db.committed is True
    assert len(notifications) == 1
    assert len(background_tasks.tasks) == 1


@pytest.mark.asyncio
async def test_deliver_broadcast_emails_continues_after_single_failure(monkeypatch):
    from app.services.system_email_service import BroadcastEmailRecipient, deliver_broadcast_emails

    delivered = []

    async def fake_send_system_email(email: str, subject: str, body: str) -> None:
        if email == "bad@example.com":
            raise RuntimeError("smtp down")
        delivered.append((email, subject, body))

    monkeypatch.setattr("app.services.system_email_service.send_system_email", fake_send_system_email)

    await deliver_broadcast_emails([
        BroadcastEmailRecipient(email="bad@example.com", subject="s1", body="b1"),
        BroadcastEmailRecipient(email="good@example.com", subject="s2", body="b2"),
    ])

    assert delivered == [("good@example.com", "s2", "b2")]
