"""Database-backed platform model choices for shared multi-Agent Runtime work."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.system_settings import SystemSetting


RUNTIME_MODEL_SETTING_KEY = "multi_agent_runtime_models"


def runtime_model_setting_key(tenant_id: uuid.UUID) -> str:
    return f"{RUNTIME_MODEL_SETTING_KEY}:{tenant_id}"


@dataclass(frozen=True, slots=True)
class RuntimeModelSettings:
    planning_model_id: uuid.UUID | None
    compact_model_id: uuid.UUID | None
    planning_source: str
    compact_source: str


def _configured_uuid(value: object, *, setting_name: str) -> uuid.UUID | None:
    if value is None or value == "":
        return None
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError, AttributeError) as exc:
        raise ValueError(f"{setting_name} is not a valid model UUID") from exc


async def resolve_runtime_model_settings(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    environment_planning_model_id: uuid.UUID | None,
    environment_compact_model_id: uuid.UUID | None,
) -> RuntimeModelSettings:
    """Prefer persisted admin choices and retain environment values as fallback."""
    result = await db.execute(
        select(SystemSetting).where(
            SystemSetting.key.in_(
                (runtime_model_setting_key(tenant_id), RUNTIME_MODEL_SETTING_KEY)
            )
        )
    )
    settings_by_key = {setting.key: setting for setting in result.scalars().all()}
    setting = settings_by_key.get(runtime_model_setting_key(tenant_id))
    if setting is None:
        # The legacy global row could only contain validated platform models,
        # so it is a safe compatibility bridge until each tenant saves once.
        setting = settings_by_key.get(RUNTIME_MODEL_SETTING_KEY)
    value = setting.value if isinstance(getattr(setting, "value", None), dict) else {}

    configured_planning = _configured_uuid(
        value.get("planning_model_id"),
        setting_name="planning_model_id",
    )
    configured_compact = _configured_uuid(
        value.get("compact_model_id"),
        setting_name="compact_model_id",
    )
    return RuntimeModelSettings(
        planning_model_id=configured_planning or environment_planning_model_id,
        compact_model_id=configured_compact or environment_compact_model_id,
        planning_source="database" if configured_planning else "environment",
        compact_source="database" if configured_compact else "environment",
    )
