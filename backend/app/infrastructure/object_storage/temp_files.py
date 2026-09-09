"""CAS mechanics for request-owned temporary bytes; shared backend lifetime stays outside."""

from dataclasses import dataclass
from hashlib import sha256

from app.infrastructure.errors import Conflict
from app.infrastructure.object_storage.base import StorageBackend, StorageError, WriteCondition
from app.infrastructure.object_storage.utils import normalize_storage_key

MAX_BYTES = 4 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class TempStoredFile:
    revision: str
    byte_size: int
    sha256: str


def _key(value: str) -> str:
    if not value.startswith("a2a-temporary/") or normalize_storage_key(value) != value or len(value.encode()) > 1024:
        raise StorageError("Temporary storage key is invalid")
    return value


class TempFileStorage:
    def __init__(self, backend: StorageBackend) -> None:
        self._backend = backend

    def guard(self, key: str):
        return self._backend.resource_lock("a2a-temp-publication/" + _key(key))

    async def write(self, key: str, content: bytes, *, expected_revision: str | None) -> TempStoredFile:
        if len(content) > MAX_BYTES:
            raise StorageError("Temporary file exceeds four MiB")
        result = await self._backend.write_bytes_if_match(_key(key), content,
            condition=WriteCondition(require_absent=expected_revision is None, version_token=expected_revision))
        if result.ok and result.current_version is not None:
            version = result.current_version
            if not version.exists or version.is_dir or version.size != len(content) or not version.token:
                raise StorageError("Temporary write returned invalid metadata")
            return TempStoredFile(version.token, len(content), sha256(content).hexdigest())
        if result.conflict:
            existing, stored = await self.read(key)
            if existing == content:
                return stored
            raise Conflict("Temporary file storage revision changed")
        raise StorageError("Temporary write was not confirmed")

    async def read(self, key: str) -> tuple[bytes, TempStoredFile]:
        try:
            content, version = await self._backend.read_versioned(_key(key), max_bytes=MAX_BYTES)
        except ValueError:
            raise StorageError("Temporary file exceeds its read bound") from None
        if len(content) > MAX_BYTES or not version.exists or version.is_dir or version.size != len(content) or not version.token:
            raise StorageError("Temporary file storage metadata is invalid")
        return content, TempStoredFile(version.token, len(content), sha256(content).hexdigest())

    async def inspect(self, key: str) -> TempStoredFile | None:
        try:
            _, value = await self.read(key)
        except FileNotFoundError:
            return None
        return value

    async def delete(self, key: str, *, revision: str) -> bool:
        if not revision:
            raise StorageError("Temporary deletion requires its observed revision")
        result = await self._backend.delete_if_match(_key(key), condition=WriteCondition(version_token=revision))
        if not result.ok and not result.conflict:
            raise StorageError("Temporary deletion was not confirmed")
        return result.ok
