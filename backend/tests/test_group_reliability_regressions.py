"""Boundary regressions for group uploads and realtime subscriber recovery."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

from fastapi import HTTPException
import pytest

from app.api import groups as groups_api
from app.services.realtime_runtime.router import RealtimeRouter


class _BoundedUpload:
    def __init__(self, payload: bytes) -> None:
        self.payload = payload
        self.offset = 0
        self.read_sizes: list[int] = []

    async def read(self, size: int = -1) -> bytes:
        self.read_sizes.append(size)
        if size < 0:
            raise AssertionError("group uploads must never use an unbounded read")
        start = self.offset
        self.offset = min(len(self.payload), start + size)
        return self.payload[start : self.offset]


class _ScriptedPubSub:
    def __init__(self, *, fail_first_read: bool = False, block_reads: bool = False) -> None:
        self.fail_first_read = fail_first_read
        self.block_reads = block_reads
        self.channels: list[str] = []
        self.read_count = 0
        self.read_started = asyncio.Event()
        self.closed = False

    async def subscribe(self, channel: str) -> None:
        self.channels.append(channel)

    async def get_message(self, **_kwargs):
        self.read_count += 1
        self.read_started.set()
        if self.fail_first_read:
            self.fail_first_read = False
            raise ConnectionError("subscriber read failed")
        if self.block_reads:
            await asyncio.Future()
        return None

    async def aclose(self) -> None:
        self.closed = True


class _ScriptedRedis:
    def __init__(self, pubsub: _ScriptedPubSub) -> None:
        self._pubsub = pubsub

    def pubsub(self) -> _ScriptedPubSub:
        return self._pubsub


@pytest.mark.asyncio
async def test_group_upload_exact_limit_is_allowed_and_read_in_bounded_chunks(
    monkeypatch,
) -> None:
    upload = _BoundedUpload(b"12345678")
    monkeypatch.setattr(groups_api, "GROUP_WORKSPACE_UPLOAD_MAX_BYTES", 8)
    monkeypatch.setattr(groups_api, "GROUP_WORKSPACE_UPLOAD_CHUNK_BYTES", 3)

    content = await groups_api._read_group_workspace_upload(upload)  # type: ignore[arg-type]

    assert content == b"12345678"
    assert upload.read_sizes == [3, 3, 3, 1]
    assert all(0 < size <= 3 for size in upload.read_sizes)


@pytest.mark.asyncio
async def test_group_upload_one_byte_over_limit_is_rejected(monkeypatch) -> None:
    upload = _BoundedUpload(b"123456789")
    monkeypatch.setattr(groups_api, "GROUP_WORKSPACE_UPLOAD_MAX_BYTES", 8)
    monkeypatch.setattr(groups_api, "GROUP_WORKSPACE_UPLOAD_CHUNK_BYTES", 3)

    with pytest.raises(HTTPException) as exc_info:
        await groups_api._read_group_workspace_upload(upload)  # type: ignore[arg-type]

    assert exc_info.value.status_code == 413
    assert exc_info.value.detail == {
        "code": "group_workspace_upload_too_large",
        "message": "Group workspace uploads must be 8 bytes or smaller",
    }
    assert upload.read_sizes == [3, 3, 3]


@pytest.mark.asyncio
async def test_subscriber_reconnects_after_first_read_failure_and_stop_cancels(
    monkeypatch,
) -> None:
    failing = _ScriptedPubSub(fail_first_read=True)
    replacement = _ScriptedPubSub(block_reads=True)
    redis_attempts = iter((_ScriptedRedis(failing), _ScriptedRedis(replacement)))
    get_redis_calls = 0

    async def fake_get_redis():
        nonlocal get_redis_calls
        get_redis_calls += 1
        try:
            return next(redis_attempts)
        except StopIteration as exc:
            raise AssertionError("subscriber reconnected more than once") from exc

    monkeypatch.setattr(
        "app.services.realtime_runtime.router.get_redis",
        fake_get_redis,
    )
    router = RealtimeRouter(instance_id="api-reliability")
    router._subscriber_retry_initial_seconds = 0.001
    router._subscriber_retry_max_seconds = 0.001

    await router.start(AsyncMock(), supports_scopes=True)
    await asyncio.wait_for(replacement.read_started.wait(), timeout=1)
    await asyncio.wait_for(router.stop(), timeout=0.1)

    assert get_redis_calls == 2
    assert failing.read_count == 1
    assert replacement.read_count == 1
    assert failing.channels == ["realtime:ws:instance:api-reliability"]
    assert replacement.channels == failing.channels
    assert failing.closed is True
    assert replacement.closed is True
    assert router._subscriber_task is None
    assert router._started is False
