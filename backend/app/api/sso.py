import os
import uuid
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request, status
from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.identity import SSOScanSession, IdentityProvider
from app.schemas.schemas import TokenResponse, UserOut

router = APIRouter(tags=["sso"])

@router.post("/sso/session")
async def create_sso_session(
    tenant_id: uuid.UUID | None = None,
    db: AsyncSession = Depends(get_db)
):
    """Create a new SSO scan session for QR code login."""
    session = SSOScanSession(
        id=uuid.uuid4(),
        status="pending",
        tenant_id=tenant_id,
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=5)
    )
    db.add(session)
    await db.commit()
    return {"session_id": str(session.id), "expires_at": session.expires_at}

@router.get("/sso/session/{sid}/status")
async def get_sso_session_status(sid: uuid.UUID, db: AsyncSession = Depends(get_db)):
    """Check the status of an SSO scan session."""
    result = await db.execute(select(SSOScanSession).where(SSOScanSession.id == sid))
    session = result.scalar_one_or_none()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    
    if session.expires_at < datetime.now(timezone.utc):
        session.status = "expired"
        await db.commit()

    response = {
        "status": session.status,
        "provider_type": session.provider_type,
        "error_msg": session.error_msg
    }
    
    if session.status == "authorized" and session.access_token:
        # Include token and user data once.
        # Must eagerly load the identity relationship because UserOut reads
        # hybrid properties (username, email, etc.) that proxy to Identity.
        from app.models.user import User
        from sqlalchemy.orm import selectinload
        user_result = await db.execute(
            select(User)
            .where(User.id == session.user_id)
            .options(selectinload(User.identity))
        )
        user = user_result.scalar_one_or_none()
        
        response["access_token"] = session.access_token
        if user:
            response["user"] = UserOut.model_validate(user).model_dump()
            
        # Mark as completed so it can't be reused
        session.status = "completed"
        await db.commit()
        
    return response

@router.put("/sso/session/{sid}/scan")
async def mark_sso_session_scanned(sid: uuid.UUID, db: AsyncSession = Depends(get_db)):
    """Optional: Mark session as 'scanned' when the landing page loads on mobile."""
    result = await db.execute(select(SSOScanSession).where(SSOScanSession.id == sid))
    session = result.scalar_one_or_none()
    if session and session.status == "pending":
        session.status = "scanned"
        await db.commit()
    return {"status": "ok"}

@router.get("/sso/config")
async def get_sso_config(sid: uuid.UUID, request: Request, db: AsyncSession = Depends(get_db)):
    """List active SSO providers with their redirect URLs for the specified session ID."""
    # 1. Resolve session to get tenant context
    res = await db.execute(select(SSOScanSession).where(SSOScanSession.id == sid))
    session = res.scalar_one_or_none()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
        
    # 2. Query IdentityProviders for this tenant (only those that are active AND SSO-enabled)
    query = select(IdentityProvider).where(
        IdentityProvider.is_active == True,
        IdentityProvider.sso_login_enabled == True,
    )
    if session.tenant_id:
        query = query.where(IdentityProvider.tenant_id == session.tenant_id)
    else:
        # Fallback to global/unscoped if session has no tenant_id
        # In a fully isolated system, this might return empty results
        query = query.where(IdentityProvider.tenant_id.is_(None))

    result = await db.execute(query)
    providers = result.scalars().all()
    
    # Determine the base URL for OAuth callbacks using centralized platform service:
    from app.services.platform_service import platform_service
    if session.tenant_id:
        from app.models.tenant import Tenant
        tenant_result = await db.execute(select(Tenant).where(Tenant.id == session.tenant_id))
        tenant_obj = tenant_result.scalar_one_or_none()
        public_base = await platform_service.get_tenant_sso_base_url(db, tenant_obj, request)
    else:
        public_base = await platform_service.get_public_base_url(db, request)
    
    auth_urls = []
    for p in providers:
        if p.provider_type == "feishu":
            app_id = p.config.get("app_id")
            if app_id:
                redir = f"{public_base}/api/auth/feishu/callback"
                url = f"https://open.feishu.cn/open-apis/authen/v1/index?app_id={app_id}&redirect_uri={quote(redir)}&state={sid}"
                auth_urls.append({"provider_type": "feishu", "name": p.name, "url": url})
        
        elif p.provider_type == "dingtalk":
            from app.services.auth_registry import auth_provider_registry
            auth_provider = await auth_provider_registry.get_provider(db, "dingtalk", str(session.tenant_id) if session.tenant_id else None)
            if auth_provider:
                redir = f"{public_base}/api/auth/dingtalk/callback"
                # Use provider's standardized authorization URL
                url = await auth_provider.get_authorization_url(redir, str(sid))
                auth_urls.append({"provider_type": "dingtalk", "name": p.name, "url": url})
                
        elif p.provider_type == "wecom":
            corp_id = p.config.get("corp_id")
            agent_id = p.config.get("agent_id")
            if corp_id and agent_id:
                # Callback implemented in app/api/wecom.py
                redir = f"{public_base}/api/auth/wecom/callback"
                url = f"https://open.work.weixin.qq.com/wwopen/sso/qrConnect?appid={corp_id}&agentid={agent_id}&redirect_uri={quote(redir)}&state={sid}"
                auth_urls.append({"provider_type": "wecom", "name": p.name, "url": url})

        elif p.provider_type == "oauth2":
            from app.services.auth_registry import auth_provider_registry
            auth_provider = await auth_provider_registry.get_provider(
                db, "oauth2", str(session.tenant_id) if session.tenant_id else None
            )
            if auth_provider:
                redir = f"{public_base}/api/auth/oauth2/callback"
                url = await auth_provider.get_authorization_url(redir, str(sid))
                auth_urls.append({"provider_type": "oauth2", "name": p.name, "url": url})

    return auth_urls


@router.get("/auth/oauth2/callback")
async def oauth2_callback(
    code: str,
    state: str = None,
    db: AsyncSession = Depends(get_db),
):
    """Handle OAuth2 SSO callback -- exchange code for user session."""
    from app.core.security import create_access_token
    from fastapi.responses import HTMLResponse
    from app.services.auth_registry import auth_provider_registry

    # 1. Resolve tenant context from state (= session ID)
    tenant_id = None
    sid = None
    if state:
        try:
            sid = uuid.UUID(state)
            s_res = await db.execute(select(SSOScanSession).where(SSOScanSession.id == sid))
            session = s_res.scalar_one_or_none()
            if session:
                tenant_id = session.tenant_id
        except (ValueError, AttributeError):
            pass

    # 2. Get OAuth2 provider
    auth_provider = await auth_provider_registry.get_provider(
        db, "oauth2", str(tenant_id) if tenant_id else None
    )
    if not auth_provider:
        return HTMLResponse("Auth failed: OAuth2 provider not configured")

    # 3. Exchange code -> token -> user info -> find/create user
    try:
        token_data = await auth_provider.exchange_code_for_token(code)
        access_token = token_data.get("access_token")
        if not access_token:
            logger.error("OAuth2 token exchange returned no access_token")
            return HTMLResponse("Auth failed: token exchange error")

        user_info = await auth_provider.get_user_info(access_token)
        if not user_info.provider_user_id:
            logger.error("OAuth2 user info missing user ID")
            return HTMLResponse("Auth failed: no user ID returned")

        user, is_new = await auth_provider.find_or_create_user(
            db, user_info, tenant_id=str(tenant_id) if tenant_id else None
        )
        if not user:
            return HTMLResponse("Auth failed: user resolution failed")

    except Exception as e:
        logger.error("OAuth2 login error: %s", e)
        return HTMLResponse(f"Auth failed: {e!s}")

    # 4. Generate JWT, update SSO session
    token = create_access_token(str(user.id), user.role)

    if sid:
        try:
            s_res = await db.execute(select(SSOScanSession).where(SSOScanSession.id == sid))
            session = s_res.scalar_one_or_none()
            if session:
                session.status = "authorized"
                session.provider_type = "oauth2"
                session.user_id = user.id
                session.access_token = token
                session.error_msg = None
                await db.commit()
                return HTMLResponse(
                    '<html><head><meta charset="utf-8" /></head>'
                    '<body><div>SSO login successful. Redirecting...</div>'
                    f'<script>window.location.href = "/sso/entry?sid={sid}&complete=1";</script>'
                    '</body></html>'
                )
        except Exception as e:
            logger.exception("Failed to update SSO session (oauth2): %s", e)

    return HTMLResponse("Logged in successfully.")

