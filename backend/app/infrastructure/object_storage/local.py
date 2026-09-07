"""Local filesystem storage backend."""

from __future__ import annotations

import asyncio
import base64
import errno
import fcntl
import hashlib
import json
import os
import shutil
import stat as stat_module
import uuid
from contextlib import AsyncExitStack, asynccontextmanager
from pathlib import Path

import aiofiles

from app.infrastructure.object_storage.base import (
    ConditionalWriteResult,
    StorageBackend,
    StorageEntry,
    StorageError,
    StorageVersion,
    WriteCondition,
    content_hash_bytes,
)
from app.infrastructure.object_storage.utils import normalize_storage_key


class LocalStorageBackend(StorageBackend):
    _TEMP_FILE_PREFIX = ".clawith-storage-tmp-"
    MAX_DIRECTORY_SCAN = 4096

    def __init__(self, root: str):
        self.root = Path(root)

    async def aclose(self) -> None:
        """Local handles belong to individual operations; no persistent client remains to close."""

    def _full_path(self, key: str) -> Path:
        normalized = normalize_storage_key(key)
        full = (self.root / normalized).resolve()
        root_resolved = self.root.resolve()
        try:
            full.relative_to(root_resolved)
        except ValueError as exc:
            raise ValueError("Storage key escapes the configured root") from exc
        return full

    async def exists(self, key: str) -> bool:
        return self._full_path(key).exists()

    async def is_file(self, key: str) -> bool:
        return self._full_path(key).is_file()

    async def is_dir(self, key: str) -> bool:
        return self._full_path(key).is_dir()

    async def list_dir(self, key: str) -> list[StorageEntry]:
        base = self._full_path(key)
        if not base.exists() or not base.is_dir():
            return []
        entries: list[StorageEntry] = []
        for entry in sorted(base.iterdir(), key=lambda item: (not item.is_dir(), item.name)):
            if entry.name == ".gitkeep" or entry.name.startswith(self._TEMP_FILE_PREFIX):
                continue
            stat = entry.stat()
            rel = str(entry.resolve().relative_to(self.root.resolve()))
            entries.append(
                StorageEntry(
                    name=entry.name,
                    key=rel,
                    is_dir=entry.is_dir(),
                    size=stat.st_size if entry.is_file() else 0,
                    modified_at=str(stat.st_mtime),
                    version_id=_local_version_token(stat),
                )
            )
        return entries

    async def read_bytes(self, key: str) -> bytes:
        path = self._full_path(key)
        async with aiofiles.open(path, "rb") as f:
            return await f.read()

    async def read_versioned(self, key: str, *, max_bytes: int) -> tuple[bytes, StorageVersion]:
        if max_bytes < 0:
            raise ValueError("max_bytes must be non-negative")
        return await asyncio.to_thread(_read_versioned, self._full_path(key), normalize_storage_key(key), max_bytes)

    async def list_dir_page(self, key: str, *, limit: int, cursor: str | None = None) -> tuple[list[StorageEntry], str | None]:
        if not 1 <= limit <= 1000:
            raise ValueError("limit must be between 1 and 1000")
        return await asyncio.to_thread(self._list_dir_page, key, limit, cursor)

    def _list_dir_page(self, key: str, limit: int, cursor: str | None) -> tuple[list[StorageEntry], str | None]:
        path = self._full_path(key)
        normalized = normalize_storage_key(key)
        before = _local_version_token(path.stat())
        after_name = ""
        if cursor is not None:
            try:
                decoded = json.loads(base64.b64decode(cursor, altchars=b"-_", validate=True))
            except (ValueError, UnicodeError) as exc:
                raise ValueError("Invalid directory cursor") from exc
            if not isinstance(decoded, list) or len(decoded) != 3 or decoded[:2] != [normalized, before] or not isinstance(decoded[2], str):
                raise ValueError("Directory cursor is invalid or directory changed")
            after_name = decoded[2]
        names: list[str] = []
        with os.scandir(path) as directory:
            for count, entry in enumerate(directory, start=1):
                if count > self.MAX_DIRECTORY_SCAN:
                    raise ValueError("Directory exceeds bounded scan budget of 4096 entries")
                if entry.name == ".gitkeep" or entry.name.startswith(self._TEMP_FILE_PREFIX):
                    continue
                names.append(entry.name)
        selected = sorted(name for name in names if name > after_name)
        entries: list[StorageEntry] = []
        for name in selected[:limit]:
            entry_key = f"{normalized}/{name}" if normalized else name
            entry_path = self._full_path(entry_key)
            details = entry_path.stat()
            is_directory = stat_module.S_ISDIR(details.st_mode)
            entries.append(StorageEntry(name=name, key=entry_key, is_dir=is_directory, size=0 if is_directory else details.st_size, modified_at=str(details.st_mtime), version_id=_local_version_token(details)))
        if _local_version_token(path.stat()) != before:
            raise StorageError("Directory changed during listing")
        next_cursor = None
        if len(selected) > limit:
            next_cursor = base64.urlsafe_b64encode(json.dumps([normalized, before, selected[limit - 1]]).encode()).decode()
        return entries, next_cursor

    async def mkdir(self, key: str) -> None:
        async with self._mutation_lock(key):
            await _run_sync_mutation(_mkdir, self._full_path(key))

    @asynccontextmanager
    async def resource_lock(self, key: str):
        async with self._named_lock("resource:" + normalize_storage_key(key), fcntl.LOCK_EX):
            yield

    @asynccontextmanager
    async def _named_lock(self, key: str, mode: int):
        # Lock files live outside the data root and remain stable across deletion.
        root = self.root.resolve()
        lock_root = root.parent / (".clawith-locks-" + hashlib.sha256(str(root).encode()).hexdigest())
        lock_root.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha256(key.encode()).hexdigest()
        fd = os.open(lock_root / digest, os.O_CREAT | os.O_RDWR, 0o600)
        acquired = False
        try:
            async with asyncio.timeout(30):
                while not acquired:
                    try:
                        fcntl.flock(fd, mode | fcntl.LOCK_NB)
                        acquired = True
                    except BlockingIOError:
                        await asyncio.sleep(0.01)
            yield
        finally:
            if acquired:
                fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    async def write_bytes(self, key: str, data: bytes, content_type: str | None = None) -> None:
        path = self._full_path(key)
        async with self._prepared_write(path, data) as prepared, self._mutation_lock(key):
            await _run_sync_mutation(_publish_write, prepared, path)

    async def delete(self, key: str) -> None:
        path = self._full_path(key)
        async with self._mutation_lock(key):
            await _run_sync_mutation(_local_delete, path, self.root.resolve())

    async def delete_tree(self, key: str) -> None:
        path = self._full_path(key)
        async with self._mutation_lock(key):
            await _run_sync_mutation(_local_delete_tree, path, self.root.resolve())

    async def rmdir_if_empty(self, key: str) -> bool:
        if not normalize_storage_key(key):
            raise ValueError("The storage root cannot be removed")
        async with self._mutation_lock(key):
            return await _run_sync_mutation(_rmdir_if_empty, self._full_path(key))

    async def stat(self, key: str) -> StorageEntry:
        path = self._full_path(key)
        stat = path.stat()
        version_id = _local_version_token(stat)
        return StorageEntry(
            name=path.name,
            key=normalize_storage_key(key),
            is_dir=path.is_dir(),
            size=stat.st_size if path.is_file() else 0,
            modified_at=str(stat.st_mtime),
            version_id=version_id,
        )

    async def get_version(self, key: str) -> StorageVersion:
        path = self._full_path(key)
        if not path.exists():
            return StorageVersion(key=normalize_storage_key(key), exists=False, is_dir=False)
        stat = path.stat()
        if path.is_dir():
            return StorageVersion(
                key=normalize_storage_key(key),
                exists=True,
                is_dir=True,
                modified_at=str(stat.st_mtime),
                version_id=_local_version_token(stat),
            )
        return StorageVersion(
            key=normalize_storage_key(key),
            exists=True,
            is_dir=False,
            size=stat.st_size,
            modified_at=str(stat.st_mtime),
            version_id=_local_version_token(stat),
        )

    async def write_bytes_if_match(
        self,
        key: str,
        data: bytes,
        *,
        condition: WriteCondition | None = None,
        content_type: str | None = None,
    ) -> ConditionalWriteResult:
        path = self._full_path(key)
        async with self._prepared_write(path, data) as prepared, self._mutation_lock(key):
            current = await self.get_version(key)
            if condition:
                if condition.require_absent and current.exists:
                    return ConditionalWriteResult(ok=False, conflict=True, current_version=current)
                if condition.version_token is not None and current.token != condition.version_token:
                    return ConditionalWriteResult(ok=False, conflict=True, current_version=current)
            await _run_sync_mutation(_publish_write, prepared, path)
            return ConditionalWriteResult(ok=True, current_version=await self.get_version(key))

    @asynccontextmanager
    async def _prepared_write(self, path: Path, data: bytes):
        prepared = path.parent / f"{self._TEMP_FILE_PREFIX}{uuid.uuid4().hex}"
        try:
            await _run_sync_mutation(_prepare_write, prepared, data)
            yield prepared
        finally:
            await _run_sync_mutation(prepared.unlink, True)

    async def delete_if_match(
        self,
        key: str,
        *,
        condition: WriteCondition | None = None,
    ) -> ConditionalWriteResult:
        path = self._full_path(key)
        async with self._mutation_lock(key):
            current = await self.get_version(key)
            if condition:
                if condition.require_absent:
                    if current.exists:
                        return ConditionalWriteResult(ok=False, conflict=True, current_version=current)
                    return ConditionalWriteResult(ok=True, current_version=current)
                if condition.version_token is not None and current.token != condition.version_token:
                    return ConditionalWriteResult(ok=False, conflict=True, current_version=current)
            if current.exists:
                await _run_sync_mutation(_local_delete, path, self.root.resolve())
            return ConditionalWriteResult(ok=True, current_version=await self.get_version(key))

    @asynccontextmanager
    async def _mutation_lock(self, key: str):
        """Shared ancestors and exclusive target coordinate namespace races without serializing siblings."""
        normalized = normalize_storage_key(key)
        self.root.mkdir(parents=True, exist_ok=True)
        root = self.root.resolve()
        open_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        lock_fd = os.open(root, open_flags)
        acquired = False
        try:
            while not acquired:
                try:
                    mode = fcntl.LOCK_SH if normalized else fcntl.LOCK_EX
                    fcntl.flock(lock_fd, mode | fcntl.LOCK_NB)
                    acquired = True
                except BlockingIOError:
                    await asyncio.sleep(0.01)
            async with AsyncExitStack() as stack:
                components = normalized.split("/") if normalized else []
                for index in range(len(components)):
                    prefix = "/".join(components[: index + 1])
                    mode = fcntl.LOCK_EX if index == len(components) - 1 else fcntl.LOCK_SH
                    await stack.enter_async_context(self._named_lock("mutation:" + prefix, mode))
                yield
        finally:
            if acquired:
                fcntl.flock(lock_fd, fcntl.LOCK_UN)
            os.close(lock_fd)


async def _run_sync_mutation(function, *args):
    """Keep the filesystem lock until an offloaded mutation really finishes."""
    task = asyncio.create_task(asyncio.to_thread(function, *args))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                continue
        task.result()
        raise


def _prepare_write(temp_path: Path, data: bytes) -> None:
    temp_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(temp_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o666)
    try:
        with os.fdopen(fd, "wb", closefd=True) as temp_file:
            fd = -1
            temp_file.write(data)
            temp_file.flush()
            os.fsync(temp_file.fileno())
    finally:
        if fd >= 0:
            os.close(fd)


def _publish_write(temp_path: Path, path: Path) -> None:
    if path.is_file():
        temp_path.chmod(stat_module.S_IMODE(path.stat().st_mode))
    os.replace(temp_path, path)


def _mkdir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def _rmdir_if_empty(path: Path) -> bool:
    try:
        path.rmdir()
    except FileNotFoundError:
        return True
    except OSError as exc:
        if exc.errno in (errno.ENOTEMPTY, errno.EEXIST):
            return False
        raise
    return True


def _local_delete(path: Path, root: Path) -> None:
    if not path.exists():
        return
    if path.is_dir():
        _local_delete_tree(path, root)
    else:
        path.unlink()


def _local_delete_tree(path: Path, root: Path) -> None:
    if not path.exists():
        return
    if path.resolve() != root:
        shutil.rmtree(path)
        return
    for child in path.iterdir():
        if child.is_dir():
            shutil.rmtree(child)
        else:
            child.unlink()


def _local_version_token(stat: os.stat_result) -> str:
    return f"{stat.st_dev}:{stat.st_ino}:{stat.st_mtime_ns}:{stat.st_ctime_ns}:{stat.st_size}"


def _read_versioned(path: Path, key: str, max_bytes: int) -> tuple[bytes, StorageVersion]:
    with path.open("rb") as stream:
        before = os.fstat(stream.fileno())
        if before.st_size > max_bytes:
            raise ValueError("Storage object exceeds max_bytes")
        data = stream.read(max_bytes + 1)
        after = os.fstat(stream.fileno())
    if len(data) > max_bytes:
        raise ValueError("Storage object exceeds max_bytes")
    if len(data) != after.st_size:
        raise StorageError("Storage object changed during read")
    if (before.st_mtime_ns, before.st_ctime_ns, before.st_size) != (after.st_mtime_ns, after.st_ctime_ns, after.st_size):
        raise StorageError("Storage object changed during read")
    digest = content_hash_bytes(data)
    return data, StorageVersion(key=key, exists=True, is_dir=False, size=len(data), modified_at=str(after.st_mtime), etag=digest, content_hash=digest, version_id=_local_version_token(after))
