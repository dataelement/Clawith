"""WeCom (企业微信) Channel API routes.

Provides Config CRUD and webhook-based message handling with AES encryption.
"""

import base64
import hashlib
import re
import socket
import struct
import time
import uuid
import xml.etree.ElementTree as ET

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.permissions import check_agent_access, is_agent_creator
from app.core.security import get_current_user
from app.database import get_db
from app.models.channel_config import ChannelConfig
from app.models.user import User
from app.schemas.schemas import ChannelConfigOut

router = APIRouter(tags=["wecom"])


async def _get_wecom_token_cached(corp_id: str, corp_secret: str) -> str:
    """Get WeCom access_token with Redis (preferred) + memory fallback caching.

    Key: clawith:token:wecom:{corp_id}
    TTL: 6900s (7200s validity - 5 min early refresh)
    """
    from app.core.token_cache import get_cached_token, set_cached_token
    import httpx as _httpx

    cache_key = f"clawith:token:wecom:{corp_id}"
    cached = await get_cached_token(cache_key)
    if cached:
        return cached

    async with _httpx.AsyncClient(timeout=10) as _client:
        _resp = await _client.get(
            "https://qyapi.weixin.qq.com/cgi-bin/gettoken",
            params={"corpid": corp_id, "corpsecret": corp_secret},
        )
        _data = _resp.json()
        token = _data.get("access_token", "")
        expires_in = int(_data.get("expires_in") or 7200)
        if token:
            ttl = max(expires_in - 300, 300)
            await set_cached_token(cache_key, token, ttl)
        return token


# ─── WeCom AES Crypto ──────────────────────────────────

def _pad(text: bytes) -> bytes:
    """PKCS7 padding for AES-CBC."""
    BLOCK_SIZE = 32
    pad_len = BLOCK_SIZE - (len(text) % BLOCK_SIZE)
    return text + bytes([pad_len] * pad_len)


def _unpad(text: bytes) -> bytes:
    """Remove PKCS7 padding."""
    pad_len = text[-1]
    return text[:-pad_len]


def _decrypt_msg(encrypt_key: str, encrypted_text: str) -> tuple[str, str]:
    """Decrypt a WeCom encrypted message.

    Returns (decrypted_xml, corp_id)
    """
    from Crypto.Cipher import AES
    aes_key = base64.b64decode(encrypt_key + "=")
    iv = aes_key[:16]
    cipher = AES.new(aes_key, AES.MODE_CBC, iv)
    decrypted = _unpad(cipher.decrypt(base64.b64decode(encrypted_text)))
    # Skip 16 random bytes, then 4 bytes msg_length (network order)
    msg_len = struct.unpack("!I", decrypted[16:20])[0]
    msg_content = decrypted[20:20 + msg_len].decode("utf-8")
    corp_id = decrypted[20 + msg_len:].decode("utf-8")
    return msg_content, corp_id


def _encrypt_msg(encrypt_key: str, reply_msg: str, corp_id: str) -> str:
    """Encrypt a reply message for WeCom."""
    from Crypto.Cipher import AES
    import os
    aes_key = base64.b64decode(encrypt_key + "=")
    iv = aes_key[:16]
    msg_bytes = reply_msg.encode("utf-8")
    buf = os.urandom(16) + struct.pack("!I", len(msg_bytes)) + msg_bytes + corp_id.encode("utf-8")
    cipher = AES.new(aes_key, AES.MODE_CBC, iv)
    encrypted = cipher.encrypt(_pad(buf))
    return base64.b64encode(encrypted).decode("utf-8")


def _verify_signature(token: str, timestamp: str, nonce: str, encrypt: str) -> str:
    """Generate WeCom message signature."""
    items = sorted([token, timestamp, nonce, encrypt])
    return hashlib.sha1("".join(items).encode("utf-8")).hexdigest()


# ─── Config CRUD ────────────────────────────────────────

@router.post("/agents/{agent_id}/wecom-channel", response_model=ChannelConfigOut, status_code=201)
async def configure_wecom_channel(
    agent_id: uuid.UUID,
    data: dict,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Configure WeCom bot for an agent.

    Supports two modes:
    - WebSocket (AI Bot): bot_id + bot_secret (no callback URL needed)
    - Webhook (legacy): corp_id, secret, token, encoding_aes_key
    """
    agent, _ = await check_agent_access(db, current_user, agent_id)
    if not is_agent_creator(current_user, agent):
        raise HTTPException(status_code=403, detail="Only creator can configure channel")

    # WebSocket mode fields (AI Bot)
    bot_id = data.get("bot_id", "").strip()
    bot_secret = data.get("bot_secret", "").strip()

    # Legacy webhook mode fields
    corp_id = data.get("corp_id", "").strip()
    wecom_agent_id = data.get("wecom_agent_id", "").strip()
    secret = data.get("secret", "").strip()
    token = data.get("token", "").strip()
    encoding_aes_key = data.get("encoding_aes_key", "").strip()

    # At least one mode must be configured
    has_ws_mode = bool(bot_id and bot_secret)
    has_webhook_mode = bool(corp_id and secret and token and encoding_aes_key)
    if not has_ws_mode and not has_webhook_mode:
        raise HTTPException(
            status_code=422,
            detail="Either bot_id+bot_secret (WebSocket) or corp_id+secret+token+encoding_aes_key (Webhook) required"
        )

    extra_config = {
        "wecom_agent_id": wecom_agent_id,
        "bot_id": bot_id,
        "bot_secret": bot_secret,
        "connection_mode": "websocket" if has_ws_mode else "webhook",
    }

    result = await db.execute(
        select(ChannelConfig).where(
            ChannelConfig.agent_id == agent_id,
            ChannelConfig.channel_type == "wecom",
        )
    )
    existing = result.scalar_one_or_none()
    if existing:
        existing.app_id = corp_id
        existing.app_secret = secret
        existing.encrypt_key = encoding_aes_key
        existing.verification_token = token
        existing.extra_config = extra_config
        existing.is_configured = True
        await db.flush()
        config_out = ChannelConfigOut.model_validate(existing)
    else:
        config = ChannelConfig(
            agent_id=agent_id,
            channel_type="wecom",
            app_id=corp_id,
            app_secret=secret,
            encrypt_key=encoding_aes_key,
            verification_token=token,
            extra_config=extra_config,
            is_configured=True,
        )
        db.add(config)
        await db.flush()
        config_out = ChannelConfigOut.model_validate(config)

    # Auto-start WebSocket client if bot credentials provided
    if has_ws_mode:
        try:
            from app.services.wecom_stream import wecom_stream_manager
            import asyncio
            asyncio.create_task(
                wecom_stream_manager.start_client(agent_id, bot_id, bot_secret)
            )
            logger.info(f"[WeCom] WebSocket client start triggered for agent {agent_id}")
        except Exception as e:
            logger.error(f"[WeCom] Failed to start WebSocket client: {e}")

    return config_out


@router.get("/agents/{agent_id}/wecom-channel", response_model=ChannelConfigOut)
async def get_wecom_channel(
    agent_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await check_agent_access(db, current_user, agent_id)
    result = await db.execute(
        select(ChannelConfig).where(
            ChannelConfig.agent_id == agent_id,
            ChannelConfig.channel_type == "wecom",
        )
    )
    config = result.scalar_one_or_none()
    if not config:
        raise HTTPException(status_code=404, detail="WeCom not configured")
    return ChannelConfigOut.model_validate(config)


@router.get("/agents/{agent_id}/wecom-channel/webhook-url")
async def get_wecom_webhook_url(
    agent_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    import os
    from app.models.system_settings import SystemSetting
    public_base = ""
    result = await db.execute(select(SystemSetting).where(SystemSetting.key == "platform"))
    setting = result.scalar_one_or_none()
    if setting and setting.value.get("public_base_url"):
        public_base = setting.value["public_base_url"].rstrip("/")
    if not public_base:
        public_base = os.environ.get("PUBLIC_BASE_URL", "").rstrip("/")
    if not public_base:
        public_base = str(request.base_url).rstrip("/")
    return {"webhook_url": f"{public_base}/api/channel/wecom/{agent_id}/webhook"}


@router.delete("/agents/{agent_id}/wecom-channel", status_code=204)
async def delete_wecom_channel(
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
            ChannelConfig.channel_type == "wecom",
        )
    )
    config = result.scalar_one_or_none()
    if not config:
        raise HTTPException(status_code=404, detail="WeCom not configured")
    await db.delete(config)


# ─── Event Webhook ──────────────────────────────────────

_processed_wecom_events: set[str] = set()
_processed_kf_msgids: set[str] = set()



@router.get("/channel/wecom/{agent_id}/webhook")
async def wecom_verify_webhook(
    agent_id: uuid.UUID,
    msg_signature: str = "",
    timestamp: str = "",
    nonce: str = "",
    echostr: str = "",
    db: AsyncSession = Depends(get_db),
):
    """Handle WeCom callback URL verification (GET request)."""
    result = await db.execute(
        select(ChannelConfig).where(
            ChannelConfig.agent_id == agent_id,
            ChannelConfig.channel_type == "wecom",
        )
    )
    config = result.scalar_one_or_none()
    if not config:
        return Response(status_code=404)

    token = config.verification_token or ""
    encoding_aes_key = config.encrypt_key or ""

    # Verify signature
    expected_sig = _verify_signature(token, timestamp, nonce, echostr)
    if expected_sig != msg_signature:
        logger.warning(f"[WeCom] Signature mismatch: expected={expected_sig}, got={msg_signature}")
        return Response(status_code=403)

    # Decrypt echostr and return plaintext
    try:
        decrypted, _ = _decrypt_msg(encoding_aes_key, echostr)
        return Response(content=decrypted, media_type="text/plain")
    except Exception as e:
        logger.error(f"[WeCom] Failed to decrypt echostr: {e}")
        return Response(status_code=500)


@router.post("/channel/wecom/{agent_id}/webhook")
async def wecom_event_webhook(
    agent_id: uuid.UUID,
    request: Request,
    msg_signature: str = "",
    timestamp: str = "",
    nonce: str = "",
    db: AsyncSession = Depends(get_db),
):
    """Handle WeCom message callback (POST request with encrypted XML)."""
    body_bytes = await request.body()

    # Get channel config
    result = await db.execute(
        select(ChannelConfig).where(
            ChannelConfig.agent_id == agent_id,
            ChannelConfig.channel_type == "wecom",
        )
    )
    config = result.scalar_one_or_none()
    if not config:
        return Response(status_code=404)

    token = config.verification_token or ""
    encoding_aes_key = config.encrypt_key or ""
    corp_id = config.app_id or ""

    # Parse encrypted XML body
    try:
        root = ET.fromstring(body_bytes)
        encrypt_text = root.findtext("Encrypt", "")
    except Exception as e:
        logger.error(f"[WeCom] Failed to parse XML body: {e}")
        return Response(content="success", media_type="text/plain")

    # Verify signature
    expected_sig = _verify_signature(token, timestamp, nonce, encrypt_text)
    if expected_sig != msg_signature:
        logger.warning("[WeCom] Signature mismatch on POST")
        return Response(status_code=403)

    # Decrypt message
    try:
        decrypted_xml, recv_corp_id = _decrypt_msg(encoding_aes_key, encrypt_text)
    except Exception as e:
        logger.error(f"[WeCom] Failed to decrypt message: {e}")
        return Response(content="success", media_type="text/plain")

    logger.info(f"[WeCom] Decrypted event for {agent_id}")

    # Parse decrypted message XML
    try:
        msg_root = ET.fromstring(decrypted_xml)
    except Exception as e:
        logger.error(f"[WeCom] Failed to parse decrypted XML: {e}")
        return Response(content="success", media_type="text/plain")

    msg_type = msg_root.findtext("MsgType", "")
    from_user = msg_root.findtext("FromUserName", "")  # WeCom userid
    msg_id = msg_root.findtext("MsgId", "")
    open_kfid = msg_root.findtext("OpenKfId", "")
    token = msg_root.findtext("Token", "")
    # Group chat ID — present when message comes from a WeCom group
    chat_id = msg_root.findtext("ChatId", "")

    # Dedup
    dedup_key = msg_id if msg_id else token
    if dedup_key and dedup_key in _processed_wecom_events:
        return Response(content="success", media_type="text/plain")
    if dedup_key:
        _processed_wecom_events.add(dedup_key)
        if len(_processed_wecom_events) > 1000:
            _processed_wecom_events.clear()

    logger.info(f"[WeCom] Message type={msg_type}, from={from_user}, msg_id={msg_id}, chat_id={chat_id or 'N/A'}")

    if msg_type == "text":
        user_text = msg_root.findtext("Content", "").strip()
        if not user_text:
            return Response(content="success", media_type="text/plain")

        # Process in background task
        import asyncio
        asyncio.create_task(
            _process_wecom_text(db, agent_id, config, from_user, user_text, chat_id=chat_id)
        )

    elif msg_type == "event":
        event = msg_root.findtext("Event", "")
        if event == "kf_msg_or_event":
            import asyncio
            asyncio.create_task(
                _process_wecom_kf_event(agent_id, config, token, open_kfid)
            )
        else:
            logger.info(f"[WeCom] Received event: {event} (not handled)")

    elif msg_type in ("image", "file"):
        # TODO: Handle image/file messages in future
        logger.info(f"[WeCom] Received {msg_type} message (not yet handled)")

    return Response(content="success", media_type="text/plain")


async def _process_wecom_kf_event(agent_id: uuid.UUID, config_obj: ChannelConfig, token: str, open_kfid: str = None):
    """Sync WeCom Customer Service (KF) messages in background."""
    import httpx
    import time
    from app.database import async_session
    from sqlalchemy import select as _select
    from app.models.channel_config import ChannelConfig as ChannelConfigModel
    
    try:
        async with async_session() as session:
            r = await session.execute(_select(ChannelConfigModel).where(ChannelConfigModel.agent_id == agent_id, ChannelConfigModel.channel_type == "wecom"))
            config = r.scalar_one_or_none()
            if not config:
                return

            access_token = await _get_wecom_token_cached(config.app_id, config.app_secret)
            if not access_token:
                return

                current_cursor = token
                has_more = 1
                current_ts = int(time.time())

                while has_more:
                    payload = {"limit": 20}
                    if open_kfid:
                        payload["open_kfid"] = open_kfid

                    if current_cursor.startswith("ENC"):
                        payload["token"] = current_cursor
                    else:
                        payload["cursor"] = current_cursor
                    
                    logger.info(f"[WeCom KF] Calling sync_msg with payload: {payload}")
                    sync_resp = await client.post(f"https://qyapi.weixin.qq.com/cgi-bin/kf/sync_msg?access_token={access_token}", json=payload)
                    sync_data = sync_resp.json()
                    if sync_data.get("errcode") != 0:
                        logger.error(f"[WeCom KF] sync_msg error: {sync_data}")
                        break
                    
                    has_more = sync_data.get("has_more", 0)
                    current_cursor = sync_data.get("next_cursor", "")
                    
                    for msg in sync_data.get("msg_list", []):
                        if msg.get("origin") == 3 and msg.get("msgtype") == "text":
                            mid = msg.get("msgid")
                            if mid in _processed_kf_msgids:
                                continue
                            if msg.get("send_time", 0) > 0 and (current_ts - msg.get("send_time", 0) > 86400):
                                continue
                            _processed_kf_msgids.add(mid)
                            text = msg.get("text", {}).get("content", "").strip()
                            if text:
                                logger.info(f"[WeCom KF] Found msg from {msg.get('external_userid')}: {text[:20]}...")
                                # Call the local process text with extra KF info
                                await _process_wecom_text(
                                    session, agent_id, config, 
                                    msg.get("external_userid"), text,
                                    is_kf=True, open_kfid=msg.get("open_kfid"), kf_msg_id=mid
                                )
                    if not has_more:
                        break
    except Exception as e: 
        logger.error(f"[WeCom KF] Error in background task: {e}")


async def _process_wecom_text(
    db: AsyncSession,
    agent_id: uuid.UUID,
    config: ChannelConfig,
    from_user: str,
    user_text: str,
    is_kf: bool = False,
    open_kfid: str = None,
    kf_msg_id: str = None,
    chat_id: str = "",
):
    """Process an incoming WeCom text message and reply."""
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
            logger.warning(f"[WeCom] Agent {agent_id} not found")
            return
        creator_id = agent_obj.creator_id
        ctx_size = agent_obj.context_window_size if agent_obj else 100

        # Distinguish group chat from P2P by chat_id presence
        _is_group = bool(chat_id)
        if _is_group:
            conv_id = f"wecom_group_{chat_id}"
        else:
            conv_id = f"wecom_p2p_{from_user}"

        # Find or create platform user
        wc_username = f"wecom_{from_user}"
        u_r = await db.execute(_select(UserModel).where(UserModel.username == wc_username))
        platform_user = u_r.scalar_one_or_none()

        # Try to resolve display name from WeCom API
        display_name = f"WeCom {from_user[:8]}"
        try:
            access_token = await _get_wecom_token_cached(config.app_id, config.app_secret)
            if access_token:
                async with httpx.AsyncClient(timeout=5) as client:
                    user_resp = await client.get(
                        "https://qyapi.weixin.qq.com/cgi-bin/user/get",
                        params={"access_token": access_token, "userid": from_user},
                    )
                    user_data = user_resp.json()
                    if user_data.get("errcode") == 0:
                        display_name = user_data.get("name", display_name)
        except Exception as e:
            logger.error(f"[WeCom] Failed to resolve user info: {e}")

        if not platform_user:
            import uuid as _uuid
            platform_user = UserModel(
                username=wc_username,
                email=f"{wc_username}@wecom.local",
                password_hash=hash_password(_uuid.uuid4().hex),
                display_name=display_name,
                role="member",
                tenant_id=agent_obj.tenant_id if agent_obj else None,
            )
            db.add(platform_user)
            await db.flush()
        platform_user_id = platform_user.id

        # Find or create session
        sess = await find_or_create_channel_session(
            db=db,
            agent_id=agent_id,
            user_id=creator_id if _is_group else platform_user_id,
            external_conv_id=conv_id,
            source_channel="wecom",
            first_message_title=user_text,
            is_group=_is_group,
            group_name=f"WeCom Group {chat_id[:8]}" if _is_group else None,
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
        logger.info(f"[WeCom] LLM reply: {reply_text[:100]}")

        # Send reply via WeCom API
        wecom_agent_id = (config.extra_config or {}).get("wecom_agent_id", "")
        try:
            access_token = await _get_wecom_token_cached(config.app_id, config.app_secret)
            async with httpx.AsyncClient(timeout=10) as client:
                if access_token:
                    if is_kf and open_kfid:
                        # For KF messages, need to bridge/trans state first then send via kf/send_msg
                        res_state = await client.post(
                            f"https://qyapi.weixin.qq.com/cgi-bin/kf/service_state/trans?access_token={access_token}", 
                            json={"open_kfid": open_kfid, "external_userid": from_user, "service_state": 1}
                        )
                        logger.info(f"[WeCom KF] trans state result: {res_state.json()}")
                        res_send = await client.post(
                            f"https://qyapi.weixin.qq.com/cgi-bin/kf/send_msg?access_token={access_token}", 
                            json={"touser": from_user, "open_kfid": open_kfid, "msgtype": "text", "text": {"content": reply_text}}
                        )
                        logger.info(f"[WeCom KF] send_msg result: {res_send.json()}")
                    else:
                        # Default legacy Send as text
                        await client.post(
                            f"https://qyapi.weixin.qq.com/cgi-bin/message/send?access_token={access_token}",
                            json={
                                "touser": from_user,
                                "msgtype": "text",
                                "agentid": int(wecom_agent_id) if wecom_agent_id else 0,
                                "text": {"content": reply_text},
                            },
                        )
        except Exception as e:
            logger.error(f"[WeCom] Failed to send reply: {e}")

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
            f"Replied to WeCom message: {reply_text[:80]}",
            detail={"channel": "wecom", "user_text": user_text[:200], "reply": reply_text[:500]},
        )


# ─── OAuth Callback (SSO) ──────────────────────────────

@router.get("/auth/wecom/callback")
async def wecom_callback(
    code: str,
    state: str = None,
    db: AsyncSession = Depends(get_db),
):
    # 1. Resolve session to get tenant context
    from app.models.identity import SSOScanSession
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

    # 1. Get WeCom provider config
    provider_query = select(IdentityProvider).where(IdentityProvider.provider_type == "wecom")
    if tenant_id:
        # Strict scope
        provider_query = provider_query.where(IdentityProvider.tenant_id == tenant_id)
    else:
        # Fallback to unscoped
        provider_query = provider_query.where(IdentityProvider.tenant_id.is_(None))

    provider_result = await db.execute(provider_query)
    provider = provider_result.scalar_one_or_none()
    if not provider:
        raise HTTPException(status_code=404, detail="WeCom provider not configured for this tenant")

    config = provider.config
    corp_id = config.get("app_id") or config.get("corp_id")
    secret = config.get("app_secret") or config.get("secret")

    # 2. Exchange code for user info
    try:
        access_token = await _get_wecom_token_cached(corp_id, secret)
        if not access_token:
            return HTMLResponse(f"Auth failed: Token error")
        async with httpx.AsyncClient() as client:
            # Get user info
            user_res = await client.get(
                "https://qyapi.weixin.qq.com/cgi-bin/user/getuserinfo",
                params={"access_token": access_token, "code": code}
            )
            wc_user = user_res.json()
            userid = wc_user.get("UserId")
            if not userid:
                logger.error(f"WeCom userinfo error: {wc_user}")
                return HTMLResponse("Auth failed: No UserId returned")
    except Exception as e:
        logger.error(f"WeCom login error: {e}")
        return HTMLResponse(f"Auth failed: {str(e)}")

    # 3. Fetch detailed user info (email, name) from WeCom API
    wc_name = f"WeCom {userid}"
    wc_email = None
    try:
        _at2 = await _get_wecom_token_cached(corp_id, secret)
        async with httpx.AsyncClient(timeout=5) as client:
            if _at2:
                detail_res = await client.get(
                    "https://qyapi.weixin.qq.com/cgi-bin/user/get",
                    params={"access_token": _at2, "userid": userid},
                )
                detail_data = detail_res.json()
                if detail_data.get("errcode") == 0:
                    wc_name = detail_data.get("name", wc_name)
                    wc_email = detail_data.get("email") or detail_data.get("biz_mail")
                    logger.info(f"WeCom user detail: name={wc_name}, email={wc_email}")
                else:
                    logger.warning(f"WeCom user/get failed (non-fatal): {detail_data}")
    except Exception as e:
        logger.warning(f"WeCom user detail fetch failed (non-fatal): {e}")

    # 4. Find user via OrgMember (SSO users must be in the org directory)
    from app.models.org import OrgMember

    # WeCom stores userid in external_id during org sync
    member_result = await db.execute(
        select(OrgMember).where(
            OrgMember.external_id == userid,
            OrgMember.provider_id == provider.id,
        )
    )
    member = member_result.scalar_one_or_none()

    user = None
    if member and member.user_id:
        user_result = await db.execute(select(User).where(User.id == member.user_id))
        user = user_result.scalar_one_or_none()

    if user:
        # Sync latest info on every login
        if (not user.email or user.email.endswith("@wecom.local")) and wc_email:
            user.email = wc_email
        if wc_name:
            user.display_name = wc_name
    else:
        # Create new User (first login or OrgMember not yet linked)
        email = wc_email or f"{userid}@wecom.local"
        username = wc_email.split("@")[0] if wc_email else f"wc_{userid[:12]}"
        user = User(
            username=username,
            email=email,
            display_name=wc_name,
            password_hash=hash_password(uuid.uuid4().hex),
            role="member",
            tenant_id=tenant_id or provider.tenant_id,
        )
        db.add(user)
        await db.flush()

        # Link back to OrgMember if found
        if member:
            member.user_id = user.id

    await db.flush()


    # Standard login
    token = create_access_token(str(user.id), user.role)

    if state:
        try:
            sid = uuid.UUID(state)
            s_res = await db.execute(select(SSOScanSession).where(SSOScanSession.id == sid))
            session = s_res.scalar_one_or_none()
            if session:
                session.status = "authorized"
                session.provider_type = "wecom"
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
            logger.exception("Failed to update SSO session (wecom) %s", e)

    return HTMLResponse(f"Logged in. Token: {token}")
