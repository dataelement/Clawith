"""DingTalk Stream Connection Manager.

Manages WebSocket-based Stream connections for DingTalk bots, similar to feishu_ws.py.
Uses the dingtalk-stream SDK to receive bot messages via persistent connections.
"""

import asyncio
import base64
import json
import threading
import uuid
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import httpx
from loguru import logger
from sqlalchemy import select

from app.config import get_settings
from app.database import async_session
from app.models.channel_config import ChannelConfig
from app.services.dingtalk_token import dingtalk_token_manager


# ─── DingTalk Media Helpers ─────────────────────────────


async def _get_media_download_url(
    access_token: str, download_code: str, robot_code: str
) -> Optional[str]:
    """Get media file download URL from DingTalk API."""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                "https://api.dingtalk.com/v1.0/robot/messageFiles/download",
                headers={"x-acs-dingtalk-access-token": access_token},
                json={"downloadCode": download_code, "robotCode": robot_code},
            )
            data = resp.json()
            url = data.get("downloadUrl")
            if url:
                return url
            logger.error(f"[DingTalk] Failed to get download URL: {data}")
            return None
    except Exception as e:
        logger.error(f"[DingTalk] Error getting download URL: {e}")
        return None


async def _download_file(url: str) -> Optional[bytes]:
    """Download a file from a URL and return its bytes."""
    try:
        async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            return resp.content
    except Exception as e:
        logger.error(f"[DingTalk] Error downloading file: {e}")
        return None


async def download_dingtalk_media(
    app_key: str, app_secret: str, download_code: str
) -> Optional[bytes]:
    """Download a media file from DingTalk using downloadCode.

    Steps: get access_token -> get download URL -> download file bytes.
    """
    access_token = await dingtalk_token_manager.get_token(app_key, app_secret)
    if not access_token:
        return None

    download_url = await _get_media_download_url(access_token, download_code, app_key)
    if not download_url:
        return None

    return await _download_file(download_url)


def _resolve_upload_dir(agent_id: uuid.UUID) -> Path:
    """Get the uploads directory for an agent, creating it if needed."""
    settings = get_settings()
    upload_dir = Path(settings.AGENT_DATA_DIR) / str(agent_id) / "workspace" / "uploads"
    upload_dir.mkdir(parents=True, exist_ok=True)
    return upload_dir


async def _process_media_message(
    msg_data: dict,
    app_key: str,
    app_secret: str,
    agent_id: uuid.UUID,
) -> Tuple[str, Optional[List[str]], Optional[List[str]]]:
    """Process a DingTalk message and extract text + media info.

    Returns:
        (user_text, image_base64_list, saved_file_paths)
        - user_text: text content for the LLM (may include markers)
        - image_base64_list: list of base64-encoded image data URIs, or None
        - saved_file_paths: list of saved file paths, or None
    """
    msgtype = msg_data.get("msgtype", "text")
    logger.info(f"[DingTalk] Processing message type: {msgtype}")

    image_base64_list: List[str] = []
    saved_file_paths: List[str] = []

    if msgtype == "text":
        text_content = msg_data.get("text", {}).get("content", "").strip()
        return text_content, None, None

    elif msgtype == "picture":
        download_code = msg_data.get("content", {}).get("downloadCode", "")
        if not download_code:
            download_code = msg_data.get("downloadCode", "")
        if not download_code:
            logger.warning("[DingTalk] Picture message without downloadCode")
            return "[User sent an image, but it could not be downloaded]", None, None

        file_bytes = await download_dingtalk_media(app_key, app_secret, download_code)
        if not file_bytes:
            return "[User sent an image, but download failed]", None, None

        upload_dir = _resolve_upload_dir(agent_id)
        filename = f"dingtalk_img_{uuid.uuid4().hex[:8]}.jpg"
        save_path = upload_dir / filename
        save_path.write_bytes(file_bytes)
        logger.info(f"[DingTalk] Saved image to {save_path} ({len(file_bytes)} bytes)")

        b64_data = base64.b64encode(file_bytes).decode("ascii")
        image_marker = f"[image_data:data:image/jpeg;base64,{b64_data}]"
        return (
            f"[User sent an image]\n{image_marker}",
            [f"data:image/jpeg;base64,{b64_data}"],
            [str(save_path)],
        )

    elif msgtype == "richText":
        rich_text = msg_data.get("content", {}).get("richText", [])
        text_parts: List[str] = []

        for section in rich_text:
            for item in section if isinstance(section, list) else [section]:
                if "text" in item:
                    text_parts.append(item["text"])
                elif "downloadCode" in item:
                    file_bytes = await download_dingtalk_media(
                        app_key, app_secret, item["downloadCode"]
                    )
                    if file_bytes:
                        upload_dir = _resolve_upload_dir(agent_id)
                        filename = f"dingtalk_richimg_{uuid.uuid4().hex[:8]}.jpg"
                        save_path = upload_dir / filename
                        save_path.write_bytes(file_bytes)
                        logger.info(f"[DingTalk] Saved rich text image to {save_path}")

                        b64_data = base64.b64encode(file_bytes).decode("ascii")
                        image_marker = f"[image_data:data:image/jpeg;base64,{b64_data}]"
                        text_parts.append(image_marker)
                        image_base64_list.append(f"data:image/jpeg;base64,{b64_data}")
                        saved_file_paths.append(str(save_path))

        combined_text = "\n".join(text_parts).strip()
        if not combined_text:
            combined_text = "[User sent a rich text message]"

        return (
            combined_text,
            image_base64_list if image_base64_list else None,
            saved_file_paths if saved_file_paths else None,
        )

    elif msgtype == "audio":
        content = msg_data.get("content", {})
        recognition = content.get("recognition", "")
        if recognition:
            logger.info(f"[DingTalk] Audio with recognition: {recognition[:80]}")
            return f"[Voice message] {recognition}", None, None

        download_code = content.get("downloadCode", "")
        if download_code:
            file_bytes = await download_dingtalk_media(app_key, app_secret, download_code)
            if file_bytes:
                upload_dir = _resolve_upload_dir(agent_id)
                duration = content.get("duration", "unknown")
                filename = f"dingtalk_audio_{uuid.uuid4().hex[:8]}.amr"
                save_path = upload_dir / filename
                save_path.write_bytes(file_bytes)
                logger.info(f"[DingTalk] Saved audio to {save_path} ({len(file_bytes)} bytes)")
                return (
                    f"[User sent a voice message, duration {duration}ms, saved to {filename}]",
                    None,
                    [str(save_path)],
                )
        return "[User sent a voice message, but it could not be processed]", None, None

    elif msgtype == "video":
        content = msg_data.get("content", {})
        download_code = content.get("downloadCode", "")
        if download_code:
            file_bytes = await download_dingtalk_media(app_key, app_secret, download_code)
            if file_bytes:
                upload_dir = _resolve_upload_dir(agent_id)
                duration = content.get("duration", "unknown")
                filename = f"dingtalk_video_{uuid.uuid4().hex[:8]}.mp4"
                save_path = upload_dir / filename
                save_path.write_bytes(file_bytes)
                logger.info(f"[DingTalk] Saved video to {save_path} ({len(file_bytes)} bytes)")
                return (
                    f"[User sent a video, duration {duration}ms, saved to {filename}]",
                    None,
                    [str(save_path)],
                )
        return "[User sent a video, but it could not be downloaded]", None, None

    elif msgtype == "file":
        content = msg_data.get("content", {})
        download_code = content.get("downloadCode", "")
        original_filename = content.get("fileName", "unknown_file")
        if download_code:
            file_bytes = await download_dingtalk_media(app_key, app_secret, download_code)
            if file_bytes:
                upload_dir = _resolve_upload_dir(agent_id)
                safe_name = f"dingtalk_{uuid.uuid4().hex[:8]}_{original_filename}"
                save_path = upload_dir / safe_name
                save_path.write_bytes(file_bytes)
                logger.info(
                    f"[DingTalk] Saved file '{original_filename}' to {save_path} "
                    f"({len(file_bytes)} bytes)"
                )
                return (
                    f"[file:{original_filename}]",
                    None,
                    [str(save_path)],
                )
        return f"[User sent file {original_filename}, but it could not be downloaded]", None, None

    else:
        logger.warning(f"[DingTalk] Unsupported message type: {msgtype}")
        return f"[User sent a {msgtype} message, which is not yet supported]", None, None


# ─── DingTalk Media Upload & Send ───────────────────────

async def _upload_dingtalk_media(
    app_key: str,
    app_secret: str,
    file_path: str,
    media_type: str = "file",
) -> Optional[str]:
    """Upload a media file to DingTalk and return the mediaId.

    Args:
        app_key: DingTalk app key (robotCode).
        app_secret: DingTalk app secret.
        file_path: Local file path to upload.
        media_type: One of 'image', 'voice', 'video', 'file'.

    Returns:
        mediaId string on success, None on failure.
    """
    access_token = await dingtalk_token_manager.get_token(app_key, app_secret)
    if not access_token:
        return None

    file_p = Path(file_path)
    if not file_p.exists():
        logger.error(f"[DingTalk] Upload failed: file not found: {file_path}")
        return None

    try:
        file_bytes = file_p.read_bytes()
        async with httpx.AsyncClient(timeout=60) as client:
            # Use the legacy oapi endpoint which is more reliable and widely supported.
            upload_url = (
                f"https://oapi.dingtalk.com/media/upload"
                f"?access_token={access_token}&type={media_type}"
            )
            resp = await client.post(
                upload_url,
                files={"media": (file_p.name, file_bytes)},
            )
            data = resp.json()
            # Legacy API returns media_id (snake_case), new API returns mediaId
            media_id = data.get("media_id") or data.get("mediaId")
            if media_id and data.get("errcode", 0) == 0:
                logger.info(
                    f"[DingTalk] Uploaded {media_type} '{file_p.name}' -> mediaId={media_id[:20]}..."
                )
                return media_id
            logger.error(f"[DingTalk] Upload failed: {data}")
            return None
    except Exception as e:
        logger.error(f"[DingTalk] Upload error: {e}")
        return None


async def _send_dingtalk_media_message(
    app_key: str,
    app_secret: str,
    target_id: str,
    media_id: str,
    media_type: str,
    conversation_type: str,
    filename: Optional[str] = None,
) -> bool:
    """Send a media message via DingTalk proactive message API.

    Args:
        app_key: DingTalk app key (robotCode).
        app_secret: DingTalk app secret.
        target_id: For P2P: sender_staff_id; For group: openConversationId.
        media_id: The mediaId from upload.
        media_type: One of 'image', 'voice', 'video', 'file'.
        conversation_type: '1' for P2P, '2' for group.
        filename: Original filename (used for file/video types).

    Returns:
        True on success, False on failure.
    """
    access_token = await dingtalk_token_manager.get_token(app_key, app_secret)
    if not access_token:
        return False

    headers = {"x-acs-dingtalk-access-token": access_token}

    # Build msgKey and msgParam based on media_type
    if media_type == "image":
        msg_key = "sampleImageMsg"
        msg_param = json.dumps({"photoURL": media_id})
    elif media_type == "voice":
        msg_key = "sampleAudio"
        msg_param = json.dumps({"mediaId": media_id, "duration": "3000"})
    elif media_type == "video":
        safe_name = filename or "video.mp4"
        ext = Path(safe_name).suffix.lstrip(".") or "mp4"
        msg_key = "sampleFile"
        msg_param = json.dumps({
            "mediaId": media_id,
            "fileName": safe_name,
            "fileType": ext,
        })
    else:
        # file
        safe_name = filename or "file"
        ext = Path(safe_name).suffix.lstrip(".") or "bin"
        msg_key = "sampleFile"
        msg_param = json.dumps({
            "mediaId": media_id,
            "fileName": safe_name,
            "fileType": ext,
        })

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            if conversation_type == "2":
                # Group chat
                resp = await client.post(
                    "https://api.dingtalk.com/v1.0/robot/groupMessages/send",
                    headers=headers,
                    json={
                        "robotCode": app_key,
                        "openConversationId": target_id,
                        "msgKey": msg_key,
                        "msgParam": msg_param,
                    },
                )
            else:
                # P2P chat
                resp = await client.post(
                    "https://api.dingtalk.com/v1.0/robot/oToMessages/batchSend",
                    headers=headers,
                    json={
                        "robotCode": app_key,
                        "userIds": [target_id],
                        "msgKey": msg_key,
                        "msgParam": msg_param,
                    },
                )

            data = resp.json()
            if resp.status_code >= 400 or data.get("errcode"):
                logger.error(f"[DingTalk] Send media failed: {data}")
                return False

            logger.info(
                f"[DingTalk] Sent {media_type} message to {target_id[:16]}... "
                f"(conv_type={conversation_type})"
            )
            return True
    except Exception as e:
        logger.error(f"[DingTalk] Send media error: {e}")
        return False


# ─── Stream Manager ─────────────────────────────────────


def _fire_and_forget(loop, coro):
    """Schedule a coroutine on the main loop and log any unhandled exception."""
    future = asyncio.run_coroutine_threadsafe(coro, loop)
    future.add_done_callback(lambda f: f.exception() if not f.cancelled() else None)


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
        """Run the DingTalk Stream client with auto-reconnect."""
        try:
            import dingtalk_stream
        except ImportError:
            logger.warning(
                "[DingTalk Stream] dingtalk-stream package not installed. "
                "Install with: pip install dingtalk-stream"
            )
            self._threads.pop(agent_id, None)
            self._stop_events.pop(agent_id, None)
            return

        MAX_RETRIES = 5
        RETRY_DELAYS = [2, 5, 15, 30, 60]  # exponential backoff, seconds

        # Reference to manager's main loop for async dispatch
        main_loop = self._main_loop
        retries = 0
        manager_self = self

        class ClawithChatbotHandler(dingtalk_stream.ChatbotHandler):
            """Custom handler that dispatches messages to the Clawith LLM pipeline."""

            async def process(self, callback: dingtalk_stream.CallbackMessage):
                """Handle incoming bot message from DingTalk Stream.

                NOTE: The SDK invokes this method in the thread's own asyncio loop,
                so we must dispatch to the main FastAPI loop for DB + LLM work.
                """
                try:
                    # Parse the raw data
                    incoming = dingtalk_stream.ChatbotMessage.from_dict(callback.data)
                    msg_data = callback.data if isinstance(callback.data, dict) else json.loads(callback.data)

                    msgtype = msg_data.get("msgtype", "text")
                    sender_staff_id = incoming.sender_staff_id or incoming.sender_id or ""
                    sender_nick = incoming.sender_nick or ""
                    message_id = incoming.message_id or ""
                    conversation_id = incoming.conversation_id or ""
                    conversation_type = incoming.conversation_type or "1"
                    session_webhook = incoming.session_webhook or ""

                    logger.info(
                        f"[DingTalk Stream] Received {msgtype} message from {sender_staff_id}"
                    )

                    if msgtype == "text":
                        # Plain text: use existing logic
                        text_list = incoming.get_text_list()
                        user_text = " ".join(text_list).strip() if text_list else ""
                        if not user_text:
                            return dingtalk_stream.AckMessage.STATUS_OK, "empty message"

                        logger.info(
                            f"[DingTalk Stream] Text from {sender_staff_id}: {user_text[:80]}"
                        )

                        from app.api.dingtalk import process_dingtalk_message

                        if main_loop and main_loop.is_running():
                            # Add thinking reaction immediately
                            from app.services.dingtalk_reaction import add_thinking_reaction
                            _fire_and_forget(main_loop,
                                add_thinking_reaction(app_key, app_secret, message_id, conversation_id))

                            _fire_and_forget(main_loop,
                                process_dingtalk_message(
                                    agent_id=agent_id,
                                    sender_staff_id=sender_staff_id,
                                    user_text=user_text,
                                    conversation_id=conversation_id,
                                    conversation_type=conversation_type,
                                    session_webhook=session_webhook,
                                    sender_nick=sender_nick,
                                    message_id=message_id,
                                ))
                        else:
                            logger.warning("[DingTalk Stream] Main loop not available")

                    else:
                        # Non-text message: process media in the main loop
                        if main_loop and main_loop.is_running():
                            # Add thinking reaction immediately
                            from app.services.dingtalk_reaction import add_thinking_reaction
                            _fire_and_forget(main_loop,
                                add_thinking_reaction(app_key, app_secret, message_id, conversation_id))

                            _fire_and_forget(main_loop,
                                manager_self._handle_media_and_dispatch(
                                    msg_data=msg_data,
                                    app_key=app_key,
                                    app_secret=app_secret,
                                    agent_id=agent_id,
                                    sender_staff_id=sender_staff_id,
                                    conversation_id=conversation_id,
                                    conversation_type=conversation_type,
                                    session_webhook=session_webhook,
                                    sender_nick=sender_nick,
                                    message_id=message_id,
                                ))
                        else:
                            logger.warning("[DingTalk Stream] Main loop not available")

                    return dingtalk_stream.AckMessage.STATUS_OK, "ok"
                except Exception as e:
                    logger.error(f"[DingTalk Stream] Error in message handler: {e}")
                    import traceback
                    traceback.print_exc()
                    return dingtalk_stream.AckMessage.STATUS_SYSTEM_EXCEPTION, str(e)

        while not stop_event.is_set() and retries <= MAX_RETRIES:
            try:
                credential = dingtalk_stream.Credential(client_id=app_key, client_secret=app_secret)
                client = dingtalk_stream.DingTalkStreamClient(credential=credential)
                client.register_callback_handler(
                    dingtalk_stream.chatbot.ChatbotMessage.TOPIC,
                    ClawithChatbotHandler(),
                )

                logger.info(
                    f"[DingTalk Stream] Connecting for agent {agent_id}... "
                    f"(attempt {retries + 1}/{MAX_RETRIES + 1})"
                )
                # start_forever() blocks until disconnected
                client.start_forever()

                # start_forever returned: connection dropped
                if stop_event.is_set():
                    break  # intentional stop, no retry

                # Reset retries on successful connection (ran for a while then disconnected)
                retries = 0
                retries += 1
                logger.warning(
                    f"[DingTalk Stream] Connection lost for agent {agent_id}, will retry..."
                )

            except Exception as e:
                retries += 1
                logger.error(
                    f"[DingTalk Stream] Connection error for {agent_id} "
                    f"(attempt {retries}/{MAX_RETRIES + 1}): {e}"
                )

            if retries > MAX_RETRIES:
                logger.error(
                    f"[DingTalk Stream] Agent {agent_id} exhausted all {MAX_RETRIES} retries, giving up"
                )
                break

            delay = RETRY_DELAYS[min(retries - 1, len(RETRY_DELAYS) - 1)]
            logger.info(
                f"[DingTalk Stream] Retrying in {delay}s for agent {agent_id}..."
            )
            # Use stop_event.wait so we exit immediately if stopped
            if stop_event.wait(timeout=delay):
                break  # stop was requested during wait

        self._threads.pop(agent_id, None)
        self._stop_events.pop(agent_id, None)
        logger.info(f"[DingTalk Stream] Client stopped for agent {agent_id}")

    @staticmethod
    async def _handle_media_and_dispatch(
        msg_data: dict,
        app_key: str,
        app_secret: str,
        agent_id: uuid.UUID,
        sender_staff_id: str,
        conversation_id: str,
        conversation_type: str,
        session_webhook: str,
        sender_nick: str = "",
        message_id: str = "",
    ):
        """Download media, then dispatch to process_dingtalk_message."""
        from app.api.dingtalk import process_dingtalk_message

        user_text, image_base64_list, saved_file_paths = await _process_media_message(
            msg_data=msg_data,
            app_key=app_key,
            app_secret=app_secret,
            agent_id=agent_id,
        )

        if not user_text:
            logger.info("[DingTalk Stream] Empty content after media processing, skipping")
            return

        await process_dingtalk_message(
            agent_id=agent_id,
            sender_staff_id=sender_staff_id,
            user_text=user_text,
            conversation_id=conversation_id,
            conversation_type=conversation_type,
            session_webhook=session_webhook,
            image_base64_list=image_base64_list,
            saved_file_paths=saved_file_paths,
            sender_nick=sender_nick,
            message_id=message_id,
        )

    async def stop_client(self, agent_id: uuid.UUID):
        """Stop a running Stream client for an agent."""
        stop_event = self._stop_events.pop(agent_id, None)
        if stop_event:
            stop_event.set()
        thread = self._threads.pop(agent_id, None)
        if thread and thread.is_alive():
            logger.info(f"[DingTalk Stream] Stopping client for agent {agent_id}, waiting for thread...")
            thread.join(timeout=5)
            if thread.is_alive():
                logger.warning(f"[DingTalk Stream] Thread for {agent_id} did not exit within 5s")

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
