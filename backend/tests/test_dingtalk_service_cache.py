import asyncio

from app.services.dingtalk_cache import TTLCache


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
