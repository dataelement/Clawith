"""Agent Credentials CRUD API routes.

Provides endpoints for managing encrypted session cookies
per agent. Sensitive fields (cookies_json) are encrypted at rest
using AES-256-CBC and are NEVER returned in API responses.
"""

import json
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core.permissions import check_agent_access
from app.core.security import encrypt_data, get_current_user
from app.database import get_db
from app.models.agent_credential import AgentCredential
from app.models.user import User
from app.schemas.agent_credential import (
    AgentCredentialCreate,
    AgentCredentialResponse,
    AgentCredentialUpdate,
)

router = APIRouter(prefix="/agents/{agent_id}/credentials", tags=["agent-credentials"])


def _to_response(cred: AgentCredential) -> dict:
    """Convert an AgentCredential ORM object to a safe response dict.

    NEVER exposes cookies_json. Uses has_cookies as a presence flag instead.
    """
    return {
        "id": cred.id,
        "agent_id": cred.agent_id,
        "credential_type": cred.credential_type,
        "platform": cred.platform,
        "display_name": cred.display_name or "",
        "status": cred.status,
        "cookies_updated_at": cred.cookies_updated_at,
        "last_login_at": cred.last_login_at,
        "last_injected_at": cred.last_injected_at,
        "has_cookies": bool(cred.cookies_json),
        "created_at": cred.created_at,
        "updated_at": cred.updated_at,
    }


@router.get("/")
async def list_credentials(
    agent_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List all credentials for an agent (sensitive data excluded)."""
    # Verify the user has manage-level access to this agent
    _agent, access_level = await check_agent_access(db, current_user, agent_id)
    if access_level not in ("manage",) and current_user.role not in ("platform_admin", "org_admin"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Manage access required to view credentials",
        )

    result = await db.execute(
        select(AgentCredential)
        .where(AgentCredential.agent_id == agent_id)
        .order_by(AgentCredential.created_at.desc())
    )
    credentials = result.scalars().all()
    return [_to_response(c) for c in credentials]


@router.post("/", status_code=status.HTTP_201_CREATED)
async def create_credential(
    agent_id: uuid.UUID,
    data: AgentCredentialCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Create a new credential for an agent.

    Sensitive fields (cookies_json) are encrypted before storage.
    """
    _agent, access_level = await check_agent_access(db, current_user, agent_id)
    if access_level not in ("manage",) and current_user.role not in ("platform_admin", "org_admin"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Manage access required to create credentials",
        )

    settings = get_settings()

    # Validate cookies_json format if provided
    if data.cookies_json:
        try:
            parsed = json.loads(data.cookies_json)
            if not isinstance(parsed, list):
                raise ValueError("cookies_json must be a JSON array")
        except (json.JSONDecodeError, ValueError) as e:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid cookies_json format: {e}",
            )

    cred = AgentCredential(
        agent_id=agent_id,
        credential_type=data.credential_type,
        platform=data.platform,
        display_name=data.display_name or "",
        status="active",
    )

    # Encrypt sensitive fields
    if data.cookies_json:
        cred.cookies_json = encrypt_data(data.cookies_json, settings.SECRET_KEY)
        cred.cookies_updated_at = datetime.now(timezone.utc)

    db.add(cred)
    await db.commit()
    await db.refresh(cred)

    return _to_response(cred)


@router.put("/{credential_id}")
async def update_credential(
    agent_id: uuid.UUID,
    credential_id: uuid.UUID,
    data: AgentCredentialUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Update an existing credential.

    Only provided fields are updated. Sensitive fields are re-encrypted.
    If cookies_json is updated, status is reset to 'active'.
    """
    _agent, access_level = await check_agent_access(db, current_user, agent_id)
    if access_level not in ("manage",) and current_user.role not in ("platform_admin", "org_admin"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Manage access required to update credentials",
        )

    result = await db.execute(
        select(AgentCredential).where(
            AgentCredential.id == credential_id,
            AgentCredential.agent_id == agent_id,
        )
    )
    cred = result.scalar_one_or_none()
    if not cred:
        raise HTTPException(status_code=404, detail="Credential not found")

    settings = get_settings()
    update_data = data.model_dump(exclude_unset=True)

    # Handle plaintext fields
    for field in ("credential_type", "platform", "display_name", "status"):
        if field in update_data:
            setattr(cred, field, update_data[field])

    if "cookies_json" in update_data:
        if update_data["cookies_json"]:
            # Validate JSON format
            try:
                parsed = json.loads(update_data["cookies_json"])
                if not isinstance(parsed, list):
                    raise ValueError("cookies_json must be a JSON array")
            except (json.JSONDecodeError, ValueError) as e:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Invalid cookies_json format: {e}",
                )
            cred.cookies_json = encrypt_data(update_data["cookies_json"], settings.SECRET_KEY)
            cred.cookies_updated_at = datetime.now(timezone.utc)
            # Reset status to active when cookies are updated
            cred.status = "active"
        else:
            cred.cookies_json = None
            cred.cookies_updated_at = None

    await db.commit()
    await db.refresh(cred)

    return _to_response(cred)


@router.delete("/{credential_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_credential(
    agent_id: uuid.UUID,
    credential_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Delete a credential."""
    _agent, access_level = await check_agent_access(db, current_user, agent_id)
    if access_level not in ("manage",) and current_user.role not in ("platform_admin", "org_admin"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Manage access required to delete credentials",
        )

    result = await db.execute(
        select(AgentCredential).where(
            AgentCredential.id == credential_id,
            AgentCredential.agent_id == agent_id,
        )
    )
    cred = result.scalar_one_or_none()
    if not cred:
        raise HTTPException(status_code=404, detail="Credential not found")

    await db.delete(cred)
    await db.commit()
