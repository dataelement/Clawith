import uuid
import pytest
from unittest.mock import patch, MagicMock
from pathlib import Path

from app.services.agent_tools import (
    _get_vercel_token,
    _check_neon_quota_limit,
    _vercel_deploy,
    _vercel_get_deploy_logs,
    _vercel_list_deployments,
    _vercel_set_env,
    _vercel_manage_domain,
    _neon_create_database,
)

@pytest.mark.asyncio
@patch("app.services.agent_tools._get_tool_config")
async def test_get_vercel_token(mock_get_config):
    agent_id = uuid.uuid4()
    mock_get_config.return_value = {"vercel_token": "shared-token"}

    token = await _get_vercel_token(agent_id, "vercel_list_deployments")

    assert token == "shared-token"
    mock_get_config.assert_awaited_once_with(agent_id, "vercel_deploy")


@pytest.mark.asyncio
@patch("httpx.AsyncClient.get")
async def test_check_neon_quota_limit(mock_get):
    # Case 1: Quota reached (1 project)
    mock_get.return_value = MagicMock(
        status_code=200,
        json=lambda: {"projects": [{"id": "proj_1", "name": "my-existing-db"}]}
    )
    is_blocked, msg = await _check_neon_quota_limit("test-key")
    assert is_blocked is True
    assert "Neon 免费额度已达上限" in msg

    # Case 2: Quota not reached (0 projects)
    mock_get.return_value = MagicMock(
        status_code=200,
        json=lambda: {"projects": []}
    )
    is_blocked, msg = await _check_neon_quota_limit("test-key")
    assert is_blocked is False
    assert "0/1" in msg


@pytest.mark.asyncio
@patch("app.services.agent_tools._get_vercel_token")
@patch("httpx.AsyncClient.patch")
@patch("httpx.AsyncClient.post")
@patch("httpx.AsyncClient.get")
async def test_vercel_deploy_github(mock_get, mock_post, mock_patch, mock_get_token):
    mock_get_token.return_value = "fake-token"

    # Mock project protection patch
    mock_patch.return_value = MagicMock(status_code=200, json=lambda: {})

    # Mock exact project-link and accepted-deployment receipts.
    mock_post.side_effect = [
        MagicMock(
            status_code=200,
            json=lambda: {"type": "github", "repo": "owner/repo"},
        ),
        MagicMock(
            status_code=200,
            json=lambda: {
                "id": "dep_123",
                "url": "test.vercel.app",
                "readyState": "QUEUED",
            },
        ),
    ]

    # Mock polling status to return READY immediately
    mock_get.side_effect = [
        MagicMock(status_code=200, json=lambda: {"id": "proj_123", "name": "my-project"}), # Project check GET
        MagicMock(
            status_code=200,
            json=lambda: {
                "id": "dep_123",
                "readyState": "READY",
                "url": "test.vercel.app",
            },
        ),
    ]

    result = await _vercel_deploy(
        agent_id=uuid.uuid4(),
        ws=Path("/tmp"),
        arguments={
            "project_name": "my-project",
            "deploy_method": "github",
            "github_repo": "owner/repo",
            "production": True
        }
    )
    assert "Vercel deployment dep_123 is READY" in result
    assert "test.vercel.app" in result
    mock_patch.assert_not_awaited()


@pytest.mark.asyncio
@patch("app.services.agent_tools._get_vercel_token")
@patch("httpx.AsyncClient.get")
async def test_vercel_list_deployments_legacy_happy_path(
    mock_get,
    mock_get_token,
):
    mock_get_token.return_value = "fake-token"
    mock_get.return_value = MagicMock(
        status_code=200,
        json=lambda: {
            "deployments": [
                {
                    "uid": "dpl_legacy",
                    "url": "legacy.vercel.app",
                    "state": "READY",
                    "created": 1_752_620_400_000,
                }
            ]
        },
    )

    result = await _vercel_list_deployments(
        uuid.uuid4(),
        {"project_name": "legacy-project"},
    )

    assert "dpl_legacy" in result
    assert "legacy.vercel.app" in result


@pytest.mark.asyncio
@patch("app.services.agent_tools._get_vercel_token")
@patch("httpx.AsyncClient.get")
async def test_vercel_get_deploy_logs_legacy_happy_path(
    mock_get,
    mock_get_token,
):
    mock_get_token.return_value = "fake-token"
    mock_get.return_value = MagicMock(
        status_code=200,
        json=lambda: [
            {
                "type": "stdout",
                "payload": {"text": "legacy build completed"},
            }
        ],
    )

    result = await _vercel_get_deploy_logs(
        uuid.uuid4(),
        {"deployment_id": "dpl_legacy"},
    )

    assert "legacy build completed" in result


@pytest.mark.asyncio
@patch("app.services.agent_tools._get_vercel_token")
@patch("httpx.AsyncClient.post")
async def test_vercel_set_env(mock_post, mock_get_token):
    mock_get_token.return_value = "fake-token"
    mock_post.return_value = MagicMock(
        status_code=201,
        json=lambda: {"id": "env_123", "key": "DATABASE_URL"},
    )
    
    result = await _vercel_set_env(
        agent_id=uuid.uuid4(),
        arguments={
            "project_name": "my-project",
            "key": "DATABASE_URL",
            "value": "postgres://..."
        }
    )
    assert "was created" in result


@pytest.mark.asyncio
@patch("app.services.agent_tools._get_vercel_token")
@patch("httpx.AsyncClient.post")
@patch("httpx.AsyncClient.get")
@patch("httpx.AsyncClient.patch")
async def test_vercel_set_env_conflict_updates(mock_patch, mock_get, mock_post, mock_get_token):
    mock_get_token.return_value = "fake-token"

    # Only the structured 409 receipt enters reconciliation.
    mock_post.return_value = MagicMock(
        status_code=409,
        json=lambda: {"error": {"code": "ENV_ALREADY_EXISTS"}},
    )
    # Mock list envs to retrieve ID
    mock_get.return_value = MagicMock(status_code=200, json=lambda: {"envs": [{"id": "env_abc", "key": "DATABASE_URL"}]})
    # Mock patch request
    mock_patch.return_value = MagicMock(
        status_code=200,
        json=lambda: {"id": "env_abc", "key": "DATABASE_URL"},
    )

    result = await _vercel_set_env(
        agent_id=uuid.uuid4(),
        arguments={
            "project_name": "my-project",
            "key": "DATABASE_URL",
            "value": "postgres://new-value"
        }
    )
    assert "was updated" in result


@pytest.mark.asyncio
@patch("app.services.agent_tools._get_vercel_token")
@patch("httpx.AsyncClient.get")
async def test_vercel_manage_domain_check(mock_get, mock_get_token):
    mock_get_token.return_value = "fake-token"

    mock_get.return_value = MagicMock(status_code=200, json=lambda: {"available": True, "price": 10, "period": 1})

    result = await _vercel_manage_domain(
        agent_id=uuid.uuid4(),
        arguments={
            "action": "check",
            "domain": "example.com"
        }
    )
    assert "example.com" in result
    assert "is available" in result
    assert "$10" in result
    assert "$10" in result


@pytest.mark.asyncio
@patch("app.services.agent_tools._store_deploy_value_ref")
@patch("app.services.agent_tools._get_tool_config")
@patch("app.services.agent_tools._check_neon_quota_limit")
@patch("httpx.AsyncClient.get")
@patch("httpx.AsyncClient.post")
async def test_neon_create_database_auto_resolve_org_id(
    mock_post,
    mock_get,
    mock_quota,
    mock_get_config,
    mock_store_value,
):
    mock_get_config.return_value = {"neon_api_key": "fake-key"}
    mock_quota.return_value = (False, "")
    mock_store_value.return_value = "deploy-value://tenant/agent/value"
    
    # Mock GET for organizations (returns single org)
    mock_get.return_value = MagicMock(
        status_code=200,
        json=lambda: {"organizations": [{"id": "org-resolved-123", "name": "Test Org"}]}
    )
    
    # Mock POST for project creation
    mock_post.return_value = MagicMock(
        status_code=201,
        json=lambda: {"project": {"id": "proj_123"}, "connection_uri": "postgresql://user:pass@host/neondb"}
    )
    
    result = await _neon_create_database(
        agent_id=uuid.uuid4(),
        arguments={
            "project_name": "my-neon-project",
            "database_name": "neondb",
        }
    )
    assert "proj_123" in result
    assert "private value_ref" in result
    assert "deploy-value://tenant/agent/value" in result
    assert "postgresql://user:pass@host/neondb" not in result
    assert "proj_123" in result


@pytest.mark.asyncio
@patch("app.services.agent_tools._store_deploy_value_ref")
@patch("app.services.agent_tools._get_tool_config")
@patch("app.services.agent_tools._check_neon_quota_limit")
@patch("httpx.AsyncClient.post")
async def test_neon_create_database_with_provided_org_id(
    mock_post,
    mock_quota,
    mock_get_config,
    mock_store_value,
):
    mock_get_config.return_value = {"neon_api_key": "fake-key"}
    mock_quota.return_value = (False, "")
    mock_store_value.return_value = "deploy-value://tenant/agent/value"
    
    mock_post.return_value = MagicMock(
        status_code=201,
        json=lambda: {"project": {"id": "proj_123"}, "connection_uri": "postgresql://user:pass@host/neondb"}
    )
    
    result = await _neon_create_database(
        agent_id=uuid.uuid4(),
        arguments={
            "project_name": "my-neon-project",
            "database_name": "neondb",
            "org_id": "my-manual-org",
        }
    )
    assert "proj_123" in result
    assert "private value_ref" in result
    assert "deploy-value://tenant/agent/value" in result
