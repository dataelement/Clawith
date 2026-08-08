"""Runtime model settings must never resolve stale or unrunnable model IDs."""

from types import SimpleNamespace
import uuid

import pytest

from app.services.agent_runtime.runtime_model_settings import (
    resolve_runtime_model_settings,
    runtime_model_setting_key,
)


class _Result:
    def __init__(self, values: list[object]) -> None:
        self._values = values

    def scalars(self):
        return self

    def all(self) -> list[object]:
        return self._values


class _Session:
    def __init__(self, *results: _Result) -> None:
        self._results = iter(results)
        self.statements = []

    async def execute(self, statement):
        self.statements.append(statement)
        return next(self._results)


@pytest.mark.asyncio
async def test_deleted_configured_model_falls_back_only_to_eligible_environment_model() -> None:
    tenant_id = uuid.uuid4()
    deleted_id = uuid.uuid4()
    environment_id = uuid.uuid4()
    setting = SimpleNamespace(
        key=runtime_model_setting_key(tenant_id),
        value={
            "planning_model_id": str(deleted_id),
            "compact_model_id": str(deleted_id),
        },
    )
    db = _Session(
        _Result([setting]),
        _Result([environment_id]),
    )

    resolved = await resolve_runtime_model_settings(
        db,  # type: ignore[arg-type]
        tenant_id=tenant_id,
        environment_planning_model_id=environment_id,
        environment_compact_model_id=None,
    )

    assert resolved.planning_model_id == environment_id
    assert resolved.planning_source == "environment"
    assert resolved.compact_model_id is None
    assert resolved.compact_source == "unavailable"
    assert "supports_tool_calling" not in str(db.statements[1])


@pytest.mark.asyncio
async def test_no_configured_models_requires_no_model_lookup() -> None:
    tenant_id = uuid.uuid4()
    db = _Session(_Result([]))

    resolved = await resolve_runtime_model_settings(
        db,  # type: ignore[arg-type]
        tenant_id=tenant_id,
        environment_planning_model_id=None,
        environment_compact_model_id=None,
    )

    assert resolved.planning_model_id is None
    assert resolved.compact_model_id is None
    assert resolved.planning_source == "unavailable"
    assert resolved.compact_source == "unavailable"
