import asyncio

import pytest

from app.services.dingtalk_cache import TTLCache
from app.services import dingtalk_service


class _FakeResp:
    def __init__(self, payload):
        self._payload = payload
        self.status_code = 200

    def json(self):
        return self._payload


class _FakeClient:
    instances: list["_FakeClient"] = []

    def __init__(self, *args, **kwargs):
        self.get_calls: list[tuple[str, dict]] = []
        self.post_calls: list[tuple[str, dict, dict]] = []
        _FakeClient.instances.append(self)

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def get(self, url, params=None):
        self.get_calls.append((url, params or {}))
        return _FakeResp({"errcode": 0, "access_token": "tok-A", "expires_in": 7200})

    async def post(self, url, params=None, json=None):
        self.post_calls.append((url, params or {}, json or {}))
        return _FakeResp({
            "errcode": 0,
            "result": {
                "userid": json["userid"],
                "name": "Alice",
                "mobile": "13800000000",
                "email": "alice@example.com",
                "unionid": f"UNION-{json['userid']}",
            },
        })


@pytest.fixture(autouse=True)
def _reset_caches(monkeypatch):
    dingtalk_service._token_cache._store.clear()
    dingtalk_service._user_detail_cache._store.clear()
    _FakeClient.instances.clear()
    monkeypatch.setattr(dingtalk_service.httpx, "AsyncClient", _FakeClient)


async def test_ttl_cache_hit_miss():
    cache = TTLCache(default_ttl=60)
    calls = {"n": 0}

    async def factory():
        calls["n"] += 1
        return {"v": calls["n"]}

    v1 = await cache.get_or_set("k", factory)
    v2 = await cache.get_or_set("k", factory)
    assert v1 == v2 == {"v": 1}
    assert calls["n"] == 1


async def test_ttl_cache_expires():
    cache = TTLCache(default_ttl=0.05)
    calls = {"n": 0}

    async def factory():
        calls["n"] += 1
        return calls["n"]

    assert await cache.get_or_set("k", factory) == 1
    await asyncio.sleep(0.08)
    assert await cache.get_or_set("k", factory) == 2


async def test_ttl_cache_single_flight():
    cache = TTLCache(default_ttl=60)
    calls = {"n": 0}
    start = asyncio.Event()

    async def factory():
        await start.wait()
        calls["n"] += 1
        return calls["n"]

    t1 = asyncio.create_task(cache.get_or_set("k", factory))
    t2 = asyncio.create_task(cache.get_or_set("k", factory))
    await asyncio.sleep(0)
    start.set()
    r1, r2 = await asyncio.gather(t1, t2)
    assert r1 == r2 == 1
    assert calls["n"] == 1


async def test_access_token_cached_across_calls():
    t1 = await dingtalk_service.get_dingtalk_access_token("APP", "SEC")
    t2 = await dingtalk_service.get_dingtalk_access_token("APP", "SEC")
    assert t1["access_token"] == t2["access_token"] == "tok-A"
    gets = [c for inst in _FakeClient.instances for c in inst.get_calls]
    assert len(gets) == 1


async def test_user_detail_cached_per_userid():
    d1 = await dingtalk_service.get_dingtalk_user_detail("APP", "SEC", "user-1")
    d2 = await dingtalk_service.get_dingtalk_user_detail("APP", "SEC", "user-1")
    d3 = await dingtalk_service.get_dingtalk_user_detail("APP", "SEC", "user-2")
    assert d1 == d2
    assert d1["unionid"] == "UNION-user-1"
    assert d3["unionid"] == "UNION-user-2"
    user_posts = [
        c for inst in _FakeClient.instances
        for c in inst.post_calls if "user/get" in c[0]
    ]
    assert len(user_posts) == 2  # 2 distinct userids


class _EmptyResultClient(_FakeClient):
    async def post(self, url, params=None, json=None):
        self.post_calls.append((url, params or {}, json or {}))
        # DingTalk returns success but result field missing → _fetch coerces to {}
        return _FakeResp({"errcode": 0})


async def test_user_detail_empty_result_is_not_cached(monkeypatch):
    monkeypatch.setattr(dingtalk_service.httpx, "AsyncClient", _EmptyResultClient)
    r1 = await dingtalk_service.get_dingtalk_user_detail("APP", "SEC", "user-x")
    r2 = await dingtalk_service.get_dingtalk_user_detail("APP", "SEC", "user-x")
    assert r1 == {}
    assert r2 == {}
    user_posts = [
        c for inst in _FakeClient.instances
        for c in inst.post_calls if "user/get" in c[0]
    ]
    # Without the fix: only 1 POST (empty dict cached). With fix: 2 POSTs.
    assert len(user_posts) == 2
