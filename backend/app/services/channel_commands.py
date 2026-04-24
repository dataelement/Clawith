"""Channel command handler for external channels (DingTalk, Feishu, etc.)

Supports slash commands like /new to reset session context.
"""

import uuid
from datetime import datetime, timezone
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.chat_session import ChatSession
from app.services.channel_session import find_or_create_channel_session


COMMANDS = {"/new", "/reset"}


def is_channel_command(text: str) -> bool:
    """Check if the message is a recognized channel command."""
    stripped = text.strip().lower()
    return stripped in COMMANDS


async def handle_channel_command(
    db: AsyncSession,
    command: str,
    agent_id: uuid.UUID,
    user_id: uuid.UUID,
    external_conv_id: str,
    source_channel: str,
) -> dict:
    """Handle a channel command and return response info.

    Returns:
        {"action": "new_session", "message": "..."}
    """
    cmd = command.strip().lower()

    if cmd in ("/new", "/reset"):
        # Find current session. Scope by source_channel as well so we never
        # accidentally archive a session from a different channel that happens
        # to share the same external_conv_id (defensive against future changes
        # to the per-channel ID prefix scheme).
        result = await db.execute(
            select(ChatSession).where(
                ChatSession.agent_id == agent_id,
                ChatSession.external_conv_id == external_conv_id,
                ChatSession.source_channel == source_channel,
            )
        )
        old_session = result.scalar_one_or_none()

        if old_session:
            # Rename old external_conv_id so find_or_create will make a new one
            now = datetime.now(timezone.utc)
            old_session.external_conv_id = (
                f"{external_conv_id}__archived_{now.strftime('%Y%m%d_%H%M%S')}"
            )
            await db.flush()

        # Defer session creation to the user's next message so its title
        # auto-names from that message (via find_or_create_channel_session)
        # instead of being locked to a hard-coded placeholder.
        return {
            "action": "new_session",
            "message": "已开启新对话，之前的上下文已清除。",
        }

    return {"action": "unknown", "message": f"未知命令: {cmd}"}
