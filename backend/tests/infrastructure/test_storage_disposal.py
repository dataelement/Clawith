import asyncio
import threading

import pytest

from app.infrastructure.object_storage.base import StorageError
from app.infrastructure.object_storage.local import LocalStorageBackend
from app.infrastructure.object_storage.s3 import S3StorageBackend


def backend_with_native_client():
    storage = S3StorageBackend(bucket="test", endpoint_url="http://127.0.0.1:1",
        access_key_id="test", secret_access_key="test")
    client = storage._client_or_raise()
    # A real SDK pool entry, without contacting any external service or using ambient credentials.
    manager = client._endpoint.http_session._manager
    manager.connection_from_url("http://127.0.0.1:1")
    assert len(manager.pools) == 1
    return storage, client, manager


async def test_s3_close_releases_native_pool_and_is_idempotent():
    storage, _, manager = backend_with_native_client()
    await asyncio.gather(storage.aclose(), storage.aclose())
    assert len(manager.pools) == 0
    assert storage._client is None
    assert storage._aioboto3_session is None
    await storage.aclose()
    with pytest.raises(StorageError, match="closed"):
        storage._client_or_raise()
    with pytest.raises(StorageError, match="closed"):
        async with storage._async_client():
            pytest.fail("closed storage opened a new S3 client")


async def test_s3_cancelled_close_waits_until_native_pool_is_released(monkeypatch):
    storage, client, manager = backend_with_native_client()
    started, release = threading.Event(), threading.Event()
    original = client.close
    def delayed_close():
        started.set()
        if not release.wait(3):
            raise TimeoutError("test release missing")
        original()
    monkeypatch.setattr(client, "close", delayed_close)
    task = asyncio.create_task(storage.aclose())
    try:
        assert await asyncio.to_thread(started.wait, 1)
        task.cancel()
        await asyncio.sleep(.02)
        assert not task.done()
        assert len(manager.pools) == 1
    finally:
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert len(manager.pools) == 0
    await storage.aclose()


async def test_s3_close_failure_is_stable_and_never_recreates_a_client(monkeypatch):
    storage, client, manager = backend_with_native_client()
    original = client.close
    attempts = 0
    def failing_close():
        nonlocal attempts
        attempts += 1
        original()
        raise OSError("close failure")
    monkeypatch.setattr(client, "close", failing_close)
    for _ in range(2):
        with pytest.raises(OSError, match="close failure"):
            await storage.aclose()
    assert attempts == 1
    assert len(manager.pools) == 0
    assert storage._client is None
    with pytest.raises(StorageError, match="closed"):
        storage._client_or_raise()


async def test_local_close_has_no_persistent_handle_or_client(tmp_path):
    storage = LocalStorageBackend(str(tmp_path))
    await storage.write_bytes("file", b"retained")
    await storage.aclose()
    await storage.aclose()
    assert (tmp_path / "file").read_bytes() == b"retained"
