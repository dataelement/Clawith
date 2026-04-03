"""WeChat Personal Channel API routes.

This module implements the WeChat channel for Clawith, enabling each agent
to interact with users via personal WeChat through the wechatbot SDK.

Architecture (Python SDK Integration):
    Python Backend (FastAPI)
           │
           ├── WeChatBotManager
           │        │
           │        └── WeChatBot (SDK) ──► WeChat iLink API

Each agent can have its own WeChat bot instance with independent credentials.
"""

import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
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
from app.services.agent_tools import WORKSPACE_ROOT, channel_file_sender
from app.services.channel_session import find_or_create_channel_session
from app.services.channel_user_service import channel_user_service
from app.services.wechatbot.types import MediaExtensions, WeChatConstants

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
    is_logged_in: bool | None = None

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

    The configuration initiates a QR login flow. After scanning the QR code,
    the bot stores credentials and starts polling for messages.

    Request body (optional):
        - storage_dir: Custom directory for credential storage
        - auto_reconnect: Enable automatic reconnection on session expiry (default: true)
        - force: Force re-login even if already logged in

    Returns:
        - is_configured: Whether the channel is configured
        - qr_url: QR code URL for WeChat login (scan with WeChat app)
    """
    try:
        agent, _ = await check_agent_access(db, current_user, agent_id)
        if not is_agent_creator(current_user, agent):
            raise HTTPException(status_code=403, detail="Only creator can configure channel")

        storage_dir = data.get("storage_dir", "").strip()
        auto_reconnect = data.get("auto_reconnect", True)
        force_login = data.get("force", False)

        extra_config = {
            "storage_dir": storage_dir,
            "auto_reconnect": auto_reconnect,
            "connection_mode": "python_sdk",
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

        await db.commit()

        # Initiate login via Python SDK
        logger.info(f"[WeChat] Initiating login for agent {agent_id}, force={force_login}")
        from app.services.wechat_bot_manager import wechat_bot_manager
        qr_url, error_msg = await wechat_bot_manager.initiate_login(agent_id, storage_dir, force=force_login)

        # Get login status
        status = await wechat_bot_manager.get_status(agent_id)
        is_logged_in = status.get("is_logged_in", False)

        if error_msg:
            logger.warning(f"[WeChat] Failed to initiate login for agent {agent_id}: {error_msg}")
        elif not qr_url:
            logger.info(f"[WeChat] Login not needed or already logged in for agent {agent_id}")
        else:
            logger.info(f"[WeChat] QR code ready for agent {agent_id}")

        return WeChatConfigResponse(
            id=config.id,
            agent_id=config.agent_id,
            channel_type=config.channel_type,
            is_configured=config.is_configured,
            extra_config=config.extra_config,
            qr_url=qr_url,
            error=error_msg,
            is_logged_in=is_logged_in,
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"[WeChat] Error configuring channel for agent {agent_id}: {e}")
        raise HTTPException(status_code=500, detail=f"WeChat login failed: {str(e)}") from e


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

    from app.services.wechat_bot_manager import wechat_bot_manager
    status = await wechat_bot_manager.get_status(agent_id)

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

    from app.services.wechat_bot_manager import wechat_bot_manager
    qr_url = await wechat_bot_manager.get_qr_url(agent_id)

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

    # Remove the bot instance (stops and clears credentials)
    try:
        from app.services.wechat_bot_manager import wechat_bot_manager
        await wechat_bot_manager.remove_client(agent_id)
    except Exception as e:
        logger.warning(f"[WeChat] Error removing bot: {e}")

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
# Message Processing (called by Bot Manager when messages arrive)
# ═══════════════════════════════════════════════════════════════════════

async def process_incoming_wechat_message(
    agent_id: uuid.UUID,
    msg,
) -> dict:
    """Process a WeChat message through the agent's LLM pipeline.

    Called by the WeChatBotManager when a message is received.

    Args:
        agent_id: The agent UUID
        msg: IncomingMessage from wechatbot SDK

    Returns:
        dict with keys:
            - reply: str, the text reply
            - files: list of dict with file info (path, file_name, type, caption)
    """
    from app.database import async_session
    from app.models.audit import ChatMessage
    from app.models.agent import Agent as AgentModel, DEFAULT_CONTEXT_WINDOW_SIZE
    from app.api.feishu import _call_agent_llm
    from app.services.wechat_bot_manager import wechat_bot_manager

    # Detect group message from userId format (group IDs end with @chatroom)
    is_group = msg.user_id.endswith(WeChatConstants.GROUP_ID_SUFFIX)
    group_id = msg.user_id if is_group else ""

    user_id = msg.user_id
    user_name = msg.user_id  # WeChat doesn't always provide display name in message
    text = msg.text or ""

    logger.info(f"[WeChat] Message from {user_name} ({user_id}): {text[:80]}")

    # Download and save media files to workspace
    saved_files = []
    bot_instance = wechat_bot_manager._bots.get(agent_id)

    if bot_instance and (msg.images or msg.files or msg.videos):
        workspace_path = WORKSPACE_ROOT / str(agent_id) / "workspace"
        workspace_path.mkdir(parents=True, exist_ok=True)

        # Download media using SDK
        try:
            downloaded = await bot_instance.download_media(msg)
            if downloaded:
                file_data = downloaded.data

                # File validation: check size
                if len(file_data) == 0:
                    logger.warning("[WeChat] Skipping empty media file")
                elif len(file_data) > WeChatConstants.MAX_FILE_SIZE:
                    logger.warning(
                        f"[WeChat] File too large: {len(file_data)} bytes "
                        f"(max: {WeChatConstants.MAX_FILE_SIZE})"
                    )
                else:
                    file_name = downloaded.file_name or f"media_{int(datetime.now().timestamp())}"

                    # Determine file extension by type
                    if downloaded.type == "image":
                        if not any(file_name.endswith(ext) for ext in MediaExtensions.IMAGE):
                            file_name = file_name + ".png"
                    elif downloaded.type == "video":
                        if not any(file_name.endswith(ext) for ext in MediaExtensions.VIDEO):
                            file_name = file_name + ".mp4"

                    # Handle duplicate filenames
                    file_path = workspace_path / file_name
                    counter = 1
                    original_stem = file_path.stem
                    original_suffix = file_path.suffix
                    while file_path.exists():
                        file_path = workspace_path / f"{original_stem}_{counter}{original_suffix}"
                        counter += 1

                    file_path.write_bytes(file_data)
                    saved_files.append({
                        "path": f"workspace/{file_path.name}",
                        "file_name": file_path.name,
                        "type": downloaded.type,
                    })
                    logger.info(f"[WeChat] Saved media: {file_path.name} ({len(file_data)} bytes)")
        except Exception as e:
            logger.error(f"[WeChat] Failed to download media: {e}")

    # Set channel_file_sender ContextVar so agent can send files directly to user
    async def _wechat_file_sender(file_path, msg: str = ""):
        """Send file to WeChat user via bot."""
        try:
            await wechat_bot_manager.send_file(agent_id, user_id, file_path, caption=msg)
        except Exception as e:
            logger.error(f"[WeChat] Failed to send file: {e}")
            raise

    _cfs_token = channel_file_sender.set(_wechat_file_sender)

    try:
        # Use a new db session for message processing
        async with async_session() as db:
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

                for file_rel in sorted(new_file_paths):
                    file_path = workspace_path / file_rel
                    file_name = file_path.name
                    ext = file_path.suffix.lower()

                    # Skip hidden files and temp files
                    if file_name.startswith('.') or file_name.endswith('.tmp'):
                        continue

                    # Determine media type using constants
                    if ext in MediaExtensions.IMAGE:
                        file_type = 'image'
                    elif ext in MediaExtensions.VIDEO:
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
                if len(new_files) > WeChatConstants.MAX_ATTACHED_FILES:
                    new_files = new_files[:WeChatConstants.MAX_ATTACHED_FILES]
                    logger.warning(f"[WeChat] Too many new files, limiting to {WeChatConstants.MAX_ATTACHED_FILES}")

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

            # Send reply and files via bot
            if reply_text:
                await wechat_bot_manager.send_text(agent_id, user_id, reply_text)

            for file_info in new_files:
                file_path = WORKSPACE_ROOT / agent_id / file_info["path"]
                await wechat_bot_manager.send_file(agent_id, user_id, file_path)

            return {"reply": reply_text, "files": new_files}

    finally:
        # Reset ContextVar to avoid affecting other requests
        channel_file_sender.reset(_cfs_token)
