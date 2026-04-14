"""DingTalk Stream Connection Manager.

Manages WebSocket-based Stream connections for DingTalk bots, similar to feishu_ws.py.
Uses the dingtalk-stream SDK to receive bot messages via persistent connections.
"""

import asyncio
import threading
import uuid
from typing import Dict

from loguru import logger
from sqlalchemy import select

from app.database import async_session
from app.models.channel_config import ChannelConfig


class DingTalkStreamManager:
    """Manages DingTalk Stream clients for all agents."""

    def __init__(self):
        self._threads: Dict[uuid.UUID, threading.Thread] = {}
        self._stop_events: Dict[uuid.UUID, threading.Event] = {}
        self._main_loop: asyncio.AbstractEventLoop | None = None

    async def start_client(
        self,
        agent_id: uuid.UUID,
        app_key: str,
        app_secret: str,
        stop_existing: bool = True,
    ):
        """Start a DingTalk Stream client for a specific agent."""
        if not app_key or not app_secret:
            logger.warning(f"[DingTalk Stream] Missing credentials for {agent_id}, skipping")
            return

        logger.info(f"[DingTalk Stream] Starting client for agent {agent_id} (AppKey: {app_key[:8]}...)")

        # Capture the main event loop so threads can dispatch coroutines back
        if self._main_loop is None:
            self._main_loop = asyncio.get_running_loop()

        # Stop existing client if any
        if stop_existing:
            await self.stop_client(agent_id)

        stop_event = threading.Event()
        self._stop_events[agent_id] = stop_event

        # Run Stream client in a separate thread (SDK uses its own event loop)
        thread = threading.Thread(
            target=self._run_client_thread,
            args=(agent_id, app_key, app_secret, stop_event),
            name=f"dingtalk-stream-{str(agent_id)[:8]}",
            daemon=True,
        )
        self._threads[agent_id] = thread
        thread.start()
        logger.info(f"[DingTalk Stream] Client thread started for agent {agent_id}")

    def _run_client_thread(
        self,
        agent_id: uuid.UUID,
        app_key: str,
        app_secret: str,
        stop_event: threading.Event,
    ):
        """Run the DingTalk Stream client in a blocking thread."""
        try:
            import dingtalk_stream

            # Reference to manager's main loop for async dispatch
            main_loop = self._main_loop

            class ClawithChatbotHandler(dingtalk_stream.ChatbotHandler):
                """Custom handler that dispatches messages to the Clawith LLM pipeline."""

                async def process(self, callback: dingtalk_stream.CallbackMessage):
                    """Handle incoming bot message from DingTalk Stream.

                    NOTE: The SDK invokes this method in the thread's own asyncio loop,
                    so we must dispatch to the main FastAPI loop for DB + LLM work.
                    """
                    try:
                        # Parse the raw data into a ChatbotMessage via class method
                        incoming = dingtalk_stream.ChatbotMessage.from_dict(callback.data)

                        # Extract text content
                        text_list = incoming.get_text_list()
                        user_text = " ".join(text_list).strip() if text_list else ""

                        if not user_text:
                            return dingtalk_stream.AckMessage.STATUS_OK, "empty message"

                        sender_staff_id = incoming.sender_staff_id or incoming.sender_id or ""
                        conversation_id = incoming.conversation_id or ""
                        conversation_type = incoming.conversation_type or "1"
                        session_webhook = incoming.session_webhook or ""

                        logger.info(
                            f"[DingTalk Stream] Message from [{incoming.sender_nick}]{sender_staff_id}: {user_text[:80]}"
                        )

                        # Dispatch to the main FastAPI event loop for DB + LLM processing
                        from app.api.dingtalk import process_dingtalk_message

                        if main_loop and main_loop.is_running():
                            future = asyncio.run_coroutine_threadsafe(
                                process_dingtalk_message(
                                    agent_id=agent_id,
                                    sender_staff_id=sender_staff_id,
                                    user_text=user_text,
                                    conversation_id=conversation_id,
                                    conversation_type=conversation_type,
                                    session_webhook=session_webhook,
                                ),
                                main_loop,
                            )
                            # Wait for result (with timeout)
                            try:
                                future.result(timeout=120)
                            except Exception as e:
                                logger.error(f"[DingTalk Stream] LLM processing error: {e}")
                                import traceback
                                traceback.print_exc()
                        else:
                            logger.warning("[DingTalk Stream] Main loop not available for dispatch")

                        return dingtalk_stream.AckMessage.STATUS_OK, "ok"
                    except Exception as e:
                        logger.error(f"[DingTalk Stream] Error in message handler: {e}")
                        import traceback
                        traceback.print_exc()
                        return dingtalk_stream.AckMessage.STATUS_SYSTEM_EXCEPTION, str(e)

            credential = dingtalk_stream.Credential(client_id=app_key, client_secret=app_secret)
            client = dingtalk_stream.DingTalkStreamClient(credential=credential)
            client.register_callback_handler(
                dingtalk_stream.chatbot.ChatbotMessage.TOPIC,
                ClawithChatbotHandler(),
            )

            logger.info(f"[DingTalk Stream] Connecting for agent {agent_id}...")
            # start_forever() blocks until disconnected
            client.start_forever()

        except ImportError:
            logger.warning(
                "[DingTalk Stream] dingtalk-stream package not installed. "
                "Install with: pip install dingtalk-stream"
            )
        except Exception as e:
            logger.error(f"[DingTalk Stream] Client error for {agent_id}: {e}")
            import traceback
            traceback.print_exc()
        finally:
            self._threads.pop(agent_id, None)
            self._stop_events.pop(agent_id, None)
            logger.info(f"[DingTalk Stream] Client stopped for agent {agent_id}")

    async def stop_client(self, agent_id: uuid.UUID):
        """Stop a running Stream client for an agent."""
        stop_event = self._stop_events.pop(agent_id, None)
        if stop_event:
            stop_event.set()
        thread = self._threads.pop(agent_id, None)
        if thread and thread.is_alive():
            logger.info(f"[DingTalk Stream] Stopping client for agent {agent_id}")

    async def start_all(self):
        """Start Stream clients for all configured DingTalk agents."""
        logger.info("[DingTalk Stream] Initializing all active DingTalk channels...")
        async with async_session() as db:
            result = await db.execute(
                select(ChannelConfig).where(
                    ChannelConfig.is_configured == True,
                    ChannelConfig.channel_type == "dingtalk",
                )
            )
            configs = result.scalars().all()

        logger.info(f"[DingTalk Stream] Found {len(configs)} configured DingTalk channel(s)")

        for config in configs:
            if config.app_id and config.app_secret:
                await self.start_client(
                    config.agent_id, config.app_id, config.app_secret,
                    stop_existing=False,
                )
            else:
                logger.warning(
                    f"[DingTalk Stream] Skipping agent {config.agent_id}: missing credentials"
                )

    def status(self) -> dict:
        """Return status of all active Stream clients."""
        return {
            str(aid): self._threads[aid].is_alive()
            for aid in self._threads
        }


dingtalk_stream_manager = DingTalkStreamManager()
