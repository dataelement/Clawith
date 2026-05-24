"""Base storage types and interfaces."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path


@dataclass
class StorageEntry:
    name: str
    key: str
    is_dir: bool
    size: int = 0
    modified_at: str = ""
    etag: str = ""
    version_id: str = ""
    content_hash: str = ""


@dataclass
class StorageVersion:
    key: str
    exists: bool
    is_dir: bool
    size: int = 0
    modified_at: str = ""
    etag: str = ""
    version_id: str = ""
    content_hash: str = ""

    @property
    def token(self) -> str:
        return self.version_id or self.etag or self.content_hash or f"{self.modified_at}:{self.size}"


@dataclass
class WriteCondition:
    version_token: str | None = None
    require_absent: bool = False


@dataclass
class ConditionalWriteResult:
    ok: bool
    conflict: bool = False
    current_version: StorageVersion | None = None


class StorageBackend:
    async def exists(self, key: str) -> bool:
        raise NotImplementedError

    async def is_file(self, key: str) -> bool:
        raise NotImplementedError

    async def is_dir(self, key: str) -> bool:
        raise NotImplementedError

    async def list_dir(self, key: str) -> list[StorageEntry]:
        raise NotImplementedError

    async def read_bytes(self, key: str) -> bytes:
        raise NotImplementedError

    async def read_text(self, key: str, encoding: str = "utf-8", errors: str = "replace") -> str:
        raw = await self.read_bytes(key)
        return raw.decode(encoding, errors=errors)

    async def write_bytes(self, key: str, data: bytes, content_type: str | None = None) -> None:
        raise NotImplementedError

    async def write_text(self, key: str, content: str, encoding: str = "utf-8") -> None:
        await self.write_bytes(key, content.encode(encoding), content_type="text/plain; charset=utf-8")

    async def delete(self, key: str) -> None:
        raise NotImplementedError

    async def delete_tree(self, key: str) -> None:
        raise NotImplementedError

    async def stat(self, key: str) -> StorageEntry:
        raise NotImplementedError

    async def get_version(self, key: str) -> StorageVersion:
        try:
            entry = await self.stat(key)
        except FileNotFoundError:
            return StorageVersion(key=key, exists=False, is_dir=False)
        return StorageVersion(
            key=entry.key,
            exists=True,
            is_dir=entry.is_dir,
            size=entry.size,
            modified_at=entry.modified_at,
            etag=entry.etag,
            version_id=entry.version_id,
            content_hash=entry.content_hash,
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

    async def delete_if_match(
        self,
        key: str,
        *,
        condition: WriteCondition | None = None,
    ) -> ConditionalWriteResult:
        current = await self.get_version(key)
        if condition:
            if condition.require_absent:
                if current.exists:
                    return ConditionalWriteResult(ok=False, conflict=True, current_version=current)
                return ConditionalWriteResult(ok=True, current_version=current)
            if condition.version_token is not None and current.token != condition.version_token:
                return ConditionalWriteResult(ok=False, conflict=True, current_version=current)
        if current.exists:
            await self.delete(key)
        return ConditionalWriteResult(ok=True, current_version=await self.get_version(key))

    async def local_path_for(self, key: str) -> Path | None:
        return None

    async def presign_download_url(self, key: str, filename: str | None = None, inline: bool = False) -> str | None:
        return None


def content_hash_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()
