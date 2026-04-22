"""WeCom (企业微信) Channel API routes.

Provides Config CRUD and webhook-based message handling with AES encryption.
"""

import base64
import hashlib
import os
import re
import struct
import time
import uuid
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

import asyncio
import httpx
from Crypto.Cipher import AES
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import HTMLResponse
from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.permissions import check_agent_access, is_agent_creator
from app.core.security import create_access_token, get_current_user
from app.database import async_session, get_db
from app.models.agent import Agent as AgentModel
from app.models.agent import DEFAULT_CONTEXT_WINDOW_SIZE
from app.models.audit import ChatMessage
from app.models.channel_config import ChannelConfig
from app.models.identity import IdentityProvider, SSOScanSession
from app.models.user import User
from app.services.activity_logger import log_activity
from app.services.auth_registry import auth_provider_registry
from app.services.channel_session import find_or_create_channel_session
from app.services.channel_user_service import channel_user_service
from app.services.platform_service import platform_service
from app.api.feishu import _call_agent_llm
from app.schemas.schemas import ChannelConfigOut
from app.services.wecom_stream import wecom_stream_manager

router = APIRouter(tags=["wecom"])


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


# ─── WeCom Domain Verification File Hosting ────────────

# WeCom requires that each self-built app's trusted domain host a
# verification file at: https://domain/WW_verify_<token>.txt
# The file content is just the token string (plain text).
#
# For multi-tenant SaaS, we don't want every tenant to have their own server.
# Instead, tenants paste their verification token into the enterprise settings,
# and this endpoint serves the correct file content for any known token.
#
# Nginx config required to route requests at the root path:
#   location ~ ^/(WW_verify_[A-Za-z0-9_.-]{1,64}\.txt)$ {
#       proxy_pass http://backend:8000/api/wecom-verify/$1;
#   }

_VERIFY_FILENAME_RE = re.compile(r"^WW_verify_[A-Za-z0-9_]{1,64}\.txt$")


@router.get("/wecom-verify/{filename}")
async def serve_wecom_verify_file(
    filename: str,
    db: AsyncSession = Depends(get_db),
):
    """Serve a WeCom domain verification file.

    Looks across all active WeCom IdentityProviders for one whose config
    contains the requested filename. Returns the verification content as
    plain text so WeCom's ownership-check bot can confirm it.

    Security: filename is validated against a strict whitelist regex before
    any DB lookup to prevent path traversal or injection attacks.
    """
    # Strict allowlist: only WW_verify_*.txt filenames are legal
    if not _VERIFY_FILENAME_RE.fullmatch(filename):
        return Response(status_code=404)

    # Search all active WeCom providers for a matching verification entry
    result = await db.execute(
        select(IdentityProvider).where(
            IdentityProvider.provider_type == "wecom",
            IdentityProvider.is_active == True,
        )
    )
    providers = result.scalars().all()

    for provider in providers:
        config = provider.config or {}
        verify_files: dict = config.get("wecom_verify_files", {})
        if filename in verify_files:
            content = verify_files[filename]
            logger.info(
                f"[WeCom Verify] Serving {filename} for tenant {provider.tenant_id}"
            )
            return Response(content=content, media_type="text/plain")

    return Response(status_code=404)


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
        existing.is_connected = False
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
            is_connected=False,
        )
        db.add(config)
        await db.flush()
        config_out = ChannelConfigOut.model_validate(config)

    try:
        if has_ws_mode:
            asyncio.create_task(
                wecom_stream_manager.start_client(agent_id, bot_id, bot_secret)
            )
            logger.info(f"[WeCom] WebSocket client start triggered for agent {agent_id}")
        else:
            asyncio.create_task(wecom_stream_manager.stop_client(agent_id))
            logger.info(f"[WeCom] WebSocket client stop triggered for agent {agent_id}")
    except Exception as e:
        logger.error(f"[WeCom] Failed to update WebSocket client state: {e}")

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

    config_out = ChannelConfigOut.model_validate(config)
    if (config.extra_config or {}).get("connection_mode") == "websocket":
        config_out.is_connected = wecom_stream_manager.status().get(str(agent_id), False)
    else:
        config_out.is_connected = False
    return config_out


@router.get("/agents/{agent_id}/wecom-channel/webhook-url")
async def get_wecom_webhook_url(
    agent_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    public_base = await platform_service.get_public_base_url(db, request)
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
    await wecom_stream_manager.stop_client(agent_id)
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
        asyncio.create_task(
            _process_wecom_text(db, agent_id, config, from_user, user_text, chat_id=chat_id)
        )

    elif msg_type == "event":
        event = msg_root.findtext("Event", "")
        if event == "kf_msg_or_event":
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
    try:
        async with async_session() as session:
            r = await session.execute(
                select(ChannelConfig).where(ChannelConfig.agent_id == agent_id, ChannelConfig.channel_type == "wecom")
            )
            config = r.scalar_one_or_none()
            if not config:
                return

            async with httpx.AsyncClient(timeout=10) as client:
                tok_resp = await client.get("https://qyapi.weixin.qq.com/cgi-bin/gettoken", params={"corpid": config.app_id, "corpsecret": config.app_secret})
                token_data = tok_resp.json()
                access_token = token_data.get("access_token")
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

    async with async_session() as db:
        # Load agent
        agent_r = await db.execute(select(AgentModel).where(AgentModel.id == agent_id))
        agent_obj = agent_r.scalar_one_or_none()
        if not agent_obj:
            logger.warning(f"[WeCom] Agent {agent_id} not found")
            return
        creator_id = agent_obj.creator_id
        ctx_size = (agent_obj.context_window_size or DEFAULT_CONTEXT_WINDOW_SIZE) if agent_obj else DEFAULT_CONTEXT_WINDOW_SIZE

        # Distinguish group chat from P2P by chat_id presence
        _is_group = bool(chat_id)
        if _is_group:
            conv_id = f"wecom_group_{chat_id}"
        else:
            conv_id = f"wecom_p2p_{from_user}"

        # The channel_user_service resolves display names from OrgMember records
        # (populated by org-sync or enriched on first SSO login). No need to
        # make an extra API call here — it fails with 48009 when IP is not whitelisted.
        extra_info = {"unionid": from_user}

        # Resolve channel user via unified service (uses OrgMember + SSO patterns)
        platform_user = await channel_user_service.resolve_channel_user(
            db=db,
            agent=agent_obj,
            channel_type="wecom",
            external_user_id=from_user,
            extra_info=extra_info,
        )
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
            select(ChatMessage)
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
            async with httpx.AsyncClient(timeout=10) as client:
                tok_resp = await client.get(
                    "https://qyapi.weixin.qq.com/cgi-bin/gettoken",
                    params={"corpid": config.app_id, "corpsecret": config.app_secret},
                )
                access_token = tok_resp.json().get("access_token", "")
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

    # 2. Extract user info and login/register via RegistrationService
    try:
        auth_provider = await auth_provider_registry.get_provider(
            db, "wecom", str(tenant_id or provider.tenant_id) if (tenant_id or provider.tenant_id) else None
        )
        if not auth_provider:
            return HTMLResponse("Auth failed: WeCom provider unavailable")
        
        token_data = await auth_provider.exchange_code_for_token(code)
        access_token_str = token_data.get("access_token")
        if not access_token_str:
            return HTMLResponse("Auth failed: Token error")
            
        user_info = await auth_provider.get_user_info(access_token_str)
        if not user_info.provider_user_id:
            return HTMLResponse("Auth failed: No UserId returned")
            
        # Find or Create User (handles Identity and OrgMember linking)
        user, _is_new = await auth_provider.find_or_create_user(
            db, user_info, tenant_id=tenant_id or provider.tenant_id
        )
    except Exception as e:
        logger.exception(f"WeCom login/register error: {e}")
        return HTMLResponse(f"Auth failed: {str(e)}")


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
