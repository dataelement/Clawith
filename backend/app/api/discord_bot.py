"""Discord Bot Channel API routes (slash command interactions)."""

import os
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

router = APIRouter(tags=["discord"])

DISCORD_MSG_LIMIT = 2000  # Discord message char limit


# ─── Config CRUD ────────────────────────────────────────

@router.post("/agents/{agent_id}/discord-channel", response_model=ChannelConfigOut, status_code=201)
async def configure_discord_channel(
    agent_id: uuid.UUID,
    data: dict,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Configure Discord bot for an agent. Fields: application_id, bot_token, public_key."""
    agent, _ = await check_agent_access(db, current_user, agent_id)
    if not is_agent_creator(current_user, agent):
        raise HTTPException(status_code=403, detail="Only creator can configure channel")

    application_id = data.get("application_id", "").strip()
    bot_token = data.get("bot_token", "").strip()
    public_key = data.get("public_key", "").strip()
    if not application_id or not bot_token or not public_key:
        raise HTTPException(status_code=422, detail="application_id, bot_token, and public_key are required")

    result = await db.execute(
        select(ChannelConfig).where(
            ChannelConfig.agent_id == agent_id,
            ChannelConfig.channel_type == "discord",
        )
    )
    existing = result.scalar_one_or_none()
    if existing:
        existing.app_id = application_id
        existing.app_secret = bot_token
        existing.encrypt_key = public_key
        existing.is_configured = True
        await db.flush()
        try:
            reg = await _register_slash_commands(application_id, bot_token)
            print(f"[Discord] Slash command registration (update): {reg['status']}")
        except Exception as e:
            print(f"[Discord] Warning: could not register slash commands: {e}")
        return ChannelConfigOut.model_validate(existing)

    config = ChannelConfig(
        agent_id=agent_id,
        channel_type="discord",
        app_id=application_id,
        app_secret=bot_token,
        encrypt_key=public_key,
        is_configured=True,
    )
    db.add(config)
    await db.flush()
    try:
        reg = await _register_slash_commands(application_id, bot_token)
        print(f"[Discord] Slash command registration (new): {reg['status']}")
    except Exception as e:
        print(f"[Discord] Warning: could not register slash commands: {e}")
    return ChannelConfigOut.model_validate(config)


@router.get("/agents/{agent_id}/discord-channel", response_model=ChannelConfigOut)
async def get_discord_channel(
    agent_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await check_agent_access(db, current_user, agent_id)
    result = await db.execute(
        select(ChannelConfig).where(
            ChannelConfig.agent_id == agent_id,
            ChannelConfig.channel_type == "discord",
        )
    )
    config = result.scalar_one_or_none()
    if not config:
        raise HTTPException(status_code=404, detail="Discord not configured")
    return ChannelConfigOut.model_validate(config)


@router.get("/agents/{agent_id}/discord-channel/webhook-url")
async def get_discord_webhook_url(agent_id: uuid.UUID, request: Request, db: AsyncSession = Depends(get_db)):
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
    return {"webhook_url": f"{public_base}/api/channel/discord/{agent_id}/webhook"}


@router.delete("/agents/{agent_id}/discord-channel", status_code=204)
async def delete_discord_channel(
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
            ChannelConfig.channel_type == "discord",
        )
    )
    config = result.scalar_one_or_none()
    if not config:
        raise HTTPException(status_code=404, detail="Discord not configured")
    await db.delete(config)


# ─── Slash Command Registration ─────────────────────────

async def _register_slash_commands(application_id: str, bot_token: str) -> dict:
    """Register /ask global slash command with Discord API."""
    import httpx
    import os
    command = {
        "name": "ask",
        "description": "Ask the AI agent a question",
        "options": [
            {
                "name": "message",
                "description": "Your question or message to the agent",
                "type": 3,   # STRING
                "required": True,
            }
        ],
    }
    url = f"https://discord.com/api/v10/applications/{application_id}/commands"
    proxy = os.environ.get("DISCORD_PROXY") or os.environ.get("HTTPS_PROXY") or None
    async with httpx.AsyncClient(timeout=15, proxy=proxy) as client:
        resp = await client.put(
            url,
            headers={"Authorization": f"Bot {bot_token}", "Content-Type": "application/json"},
            json=[command],
        )
        return {"status": resp.status_code, "body": resp.text}


# ─── Interactions Webhook ───────────────────────────────

def _verify_discord_signature(public_key: str, body: bytes, headers: dict) -> bool:
    """Verify Discord ed25519 signature."""
    try:
        from nacl.signing import VerifyKey
        from nacl.exceptions import BadSignatureError

        timestamp = headers.get("x-signature-timestamp", "")
        signature = headers.get("x-signature-ed25519", "")
        if not timestamp or not signature:
            return False

        verify_key = VerifyKey(bytes.fromhex(public_key))
        verify_key.verify(f"{timestamp}".encode() + body, bytes.fromhex(signature))
        return True
    except Exception:
        return False


async def _send_discord_followup(application_id: str, bot_token: str, interaction_token: str, text: str) -> None:
    """Send follow-up message(s) to Discord Interactions, chunked at 2000 chars."""
    import httpx
    chunks = [text[i:i + DISCORD_MSG_LIMIT] for i in range(0, len(text), DISCORD_MSG_LIMIT)]
    proxy = os.environ.get("DISCORD_PROXY") or os.environ.get("HTTPS_PROXY") or None
    async with httpx.AsyncClient(timeout=10, proxy=proxy) as client:
        for i, chunk in enumerate(chunks):
            if i == 0:
                # Edit the original deferred response
                await client.patch(
                    f"https://discord.com/api/v10/webhooks/{application_id}/{interaction_token}/messages/@original",
                    headers={"Authorization": f"Bot {bot_token}", "Content-Type": "application/json"},
                    json={"content": chunk},
                )
            else:
                # Additional chunks as follow-up messages
                await client.post(
                    f"https://discord.com/api/v10/webhooks/{application_id}/{interaction_token}",
                    headers={"Authorization": f"Bot {bot_token}", "Content-Type": "application/json"},
                    json={"content": chunk},
                )


@router.post("/channel/discord/{agent_id}/webhook")
async def discord_interaction_webhook(
    agent_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Handle Discord Interaction webhooks (PING + slash commands)."""
    body_bytes = await request.body()

    # Get channel config
    result = await db.execute(
        select(ChannelConfig).where(
            ChannelConfig.agent_id == agent_id,
            ChannelConfig.channel_type == "discord",
        )
    )
    config = result.scalar_one_or_none()
    if not config:
        return Response(status_code=404)

    # Verify Discord signature
    public_key = config.encrypt_key or ""
    if public_key and not _verify_discord_signature(public_key, body_bytes, dict(request.headers)):
        return Response(content="Invalid signature", status_code=401)

    import json
    import asyncio
    body = json.loads(body_bytes)
    interaction_type = body.get("type", 0)

    # Type 1: PING — Discord URL verification
    if interaction_type == 1:
        return {"type": 1}

    # Type 2: APPLICATION_COMMAND (slash command)
    if interaction_type == 2:
        data_obj = body.get("data", {})
        command_name = data_obj.get("name", "")
        options = data_obj.get("options", [])
        user_text = ""
        for opt in options:
            if opt.get("name") == "message":
                user_text = opt.get("value", "").strip()
                break

        if not user_text:
            return {"type": 4, "data": {"content": "⚠️ 请提供消息内容。Usage: `/ask message:<你的问题>`"}}

        interaction_token = body.get("token", "")
        application_id = config.app_id or ""
        sender_id = body.get("member", {}).get("user", {}).get("id") or body.get("user", {}).get("id", "")
        channel_id = body.get("channel_id", "")
        conv_id = f"discord_{channel_id}_{sender_id}" if channel_id else f"discord_dm_{sender_id}"

        print(f"[Discord] /{command_name} from {sender_id}: {user_text[:80]}")

        # Defer response immediately (Discord requires response within 3 seconds)
        # We return type 5 (DEFERRED_CHANNEL_MESSAGE_WITH_SOURCE) and reply later
        async def handle_in_background():
            from app.models.audit import ChatMessage
            from app.models.agent import Agent as AgentModel
            from app.api.feishu import _call_agent_llm
            from app.services.channel_session import find_or_create_channel_session
            from app.database import async_session
            from datetime import datetime, timezone

            async with async_session() as bg_db:
                # Load agent
                agent_r = await bg_db.execute(select(AgentModel).where(AgentModel.id == agent_id))
                agent_obj = agent_r.scalar_one_or_none()
                creator_id = agent_obj.creator_id if agent_obj else agent_id
                ctx_size = agent_obj.context_window_size if agent_obj else 20

                # Find-or-create ChatSession for this Discord conversation
                sess = await find_or_create_channel_session(
                    db=bg_db,
                    agent_id=agent_id,
                    user_id=creator_id,
                    external_conv_id=conv_id,
                    source_channel="discord",
                    first_message_title=user_text,
                )
                session_conv_id = str(sess.id)

                # Load history from session
                history_r = await bg_db.execute(
                    select(ChatMessage)
                    .where(ChatMessage.agent_id == agent_id, ChatMessage.conversation_id == session_conv_id)
                    .order_by(ChatMessage.created_at.desc())
                    .limit(ctx_size)
                )
                history = [{"role": m.role, "content": m.content} for m in reversed(history_r.scalars().all())]

                # Save user message
                bg_db.add(ChatMessage(agent_id=agent_id, user_id=creator_id, role="user", content=user_text, conversation_id=session_conv_id))
                sess.last_message_at = datetime.now(timezone.utc)
                await bg_db.commit()

                # Call LLM
                reply_text = await _call_agent_llm(bg_db, agent_id, user_text, history=history)
                print(f"[Discord] LLM reply: {reply_text[:80]}")

                # Save reply
                bg_db.add(ChatMessage(agent_id=agent_id, user_id=creator_id, role="assistant", content=reply_text, conversation_id=session_conv_id))
                sess.last_message_at = datetime.now(timezone.utc)
                await bg_db.commit()

                # Bot token stored in config — read from DB to avoid detached ORM issues
                from sqlalchemy import select as _sel
                cfg_r = await bg_db.execute(_sel(ChannelConfig).where(
                    ChannelConfig.agent_id == agent_id,
                    ChannelConfig.channel_type == "discord",
                ))
                cfg = cfg_r.scalar_one_or_none()
                bot_token_bg = cfg.app_secret if cfg else ""
                app_id_bg = cfg.app_id if cfg else ""

                # Send chunked reply via Discord follow-up
                if bot_token_bg and interaction_token and app_id_bg:
                    try:
                        await _send_discord_followup(app_id_bg, bot_token_bg, interaction_token, reply_text)
                    except Exception as e:
                        print(f"[Discord] Failed to send follow-up: {e}")

        asyncio.create_task(handle_in_background())
        # Return DEFERRED_CHANNEL_MESSAGE_WITH_SOURCE — shows "thinking..." to user
        return {"type": 5}

    # Unsupported interaction type
    return {"type": 1}
