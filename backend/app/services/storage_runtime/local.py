"""Local filesystem storage backend."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import fcntl
import os
from pathlib import Path
import shutil
import stat as stat_module
import uuid

import aiofiles
from fastapi import HTTPException, status

from app.services.storage_runtime.base import (
    ConditionalWriteResult,
    StorageBackend,
    StorageEntry,
    StorageVersion,
    WriteCondition,
    content_hash_bytes,
)
from app.services.storage_runtime.utils import normalize_storage_key


class LocalStorageBackend(StorageBackend):
    _TEMP_FILE_PREFIX = ".clawith-storage-tmp-"

    def __init__(self, root: str):
        self.root = Path(root)

    def _full_path(self, key: str) -> Path:
        normalized = normalize_storage_key(key)
        full = (self.root / normalized).resolve()
        root_resolved = self.root.resolve()
        if not str(full).startswith(str(root_resolved)):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Path traversal not allowed")
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
                    version_id=_local_version_token(stat, None),
                )
            )
        return entries

    async def read_bytes(self, key: str) -> bytes:
        path = self._full_path(key)
        async with aiofiles.open(path, "rb") as f:
            return await f.read()

    async def write_bytes(self, key: str, data: bytes, content_type: str | None = None) -> None:
        path = self._full_path(key)
        async with self._mutation_lock():
            await _run_sync_mutation(
                _atomic_write_bytes,
                path,
                data,
                self._TEMP_FILE_PREFIX,
            )

    async def delete(self, key: str) -> None:
        path = self._full_path(key)
        async with self._mutation_lock():
            await _run_sync_mutation(_local_delete, path, self.root.resolve())

    async def delete_tree(self, key: str) -> None:
        path = self._full_path(key)
        async with self._mutation_lock():
            await _run_sync_mutation(_local_delete_tree, path, self.root.resolve())

    async def stat(self, key: str) -> StorageEntry:
        path = self._full_path(key)
        stat = path.stat()
        file_hash = ""
        version_id = _local_version_token(stat, None)
        if path.is_file():
            data = await self.read_bytes(key)
            file_hash = content_hash_bytes(data)
            version_id = _local_version_token(stat, file_hash)
        return StorageEntry(
            name=path.name,
            key=normalize_storage_key(key),
            is_dir=path.is_dir(),
            size=stat.st_size if path.is_file() else 0,
            modified_at=str(stat.st_mtime),
            version_id=version_id,
            etag=file_hash,
            content_hash=file_hash,
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
                version_id=_local_version_token(stat, None),
            )
        data = await self.read_bytes(key)
        file_hash = content_hash_bytes(data)
        return StorageVersion(
            key=normalize_storage_key(key),
            exists=True,
            is_dir=False,
            size=stat.st_size,
            modified_at=str(stat.st_mtime),
            etag=file_hash,
            version_id=_local_version_token(stat, file_hash),
            content_hash=file_hash,
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
        async with self._mutation_lock():
            current = await self.get_version(key)
            if condition:
                if condition.require_absent and current.exists:
                    return ConditionalWriteResult(ok=False, conflict=True, current_version=current)
                if condition.version_token is not None and current.token != condition.version_token:
                    return ConditionalWriteResult(ok=False, conflict=True, current_version=current)
            await _run_sync_mutation(
                _atomic_write_bytes,
                path,
                data,
                self._TEMP_FILE_PREFIX,
            )
            return ConditionalWriteResult(ok=True, current_version=await self.get_version(key))

    async def delete_if_match(
        self,
        key: str,
        *,
        condition: WriteCondition | None = None,
    ) -> ConditionalWriteResult:
        path = self._full_path(key)
        async with self._mutation_lock():
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
    async def _mutation_lock(self):
        """Serialize mutations across every process sharing this local root."""
        self.root.mkdir(parents=True, exist_ok=True)
        root = self.root.resolve()
        open_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        lock_fd = os.open(root, open_flags)
        acquired = False
        try:
            while not acquired:
                try:
                    fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    acquired = True
                except BlockingIOError:
                    await asyncio.sleep(0.01)
            yield
        finally:
            if acquired:
                fcntl.flock(lock_fd, fcntl.LOCK_UN)
            os.close(lock_fd)

    async def local_path_for(self, key: str) -> Path | None:
        return self._full_path(key)


async def _run_sync_mutation(function, *args):
    """Keep the filesystem lock until an offloaded mutation really finishes."""
    task = asyncio.create_task(asyncio.to_thread(function, *args))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError as cancelled:
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                continue
        task.result()
        raise cancelled


def _atomic_write_bytes(path: Path, data: bytes, temp_prefix: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.parent / f"{temp_prefix}{uuid.uuid4().hex}"
    existing_mode: int | None = None
    if path.is_file():
        existing_mode = stat_module.S_IMODE(path.stat().st_mode)
    fd = os.open(temp_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o666)
    try:
        if existing_mode is not None:
            os.fchmod(fd, existing_mode)
        with os.fdopen(fd, "wb", closefd=True) as temp_file:
            fd = -1
            temp_file.write(data)
            temp_file.flush()
            os.fsync(temp_file.fileno())
        os.replace(temp_path, path)
    finally:
        if fd >= 0:
            os.close(fd)
        if temp_path.exists():
            temp_path.unlink()


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


def _local_version_token(stat, file_hash: str | None) -> str:
    hash_part = file_hash or ""
    return f"{stat.st_mtime_ns}:{stat.st_size}:{hash_part}"
