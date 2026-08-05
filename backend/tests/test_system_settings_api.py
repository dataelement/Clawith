"""Authorization regression coverage for platform system settings."""

import uuid
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI

from app.api.enterprise import router
from app.core.security import get_current_user
from app.database import get_db


app = FastAPI()
app.include_router(router, prefix="/api")


class _Result:
    def scalar_one_or_none(self):
        return None


class _Session:
    async def execute(self, _statement: object) -> _Result:
        return _Result()


async def _get_db():
    yield _Session()


@pytest.fixture
def client():
    transport = httpx.ASGITransport(app=app)

    async def _build():
        return httpx.AsyncClient(transport=transport, base_url="http://test")

    return _build


@pytest.fixture(autouse=True)
def clear_dependency_overrides():
    yield
    app.dependency_overrides.clear()


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["get", "put"])
async def test_org_admin_cannot_read_or_modify_platform_system_settings(client, method: str) -> None:
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(
        id=uuid.uuid4(),
        role="org_admin",
        identity=None,
        tenant_id=uuid.uuid4(),
        is_active=True,
    )
    app.dependency_overrides[get_db] = _get_db

    async with await client() as ac:
        if method == "get":
            response = await ac.get("/api/enterprise/system-settings/system_email_platform")
        else:
            response = await ac.put(
                "/api/enterprise/system-settings/system_email_platform",
                json={"value": {"SYSTEM_SMTP_PASSWORD": "attacker-value"}},
            )

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_member_cannot_read_platform_system_settings(client) -> None:
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(
        id=uuid.uuid4(),
        role="member",
        identity=None,
        tenant_id=uuid.uuid4(),
        is_active=True,
    )
    app.dependency_overrides[get_db] = _get_db

    async with await client() as ac:
        response = await ac.get("/api/enterprise/system-settings/jina_api_key")

    assert response.status_code == 403
