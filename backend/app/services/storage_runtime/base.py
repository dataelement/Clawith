"""Base storage types and interfaces."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass
class StorageEntry:
    name: str
    key: str
    is_dir: bool
    size: int = 0
    modified_at: str = ""


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

    async def local_path_for(self, key: str) -> Path | None:
        return None

    async def presign_download_url(self, key: str, filename: str | None = None, inline: bool = False) -> str | None:
        return None
