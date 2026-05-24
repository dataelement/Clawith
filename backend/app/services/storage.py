"""Compatibility facade for storage services.

New code should prefer the `app.services.storage_runtime` package.
This module remains as the stable import path for existing callers.
"""

from app.services.storage_runtime import (
    LocalStorageBackend,
    S3StorageBackend,
    StorageBackend,
    StorageEntry,
    agent_storage_key,
    agent_storage_prefix,
    agent_upload_key,
    agent_workspace_key,
    ensure_local_path,
    get_storage_backend,
    guess_content_type,
    normalize_storage_key,
    sanitize_filename,
    store_agent_bytes,
    store_agent_upload,
    tenant_storage_key,
    tenant_storage_prefix,
)

__all__ = [
    "LocalStorageBackend",
    "S3StorageBackend",
    "StorageBackend",
    "StorageEntry",
    "agent_storage_key",
    "agent_storage_prefix",
    "agent_upload_key",
    "agent_workspace_key",
    "ensure_local_path",
    "get_storage_backend",
    "guess_content_type",
    "normalize_storage_key",
    "sanitize_filename",
    "store_agent_bytes",
    "store_agent_upload",
    "tenant_storage_key",
    "tenant_storage_prefix",
]
