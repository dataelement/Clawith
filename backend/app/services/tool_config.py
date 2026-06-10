"""Tool configuration helpers.

Builtin tools are global capability records, so tenant/company configuration
must not live in ``tools.config`` for those rows. Tenant-specific values are
stored in ``tenant_settings`` under ``tool_config:<tool_name>``.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core.security import decrypt_data, encrypt_data
from app.models.tenant_setting import TenantSetting
from app.models.tool import Tool


SENSITIVE_FIELD_KEYS = {"api_key", "private_key", "auth_code", "password", "secret"}
TENANT_TOOL_CONFIG_PREFIX = "tool_config:"


def tenant_tool_config_key(tool_name: str) -> str:
    return f"{TENANT_TOOL_CONFIG_PREFIX}{tool_name}"


def get_sensitive_keys(config_schema: dict | None = None) -> set[str]:
    keys = set(SENSITIVE_FIELD_KEYS)
    if config_schema:
        for field in config_schema.get("fields", []):
            if field.get("type") == "password":
                keys.add(field.get("key", ""))
    keys.discard("")
    return keys


def encrypt_sensitive_fields(config: dict, config_schema: dict | None = None) -> dict:
    if not config:
        return config

    settings = get_settings()
    result = dict(config)
    for key in get_sensitive_keys(config_schema):
        value = result.get(key)
        if not isinstance(value, str) or not value:
            continue
        try:
            decrypt_data(value, settings.SECRET_KEY)
            continue
        except Exception:
            pass
        try:
            result[key] = encrypt_data(value, settings.SECRET_KEY)
        except Exception:
            pass
    return result


def decrypt_sensitive_fields(config: dict, config_schema: dict | None = None) -> dict:
    if not config:
        return config

    settings = get_settings()
    result = dict(config)
    for key in get_sensitive_keys(config_schema):
        value = result.get(key)
        if not isinstance(value, str) or not value:
            continue
        try:
            result[key] = decrypt_data(value, settings.SECRET_KEY)
        except Exception:
            pass
    return result


def meaningful_config(config: dict | None) -> dict:
    """Drop empty form values while preserving booleans/numbers."""
    if not config:
        return {}
    cleaned: dict[str, Any] = {}
    for key, value in config.items():
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        cleaned[key] = value
    return cleaned


async def get_tenant_tool_config(
    db: AsyncSession,
    tenant_id: uuid.UUID | None,
    tool_name: str,
    config_schema: dict | None = None,
) -> dict:
    if not tenant_id:
        return {}
    result = await db.execute(
        select(TenantSetting).where(
            TenantSetting.tenant_id == tenant_id,
            TenantSetting.key == tenant_tool_config_key(tool_name),
        )
    )
    setting = result.scalar_one_or_none()
    raw = (setting.value or {}).get("config", {}) if setting else {}
    return decrypt_sensitive_fields(raw, config_schema)


async def set_tenant_tool_config(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    tool_name: str,
    config: dict,
    config_schema: dict | None = None,
) -> None:
    encrypted = encrypt_sensitive_fields(meaningful_config(config), config_schema)
    key = tenant_tool_config_key(tool_name)
    result = await db.execute(
        select(TenantSetting).where(
            TenantSetting.tenant_id == tenant_id,
            TenantSetting.key == key,
        )
    )
    existing = result.scalar_one_or_none()
    if existing:
        existing.value = {"config": encrypted}
    else:
        db.add(TenantSetting(tenant_id=tenant_id, key=key, value={"config": encrypted}))


async def delete_tenant_tool_config(db: AsyncSession, tenant_id: uuid.UUID, tool_name: str) -> None:
    result = await db.execute(
        select(TenantSetting).where(
            TenantSetting.tenant_id == tenant_id,
            TenantSetting.key == tenant_tool_config_key(tool_name),
        )
    )
    existing = result.scalar_one_or_none()
    if existing:
        await db.delete(existing)


async def get_tool_company_config(db: AsyncSession, tool: Tool, tenant_id: uuid.UUID | None) -> dict:
    """Return company config for a tool without leaking builtin config across tenants."""
    if tool.source == "builtin":
        return await get_tenant_tool_config(db, tenant_id, tool.name, tool.config_schema)
    return decrypt_sensitive_fields(tool.config or {}, tool.config_schema)


def mask_sensitive_fields(config: dict, config_schema: dict | None = None) -> dict:
    masked = dict(config or {})
    for key in get_sensitive_keys(config_schema):
        value = masked.get(key)
        if value and isinstance(value, str):
            suffix = value[-4:] if len(value) > 4 else value
            masked[key] = f"****{suffix}"
    return masked
