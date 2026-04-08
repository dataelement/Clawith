"""WeCom (企业微信) AI Bot WebSocket Long Connection Manager.

Uses the wecom-aibot-sdk-python SDK for WebSocket-based message reception.
No callback URL or domain verification needed.
"""

import asyncio
import uuid
from typing import Dict, Tuple

from loguru import logger
from sqlalchemy import select

from app.database import async_session
from app.models.channel_config import ChannelConfig


def _disable_wecom_sdk_proxy() -> None:
    """Force the WeCom SDK websocket path to bypass system proxies."""
    import wecom_aibot_sdk.ws as sdk_ws

    if getattr(sdk_ws.websockets.connect, "__clawith_no_proxy_patch__", False):
        return

    original_connect = sdk_ws.websockets.connect

    def connect_no_proxy(*args, **kwargs):
        kwargs.setdefault("proxy", None)
        return original_connect(*args, **kwargs)

    connect_no_proxy.__clawith_no_proxy_patch__ = True
    sdk_ws.websockets.connect = connect_no_proxy


def _extract_wecom_sender_id(body: dict) -> str:
    sender = body.get("from")
    if isinstance(sender, dict):
        sender_id = sender.get("user_id") or sender.get("userid")
        if sender_id:
            return str(sender_id).strip()
    return str(body.get("from_userid") or body.get("userid") or "").strip()


def _extract_wecom_chat_type(body: dict) -> str:
    return str(body.get("chattype") or body.get("chat_type") or "single").strip().lower()


def _extract_wecom_chat_id(body: dict) -> str:
    return str(body.get("chatid") or body.get("chat_id") or "").strip()


def _build_wecom_conv_id(sender_id: str, chat_id: str, chat_type: str) -> str:
    normalized_type = (chat_type or "single").strip().lower()
    if normalized_type in {"group", "groupchat", "group_chat"} and chat_id:
        return f"wecom_group_{chat_id}"
    return f"wecom_p2p_{sender_id}"


class WeComStreamManager:
    """Manages WeCom AI Bot WebSocket clients for all agents and accounts."""

    def __init__(self):
        # Use (agent_id, account_id) tuple as key for multi-account support
        self._clients: Dict[Tuple[uuid.UUID, str], object] = {}
        self._tasks: Dict[Tuple[uuid.UUID, str], asyncio.Task] = {}
        self._connected: Dict[uuid.UUID, bool] = {}

    async def start_client(
        self,
        agent_id: uuid.UUID,
        account_id: str,
        bot_id: str,
        bot_secret: str,
        stop_existing: bool = True,
    ):
        """Start a WeCom AI Bot WebSocket client for a specific agent and account."""
        if not bot_id or not bot_secret:
            logger.warning(f"[WeCom Stream] Missing bot_id or bot_secret for {agent_id}, account {account_id}, skipping")
            return

        logger.info(f"[WeCom Stream] Starting client for agent {agent_id}, account {account_id} (BotID: {bot_id[:12]}...)")

        # Stop existing client if any
        if stop_existing:
            await self.stop_client(agent_id, account_id)

        self._connected[agent_id] = False
        task = asyncio.create_task(
            self._run_client(agent_id, account_id, bot_id, bot_secret),
            name=f"wecom-stream-{str(agent_id)[:8]}-{account_id}",
        )
        self._tasks[(agent_id, account_id)] = task

    async def _run_client(
        self,
        agent_id: uuid.UUID,
        account_id: str,
        bot_id: str,
        bot_secret: str,
    ):
        """Run the WeCom WebSocket client (async, runs in the main event loop)."""
        client_key = (agent_id, account_id)
        
        try:
            from wecom_aibot_sdk import WSClient, generate_req_id
        except ImportError:
            self._connected[agent_id] = False
            logger.warning(
                "[WeCom Stream] wecom-aibot-sdk-python not installed. "
                "Install with: pip install wecom-aibot-sdk-python"
            )
            return

        try:
            _disable_wecom_sdk_proxy()
            client = WSClient({
                "bot_id": bot_id,
                "secret": bot_secret,
                "max_reconnect_attempts": -1,  # infinite reconnect
                "heartbeat_interval": 30000,   # 30s heartbeat
            })
            self._clients[client_key] = client

            # ── Message handler: text ──
            async def on_text(frame):
                try:
                    body = frame.body or {}
                    text_obj = body.get("text", {})
                    user_text = text_obj.get("content", "").strip()
                    if not user_text:
                        return

                    sender_id = _extract_wecom_sender_id(body)
                    if not sender_id:
                        logger.warning(
                            f"[WeCom Stream] Missing sender id in text payload for agent {agent_id}: "
                            f"body_keys={list(body.keys())}"
                        )
                        stream_id = generate_req_id("stream")
                        await client.reply_stream(
                            frame,
                            stream_id,
                            "Unable to identify the sender for this WeCom message.",
                            finish=True,
                        )
                        return

                    chat_type = _extract_wecom_chat_type(body)
                    chat_id = _extract_wecom_chat_id(body)
                    is_group_msg = chat_type in {"group", "groupchat", "group_chat"} and bool(chat_id)

                    # Debug: log full body to understand the data structure
                    logger.info(
                        f"[WeCom Stream] Text from {sender_id}, "
                        f"is_group={is_group_msg}, chat_id={chat_id or 'N/A'}, "
                        f"account={account_id}, body_keys={list(body.keys())}: {user_text[:80]}"
                    )

                    # Process message and get reply
                    reply_text = await _process_wecom_stream_message(
                        agent_id=agent_id,
                        account_id=account_id,
                        sender_id=sender_id,
                        user_text=user_text,
                        chat_id=chat_id,
                        chat_type=chat_type,
                    )

                    # Reply via streaming
                    stream_id = generate_req_id("stream")
                    await client.reply_stream(frame, stream_id, reply_text, finish=True)
                    logger.info(f"[WeCom Stream] Replied to {sender_id}: {reply_text[:80]}")

                except Exception as e:
                    logger.error(f"[WeCom Stream] Error handling text message: {e}")
                    import traceback
                    traceback.print_exc()
                    try:
                        stream_id = generate_req_id("stream")
                        await client.reply_stream(
                            frame, stream_id,
                            f"Processing error: {str(e)[:100]}",
                            finish=True,
                        )
                    except Exception:
                        pass

            # ── Message handler: image ──
            async def on_image(frame):
                try:
                    body = frame.body or {}
                    sender_id = _extract_wecom_sender_id(body)
                    logger.info(f"[WeCom Stream] Image message from {sender_id} (not yet handled)")
                    stream_id = generate_req_id("stream")
                    await client.reply_stream(
                        frame, stream_id,
                        "Received your image. Image processing is not yet supported.",
                        finish=True,
                    )
                except Exception as e:
                    logger.error(f"[WeCom Stream] Error handling image: {e}")

            # ── Message handler: file ──
            async def on_file(frame):
                try:
                    body = frame.body or {}
                    sender_id = _extract_wecom_sender_id(body)
                    logger.info(f"[WeCom Stream] File message from {sender_id} (not yet handled)")
                    stream_id = generate_req_id("stream")
                    await client.reply_stream(
                        frame, stream_id,
                        "Received your file. File processing is not yet supported.",
                        finish=True,
                    )
                except Exception as e:
                    logger.error(f"[WeCom Stream] Error handling file: {e}")

            # ── Enter chat event: send welcome ──
            async def on_enter_chat(frame):
                try:
                    # Look up agent's welcome message
                    from app.models.agent import Agent as AgentModel
                    async with async_session() as db:
                        r = await db.execute(select(AgentModel).where(AgentModel.id == agent_id))
                        agent = r.scalar_one_or_none()
                        welcome = (agent.welcome_message if agent else None) or "Hello! How can I help you?"
                    await client.reply_welcome(frame, {
                        "msgtype": "text",
                        "text": {"content": welcome},
                    })
                    logger.info(f"[WeCom Stream] Sent welcome message for agent {agent_id}")
                except Exception as e:
                    logger.error(f"[WeCom Stream] Error sending welcome: {e}")

            # Register event handlers
            client.on("message.text", on_text)
            client.on("message.image", on_image)
            client.on("message.file", on_file)
            client.on("event.enter_chat", on_enter_chat)

            # Connect and run (with retry on failure)
            retry_delay = 5  # Start with 5 seconds
            max_retry_delay = 120  # Cap at 2 minutes
            while True:
                try:
                    logger.info(f"[WeCom Stream] Connecting for agent {agent_id}...")
                    await client.connect_async()
                    self._connected[agent_id] = True

                    # Keep alive
                    retry_delay = 5  # Reset on successful connect
                    while client.is_connected:
                        await asyncio.sleep(1)

                    self._connected[agent_id] = False
                    logger.info(f"[WeCom Stream] Client disconnected for agent {agent_id}, reconnecting in {retry_delay}s...")
                except asyncio.CancelledError:
                    raise  # Propagate cancellation
                except Exception as e:
                    self._connected[agent_id] = False
                    logger.error(f"[WeCom Stream] Connection error for {agent_id}: {e}, retrying in {retry_delay}s...")

                await asyncio.sleep(retry_delay)
                retry_delay = min(retry_delay * 2, max_retry_delay)

        except asyncio.CancelledError:
            self._connected[agent_id] = False
            logger.info(f"[WeCom Stream] Client task cancelled for agent {agent_id}, account {account_id}")
            if client_key in self._clients:
                try:
                    await self._clients[client_key].disconnect()
                except Exception:
                    pass
        except Exception as e:
            logger.error(f"[WeCom Stream] Fatal client error for {agent_id}, account {account_id}: {e}")
            import traceback
            traceback.print_exc()
        finally:
            self._connected.pop(agent_id, None)
            self._clients.pop(client_key, None)
            self._tasks.pop(client_key, None)

    async def stop_client(self, agent_id: uuid.UUID, account_id: str = "default"):
        """Stop a running WebSocket client for an agent and account."""
        key = (agent_id, account_id)
        task = self._tasks.pop(key, None)
        if task and not task.done():
            task.cancel()
            logger.info(f"[WeCom Stream] Stopped client for agent {agent_id}, account {account_id}")
        client = self._clients.pop(key, None)
        if client:
            try:
                await client.disconnect()
            except Exception:
                pass
        self._connected.pop(agent_id, None)

    async def start_all(self):
        """Start WebSocket clients for all configured WeCom accounts with bot credentials."""
        logger.info("[WeCom Stream] Initializing all active WeCom AI Bot channels...")
        async with async_session() as db:
            result = await db.execute(
                select(ChannelConfig).where(
                    ChannelConfig.is_configured,
                    ChannelConfig.channel_type == "wecom",
                )
            )
            configs = result.scalars().all()

            # Extract all account configs before session closes to avoid lazy loading issues
            account_configs = []
            for config in configs:
                extra = config.extra_config or {}
                accounts = extra.get("accounts", {})

                # New multi-account format with nested bot/agent structure
                if accounts:
                    for account_id, account_config in accounts.items():
                        # Check if bot_websocket mode is enabled
                        if account_config.get("bot_websocket_enabled", False):
                            bot_config = account_config.get("bot", {})
                            bot_id = bot_config.get("id", "").strip()
                            bot_secret = bot_config.get("secret", "").strip()
                            if bot_id and bot_secret:
                                account_configs.append({
                                    "agent_id": config.agent_id,
                                    "account_id": account_id,
                                    "bot_id": bot_id,
                                    "bot_secret": bot_secret,
                                })
                else:
                    # Legacy single-account format - migrate on the fly
                    connection_mode = extra.get("connection_mode", "websocket")
                    if connection_mode == "websocket":
                        bot_id = extra.get("bot_id", "").strip()
                        bot_secret = extra.get("bot_secret", "").strip()
                        if bot_id and bot_secret:
                            account_configs.append({
                                "agent_id": config.agent_id,
                                "account_id": "default",
                                "bot_id": bot_id,
                                "bot_secret": bot_secret,
                            })

        started = 0
        for cfg in account_configs:
            await self.start_client(
                cfg["agent_id"],
                cfg["account_id"],
                cfg["bot_id"],
                cfg["bot_secret"],
                stop_existing=False,
            )
            started += 1

        logger.info(f"[WeCom Stream] Started {started} WeCom AI Bot client(s)")

    def status(self) -> dict:
        """Return status of all active WebSocket clients."""
        return {
            f"{agent_id}:{account_id}": not self._tasks[(agent_id, account_id)].done()
            for (agent_id, account_id) in self._tasks.keys()
        }


# ── Message processing helper ──

async def _process_wecom_stream_message(
    agent_id: uuid.UUID,
    sender_id: str,
    user_text: str,
    chat_id: str = "",
    is_group: bool = False,
    account_id: str = "default",
) -> str:
    """Process a WeCom message through the LLM pipeline and return the reply text."""
    from datetime import datetime, timezone
    from sqlalchemy import select as _select
    from app.database import async_session
    from app.models.agent import Agent as AgentModel
    from app.models.audit import ChatMessage
    from app.services.channel_session import find_or_create_channel_session
    from app.services.channel_user_service import channel_user_service
    from app.api.feishu import _call_agent_llm

    async with async_session() as db:
        # Load agent
        agent_r = await db.execute(_select(AgentModel).where(AgentModel.id == agent_id))
        agent_obj = agent_r.scalar_one_or_none()
        if not agent_obj:
            logger.warning(f"[WeCom Stream] Agent {agent_id} not found")
            return "Agent not found"
        from app.models.agent import DEFAULT_CONTEXT_WINDOW_SIZE
        ctx_size = agent_obj.context_window_size or DEFAULT_CONTEXT_WINDOW_SIZE

        # Conversation ID: differentiate single chat vs group chat, include account_id
        # Group detection is based on chatid presence, not chattype (SDK bug)
        account_suffix = f"_{account_id}" if account_id != "default" else ""
        if is_group and chat_id:
            conv_id = f"wecom_group_{chat_id}{account_suffix}"
        else:
            conv_id = f"wecom_p2p_{sender_id}{account_suffix}"

        # Resolve or create platform user via unified channel user service.
        # This correctly handles the User/Identity model relationship
        # (email/username/password_hash are AssociationProxy fields — cannot be
        # set directly in UserModel constructor).
        platform_user = await channel_user_service.resolve_channel_user(
            db=db,
            agent=agent_obj,
            channel_type="wecom",
            external_user_id=sender_id,
            extra_info={"display_name": f"WeCom {sender_id[:8]}"},
        )
        platform_user_id = platform_user.id

        # Find or create session
        _is_group = normalized_chat_type in {"group", "groupchat", "group_chat"} and bool(chat_id)
        sess = await find_or_create_channel_session(
            db=db,
            agent_id=agent_id,
            user_id=agent_obj.creator_id if _is_group else platform_user_id,
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
        logger.info(f"[WeCom Stream] LLM reply: {reply_text[:100]}")

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

    return reply_text


wecom_stream_manager = WeComStreamManager()
