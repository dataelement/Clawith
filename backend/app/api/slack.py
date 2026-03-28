"""Slack Bot Channel API routes."""

import hashlib
import hmac
import time
import uuid

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

router = APIRouter(tags=["slack"])

SLACK_MSG_LIMIT = 4000  # Slack text message char limit


# ─── Config CRUD ────────────────────────────────────────

@router.post("/agents/{agent_id}/slack-channel", response_model=ChannelConfigOut, status_code=201)
async def configure_slack_channel(
    agent_id: uuid.UUID,
    data: dict,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Configure Slack bot for an agent. Fields: bot_token, signing_secret."""
    agent, _ = await check_agent_access(db, current_user, agent_id)
    if not is_agent_creator(current_user, agent):
        raise HTTPException(status_code=403, detail="Only creator can configure channel")

    bot_token = data.get("bot_token", "").strip()
    signing_secret = data.get("signing_secret", "").strip()
    if not bot_token or not signing_secret:
        raise HTTPException(status_code=422, detail="bot_token and signing_secret are required")

    result = await db.execute(
        select(ChannelConfig).where(
            ChannelConfig.agent_id == agent_id,
            ChannelConfig.channel_type == "slack",
        )
    )
    existing = result.scalar_one_or_none()
    if existing:
        existing.app_secret = bot_token        # Bot Token
        existing.encrypt_key = signing_secret  # Signing Secret
        existing.is_configured = True
        await db.flush()
        return ChannelConfigOut.model_validate(existing)

    config = ChannelConfig(
        agent_id=agent_id,
        channel_type="slack",
        app_id="slack",               # placeholder
        app_secret=bot_token,         # Bot Token (xoxb-...)
        encrypt_key=signing_secret,   # Signing Secret
        is_configured=True,
    )
    db.add(config)
    await db.flush()
    return ChannelConfigOut.model_validate(config)


@router.get("/agents/{agent_id}/slack-channel", response_model=ChannelConfigOut)
async def get_slack_channel(
    agent_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await check_agent_access(db, current_user, agent_id)
    result = await db.execute(
        select(ChannelConfig).where(
            ChannelConfig.agent_id == agent_id,
            ChannelConfig.channel_type == "slack",
        )
    )
    config = result.scalar_one_or_none()
    if not config:
        raise HTTPException(status_code=404, detail="Slack not configured")
    return ChannelConfigOut.model_validate(config)


@router.get("/agents/{agent_id}/slack-channel/webhook-url")
async def get_slack_webhook_url(agent_id: uuid.UUID, request: Request, db: AsyncSession = Depends(get_db)):
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
    return {"webhook_url": f"{public_base}/api/channel/slack/{agent_id}/webhook"}


@router.delete("/agents/{agent_id}/slack-channel", status_code=204)
async def delete_slack_channel(
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
            ChannelConfig.channel_type == "slack",
        )
    )
    config = result.scalar_one_or_none()
    if not config:
        raise HTTPException(status_code=404, detail="Slack not configured")
    await db.delete(config)


# ─── Event Webhook ──────────────────────────────────────

_processed_slack_events: set[str] = set()


def _verify_slack_signature(signing_secret: str, body: bytes, headers: dict) -> bool:
    """Verify Slack's HMAC-SHA256 request signature."""
    ts = headers.get("x-slack-request-timestamp", "")
    sig = headers.get("x-slack-signature", "")
    if not ts or not sig:
        return False
    # Reject requests older than 5 minutes
    if abs(time.time() - int(ts)) > 300:
        return False
    base = f"v0:{ts}:{body.decode()}"
    expected = "v0=" + hmac.new(signing_secret.encode(), base.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, sig)


async def _send_slack_messages(bot_token: str, channel: str, text: str) -> None:
    """Send text to Slack, splitting into SLACK_MSG_LIMIT chunks if needed."""
    import httpx
    chunks = [text[i:i + SLACK_MSG_LIMIT] for i in range(0, len(text), SLACK_MSG_LIMIT)]
    async with httpx.AsyncClient(timeout=10) as client:
        for chunk in chunks:
            await client.post(
                "https://slack.com/api/chat.postMessage",
                headers={"Authorization": f"Bearer {bot_token}", "Content-Type": "application/json"},
                json={"channel": channel, "text": chunk},
            )


@router.post("/channel/slack/{agent_id}/webhook")
async def slack_event_webhook(
    agent_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Handle Slack Event API callbacks."""
    body_bytes = await request.body()

    # Get channel config
    result = await db.execute(
        select(ChannelConfig).where(
            ChannelConfig.agent_id == agent_id,
            ChannelConfig.channel_type == "slack",
        )
    )
    config = result.scalar_one_or_none()
    if not config:
        return Response(status_code=404)

    # Verify Slack signature
    signing_secret = config.encrypt_key or ""
    if signing_secret:
        if not _verify_slack_signature(signing_secret, body_bytes, dict(request.headers)):
            return Response(status_code=401)

    import json
    body = json.loads(body_bytes)
    logger.info(f"[Slack] Webhook for {agent_id}: type={body.get('type')}")

    # URL verification challenge
    if body.get("type") == "url_verification":
        return {"challenge": body["challenge"]}

    # Event callback
    if body.get("type") != "event_callback":
        return {"ok": True}

    event = body.get("event", {})
    event_id = body.get("event_id", "")

    # Dedup
    if event_id in _processed_slack_events:
        return {"ok": True}
    if event_id:
        _processed_slack_events.add(event_id)
        if len(_processed_slack_events) > 1000:
            _processed_slack_events.clear()

    # Ignore bot messages (avoid self-reply loop)
    if event.get("bot_id") or event.get("subtype"):
        return {"ok": True}

    event_type = event.get("type", "")
    if event_type not in ("message", "app_mention"):
        return {"ok": True}

    user_text = event.get("text", "").strip()
    # Strip <@BOTID> mention prefix if present
    import re
    user_text = re.sub(r"^<@[A-Z0-9]+>\s*", "", user_text).strip()

    slack_files = event.get("files", [])

    if not user_text and not slack_files:
        return {"ok": True}

    channel_id = event.get("channel", "")
    sender_id = event.get("user", "")
    # Slack channel_id starting with 'D' = DM, 'C'/'G' = group/channel
    _is_group_slack = bool(channel_id) and not channel_id.startswith("D")
    conv_id = f"slack_{channel_id}" if channel_id else f"slack_dm_{sender_id}"

    logger.info(f"[Slack] Message from={sender_id}, channel={channel_id}: {user_text[:80]}")

    # Load history
    from app.models.audit import ChatMessage
    from app.models.agent import Agent as AgentModel
    from app.services.channel_session import find_or_create_channel_session
    agent_r = await db.execute(select(AgentModel).where(AgentModel.id == agent_id))
    agent_obj = agent_r.scalar_one_or_none()
    creator_id = agent_obj.creator_id if agent_obj else agent_id
    ctx_size = agent_obj.context_window_size if agent_obj else 20

    # Find-or-create platform user for this Slack sender
    from app.models.user import User as _User
    from app.core.security import hash_password as _hp
    import uuid as _uuid2
    _slack_username = f"slack_{sender_id}"
    _u_r = await db.execute(select(_User).where(_User.username == _slack_username))
    _platform_user = _u_r.scalar_one_or_none()

    # Resolve real display name from Slack API
    _bot_token_for_info = config.app_secret or ""
    _slack_real_name = ""
    if _bot_token_for_info and sender_id:
        try:
            import httpx as _httpx_info
            async with _httpx_info.AsyncClient(timeout=5) as _info_client:
                _info_resp = await _info_client.get(
                    "https://slack.com/api/users.info",
                    headers={"Authorization": f"Bearer {_bot_token_for_info}"},
                    params={"user": sender_id},
                )
                _info_data = _info_resp.json()
                if _info_data.get("ok"):
                    _profile = _info_data.get("user", {}).get("profile", {})
                    _slack_real_name = (
                        _profile.get("display_name")
                        or _profile.get("real_name")
                        or _info_data.get("user", {}).get("real_name")
                        or ""
                    )
        except Exception as _e_info:
            logger.error(f"[Slack] Failed to fetch user info for {sender_id}: {_e_info}")

    if not _platform_user:
        _platform_user = _User(
            username=_slack_username,
            email=f"{_slack_username}@slack.local",
            password_hash=_hp(_uuid2.uuid4().hex),
            display_name=_slack_real_name or f"Slack User {sender_id[:8]}",
            role="member",
            tenant_id=agent_obj.tenant_id if agent_obj else None,
        )
        db.add(_platform_user)
        await db.flush()
    elif _slack_real_name and _platform_user.display_name.startswith("Slack User "):
        # Update display_name if we now have the real name
        _platform_user.display_name = _slack_real_name
        await db.flush()
    platform_user_id = _platform_user.id

    # Find-or-create session for this Slack conversation
    sess = await find_or_create_channel_session(
        db=db,
        agent_id=agent_id,
        user_id=creator_id if _is_group_slack else platform_user_id,
        external_conv_id=conv_id,
        source_channel="slack",
        first_message_title=user_text,
        is_group=_is_group_slack,
        group_name=f"Slack Channel {channel_id[:8]}" if _is_group_slack else None,
    )
    session_conv_id = str(sess.id)

    history_r = await db.execute(
        select(ChatMessage)
        .where(ChatMessage.agent_id == agent_id, ChatMessage.conversation_id == session_conv_id)
        .order_by(ChatMessage.created_at.desc())
        .limit(ctx_size)
    )
    history = [{"role": m.role, "content": m.content} for m in reversed(history_r.scalars().all())]

    # Handle file attachments: save to workspace/uploads/ and send ack
    from app.config import get_settings as _gs
    import asyncio as _asyncio
    import random as _random
    from pathlib import Path as _Path
    import httpx as _httpx
    from datetime import datetime, timezone
    from app.api.feishu import _FILE_ACK_MESSAGES
    _file_user_messages = []
    _settings = _gs()
    _upload_dir = _Path(_settings.AGENT_DATA_DIR) / str(agent_id) / "workspace" / "uploads"
    _upload_dir.mkdir(parents=True, exist_ok=True)
    _bot_token = config.app_secret or ""
    for _sf in slack_files:
        _fname = _sf.get("name") or _sf.get("title") or f"slack_file_{_sf.get('id', 'unk')}.bin"
        _url = _sf.get("url_private_download") or _sf.get("url_private", "")
        if not _url:
            continue
        try:
            async with _httpx.AsyncClient(timeout=30, follow_redirects=True) as _hc:
                _r = await _hc.get(_url, headers={"Authorization": f"Bearer {_bot_token}"})
                _r.raise_for_status()
                # Detect Slack SSO redirect returning HTML instead of actual file
                _ct = _r.headers.get("content-type", "")
                if "text/html" in _ct or _r.content[:15].lower().startswith(b"<!doctype html"):
                    raise ValueError(f"Got HTML response (SSO redirect) — Slack App needs 'files:read' scope. Content-Type: {_ct}")
                (_upload_dir / _fname).write_bytes(_r.content)
            _file_user_messages.append(f"workspace/uploads/{_fname}")
            logger.info(f"[Slack] Saved file {_fname} ({len(_r.content)} bytes)")
        except Exception as _e:
            logger.error(f"[Slack] Failed to download file {_fname}: {_e}")


    if not user_text and not _file_user_messages and slack_files:
        # Files were present but all downloads failed — still send ack so user knows we got the file event
        _file_names = ", ".join(_sf.get("name", "file") for _sf in slack_files)
        _ack = f"收到了文件 {_file_names}，不过我暂时无法下载其内容，请检查 Slack App 是否已授权 files:read 权限。"
        db.add(ChatMessage(agent_id=agent_id, user_id=platform_user_id, role="assistant",
                           content=_ack, conversation_id=session_conv_id))
        sess.last_message_at = datetime.now(timezone.utc)
        await db.commit()
        if _bot_token and channel_id:
            await _send_slack_messages(_bot_token, channel_id, _ack)
        return {"ok": True}

    if _file_user_messages and not user_text:
        # Files downloaded, no text — store file paths as user message & send ack
        _file_content = " ".join(f"[file:{p.split('/')[-1]}]" for p in _file_user_messages)
        db.add(ChatMessage(agent_id=agent_id, user_id=platform_user_id, role="user",
                           content=_file_content, conversation_id=session_conv_id))
        await _asyncio.sleep(_random.uniform(1.0, 2.0))
        _ack = _random.choice(_FILE_ACK_MESSAGES)
        db.add(ChatMessage(agent_id=agent_id, user_id=platform_user_id, role="assistant",
                           content=_ack, conversation_id=session_conv_id))
        sess.last_message_at = datetime.now(timezone.utc)
        await db.commit()
        if _bot_token and channel_id:
            await _send_slack_messages(_bot_token, channel_id, _ack)
        return {"ok": True}

    # Append uploaded file paths to user message for context
    if _file_user_messages and user_text:
        user_text += "\n" + " ".join(f"[file:{p.split('/')[-1]}]" for p in _file_user_messages)

    # Save user message
    db.add(ChatMessage(agent_id=agent_id, user_id=platform_user_id, role="user", content=user_text, conversation_id=session_conv_id))
    sess.last_message_at = datetime.now(timezone.utc)
    await db.commit()

    # Set channel_file_sender contextvar for agent → user file delivery
    from app.services.agent_tools import channel_file_sender as _cfs_s
    async def _slack_file_sender(file_path, msg: str = ""):
        from pathlib import Path as _P
        _fp = _P(file_path)
        if not _bot_token or not channel_id:
            return
        async with _httpx.AsyncClient(timeout=60) as _hc:
            _upload_url_resp = await _hc.post(
                "https://slack.com/api/files.getUploadURLExternal",
                headers={"Authorization": f"Bearer {_bot_token}"},
                data={"filename": _fp.name, "length": str(_fp.stat().st_size)},
            )
            _ud = _upload_url_resp.json()
            if not _ud.get("ok"):
                raise RuntimeError(f"Slack upload URL error: {_ud}")
            _upload_url = _ud["upload_url"]
            _file_id = _ud["file_id"]
            await _hc.post(_upload_url, content=_fp.read_bytes(),
                            headers={"Content-Type": "application/octet-stream"})
            _complete = await _hc.post(
                "https://slack.com/api/files.completeUploadExternal",
                headers={"Authorization": f"Bearer {_bot_token}"},
                json={"files": [{"id": _file_id}], "channel_id": channel_id,
                      "initial_comment": msg or ""},
            )
            if not _complete.json().get("ok"):
                raise RuntimeError(f"Slack upload complete error: {_complete.json()}")
    _cfs_s_token = _cfs_s.set(_slack_file_sender)

    # Call LLM
    from app.api.feishu import _call_agent_llm
    reply_text = await _call_agent_llm(db, agent_id, user_text, history=history)
    _cfs_s.reset(_cfs_s_token)
    logger.info(f"[Slack] LLM reply: {reply_text[:80]}")

    # Save reply
    db.add(ChatMessage(agent_id=agent_id, user_id=platform_user_id, role="assistant", content=reply_text, conversation_id=session_conv_id))
    sess.last_message_at = datetime.now(timezone.utc)
    await db.commit()

    # Send to Slack (chunked)
    bot_token = config.app_secret or ""
    if bot_token and channel_id:
        try:
            await _send_slack_messages(bot_token, channel_id, reply_text)
        except Exception as e:
            logger.error(f"[Slack] Failed to send: {e}")

    return {"ok": True}
