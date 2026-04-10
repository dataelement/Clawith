"""Google Workspace service layer — OAuth token management and API helpers."""

import base64
import json
import uuid
from datetime import datetime, timedelta, timezone

import httpx
from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core.security import decrypt_data, encrypt_data
from app.database import async_session
from app.models.gws_oauth_token import GwsOAuthToken
from app.models.tenant_setting import TenantSetting

settings = get_settings()


async def get_tenant_gws_config(tenant_id: uuid.UUID, db: AsyncSession | None = None) -> dict:
    """Read Google Workspace configuration from TenantSetting.
    
    Returns dict with client_id, client_secret (decrypted), and project_id.
    Returns empty dict if not configured.
    """
    async def _fetch(session: AsyncSession) -> dict:
        result = await session.execute(
            select(TenantSetting).where(
                TenantSetting.tenant_id == tenant_id,
                TenantSetting.key == "google_workspace",
            )
        )
        setting = result.scalar_one_or_none()
        if not setting or not setting.value:
            return {}
        
        config = setting.value.copy()
        
        if config.get("client_secret"):
            try:
                config["client_secret"] = decrypt_data(config["client_secret"], settings.SECRET_KEY)
            except Exception as e:
                logger.error(f"Failed to decrypt GWS client_secret: {e}")
                config["client_secret"] = ""
        
        return config
    
    if db is not None:
        return await _fetch(db)
    async with async_session() as session:
        return await _fetch(session)


async def save_tenant_gws_config(
    tenant_id: uuid.UUID,
    client_id: str,
    client_secret: str,
    project_id: str,
    scope_preset: str = "standard",
    custom_scopes: list[str] | None = None,
    db: AsyncSession | None = None,
) -> None:
    """Encrypt and store Google Workspace configuration in TenantSetting."""
    async def _save(session: AsyncSession) -> None:
        encrypted_secret = encrypt_data(client_secret, settings.SECRET_KEY) if client_secret else ""

        result = await session.execute(
            select(TenantSetting).where(
                TenantSetting.tenant_id == tenant_id,
                TenantSetting.key == "google_workspace",
            )
        )
        setting = result.scalar_one_or_none()

        if setting and setting.value:
            value = setting.value.copy()
            if client_id:
                value["client_id"] = client_id
            if encrypted_secret:
                value["client_secret"] = encrypted_secret
            if project_id:
                value["project_id"] = project_id
            value["scope_preset"] = scope_preset
            value["custom_scopes"] = custom_scopes or []
        else:
            value = {
                "client_id": client_id,
                "client_secret": encrypted_secret,
                "project_id": project_id,
                "scope_preset": scope_preset,
                "custom_scopes": custom_scopes or [],
            }

        if setting:
            setting.value = value
        else:
            setting = TenantSetting(
                tenant_id=tenant_id,
                key="google_workspace",
                value=value,
            )
            session.add(setting)

        await session.commit()
    
    if db is not None:
        return await _save(db)
    async with async_session() as session:
        return await _save(session)


def generate_oauth_state(agent_id: uuid.UUID, user_id: uuid.UUID, tenant_id: uuid.UUID) -> str:
    """Create encrypted OAuth state parameter.
    
    Returns base64-encoded encrypted JSON containing agent_id, user_id, tenant_id.
    """
    state_data = json.dumps({
        "agent_id": str(agent_id),
        "user_id": str(user_id),
        "tenant_id": str(tenant_id),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })
    return encrypt_data(state_data, settings.SECRET_KEY)


def decrypt_oauth_state(state: str) -> dict:
    """Decrypt OAuth state parameter.
    
    Returns dict with agent_id, user_id, tenant_id.
    Raises ValueError if decryption or parsing fails.
    """
    try:
        decrypted = decrypt_data(state, settings.SECRET_KEY)
        return json.loads(decrypted)
    except Exception as e:
        raise ValueError(f"Invalid OAuth state: {e}") from e


async def exchange_code_for_tokens(
    code: str,
    tenant_id: uuid.UUID,
    redirect_uri: str,
    db: AsyncSession | None = None,
) -> dict:
    """Exchange OAuth authorization code for access and refresh tokens.
    
    POST to https://oauth2.googleapis.com/token with client credentials from TenantSetting.
    Returns token response dict with access_token, refresh_token, expires_in, etc.
    """
    config = await get_tenant_gws_config(tenant_id, db)
    
    if not config.get("client_id") or not config.get("client_secret"):
        raise ValueError("Google Workspace not configured for tenant")
    
    token_url = "https://oauth2.googleapis.com/token"
    data = {
        "code": code,
        "client_id": config["client_id"],
        "client_secret": config["client_secret"],
        "redirect_uri": redirect_uri,
        "grant_type": "authorization_code",
    }
    
    async with httpx.AsyncClient() as client:
        response = await client.post(token_url, data=data)
        response.raise_for_status()
        return response.json()


async def refresh_access_token(
    token_record: GwsOAuthToken,
    tenant_id: uuid.UUID,
    db: AsyncSession | None = None,
) -> str:
    """Refresh expired access token using refresh_token.
    
    Returns new plaintext access_token and updates DB record.
    """
    config = await get_tenant_gws_config(tenant_id, db)
    
    if not config.get("client_id") or not config.get("client_secret"):
        raise ValueError("Google Workspace not configured for tenant")
    
    refresh_token = decrypt_data(token_record.refresh_token, settings.SECRET_KEY)
    
    token_url = "https://oauth2.googleapis.com/token"
    data = {
        "client_id": config["client_id"],
        "client_secret": config["client_secret"],
        "refresh_token": refresh_token,
        "grant_type": "refresh_token",
    }
    
    async with httpx.AsyncClient() as client:
        response = await client.post(token_url, data=data)
        response.raise_for_status()
        token_data = response.json()
    
    # Update token record
    new_access_token = token_data["access_token"]
    expires_in = token_data.get("expires_in", 3600)
    
    token_record.access_token = encrypt_data(new_access_token, settings.SECRET_KEY)
    token_record.token_expiry = datetime.now(timezone.utc) + timedelta(seconds=expires_in)
    token_record.status = "active"
    token_record.last_used_at = datetime.now(timezone.utc)
    
    async def _update(session: AsyncSession):
        session.add(token_record)
        await session.commit()
        await session.refresh(token_record)
    
    if db is not None:
        await _update(db)
    else:
        async with async_session() as session:
            await _update(session)
    
    return new_access_token


async def get_user_token_for_agent(
    agent_id: uuid.UUID,
    user_id: uuid.UUID,
    db: AsyncSession | None = None,
) -> str:
    """Get plaintext access_token for (agent_id, user_id) pair.
    
    - Looks up GwsOAuthToken
    - Decrypts access_token
    - Refreshes if expired (within 5-min buffer)
    - Returns plaintext access_token
    
    Raises ValueError if no token found or token is revoked.
    """
    async def _fetch(session: AsyncSession) -> str:
        result = await session.execute(
            select(GwsOAuthToken).where(
                GwsOAuthToken.agent_id == agent_id,
                GwsOAuthToken.user_id == user_id,
            )
        )
        token_record = result.scalar_one_or_none()
        
        if not token_record:
            raise ValueError("No OAuth token found for this agent and user")
        
        if token_record.status == "revoked":
            raise ValueError("OAuth token has been revoked")

        now = datetime.now(timezone.utc)
        if token_record.token_expiry and token_record.token_expiry <= now + timedelta(minutes=5):
            if not token_record.tenant_id:
                raise ValueError("Cannot refresh token: tenant_id is missing")
            logger.info(f"Refreshing expired GWS token for agent {agent_id}, user {user_id}")
            return await refresh_access_token(token_record, token_record.tenant_id, session)

        plaintext_token = decrypt_data(token_record.access_token, settings.SECRET_KEY)

        token_record.last_used_at = now
        session.add(token_record)
        await session.commit()

        return plaintext_token
    
    if db is not None:
        return await _fetch(db)
    async with async_session() as session:
        return await _fetch(session)


async def revoke_oauth_token(
    agent_id: uuid.UUID,
    user_id: uuid.UUID,
    db: AsyncSession | None = None,
) -> None:
    """Revoke OAuth token by calling Google's revocation endpoint.
    
    Marks token as revoked in DB.
    """
    async def _revoke(session: AsyncSession) -> None:
        result = await session.execute(
            select(GwsOAuthToken).where(
                GwsOAuthToken.agent_id == agent_id,
                GwsOAuthToken.user_id == user_id,
            )
        )
        token_record = result.scalar_one_or_none()
        
        if not token_record:
            return  # No token to revoke
        
        # Decrypt token for revocation call
        access_token = decrypt_data(token_record.access_token, settings.SECRET_KEY)
        
        # Call Google revocation endpoint
        revoke_url = f"https://oauth2.googleapis.com/revoke?token={access_token}"
        async with httpx.AsyncClient() as client:
            try:
                response = await client.post(revoke_url)
                if response.status_code != 200:
                    logger.warning(f"GWS token revocation returned {response.status_code}")
            except Exception as e:
                logger.error(f"Failed to revoke GWS token: {e}")
        
        # Mark as revoked in DB
        token_record.status = "revoked"
        session.add(token_record)
        await session.commit()
    
    if db is not None:
        return await _revoke(db)
    async with async_session() as session:
        return await _revoke(session)


async def list_agent_oauth_accounts(
    agent_id: uuid.UUID,
    db: AsyncSession | None = None,
) -> list[dict]:
    """List all OAuth accounts for an agent (never exposes raw tokens).
    
    Returns list of dicts with google_email, status, scopes, authorized_at, last_used_at.
    """
    async def _list(session: AsyncSession) -> list[dict]:
        result = await session.execute(
            select(GwsOAuthToken)
            .where(GwsOAuthToken.agent_id == agent_id)
            .order_by(GwsOAuthToken.created_at.desc())
        )
        tokens = result.scalars().all()
        
        return [
            {
                "google_email": token.google_email,
                "status": token.status,
                "scopes": token.scopes or [],
                "authorized_at": token.created_at.isoformat() if token.created_at else None,
                "last_used_at": token.last_used_at.isoformat() if token.last_used_at else None,
            }
            for token in tokens
        ]
    
    if db is not None:
        return await _list(db)
    async with async_session() as session:
        return await _list(session)


def parse_id_token_email(id_token: str) -> str | None:
    """Extract email from Google ID token JWT payload.
    
    Simple parsing without full JWT verification (we trust Google's response).
    """
    try:
        # JWT format: header.payload.signature
        parts = id_token.split(".")
        if len(parts) != 3:
            return None
        
        # Decode payload (middle part)
        payload_b64 = parts[1]
        # Add padding if needed
        padding = 4 - len(payload_b64) % 4
        if padding != 4:
            payload_b64 += "=" * padding
        
        payload_json = base64.b64decode(payload_b64).decode("utf-8")
        payload = json.loads(payload_json)
        
        return payload.get("email")
    except Exception as e:
        logger.error(f"Failed to parse ID token: {e}")
        return None
