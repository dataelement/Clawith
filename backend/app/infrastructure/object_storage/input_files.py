"""Bounded immutable input blobs over the application-owned storage backend."""

import hashlib
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass

from app.infrastructure.object_storage.base import StorageBackend, StorageError, StorageVersion, WriteCondition
from app.infrastructure.object_storage.utils import normalize_storage_key

MAX_INPUT_FILE_BYTES = 4 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class InputFileObject:
    revision: str
    byte_size: int
    sha256: str


def _key(value: str) -> str:
    try:
        normalized = normalize_storage_key(value)
        if not normalized or normalized != value or len(value.encode()) > 1024:
            raise ValueError()
    except (ValueError, UnicodeError):
        raise StorageError("Input file storage key is invalid") from None
    return normalized


def _validate_version(content: bytes, version: StorageVersion) -> None:
    if not version.exists or version.is_dir or version.size != len(content) or not version.token:
        raise StorageError("Input file storage revision is invalid")


def _object(content: bytes, version: StorageVersion) -> InputFileObject:
    _validate_version(content, version)
    return InputFileObject(version.token, len(content), hashlib.sha256(content).hexdigest())


class InputFileStorage:
    """The caller owns publication guards; this wrapper never closes the shared backend."""

    def __init__(self, backend: StorageBackend) -> None:
        self._backend = backend

    def guard(self, storage_key: str) -> AbstractAsyncContextManager[None]:
        return self._backend.resource_lock("input-file-publication/" + _key(storage_key))

    async def put_if_absent(self, storage_key: str, content: bytes) -> InputFileObject:
        key = _key(storage_key)
        if len(content) > MAX_INPUT_FILE_BYTES:
            raise StorageError("Input file exceeds the four-MiB bound")
        result = await self._backend.write_bytes_if_match(key, content, condition=WriteCondition(require_absent=True))
        if result.ok:
            if result.current_version is None:
                raise StorageError("Input file write did not identify its revision")
            return _object(content, result.current_version)
        if not result.conflict:
            raise StorageError("Input file write was not confirmed")
        existing, version = await self._read(key)
        if existing != content:
            raise StorageError("Input file already exists with different content")
        return _object(existing, version)

    async def inspect(self, storage_key: str) -> InputFileObject | None:
        key = _key(storage_key)
        try:
            content, version = await self._read(key)
        except FileNotFoundError:
            return None
        return _object(content, version)

    async def read_range(self, storage_key: str, *, revision: str, offset: int, limit: int) -> bytes:
        key = _key(storage_key)
        if not revision or type(offset) is not int or type(limit) is not int or not 0 <= offset <= MAX_INPUT_FILE_BYTES or not 0 <= limit <= MAX_INPUT_FILE_BYTES:
            raise StorageError("Input file read range is invalid")
        content, version = await self._read(key)
        if version.token != revision:
            raise StorageError("Input file revision changed")
        if offset > len(content):
            raise StorageError("Input file offset is beyond its content")
        return content[offset:offset + limit]

    async def delete_if_revision(self, storage_key: str, *, revision: str) -> bool:
        key = _key(storage_key)
        if not revision:
            raise StorageError("Input file revision is required")
        result = await self._backend.delete_if_match(key, condition=WriteCondition(version_token=revision))
        if not result.ok and not result.conflict:
            raise StorageError("Input file deletion was not confirmed")
        return result.ok

    async def _read(self, key: str) -> tuple[bytes, StorageVersion]:
        try:
            content, version = await self._backend.read_versioned(key, max_bytes=MAX_INPUT_FILE_BYTES)
        except ValueError:
            raise StorageError("Input file exceeds the read bound or has invalid storage metadata") from None
        if len(content) > MAX_INPUT_FILE_BYTES:
            raise StorageError("Input file exceeds the read bound")
        _validate_version(content, version)
        return content, version
