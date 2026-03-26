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
    """Configure DingTalk bot for an agent. Fields: app_key, app_secret."""
    agent, _ = await check_agent_access(db, current_user, agent_id)
    if not is_agent_creator(current_user, agent):
        raise HTTPException(status_code=403, detail="Only creator can configure channel")

    app_key = data.get("app_key", "").strip()
    app_secret = data.get("app_secret", "").strip()
    if not app_key or not app_secret:
        raise HTTPException(status_code=422, detail="app_key and app_secret are required")

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
        await db.commit()
        # Restart Stream client
        from app.services.dingtalk_stream import dingtalk_stream_manager
        import asyncio
        asyncio.create_task(dingtalk_stream_manager.start_client(agent_id, app_key, app_secret))
        return ChannelConfigOut.model_validate(existing)

    config = ChannelConfig(
        agent_id=agent_id,
        channel_type="dingtalk",
        app_id=app_key,
        app_secret=app_secret,
        is_configured=True,
    )
    db.add(config)
    await db.commit()

    # Start Stream client
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
    """
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

        # Find or create platform user
        dt_username = f"dingtalk_{sender_staff_id}"
        u_r = await db.execute(_select(UserModel).where(UserModel.username == dt_username))
        platform_user = u_r.scalar_one_or_none()
        if not platform_user:
            import uuid as _uuid
            platform_user = UserModel(
                username=dt_username,
                email=f"{dt_username}@dingtalk.local",
                password_hash=hash_password(_uuid.uuid4().hex),
                display_name=sender_nick or f"DingTalk {sender_staff_id[:8]}",
                role="member",
                tenant_id=agent_obj.tenant_id if agent_obj else None,
                source="dingtalk",
            )
            db.add(platform_user)
            await db.flush()
        else:
            # Update display_name and source for existing users
            updated = False
            if sender_nick and platform_user.display_name != sender_nick:
                platform_user.display_name = sender_nick
                updated = True
            if not platform_user.source or platform_user.source == "web":
                platform_user.source = "dingtalk"
                updated = True
            if updated:
                await db.flush()
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
            # Determine send target: group → conversation_id, P2P → sender_staff_id
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

                # Fallback: send a text message with download link
                from pathlib import Path as _P2
                from app.config import get_settings as _gs_fallback
                _fs = _gs_fallback()
                _base_url = getattr(_fs, 'BASE_URL', '').rstrip('/') or ''
                _fp2 = _P2(file_path)
                _ws_root = _P2(_fs.AGENT_DATA_DIR)
                try:
                    _rel = str(_fp2.relative_to(_ws_root / str(agent_id)))
                except ValueError:
                    _rel = _fp2.name
                _fallback_parts = []
                if msg:
                    _fallback_parts.append(msg)
                if _base_url:
                    _dl_url = f"{_base_url}/api/agents/{agent_id}/files/download?path={_rel}"
                    _fallback_parts.append(f"📎 {_fp2.name}\n🔗 {_dl_url}")
                _fallback_parts.append("⚠️ 文件通过钉钉直接发送失败，请通过上方链接下载。")
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
                db, agent_id, user_text,
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
            f"[DingTalk] LLM reply ({('media' if has_media else 'text')} input): "
            f"{reply_text[:100]}"
        )

        # Reply via session webhook (markdown)
        # Note: File/image sending is handled by channel_file_sender ContextVar above.
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
