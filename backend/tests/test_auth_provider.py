from unittest.mock import AsyncMock, patch

import pytest

from app.services.auth_provider import FeishuAuthProvider


class _DummyResponse:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


class _DummyAsyncClient:
    def __init__(self, responses):
        self._responses = list(responses)

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def get(self, *args, **kwargs):
        return self._responses.pop(0)


@pytest.mark.asyncio
async def test_feishu_auth_provider_prefers_contact_user_id_over_open_id():
    provider = FeishuAuthProvider(config={"app_id": "app-id", "app_secret": "app-secret"})

    responses = [
        _DummyResponse(
            {
                "data": {
                    "open_id": "ou_open_123",
                    "union_id": "on_union_456",
                    "name": "Alice",
                }
            }
        ),
        _DummyResponse(
            {
                "code": 0,
                "data": {
                    "user": {
                        "user_id": "u_emp_789",
                        "email": "alice@example.com",
                        "mobile": "13800000000",
                    }
                },
            }
        ),
    ]

    with patch("app.services.auth_provider.httpx.AsyncClient", return_value=_DummyAsyncClient(responses)):
        with patch.object(provider, "get_app_access_token", AsyncMock(return_value="app-token")):
            user_info = await provider.get_user_info("user-token")

    assert user_info.provider_user_id == "u_emp_789"
    assert user_info.provider_union_id == "on_union_456"
    assert user_info.email == "alice@example.com"
    assert user_info.mobile == "13800000000"
    assert user_info.raw_data["user_id"] == "u_emp_789"
