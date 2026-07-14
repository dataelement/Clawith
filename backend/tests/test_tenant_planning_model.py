"""Tenant Planning model configuration API tests."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
import uuid

from fastapi import HTTPException
import pytest

from app.api import tenants as tenants_api
from app.models.llm import LLMModel
from app.models.tenant import Tenant


class _Result:
    def __init__(self, value: object) -> None:
        self.value = value

    def scalar_one_or_none(self) -> object:
        return self.value


class _Session:
    def __init__(self, value: object = None) -> None:
        self.value = value
        self.execute_calls = 0
        self.flushes = 0

    async def execute(self, _statement: object) -> _Result:
        self.execute_calls += 1
        return _Result(self.value)

    async def flush(self) -> None:
        self.flushes += 1


def _tenant() -> Tenant:
    return Tenant(
        id=uuid.uuid4(),
        name="Acme",
        slug="acme",
        im_provider="web_only",
        timezone="UTC",
        country_region="001",
        is_active=True,
        sso_enabled=False,
        a2a_async_enabled=True,
        default_model_id=None,
        planning_model_id=None,
    )


def _model(*, tenant_id: uuid.UUID, enabled: bool = True) -> LLMModel:
    return LLMModel(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        provider="openrouter",
        model="planning-model",
        api_key_encrypted="encrypted",
        label="Planning model",
        enabled=enabled,
    )


@pytest.mark.asyncio
async def test_update_planning_model_pins_enabled_tenant_model() -> None:
    tenant = _tenant()
    model = _model(tenant_id=tenant.id)
    db = _Session(model)
    user = SimpleNamespace(role="org_admin", tenant_id=tenant.id)

    with patch.object(
        tenants_api,
        "_get_updateable_tenant",
        new=AsyncMock(return_value=tenant),
    ):
        result = await tenants_api.update_tenant_planning_model(
            tenant.id,
            tenants_api.TenantPlanningModelUpdate(model_id=model.id),
            user,
            db,  # type: ignore[arg-type]
        )

    assert tenant.planning_model_id == model.id
    assert result.planning_model_id == model.id
    assert db.flushes == 1


@pytest.mark.asyncio
async def test_update_planning_model_rejects_cross_tenant_model() -> None:
    tenant = _tenant()
    model = _model(tenant_id=uuid.uuid4())
    db = _Session(model)

    with (
        patch.object(
            tenants_api,
            "_get_updateable_tenant",
            new=AsyncMock(return_value=tenant),
        ),
        pytest.raises(HTTPException, match="selected company") as exc_info,
    ):
        await tenants_api.update_tenant_planning_model(
            tenant.id,
            tenants_api.TenantPlanningModelUpdate(model_id=model.id),
            SimpleNamespace(role="platform_admin", tenant_id=None),
            db,  # type: ignore[arg-type]
        )

    assert exc_info.value.status_code == 400
    assert tenant.planning_model_id is None
    assert db.flushes == 0


@pytest.mark.asyncio
async def test_clear_planning_model_does_not_query_model_table() -> None:
    tenant = _tenant()
    tenant.planning_model_id = uuid.uuid4()
    db = _Session()

    with patch.object(
        tenants_api,
        "_get_updateable_tenant",
        new=AsyncMock(return_value=tenant),
    ):
        result = await tenants_api.update_tenant_planning_model(
            tenant.id,
            tenants_api.TenantPlanningModelUpdate(model_id=None),
            SimpleNamespace(role="org_admin", tenant_id=tenant.id),
            db,  # type: ignore[arg-type]
        )

    assert result.planning_model_id is None
    assert db.execute_calls == 0
    assert db.flushes == 1
