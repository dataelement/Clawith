"""WeCom (企业微信) AI Bot WebSocket Long Connection Manager.

Uses the wecom-aibot-sdk-python SDK for WebSocket-based message reception.
No callback URL or domain verification needed.
"""

import asyncio
import uuid
from typing import Dict

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
    """Manages WeCom AI Bot WebSocket clients for all agents."""

    def __init__(self):
        self._clients: Dict[uuid.UUID, object] = {}
        self._tasks: Dict[uuid.UUID, asyncio.Task] = {}

    async def start_client(
        self,
        agent_id: uuid.UUID,
        bot_id: str,
        bot_secret: str,
        stop_existing: bool = True,
    ):
        """Start a WeCom AI Bot WebSocket client for a specific agent."""
        if not bot_id or not bot_secret:
            logger.warning(f"[WeCom Stream] Missing bot_id or bot_secret for {agent_id}, skipping")
            return

        logger.info(f"[WeCom Stream] Starting client for agent {agent_id} (BotID: {bot_id[:12]}...)")

        # Stop existing client if any
        if stop_existing:
            await self.stop_client(agent_id)

        task = asyncio.create_task(
            self._run_client(agent_id, bot_id, bot_secret),
            name=f"wecom-stream-{str(agent_id)[:8]}",
        )
        self._tasks[agent_id] = task

    async def _run_client(
        self,
        agent_id: uuid.UUID,
        bot_id: str,
        bot_secret: str,
    ):
        """Run the WeCom WebSocket client (async, runs in the main event loop)."""
        try:
            from wecom_aibot_sdk import WSClient, generate_req_id
        except ImportError:
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
            self._clients[agent_id] = client

            # ── Message handler: text ──
            async def on_text(frame):
                try:
                    body = frame.body or {}
                    text_obj = body.get("text", {})
                    user_text = text_obj.get("content", "").strip()
                    if not user_text:
                        return

                    sender_id = _extract_wecom_sender_id(body)
                    chat_id = _extract_wecom_chat_id(body)
                    chat_type = _extract_wecom_chat_type(body)
                    msg_id = str(body.get("msgid", "")).strip()

                    if not sender_id:
                        logger.error(
                            "[WeCom Stream] Missing sender_id, skip message",
                            extra={
                                "msgid": msg_id,
                                "chat_type": chat_type,
                                "chat_id": chat_id,
                                "body_keys": sorted(body.keys()),
                            },
                        )
                        return

                    logger.info(
                        f"[WeCom Stream] Text from {sender_id} ({chat_type}, chat_id={chat_id or '-'})"
                    )

                    # Process message and get reply
                    reply_text = await _process_wecom_stream_message(
                        agent_id=agent_id,
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

            # Connect and run
            logger.info(f"[WeCom Stream] Connecting for agent {agent_id}...")
            await client.connect_async()

            # Keep alive
            while client.is_connected:
                await asyncio.sleep(1)

            logger.info(f"[WeCom Stream] Client disconnected for agent {agent_id}")

        except asyncio.CancelledError:
            logger.info(f"[WeCom Stream] Client task cancelled for agent {agent_id}")
            if agent_id in self._clients:
                try:
                    await self._clients[agent_id].disconnect()
                except Exception:
                    pass
        except Exception as e:
            logger.error(f"[WeCom Stream] Client error for {agent_id}: {e}")
            import traceback
            traceback.print_exc()
        finally:
            self._clients.pop(agent_id, None)
            self._tasks.pop(agent_id, None)

    async def stop_client(self, agent_id: uuid.UUID):
        """Stop a running WebSocket client for an agent."""
        task = self._tasks.pop(agent_id, None)
        if task and not task.done():
            task.cancel()
            logger.info(f"[WeCom Stream] Stopped client for agent {agent_id}")
        client = self._clients.pop(agent_id, None)
        if client:
            try:
                await client.disconnect()
            except Exception:
                pass

    async def start_all(self):
        """Start WebSocket clients for all configured WeCom agents with bot credentials."""
        logger.info("[WeCom Stream] Initializing all active WeCom AI Bot channels...")
        async with async_session() as db:
            result = await db.execute(
                select(ChannelConfig).where(
                    ChannelConfig.is_configured,
                    ChannelConfig.channel_type == "wecom",
                )
            )
            configs = result.scalars().all()

        started = 0
        for config in configs:
            extra = config.extra_config or {}
            bot_id = extra.get("bot_id", "")
            bot_secret = extra.get("bot_secret", "")
            if bot_id and bot_secret:
                await self.start_client(
                    config.agent_id, bot_id, bot_secret,
                    stop_existing=False,
                )
                started += 1

        logger.info(f"[WeCom Stream] Started {started} WeCom AI Bot client(s)")

    def status(self) -> dict:
        """Return status of all active WebSocket clients."""
        return {
            str(aid): not self._tasks[aid].done()
            for aid in self._tasks
        }


# ── Message processing helper ──

async def _process_wecom_stream_message(
    agent_id: uuid.UUID,
    sender_id: str,
    user_text: str,
    chat_id: str = "",
    chat_type: str = "single",
) -> str:
    """Process a WeCom message through the LLM pipeline and return the reply text."""
    from datetime import datetime, timezone
    from sqlalchemy import select as _select
    from app.database import async_session
    from app.models.agent import Agent as AgentModel
    from app.models.audit import ChatMessage
    from app.models.user import User as UserModel
    from app.core.security import hash_password
    from app.services.channel_session import find_or_create_channel_session
    from app.api.feishu import _call_agent_llm
    import uuid as _uuid

    async with async_session() as db:
        # Load agent
        agent_r = await db.execute(_select(AgentModel).where(AgentModel.id == agent_id))
        agent_obj = agent_r.scalar_one_or_none()
        if not agent_obj:
            logger.warning(f"[WeCom Stream] Agent {agent_id} not found")
            return "Agent not found"
        ctx_size = agent_obj.context_window_size or 20

        conv_id = _build_wecom_conv_id(sender_id, chat_id, chat_type)

        # Find or create platform user
        wc_username = f"wecom_{sender_id}"
        u_r = await db.execute(_select(UserModel).where(UserModel.username == wc_username))
        platform_user = u_r.scalar_one_or_none()

        if not platform_user:
            platform_user = UserModel(
                username=wc_username,
                email=f"{wc_username}@wecom.local",
                password_hash=hash_password(_uuid.uuid4().hex),
                display_name=f"WeCom {sender_id[:8]}",
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
            user_id=platform_user_id,
            external_conv_id=conv_id,
            source_channel="wecom",
            first_message_title=user_text,
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
