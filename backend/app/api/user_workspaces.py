"""User workspace isolation APIs."""

import uuid
from fastapi import APIRouter, Depends, HTTPException, status, UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import get_current_user
from app.database import get_db
from app.models.user import User
from app.models.agent import Agent as AgentModel
from pathlib import Path
from app.config import get_settings

settings = get_settings()
WORKSPACE_ROOT = Path(settings.AGENT_DATA_DIR)

router = APIRouter(prefix="/agents/{agent_id}/user-workspaces", tags=["user-workspaces"])


@router.get("/users")
async def list_agent_users(
    agent_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List all users who have interacted with this agent.
    
    Only accessible by agent creator or admin.
    """
    # Check permission
    result = await db.execute(select(AgentModel).where(AgentModel.id == agent_id))
    agent = result.scalar_one_or_none()
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    
    is_creator = agent.creator_id == current_user.id
    is_admin = current_user.role in ("platform_admin", "org_admin")
    
    if not is_creator and not is_admin:
        raise HTTPException(status_code=403, detail="Only creator or admin can access user list")
    
    # Find all users who have workspaces under this agent
    agent_dir = WORKSPACE_ROOT / str(agent_id)
    users_dir = agent_dir / "users"
    
    if not users_dir.exists():
        return {"users": []}
    
    user_ids = []
    for entry in users_dir.iterdir():
        if entry.is_dir():
            try:
                user_uuid = uuid.UUID(entry.name)
                user_ids.append(str(user_uuid))
            except ValueError:
                continue
    
    # Get user details from database
    from app.models.user import User as UserModel
    user_result = await db.execute(
        select(UserModel).where(UserModel.id.in_(user_ids))
    )
    users = user_result.scalars().all()
    
    return {
        "users": [
            {
                "id": str(u.id),
                "display_name": u.display_name,
                "avatar_url": u.avatar_url,
            }
            for u in users
        ]
    }


@router.get("/users/{user_id}/files")
async def list_user_files(
    agent_id: uuid.UUID,
    user_id: uuid.UUID,
    path: str = "",
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List files in a user's workspace.
    
    Users can only access their own files.
    Admins/creators can access any user's files.
    """
    # Check permission
    if user_id != current_user.id:
        result = await db.execute(select(AgentModel).where(AgentModel.id == agent_id))
        agent = result.scalar_one_or_none()
        if not agent:
            raise HTTPException(status_code=404, detail="Agent not found")
        
        is_creator = agent.creator_id == current_user.id
        is_admin = current_user.role in ("platform_admin", "org_admin")
        
        if not is_creator and not is_admin:
            raise HTTPException(status_code=403, detail="Cannot access other user's workspace")
    
    # Build path - user files are stored in users/{user_id}/files/
    agent_dir = WORKSPACE_ROOT / str(agent_id)
    user_dir = agent_dir / "users" / str(user_id) / "files"

    if not user_dir.exists():
        return {"files": [], "directories": []}

    target_path = user_dir / path if path else user_dir
    
    if not target_path.exists() or not str(target_path).startswith(str(user_dir)):
        raise HTTPException(status_code=404, detail="Path not found")
    
    files = []
    directories = []
    
    for entry in target_path.iterdir():
        if entry.is_file():
            files.append({
                "name": entry.name,
                "path": str(entry.relative_to(user_dir)),
                "size": entry.stat().st_size,
            })
        elif entry.is_dir():
            directories.append({
                "name": entry.name,
                "path": str(entry.relative_to(user_dir)),
            })
    
    return {
        "files": files,
        "directories": directories,
        "current_path": str(target_path.relative_to(user_dir)) if path else "",
    }


@router.get("/users/{user_id}/memory")
async def get_user_memory(
    agent_id: uuid.UUID,
    user_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get user's personal memory.
    
    Users can only access their own memory.
    Admins/creators can access any user's memory.
    """
    # Check permission (same as list_user_files)
    if user_id != current_user.id:
        result = await db.execute(select(AgentModel).where(AgentModel.id == agent_id))
        agent = result.scalar_one_or_none()
        if not agent:
            raise HTTPException(status_code=404, detail="Agent not found")
        
        is_creator = agent.creator_id == current_user.id
        is_admin = current_user.role in ("platform_admin", "org_admin")
        
        if not is_creator and not is_admin:
            raise HTTPException(status_code=403, detail="Cannot access other user's workspace")
    
    # Read memory file
    agent_dir = WORKSPACE_ROOT / str(agent_id)
    user_dir = agent_dir / "users" / str(user_id)
    memory_file = user_dir / "memory.md"
    
    if not memory_file.exists():
        return {"content": ""}
    
    return {"content": memory_file.read_text(encoding="utf-8")}


@router.put("/users/{user_id}/memory")
async def update_user_memory(
    agent_id: uuid.UUID,
    user_id: uuid.UUID,
    content: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Update user's personal memory.
    
    Users can only update their own memory.
    Admins/creators can update any user's memory.
    """
    # Check permission
    if user_id != current_user.id:
        result = await db.execute(select(AgentModel).where(AgentModel.id == agent_id))
        agent = result.scalar_one_or_none()
        if not agent:
            raise HTTPException(status_code=404, detail="Agent not found")
        
        is_creator = agent.creator_id == current_user.id
        is_admin = current_user.role in ("platform_admin", "org_admin")
        
        if not is_creator and not is_admin:
            raise HTTPException(status_code=403, detail="Cannot access other user's workspace")
    
    # Write memory file
    agent_dir = WORKSPACE_ROOT / str(agent_id)
    user_dir = agent_dir / "users" / str(user_id)
    user_dir.mkdir(parents=True, exist_ok=True)
    
    memory_file = user_dir / "memory.md"
    memory_file.write_text(content, encoding="utf-8")

    return {"success": True}


@router.post("/users/{user_id}/files/upload")
async def upload_user_file(
    agent_id: uuid.UUID,
    user_id: uuid.UUID,
    path: str,
    file: UploadFile,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Upload a file to user's workspace.

    Users can only upload to their own files.
    Admins/creators can upload to any user's files.
    """
    from fastapi import UploadFile
    
    # Check permission
    if user_id != current_user.id:
        result = await db.execute(select(AgentModel).where(AgentModel.id == agent_id))
        agent = result.scalar_one_or_none()
        if not agent:
            raise HTTPException(status_code=404, detail="Agent not found")

        is_creator = agent.creator_id == current_user.id
        is_admin = current_user.role in ("platform_admin", "org_admin")

        if not is_creator and not is_admin:
            raise HTTPException(status_code=403, detail="Cannot access other user's workspace")

    # Create user directory
    agent_dir = WORKSPACE_ROOT / str(agent_id)
    user_dir = agent_dir / "users" / str(user_id) / "files"
    user_dir.mkdir(parents=True, exist_ok=True)

    # Save file
    file_path = user_dir / file.filename
    content = await file.read()
    file_path.write_bytes(content)

    return {
        "success": True,
        "path": str(file_path.relative_to(user_dir.parent)),
        "filename": file.filename,
        "size": len(content),
    }
