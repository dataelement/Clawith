import os
import uuid
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, status
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
        # Include token and user data once
        from app.models.user import User
        user_result = await db.execute(select(User).where(User.id == session.user_id))
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
async def get_sso_config(sid: uuid.UUID, db: AsyncSession = Depends(get_db)):
    """List active SSO providers with their redirect URLs for the specified session ID."""
    # 1. Resolve session to get tenant context
    res = await db.execute(select(SSOScanSession).where(SSOScanSession.id == sid))
    session = res.scalar_one_or_none()
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
        
    # 2. Query IdentityProviders for this tenant (only those that are active)
    query = select(IdentityProvider).where(IdentityProvider.is_active == True)
    if session.tenant_id:
        query = query.where(IdentityProvider.tenant_id == session.tenant_id)
    else:
        # Fallback to global/unscoped if session has no tenant_id
        # In a fully isolated system, this might return empty results
        query = query.where(IdentityProvider.tenant_id.is_(None))

    result = await db.execute(query)
    providers = result.scalars().all()
    
    # Base URL for callbacks (adjust to your platform's public address)
    public_base = os.environ.get("PUBLIC_BASE_URL", "http://localhost:8000").rstrip("/")
    
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

    return auth_urls

