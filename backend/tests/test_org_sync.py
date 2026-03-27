import uuid
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.api import enterprise as enterprise_api
from app.models.org import OrgDepartment, OrgMember
from app.models.user import User
from app.services import org_sync_service


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


class RecordingDB:
    def __init__(self, responses=None):
        self.responses = list(responses or [])
        self.executed = []
        self.added = []
        self.committed = False

    async def execute(self, statement):
        self.executed.append(statement)
        if self.responses:
            return self.responses.pop(0)
        return DummyResult()

    def add(self, obj):
        self.added.append(obj)

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
async def test_org_sync_public_config_falls_back_to_legacy_feishu_setting():
    legacy_setting = SimpleNamespace(
        value={
            "app_id": "cli_123",
            "app_secret": "legacy-secret",
            "last_synced_at": "2026-03-24T10:00:00+00:00",
        }
    )
    db = RecordingDB([DummyResult(None), DummyResult(legacy_setting)])

    value = await org_sync_service.org_sync_service.get_public_config(db)

    assert value["provider"] == "feishu"
    assert value["feishu"]["app_id"] == "cli_123"
    assert value["feishu"]["app_secret"] == ""
    assert value["feishu"]["has_secret"] is True
    assert value["feishu"]["last_synced_at"] == "2026-03-24T10:00:00+00:00"
    assert value["wecom"]["corp_id"] == ""
    assert value["wecom"]["has_secret"] is False


@pytest.mark.asyncio
async def test_org_sync_public_config_redacts_provider_secrets():
    stored_setting = SimpleNamespace(
        value={
            "provider": "wecom",
            "feishu": {"app_id": "cli_123", "app_secret": "keep-feishu", "last_synced_at": None},
            "wecom": {"corp_id": "ww123", "corp_secret": "keep-wecom", "last_synced_at": None},
        }
    )
    db = RecordingDB([DummyResult(stored_setting)])

    value = await org_sync_service.org_sync_service.get_public_config(db)

    assert value["feishu"]["app_secret"] == ""
    assert value["feishu"]["has_secret"] is True
    assert value["wecom"]["corp_secret"] == ""
    assert value["wecom"]["has_secret"] is True


@pytest.mark.asyncio
async def test_org_sync_save_config_preserves_existing_provider_secrets():
    existing_setting = SimpleNamespace(
        key="org_sync",
        value={
            "provider": "wecom",
            "feishu": {"app_id": "cli_123", "app_secret": "keep-feishu", "last_synced_at": None},
            "wecom": {"corp_id": "ww123", "corp_secret": "keep-wecom", "last_synced_at": None},
        },
    )
    db = RecordingDB([DummyResult(existing_setting), DummyResult(existing_setting)])

    saved = await org_sync_service.org_sync_service.save_config(
        db,
        {
            "provider": "wecom",
            "feishu": {"app_id": "cli_456", "app_secret": ""},
            "wecom": {"corp_id": "ww456", "corp_secret": ""},
        },
    )

    assert saved["feishu"]["app_secret"] == "keep-feishu"
    assert saved["wecom"]["corp_secret"] == "keep-wecom"
    assert existing_setting.value["feishu"]["app_id"] == "cli_456"
    assert existing_setting.value["wecom"]["corp_id"] == "ww456"
    assert db.committed is True


@pytest.mark.asyncio
async def test_get_active_provider_uses_stored_secrets_for_sync():
    stored_setting = SimpleNamespace(
        value={
            "provider": "wecom",
            "feishu": {"app_id": "cli_123", "app_secret": "keep-feishu", "last_synced_at": None},
            "wecom": {"corp_id": "ww123", "corp_secret": "keep-wecom", "last_synced_at": None},
        }
    )
    db = RecordingDB([DummyResult(stored_setting)])

    provider, provider_config, setting = await org_sync_service.org_sync_service.get_active_provider(db)

    assert provider == "wecom"
    assert provider_config["corp_id"] == "ww123"
    assert provider_config["corp_secret"] == "keep-wecom"
    assert setting["wecom"]["corp_secret"] == "keep-wecom"


@pytest.mark.asyncio
async def test_get_org_sync_setting_requires_admin():
    with pytest.raises(HTTPException) as excinfo:
        await enterprise_api.get_system_setting(
            key="org_sync",
            current_user=make_user(role="member"),
            db=RecordingDB(),
        )

    assert excinfo.value.status_code == 403
    assert excinfo.value.detail == "Admin access required"


@pytest.mark.asyncio
async def test_list_org_departments_filters_by_active_provider(monkeypatch):
    wecom_dept = OrgDepartment(
        wecom_id="2",
        sync_provider="wecom",
        name="Engineering",
        member_count=3,
    )
    db = RecordingDB([DummyResult(values=[wecom_dept])])

    async def fake_get_active_provider(_db):
        return "wecom", {"corp_id": "ww123"}, {"provider": "wecom"}

    monkeypatch.setattr(org_sync_service.org_sync_service, "get_active_provider", fake_get_active_provider)

    rows = await enterprise_api.list_org_departments(
        tenant_id=None,
        current_user=make_user(),
        db=db,
    )

    assert rows == [
        {
            "id": str(wecom_dept.id),
            "provider": "wecom",
            "feishu_id": None,
            "wecom_id": "2",
            "name": "Engineering",
            "parent_id": None,
            "path": None,
            "member_count": 3,
        }
    ]
    assert "sync_provider" in str(db.executed[0])


@pytest.mark.asyncio
async def test_list_org_members_filters_by_active_provider(monkeypatch):
    wecom_member = OrgMember(
        wecom_user_id="zhangsan",
        sync_provider="wecom",
        name="张三",
        email="zhangsan@example.com",
        title="Engineer",
        department_path="Root / Engineering",
    )
    db = RecordingDB([DummyResult(values=[wecom_member])])

    async def fake_get_active_provider(_db):
        return "wecom", {"corp_id": "ww123"}, {"provider": "wecom"}

    monkeypatch.setattr(org_sync_service.org_sync_service, "get_active_provider", fake_get_active_provider)

    rows = await enterprise_api.list_org_members(
        department_id=None,
        search=None,
        tenant_id=None,
        current_user=make_user(),
        db=db,
    )

    assert rows == [
        {
            "id": str(wecom_member.id),
            "provider": "wecom",
            "name": "张三",
            "email": "zhangsan@example.com",
            "title": "Engineer",
            "department_path": "Root / Engineering",
            "avatar_url": None,
        }
    ]
    assert "sync_provider" in str(db.executed[0])
