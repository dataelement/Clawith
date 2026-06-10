"""Agent-scoped storage helpers.

This module centralizes how agent and tenant workspace keys are built so
channel handlers and background services do not manually assemble
`workspace/uploads/...` paths all over the codebase.
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path

from app.services.storage_runtime.facade import (
    ensure_local_path,
    get_storage_backend,
    guess_content_type,
    normalize_storage_key,
)


def sanitize_filename(filename: str, fallback: str = "file.bin") -> str:
    name = (filename or "").replace("\\", "_").replace("/", "_").strip()
    return name or fallback


def agent_storage_key(agent_id: uuid.UUID | str, rel_path: str = "") -> str:
    prefix = str(agent_id)
    rel = normalize_storage_key(rel_path)
    return f"{prefix}/{rel}" if rel else prefix


def agent_workspace_key(agent_id: uuid.UUID | str, rel_path: str = "") -> str:
    rel = normalize_storage_key(rel_path)
    workspace_rel = f"workspace/{rel}" if rel else "workspace"
    return agent_storage_key(agent_id, workspace_rel)


def agent_upload_key(agent_id: uuid.UUID | str, filename: str) -> str:
    safe_name = sanitize_filename(filename)
    return agent_workspace_key(agent_id, f"uploads/{safe_name}")


def tenant_storage_key(tenant_id: uuid.UUID | str, rel_path: str = "") -> str:
    prefix = normalize_storage_key(f"enterprise_info_{tenant_id}")
    rel = normalize_storage_key(rel_path)
    return f"{prefix}/{rel}" if rel else prefix


async def store_agent_bytes(
    agent_id: uuid.UUID | str,
    rel_path: str,
    data: bytes,
    *,
    content_type: str | None = None,
) -> str:
    key = agent_storage_key(agent_id, rel_path)
    storage = get_storage_backend()
    await storage.write_bytes(
        key,
        data,
        content_type=content_type or guess_content_type(Path(rel_path).name),
    )
    return key


async def store_agent_upload(
    agent_id: uuid.UUID | str,
    filename: str,
    data: bytes,
    *,
    content_type: str | None = None,
) -> tuple[str, str, Path]:
    key = agent_upload_key(agent_id, filename)
    storage = get_storage_backend()
    safe_name = os.path.basename(key)
    await storage.write_bytes(
        key,
        data,
        content_type=content_type or guess_content_type(safe_name),
    )
    local_path = await ensure_local_path(key)
    workspace_path = f"workspace/uploads/{safe_name}"
    return key, workspace_path, local_path
