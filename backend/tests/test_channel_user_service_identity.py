"""channel_user_service 的 OrgMember 匹配与回填逻辑。

不走 DB: 用 FakeSession 吸收 session 方法, monkeypatch 替换查询入口。
聚焦: resolve_channel_user 如何组合 find → match → backfill → link 这一条链。
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

from app.services import channel_user_service as cus_mod
from app.services.channel_user_service import channel_user_service


class _FakeSession:
    """吸收 resolve_channel_user 用到的 session 方法, 行为对业务无副作用。"""

    def __init__(self) -> None:
        self.added: list = []
        self.flushed = 0

    def add(self, obj):
        self.added.append(obj)

    async def flush(self):
        self.flushed += 1

    async def get(self, model, key):
        return None

    async def execute(self, _query):
        class _R:
            def scalar_one_or_none(self_inner):
                return None
        return _R()


@pytest.fixture
def fake_session():
    return _FakeSession()


@pytest.fixture
def agent():
    return SimpleNamespace(id=uuid.uuid4(), tenant_id=uuid.uuid4(), name="A")


@pytest.fixture
def patch_provider(monkeypatch):
    """跳过 provider 查询; 直接返回固定 IdentityProvider."""
    provider = SimpleNamespace(id=uuid.uuid4(), tenant_id=None, provider_type="dingtalk")

    async def _fake_ensure(self, db, provider_type, tenant_id):
        return provider

    monkeypatch.setattr(
        cus_mod.ChannelUserService, "_ensure_provider", _fake_ensure
    )
    return provider


async def test_find_org_member_receives_candidate_ids(
    fake_session, agent, patch_provider, monkeypatch
):
    captured = {}

    async def _fake_find(self, db, provider_id, channel_type, candidate_ids):
        captured["ids"] = list(candidate_ids)
        user = SimpleNamespace(
            id=uuid.uuid4(), identity_id=None,
            display_name="Bob", avatar_url=None,
        )
        member = SimpleNamespace(id=uuid.uuid4(), user_id=user.id)
        fake_session._preloaded_user = user
        return member

    async def _fake_db_get(model, key):
        return fake_session._preloaded_user

    monkeypatch.setattr(cus_mod.ChannelUserService, "_find_org_member", _fake_find)
    monkeypatch.setattr(fake_session, "get", _fake_db_get, raising=False)

    await channel_user_service.resolve_channel_user(
        db=fake_session,
        agent=agent,
        channel_type="dingtalk",
        external_user_id="staff-1",
        extra_info={"unionid": "UNION-1"},
        extra_ids=["UNION-1"],
    )

    assert captured["ids"] == ["staff-1", "UNION-1"]


async def test_find_org_member_deduplicates_candidate_ids(
    fake_session, agent, patch_provider, monkeypatch
):
    captured = {}

    async def _fake_find(self, db, provider_id, channel_type, candidate_ids):
        captured["ids"] = list(candidate_ids)
        user = SimpleNamespace(id=uuid.uuid4(), identity_id=None)
        member = SimpleNamespace(id=uuid.uuid4(), user_id=user.id)
        fake_session._preloaded_user = user
        return member

    monkeypatch.setattr(cus_mod.ChannelUserService, "_find_org_member", _fake_find)

    async def _fake_db_get(model, key):
        return fake_session._preloaded_user

    monkeypatch.setattr(fake_session, "get", _fake_db_get, raising=False)

    await channel_user_service.resolve_channel_user(
        db=fake_session,
        agent=agent,
        channel_type="dingtalk",
        external_user_id="staff-1",
        extra_info={"unionid": "staff-1"},
        extra_ids=["staff-1"],
    )

    assert captured["ids"] == ["staff-1"]  # 去重
