import asyncio
import io
import threading

import pytest
from botocore.exceptions import BotoCoreError
from botocore.stub import Stubber

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


async def test_concurrent_first_reads_create_one_cached_native_client_and_close_every_pool(monkeypatch):
    import boto3

    storage = S3StorageBackend(bucket="test", endpoint_url="http://127.0.0.1:1",
        access_key_id="test", secret_access_key="test")
    original_factory = boto3.client
    started, release = threading.Event(), threading.Event()
    sdk_creation = threading.Lock()
    clients, managers, attempts = [], [], []
    def delayed_factory(*args, **kwargs):
        attempts.append(1)
        started.set()
        if not release.wait(3):
            raise TimeoutError("test release missing")
        with sdk_creation:
            client = original_factory(*args, **kwargs)
        manager = client._endpoint.http_session._manager
        manager.connection_from_url("http://127.0.0.1:1")
        stubber = Stubber(client)
        for _ in range(2):
            stubber.add_response("get_object", {"Body": io.BytesIO(b"data"), "ContentLength": 4,
                "ETag": '"tag"'}, {"Bucket": "test", "Key": "file"})
        stubber.activate()
        clients.append(client)
        managers.append(manager)
        return client
    monkeypatch.setattr(boto3, "client", delayed_factory)
    tasks = [asyncio.create_task(storage.read_versioned("file", max_bytes=4)) for _ in range(2)]
    try:
        assert await asyncio.to_thread(started.wait, 1)
        await asyncio.sleep(.05)
        release.set()
        assert [value[0] for value in await asyncio.gather(*tasks)] == [b"data", b"data"]
        await storage.aclose()
        assert len(attempts) == 1
        assert all(len(manager.pools) == 0 for manager in managers)
    finally:
        release.set()
        await asyncio.gather(*tasks, return_exceptions=True)
        for client in clients:
            client.close()


async def test_cancelled_first_read_drains_initialization_before_disposal(monkeypatch):
    import boto3

    storage = S3StorageBackend(bucket="test", endpoint_url="http://127.0.0.1:1",
        access_key_id="test", secret_access_key="test")
    original_factory = boto3.client
    started, release = threading.Event(), threading.Event()
    managers = []
    def delayed_factory(*args, **kwargs):
        started.set()
        if not release.wait(3):
            raise TimeoutError("test release missing")
        client = original_factory(*args, **kwargs)
        manager = client._endpoint.http_session._manager
        manager.connection_from_url("http://127.0.0.1:1")
        managers.append(manager)
        stubber = Stubber(client)
        stubber.add_response("get_object", {"Body": io.BytesIO(b"data"), "ContentLength": 4,
            "ETag": '"tag"'}, {"Bucket": "test", "Key": "file"})
        stubber.activate()
        return client
    monkeypatch.setattr(boto3, "client", delayed_factory)
    read = asyncio.create_task(storage.read_versioned("file", max_bytes=4))
    try:
        assert await asyncio.to_thread(started.wait, 1)
        read.cancel()
        await asyncio.sleep(.03)
        assert not read.done()
    finally:
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await read
        await storage.aclose()
    assert len(managers) == 1
    assert len(managers[0].pools) == 0
    assert storage._client is None


async def test_initialization_failure_releases_lock_and_does_not_publish_failed_client(monkeypatch):
    import boto3

    storage = S3StorageBackend(bucket="test", endpoint_url="http://127.0.0.1:1",
        access_key_id="test", secret_access_key="test")
    original_factory = boto3.client
    attempts, managers = [], []
    def factory(*args, **kwargs):
        attempts.append(1)
        if len(attempts) == 1:
            raise BotoCoreError()
        client = original_factory(*args, **kwargs)
        manager = client._endpoint.http_session._manager
        manager.connection_from_url("http://127.0.0.1:1")
        managers.append(manager)
        stubber = Stubber(client)
        stubber.add_response("get_object", {"Body": io.BytesIO(b"data"), "ContentLength": 4,
            "ETag": '"tag"'}, {"Bucket": "test", "Key": "file"})
        stubber.activate()
        return client
    monkeypatch.setattr(boto3, "client", factory)
    try:
        with pytest.raises(StorageError, match="Object storage request failed"):
            await storage.read_versioned("file", max_bytes=4)
        assert storage._client is None
        assert (await storage.read_versioned("file", max_bytes=4))[0] == b"data"
    finally:
        await storage.aclose()
    assert len(attempts) == 2
    assert len(managers) == 1
    assert len(managers[0].pools) == 0


async def test_first_listing_initialization_does_not_block_event_loop(monkeypatch):
    import boto3

    storage = S3StorageBackend(bucket="test", endpoint_url="http://127.0.0.1:1",
        access_key_id="test", secret_access_key="test")
    original_factory = boto3.client
    started, release = threading.Event(), threading.Event()
    def delayed_factory(*args, **kwargs):
        started.set()
        if not release.wait(3):
            raise TimeoutError("test release missing")
        client = original_factory(*args, **kwargs)
        stubber = Stubber(client)
        stubber.add_response("list_objects_v2", {"Contents": [], "IsTruncated": False},
            {"Bucket": "test", "Prefix": "dir/", "Delimiter": "/", "MaxKeys": 1})
        stubber.activate()
        return client
    monkeypatch.setattr(boto3, "client", delayed_factory)
    listing = asyncio.create_task(storage.list_dir_page("dir", limit=1))
    try:
        assert await asyncio.to_thread(started.wait, 1)
        await asyncio.wait_for(asyncio.sleep(.02), .2)
        assert not listing.done()
    finally:
        release.set()
        await listing
        await storage.aclose()


@pytest.mark.parametrize("operation", ["head", "list"])
async def test_cancelled_sdk_operation_drains_before_disposing_shared_client(monkeypatch, operation):
    storage, client, _ = backend_with_native_client()
    started, release, finished = threading.Event(), threading.Event(), threading.Event()
    closed_while_active = []
    method = "head_object" if operation == "head" else "list_objects_v2"
    stubber = Stubber(client)
    if operation == "head":
        stubber.add_response(method, {"ContentLength": 4, "ETag": '"tag"'}, {"Bucket": "test", "Key": "file"})
    else:
        stubber.add_response(method, {"Contents": [], "IsTruncated": False},
            {"Bucket": "test", "Prefix": "dir/", "Delimiter": "/", "MaxKeys": 1})
    stubber.activate()
    original, close = getattr(client, method), client.close
    def blocked(**kwargs):
        started.set()
        try:
            if not release.wait(3):
                raise TimeoutError("test release missing")
            return original(**kwargs)
        finally:
            finished.set()
    def observe_close():
        closed_while_active.append(not finished.is_set())
        close()
    monkeypatch.setattr(client, method, blocked)
    monkeypatch.setattr(client, "close", observe_close)
    task = asyncio.create_task(storage.get_version("file") if operation == "head" else storage.list_dir_page("dir", limit=1))
    try:
        assert await asyncio.to_thread(started.wait, 1)
        task.cancel()
        await asyncio.sleep(.03)
        assert not task.done()
    finally:
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        await storage.aclose()
        assert await asyncio.to_thread(finished.wait, 1)
    assert closed_while_active == [False]


async def test_cancelled_read_bytes_cannot_lose_body_returned_by_get(monkeypatch):
    storage, client, _ = backend_with_native_client()
    body = io.BytesIO(b"data")
    stubber = Stubber(client)
    stubber.add_response("get_object", {"Body": body, "ContentLength": 4}, {"Bucket": "test", "Key": "file"})
    stubber.activate()
    received, release = threading.Event(), threading.Event()
    original = client.get_object
    def delayed_get(**kwargs):
        response = original(**kwargs)
        received.set()
        if not release.wait(3):
            raise TimeoutError("test release missing")
        return response
    monkeypatch.setattr(client, "get_object", delayed_get)
    task = asyncio.create_task(storage.read_bytes("file"))
    try:
        assert await asyncio.to_thread(received.wait, 1)
        task.cancel()
        await asyncio.sleep(.03)
        assert not task.done()
        assert not body.closed
    finally:
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        await storage.aclose()
    assert body.closed


@pytest.mark.parametrize("failed", [False, True])
async def test_read_bytes_closes_body_on_success_and_read_failure(failed):
    storage, client, _ = backend_with_native_client()
    class Body(io.BytesIO):
        def read(self, *args):
            if failed:
                raise OSError("body read failed")
            return super().read(*args)
    body = Body(b"data")
    stubber = Stubber(client)
    stubber.add_response("get_object", {"Body": body, "ContentLength": 4}, {"Bucket": "test", "Key": "file"})
    stubber.activate()
    try:
        if failed:
            with pytest.raises(OSError, match="body read failed"):
                await storage.read_bytes("file")
        else:
            assert await storage.read_bytes("file") == b"data"
    finally:
        await storage.aclose()
    assert body.closed
