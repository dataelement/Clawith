"""DingTalk Channel API routes.

Provides Config CRUD and message handling for DingTalk bots using Stream mode.
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.permissions import check_agent_access, is_agent_creator
from app.core.security import get_current_user
from app.database import get_db
from app.models.channel_config import ChannelConfig
from app.models.user import User
from app.schemas.schemas import ChannelConfigOut

router = APIRouter(tags=["dingtalk"])

# --- DingTalk Corp API helpers -----------------------------------------
import time as _time

_corp_token_cache: dict[str, tuple[str, float]] = {}  # {app_key: (token, expire_ts)}


async def _get_corp_access_token(app_key: str, app_secret: str) -> str | None:
    """Get corp access_token with in-memory cache (2h validity, refresh 5min early)."""
    import httpx

    cached = _corp_token_cache.get(app_key)
    if cached and cached[1] > _time.time():
        return cached[0]

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                "https://oapi.dingtalk.com/gettoken",
                params={"appkey": app_key, "appsecret": app_secret},
            )
            data = resp.json()
            token = data.get("access_token")
            expires_in = data.get("expires_in", 7200)
            if not token:
                logger.warning(f"[DingTalk] Failed to get corp access_token: {data}")
                return None
            _corp_token_cache[app_key] = (token, _time.time() + expires_in - 300)
            return token
    except Exception as e:
        logger.warning(f"[DingTalk] _get_corp_access_token error: {e}")
        return None


async def _get_dingtalk_user_detail(
    app_key: str,
    app_secret: str,
    staff_id: str,
) -> dict | None:
    """Query DingTalk user detail via corp API to get unionId/mobile/email.

    Uses /topapi/v2/user/get, requires contact.user.read permission.
    Returns None on failure (graceful degradation).
    """
    import httpx

    try:
        access_token = await _get_corp_access_token(app_key, app_secret)
        if not access_token:
            return None

        async with httpx.AsyncClient(timeout=10) as client:
            user_resp = await client.post(
                "https://oapi.dingtalk.com/topapi/v2/user/get",
                params={"access_token": access_token},
                json={"userid": staff_id, "language": "zh_CN"},
            )
            user_data = user_resp.json()

            if user_data.get("errcode") != 0:
                logger.warning(
                    f"[DingTalk] /topapi/v2/user/get failed for {staff_id}: "
                    f"errcode={user_data.get('errcode')} errmsg={user_data.get('errmsg')}"
                )
                return None

            result = user_data.get("result", {})
            return {
                "unionid": result.get("unionid", ""),
                "mobile": result.get("mobile", ""),
                "email": result.get("email", "") or result.get("org_email", ""),
            }

    except Exception as e:
        logger.warning(f"[DingTalk] _get_dingtalk_user_detail error for {staff_id}: {e}")
        return None



# ─── Config CRUD ────────────────────────────────────────

@router.post("/agents/{agent_id}/dingtalk-channel", response_model=ChannelConfigOut, status_code=201)
async def configure_dingtalk_channel(
    agent_id: uuid.UUID,
    data: dict,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Configure DingTalk bot for an agent. Fields: app_key, app_secret, agent_id (optional)."""
    agent, _ = await check_agent_access(db, current_user, agent_id)
    if not is_agent_creator(current_user, agent):
        raise HTTPException(status_code=403, detail="Only creator can configure channel")

    app_key = data.get("app_key", "").strip()
    app_secret = data.get("app_secret", "").strip()
    if not app_key or not app_secret:
        raise HTTPException(status_code=422, detail="app_key and app_secret are required")

    # Handle connection mode (Stream/WebSocket vs Webhook) and agent_id
    extra_config = data.get("extra_config", {})
    conn_mode = extra_config.get("connection_mode", "websocket")
    dingtalk_agent_id = extra_config.get("agent_id", "")  # DingTalk AgentId for API messaging

    result = await db.execute(
        select(ChannelConfig).where(
            ChannelConfig.agent_id == agent_id,
            ChannelConfig.channel_type == "dingtalk",
        )
    )
    existing = result.scalar_one_or_none()
    if existing:
        existing.app_id = app_key
        existing.app_secret = app_secret
        existing.is_configured = True
        existing.extra_config = {**existing.extra_config, "connection_mode": conn_mode, "agent_id": dingtalk_agent_id}
        await db.flush()
        
        # Restart Stream client if in websocket mode
        if conn_mode == "websocket":
            from app.services.dingtalk_stream import dingtalk_stream_manager
            import asyncio
            asyncio.create_task(dingtalk_stream_manager.start_client(agent_id, app_key, app_secret))
        else:
            # Stop existing Stream client if switched to webhook
            from app.services.dingtalk_stream import dingtalk_stream_manager
            import asyncio
            asyncio.create_task(dingtalk_stream_manager.stop_client(agent_id))
            
        return ChannelConfigOut.model_validate(existing)

    config = ChannelConfig(
        agent_id=agent_id,
        channel_type="dingtalk",
        app_id=app_key,
        app_secret=app_secret,
        is_configured=True,
        extra_config={"connection_mode": conn_mode},
    )
    db.add(config)
    await db.flush()

    # Start Stream client if in websocket mode
    if conn_mode == "websocket":
        from app.services.dingtalk_stream import dingtalk_stream_manager
        import asyncio
        asyncio.create_task(dingtalk_stream_manager.start_client(agent_id, app_key, app_secret))

    return ChannelConfigOut.model_validate(config)


@router.get("/agents/{agent_id}/dingtalk-channel", response_model=ChannelConfigOut)
async def get_dingtalk_channel(
    agent_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await check_agent_access(db, current_user, agent_id)
    result = await db.execute(
        select(ChannelConfig).where(
            ChannelConfig.agent_id == agent_id,
            ChannelConfig.channel_type == "dingtalk",
        )
    )
    config = result.scalar_one_or_none()
    if not config:
        raise HTTPException(status_code=404, detail="DingTalk not configured")
    return ChannelConfigOut.model_validate(config)


@router.delete("/agents/{agent_id}/dingtalk-channel", status_code=204)
async def delete_dingtalk_channel(
    agent_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    agent, _ = await check_agent_access(db, current_user, agent_id)
    if not is_agent_creator(current_user, agent):
        raise HTTPException(status_code=403, detail="Only creator can remove channel")
    result = await db.execute(
        select(ChannelConfig).where(
            ChannelConfig.agent_id == agent_id,
            ChannelConfig.channel_type == "dingtalk",
        )
    )
    config = result.scalar_one_or_none()
    if not config:
        raise HTTPException(status_code=404, detail="DingTalk not configured")
    await db.delete(config)

    # Stop Stream client
    from app.services.dingtalk_stream import dingtalk_stream_manager
    import asyncio
    asyncio.create_task(dingtalk_stream_manager.stop_client(agent_id))


# ─── Message Processing (called by Stream callback) ────

async def process_dingtalk_message(
    agent_id: uuid.UUID,
    sender_staff_id: str,
    user_text: str,
    conversation_id: str,
    conversation_type: str,
    session_webhook: str,
):
    """Process an incoming DingTalk bot message and reply via session webhook."""
    import json
    import httpx
    from datetime import datetime, timezone
    from sqlalchemy import select as _select
    from app.database import async_session
    from app.models.agent import Agent as AgentModel
    from app.models.audit import ChatMessage
    from app.models.user import User as UserModel
    from app.core.security import hash_password
    from app.services.channel_session import find_or_create_channel_session
    from app.api.feishu import _call_agent_llm

    async with async_session() as db:
        # Load agent
        agent_r = await db.execute(_select(AgentModel).where(AgentModel.id == agent_id))
        agent_obj = agent_r.scalar_one_or_none()
        if not agent_obj:
            logger.warning(f"[DingTalk] Agent {agent_id} not found")
            return
        creator_id = agent_obj.creator_id
        ctx_size = agent_obj.context_window_size if agent_obj else 20

        # Determine conv_id for session isolation
        if conversation_type == "2":
            # Group chat
            conv_id = f"dingtalk_group_{conversation_id}"
        else:
            # P2P / single chat
            conv_id = f"dingtalk_p2p_{sender_staff_id}"

        # -- Load ChannelConfig early for DingTalk corp API calls --
        _early_cfg_r = await db.execute(
            _select(ChannelConfig).where(
                ChannelConfig.agent_id == agent_id,
                ChannelConfig.channel_type == "dingtalk",
            )
        )
        _early_cfg = _early_cfg_r.scalar_one_or_none()
        _early_app_key = _early_cfg.app_id if _early_cfg else None
        _early_app_secret = _early_cfg.app_secret if _early_cfg else None

        # -- Multi-dimension user matching to prevent duplicate creation --
        dt_username = f"dingtalk_{sender_staff_id}"

        # Step 1: Exact username match (backward compatible)
        u_r = await db.execute(_select(UserModel).where(UserModel.username == dt_username))
        platform_user = u_r.scalar_one_or_none()

        if not platform_user and _early_app_key and _early_app_secret:
            # Step 2: Call DingTalk corp API to get unionId/mobile/email
            dt_user_detail = await _get_dingtalk_user_detail(
                _early_app_key, _early_app_secret, sender_staff_id
            )

            if dt_user_detail:
                dt_unionid = dt_user_detail.get("unionid", "")
                dt_mobile = dt_user_detail.get("mobile", "")
                dt_email = dt_user_detail.get("email", "")

                # Step 3: Match via org_members unionId (SSO users)
                if dt_unionid and not platform_user:
                    from app.models.org import OrgMember
                    from app.models.identity import IdentityProvider
                    from sqlalchemy import or_ as _or
                    _ip_r = await db.execute(
                        _select(IdentityProvider).where(
                            IdentityProvider.provider_type == "dingtalk",
                            IdentityProvider.tenant_id == agent_obj.tenant_id,
                        )
                    )
                    _ip = _ip_r.scalar_one_or_none()
                    if _ip:
                        _om_r = await db.execute(
                            _select(OrgMember).where(
                                OrgMember.provider_id == _ip.id,
                                OrgMember.status == "active",
                                _or(
                                    OrgMember.unionid == dt_unionid,
                                    OrgMember.external_id == dt_unionid,
                                ),
                            )
                        )
                        _om = _om_r.scalar_one_or_none()
                        if _om and _om.user_id:
                            _u_r = await db.execute(
                                _select(UserModel).where(UserModel.id == _om.user_id)
                            )
                            platform_user = _u_r.scalar_one_or_none()
                            if platform_user:
                                logger.info(
                                    f"[DingTalk] Matched user via org_members unionid "
                                    f"{dt_unionid}: {platform_user.username}"
                                )

                # Step 4: Match via mobile
                if dt_mobile and not platform_user:
                    _u_r = await db.execute(
                        _select(UserModel).where(
                            UserModel.primary_mobile == dt_mobile,
                            UserModel.tenant_id == agent_obj.tenant_id,
                        )
                    )
                    platform_user = _u_r.scalar_one_or_none()
                    if platform_user:
                        logger.info(
                            f"[DingTalk] Matched user via mobile {dt_mobile}: "
                            f"{platform_user.username}"
                        )

                # Step 5: Match via email
                if dt_email and not platform_user:
                    _u_r = await db.execute(
                        _select(UserModel).where(
                            UserModel.email == dt_email,
                            UserModel.tenant_id == agent_obj.tenant_id,
                        )
                    )
                    platform_user = _u_r.scalar_one_or_none()
                    if platform_user:
                        logger.info(
                            f"[DingTalk] Matched user via email {dt_email}: "
                            f"{platform_user.username}"
                        )

        if not platform_user:
            # Step 6: No match found, create new user
            import uuid as _uuid
            platform_user = UserModel(
                username=dt_username,
                email=f"{dt_username}@dingtalk.local",
                password_hash=hash_password(_uuid.uuid4().hex),
                display_name=f"DingTalk {sender_staff_id[:8]}",
                role="member",
                tenant_id=agent_obj.tenant_id if agent_obj else None,
            )
            db.add(platform_user)
            await db.flush()
            logger.info(f"[DingTalk] Created new user: {dt_username}")

        platform_user_id = platform_user.id

        # Find or create session
        sess = await find_or_create_channel_session(
            db=db,
            agent_id=agent_id,
            user_id=platform_user_id,
            external_conv_id=conv_id,
            source_channel="dingtalk",
            first_message_title=user_text,
        )
        session_conv_id = str(sess.id)

        # Load history
        history_r = await db.execute(
            _select(ChatMessage)
            .where(ChatMessage.agent_id == agent_id, ChatMessage.conversation_id == session_conv_id)
            .order_by(ChatMessage.created_at.desc())
            .limit(ctx_size)
        )
        history = [{"role": m.role, "content": m.content} for m in reversed(history_r.scalars().all())]

        # Save user message
        db.add(ChatMessage(
            agent_id=agent_id, user_id=platform_user_id,
            role="user", content=user_text,
            conversation_id=session_conv_id,
        ))
        sess.last_message_at = datetime.now(timezone.utc)
        await db.commit()

        # Call LLM
        reply_text = await _call_agent_llm(
            db, agent_id, user_text,
            history=history, user_id=platform_user_id,
        )
        logger.info(f"[DingTalk] LLM reply: {reply_text[:100]}")

        # Reply via session webhook (markdown)
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                await client.post(session_webhook, json={
                    "msgtype": "markdown",
                    "markdown": {
                        "title": agent_obj.name or "AI Reply",
                        "text": reply_text,
                    },
                })
        except Exception as e:
            logger.error(f"[DingTalk] Failed to reply via webhook: {e}")
            # Fallback: try plain text
            try:
                async with httpx.AsyncClient(timeout=10) as client:
                    await client.post(session_webhook, json={
                        "msgtype": "text",
                        "text": {"content": reply_text},
                    })
            except Exception as e2:
                logger.error(f"[DingTalk] Fallback text reply also failed: {e2}")

        # Save assistant reply
        db.add(ChatMessage(
            agent_id=agent_id, user_id=platform_user_id,
            role="assistant", content=reply_text,
            conversation_id=session_conv_id,
        ))
        sess.last_message_at = datetime.now(timezone.utc)
        await db.commit()

        # Log activity
        from app.services.activity_logger import log_activity
        await log_activity(
            agent_id, "chat_reply",
            f"Replied to DingTalk message: {reply_text[:80]}",
            detail={"channel": "dingtalk", "user_text": user_text[:200], "reply": reply_text[:500]},
        )


# ─── OAuth Callback (SSO) ──────────────────────────────

@router.get("/auth/dingtalk/callback")
async def dingtalk_callback(
    authCode: str, # DingTalk uses authCode parameter
    state: str = None,
    db: AsyncSession = Depends(get_db),
):
    """Callback for DingTalk OAuth2 login."""
    from app.models.identity import SSOScanSession
    from app.core.security import create_access_token
    from fastapi.responses import HTMLResponse
    from app.services.auth_registry import auth_provider_registry

    # 1. Resolve session to get tenant context
    tenant_id = None
    if state:
        try:
            sid = uuid.UUID(state)
            s_res = await db.execute(select(SSOScanSession).where(SSOScanSession.id == sid))
            session = s_res.scalar_one_or_none()
            if session:
                tenant_id = session.tenant_id
        except (ValueError, AttributeError):
            pass

    # 2. Get DingTalk provider config
    auth_provider = await auth_provider_registry.get_provider(db, "dingtalk", str(tenant_id) if tenant_id else None)
    if not auth_provider:
        return HTMLResponse("Auth failed: DingTalk provider not configured for this tenant")

    # 3. Exchange code for token and get user info
    try:
        # Step 1: Exchange authCode for userAccessToken
        token_data = await auth_provider.exchange_code_for_token(authCode)
        access_token = token_data.get("access_token")
        if not access_token:
            logger.error(f"DingTalk token exchange failed: {token_data}")
            return HTMLResponse(f"Auth failed: Token exchange error")

        # Step 2: Get user info using modern v1.0 API
        user_info = await auth_provider.get_user_info(access_token)
        if not user_info.provider_union_id:
            logger.error(f"DingTalk user info missing unionId: {user_info.raw_data}")
            return HTMLResponse("Auth failed: No unionid returned")

        # Step 3: Find or create user (handles OrgMember linking)
        user, is_new = await auth_provider.find_or_create_user(
            db, user_info, tenant_id=str(tenant_id) if tenant_id else None
        )
        if not user:
            return HTMLResponse("Auth failed: User resolution failed")

    except Exception as e:
        logger.error(f"DingTalk login error: {e}")
        return HTMLResponse(f"Auth failed: {str(e)}")

    # 4. Standard login
    token = create_access_token(str(user.id), user.role)

    if state:
        try:
            sid = uuid.UUID(state)
            s_res = await db.execute(select(SSOScanSession).where(SSOScanSession.id == sid))
            session = s_res.scalar_one_or_none()
            if session:
                session.status = "authorized"
                session.provider_type = "dingtalk"
                session.user_id = user.id
                session.access_token = token
                session.error_msg = None
                await db.commit()
                return HTMLResponse(
                    f"""<html><head><meta charset="utf-8" /></head>
                    <body style="font-family: sans-serif; padding: 24px;">
                        <div>SSO login successful. Redirecting...</div>
                        <script>window.location.href = "/sso/entry?sid={sid}&complete=1";</script>
                    </body></html>"""
                )
        except Exception as e:
            logger.exception("Failed to update SSO session (dingtalk) %s", e)

    return HTMLResponse(f"Logged in. Token: {token}")
