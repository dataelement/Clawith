import uuid
from types import SimpleNamespace

import pytest

from app.services import agent_tools


def _make_agent(**overrides):
    values = {
        "id": uuid.uuid4(),
        "tenant_id": uuid.uuid4(),
        "creator_id": uuid.uuid4(),
        "access_mode": "company",
        "status": "running",
        "is_expired": False,
        "expires_at": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _make_member(**overrides):
    values = {
        "id": uuid.uuid4(),
        "tenant_id": uuid.uuid4(),
        "user_id": None,
        "name": "张三",
        "title": "",
        "status": "active",
        "provider_id": None,
        "external_id": None,
        "open_id": None,
        "unionid": None,
        "synced_at": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _make_provider(**overrides):
    values = {
        "id": uuid.uuid4(),
        "provider_type": "feishu",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _make_user(**overrides):
    values = {
        "id": uuid.uuid4(),
        "tenant_id": uuid.uuid4(),
        "display_name": "张三",
        "username": "zhangsan",
        "is_active": True,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


class DummyResult:
    def __init__(self, values=None, scalar_value=None):
        self._values = list(values or [])
        self._scalar_value = scalar_value

    def scalar_one_or_none(self):
        if self._scalar_value is not None:
            return self._scalar_value
        return self._values[0] if self._values else None

    def all(self):
        return list(self._values)


class RecordingDB:
    def __init__(self, responses):
        self.responses = list(responses)
        self.execute_count = 0

    async def execute(self, _statement, _params=None):
        self.execute_count += 1
        if not self.responses:
            raise AssertionError("unexpected execute() call")
        return self.responses.pop(0)


@pytest.mark.asyncio
async def test_resolve_roster_human_target_by_target_member_id():
    tenant_id = uuid.uuid4()
    source = _make_agent(tenant_id=tenant_id)
    provider = _make_provider(provider_type="feishu")
    member = _make_member(tenant_id=tenant_id, provider_id=provider.id, external_id="ou_1")
    db = RecordingDB([
        DummyResult(scalar_value=source),
        DummyResult(values=[(member, provider)]),
    ])

    target, error = await agent_tools._resolve_roster_human_target(
        db,
        source.id,
        target_member_id=str(member.id),
    )

    assert error is None
    assert target.member is member
    assert target.provider_type == "feishu"
    assert target.platform_user is None
    assert db.execute_count == 2


@pytest.mark.asyncio
async def test_resolve_roster_human_target_requires_active_platform_user():
    tenant_id = uuid.uuid4()
    user = _make_user(tenant_id=tenant_id)
    source = _make_agent(tenant_id=tenant_id)
    member = _make_member(tenant_id=tenant_id, user_id=user.id)
    db = RecordingDB([
        DummyResult(scalar_value=source),
        DummyResult(values=[(member, None)]),
        DummyResult(scalar_value=user),
    ])

    target, error = await agent_tools._resolve_roster_human_target(
        db,
        source.id,
        platform_user_id=str(user.id),
    )

    assert error is None
    assert target.member is member
    assert target.platform_user is user


@pytest.mark.asyncio
async def test_resolve_roster_human_target_rejects_member_name_ambiguity():
    tenant_id = uuid.uuid4()
    source = _make_agent(tenant_id=tenant_id)
    first = _make_member(tenant_id=tenant_id, name="张三")
    second = _make_member(tenant_id=tenant_id, name="张三")
    db = RecordingDB([
        DummyResult(scalar_value=source),
        DummyResult(values=[(first, None), (second, None)]),
    ])

    target, error = await agent_tools._resolve_roster_human_target(
        db,
        source.id,
        member_name="张三",
    )

    assert target is None
    assert "Multiple human recipients" in error


@pytest.mark.asyncio
async def test_resolve_roster_human_target_blocks_private_agent_other_people():
    tenant_id = uuid.uuid4()
    source = _make_agent(tenant_id=tenant_id, access_mode="private", creator_id=uuid.uuid4())
    member = _make_member(tenant_id=tenant_id, user_id=uuid.uuid4())
    db = RecordingDB([
        DummyResult(scalar_value=source),
        DummyResult(values=[(member, None)]),
    ])

    target, error = await agent_tools._resolve_roster_human_target(
        db,
        source.id,
        target_member_id=str(member.id),
    )

    assert target is None
    assert "not_visible" in error


@pytest.mark.asyncio
async def test_resolve_roster_human_target_rejects_provider_mismatch():
    tenant_id = uuid.uuid4()
    source = _make_agent(tenant_id=tenant_id)
    provider = _make_provider(provider_type="dingtalk")
    member = _make_member(tenant_id=tenant_id, provider_id=provider.id, external_id="user_1")
    db = RecordingDB([
        DummyResult(scalar_value=source),
        DummyResult(values=[(member, provider)]),
    ])

    target, error = await agent_tools._resolve_roster_human_target(
        db,
        source.id,
        provider_user_id="user_1",
        provider_type="feishu",
    )

    assert target is None
    assert "not in feishu channel" in error
