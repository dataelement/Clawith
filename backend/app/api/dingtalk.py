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
    image_base64_list: list[str] | None = None,
    saved_file_paths: list[str] | None = None,
    sender_nick: str = "",
    message_id: str = "",
):
    """Process an incoming DingTalk bot message and reply via session webhook.

    Args:
        image_base64_list: List of base64-encoded image data URIs for vision LLM.
        saved_file_paths: List of local file paths where media files were saved.
        sender_nick: Display name of the sender from DingTalk.
        message_id: DingTalk message ID (used for reactions).
    """
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
        sender_staff_id = (sender_staff_id or "").strip()

        # Load agent
        agent_r = await db.execute(_select(AgentModel).where(AgentModel.id == agent_id))
        agent_obj = agent_r.scalar_one_or_none()
        if not agent_obj:
            logger.warning(f"[DingTalk] Agent {agent_id} not found")
            return
        if not sender_staff_id:
            logger.warning("[DingTalk] Skip message attribution because sender_staff_id is empty")
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

        # Resolve channel user via unified service (uses OrgMember + SSO patterns)
        platform_user = await channel_user_service.resolve_channel_user(
            db=db,
            agent=agent_obj,
            channel_type="dingtalk",
            external_user_id=sender_staff_id,
            extra_info={},
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

        # Build saved_content for DB (no base64 blobs, keep it display-friendly)
        import re as _re_dt
        _clean_text = _re_dt.sub(
            r'\[image_data:data:image/[^;]+;base64,[A-Za-z0-9+/=]+\]',
            "", user_text,
        ).strip()
        if saved_file_paths:
            from pathlib import Path as _PathDT
            _file_prefixes = "\n".join(
                f"[file:{_PathDT(p).name}]" for p in saved_file_paths
            )
            saved_content = f"{_file_prefixes}\n{_clean_text}".strip() if _clean_text else _file_prefixes
        else:
            saved_content = _clean_text or user_text

        # Save user message
        db.add(ChatMessage(
            agent_id=agent_id, user_id=platform_user_id,
            role="user", content=saved_content,
            conversation_id=session_conv_id,
        ))
        sess.last_message_at = datetime.now(timezone.utc)
        await db.commit()

        # Build LLM input text: for images, inject base64 markers so vision models can see them
        llm_user_text = user_text
        if image_base64_list:
            image_markers = "\n".join(
                f"[image_data:{uri}]" for uri in image_base64_list
            )
            llm_user_text = f"{user_text}\n{image_markers}" if user_text else image_markers

        # ── Set up channel_file_sender so the agent can send files via DingTalk ──
        from app.services.agent_tools import channel_file_sender as _cfs
        from app.services.dingtalk_stream import (
            _upload_dingtalk_media,
            _send_dingtalk_media_message,
        )

        # Load DingTalk credentials from ChannelConfig
        _dt_cfg_r = await db.execute(
            _select(ChannelConfig).where(
                ChannelConfig.agent_id == agent_id,
                ChannelConfig.channel_type == "dingtalk",
            )
        )
        _dt_cfg = _dt_cfg_r.scalar_one_or_none()
        _dt_app_key = _dt_cfg.app_id if _dt_cfg else None
        _dt_app_secret = _dt_cfg.app_secret if _dt_cfg else None

        _cfs_token = None
        if _dt_app_key and _dt_app_secret:
            # Determine send target: group -> conversation_id, P2P -> sender_staff_id
            _dt_target_id = conversation_id if conversation_type == "2" else sender_staff_id
            _dt_conv_type = conversation_type

            async def _dingtalk_file_sender(file_path: str, msg: str = ""):
                """Send a file/image/video via DingTalk proactive message API."""
                from pathlib import Path as _P

                _fp = _P(file_path)
                _ext = _fp.suffix.lower()

                # Determine media type from extension
                if _ext in (".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp"):
                    _media_type = "image"
                elif _ext in (".mp4", ".mov", ".avi", ".mkv"):
                    _media_type = "video"
                elif _ext in (".mp3", ".wav", ".ogg", ".amr", ".m4a"):
                    _media_type = "voice"
                else:
                    _media_type = "file"

                # Upload media to DingTalk
                _mid = await _upload_dingtalk_media(
                    _dt_app_key, _dt_app_secret, file_path, _media_type
                )

                if _mid:
                    # Send via proactive message API
                    _ok = await _send_dingtalk_media_message(
                        _dt_app_key, _dt_app_secret,
                        _dt_target_id, _mid, _media_type,
                        _dt_conv_type, filename=_fp.name,
                    )
                    if _ok:
                        # Also send accompany text if provided
                        if msg:
                            try:
                                async with httpx.AsyncClient(timeout=10) as _cl:
                                    await _cl.post(session_webhook, json={
                                        "msgtype": "text",
                                        "text": {"content": msg},
                                    })
                            except Exception:
                                pass
                        return

                # Fallback: send a text message with file info
                _fallback_parts = []
                if msg:
                    _fallback_parts.append(msg)
                _fallback_parts.append(f"[File: {_fp.name}]")
                try:
                    async with httpx.AsyncClient(timeout=10) as _cl:
                        await _cl.post(session_webhook, json={
                            "msgtype": "text",
                            "text": {"content": "\n\n".join(_fallback_parts)},
                        })
                except Exception as _fb_err:
                    logger.error(f"[DingTalk] Fallback file text also failed: {_fb_err}")

            _cfs_token = _cfs.set(_dingtalk_file_sender)

        # Call LLM
        try:
            reply_text = await _call_agent_llm(
                db, agent_id, llm_user_text,
                history=history, user_id=platform_user_id,
            )
        finally:
            # Reset ContextVar
            if _cfs_token is not None:
                _cfs.reset(_cfs_token)
            # Recall thinking reaction (before sending reply)
            if message_id and _dt_app_key:
                try:
                    from app.services.dingtalk_reaction import recall_thinking_reaction
                    await recall_thinking_reaction(
                        _dt_app_key, _dt_app_secret,
                        message_id, conversation_id,
                    )
                except Exception as _recall_err:
                    logger.warning(f"[DingTalk] Failed to recall thinking reaction: {_recall_err}")

        has_media = bool(image_base64_list or saved_file_paths)
        logger.info(
            f"[DingTalk] LLM reply ({'media' if has_media else 'text'} input): "
            f"{reply_text[:100]}"
        )

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
