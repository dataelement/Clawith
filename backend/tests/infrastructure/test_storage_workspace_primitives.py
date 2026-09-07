import asyncio
import io
import sys
import threading
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, Mock

import pytest
from botocore.exceptions import ClientError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.infrastructure.object_storage import local as local_runtime
from app.infrastructure.object_storage.base import StorageError, WriteCondition
from app.infrastructure.object_storage.local import LocalStorageBackend
from app.infrastructure.object_storage.s3 import S3StorageBackend
from app.infrastructure.resource_locks import PostgresResourceLocks


@pytest.mark.asyncio
@pytest.mark.parametrize("bound", [3, 4, 5])
async def test_local_read_bounded_and_version_is_cas_compatible(tmp_path, bound):
    storage = LocalStorageBackend(str(tmp_path))
    await storage.write_bytes("file", b"abcd")
    if bound < 4:
        with pytest.raises(ValueError, match="max_bytes"):
            await storage.read_versioned("file", max_bytes=bound)
        return
    data, version = await storage.read_versioned("file", max_bytes=bound)
    assert data == b"abcd"
    assert (await storage.write_bytes_if_match("file", b"next", condition=WriteCondition(version_token=version.token))).ok


@pytest.mark.asyncio
async def test_local_resource_locks_serialize_independent_instances_and_cancel(tmp_path):
    first = LocalStorageBackend(str(tmp_path / "data"))
    second = LocalStorageBackend(str(tmp_path / "data"))
    entered = asyncio.Event()

    async def enter():
        async with second.resource_lock("skill"):
            entered.set()

    async with first.resource_lock("skill"):
        waiting = asyncio.create_task(enter())
        await asyncio.sleep(0.03)
        assert not entered.is_set()
        async with second.resource_lock("different"):
            pass
        waiting.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiting
    await asyncio.wait_for(enter(), 1)
    assert entered.is_set()


@pytest.mark.asyncio
async def test_local_page_cursor_and_scan_budget(tmp_path):
    storage = LocalStorageBackend(str(tmp_path))
    for name in ("a", "b", "c"):
        (tmp_path / name).touch()
    first, cursor = await storage.list_dir_page("", limit=2)
    assert [entry.name for entry in first] == ["a", "b"]
    second, end = await storage.list_dir_page("", limit=2, cursor=cursor)
    assert [entry.name for entry in second] == ["c"]
    assert end is None
    (tmp_path / "d").touch()
    with pytest.raises(ValueError, match="changed"):
        await storage.list_dir_page("", limit=2, cursor=cursor)
    storage.MAX_DIRECTORY_SCAN = 3
    with pytest.raises(ValueError, match="scan budget"):
        await storage.list_dir_page("", limit=2)


@pytest.mark.asyncio
async def test_local_metadata_stat_does_not_read_payload(tmp_path, monkeypatch):
    storage = LocalStorageBackend(str(tmp_path))
    await storage.write_bytes("file", b"abc")
    monkeypatch.setattr(storage, "read_bytes", Mock(side_effect=AssertionError("unbounded read")))
    version = await storage.get_version("file")
    assert (await storage.stat("file")).version_id == version.token
    assert (await storage.write_bytes_if_match("file", b"new", condition=WriteCondition(version_token=version.token))).ok


@pytest.mark.asyncio
async def test_local_resource_lock_is_cross_process(tmp_path):
    storage = LocalStorageBackend(str(tmp_path / "data"))
    script = (
        "import asyncio, sys\n"
        "from app.infrastructure.object_storage.local import LocalStorageBackend\n"
        "async def main():\n"
        " async with LocalStorageBackend(sys.argv[1]).resource_lock('skill'):\n"
        "  print('locked', flush=True)\n"
        "  await asyncio.to_thread(sys.stdin.readline)\n"
        "asyncio.run(main())\n"
    )
    process = await asyncio.create_subprocess_exec(sys.executable, "-c", script, str(storage.root), stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE)
    assert process.stdout is not None and process.stdin is not None
    entered = asyncio.Event()

    async def enter():
        async with storage.resource_lock("skill"):
            entered.set()

    task = None
    try:
        assert await asyncio.wait_for(process.stdout.readline(), 2) == b"locked\n"
        task = asyncio.create_task(enter())
        await asyncio.sleep(0.04)
        assert not entered.is_set()
        process.stdin.write(b"\n")
        await process.stdin.drain()
        assert await asyncio.wait_for(process.wait(), 2) == 0
        await asyncio.wait_for(task, 2)
        assert entered.is_set()
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()
        if task is not None:
            await asyncio.wait_for(task, 2)


@pytest.mark.asyncio
async def test_cancelled_preparation_cleans_temp_without_blocking_other_writes(tmp_path, monkeypatch):
    storage = LocalStorageBackend(str(tmp_path))
    entered = threading.Event()
    release = threading.Event()
    original = local_runtime._prepare_write

    def prepare(path, data):
        original(path, data)
        if data == b"slow":
            entered.set()
            assert release.wait(2)

    monkeypatch.setattr(local_runtime, "_prepare_write", prepare)
    task = asyncio.create_task(storage.write_bytes("file", b"slow"))
    try:
        assert await asyncio.to_thread(entered.wait, 2)
        task.cancel()
        await asyncio.sleep(0.01)
        assert not task.done()
        await asyncio.wait_for(storage.write_bytes("file", b"fast"), 1)
    finally:
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert await storage.read_bytes("file") == b"fast"
    assert not list(tmp_path.glob(".clawith-storage-tmp-*"))


@pytest.mark.asyncio
@pytest.mark.parametrize("bound", [3, 4, 5])
async def test_s3_bounded_read_closes_body_and_uses_get_version(bound):
    storage = S3StorageBackend(bucket="bucket")
    body = io.BytesIO(b"abcd")
    storage._client = Mock()
    storage._client.get_object.return_value = {"ContentLength": 4, "ETag": '"etag"', "VersionId": "v1", "Body": body}
    if bound < 4:
        with pytest.raises(ValueError, match="max_bytes"):
            await storage.read_versioned("file", max_bytes=bound)
    else:
        data, version = await storage.read_versioned("file", max_bytes=bound)
        assert data == b"abcd"
        assert version.token == "v1"
    assert body.closed
    storage._client.head_object.assert_not_called()


@pytest.mark.asyncio
async def test_s3_page_is_one_bounded_query():
    storage = S3StorageBackend(bucket="bucket", prefix="root")
    storage._client = Mock()
    storage._client.list_objects_v2.return_value = {"Contents": [{"Key": "root/dir/file", "Size": 2}], "IsTruncated": True, "NextContinuationToken": "next"}
    entries, cursor = await storage.list_dir_page("dir", limit=1, cursor="previous")
    assert [entry.name for entry in entries] == ["file"]
    assert cursor == "next"
    storage._client.list_objects_v2.assert_called_once_with(Bucket="bucket", Prefix="root/dir/", Delimiter="/", MaxKeys=1, ContinuationToken="previous")


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["read", "list", "delete_tree"])
async def test_s3_provider_errors_are_normalized_without_leaking_details(operation):
    storage = S3StorageBackend(bucket="bucket")
    storage._client = Mock()
    error = ClientError({"Error": {"Code": "AccessDenied", "Message": "private provider details"}}, "GetObject")
    storage._client.get_object.side_effect = error
    storage._client.list_objects_v2.side_effect = error
    with pytest.raises(StorageError, match="^Object storage request failed$"):
        if operation == "read":
            await storage.read_versioned("file", max_bytes=10)
        elif operation == "list":
            await storage.list_dir_page("dir", limit=10)
        else:
            await storage.delete_tree("dir")


@pytest.mark.asyncio
async def test_s3_bounded_read_missing_is_not_provider_failure():
    storage = S3StorageBackend(bucket="bucket")
    storage._client = Mock()
    storage._client.get_object.side_effect = ClientError({"Error": {"Code": "NoSuchKey"}}, "GetObject")
    with pytest.raises(FileNotFoundError):
        await storage.read_versioned("file", max_bytes=10)


@pytest.mark.asyncio
@pytest.mark.parametrize("partial_failure", [False, True])
async def test_s3_tree_cleanup_pages_and_partial_failures(monkeypatch, partial_failure):
    storage = S3StorageBackend(bucket="bucket")
    storage._client = Mock()
    storage._client.list_objects_v2.side_effect = [
        {"Contents": [{"Key": "package/a"}], "IsTruncated": True, "NextContinuationToken": "next"},
        {"Contents": [{"Key": "package/b"}], "IsTruncated": False},
    ]
    writer = Mock()
    writer.delete_objects = AsyncMock(return_value={"Errors": [{"Code": "AccessDenied"}]} if partial_failure else {})

    @asynccontextmanager
    async def client():
        yield writer

    monkeypatch.setattr(storage, "_async_client", client)
    if partial_failure:
        with pytest.raises(StorageError, match="incomplete"):
            await storage.delete_tree("package")
        assert writer.delete_objects.await_count == 1
    else:
        await storage.delete_tree("package")
        assert writer.delete_objects.await_count == 2
        assert storage._client.list_objects_v2.call_args.kwargs["ContinuationToken"] == "next"
        assert all(call.kwargs["MaxKeys"] == 1000 for call in storage._client.list_objects_v2.call_args_list)


def test_s3_resource_lock_requires_explicit_provider():
    with pytest.raises(RuntimeError, match="cross-process"):
        S3StorageBackend(bucket="bucket").resource_lock("skill")


@pytest.mark.asyncio
async def test_s3_lock_scope_includes_storage_namespace():
    keys = []

    @asynccontextmanager
    async def provider(key):
        keys.append(key)
        yield

    for bucket in ("a", "b"):
        storage = S3StorageBackend(bucket=bucket, prefix="prefix", lock_provider=provider)
        async with storage.resource_lock("skill"):
            pass
    assert keys == ["aws:a:prefix/skill", "aws:b:prefix/skill"]


@pytest.mark.asyncio
async def test_postgres_resource_lock_cancel_and_session_cleanup(postgres_url):
    engine = create_async_engine(postgres_url, pool_size=3, max_overflow=0)
    locks = PostgresResourceLocks(engine, timeout_seconds=1)
    ready = asyncio.Event()

    async def holder():
        async with locks("test/skill"):
            ready.set()
            await asyncio.Event().wait()

    try:
        task = asyncio.create_task(holder())
        await ready.wait()
        async with engine.connect() as connection:
            count = await connection.scalar(text("SELECT count(*) FROM pg_locks WHERE locktype = 'advisory' AND pid <> pg_backend_pid()"))
            assert count >= 1
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        async with locks("test/skill"):
            pass
        async with engine.connect() as connection:
            assert await connection.scalar(text("SELECT count(*) FROM pg_locks WHERE locktype = 'advisory' AND pid = pg_backend_pid()")) == 0
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_postgres_lock_wait_timeout_cancel_and_independent_key(postgres_url):
    engine = create_async_engine(postgres_url, pool_size=3, max_overflow=0)
    locks = PostgresResourceLocks(engine, timeout_seconds=0.15)
    competing_locks = PostgresResourceLocks(engine, timeout_seconds=0.15)

    async def enter(key):
        async with competing_locks(key):
            pass

    try:
        async with locks("same"):
            await enter("different")
            with pytest.raises(TimeoutError):
                await enter("same")
            task = asyncio.create_task(enter("same"))
            await asyncio.sleep(0.03)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        await enter("same")
        async with engine.connect() as connection:
            assert await connection.scalar(text("SELECT count(*) FROM pg_locks WHERE locktype = 'advisory'")) == 0
    finally:
        await engine.dispose()
