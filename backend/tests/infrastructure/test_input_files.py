import asyncio
import hashlib
from uuid import uuid4

import pytest
from aiohttp import web
from sqlalchemy.ext.asyncio import create_async_engine

from app.infrastructure.object_storage.base import StorageError
from app.infrastructure.object_storage.input_files import MAX_INPUT_FILE_BYTES, InputFileStorage
from app.infrastructure.object_storage.local import LocalStorageBackend
from app.infrastructure.object_storage.s3 import S3StorageBackend
from app.infrastructure.resource_locks import PostgresResourceLocks


@pytest.fixture(params=["local", "s3"])
async def storage(request, tmp_path, postgres_url):
    if request.param == "local":
        backend = LocalStorageBackend(str(tmp_path))
        try:
            yield InputFileStorage(backend), backend
        finally:
            await backend.aclose()
        return
    objects = {}
    serial = 0
    async def s3_peer(request):
        nonlocal serial
        key = request.path
        current = objects.get(key)
        def error(code, status):
            return web.Response(status=status, text=f"<Error><Code>{code}</Code></Error>", content_type="application/xml")
        if request.method == "PUT":
            if request.headers.get("If-None-Match") == "*" and current is not None:
                return error("PreconditionFailed", 412)
            data = await request.read()
            serial += 1
            etag = '"' + hashlib.sha256(data).hexdigest() + '"'
            objects[key] = (data, etag, str(serial))
            return web.Response(headers={"ETag":etag,"x-amz-version-id":str(serial)})
        if current is None:
            return error("NoSuchKey", 404)
        data, etag, revision = current
        if request.method == "DELETE":
            if request.headers.get("If-Match") != etag:
                return error("PreconditionFailed", 412)
            objects.pop(key)
            return web.Response(status=204)
        assert request.method in ("GET", "HEAD")
        return web.Response(body=data, headers={"ETag":etag,"x-amz-version-id":revision})
    app = web.Application(client_max_size=MAX_INPUT_FILE_BYTES + 1024)
    app.router.add_route("*", "/{key:.*}", s3_peer)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    engine = create_async_engine(postgres_url, pool_size=3, max_overflow=0)
    backend = S3StorageBackend(bucket="input-files", prefix=str(uuid4()), region="us-east-1",
        endpoint_url=f"http://127.0.0.1:{runner.addresses[0][1]}", access_key_id="test-key", secret_access_key="test-secret",
        lock_provider=PostgresResourceLocks(engine, timeout_seconds=2))
    try:
        yield InputFileStorage(backend), backend
    finally:
        await backend.aclose()
        await engine.dispose()
        await runner.cleanup()


async def test_guarded_put_retry_read_and_conditional_delete(storage):
    files, _ = storage
    async with asyncio.timeout(4), files.guard("attachments/file"):
        first = await files.put_if_absent("attachments/file", b"original")
        repeated = await files.put_if_absent("attachments/file", b"original")
        assert first == repeated
        assert first.sha256 == hashlib.sha256(b"original").hexdigest() and first.byte_size == 8
        assert await files.inspect("attachments/file") == first
        assert await files.read_range("attachments/file", revision=first.revision, offset=2, limit=3) == b"igi"
        with pytest.raises(StorageError):
            await files.put_if_absent("attachments/file", b"different")
        with pytest.raises(StorageError):
            await files.read_range("attachments/file", revision="stale", offset=0, limit=4)
        assert not await files.delete_if_revision("attachments/file", revision="stale")
        assert await files.delete_if_revision("attachments/file", revision=first.revision)
        assert await files.inspect("attachments/file") is None
        with pytest.raises(FileNotFoundError):
            await files.read_range("attachments/file", revision=first.revision, offset=0, limit=4)
        empty = await files.put_if_absent("attachments/file", b"")
        assert empty.byte_size == 0
        assert await files.read_range("attachments/file", revision=empty.revision, offset=0, limit=0) == b""
        for offset, limit in ((-1,1),(True,1),(0,-1),(0,MAX_INPUT_FILE_BYTES+1)):
            with pytest.raises(StorageError):
                await files.read_range("attachments/file", revision=empty.revision, offset=offset, limit=limit)


async def test_whole_object_bound_is_enforced_even_for_tiny_range(storage):
    files, backend = storage
    value = await files.put_if_absent("limit", b"x" * MAX_INPUT_FILE_BYTES)
    assert await files.read_range("limit", revision=value.revision, offset=MAX_INPUT_FILE_BYTES, limit=1) == b""
    with pytest.raises(StorageError):
        await files.put_if_absent("large", b"x" * (MAX_INPUT_FILE_BYTES + 1))
    await backend.write_bytes("oversized", b"x" * (MAX_INPUT_FILE_BYTES + 1))
    with pytest.raises(StorageError):
        await files.inspect("oversized")
    with pytest.raises(StorageError):
        await files.read_range("oversized", revision="any", offset=0, limit=1)


async def test_publication_guard_serializes_same_key_without_blocking_other_key(storage):
    files, _ = storage
    held, release, other = asyncio.Event(), asyncio.Event(), asyncio.Event()
    entered = []
    async def first():
        async with files.guard("same"):
            held.set()
            await release.wait()
            await files.put_if_absent("same", b"data")
    async def second():
        await held.wait()
        async with files.guard("same"):
            entered.append(True)
            await files.put_if_absent("same", b"data")
    async def unrelated():
        await held.wait()
        async with files.guard("other"):
            await files.put_if_absent("other", b"other")
            other.set()
    tasks = [asyncio.create_task(fn()) for fn in (first, second, unrelated)]
    try:
        await asyncio.wait_for(other.wait(), 3)
        assert not entered
    finally:
        release.set()
        await asyncio.wait_for(asyncio.gather(*tasks), 4)
    assert entered == [True]


@pytest.mark.parametrize("key", ["", "../escape", "/alias", "a//b", "a/./b"])
async def test_noncanonical_keys_are_rejected_before_storage(storage, key):
    files, _ = storage
    with pytest.raises(StorageError):
        files.guard(key)
    with pytest.raises(StorageError):
        await files.put_if_absent(key, b"data")
