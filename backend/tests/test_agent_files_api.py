"""Unit tests for agent files listing API and boundary path coverage."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from app.api.files import list_files
from app.models.user import User
from app.services.storage_runtime.base import StorageEntry


@pytest.fixture
def sample_user():
    user = User()
    user.id = uuid.uuid4()
    user.tenant_id = uuid.uuid4()
    user.role = "member"
    return user


@pytest.mark.asyncio
async def test_list_files_missing_skills_directory_returns_empty_list(sample_user):
    """When path=skills and the skills directory does not exist on storage, return empty list instead of 404."""
    agent_id = uuid.uuid4()
    
    mock_storage = AsyncMock()
    mock_storage.exists.return_value = False
    mock_storage.is_dir.return_value = False

    with patch("app.api.files.check_agent_access", AsyncMock()) as mock_check, \
         patch("app.api.files.get_storage_backend", return_value=mock_storage):
        
        result = await list_files(agent_id=agent_id, path="skills", current_user=sample_user, db=AsyncMock())
        
        assert result == []
        mock_check.assert_awaited_once()


@pytest.mark.asyncio
async def test_list_files_existing_skills_directory_returns_entries(sample_user):
    """When path=skills and skills exist, return skill directories."""
    agent_id = uuid.uuid4()
    storage_key = f"{agent_id}/skills"

    entry1 = StorageEntry(
        key=f"{storage_key}/web-search",
        name="web-search",
        is_dir=True,
        size=1024,
        modified_at="1779461034.0",
    )
    
    mock_storage = AsyncMock()
    mock_storage.exists.return_value = True
    mock_storage.is_dir.return_value = True
    mock_storage.list_dir.return_value = [entry1]

    with patch("app.api.files.check_agent_access", AsyncMock()), \
         patch("app.api.files.get_storage_backend", return_value=mock_storage), \
         patch("app.api.files._directory_total_size", AsyncMock(return_value=1024)):
        
        result = await list_files(agent_id=agent_id, path="skills", current_user=sample_user, db=AsyncMock())
        
        assert len(result) == 1
        assert result[0].name == "web-search"
        assert result[0].is_dir is True
        assert result[0].path == "skills/web-search"


@pytest.mark.asyncio
async def test_list_files_invalid_path_raises_404(sample_user):
    """When an arbitrary non-existent path is requested, raise 404 Path not found."""
    agent_id = uuid.uuid4()
    
    mock_storage = AsyncMock()
    mock_storage.exists.return_value = False
    mock_storage.is_dir.return_value = False

    with patch("app.api.files.check_agent_access", AsyncMock()), \
         patch("app.api.files.get_storage_backend", return_value=mock_storage):
        
        with pytest.raises(HTTPException) as exc_info:
            await list_files(agent_id=agent_id, path="invalid_non_existent_dir", current_user=sample_user, db=AsyncMock())
            
        assert exc_info.value.status_code == 404
        assert exc_info.value.detail == "Path not found"


@pytest.mark.asyncio
async def test_list_files_cross_tenant_access_denied_raises_404(sample_user):
    """When agent belongs to another tenant or does not exist, check_agent_access raises 404 Agent not found."""
    agent_id = uuid.uuid4()

    with patch("app.api.files.check_agent_access", AsyncMock(side_effect=HTTPException(status_code=404, detail="Agent not found"))):
        with pytest.raises(HTTPException) as exc_info:
            await list_files(agent_id=agent_id, path="skills", current_user=sample_user, db=AsyncMock())
            
        assert exc_info.value.status_code == 404
        assert exc_info.value.detail == "Agent not found"
