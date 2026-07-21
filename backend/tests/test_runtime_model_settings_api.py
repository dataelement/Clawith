"""Authorization and model-scope checks for shared Runtime model settings."""

import uuid
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.api.enterprise import (
    RuntimeModelSettingsUpdate,
    get_runtime_model_settings,
    update_runtime_model_settings,
)
from app.models.llm import LLMModel


class _ModelResult:
    def __init__(self, models: list[LLMModel]) -> None:
        self.models = models

    def scalars(self) -> "_ModelResult":
        return self

    def all(self) -> list[LLMModel]:
        return self.models


class _ModelSession:
    def __init__(self, models: list[LLMModel]) -> None:
        self.models = models
        self.execute_count = 0

    async def execute(self, _statement: object) -> _ModelResult:
        self.execute_count += 1
        return _ModelResult(self.models)


def _model(model_id: uuid.UUID, **overrides: object) -> LLMModel:
    values: dict[str, object] = {
        "id": model_id,
        "provider": "test",
        "model": "runtime-model",
        "label": "Runtime model",
        "api_key_encrypted": "secret",
        "enabled": True,
        "supports_tool_calling": True,
        "tenant_id": None,
    }
    values.update(overrides)
    return LLMModel(**values)


@pytest.mark.asyncio
async def test_runtime_model_settings_are_company_admin_only() -> None:
    session = _ModelSession([])
    tenant_id = uuid.uuid4()

    with pytest.raises(HTTPException) as exc_info:
        await get_runtime_model_settings(
            tenant_id=str(tenant_id),
            current_user=SimpleNamespace(
                role="agent_admin",
                identity=None,
                tenant_id=tenant_id,
            ),  # type: ignore[arg-type]
            db=session,  # type: ignore[arg-type]
        )

    assert exc_info.value.status_code == 403
    assert session.execute_count == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "overrides",
    [
        {"tenant_id": uuid.uuid4()},
        {"enabled": False},
        {"supports_tool_calling": False},
    ],
)
async def test_runtime_model_settings_reject_ineligible_models(
    overrides: dict[str, object],
) -> None:
    model_id = uuid.uuid4()
    tenant_id = uuid.uuid4()
    session = _ModelSession([_model(model_id, **overrides)])

    with pytest.raises(HTTPException) as exc_info:
        await update_runtime_model_settings(
            RuntimeModelSettingsUpdate(
                planning_model_id=model_id,
                compact_model_id=model_id,
            ),
            tenant_id=str(tenant_id),
            current_user=SimpleNamespace(
                role="platform_admin",
                identity=None,
                tenant_id=tenant_id,
            ),  # type: ignore[arg-type]
            db=session,  # type: ignore[arg-type]
        )

    assert exc_info.value.status_code == 422
    assert session.execute_count == 1
