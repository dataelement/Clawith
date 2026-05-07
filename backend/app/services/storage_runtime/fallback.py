"""Storage backend wrapper for gradual local-to-remote migration."""

from __future__ import annotations

from pathlib import Path

from app.services.storage_runtime.base import StorageBackend, StorageEntry


class FallbackStorageBackend(StorageBackend):
    """Read-through fallback backend.

    Writes go to the primary backend. Reads first try primary storage, then
    fallback storage; fallback hits are copied into primary storage so old local
    files are gradually migrated as they are used.
    """

    def __init__(self, primary: StorageBackend, fallback: StorageBackend):
        self.primary = primary
        self.fallback = fallback

    async def exists(self, key: str) -> bool:
        return await self.primary.exists(key) or await self.fallback.exists(key)

    async def is_file(self, key: str) -> bool:
        return await self.primary.is_file(key) or await self.fallback.is_file(key)

    async def is_dir(self, key: str) -> bool:
        return await self.primary.is_dir(key) or await self.fallback.is_dir(key)

    async def list_dir(self, key: str) -> list[StorageEntry]:
        entries_by_key: dict[str, StorageEntry] = {}
        for entry in await self.fallback.list_dir(key):
            entries_by_key[entry.key] = entry
        for entry in await self.primary.list_dir(key):
            entries_by_key[entry.key] = entry
        return sorted(entries_by_key.values(), key=lambda entry: (not entry.is_dir, entry.name))

    async def read_bytes(self, key: str) -> bytes:
        if await self.primary.exists(key) and await self.primary.is_file(key):
            return await self.primary.read_bytes(key)
        data = await self.fallback.read_bytes(key)
        await self.primary.write_bytes(key, data)
        return data

    async def write_bytes(self, key: str, data: bytes, content_type: str | None = None) -> None:
        await self.primary.write_bytes(key, data, content_type=content_type)

    async def delete(self, key: str) -> None:
        await self.primary.delete(key)
        await self.fallback.delete(key)

    async def delete_tree(self, key: str) -> None:
        await self.primary.delete_tree(key)
        await self.fallback.delete_tree(key)

    async def stat(self, key: str) -> StorageEntry:
        if await self.primary.exists(key):
            return await self.primary.stat(key)
        entry = await self.fallback.stat(key)
        if not entry.is_dir:
            data = await self.fallback.read_bytes(key)
            await self.primary.write_bytes(key, data)
        return entry

    async def local_path_for(self, key: str) -> Path | None:
        if await self.primary.exists(key):
            return await self.primary.local_path_for(key)
        path = await self.fallback.local_path_for(key)
        if path is not None and await self.fallback.is_file(key):
            data = await self.fallback.read_bytes(key)
            await self.primary.write_bytes(key, data)
        return path

    async def presign_download_url(self, key: str, filename: str | None = None, inline: bool = False) -> str | None:
        if not await self.primary.exists(key) and await self.fallback.exists(key) and await self.fallback.is_file(key):
            data = await self.fallback.read_bytes(key)
            await self.primary.write_bytes(key, data)
        return await self.primary.presign_download_url(key, filename=filename, inline=inline)
