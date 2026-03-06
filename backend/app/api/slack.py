"""Slack Bot Channel API routes."""

import hashlib
import hmac
import time
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
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
    print(f"[Slack] Webhook for {agent_id}: type={body.get('type')}")

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

    if not user_text:
        return {"ok": True}

    channel_id = event.get("channel", "")
    sender_id = event.get("user", "")
    conv_id = f"slack_{channel_id}_{sender_id}" if channel_id else f"slack_dm_{sender_id}"

    print(f"[Slack] Message from={sender_id}, channel={channel_id}: {user_text[:80]}")

    # Load history
    from app.models.audit import ChatMessage
    from app.models.agent import Agent as AgentModel
    from app.services.channel_session import find_or_create_channel_session
    agent_r = await db.execute(select(AgentModel).where(AgentModel.id == agent_id))
    agent_obj = agent_r.scalar_one_or_none()
    creator_id = agent_obj.creator_id if agent_obj else agent_id
    ctx_size = agent_obj.context_window_size if agent_obj else 20

    # Find-or-create session for this Slack conversation
    sess = await find_or_create_channel_session(
        db=db,
        agent_id=agent_id,
        user_id=creator_id,  # Slack doesn't resolve to platform users yet
        external_conv_id=conv_id,
        source_channel="slack",
        first_message_title=user_text,
    )
    session_conv_id = str(sess.id)

    history_r = await db.execute(
        select(ChatMessage)
        .where(ChatMessage.agent_id == agent_id, ChatMessage.conversation_id == session_conv_id)
        .order_by(ChatMessage.created_at.desc())
        .limit(ctx_size)
    )
    history = [{"role": m.role, "content": m.content} for m in reversed(history_r.scalars().all())]

    # Save user message
    from datetime import datetime, timezone
    db.add(ChatMessage(agent_id=agent_id, user_id=creator_id, role="user", content=user_text, conversation_id=session_conv_id))
    sess.last_message_at = datetime.now(timezone.utc)
    await db.commit()

    # Call LLM
    from app.api.feishu import _call_agent_llm
    reply_text = await _call_agent_llm(db, agent_id, user_text, history=history)
    print(f"[Slack] LLM reply: {reply_text[:80]}")

    # Save reply
    db.add(ChatMessage(agent_id=agent_id, user_id=creator_id, role="assistant", content=reply_text, conversation_id=session_conv_id))
    sess.last_message_at = datetime.now(timezone.utc)
    await db.commit()

    # Send to Slack (chunked)
    bot_token = config.app_secret or ""
    if bot_token and channel_id:
        try:
            await _send_slack_messages(bot_token, channel_id, reply_text)
        except Exception as e:
            print(f"[Slack] Failed to send: {e}")

    return {"ok": True}
