"""Discord Gateway (WebSocket) Manager.

Maintains long-lived Gateway connections for agents configured with
connection_mode='gateway'.  When a user @mentions the bot or sends it a
DM, the message is forwarded to the agent's LLM pipeline — exactly like
the Feishu WebSocket manager.

Requires:  pip install discord.py>=2.3.0
"""

import asyncio
import os
import uuid
from typing import Dict, Optional

from loguru import logger
from sqlalchemy import select

from app.database import async_session
from app.models.channel_config import ChannelConfig

try:
    import discord
    _HAS_DISCORD = True
except ImportError:
    discord = None  # type: ignore
    _HAS_DISCORD = False

if not _HAS_DISCORD:
    logger.warning(
        "[Discord GW] discord.py package not installed. "
        "Discord Gateway features will be disabled. "
        "Install with: pip install discord.py"
    )

DISCORD_MSG_LIMIT = 2000  # Discord message character limit


class DiscordGatewayManager:
    """Manages Discord Gateway bot clients for all agents."""

    def __init__(self):
        self._clients: Dict[uuid.UUID, discord.Client] = {}
        self._tasks: Dict[uuid.UUID, asyncio.Task] = {}

    async def start_client(
        self,
        agent_id: uuid.UUID,
        bot_token: str,
        *,
        stop_existing: bool = True,
    ):
        """Start a Discord Gateway client for the given agent."""
        if not _HAS_DISCORD:
            logger.warning("[Discord GW] discord.py not installed, cannot start client")
            return
        if not bot_token:
            logger.warning(f"[Discord GW] Missing bot_token for {agent_id}, skipping")
            return

        logger.info(f"[Discord GW] Starting Gateway client for agent {agent_id}")

        # Stop existing client if any
        if stop_existing and agent_id in self._tasks:
            await self.stop_client(agent_id)

        intents = discord.Intents.default()
        intents.message_content = True  # Required to read message text

        client = discord.Client(intents=intents)
        self._clients[agent_id] = client

        @client.event
        async def on_ready():
            logger.info(
                f"[Discord GW] Bot connected for agent {agent_id}: "
                f"{client.user.name}#{client.user.discriminator} ({client.user.id})"
            )

        @client.event
        async def on_message(message: discord.Message):
            # Ignore own messages
            if message.author == client.user:
                return

            # Respond to DMs or @mentions
            is_dm = message.guild is None
            is_mention = client.user in message.mentions if message.mentions else False

            if not is_dm and not is_mention:
                return

            # Strip the @mention from the message text
            user_text = message.content
            if is_mention and client.user:
                user_text = user_text.replace(f"<@{client.user.id}>", "").strip()
                user_text = user_text.replace(f"<@!{client.user.id}>", "").strip()

            if not user_text:
                return

            logger.info(
                f"[Discord GW] Message for agent {agent_id} from "
                f"{message.author.name}: {user_text[:80]}"
            )

            # Show typing indicator while processing
            async with message.channel.typing():
                reply = await self._handle_message(agent_id, message, user_text)

            # Send reply, chunked if needed
            if reply:
                chunks = [reply[i:i + DISCORD_MSG_LIMIT] for i in range(0, len(reply), DISCORD_MSG_LIMIT)]
                for chunk in chunks:
                    await message.reply(chunk, mention_author=False)

        # Run the bot in a background task
        proxy = os.environ.get("DISCORD_PROXY") or os.environ.get("HTTPS_PROXY") or None

        async def _run_bot():
            try:
                # discord.py supports proxy via the `proxy` kwarg on Client.start
                await client.start(bot_token, reconnect=True)
            except asyncio.CancelledError:
                logger.info(f"[Discord GW] Bot task cancelled for agent {agent_id}")
            except discord.LoginFailure:
                logger.error(f"[Discord GW] Invalid bot token for agent {agent_id}")
            except Exception as e:
                logger.error(f"[Discord GW] Bot error for agent {agent_id}: {e}", exc_info=True)
            finally:
                if not client.is_closed():
                    await client.close()
                self._clients.pop(agent_id, None)

        task = asyncio.create_task(_run_bot(), name=f"discord-gw-{str(agent_id)[:8]}")
        self._tasks[agent_id] = task
        logger.info(f"[Discord GW] Gateway task scheduled for agent {agent_id}")

    async def _handle_message(
        self,
        agent_id: uuid.UUID,
        message: "discord.Message",
        user_text: str,
    ) -> Optional[str]:
        """Process an incoming Discord message through the agent LLM."""
        try:
            from app.models.audit import ChatMessage
            from app.models.agent import Agent as AgentModel
            from app.api.feishu import _call_agent_llm
            from app.services.channel_session import find_or_create_channel_session
            from app.models.user import User as _User
            from app.core.security import hash_password as _hp
            from datetime import datetime, timezone
            import uuid as _uuid

            sender_id = str(message.author.id)
            channel_id = str(message.channel.id)
            conv_id = (
                f"discord_dm_{sender_id}"
                if message.guild is None
                else f"discord_{channel_id}_{sender_id}"
            )

            async with async_session() as db:
                # Load agent
                agent_r = await db.execute(
                    select(AgentModel).where(AgentModel.id == agent_id)
                )
                agent_obj = agent_r.scalar_one_or_none()
                if not agent_obj:
                    return "Agent not found."
                creator_id = agent_obj.creator_id
                ctx_size = agent_obj.context_window_size or 20

                # Find or create platform user for this Discord sender
                _username = f"discord_{sender_id}"
                _u_r = await db.execute(
                    select(_User).where(_User.username == _username)
                )
                _platform_user = _u_r.scalar_one_or_none()
                if not _platform_user:
                    _display = message.author.display_name or message.author.name or f"Discord User {sender_id[:8]}"
                    _platform_user = _User(
                        username=_username,
                        email=f"{_username}@discord.local",
                        password_hash=_hp(_uuid.uuid4().hex),
                        display_name=_display,
                        role="member",
                        tenant_id=agent_obj.tenant_id,
                    )
                    db.add(_platform_user)
                    await db.flush()
                platform_user_id = _platform_user.id

                # Find or create session
                sess = await find_or_create_channel_session(
                    db=db,
                    agent_id=agent_id,
                    user_id=platform_user_id,
                    external_conv_id=conv_id,
                    source_channel="discord",
                    first_message_title=user_text,
                )
                session_conv_id = str(sess.id)

                # Load history
                history_r = await db.execute(
                    select(ChatMessage)
                    .where(
                        ChatMessage.agent_id == agent_id,
                        ChatMessage.conversation_id == session_conv_id,
                    )
                    .order_by(ChatMessage.created_at.desc())
                    .limit(ctx_size)
                )
                history = [
                    {"role": m.role, "content": m.content}
                    for m in reversed(history_r.scalars().all())
                ]

                # Save user message
                db.add(ChatMessage(
                    agent_id=agent_id,
                    user_id=platform_user_id,
                    role="user",
                    content=user_text,
                    conversation_id=session_conv_id,
                ))
                sess.last_message_at = datetime.now(timezone.utc)
                await db.commit()

                # Call LLM
                reply_text = await _call_agent_llm(
                    db, agent_id, user_text, history=history
                )
                logger.info(f"[Discord GW] LLM reply for {agent_id}: {reply_text[:80]}")

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

                return reply_text

        except Exception as e:
            logger.error(
                f"[Discord GW] Error handling message for {agent_id}: {e}",
                exc_info=True,
            )
            return f"An error occurred while processing your message: {str(e)[:100]}"

    async def stop_client(self, agent_id: uuid.UUID):
        """Stop a running Discord Gateway client."""
        if agent_id in self._tasks:
            task = self._tasks.pop(agent_id)
            if not task.done():
                task.cancel()
                logger.info(f"[Discord GW] Cancelled task for agent {agent_id}")
        if agent_id in self._clients:
            client = self._clients.pop(agent_id)
            try:
                if not client.is_closed():
                    await client.close()
            except Exception as e:
                logger.error(f"[Discord GW] Error closing client for {agent_id}: {e}")

    async def start_all(self):
        """Start Gateway clients for all configured Discord agents."""
        if not _HAS_DISCORD:
            logger.info("[Discord GW] discord.py not installed, skipping Discord Gateway init")
            return
        logger.info("[Discord GW] Initializing all active Discord Gateway channels...")
        async with async_session() as db:
            result = await db.execute(
                select(ChannelConfig).where(
                    ChannelConfig.is_configured == True,
                    ChannelConfig.channel_type == "discord",
                )
            )
            configs = result.scalars().all()

        for config in configs:
            extra = config.extra_config or {}
            mode = extra.get("connection_mode", "webhook")
            if mode == "gateway":
                bot_token = config.app_secret
                if bot_token:
                    await self.start_client(
                        config.agent_id, bot_token, stop_existing=False
                    )
                else:
                    logger.warning(
                        f"[Discord GW] Skipping agent {config.agent_id}: missing bot_token"
                    )

    def status(self) -> dict:
        """Return status of all active Gateway tasks."""
        return {
            str(aid): not self._tasks[aid].done()
            for aid in self._tasks
        }


discord_gateway_manager = DiscordGatewayManager()
""" is the module-level singleton, imported by main.py and discord_bot.py."""
