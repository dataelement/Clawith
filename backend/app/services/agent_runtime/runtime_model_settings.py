"""Database-backed platform model choices for shared multi-Agent Runtime work."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.llm import LLMModel
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
    requested_ids = {
        model_id
        for model_id in (
            configured_planning,
            configured_compact,
            environment_planning_model_id,
            environment_compact_model_id,
        )
        if model_id is not None
    }
    eligible_ids: set[uuid.UUID] = set()
    if requested_ids:
        eligible_result = await db.execute(
            select(LLMModel.id).where(
                LLMModel.id.in_(requested_ids),
                or_(LLMModel.tenant_id.is_(None), LLMModel.tenant_id == tenant_id),
                LLMModel.enabled.is_(True),
                LLMModel.deleted_at.is_(None),
            )
        )
        eligible_ids = {
            value.id if isinstance(value, LLMModel) else value
            for value in eligible_result.scalars().all()
        }

    def resolve_one(
        configured_id: uuid.UUID | None,
        environment_id: uuid.UUID | None,
    ) -> tuple[uuid.UUID | None, str]:
        if configured_id in eligible_ids:
            return configured_id, "database"
        if environment_id in eligible_ids:
            return environment_id, "environment"
        return None, "unavailable"

    planning_id, planning_source = resolve_one(
        configured_planning,
        environment_planning_model_id,
    )
    compact_id, compact_source = resolve_one(
        configured_compact,
        environment_compact_model_id,
    )
    return RuntimeModelSettings(
        planning_model_id=planning_id,
        compact_model_id=compact_id,
        planning_source=planning_source,
        compact_source=compact_source,
    )
