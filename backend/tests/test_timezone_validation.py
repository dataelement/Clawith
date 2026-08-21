"""Timezone defaults and write-boundary validation."""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.api import agents as agents_api
from app.api.tenants import TenantOut, TenantUpdate
from app.models.tenant import Tenant
from app.schemas.schemas import AgentUpdate


def test_tenant_timezone_defaults_to_beijing() -> None:
    assert Tenant.__table__.c.timezone.default.arg == "Asia/Shanghai"
    assert TenantOut.model_fields["timezone"].default == "Asia/Shanghai"


@pytest.mark.parametrize("timezone_name", ["Asia/Shanghai", "America/New_York"])
def test_tenant_update_accepts_iana_timezone(timezone_name: str) -> None:
    assert TenantUpdate(timezone=timezone_name).timezone == timezone_name


@pytest.mark.parametrize("timezone_name", [None, "", "UTC+8", "Invalid/Timezone"])
def test_tenant_update_rejects_missing_or_invalid_timezone(
    timezone_name: str | None,
) -> None:
    with pytest.raises(ValidationError):
        TenantUpdate(timezone=timezone_name)


def test_tenant_update_allows_timezone_to_be_omitted() -> None:
    update = TenantUpdate(name="Renamed")

    assert "timezone" not in update.model_dump(exclude_unset=True)


@pytest.mark.parametrize("timezone_name", [None, "Asia/Shanghai", "America/New_York"])
def test_agent_update_accepts_inheritance_or_iana_timezone(
    timezone_name: str | None,
) -> None:
    assert AgentUpdate(timezone=timezone_name).timezone == timezone_name


@pytest.mark.parametrize("timezone_name", ["", "UTC+8", "Invalid/Timezone"])
def test_agent_update_rejects_invalid_timezone(timezone_name: str) -> None:
    with pytest.raises(ValidationError):
        AgentUpdate(timezone=timezone_name)


@pytest.mark.asyncio
async def test_agent_detail_uses_platform_timezone_when_agent_and_tenant_missing(
    monkeypatch,
) -> None:
    agent = SimpleNamespace(
        id=uuid.uuid4(),
        creator_id=None,
        tenant_id=None,
        timezone=None,
    )

    async def fake_check_agent_access(*_args, **_kwargs):
        return agent, "manage"

    async def fake_lazy_reset(*_args, **_kwargs):
        return False

    async def fake_agent_to_out(*_args, **_kwargs):
        return SimpleNamespace(model_dump=lambda: {})

    monkeypatch.setattr(
        agents_api,
        "check_agent_access",
        fake_check_agent_access,
    )
    monkeypatch.setattr(
        agents_api,
        "_lazy_reset_token_counters",
        fake_lazy_reset,
    )
    monkeypatch.setattr(agents_api, "_agent_to_out", fake_agent_to_out)

    result = await agents_api.get_agent(
        agent.id,
        current_user=SimpleNamespace(id=uuid.uuid4()),
        db=SimpleNamespace(),
    )

    assert result["effective_timezone"] == "Asia/Shanghai"
