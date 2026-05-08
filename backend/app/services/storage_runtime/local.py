"""Local filesystem storage backend."""

from __future__ import annotations

import asyncio
from pathlib import Path

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
            if entry.name == ".gitkeep":
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
        path.parent.mkdir(parents=True, exist_ok=True)
        async with aiofiles.open(path, "wb") as f:
            await f.write(data)

    async def delete(self, key: str) -> None:
        path = self._full_path(key)
        if not path.exists():
            return
        if path.is_dir():
            await self.delete_tree(key)
        else:
            path.unlink()

    async def delete_tree(self, key: str) -> None:
        path = self._full_path(key)
        if not path.exists():
            return
        await asyncio.to_thread(_local_delete_tree, path)

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
        current = await self.get_version(key)
        if condition:
            if condition.require_absent and current.exists:
                return ConditionalWriteResult(ok=False, conflict=True, current_version=current)
            if condition.version_token is not None and current.token != condition.version_token:
                return ConditionalWriteResult(ok=False, conflict=True, current_version=current)
        await self.write_bytes(key, data, content_type=content_type)
        return ConditionalWriteResult(ok=True, current_version=await self.get_version(key))

    async def local_path_for(self, key: str) -> Path | None:
        return self._full_path(key)


def _local_delete_tree(path: Path) -> None:
    import shutil

    shutil.rmtree(path)


def _local_version_token(stat, file_hash: str | None) -> str:
    hash_part = file_hash or ""
    return f"{stat.st_mtime_ns}:{stat.st_size}:{hash_part}"
