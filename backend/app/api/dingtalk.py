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


async def _get_corp_access_token(app_key: str, app_secret: str) -> str | None:
    """Get corp access_token via global DingTalkTokenManager (shared with stream/reaction)."""
    from app.services.dingtalk_token import dingtalk_token_manager
    return await dingtalk_token_manager.get_token(app_key, app_secret)


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
    await db.commit()

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


# ─── Message Dedup (防止钉钉重传导致重复处理) ─────────────

_processed_messages: dict[str, float] = {}  # {message_id: timestamp}
_dedup_check_counter: int = 0


async def _check_message_dedup(message_id: str) -> bool:
    """Check if a message_id has already been processed. Returns True if duplicate.

    Uses Redis SETNX as primary (atomic, cross-process), falls back to in-memory dict.
    """
    global _dedup_check_counter

    if not message_id:
        return False

    # Try Redis first
    try:
        from app.core.events import get_redis
        redis_client = await get_redis()
        dedup_key = f"dingtalk:dedup:{message_id}"
        # SETNX + EX: set only if not exists, expire in 300s
        was_set = await redis_client.set(dedup_key, "1", ex=300, nx=True)
        if not was_set:
            logger.info(f"[DingTalk Dedup] Duplicate message_id={message_id} (Redis)")
            return True
        return False
    except Exception:
        pass  # Redis unavailable, fall back to in-memory

    # In-memory fallback
    import time as _time_dedup
    now = _time_dedup.time()

    if message_id in _processed_messages:
        if now - _processed_messages[message_id] < 300:  # 5 minutes
            logger.info(f"[DingTalk Dedup] Duplicate message_id={message_id} (memory)")
            return True

    _processed_messages[message_id] = now

    # Periodic cleanup (every 100 checks, remove entries older than 10 minutes)
    _dedup_check_counter += 1
    if _dedup_check_counter % 100 == 0:
        cutoff = now - 600
        expired = [k for k, v in _processed_messages.items() if v < cutoff]
        for k in expired:
            del _processed_messages[k]
        if expired:
            logger.debug(f"[DingTalk Dedup] Cleaned {len(expired)} expired entries")

    return False


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
    sender_id: str = "",
):
    """Process an incoming DingTalk bot message and reply via session webhook.

    Args:
        image_base64_list: List of base64-encoded image data URIs for vision LLM.
        saved_file_paths: List of local file paths where media files were saved.
    """
    # ── Dedup check: skip if this message_id was already processed ──
    if await _check_message_dedup(message_id):
        logger.info(f"[DingTalk] Skipping duplicate message_id={message_id}")
        return

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
        ctx_size = agent_obj.context_window_size if agent_obj else 100

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

        # -- Multi-dimension user matching (optimized: local-first, API-last) --
        from app.models.org import OrgMember
        from app.models.identity import IdentityProvider
        from sqlalchemy import or_ as _or

        dt_username = f"dingtalk_{sender_staff_id}"
        platform_user = None
        dt_unionid = ""
        matched_via = ""

        # Find the DingTalk identity provider for this tenant
        _ip_r = await db.execute(
            _select(IdentityProvider).where(
                IdentityProvider.provider_type == "dingtalk",
                IdentityProvider.tenant_id == agent_obj.tenant_id,
            )
        )
        _dingtalk_provider = _ip_r.scalar_one_or_none()

        # Step 1: Match via sender_staff_id in org_members.external_id (企业 userId，最稳定)
        if sender_staff_id and _dingtalk_provider and not platform_user:
            _om_r = await db.execute(
                _select(OrgMember).where(
                    OrgMember.provider_id == _dingtalk_provider.id,
                    OrgMember.external_id == sender_staff_id,
                    OrgMember.status == "active",
                )
            )
            _om = _om_r.scalar_one_or_none()
            if _om and _om.user_id:
                _u_r = await db.execute(_select(UserModel).where(UserModel.id == _om.user_id))
                platform_user = _u_r.scalar_one_or_none()
                if platform_user:
                    matched_via = "org_member.external_id(staff_id)"
                    logger.info(f"[DingTalk] Step1: Matched user via staff_id {sender_staff_id}: {platform_user.username}")

        # Step 2: Match via username = dingtalk_{staffId} (兼容旧用户)
        if sender_staff_id and not platform_user:
            _u_r = await db.execute(_select(UserModel).where(UserModel.username == dt_username))
            platform_user = _u_r.scalar_one_or_none()
            if platform_user:
                matched_via = "username"
                logger.info(f"[DingTalk] Step2: Matched user via username {dt_username}")

        # Step 3: Call DingTalk API to get unionId/mobile/email (仅首次未匹配时)
        if not platform_user and _early_app_key and _early_app_secret and sender_staff_id:
            dt_user_detail = await _get_dingtalk_user_detail(
                _early_app_key, _early_app_secret, sender_staff_id
            )
            if dt_user_detail:
                dt_unionid = dt_user_detail.get("unionid", "")
                dt_mobile = dt_user_detail.get("mobile", "")
                dt_email = dt_user_detail.get("email", "")

                # 3a: unionId 查 org_members（跨通道匹配 SSO 用户）
                if dt_unionid and _dingtalk_provider and not platform_user:
                    _om_r = await db.execute(
                        _select(OrgMember).where(
                            OrgMember.provider_id == _dingtalk_provider.id,
                            OrgMember.status == "active",
                            _or(
                                OrgMember.unionid == dt_unionid,
                                OrgMember.external_id == dt_unionid,
                            ),
                        )
                    )
                    _om = _om_r.scalar_one_or_none()
                    if _om and _om.user_id:
                        _u_r = await db.execute(_select(UserModel).where(UserModel.id == _om.user_id))
                        platform_user = _u_r.scalar_one_or_none()
                        if platform_user:
                            matched_via = "org_member.unionid"
                            logger.info(f"[DingTalk] Step3a: Matched user via unionid {dt_unionid}: {platform_user.username}")

                # 3b: mobile 匹配
                if dt_mobile and not platform_user:
                    _u_r = await db.execute(
                        _select(UserModel).where(
                            UserModel.primary_mobile == dt_mobile,
                            UserModel.tenant_id == agent_obj.tenant_id,
                        )
                    )
                    platform_user = _u_r.scalar_one_or_none()
                    if platform_user:
                        matched_via = "mobile"
                        logger.info(f"[DingTalk] Step3b: Matched user via mobile: {platform_user.username}")

                # 3c: email 匹配
                if dt_email and not platform_user:
                    _u_r = await db.execute(
                        _select(UserModel).where(
                            UserModel.email == dt_email,
                            UserModel.tenant_id == agent_obj.tenant_id,
                        )
                    )
                    platform_user = _u_r.scalar_one_or_none()
                    if platform_user:
                        matched_via = "email"
                        logger.info(f"[DingTalk] Step3c: Matched user via email: {platform_user.username}")

        # Step 4: No match found — create new user
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
            matched_via = "created"
            logger.info(f"[DingTalk] Step4: Created new user: {dt_username}")
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

        # -- Ensure org_member record exists (for future Step 1 fast-path) --
        if _dingtalk_provider and sender_staff_id:
            _om_check_r = await db.execute(
                _select(OrgMember).where(
                    OrgMember.user_id == platform_user.id,
                    OrgMember.provider_id == _dingtalk_provider.id,
                )
            )
            _existing_om = _om_check_r.scalar_one_or_none()
            if not _existing_om:
                # Create org_member so next message hits Step 1 directly
                _new_om = OrgMember(
                    user_id=platform_user.id,
                    provider_id=_dingtalk_provider.id,
                    external_id=sender_staff_id,
                    unionid=dt_unionid or None,
                    name=sender_nick or platform_user.display_name or dt_username,
                    status="active",
                    tenant_id=agent_obj.tenant_id,
                )
                db.add(_new_om)
                await db.flush()
                logger.info(f"[DingTalk] Created org_member for user {platform_user.username}, external_id={sender_staff_id}")
            elif _existing_om.external_id != sender_staff_id:
                # Update external_id to sender_staff_id (企业 userId)
                _existing_om.external_id = sender_staff_id
                if dt_unionid and not _existing_om.unionid:
                    _existing_om.unionid = dt_unionid
                await db.flush()
                logger.info(f"[DingTalk] Updated org_member external_id to {sender_staff_id} for user {platform_user.username}")

        platform_user_id = platform_user.id

        # Check for channel commands (/new, /reset)
        from app.services.channel_commands import is_channel_command, handle_channel_command
        if is_channel_command(user_text):
            cmd_result = await handle_channel_command(
                db=db, command=user_text, agent_id=agent_id,
                user_id=platform_user_id, external_conv_id=conv_id,
                source_channel="dingtalk",
            )
            await db.commit()
            import httpx as _httpx_cmd
            async with _httpx_cmd.AsyncClient(timeout=10) as _cl_cmd:
                await _cl_cmd.post(session_webhook, json={
                    "msgtype": "text",
                    "text": {"content": cmd_result["message"]},
                })
            return

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

        # Re-hydrate historical images for multi-turn LLM context
        from app.services.image_context import rehydrate_image_messages
        history = rehydrate_image_messages(history, agent_id, max_images=3)

        # Save user message — use display-friendly format for DB (no base64)
        # Build saved_content: [file:name] prefix for each saved file + clean text
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
        db.add(ChatMessage(
            agent_id=agent_id, user_id=platform_user_id,
            role="user", content=saved_content,
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
