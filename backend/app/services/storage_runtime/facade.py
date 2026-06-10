"""Facade for selecting the configured storage backend."""

from __future__ import annotations

import mimetypes
from pathlib import Path

from app.config import get_settings
from app.services.storage_runtime.base import StorageBackend
from app.services.storage_runtime.fallback import FallbackStorageBackend
from app.services.storage_runtime.local import LocalStorageBackend
from app.services.storage_runtime.s3 import S3StorageBackend
from app.services.storage_runtime.utils import (
    agent_storage_prefix,
    normalize_storage_key,
    tenant_storage_prefix,
)

_storage_backend: StorageBackend | None = None


def get_storage_backend() -> StorageBackend:
    global _storage_backend
    if _storage_backend is not None:
        return _storage_backend

    settings = get_settings()
    backend = (settings.STORAGE_BACKEND or "local").strip().lower()
    if backend == "s3":
        primary = S3StorageBackend(
            bucket=settings.S3_BUCKET,
            prefix=settings.S3_PREFIX,
            region=settings.S3_REGION,
            endpoint_url=settings.S3_ENDPOINT_URL,
            access_key_id=settings.S3_ACCESS_KEY_ID,
            secret_access_key=settings.S3_SECRET_ACCESS_KEY,
            presign_ttl_seconds=settings.S3_PRESIGN_TTL_SECONDS,
            max_pool_connections=settings.S3_MAX_POOL_CONNECTIONS,
            write_workers=settings.S3_WRITE_WORKERS,
        )
        if settings.STORAGE_LOCAL_FALLBACK_ENABLED:
            fallback = LocalStorageBackend(settings.STORAGE_LOCAL_ROOT or settings.AGENT_DATA_DIR)
            _storage_backend = FallbackStorageBackend(primary=primary, fallback=fallback)
        else:
            _storage_backend = primary
    else:
        _storage_backend = LocalStorageBackend(settings.STORAGE_LOCAL_ROOT or settings.AGENT_DATA_DIR)
    return _storage_backend


async def ensure_local_path(key: str) -> Path:
    backend = get_storage_backend()
    path = await backend.local_path_for(key)
    if path is None:
        raise RuntimeError("Storage backend cannot materialize a local path")
    return path


def guess_content_type(filename: str) -> str:
    return mimetypes.guess_type(filename)[0] or "application/octet-stream"
