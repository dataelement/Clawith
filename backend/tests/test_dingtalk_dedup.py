import asyncio

import pytest

from app.api import dingtalk as dingtalk_api


@pytest.fixture(autouse=True)
def _reset_state(monkeypatch):
    dingtalk_api._processed_messages.clear()
    dingtalk_api._dedup_check_counter = 0
    # 禁用 Redis: 强制走内存 fallback
    monkeypatch.setattr(dingtalk_api, "_get_redis_client", None)


async def test_acquire_first_returns_accepted():
    accepted, state = await dingtalk_api.acquire_dedup_lock("m-1")
    assert accepted is True
    assert state == "new"


async def test_acquire_second_while_processing_returns_duplicate():
    await dingtalk_api.acquire_dedup_lock("m-2")
    accepted, state = await dingtalk_api.acquire_dedup_lock("m-2")
    assert accepted is False
    assert state == "processing"


async def test_acquire_after_done_returns_duplicate():
    await dingtalk_api.acquire_dedup_lock("m-3")
    await dingtalk_api.mark_dedup_done("m-3")
    accepted, state = await dingtalk_api.acquire_dedup_lock("m-3")
    assert accepted is False
    assert state == "done"


async def test_release_allows_retry():
    await dingtalk_api.acquire_dedup_lock("m-4")
    await dingtalk_api.release_dedup_lock("m-4")
    accepted, state = await dingtalk_api.acquire_dedup_lock("m-4")
    assert accepted is True
    assert state == "new"


async def test_release_after_done_is_noop():
    await dingtalk_api.acquire_dedup_lock("m-4b")
    await dingtalk_api.mark_dedup_done("m-4b")
    await dingtalk_api.release_dedup_lock("m-4b")  # 不应把 done 删掉
    accepted, state = await dingtalk_api.acquire_dedup_lock("m-4b")
    assert accepted is False
    assert state == "done"


async def test_empty_message_id_always_accepts():
    accepted, state = await dingtalk_api.acquire_dedup_lock("")
    assert accepted is True
    assert state == "new"


async def test_processing_ttl_expires(monkeypatch):
    monkeypatch.setattr(dingtalk_api, "PROCESSING_TTL", 0.05, raising=False)
    await dingtalk_api.acquire_dedup_lock("m-5")
    await asyncio.sleep(0.08)
    accepted, state = await dingtalk_api.acquire_dedup_lock("m-5")
    assert accepted is True
    assert state == "new"
