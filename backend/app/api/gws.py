"""Google Workspace API routes.

Provides OAuth flow and credential management for Google Workspace integration.
"""

import uuid
from datetime import datetime, timezone
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Request
from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core.permissions import check_agent_access
from app.core.security import encrypt_data, get_current_user, require_role
from app.database import get_db
from app.models.gws_oauth_token import GwsOAuthToken
from app.models.user import User
from app.services import gws_service

router = APIRouter(prefix="/gws", tags=["google-workspace"])

settings = get_settings()


def _get_gws_redirect_uri() -> str:
    """Resolve GWS OAuth redirect URI.

    If GWS_OAUTH_REDIRECT_URI is explicitly set (e.g. http://localhost:8008/api/gws/auth/callback
    for local development with Desktop app OAuth client), use it directly.
    Otherwise, auto-generate from PUBLIC_BASE_URL + GWS_OAUTH_CALLBACK_PATH.
    """
    if settings.GWS_OAUTH_REDIRECT_URI:
        return settings.GWS_OAUTH_REDIRECT_URI
    return f"{settings.PUBLIC_BASE_URL}{settings.GWS_OAUTH_CALLBACK_PATH}"

GWS_SCOPES = [
    "openid",
    "email",
    "profile",
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/drive.readonly",
    "https://www.googleapis.com/auth/calendar.readonly",
    "https://www.googleapis.com/auth/spreadsheets.readonly",
]


@router.put("/settings/credentials")
async def store_gws_credentials(
    data: dict,
    current_user: User = Depends(require_role("org_admin", "platform_admin")),
    db: AsyncSession = Depends(get_db),
):
    """Store Google Workspace OAuth credentials for the tenant.
    
    Requires org_admin or platform_admin role.
    Body: {client_id, client_secret, project_id}
    """
    client_id = (data.get("client_id") or "").strip()
    client_secret = (data.get("client_secret") or "").strip()
    project_id = (data.get("project_id") or "").strip()
    
    if not client_id:
        raise HTTPException(status_code=422, detail="client_id is required")
    
    tenant_id = current_user.tenant_id
    if not tenant_id:
        raise HTTPException(status_code=400, detail="User has no tenant")
    
    await gws_service.save_tenant_gws_config(
        tenant_id=tenant_id,
        client_id=client_id,
        client_secret=client_secret,
        project_id=project_id,
        db=db,
    )
    
    return {"ok": True, "message": "Google Workspace credentials stored"}


@router.get("/settings/credentials")
async def get_gws_credentials(
    current_user: User = Depends(require_role("org_admin", "platform_admin")),
    db: AsyncSession = Depends(get_db),
):
    """Get Google Workspace credential status for the tenant.
    
    Returns {configured, masked_client_id, has_client_secret, project_id}.
    """
    tenant_id = current_user.tenant_id
    if not tenant_id:
        raise HTTPException(status_code=400, detail="User has no tenant")
    
    config = await gws_service.get_tenant_gws_config(tenant_id, db)
    
    if not config:
        return {
            "configured": False,
            "masked_client_id": "",
            "has_client_secret": False,
            "project_id": "",
        }
    
    client_id = config.get("client_id", "")
    masked_client_id = ""
    if client_id and len(client_id) > 8:
        masked_client_id = client_id[:4] + "****" + client_id[-4:]
    elif client_id:
        masked_client_id = client_id[:2] + "****"
    
    return {
        "configured": True,
        "masked_client_id": masked_client_id,
        "has_client_secret": bool(config.get("client_secret")),
        "project_id": config.get("project_id", ""),
    }


@router.post("/agents/{agent_id}/auth/authorize")
async def get_gws_authorize_url(
    agent_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Generate Google OAuth authorization URL for agent.
    
    Returns {authorize_url}.
    """
    await check_agent_access(db, current_user, agent_id)
    
    tenant_id = current_user.tenant_id
    if not tenant_id:
        raise HTTPException(status_code=400, detail="User has no tenant")
    
    config = await gws_service.get_tenant_gws_config(tenant_id, db)
    if not config or not config.get("client_id"):
        raise HTTPException(
            status_code=400,
            detail="Google Workspace not configured for tenant",
        )
    
    state = gws_service.generate_oauth_state(
        agent_id=agent_id,
        user_id=current_user.id,
        tenant_id=tenant_id,
    )
    
    redirect_uri = _get_gws_redirect_uri()
    
    params = {
        "client_id": config["client_id"],
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": " ".join(GWS_SCOPES),
        "access_type": "offline",
        "prompt": "consent",
        "state": state,
    }
    
    authorize_url = f"https://accounts.google.com/o/oauth2/v2/auth?{urlencode(params)}"
    
    return {"authorize_url": authorize_url}


@router.get("/auth/callback")
async def handle_gws_oauth_callback(
    code: str,
    state: str,
    db: AsyncSession = Depends(get_db),
):
    """Handle Google OAuth callback.
    
    Exchanges code for tokens, stores them encrypted, redirects to frontend.
    """
    try:
        state_data = gws_service.decrypt_oauth_state(state)
    except ValueError as e:
        logger.error(f"Invalid OAuth state: {e}")
        raise HTTPException(status_code=400, detail="Invalid OAuth state")
    
    agent_id = uuid.UUID(state_data["agent_id"])
    user_id = uuid.UUID(state_data["user_id"])
    tenant_id = uuid.UUID(state_data["tenant_id"])
    
    redirect_uri = _get_gws_redirect_uri()
    
    try:
        token_response = await gws_service.exchange_code_for_tokens(
            code=code,
            tenant_id=tenant_id,
            redirect_uri=redirect_uri,
            db=db,
        )
    except Exception as e:
        logger.error(f"Failed to exchange OAuth code: {e}")
        from fastapi.responses import RedirectResponse
        return RedirectResponse(
            url=f"{settings.PUBLIC_BASE_URL}/agents/{agent_id}?gws=error&message={str(e)[:100]}",
            status_code=302,
        )
    
    google_email = None
    google_user_id = None
    if token_response.get("id_token"):
        google_email = gws_service.parse_id_token_email(token_response["id_token"])
    
    access_token = token_response["access_token"]
    refresh_token = token_response.get("refresh_token", "")
    expires_in = token_response.get("expires_in", 3600)
    scopes = token_response.get("scope", "").split(" ")
    
    result = await db.execute(
        select(GwsOAuthToken).where(
            GwsOAuthToken.agent_id == agent_id,
            GwsOAuthToken.user_id == user_id,
        )
    )
    existing_token = result.scalar_one_or_none()
    
    encrypted_access_token = encrypt_data(access_token, settings.SECRET_KEY)
    encrypted_refresh_token = encrypt_data(refresh_token, settings.SECRET_KEY) if refresh_token else ""
    token_expiry = datetime.now(timezone.utc) + __import__("datetime").timedelta(seconds=expires_in)
    
    if existing_token:
        existing_token.access_token = encrypted_access_token
        if refresh_token:
            existing_token.refresh_token = encrypted_refresh_token
        existing_token.token_expiry = token_expiry
        existing_token.scopes = scopes
        existing_token.status = "active"
        existing_token.last_used_at = datetime.now(timezone.utc)
        if google_email:
            existing_token.google_email = google_email
        db.add(existing_token)
    else:
        new_token = GwsOAuthToken(
            agent_id=agent_id,
            user_id=user_id,
            tenant_id=tenant_id,
            google_email=google_email or "unknown",
            google_user_id=google_user_id,
            access_token=encrypted_access_token,
            refresh_token=encrypted_refresh_token,
            token_expiry=token_expiry,
            scopes=scopes,
            status="active",
            last_used_at=datetime.now(timezone.utc),
        )
        db.add(new_token)
    
    await db.commit()
    
    from fastapi.responses import RedirectResponse
    return RedirectResponse(
        url=f"{settings.PUBLIC_BASE_URL}/agents/{agent_id}?gws=success",
        status_code=302,
    )


@router.delete("/agents/{agent_id}/auth/revoke")
async def revoke_gws_oauth(
    agent_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Revoke Google Workspace OAuth token for current user."""
    await check_agent_access(db, current_user, agent_id)
    
    await gws_service.revoke_oauth_token(
        agent_id=agent_id,
        user_id=current_user.id,
        db=db,
    )
    
    return {"ok": True, "message": "OAuth token revoked"}


@router.get("/agents/{agent_id}/auth/accounts")
async def list_gws_oauth_accounts(
    agent_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List all authorized Google Workspace accounts for an agent."""
    await check_agent_access(db, current_user, agent_id)
    
    accounts = await gws_service.list_agent_oauth_accounts(agent_id, db)
    
    return accounts


@router.post("/skills/import")
async def import_gws_skills_endpoint(
    current_user: User = Depends(require_role("org_admin", "platform_admin")),
):
    """Manually re-import GWS skills from GitHub.
    
    Requires org_admin or platform_admin role.
    Imports skills scoped to the current user's tenant.
    """
    from app.services.gws_skill_seeder import import_gws_skills
    from pydantic import BaseModel
    
    class GWSImportResponse(BaseModel):
        ok: bool
        imported: int
    
    tenant_id = str(current_user.tenant_id) if current_user.tenant_id else None
    imported_count = await import_gws_skills(tenant_id)
    
    return GWSImportResponse(ok=True, imported=imported_count)
