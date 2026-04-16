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


# ─── DingTalk Corp API helpers ──────────────────────────

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
    from app.services.dingtalk_token import dingtalk_token_manager

    try:
        access_token = await dingtalk_token_manager.get_corp_token(app_key, app_secret)
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
                "name": result.get("name", ""),
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
    sender_nick: str = "",
    sender_id: str = "",
):
    """Process an incoming DingTalk bot message and reply via session webhook."""
    import json
    import httpx
    from datetime import datetime, timezone
    from sqlalchemy import select as _select
    from app.database import async_session
    from app.models.agent import Agent as AgentModel
    from app.models.audit import ChatMessage
    from app.services.channel_session import find_or_create_channel_session
    from app.services.channel_user_service import channel_user_service
    from app.api.feishu import _call_agent_llm

    async with async_session() as db:
        # Load agent
        agent_r = await db.execute(_select(AgentModel).where(AgentModel.id == agent_id))
        agent_obj = agent_r.scalar_one_or_none()
        if not agent_obj:
            logger.warning(f"[DingTalk] Agent {agent_id} not found")
            return
        creator_id = agent_obj.creator_id
        from app.models.agent import DEFAULT_CONTEXT_WINDOW_SIZE
        ctx_size = (agent_obj.context_window_size or DEFAULT_CONTEXT_WINDOW_SIZE) if agent_obj else DEFAULT_CONTEXT_WINDOW_SIZE

        # Determine conv_id for session isolation
        if conversation_type == "2":
            # Group chat
            conv_id = f"dingtalk_group_{conversation_id}"
        else:
            # P2P / single chat
            conv_id = f"dingtalk_p2p_{sender_staff_id}"

        # Build extra_info for user resolution using senderStaffId as primary key.
        # Try to enrich with corp API data (unionid/mobile/email) for better
        # cross-channel matching when the basic OrgMember lookup fails.
        extra_info: dict = {"name": sender_nick} if sender_nick else {}

        # Load channel config for corp API calls
        _cfg_r = await db.execute(
            _select(ChannelConfig).where(
                ChannelConfig.agent_id == agent_id,
                ChannelConfig.channel_type == "dingtalk",
            )
        )
        _cfg = _cfg_r.scalar_one_or_none()

        if _cfg and _cfg.app_id and _cfg.app_secret:
            dt_detail = await _get_dingtalk_user_detail(
                _cfg.app_id, _cfg.app_secret, sender_staff_id
            )
            if dt_detail:
                if dt_detail.get("unionid"):
                    extra_info["unionid"] = dt_detail["unionid"]
                if dt_detail.get("mobile"):
                    extra_info["mobile"] = dt_detail["mobile"]
                if dt_detail.get("email"):
                    extra_info["email"] = dt_detail["email"]
                if dt_detail.get("name") and not sender_nick:
                    extra_info["name"] = dt_detail["name"]
                logger.debug(
                    f"[DingTalk] Enriched user detail for {sender_staff_id}: "
                    f"unionid={dt_detail.get('unionid', '')[:8]}..."
                )

        # Resolve channel user via unified service (uses OrgMember + SSO patterns).
        # senderStaffId is the stable enterprise userId — used as external_user_id.
        platform_user = await channel_user_service.resolve_channel_user(
            db=db,
            agent=agent_obj,
            channel_type="dingtalk",
            external_user_id=sender_staff_id,
            extra_info=extra_info,
        )
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
