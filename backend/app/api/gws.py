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

GWS_SCOPE_PRESETS = {
    "readonly": {
        "label": "Read Only",
        "description": "View emails, calendar, drive files and spreadsheets (no modifications)",
        "scopes": [
            "openid",
            "email",
            "profile",
            "https://www.googleapis.com/auth/gmail.readonly",
            "https://www.googleapis.com/auth/drive.readonly",
            "https://www.googleapis.com/auth/calendar.readonly",
            "https://www.googleapis.com/auth/spreadsheets.readonly",
            "https://www.googleapis.com/auth/documents.readonly",
            "https://www.googleapis.com/auth/presentations.readonly",
            "https://www.googleapis.com/auth/tasks.readonly",
            "https://www.googleapis.com/auth/contacts.readonly",
        ],
    },
    "standard": {
        "label": "Standard (Read & Write)",
        "description": "Read and write emails, calendar events, drive files, spreadsheets, docs, slides, tasks, and contacts",
        "scopes": [
            "openid",
            "email",
            "profile",
            "https://www.googleapis.com/auth/gmail.modify",
            "https://www.googleapis.com/auth/gmail.send",
            "https://www.googleapis.com/auth/gmail.compose",
            "https://www.googleapis.com/auth/drive",
            "https://www.googleapis.com/auth/calendar",
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/documents",
            "https://www.googleapis.com/auth/presentations",
            "https://www.googleapis.com/auth/tasks",
            "https://www.googleapis.com/auth/contacts",
        ],
    },
    "full": {
        "label": "Full Access",
        "description": "All standard permissions plus Gmail settings, Chat, Forms, and Apps Script",
        "scopes": [
            "openid",
            "email",
            "profile",
            "https://www.googleapis.com/auth/gmail.modify",
            "https://www.googleapis.com/auth/gmail.send",
            "https://www.googleapis.com/auth/gmail.compose",
            "https://www.googleapis.com/auth/gmail.settings.basic",
            "https://www.googleapis.com/auth/drive",
            "https://www.googleapis.com/auth/calendar",
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/documents",
            "https://www.googleapis.com/auth/presentations",
            "https://www.googleapis.com/auth/tasks",
            "https://www.googleapis.com/auth/contacts",
            "https://www.googleapis.com/auth/chat.messages",
            "https://www.googleapis.com/auth/chat.spaces.readonly",
            "https://www.googleapis.com/auth/forms.body",
            "https://www.googleapis.com/auth/script.projects",
        ],
    },
}

GWS_AVAILABLE_SCOPES = [
    {"scope": "https://www.googleapis.com/auth/gmail.readonly", "label": "Gmail (Read)", "category": "Gmail"},
    {"scope": "https://www.googleapis.com/auth/gmail.modify", "label": "Gmail (Read & Modify)", "category": "Gmail"},
    {"scope": "https://www.googleapis.com/auth/gmail.send", "label": "Gmail (Send)", "category": "Gmail"},
    {"scope": "https://www.googleapis.com/auth/gmail.compose", "label": "Gmail (Compose)", "category": "Gmail"},
    {"scope": "https://www.googleapis.com/auth/gmail.settings.basic", "label": "Gmail (Settings)", "category": "Gmail"},
    {"scope": "https://www.googleapis.com/auth/drive.readonly", "label": "Drive (Read)", "category": "Drive"},
    {"scope": "https://www.googleapis.com/auth/drive", "label": "Drive (Full)", "category": "Drive"},
    {"scope": "https://www.googleapis.com/auth/calendar.readonly", "label": "Calendar (Read)", "category": "Calendar"},
    {"scope": "https://www.googleapis.com/auth/calendar", "label": "Calendar (Full)", "category": "Calendar"},
    {"scope": "https://www.googleapis.com/auth/spreadsheets.readonly", "label": "Sheets (Read)", "category": "Sheets"},
    {"scope": "https://www.googleapis.com/auth/spreadsheets", "label": "Sheets (Full)", "category": "Sheets"},
    {"scope": "https://www.googleapis.com/auth/documents.readonly", "label": "Docs (Read)", "category": "Docs"},
    {"scope": "https://www.googleapis.com/auth/documents", "label": "Docs (Full)", "category": "Docs"},
    {"scope": "https://www.googleapis.com/auth/presentations.readonly", "label": "Slides (Read)", "category": "Slides"},
    {"scope": "https://www.googleapis.com/auth/presentations", "label": "Slides (Full)", "category": "Slides"},
    {"scope": "https://www.googleapis.com/auth/tasks.readonly", "label": "Tasks (Read)", "category": "Tasks"},
    {"scope": "https://www.googleapis.com/auth/tasks", "label": "Tasks (Full)", "category": "Tasks"},
    {"scope": "https://www.googleapis.com/auth/contacts.readonly", "label": "Contacts (Read)", "category": "Contacts"},
    {"scope": "https://www.googleapis.com/auth/contacts", "label": "Contacts (Full)", "category": "Contacts"},
    {"scope": "https://www.googleapis.com/auth/chat.messages", "label": "Chat (Messages)", "category": "Chat", "requires_api": "Google Chat API"},
    {"scope": "https://www.googleapis.com/auth/chat.spaces.readonly", "label": "Chat (Spaces Read)", "category": "Chat", "requires_api": "Google Chat API"},
    {"scope": "https://www.googleapis.com/auth/chat.spaces", "label": "Chat (Spaces Full)", "category": "Chat", "requires_api": "Google Chat API"},
    {"scope": "https://www.googleapis.com/auth/forms.body", "label": "Forms", "category": "Forms", "requires_api": "Google Forms API"},
    {"scope": "https://www.googleapis.com/auth/script.projects", "label": "Apps Script", "category": "Apps Script", "requires_api": "Apps Script API"},
    {"scope": "https://www.googleapis.com/auth/admin.reports.audit.readonly", "label": "Admin Reports (Read)", "category": "Admin", "requires_api": "Admin SDK API"},
    {"scope": "https://www.googleapis.com/auth/classroom.courses", "label": "Classroom", "category": "Classroom", "requires_api": "Google Classroom API"},
    {"scope": "https://www.googleapis.com/auth/pubsub", "label": "Pub/Sub", "category": "Cloud", "requires_api": "Cloud Pub/Sub API"},
    {"scope": "https://www.googleapis.com/auth/cloud-platform", "label": "Cloud Platform", "category": "Cloud", "requires_api": "Cloud Resource Manager API"},
]

DEFAULT_SCOPE_PRESET = "readonly"

IDENTITY_SCOPES = ["openid", "email", "profile"]


def _resolve_scopes(config: dict) -> list[str]:
    """Resolve OAuth scopes from tenant GWS configuration.

    Reads scope_preset and custom_scopes from the tenant config.
    Falls back to DEFAULT_SCOPE_PRESET if not configured.
    """
    preset = config.get("scope_preset", DEFAULT_SCOPE_PRESET)

    if preset == "custom":
        custom = config.get("custom_scopes", [])
        if not custom:
            return GWS_SCOPE_PRESETS[DEFAULT_SCOPE_PRESET]["scopes"]
        scopes = list(IDENTITY_SCOPES)
        for s in custom:
            if s not in scopes:
                scopes.append(s)
        return scopes

    if preset in GWS_SCOPE_PRESETS:
        return GWS_SCOPE_PRESETS[preset]["scopes"]

    return GWS_SCOPE_PRESETS[DEFAULT_SCOPE_PRESET]["scopes"]


@router.put("/settings/credentials")
async def store_gws_credentials(
    data: dict,
    current_user: User = Depends(require_role("org_admin", "platform_admin")),
    db: AsyncSession = Depends(get_db),
):
    """Store Google Workspace OAuth credentials and scope configuration.

    Requires org_admin or platform_admin role.
    Body: {client_id, client_secret, project_id, scope_preset?, custom_scopes?}
    """
    client_id = (data.get("client_id") or "").strip()
    client_secret = (data.get("client_secret") or "").strip()
    project_id = (data.get("project_id") or "").strip()
    scope_preset = (data.get("scope_preset") or DEFAULT_SCOPE_PRESET).strip()
    custom_scopes = data.get("custom_scopes") or []

    if scope_preset not in (*GWS_SCOPE_PRESETS, "custom"):
        raise HTTPException(status_code=422, detail=f"Invalid scope_preset: {scope_preset}")

    tenant_id = current_user.tenant_id
    if not tenant_id:
        raise HTTPException(status_code=400, detail="User has no tenant")

    existing_config = await gws_service.get_tenant_gws_config(tenant_id, db)
    if not client_id and not existing_config.get("client_id"):
        raise HTTPException(status_code=422, detail="client_id is required")

    await gws_service.save_tenant_gws_config(
        tenant_id=tenant_id,
        client_id=client_id,
        client_secret=client_secret,
        project_id=project_id,
        scope_preset=scope_preset,
        custom_scopes=custom_scopes,
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
            "scope_preset": DEFAULT_SCOPE_PRESET,
            "custom_scopes": [],
            "resolved_scopes": GWS_SCOPE_PRESETS[DEFAULT_SCOPE_PRESET]["scopes"],
        }

    client_id = config.get("client_id", "")
    masked_client_id = ""
    if client_id and len(client_id) > 8:
        masked_client_id = client_id[:4] + "****" + client_id[-4:]
    elif client_id:
        masked_client_id = client_id[:2] + "****"

    scope_preset = config.get("scope_preset", DEFAULT_SCOPE_PRESET)
    custom_scopes = config.get("custom_scopes", [])

    return {
        "configured": True,
        "masked_client_id": masked_client_id,
        "has_client_secret": bool(config.get("client_secret")),
        "project_id": config.get("project_id", ""),
        "scope_preset": scope_preset,
        "custom_scopes": custom_scopes,
        "resolved_scopes": _resolve_scopes(config),
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
    
    resolved_scopes = _resolve_scopes(config)

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
        "scope": " ".join(resolved_scopes),
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


@router.get("/settings/scope-options")
async def get_scope_options(
    current_user: User = Depends(require_role("org_admin", "platform_admin")),
):
    """Return available scope presets and individual scopes for the config UI."""
    presets = {
        k: {"label": v["label"], "description": v["description"], "scopes": v["scopes"]}
        for k, v in GWS_SCOPE_PRESETS.items()
    }
    return {
        "presets": presets,
        "available_scopes": GWS_AVAILABLE_SCOPES,
        "default_preset": DEFAULT_SCOPE_PRESET,
    }


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
