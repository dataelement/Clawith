"""Atomic conditional-mutation contracts for storage backends."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import os
import subprocess
import sys
from typing import Any

import pytest

from app.services.storage_runtime import local as local_runtime
from app.services.storage_runtime.base import WriteCondition
from app.services.storage_runtime.local import LocalStorageBackend
from app.services.storage_runtime.s3 import S3StorageBackend


class _BarrierLocalStorage(LocalStorageBackend):
    """Expose the former check-then-mutate race deterministically."""

    def __init__(self, root: str, barrier: asyncio.Barrier) -> None:
        super().__init__(root)
        self._barrier = barrier

    async def write_bytes(
        self,
        key: str,
        data: bytes,
        content_type: str | None = None,
    ) -> None:
        await self._barrier.wait()
        await super().write_bytes(key, data, content_type=content_type)

    async def delete(self, key: str) -> None:
        await self._barrier.wait()
        await super().delete(key)


@pytest.mark.asyncio
async def test_local_same_version_barrier_allows_only_one_writer(tmp_path) -> None:
    seed = LocalStorageBackend(str(tmp_path))
    await seed.write_text("workspace/report.md", "v1")
    version = await seed.get_version("workspace/report.md")
    barrier = asyncio.Barrier(2)
    first = _BarrierLocalStorage(str(tmp_path), barrier)
    second = _BarrierLocalStorage(str(tmp_path), barrier)

    results = await asyncio.gather(
        first.write_bytes_if_match(
            "workspace/report.md",
            b"first",
            condition=WriteCondition(version_token=version.token),
        ),
        second.write_bytes_if_match(
            "workspace/report.md",
            b"second",
            condition=WriteCondition(version_token=version.token),
        ),
    )

    assert sum(result.ok for result in results) == 1
    assert sum(result.conflict for result in results) == 1
    assert await seed.read_text("workspace/report.md") in {"first", "second"}


@pytest.mark.asyncio
async def test_local_require_absent_barrier_allows_only_one_writer(tmp_path) -> None:
    barrier = asyncio.Barrier(2)
    first = _BarrierLocalStorage(str(tmp_path), barrier)
    second = _BarrierLocalStorage(str(tmp_path), barrier)

    results = await asyncio.gather(
        first.write_bytes_if_match(
            "workspace/new.md",
            b"first",
            condition=WriteCondition(require_absent=True),
        ),
        second.write_bytes_if_match(
            "workspace/new.md",
            b"second",
            condition=WriteCondition(require_absent=True),
        ),
    )

    assert sum(result.ok for result in results) == 1
    assert sum(result.conflict for result in results) == 1


@pytest.mark.asyncio
async def test_local_same_version_barrier_allows_only_one_deleter(tmp_path) -> None:
    seed = LocalStorageBackend(str(tmp_path))
    await seed.write_text("workspace/report.md", "v1")
    version = await seed.get_version("workspace/report.md")
    barrier = asyncio.Barrier(2)
    first = _BarrierLocalStorage(str(tmp_path), barrier)
    second = _BarrierLocalStorage(str(tmp_path), barrier)

    results = await asyncio.gather(
        first.delete_if_match(
            "workspace/report.md",
            condition=WriteCondition(version_token=version.token),
        ),
        second.delete_if_match(
            "workspace/report.md",
            condition=WriteCondition(version_token=version.token),
        ),
    )

    assert sum(result.ok for result in results) == 1
    assert sum(result.conflict for result in results) == 1
    assert not await seed.exists("workspace/report.md")


@pytest.mark.asyncio
async def test_local_write_atomically_replaces_from_the_target_directory(
    monkeypatch,
    tmp_path,
) -> None:
    storage = LocalStorageBackend(str(tmp_path))
    replacements: list[tuple[str, str]] = []
    real_replace = local_runtime.os.replace

    def record_replace(source, destination) -> None:
        replacements.append((os.fspath(source), os.fspath(destination)))
        real_replace(source, destination)

    monkeypatch.setattr(local_runtime.os, "replace", record_replace)

    await storage.write_bytes("workspace/report.md", b"complete")

    assert len(replacements) == 1
    source, destination = replacements[0]
    assert os.path.dirname(source) == os.path.dirname(destination)
    assert await storage.read_bytes("workspace/report.md") == b"complete"
    assert all(
        not entry.name.startswith(storage._TEMP_FILE_PREFIX)
        for entry in await storage.list_dir("workspace")
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "operation",
    [
        "write",
        "delete",
        "delete_tree",
        "conditional_write",
        "conditional_delete",
    ],
)
async def test_every_local_mutation_waits_for_the_shared_process_lock(
    tmp_path,
    operation: str,
) -> None:
    fcntl = pytest.importorskip("fcntl")
    storage = LocalStorageBackend(str(tmp_path))
    await storage.write_text("workspace/file.md", "v1")
    await storage.write_text("tree/file.md", "v1")
    version = await storage.get_version("workspace/file.md")
    lock_fd = os.open(tmp_path, os.O_RDONLY)
    fcntl.flock(lock_fd, fcntl.LOCK_EX)
    try:
        if operation == "write":
            task = asyncio.create_task(storage.write_bytes("workspace/file.md", b"v2"))
        elif operation == "delete":
            task = asyncio.create_task(storage.delete("workspace/file.md"))
        elif operation == "delete_tree":
            task = asyncio.create_task(storage.delete_tree("tree"))
        elif operation == "conditional_write":
            task = asyncio.create_task(
                storage.write_bytes_if_match(
                    "workspace/file.md",
                    b"v2",
                    condition=WriteCondition(version_token=version.token),
                )
            )
        else:
            task = asyncio.create_task(
                storage.delete_if_match(
                    "workspace/file.md",
                    condition=WriteCondition(version_token=version.token),
                )
            )
        await asyncio.sleep(0.05)
        still_waiting = not task.done()
    finally:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        os.close(lock_fd)

    await asyncio.wait_for(task, timeout=1)
    assert still_waiting


@pytest.mark.asyncio
async def test_local_mutation_waits_for_lock_held_by_another_process(tmp_path) -> None:
    pytest.importorskip("fcntl")
    storage = LocalStorageBackend(str(tmp_path))
    await storage.write_text("workspace/file.md", "v1")
    script = (
        "import fcntl, os, sys; "
        "fd = os.open(sys.argv[1], os.O_RDONLY); "
        "fcntl.flock(fd, fcntl.LOCK_EX); "
        "print('locked', flush=True); "
        "sys.stdin.readline(); "
        "fcntl.flock(fd, fcntl.LOCK_UN); "
        "os.close(fd)"
    )
    process = subprocess.Popen(
        [sys.executable, "-c", script, os.fspath(tmp_path)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    assert process.stdout is not None
    assert process.stdin is not None
    try:
        ready = await asyncio.to_thread(process.stdout.readline)
        assert ready.strip() == "locked"
        task = asyncio.create_task(storage.write_bytes("workspace/file.md", b"v2"))
        await asyncio.sleep(0.05)
        assert not task.done()
        process.stdin.write("\n")
        process.stdin.flush()
        assert await asyncio.to_thread(process.wait, 1) == 0
        await asyncio.wait_for(task, timeout=1)
    finally:
        if process.poll() is None:
            process.kill()
            await asyncio.to_thread(process.wait)


class _S3Error(Exception):
    def __init__(self, status: int, code: str) -> None:
        super().__init__(code)
        self.response = {
            "ResponseMetadata": {"HTTPStatusCode": status},
            "Error": {"Code": code},
        }


class _HeadClient:
    def __init__(self, response: dict[str, Any] | None = None, error: Exception | None = None) -> None:
        self.response = response or {}
        self.error = error
        self.calls: list[dict[str, Any]] = []

    def head_object(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return self.response


class _GetClient:
    def __init__(self, *, error: Exception | None = None) -> None:
        self.error = error
        self.calls: list[dict[str, Any]] = []

    def get_object(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        raise AssertionError("test get client requires an explicit outcome")


class _MutationClient:
    def __init__(
        self,
        *,
        put_response: dict[str, Any] | None = None,
        delete_response: dict[str, Any] | None = None,
        error: Exception | None = None,
    ) -> None:
        self.put_response = put_response or {"ETag": '"written-etag"'}
        self.delete_response = delete_response or {}
        self.error = error
        self.put_calls: list[dict[str, Any]] = []
        self.delete_calls: list[dict[str, Any]] = []

    async def put_object(self, **kwargs):
        self.put_calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return self.put_response

    async def delete_object(self, **kwargs):
        self.delete_calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return self.delete_response


def _install_async_client(monkeypatch, backend: S3StorageBackend, client: _MutationClient) -> None:
    @asynccontextmanager
    async def client_context():
        yield client

    monkeypatch.setattr(backend, "_async_client", client_context)


def _existing_head(*, etag: str = '"etag-v1"', version_id: str = "version-v1") -> dict[str, Any]:
    return {
        "ContentLength": 2,
        "LastModified": "now",
        "ETag": etag,
        "VersionId": version_id,
    }


@pytest.mark.asyncio
async def test_s3_version_token_uses_head_etag_for_native_conditional_put(monkeypatch) -> None:
    backend = S3StorageBackend(bucket="bucket")
    head = _HeadClient(_existing_head())
    mutation = _MutationClient(put_response={"ETag": '"etag-v2"', "VersionId": "version-v2"})
    backend._client = head
    _install_async_client(monkeypatch, backend, mutation)

    result = await backend.write_bytes_if_match(
        "workspace/report.md",
        b"v2",
        condition=WriteCondition(version_token="version-v1"),
        content_type="text/plain",
    )

    assert result.ok is True
    assert len(head.calls) == 1
    assert mutation.put_calls == [
        {
            "Bucket": "bucket",
            "Key": "workspace/report.md",
            "Body": b"v2",
            "ContentType": "text/plain",
            "IfMatch": '"etag-v1"',
        }
    ]
    assert result.current_version is not None
    assert result.current_version.token == "version-v2"


@pytest.mark.asyncio
async def test_s3_require_absent_uses_native_if_none_match_without_head(monkeypatch) -> None:
    backend = S3StorageBackend(
        bucket="bucket",
        endpoint_url="https://storage.googleapis.com",
    )
    mutation = _MutationClient()
    _install_async_client(monkeypatch, backend, mutation)

    result = await backend.write_bytes_if_match(
        "workspace/new.md",
        b"new",
        condition=WriteCondition(require_absent=True),
    )

    assert result.ok is True
    assert mutation.put_calls[0]["IfNoneMatch"] == "*"


@pytest.mark.asyncio
async def test_s3_unconditional_write_keeps_one_unconditional_mutation(monkeypatch) -> None:
    backend = S3StorageBackend(bucket="bucket")
    head = _HeadClient(_existing_head())
    mutation = _MutationClient()
    backend._client = head
    _install_async_client(monkeypatch, backend, mutation)

    result = await backend.write_bytes_if_match("workspace/report.md", b"v2")

    assert result.ok is True
    assert len(mutation.put_calls) == 1
    assert "IfMatch" not in mutation.put_calls[0]
    assert "IfNoneMatch" not in mutation.put_calls[0]


@pytest.mark.asyncio
async def test_s3_unconditional_delete_keeps_one_unconditional_mutation(monkeypatch) -> None:
    backend = S3StorageBackend(bucket="bucket")
    backend._client = _HeadClient(_existing_head())
    mutation = _MutationClient()
    _install_async_client(monkeypatch, backend, mutation)

    result = await backend.delete_if_match("workspace/report.md")

    assert result.ok is True
    assert len(mutation.delete_calls) == 1
    assert "IfMatch" not in mutation.delete_calls[0]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "code"),
    [(412, "PreconditionFailed"), (409, "ConditionalRequestConflict")],
)
async def test_s3_conditional_put_maps_provider_conflict(
    monkeypatch,
    status: int,
    code: str,
) -> None:
    backend = S3StorageBackend(bucket="bucket")
    backend._client = _HeadClient(_existing_head())
    mutation = _MutationClient(error=_S3Error(status, code))
    _install_async_client(monkeypatch, backend, mutation)

    result = await backend.write_bytes_if_match(
        "workspace/report.md",
        b"v2",
        condition=WriteCondition(version_token="version-v1"),
    )

    assert result.ok is False
    assert result.conflict is True
    assert len(mutation.put_calls) == 1


@pytest.mark.asyncio
async def test_s3_version_token_uses_head_etag_for_native_conditional_delete(monkeypatch) -> None:
    backend = S3StorageBackend(bucket="bucket")
    head = _HeadClient(_existing_head())
    mutation = _MutationClient()
    backend._client = head
    _install_async_client(monkeypatch, backend, mutation)

    result = await backend.delete_if_match(
        "workspace/report.md",
        condition=WriteCondition(version_token="version-v1"),
    )

    assert result.ok is True
    assert len(head.calls) == 1
    assert mutation.delete_calls == [
        {
            "Bucket": "bucket",
            "Key": "workspace/report.md",
            "IfMatch": '"etag-v1"',
        }
    ]
    assert result.current_version is not None
    assert result.current_version.exists is False


@pytest.mark.asyncio
async def test_s3_conditional_delete_maps_provider_conflict(monkeypatch) -> None:
    backend = S3StorageBackend(bucket="bucket")
    backend._client = _HeadClient(_existing_head())
    mutation = _MutationClient(error=_S3Error(412, "PreconditionFailed"))
    _install_async_client(monkeypatch, backend, mutation)

    result = await backend.delete_if_match(
        "workspace/report.md",
        condition=WriteCondition(version_token="version-v1"),
    )

    assert result.ok is False
    assert result.conflict is True
    assert len(mutation.delete_calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error",
    [
        _S3Error(403, "AccessDenied"),
        _S3Error(500, "InternalError"),
        TimeoutError("head timed out"),
    ],
)
async def test_s3_head_operational_failures_propagate(error: Exception) -> None:
    backend = S3StorageBackend(bucket="bucket")
    backend._client = _HeadClient(error=error)

    with pytest.raises(type(error)):
        await backend.get_version("workspace/report.md")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error",
    [_S3Error(404, "NoSuchBucket"), _S3Error(404, "WrongEndpoint")],
)
async def test_s3_head_non_object_404_failures_propagate(error: Exception) -> None:
    backend = S3StorageBackend(bucket="bucket")
    backend._client = _HeadClient(error=error)

    with pytest.raises(_S3Error):
        await backend.get_version("workspace/report.md")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error",
    [_S3Error(404, "404"), _S3Error(404, "NoSuchKey"), _S3Error(404, "NotFound")],
)
async def test_s3_head_explicit_missing_returns_absent(error: Exception) -> None:
    backend = S3StorageBackend(bucket="bucket")
    backend._client = _HeadClient(error=error)

    version = await backend.get_version("workspace/report.md")

    assert version.exists is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error",
    [_S3Error(404, "404"), _S3Error(404, "NoSuchKey"), _S3Error(404, "NotFound")],
)
async def test_s3_read_explicit_missing_raises_file_not_found(error: Exception) -> None:
    backend = S3StorageBackend(bucket="bucket")
    backend._client = _GetClient(error=error)

    with pytest.raises(FileNotFoundError):
        await backend.read_bytes("runtime/tool-results/missing.json")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error",
    [_S3Error(500, "InternalError"), TimeoutError("read timed out")],
)
async def test_s3_read_operational_failures_propagate(error: Exception) -> None:
    backend = S3StorageBackend(bucket="bucket")
    backend._client = _GetClient(error=error)

    with pytest.raises(type(error)):
        await backend.read_bytes("runtime/tool-results/unavailable.json")


@pytest.mark.asyncio
async def test_s3_missing_etag_fails_closed_before_conditional_mutation(monkeypatch) -> None:
    backend = S3StorageBackend(bucket="bucket")
    backend._client = _HeadClient(_existing_head(etag=""))
    mutation = _MutationClient()
    _install_async_client(monkeypatch, backend, mutation)

    with pytest.raises(RuntimeError, match="ETag"):
        await backend.write_bytes_if_match(
            "workspace/report.md",
            b"v2",
            condition=WriteCondition(version_token="version-v1"),
        )

    assert mutation.put_calls == []


@pytest.mark.asyncio
async def test_s3_sdk_rejecting_condition_header_fails_closed(monkeypatch) -> None:
    backend = S3StorageBackend(bucket="bucket")
    mutation = _MutationClient(error=TypeError("unknown parameter IfNoneMatch"))
    _install_async_client(monkeypatch, backend, mutation)

    with pytest.raises(TypeError, match="IfNoneMatch"):
        await backend.write_bytes_if_match(
            "workspace/new.md",
            b"new",
            condition=WriteCondition(require_absent=True),
        )

    assert len(mutation.put_calls) == 1


@pytest.mark.asyncio
async def test_s3_conditional_write_without_stable_response_version_is_unknown(
    monkeypatch,
) -> None:
    backend = S3StorageBackend(bucket="bucket")
    mutation = _MutationClient(put_response={"ResponseMetadata": {"HTTPStatusCode": 200}})
    _install_async_client(monkeypatch, backend, mutation)

    with pytest.raises(RuntimeError, match="ETag or VersionId"):
        await backend.write_bytes_if_match(
            "workspace/new.md",
            b"new",
            condition=WriteCondition(require_absent=True),
        )

    assert len(mutation.put_calls) == 1
