"""WeChat Personal Channel API routes.

This module implements the WeChat channel for Clawith, enabling each agent
to interact with users via personal WeChat through the wechatbot SDK.

Architecture:
    Python Backend <--HTTP--> Node.js Gateway <--iLink--> WeChat

The Node.js gateway service manages WeChat connections and forwards
messages to this Python backend for LLM processing.
"""

import os
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from loguru import logger
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.permissions import check_agent_access, is_agent_creator
from app.core.security import get_current_user
from app.database import get_db
from app.models.channel_config import ChannelConfig
from app.models.user import User
from app.schemas.schemas import ChannelConfigOut

router = APIRouter(tags=["wechat"])


# ─── Response Models ─────────────────────────────────────────────────────

class WeChatConfigResponse(BaseModel):
    """Response model for WeChat channel configuration."""
    id: uuid.UUID
    agent_id: uuid.UUID
    channel_type: str
    is_configured: bool
    extra_config: dict | None = None
    qr_url: str | None = None
    error: str | None = None

    model_config = {"from_attributes": True}


# ═══════════════════════════════════════════════════════════════════════
# Config CRUD
# ═══════════════════════════════════════════════════════════════════════

@router.post("/agents/{agent_id}/wechat-channel", response_model=WeChatConfigResponse, status_code=201)
async def configure_wechat_channel(
    agent_id: uuid.UUID,
    data: dict,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Configure WeChat personal channel for an agent.

    The configuration initiates a QR login flow handled by the Node.js gateway.
    After scanning the QR code, the gateway stores credentials and starts polling.

    Request body (optional):
        - storage_dir: Custom directory for credential storage
        - auto_reconnect: Enable automatic reconnection on session expiry (default: true)

    Returns:
        - is_configured: Whether the channel is configured
        - qr_url: QR code URL for WeChat login (scan with WeChat app)
    """
    agent, _ = await check_agent_access(db, current_user, agent_id)
    if not is_agent_creator(current_user, agent):
        raise HTTPException(status_code=403, detail="Only creator can configure channel")

    storage_dir = data.get("storage_dir", "").strip()
    auto_reconnect = data.get("auto_reconnect", True)
    force_login = data.get("force", False)

    extra_config = {
        "storage_dir": storage_dir,
        "auto_reconnect": auto_reconnect,
        "connection_mode": "gateway",
    }

    # Check for existing config
    result = await db.execute(
        select(ChannelConfig).where(
            ChannelConfig.agent_id == agent_id,
            ChannelConfig.channel_type == "wechat",
        )
    )
    existing = result.scalar_one_or_none()

    if existing:
        existing.extra_config = extra_config
        existing.is_configured = True
        await db.flush()
        config = existing
    else:
        config = ChannelConfig(
            agent_id=agent_id,
            channel_type="wechat",
            extra_config=extra_config,
            is_configured=True,
        )
        db.add(config)
        await db.flush()

    # Request QR login from Node.js gateway
    from app.services.wechat_gateway import wechat_gateway_manager
    qr_url, error_msg = await wechat_gateway_manager.initiate_login(agent_id, storage_dir, force=force_login)

    if error_msg:
        logger.warning(f"[WeChat] Failed to initiate login for agent {agent_id}: {error_msg}")
    elif not qr_url:
        logger.info(f"[WeChat] Login not needed or already logged in for agent {agent_id}")

    # Return config with qr_url for frontend to display
    return WeChatConfigResponse(
        id=config.id,
        agent_id=config.agent_id,
        channel_type=config.channel_type,
        is_configured=config.is_configured,
        extra_config=config.extra_config,
        qr_url=qr_url,
        error=error_msg,
    )


@router.get("/agents/{agent_id}/wechat-channel", response_model=ChannelConfigOut)
async def get_wechat_channel(
    agent_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get WeChat channel configuration for an agent."""
    await check_agent_access(db, current_user, agent_id)
    result = await db.execute(
        select(ChannelConfig).where(
            ChannelConfig.agent_id == agent_id,
            ChannelConfig.channel_type == "wechat",
        )
    )
    config = result.scalar_one_or_none()
    if not config:
        raise HTTPException(status_code=404, detail="WeChat channel not configured")
    return ChannelConfigOut.model_validate(config)


@router.get("/agents/{agent_id}/wechat-channel/status")
async def get_wechat_channel_status(
    agent_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get real-time connection status of WeChat channel.

    Returns:
        - is_running: Whether the bot is polling for messages
        - is_logged_in: Whether valid credentials exist
        - qr_url: QR code URL if waiting for scan (for login flow)
    """
    await check_agent_access(db, current_user, agent_id)

    from app.services.wechat_gateway import wechat_gateway_manager
    status = await wechat_gateway_manager.get_status(agent_id)

    return status


@router.get("/agents/{agent_id}/wechat-channel/qr")
async def get_wechat_qr_code(
    agent_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get QR code URL for WeChat login.

    Returns a URL that can be rendered as a QR code for scanning.
    """
    await check_agent_access(db, current_user, agent_id)

    from app.services.wechat_gateway import wechat_gateway_manager
    qr_url = await wechat_gateway_manager.get_qr_url(agent_id)

    if not qr_url:
        raise HTTPException(status_code=404, detail="No QR code available. Initiate login first.")

    return {"qr_url": qr_url}


@router.delete("/agents/{agent_id}/wechat-channel", status_code=204)
async def delete_wechat_channel(
    agent_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Delete WeChat channel configuration and stop the bot."""
    agent, _ = await check_agent_access(db, current_user, agent_id)
    if not is_agent_creator(current_user, agent):
        raise HTTPException(status_code=403, detail="Only creator can remove channel")

    # Remove the gateway client first (stops and clears credentials)
    try:
        from app.services.wechat_gateway import wechat_gateway_manager
        await wechat_gateway_manager.remove_client(agent_id)
    except Exception as e:
        logger.warning(f"[WeChat] Error removing gateway bot: {e}")

    # Delete config from database
    result = await db.execute(
        select(ChannelConfig).where(
            ChannelConfig.agent_id == agent_id,
            ChannelConfig.channel_type == "wechat",
        )
    )
    config = result.scalar_one_or_none()
    if config:
        await db.delete(config)
        await db.commit()


# ═══════════════════════════════════════════════════════════════════════
# Message Ingestion (called by Node.js gateway)
# ═══════════════════════════════════════════════════════════════════════

@router.post("/channel/wechat/{agent_id}/message")
async def wechat_message_webhook(
    agent_id: uuid.UUID,
    request: Request,
):
    """Receive messages from the Node.js WeChat gateway.

    This endpoint is called by the Node.js gateway when a WeChat message
    is received. It processes the message through the agent's LLM pipeline
    and returns the reply.

    Request body:
        {
            "user_id": "wxid_xxx",
            "user_name": "Display Name",
            "text": "Message content",
            "message_type": "text" | "image" | "file" | "video" | "voice",
            "is_group": false,
            "group_id": "xxx@chatroom" (if group message),
            "timestamp": "2024-01-01T00:00:00Z"
        }
    """
    from app.database import async_session

    body = await request.json()

    user_id = body.get("user_id", "")
    user_name = body.get("user_name", "")
    text = body.get("text", "")
    message_type = body.get("message_type", "text")
    is_group = body.get("is_group", False)
    group_id = body.get("group_id", "")

    if not user_id:
        return Response(content="Missing user_id", status_code=400)

    logger.info(f"[WeChat] Message from {user_name} ({user_id}): {text[:80]}")

    # Process message through agent LLM pipeline (uses its own db session)
    async with async_session() as db:
        result = await _process_wechat_message(
            db=db,
            agent_id=agent_id,
            user_id=user_id,
            user_name=user_name,
            text=text,
            is_group=is_group,
            group_id=group_id,
        )

    return result


async def _process_wechat_message(
    db: AsyncSession,
    agent_id: uuid.UUID,
    user_id: str,
    user_name: str,
    text: str,
    is_group: bool,
    group_id: str,
) -> dict:
    """Process a WeChat message through the agent's LLM pipeline.

    Returns:
        dict with keys:
            - reply: str, the text reply
            - files: list of dict with file info (path, file_name, type, caption)
    """
    from app.models.audit import ChatMessage
    from app.models.agent import Agent as AgentModel, DEFAULT_CONTEXT_WINDOW_SIZE
    from app.api.feishu import _call_agent_llm
    from app.services.channel_session import find_or_create_channel_session
    from app.services.channel_user_service import channel_user_service
    from app.services.agent_tools import WORKSPACE_ROOT, channel_file_sender
    from app.services.wechat_gateway import wechat_gateway_manager
    from datetime import datetime, timezone
    from pathlib import Path

    # Set channel_file_sender ContextVar so agent can send files directly to user
    async def _wechat_file_sender(file_path, msg: str = ""):
        """Send file to WeChat user via gateway."""
        try:
            await wechat_gateway_manager.send_file(agent_id, user_id, file_path, caption=msg)
        except Exception as e:
            logger.error(f"[WeChat] Failed to send file: {e}")
            raise

    _cfs_token = channel_file_sender.set(_wechat_file_sender)
    logger.info(f"[WeChat] Set channel_file_sender for agent={agent_id}, user={user_id}")

    try:  # Ensure ContextVar is reset after processing
        # Load agent
        agent_r = await db.execute(select(AgentModel).where(AgentModel.id == agent_id))
        agent_obj = agent_r.scalar_one_or_none()
        if not agent_obj:
            return {"reply": "Agent not found.", "files": []}

        creator_id = agent_obj.creator_id
        ctx_size = agent_obj.context_window_size or DEFAULT_CONTEXT_WINDOW_SIZE

        # Resolve or create platform user
        display_name = user_name or f"WeChat User {user_id[:8]}"
        _extra_info = {"name": display_name, "wechat_id": user_id}

        platform_user = await channel_user_service.resolve_channel_user(
            db=db,
            agent=agent_obj,
            channel_type="wechat",
            external_user_id=user_id,
            extra_info=_extra_info,
        )

        # Update display name if better name available
        if user_name and platform_user.display_name and platform_user.display_name.startswith("WeChat User "):
            platform_user.display_name = display_name
            await db.flush()

        platform_user_id = platform_user.id

        # Build conversation ID
        if is_group and group_id:
            conv_id = f"wechat_group_{group_id}"
            group_name = f"WeChat Group {group_id[:8]}"
        else:
            conv_id = f"wechat_dm_{user_id}"
            group_name = None

        # Find or create session
        sess = await find_or_create_channel_session(
            db=db,
            agent_id=agent_id,
            user_id=creator_id if is_group else platform_user_id,
            external_conv_id=conv_id,
            source_channel="wechat",
            first_message_title=text,
            is_group=is_group,
            group_name=group_name,
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
            agent_id=agent_id,
            user_id=platform_user_id,
            role="user",
            content=text,
            conversation_id=session_conv_id,
        ))
        sess.last_message_at = datetime.now(timezone.utc)
        await db.commit()

        # Track workspace files before LLM call (to detect new files)
        workspace_path = WORKSPACE_ROOT / str(agent_id) / "workspace"
        existing_files: set = set()
        if workspace_path.exists():
            existing_files = {f.relative_to(workspace_path) for f in workspace_path.rglob("*") if f.is_file()}

        # Call LLM
        try:
            reply_text = await _call_agent_llm(db, agent_id, text, history=history)
        except Exception as e:
            logger.exception(f"[WeChat] LLM error: {e}")
            reply_text = f"处理消息时发生错误: {str(e)[:100]}"

        # Detect new files created during LLM processing
        new_files: list[dict] = []
        if workspace_path.exists():
            current_files = {f.relative_to(workspace_path) for f in workspace_path.rglob("*") if f.is_file()}
            new_file_paths = current_files - existing_files

            # Media file extensions for auto-detection
            image_exts = {'.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp', '.heic', '.heif'}
            video_exts = {'.mp4', '.mov', '.avi', '.mkv', '.webm', '.3gp', '.m4v'}

            for file_rel in sorted(new_file_paths):
                file_path = workspace_path / file_rel
                file_name = file_path.name
                ext = file_path.suffix.lower()

                # Skip hidden files and temp files
                if file_name.startswith('.') or file_name.endswith('.tmp'):
                    continue

                # Determine media type
                if ext in image_exts:
                    file_type = 'image'
                elif ext in video_exts:
                    file_type = 'video'
                else:
                    file_type = 'file'

                new_files.append({
                    "path": f"workspace/{file_rel}",
                    "file_name": file_name,
                    "type": file_type,
                })
                logger.info(f"[WeChat] New file detected: {file_rel} ({file_type})")

            # Limit to avoid sending too many files at once
            if len(new_files) > 3:
                new_files = new_files[:3]
                logger.warning(f"[WeChat] Too many new files, limiting to 3")

        # Save reply
        db.add(ChatMessage(
            agent_id=agent_id,
            user_id=platform_user_id,
            role="assistant",
            content=reply_text,
            conversation_id=session_conv_id,
        ))
        sess.last_message_at = datetime.now(timezone.utc)
        await db.commit()

        logger.info(f"[WeChat] Reply to {user_id}: {reply_text[:80]}, files: {len(new_files)}")
        return {"reply": reply_text, "files": new_files}
    finally:
        # Reset ContextVar to avoid affecting other requests
        channel_file_sender.reset(_cfs_token)
