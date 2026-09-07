import asyncio
import threading
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.infrastructure.object_storage import local as local_runtime
from app.infrastructure.object_storage.local import LocalStorageBackend
from app.infrastructure.object_storage.s3 import S3StorageBackend
from app.infrastructure.resource_locks import PostgresResourceLocks


async def test_nested_postgres_keys_share_one_connection_even_with_one_pool_slot(postgres_url):
    engine = create_async_engine(postgres_url, pool_size=1, max_overflow=0)
    locks = PostgresResourceLocks(engine, timeout_seconds=.5)
    try:
        async with locks("catalog"), locks("binding"), locks("preparation"), locks("package"), locks("package"):
            pass
        async with engine.connect() as connection:
            assert await connection.scalar(text("SELECT count(*) FROM pg_locks WHERE locktype='advisory' AND pid=pg_backend_pid()")) == 0
    finally:
        await engine.dispose()


async def test_concurrent_nested_operations_finish_in_a_finite_lock_pool(postgres_url):
    engine = create_async_engine(postgres_url, pool_size=2, max_overflow=0)
    locks = PostgresResourceLocks(engine, timeout_seconds=2)
    completed = []
    async def operation(index):
        async with locks(f"catalog/{index}"), locks(f"binding/{index}"), locks(f"package/{index}"):
            await asyncio.sleep(.01)
            completed.append(index)
    try:
        await asyncio.wait_for(asyncio.gather(*(operation(index) for index in range(8))), 3)
        assert sorted(completed) == list(range(8))
        async with engine.connect() as connection:
            assert await connection.scalar(text("SELECT count(*) FROM pg_locks WHERE locktype='advisory' AND pid=pg_backend_pid()")) == 0
    finally:
        await engine.dispose()


async def test_inherited_live_lease_is_rejected_but_completed_lease_does_not_poison_child(postgres_url):
    engine = create_async_engine(postgres_url, pool_size=1, max_overflow=0)
    locks = PostgresResourceLocks(engine, timeout_seconds=.5)
    ready = asyncio.Event()
    async def enter(wait=False):
        if wait:
            await ready.wait()
        async with locks("child"):
            return "done"
    try:
        async with locks("parent"):
            child = asyncio.create_task(enter())
            with pytest.raises(RuntimeError, match="inherited"):
                await child
            later = asyncio.create_task(enter(True))
        ready.set()
        assert await later == "done"
    finally:
        await engine.dispose()


async def test_nested_cancellation_releases_every_advisory_lock(postgres_url):
    engine = create_async_engine(postgres_url, pool_size=1, max_overflow=0)
    locks = PostgresResourceLocks(engine, timeout_seconds=.5)
    entered = asyncio.Event()
    async def operation():
        async with locks("outer"), locks("inner"):
            entered.set()
            await asyncio.Event().wait()
    try:
        task = asyncio.create_task(operation())
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        async with engine.connect() as connection:
            assert await connection.scalar(text("SELECT count(*) FROM pg_locks WHERE locktype='advisory' AND pid=pg_backend_pid()")) == 0
        async with locks("outer"), locks("inner"):
            pass
    finally:
        await engine.dispose()


async def test_local_unrelated_commit_progress_and_parent_delete_waits(tmp_path, monkeypatch):
    storage = LocalStorageBackend(str(tmp_path))
    started, release = threading.Event(), threading.Event()
    original = local_runtime._publish_write
    def slow_publish(prepared, path):
        if path.name == "slow":
            started.set()
            if not release.wait(3):
                raise TimeoutError("test release missing")
        return original(prepared, path)
    monkeypatch.setattr(local_runtime, "_publish_write", slow_publish)
    task = asyncio.create_task(storage.write_bytes("one/slow", b"first"))
    delete = None
    try:
        assert await asyncio.to_thread(started.wait, 1)
        delete = asyncio.create_task(storage.delete_tree("one"))
        await asyncio.wait_for(storage.write_bytes("two/fast", b"second"), .5)
        await asyncio.sleep(.03)
        assert not delete.done()
        assert await storage.read_bytes("two/fast") == b"second"
    finally:
        release.set()
        await task
        if delete:
            await delete
    assert not await storage.exists("one")
    assert await storage.read_bytes("two/fast") == b"second"


async def test_local_rmdir_only_removes_empty_directories(tmp_path):
    storage = LocalStorageBackend(str(tmp_path))
    assert await storage.rmdir_if_empty("absent")
    await storage.mkdir("empty")
    assert await storage.rmdir_if_empty("empty")
    assert not await storage.exists("empty")
    await storage.write_bytes("nonempty/child", b"preserved")
    assert not await storage.rmdir_if_empty("nonempty")
    assert await storage.read_bytes("nonempty/child") == b"preserved"


@pytest.mark.parametrize("before,after,expected,deleted", [
    ([], [], True, False),
    ([{"Key":"root/dir/"}], [], True, True),
    ([{"Key":"root/dir/child"}], [], False, False),
    ([{"Key":"root/dir/"}], [{"Key":"root/dir/new"}], False, True),
])
async def test_s3_empty_directory_deletes_only_marker_and_preserves_racing_children(before, after, expected, deleted):
    storage = S3StorageBackend(bucket="bucket", prefix="root")
    client = AsyncMock()
    client.list_objects_v2.side_effect = [{"Contents": before}, {"Contents": after}]
    @asynccontextmanager
    async def session():
        yield client
    storage._async_client = session
    assert await storage.rmdir_if_empty("dir") is expected
    if deleted:
        client.delete_object.assert_awaited_once_with(Bucket="bucket", Key="root/dir/")
    else:
        client.delete_object.assert_not_awaited()
    client.delete_objects.assert_not_awaited()
