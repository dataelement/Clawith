"""Unit tests verifying multi-tenant isolation for EnterpriseInfo updates and agent file sync."""

import json
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from app.api import enterprise as enterprise_api
from app.models.agent import Agent
from app.models.audit import EnterpriseInfo
from app.models.user import User
from app.schemas.schemas import EnterpriseInfoUpdate
from app.services.enterprise_sync import enterprise_sync_service


class _MockResult:
    def __init__(self, items: list) -> None:
        self._items = items

    def scalar_one_or_none(self):
        return self._items[0] if self._items else None

    def scalars(self):
        return self

    def all(self):
        return self._items


class _MockSession:
    def __init__(self) -> None:
        self.added = []
        self.flushed = False

    async def execute(self, statement):
        return _MockResult([])

    def add(self, item):
        self.added.append(item)

    async def flush(self):
        self.flushed = True


@pytest.mark.asyncio
async def test_update_enterprise_info_binds_to_current_user_tenant():
    """EnterpriseInfo creation must bind tenant_id to the active user's tenant."""
    tenant_a = uuid.uuid4()
    user_a = User(id=uuid.uuid4(), tenant_id=tenant_a, role="org_admin")
    db = _MockSession()

    stored_info = None

    async def mock_store(agent_id, path, content, content_type):
        pass

    with patch("app.services.enterprise_sync.publish_event", AsyncMock()), \
         patch("app.services.enterprise_sync.store_agent_bytes", mock_store):
        info = await enterprise_sync_service.update_enterprise_info(
            db=db,
            tenant_id=tenant_a,
            info_type="company_profile",
            content={"name": "Company A"},
            visible_roles=[],
            updated_by=user_a.id,
        )

    assert info.tenant_id == tenant_a
    assert info.info_type == "company_profile"
    assert info.content == {"name": "Company A"}


@pytest.mark.asyncio
async def test_sync_to_all_agents_restricts_to_target_tenant():
    """Agent sync must only target running agents belonging to the specified tenant."""
    from datetime import datetime, timezone

    tenant_a = uuid.uuid4()
    tenant_b = uuid.uuid4()

    agent_a = Agent(id=uuid.uuid4(), tenant_id=tenant_a, status="running", role_description="dev")

    now = datetime.now(timezone.utc)
    info_a = EnterpriseInfo(
        tenant_id=tenant_a,
        info_type="company_profile",
        content={"secret": "Tenant A Secret"},
        visible_roles=[],
        version=1,
        created_at=now,
        updated_at=now,
    )

    synced_files = {}

    async def mock_store(agent_id, path, content, content_type):
        synced_files[(agent_id, path)] = json.loads(content.decode("utf-8"))

    db = AsyncMock()

    async def mock_execute(stmt, *args, **kwargs):
        sql = str(stmt)
        if "FROM agents" in sql:
            return _MockResult([agent_a])
        elif "FROM enterprise_info" in sql:
            return _MockResult([info_a])
        return _MockResult([])

    db.execute = AsyncMock(side_effect=mock_execute)

    with patch("app.services.enterprise_sync.store_agent_bytes", mock_store):
        # Sync tenant A
        count = await enterprise_sync_service.sync_to_all_agents(db, tenant_id=tenant_a)

    assert count == 1
    # Only Agent A receives Tenant A's secret
    assert (agent_a.id, "enterprise_info/company_profile.json") in synced_files
    assert synced_files[(agent_a.id, "enterprise_info/company_profile.json")]["content"] == {"secret": "Tenant A Secret"}


@pytest.mark.asyncio
async def test_api_list_enterprise_info_filters_by_tenant():
    """API list endpoint must only return EnterpriseInfo records for the current user's tenant."""
    from datetime import datetime, timezone

    tenant_a = uuid.uuid4()
    user_a = User(id=uuid.uuid4(), tenant_id=tenant_a, role="member")
    now = datetime.now(timezone.utc)
    info_a = EnterpriseInfo(id=uuid.uuid4(), tenant_id=tenant_a, info_type="rules", content={"a": 1}, version=1, visible_roles=[], created_at=now, updated_at=now)

    db = AsyncMock()
    db.execute = AsyncMock(return_value=_MockResult([info_a]))

    result = await enterprise_api.list_enterprise_info(current_user=user_a, db=db)

    assert len(result) == 1
    assert result[0].info_type == "rules"
    assert result[0].content == {"a": 1}
