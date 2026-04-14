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


class _RecordingSession:
    """Captures the SQL from db.execute without running it."""

    def __init__(self):
        self.last_stmt = None

    async def execute(self, stmt):
        self.last_stmt = stmt

        class _R:
            def scalar_one_or_none(self_inner):
                return None

        return _R()


async def test_find_org_member_sql_dingtalk():
    sess = _RecordingSession()
    await channel_user_service._find_org_member(
        sess, uuid.uuid4(), "dingtalk", ["staff-1", "UNION-1"]
    )
    sql = str(sess.last_stmt.compile(compile_kwargs={"literal_binds": True}))
    # Isolate the WHERE clause so SELECT-column references don't pollute checks
    where_clause = sql.split("WHERE", 1)[1]
    # dingtalk: OR over unionid + external_id, NOT open_id IN (...)
    assert "org_members.unionid IN" in where_clause
    assert "org_members.external_id IN" in where_clause
    assert "org_members.open_id IN" not in where_clause
    assert "'staff-1'" in where_clause and "'UNION-1'" in where_clause


async def test_find_org_member_sql_feishu():
    sess = _RecordingSession()
    await channel_user_service._find_org_member(
        sess, uuid.uuid4(), "feishu", ["ou_x", "on_y"]
    )
    sql = str(sess.last_stmt.compile(compile_kwargs={"literal_binds": True}))
    where_clause = sql.split("WHERE", 1)[1]
    # feishu: OR over unionid + open_id + external_id
    assert "org_members.unionid IN" in where_clause
    assert "org_members.open_id IN" in where_clause
    assert "org_members.external_id IN" in where_clause


async def test_find_org_member_sql_wecom():
    sess = _RecordingSession()
    await channel_user_service._find_org_member(
        sess, uuid.uuid4(), "wecom", ["userid-1"]
    )
    sql = str(sess.last_stmt.compile(compile_kwargs={"literal_binds": True}))
    where_clause = sql.split("WHERE", 1)[1]
    # wecom: external_id only, no unionid IN / open_id IN in WHERE
    assert "org_members.external_id IN" in where_clause
    assert "org_members.unionid IN" not in where_clause
    assert "org_members.open_id IN" not in where_clause


async def test_find_org_member_empty_ids_returns_none_without_execute():
    sess = _RecordingSession()
    result = await channel_user_service._find_org_member(
        sess, uuid.uuid4(), "dingtalk", []
    )
    assert result is None
    assert sess.last_stmt is None  # short-circuits, no execute


def _make_member(**kwargs):
    defaults = dict(
        id=uuid.uuid4(), external_id=None, unionid=None, open_id=None, user_id=None,
    )
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def test_backfill_dingtalk_fills_external_and_unionid():
    svc = channel_user_service
    member = _make_member()
    svc._backfill_org_member_ids(
        member,
        channel_type="dingtalk",
        external_user_id="staff-carol-777",
        extra_info={"unionid": "UNION-CAROL", "mobile": "13800000001"},
    )
    assert member.external_id == "staff-carol-777"
    assert member.unionid == "UNION-CAROL"


def test_backfill_dingtalk_does_not_overwrite_existing():
    svc = channel_user_service
    member = _make_member(external_id="existing-staff", unionid="existing-union")
    svc._backfill_org_member_ids(
        member,
        channel_type="dingtalk",
        external_user_id="staff-new",
        extra_info={"unionid": "UNION-NEW"},
    )
    assert member.external_id == "existing-staff"
    assert member.unionid == "existing-union"


def test_backfill_feishu_on_prefix_goes_to_unionid():
    svc = channel_user_service
    member = _make_member()
    svc._backfill_org_member_ids(
        member,
        channel_type="feishu",
        external_user_id="on_unionid_xxx",
        extra_info={},
    )
    assert member.unionid == "on_unionid_xxx"


def test_backfill_feishu_ou_prefix_goes_to_openid():
    svc = channel_user_service
    member = _make_member()
    svc._backfill_org_member_ids(
        member,
        channel_type="feishu",
        external_user_id="ou_openid_xxx",
        extra_info={},
    )
    assert member.open_id == "ou_openid_xxx"


def test_backfill_wecom_only_fills_external_id():
    svc = channel_user_service
    member = _make_member()
    svc._backfill_org_member_ids(
        member,
        channel_type="wecom",
        external_user_id="userid-wecom-1",
        extra_info={"unionid": "ignored-for-wecom"},
    )
    assert member.external_id == "userid-wecom-1"
    assert member.unionid is None


async def test_reuse_existing_org_member_triggers_backfill(
    fake_session, agent, patch_provider, monkeypatch
):
    """email 命中 User → 找到 existing_member → 应回填 dingtalk 标识到 existing_member"""
    matched_user = SimpleNamespace(
        id=uuid.uuid4(), identity_id=None,
        display_name="Carol", avatar_url=None,
    )
    existing_member = _make_member(user_id=matched_user.id)

    async def _fake_find_none(self, db, provider_id, channel_type, candidate_ids):
        return None

    async def _fake_match_email(db, email, tenant_id):
        return matched_user

    async def _fake_match_mobile(db, mobile, tenant_id):
        return None

    async def _fake_find_existing(self, db, user_id, provider_id, tenant_id):
        return existing_member

    monkeypatch.setattr(cus_mod.ChannelUserService, "_find_org_member", _fake_find_none)
    monkeypatch.setattr(cus_mod.sso_service, "match_user_by_email", _fake_match_email)
    monkeypatch.setattr(cus_mod.sso_service, "match_user_by_mobile", _fake_match_mobile)
    monkeypatch.setattr(
        cus_mod.ChannelUserService, "_find_existing_org_member_for_user",
        _fake_find_existing,
    )

    async def _get_none(model, key):
        return None
    monkeypatch.setattr(fake_session, "get", _get_none, raising=False)

    await channel_user_service.resolve_channel_user(
        db=fake_session,
        agent=agent,
        channel_type="dingtalk",
        external_user_id="staff-carol-777",
        extra_info={
            "unionid": "UNION-CAROL",
            "mobile": "13800000001",
            "email": "carol@example.com",
            "name": "Carol",
        },
        extra_ids=["UNION-CAROL"],
    )

    assert existing_member.external_id == "staff-carol-777"
    assert existing_member.unionid == "UNION-CAROL"


async def test_enrich_skips_phone_when_other_identity_uses_it(fake_session, monkeypatch):
    """Pre-check: if another Identity already has the phone, skip instead of raising."""
    from app.services.channel_user_service import channel_user_service as svc
    from app.services import channel_user_service as cus_mod

    current_identity = SimpleNamespace(
        id=uuid.uuid4(), phone=None, email=None,
    )
    user = SimpleNamespace(
        id=uuid.uuid4(), identity_id=current_identity.id,
        display_name=None, avatar_url=None,
    )

    async def _fake_get(model, key):
        assert key == current_identity.id
        return current_identity
    monkeypatch.setattr(fake_session, "get", _fake_get, raising=False)

    # Simulate "another identity has this phone": execute returns a truthy row
    other_identity_id = uuid.uuid4()

    async def _fake_execute(stmt):
        sql = str(stmt)

        class _R:
            def scalar_one_or_none(self_inner):
                # Return the other identity's id if the query is looking up
                # identities by phone; else None.
                if "identities.phone" in sql or "phone =" in sql.lower():
                    return other_identity_id
                return None
        return _R()

    monkeypatch.setattr(fake_session, "execute", _fake_execute, raising=False)

    await svc._enrich_user_from_extra_info(
        fake_session, user, {"mobile": "15703300627", "email": None, "name": None}
    )

    # Phone was NOT written, no exception raised
    assert current_identity.phone is None


async def test_enrich_skips_email_when_other_identity_uses_it(fake_session, monkeypatch):
    from app.services.channel_user_service import channel_user_service as svc

    current_identity = SimpleNamespace(
        id=uuid.uuid4(), phone=None, email=None,
    )
    user = SimpleNamespace(
        id=uuid.uuid4(), identity_id=current_identity.id,
        display_name=None, avatar_url=None,
    )

    async def _fake_get(model, key):
        return current_identity
    monkeypatch.setattr(fake_session, "get", _fake_get, raising=False)

    async def _fake_execute(stmt):
        sql = str(stmt)

        class _R:
            def scalar_one_or_none(self_inner):
                if "identities.email" in sql or "email =" in sql.lower():
                    return uuid.uuid4()
                return None
        return _R()
    monkeypatch.setattr(fake_session, "execute", _fake_execute, raising=False)

    await svc._enrich_user_from_extra_info(
        fake_session, user,
        {"mobile": None, "email": "dup@example.com", "name": None},
    )

    assert current_identity.email is None


async def test_enrich_writes_phone_when_no_conflict(fake_session, monkeypatch):
    """Happy path: no other identity uses the phone → write succeeds."""
    from app.services.channel_user_service import channel_user_service as svc

    current_identity = SimpleNamespace(
        id=uuid.uuid4(), phone=None, email=None,
    )
    user = SimpleNamespace(
        id=uuid.uuid4(), identity_id=current_identity.id,
        display_name=None, avatar_url=None,
    )

    async def _fake_get(model, key):
        return current_identity
    monkeypatch.setattr(fake_session, "get", _fake_get, raising=False)

    async def _fake_execute(stmt):
        class _R:
            def scalar_one_or_none(self_inner):
                return None  # no conflict
        return _R()
    monkeypatch.setattr(fake_session, "execute", _fake_execute, raising=False)

    await svc._enrich_user_from_extra_info(
        fake_session, user, {"mobile": "13800000000", "email": None, "name": None}
    )

    assert current_identity.phone == "13800000000"


async def test_resolve_continues_when_enrich_raises(
    fake_session, agent, patch_provider, monkeypatch
):
    """Isolation: even if _enrich raises unexpectedly, resolve still returns the user."""
    from app.services.channel_user_service import channel_user_service as svc
    from app.services import channel_user_service as cus_mod

    matched_user = SimpleNamespace(
        id=uuid.uuid4(), identity_id=uuid.uuid4(),
        display_name=None, avatar_url=None,
    )

    async def _find_linked(self, db, provider_id, channel_type, candidate_ids):
        # Return a member already linked to matched_user → Case 1 branch
        return SimpleNamespace(id=uuid.uuid4(), user_id=matched_user.id)

    monkeypatch.setattr(cus_mod.ChannelUserService, "_find_org_member", _find_linked)

    async def _db_get(model, key):
        if key == matched_user.id:
            return matched_user
        return None
    monkeypatch.setattr(fake_session, "get", _db_get, raising=False)

    async def _boom(self, db, user, extra_info):
        raise RuntimeError("simulated enrichment failure")
    monkeypatch.setattr(
        cus_mod.ChannelUserService, "_enrich_user_from_extra_info", _boom
    )

    # resolve_channel_user should catch the enrichment error and still return the user
    result = await svc.resolve_channel_user(
        db=fake_session, agent=agent, channel_type="dingtalk",
        external_user_id="staff-xyz",
        extra_info={"mobile": "13900000000", "email": "x@y.com"},
    )
    assert result.id == matched_user.id
