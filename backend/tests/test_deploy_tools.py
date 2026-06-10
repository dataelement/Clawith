import uuid
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from pathlib import Path

from app.services.agent_tools import (
    _get_vercel_token,
    _get_vercel_quota_summary,
    _check_neon_quota_limit,
    _vercel_deploy,
    _vercel_list_deployments,
    _vercel_get_deploy_logs,
    _vercel_set_env,
    _vercel_manage_domain,
    _neon_create_database,
)

@pytest.mark.asyncio
@patch("app.services.agent_tools._get_tool_config")
async def test_get_vercel_token(mock_get_config):
    # Case 1: Direct configuration present
    mock_get_config.return_value = {"vercel_token": "my-direct-token"}
    token = await _get_vercel_token(uuid.uuid4(), "vercel_list_deployments")
    assert token == "my-direct-token"

    # Case 2: Config is missing, fallback to vercel_deploy tool configuration
    mock_get_config.side_effect = lambda agent_id, tool_name: (
        None if tool_name == "vercel_list_deployments" else {"vercel_token": "fallback-token"}
    )
    token = await _get_vercel_token(uuid.uuid4(), "vercel_list_deployments")
    assert token == "fallback-token"


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

    # Mock project linking and trigger
    mock_post.side_effect = [
        MagicMock(status_code=200, json=lambda: {}), # Link repo
        MagicMock(status_code=200, json=lambda: {"id": "dep_123", "url": "test.vercel.app"}) # Trigger deployment
    ]

    # Mock polling status to return READY immediately
    mock_get.side_effect = [
        MagicMock(status_code=200, json=lambda: {"id": "proj_123", "name": "my-project"}), # Project check GET
        MagicMock(status_code=200, json=lambda: {"readyState": "READY", "url": "test.vercel.app"}), # Deployment info GET
        MagicMock(status_code=200, json=lambda: {"projects": []}), # Project list for quota
        MagicMock(status_code=200, json=lambda: {"user": {"username": "test_user", "billing": {"plan": "Hobby"}}}) # User billing
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
    assert "Deployment triggered successfully" in result
    assert "test.vercel.app" in result


@pytest.mark.asyncio
@patch("app.services.agent_tools._get_vercel_token")
@patch("httpx.AsyncClient.post")
async def test_vercel_set_env(mock_post, mock_get_token):
    mock_get_token.return_value = "fake-token"
    mock_post.return_value = MagicMock(status_code=201, json=lambda: {})
    
    result = await _vercel_set_env(
        agent_id=uuid.uuid4(),
        arguments={
            "project_name": "my-project",
            "key": "DATABASE_URL",
            "value": "postgres://..."
        }
    )
    assert "set successfully" in result


@pytest.mark.asyncio
@patch("app.services.agent_tools._get_vercel_token")
@patch("httpx.AsyncClient.post")
@patch("httpx.AsyncClient.get")
@patch("httpx.AsyncClient.patch")
async def test_vercel_set_env_conflict_updates(mock_patch, mock_get, mock_post, mock_get_token):
    mock_get_token.return_value = "fake-token"

    # Mock conflict 403 ENV_ALREADY_EXISTS
    mock_post.return_value = MagicMock(status_code=403, text='{"error":{"code":"ENV_ALREADY_EXISTS"}}')
    # Mock list envs to retrieve ID
    mock_get.return_value = MagicMock(status_code=200, json=lambda: {"envs": [{"id": "env_abc", "key": "DATABASE_URL"}]})
    # Mock patch request
    mock_patch.return_value = MagicMock(status_code=200, json=lambda: {})

    result = await _vercel_set_env(
        agent_id=uuid.uuid4(),
        arguments={
            "project_name": "my-project",
            "key": "DATABASE_URL",
            "value": "postgres://new-value"
        }
    )
    assert "updated successfully" in result


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
    assert "Available for purchase: Yes" in result
    assert "$10" in result


@pytest.mark.asyncio
@patch("app.services.agent_tools._get_tool_config")
@patch("app.services.agent_tools._check_neon_quota_limit")
@patch("httpx.AsyncClient.get")
@patch("httpx.AsyncClient.post")
async def test_neon_create_database_auto_resolve_org_id(mock_post, mock_get, mock_quota, mock_get_config):
    mock_get_config.return_value = {"neon_api_key": "fake-key"}
    mock_quota.return_value = (False, "")
    
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
        arguments={"project_name": "my-neon-project"}
    )
    assert "database created successfully" in result
    assert "postgresql://user:pass@host/neondb" in result
    assert "proj_123" in result


@pytest.mark.asyncio
@patch("app.services.agent_tools._get_tool_config")
@patch("app.services.agent_tools._check_neon_quota_limit")
@patch("httpx.AsyncClient.post")
async def test_neon_create_database_with_provided_org_id(mock_post, mock_quota, mock_get_config):
    mock_get_config.return_value = {"neon_api_key": "fake-key"}
    mock_quota.return_value = (False, "")
    
    mock_post.return_value = MagicMock(
        status_code=201,
        json=lambda: {"project": {"id": "proj_123"}, "connection_uri": "postgresql://user:pass@host/neondb"}
    )
    
    result = await _neon_create_database(
        agent_id=uuid.uuid4(),
        arguments={"project_name": "my-neon-project", "org_id": "my-manual-org"}
    )
    assert "database created successfully" in result

