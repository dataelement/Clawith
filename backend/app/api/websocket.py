"""WebSocket chat endpoint for real-time agent conversations."""

import asyncio
from collections import deque
from dataclasses import dataclass
import json
import uuid
from datetime import datetime, timezone as tz
from time import perf_counter


from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect
from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging_config import set_trace_id
from app.core.permissions import check_agent_access, is_agent_expired
from app.core.security import decode_access_token
from app.database import async_session
from app.models.agent import Agent
from app.models.audit import ChatMessage
from app.models.chat_session import ChatSession
from app.models.llm import LLMModel
from app.models.task import Task
from app.models.user import User
from app.services.activity_logger import log_activity
from app.services.agentbay_live import detect_agentbay_env, get_browser_snapshot, get_desktop_screenshot
from app.services.agent_runtime.adapter import TransactionalAgentRuntimeAdapter
from app.services.agent_runtime.chat_intake import (
    ChatRuntimeIntake,
    ChatRuntimeIntakeError,
    enqueue_chat_runtime,
)
from app.services.agent_runtime.chat_stream import (
    ChatRuntimeStreamOutcome,
    stream_web_chat_run,
)
from app.services.agent_runtime.contracts import CancelRunCommand, RunHandle
from app.services.chat_session_service import ensure_primary_platform_session
from app.services.llm import call_llm_with_failover
from app.services.llm.utils import convert_chat_messages_to_llm_format, truncate_messages_with_pair_integrity
from app.services.onboarding import is_onboarded, mark_onboarding_phase, resolve_onboarding_prompt
from app.services.quota_guard import (
    AgentExpired,
    QuotaExceeded,
    check_agent_expired,
    check_conversation_quota,
    increment_agent_llm_usage,
    increment_conversation_usage,
)
from app.services.realtime import realtime_router
from app.services.task_executor import execute_task

router = APIRouter(tags=["websocket"])

MAX_LIVE_CODE_STREAM_CHARS = 120_000
LIVE_CODE_TRUNCATED_NOTICE = "\n\n[... live output truncated; execution continues ...]\n"


@dataclass(frozen=True, slots=True)
class WebChatRuntimeIntake:
    """Runtime intake plus the Web-only onboarding phase notification."""

    run: ChatRuntimeIntake
    onboarding_target_phase: str | None = None


def extract_partial_content(args_str: str) -> str:
    """Extract the string value of the 'content' field from a partial JSON tool-arguments string.

    When the LLM streams the finish tool call, arguments arrive as an
    incrementally-growing JSON fragment like '{"content": "hello \\\\n wor'.
    This function parses what is available so far, correctly handling JSON
    escape sequences (\\n, \\", \\\\, \\\\uXXXX, etc.) even when the string is
    truncated mid-escape.
    """
    import re as _re

    s = args_str.strip()
    match = _re.search(r'"content"\s*:\s*"', s)
    if not match:
        return ""

    start_idx = match.end()
    val_chars: list[str] = []
    escaped = False
    i = start_idx
    n = len(s)
    while i < n:
        c = s[i]
        if escaped:
            if c == "n":
                val_chars.append("\n")
            elif c == "t":
                val_chars.append("\t")
            elif c == "r":
                val_chars.append("\r")
            elif c == "b":
                val_chars.append("\b")
            elif c == "f":
                val_chars.append("\f")
            elif c == '"':
                val_chars.append('"')
            elif c == "\\":
                val_chars.append("\\")
            elif c == "/":
                val_chars.append("/")
            elif c == "u":
                if i + 4 < n:
                    try:
                        hex_val = int(s[i + 1 : i + 5], 16)
                        val_chars.append(chr(hex_val))
                        i += 4
                    except ValueError:
                        val_chars.append("\\")
                        val_chars.append("u")
                else:
                    # Incomplete \uXXXX — wait for more data
                    val_chars.append("\\")
                    val_chars.append("u")
            else:
                val_chars.append(c)
            escaped = False
        else:
            if c == "\\":
                escaped = True
            elif c == '"':
                # End of the JSON string value
                break
            else:
                val_chars.append(c)
        i += 1
    return "".join(val_chars)


class ConnectionManager:
    """Manage WebSocket connections per agent."""

    def __init__(self):
        # agent_id_str -> list of (WebSocket, session_id_str | None, user_id_str | None)
        self.active_connections: dict[str, list[tuple]] = {}

    async def connect(self, agent_id: str, websocket: WebSocket, session_id: str = None, user_id: str | None = None):
        if agent_id not in self.active_connections:
            self.active_connections[agent_id] = []
        self.active_connections[agent_id].append((websocket, session_id, user_id))
        await realtime_router.register_connection(
            agent_id=agent_id,
            websocket=websocket,
            session_id=session_id,
            user_id=user_id,
        )

    async def disconnect(self, agent_id: str, websocket: WebSocket):
        if agent_id in self.active_connections:
            self.active_connections[agent_id] = [
                (ws, sid, uid) for ws, sid, uid in self.active_connections[agent_id] if ws != websocket
            ]
        await realtime_router.unregister_connection(agent_id=agent_id, websocket=websocket)

    def _local_connections(self, agent_id: str) -> list[tuple[WebSocket, str | None, str | None]]:
        return self.active_connections.get(agent_id, [])

    async def deliver_pubsub_message(
        self,
        *,
        agent_id: str,
        payload: dict,
        session_id: str | None = None,
        user_id: str | None = None,
    ) -> None:
        if agent_id not in self.active_connections:
            return
        for ws, sid, uid in list(self.active_connections[agent_id]):
            if session_id is not None and sid != session_id:
                continue
            if user_id is not None and uid != user_id:
                continue
            try:
                await ws.send_json(payload)
            except Exception:
                pass

    async def send_message(self, agent_id: str, message: dict):
        await realtime_router.route_message(
            agent_id=agent_id,
            message=message,
            local_connections=self._local_connections(agent_id),
        )

    async def send_to_session(self, agent_id: str, session_id: str, message: dict):
        """Send message only to WebSocket connections matching the given session_id."""
        await realtime_router.route_message(
            agent_id=agent_id,
            message=message,
            local_connections=self._local_connections(agent_id),
            session_id=session_id,
        )

    async def send_to_user(self, agent_id: str, user_id: str, message: dict):
        """Send message to all live WebSocket sessions of a given platform user for an agent."""
        await realtime_router.route_message(
            agent_id=agent_id,
            message=message,
            local_connections=self._local_connections(agent_id),
            user_id=user_id,
        )

    async def get_active_session_ids(self, agent_id: str) -> list[str]:
        """Return distinct session IDs for all active WS connections of an agent."""
        return await realtime_router.get_active_session_ids(agent_id)

    async def is_user_viewing_session(self, agent_id: str, session_id: str, user_id: str) -> bool:
        """Return True if the given platform user currently has this exact session open."""
        return await realtime_router.is_user_viewing_session(
            agent_id=agent_id,
            session_id=session_id,
            user_id=user_id,
        )


manager = ConnectionManager()


async def maybe_mark_session_read_for_active_viewer(
    db: AsyncSession,
    *,
    agent_id: uuid.UUID,
    session_id: str,
    user_id: uuid.UUID,
) -> bool:
    """Advance last_read_at_by_user if the owner is actively viewing this exact session."""
    if not await manager.is_user_viewing_session(str(agent_id), session_id, str(user_id)):
        return False

    session = await db.get(ChatSession, uuid.UUID(session_id))
    if not session:
        return False

    session.last_read_at_by_user = datetime.now(tz.utc)
    return True



@router.websocket("/ws/chat/{agent_id}")
async def websocket_chat(
    websocket: WebSocket,
    agent_id: uuid.UUID,
    token: str = Query(...),
    session_id: str = Query(None),
    lang: str = Query("en"),
):
    """WebSocket endpoint for real-time chat with an agent."""
    handler = WebSocketChatHandler(websocket, agent_id, token, session_id, lang)
    await handler.run()


class WebSocketChatHandler:
    """Manages connection lifecycle, message polling, LLM orchestration, and persistence for a single user-agent session."""

    def __init__(
        self,
        websocket: WebSocket,
        agent_id: uuid.UUID,
        token: str,
        session_id: str | None = None,
        lang: str = "en",
    ):
        self.websocket = websocket
        self.agent_id = agent_id
        self.token = token
        self.session_id_param = session_id
        self.lang = lang

        # State fields initialized during setup
        self.user: User | None = None
        self.agent: Agent | None = None
        self.agent_name: str = ""
        self.agent_type: str = ""
        self.role_description: str = ""
        self.welcome_message: str = ""
        self.ctx_size: int = 100
        self.user_display_name: str = ""
        self.llm_model: LLMModel | None = None
        self.fallback_llm_model: LLMModel | None = None
        self.conv_id: str | None = None
        self.history_messages: list[ChatMessage] = []
        self.conversation: list[dict] = []
        self.current_user_text: str = ""
        self.waiting_runtime_run_id: uuid.UUID | None = None
        self.waiting_runtime_correlation_id: str | None = None

    async def run(self):
        """Main entry point for handling the lifecycle of the WebSocket connection."""
        try:
            # 1. Setup session (Authentication, permissions, loading models, history, etc.)
            success = await self.setup()
            if not success:
                return

            # 2. Start the message receiving and processing loop
            await self.message_loop()

        except WebSocketDisconnect:
            logger.info(f"[WS] Client disconnected: {getattr(self.user, 'id', 'unknown')}")
            await manager.disconnect(str(self.agent_id), self.websocket)
        except Exception as e:
            logger.exception(f"[WS] Unexpected error: {e}")
            await manager.disconnect(str(self.agent_id), self.websocket)

    async def setup(self) -> bool:
        """Accepts connection, authenticates user, verifies agent access, loads models, resolves session & history."""
        # Accept immediately so browser sees onopen without waiting for DB setup
        await self.websocket.accept()

        # Authenticate
        try:
            payload = decode_access_token(self.token)
            user_id = uuid.UUID(payload["sub"])
        except Exception:
            await self.websocket.send_json({"type": "error", "content": "Authentication failed"})
            await self.websocket.close(code=4001)
            return False

        try:
            async with async_session() as db:
                result = await db.execute(select(User).where(User.id == user_id))
                self.user = result.scalar_one_or_none()
                if not self.user:
                    logger.error("[WS] User not found")
                    await self.websocket.send_json({"type": "error", "content": "User not found"})
                    await self.websocket.close(code=4001)
                    return False

                logger.info(f"[WS] Checking agent access for {self.agent_id}")
                self.agent, _ = await check_agent_access(db, self.user, self.agent_id)
                if is_agent_expired(self.agent):
                    await self.websocket.send_json(
                        {
                            "type": "error",
                            "content": "This Agent has expired and is off duty. Please contact your admin to extend its service.",
                        }
                    )
                    await self.websocket.close(code=4003)
                    return False

                self.agent_name = self.agent.name
                self.agent_type = self.agent.agent_type or ""
                self.role_description = self.agent.role_description or ""
                self.welcome_message = self.agent.welcome_message or ""
                self.ctx_size = self.agent.context_window_size or 100
                self.user_display_name = (self.user.display_name or "").strip() or "there"
                logger.info(
                    f"[WS] Agent: {self.agent_name}, type: {self.agent_type}, model_id: {self.agent.primary_model_id}, ctx: {self.ctx_size}"
                )

                # Load models
                await self._load_models(db)

                # Resolve or create chat session
                self.conv_id = await self._resolve_chat_session(db, user_id)
                if not self.conv_id:
                    return False

                # Load history messages
                await self._load_history(db)

        except Exception as e:
            logger.exception(f"[WS] Setup error: {e}")
            await self.websocket.send_json({"type": "error", "content": "Setup failed"})
            await self.websocket.close(code=4002)
            return False

        # Connect connection manager
        agent_id_str = str(self.agent_id)
        await manager.connect(agent_id_str, self.websocket, self.conv_id, str(user_id))
        logger.info(f"[WS] Ready! Agent={self.agent_name}")

        # Send session_id to frontend
        await self.websocket.send_json({"type": "connected", "session_id": self.conv_id})

        # Build conversation context
        self.conversation = self._build_conversation_context()

        return True

    async def _load_models(self, db: AsyncSession):
        """Loads primary and fallback models for the agent."""
        if self.agent.primary_model_id:
            model_result = await db.execute(select(LLMModel).where(LLMModel.id == self.agent.primary_model_id))
            self.llm_model = model_result.scalar_one_or_none()
            if self.llm_model and not self.llm_model.enabled:
                logger.info(f"[WS] Primary model {self.llm_model.model} is disabled, skipping")
                self.llm_model = None
            else:
                logger.info(f"[WS] Primary model loaded: {self.llm_model.model if self.llm_model else 'None'}")

        if self.agent.fallback_model_id:
            fb_result = await db.execute(select(LLMModel).where(LLMModel.id == self.agent.fallback_model_id))
            self.fallback_llm_model = fb_result.scalar_one_or_none()
            if self.fallback_llm_model and not self.fallback_llm_model.enabled:
                logger.info(f"[WS] Fallback model {self.fallback_llm_model.model} is disabled, skipping")
                self.fallback_llm_model = None
            elif self.fallback_llm_model:
                logger.info(f"[WS] Fallback model loaded: {self.fallback_llm_model.model}")

        if not self.llm_model and self.fallback_llm_model:
            self.llm_model = self.fallback_llm_model
            self.fallback_llm_model = None
            logger.info(f"[WS] Primary model unavailable, using fallback: {self.llm_model.model}")

    async def _resolve_chat_session(self, db: AsyncSession, user_id: uuid.UUID) -> str | None:
        """Resolves existing session or creates a new one."""
        conv_id = self.session_id_param
        if conv_id:
            try:
                _sid = uuid.UUID(conv_id)
            except (ValueError, TypeError):
                conv_id = None
                _existing = None
            else:
                _sr = await db.execute(
                    select(ChatSession).where(
                        ChatSession.id == _sid,
                        ChatSession.agent_id == self.agent_id,
                    )
                )
                _existing = _sr.scalar_one_or_none()
                if not _existing:
                    conv_id = None
                elif _existing.source_channel != "agent" and str(_existing.user_id) != str(user_id):
                    await self.websocket.send_json({"type": "error", "content": "Not authorized for this session"})
                    await self.websocket.close(code=4003)
                    return None
        if not conv_id:
            _sr = await db.execute(
                select(ChatSession)
                .where(
                    ChatSession.agent_id == self.agent_id,
                    ChatSession.user_id == user_id,
                    ChatSession.source_channel == "web",
                    not ChatSession.is_group,
                    ChatSession.is_primary,
                )
                .order_by(ChatSession.last_message_at.desc().nulls_last(), ChatSession.created_at.desc())
                .limit(1)
            )
            _latest = _sr.scalar_one_or_none()
            if _latest:
                conv_id = str(_latest.id)
            else:
                _new_session = await ensure_primary_platform_session(db, self.agent_id, user_id)
                await db.commit()
                await db.refresh(_new_session)
                conv_id = str(_new_session.id)
                logger.info(f"[WS] Selected primary session {conv_id}")
        return conv_id

    async def _load_history(self, db: AsyncSession):
        """Loads and prepares history messages for the conversation."""
        try:
            history_result = await db.execute(
                select(ChatMessage)
                .where(ChatMessage.agent_id == self.agent_id, ChatMessage.conversation_id == self.conv_id)
                .order_by(ChatMessage.created_at.desc())
                .limit(self.ctx_size)
            )
            self.history_messages = list(reversed(history_result.scalars().all()))
            logger.info(f"[WS] Loaded {len(self.history_messages)} history messages for session {self.conv_id}")
        except Exception as e:
            logger.warning(f"[WS] History load failed (non-fatal): {e}")

    def _build_conversation_context(self) -> list[dict]:
        """Translates historical ChatMessages to LLM inputs."""
        return convert_chat_messages_to_llm_format(self.history_messages)

    async def message_loop(self):
        """Core message processing loop."""
        # Send welcome message on new session (no history)
        if self.welcome_message and not self.history_messages:
            await self.websocket.send_json({"type": "done", "role": "assistant", "content": self.welcome_message})

        pending_messages: deque[dict] = deque()
        while True:
            data = pending_messages.popleft() if pending_messages else await self.websocket.receive_json()
            if data.get("type") == "abort":
                continue

            # Set a unique trace ID for this specific message processing.
            trace_id = str(uuid.uuid4())[:12]
            set_trace_id(trace_id)

            content = data.get("content", "")
            display_content = data.get("display_content", "")
            file_name = data.get("file_name", "")
            override_model_id = data.get("model_id")
            is_onboarding_trigger = data.get("kind") == "onboarding_trigger"
            logger.info(f"[WS] Received: {content[:50]}" + (" [onboarding]" if is_onboarding_trigger else ""))

            if not content and not is_onboarding_trigger:
                continue

            if is_onboarding_trigger:
                if await self._handle_onboarding_trigger_guard():
                    continue
                content = "Please begin the onboarding."

            try:
                message_id = self._optional_client_uuid(data.get("message_id"), field="message_id")
                resume_run_id = self._optional_client_uuid(data.get("run_id"), field="run_id")
            except ChatRuntimeIntakeError as exc:
                await self.websocket.send_json(
                    {"type": "error", "content": str(exc), "code": exc.code}
                )
                continue
            resume_correlation_id = data.get("correlation_id")
            if resume_correlation_id is not None and not isinstance(resume_correlation_id, str):
                await self.websocket.send_json(
                    {
                        "type": "error",
                        "content": "correlation_id must be a string",
                        "code": "invalid_chat_resume_correlation",
                    }
                )
                continue
            if (
                resume_run_id is None
                and resume_correlation_id is None
                and self.waiting_runtime_run_id is not None
                and self.waiting_runtime_correlation_id is not None
            ):
                resume_run_id = self.waiting_runtime_run_id
                resume_correlation_id = self.waiting_runtime_correlation_id

            self.current_user_text = content
            effective_llm_model = await self._resolve_effective_model(override_model_id)

            # Quota Checks
            if not await self._check_quotas():
                continue

            # Remote OpenClaw remains a gateway transport; every native Agent
            # must enter through the durable Runtime and never fall back to the
            # legacy in-request model/tool loop.
            if self.agent_type == "openclaw":
                self.conversation.append({"role": "user", "content": content})
                await self._save_user_message(
                    content,
                    display_content,
                    file_name,
                    is_onboarding_trigger,
                )
                await self._route_openclaw(content)
                continue

            if effective_llm_model is None:
                await self.websocket.send_json(
                    {
                        "type": "error",
                        "content": (
                            f"{self.agent_name} has no enabled LLM model configured. "
                            "Select a model in Agent Settings."
                        ),
                        "code": "model_unavailable",
                    }
                )
                continue

            try:
                web_intake = await self._enqueue_runtime_chat(
                    content=content,
                    display_content=display_content,
                    file_name=file_name,
                    model_id=effective_llm_model.id,
                    message_id=message_id,
                    resume_run_id=resume_run_id,
                    resume_correlation_id=resume_correlation_id,
                    is_onboarding_trigger=is_onboarding_trigger,
                )
            except ChatRuntimeIntakeError as exc:
                logger.warning(f"[WS] Runtime chat intake rejected ({exc.code}): {exc}")
                await self.websocket.send_json(
                    {"type": "error", "content": str(exc), "code": exc.code}
                )
                continue
            except Exception as exc:
                error_code = getattr(exc, "code", "runtime_intake_failed")
                logger.exception(f"[WS] Runtime chat intake failed ({error_code}): {exc}")
                await self.websocket.send_json(
                    {
                        "type": "error",
                        "content": "Message could not be accepted by the durable Runtime.",
                        "code": error_code,
                    }
                )
                continue

            if web_intake is None:
                await self.websocket.send_json(
                    {
                        "type": "error",
                        "content": "Durable Runtime is not enabled for native Web Chat.",
                        "code": "runtime_disabled",
                    }
                )
                continue

            outcome, queued_messages = await self._run_runtime_and_stream(
                web_intake.run,
                user_content=content,
            )
            pending_messages.extend(queued_messages)
            if outcome is not None:
                if not is_onboarding_trigger:
                    self.conversation.append({"role": "user", "content": content})
                self.conversation.append(
                    {"role": "assistant", "content": outcome.content}
                )
                if (
                    outcome.status == "completed"
                    and web_intake.onboarding_target_phase is not None
                ):
                    await self._mark_onboarding_runtime_phase(
                        web_intake.onboarding_target_phase
                    )
            continue

    @staticmethod
    def _optional_client_uuid(value: object, *, field: str) -> uuid.UUID | None:
        if value is None or value == "":
            return None
        try:
            return uuid.UUID(str(value))
        except (TypeError, ValueError) as exc:
            raise ChatRuntimeIntakeError(
                f"invalid_{field}",
                f"{field} must be a UUID",
            ) from exc

    async def _enqueue_runtime_chat(
        self,
        *,
        content: str,
        display_content: str,
        file_name: str,
        model_id: uuid.UUID,
        message_id: uuid.UUID | None,
        resume_run_id: uuid.UUID | None,
        resume_correlation_id: str | None,
        is_onboarding_trigger: bool,
    ) -> WebChatRuntimeIntake | None:
        """Revalidate mutable ingress scope and commit one durable input."""
        if self.user is None or self.conv_id is None:
            raise ChatRuntimeIntakeError(
                "chat_connection_not_ready",
                "Web Chat connection has no authenticated session",
            )
        try:
            session_id = uuid.UUID(self.conv_id)
        except ValueError as exc:
            raise ChatRuntimeIntakeError(
                "invalid_chat_session",
                "Web Chat session ID is invalid",
            ) from exc

        async with async_session() as db:
            async with db.begin():
                user = await db.get(User, self.user.id)
                if user is None or not user.is_active:
                    raise ChatRuntimeIntakeError(
                        "chat_user_unavailable",
                        "Authenticated Chat user is unavailable",
                    )
                agent, _ = await check_agent_access(db, user, self.agent_id)
                session = await db.get(ChatSession, session_id)
                model = await db.get(LLMModel, model_id)
                if session is None:
                    raise ChatRuntimeIntakeError(
                        "chat_session_not_found",
                        "Web Chat session no longer exists",
                    )
                if model is None:
                    raise ChatRuntimeIntakeError(
                        "model_unavailable",
                        "Selected Chat model no longer exists",
                    )
                onboarding = (
                    None
                    if resume_run_id is not None
                    else await resolve_onboarding_prompt(
                        db,
                        agent,
                        user.id,
                        user_name=(user.display_name or "").strip() or "there",
                        user_locale=self.lang,
                    )
                )
                target_phase = (
                    onboarding.target_phase
                    if onboarding is not None and onboarding.lock_on_first_chunk
                    else None
                )
                intake = await enqueue_chat_runtime(
                    db,
                    agent=agent,
                    user=user,
                    session=session,
                    model=model,
                    content=content,
                    display_content=display_content,
                    file_name=file_name,
                    message_id=message_id,
                    resume_run_id=resume_run_id,
                    resume_correlation_id=resume_correlation_id,
                    runtime_instruction=(onboarding.prompt if onboarding is not None else ""),
                    onboarding_target_phase=target_phase or "",
                    persist_user_message=not is_onboarding_trigger,
                    application_tools_enabled=not (
                        onboarding is not None and onboarding.is_greeting_turn
                    ),
                )
                if intake is None:
                    return None
                if is_onboarding_trigger and session.title.startswith("Session "):
                    session.title = "Onboarding"
                return WebChatRuntimeIntake(
                    run=intake,
                    onboarding_target_phase=target_phase,
                )

    async def _cancel_runtime_run(self, handle: RunHandle) -> None:
        if self.user is None:
            return
        async with async_session() as db:
            async with db.begin():
                await TransactionalAgentRuntimeAdapter(db).cancel_run(
                    CancelRunCommand(
                        tenant_id=handle.tenant_id,
                        run_id=handle.run_id,
                        idempotency_key=f"cancel:web:{handle.run_id}",
                        reason="cancelled_by_user",
                        actor_user_id=self.user.id,
                    )
                )

    async def _run_runtime_and_stream(
        self,
        intake: ChatRuntimeIntake,
        *,
        user_content: str,
    ) -> tuple[ChatRuntimeStreamOutcome | None, list[dict]]:
        """Keep the socket responsive while durable work continues off-request."""
        if self.user is None or self.conv_id is None:
            raise ChatRuntimeIntakeError(
                "chat_connection_not_ready",
                "Web Chat connection has no authenticated session",
            )
        session_id = uuid.UUID(self.conv_id)
        stream_task = asyncio.create_task(
            stream_web_chat_run(
                handle=intake.handle,
                session_factory=async_session,
                send_packet=self.websocket.send_json,
                agent_id=self.agent_id,
                session_id=session_id,
                user_id=self.user.id,
                after=intake.stream_after,
            ),
            name=f"web-chat-runtime-{intake.handle.run_id}",
        )
        queued_messages: list[dict] = []
        cancel_requested = False
        try:
            while not stream_task.done():
                try:
                    message = await asyncio.wait_for(
                        self.websocket.receive_json(),
                        timeout=0.25,
                    )
                except asyncio.TimeoutError:
                    continue
                if message.get("type") == "abort":
                    if not cancel_requested:
                        cancel_requested = True
                        try:
                            await self._cancel_runtime_run(intake.handle)
                        except Exception as exc:
                            logger.warning(f"[WS] Runtime cancel enqueue failed: {exc}")
                            await self.websocket.send_json(
                                {
                                    "type": "error",
                                    "content": "Cancellation could not be accepted.",
                                    "code": getattr(exc, "code", "runtime_cancel_failed"),
                                }
                            )
                    continue
                queued_messages.append(message)
            outcome = await stream_task
        except WebSocketDisconnect:
            stream_task.cancel()
            try:
                await stream_task
            except (asyncio.CancelledError, Exception):
                pass
            raise
        except Exception as exc:
            logger.exception(f"[WS] Runtime event stream failed: {exc}")
            if not stream_task.done():
                stream_task.cancel()
                try:
                    await stream_task
                except (asyncio.CancelledError, Exception):
                    pass
            await self.websocket.send_json(
                {
                    "type": "error",
                    "content": "Runtime execution continues, but its live event stream was interrupted.",
                    "code": getattr(exc, "code", "runtime_stream_failed"),
                    "run_id": str(intake.handle.run_id),
                }
            )
            return None, queued_messages

        if outcome.status == "waiting_user":
            self.waiting_runtime_run_id = intake.handle.run_id
            self.waiting_runtime_correlation_id = outcome.correlation_id
        elif self.waiting_runtime_run_id == intake.handle.run_id:
            self.waiting_runtime_run_id = None
            self.waiting_runtime_correlation_id = None

        self.current_user_text = user_content
        await self._update_activity_and_quota(outcome.content)
        async with async_session() as db:
            await maybe_mark_session_read_for_active_viewer(
                db,
                agent_id=self.agent_id,
                session_id=self.conv_id,
                user_id=self.user.id,
            )
            await db.commit()
        return outcome, queued_messages

    async def _handle_onboarding_trigger_guard(self) -> bool:
        """Returns True if the onboarding trigger was ignored (already onboarded)."""
        async with async_session() as _gdb:
            if await is_onboarded(_gdb, self.agent_id, self.user.id):
                logger.info("[WS] Onboarding trigger ignored — pair already onboarded")
                await self.websocket.send_json(
                    {
                        "type": "onboarded",
                        "agent_id": str(self.agent_id),
                    }
                )
                return True
        return False

    async def _mark_onboarding_runtime_phase(self, target_phase: str) -> None:
        """Advance the visible socket immediately; the worker also reconciles it."""
        if self.user is None:
            return
        try:
            async with async_session() as db:
                await mark_onboarding_phase(
                    db,
                    self.agent_id,
                    self.user.id,
                    target_phase,
                )
            await self.websocket.send_json(
                {
                    "type": "onboarded",
                    "agent_id": str(self.agent_id),
                }
            )
        except Exception as exc:
            logger.warning(f"[WS] Runtime onboarding phase update failed: {exc}")

    async def _resolve_effective_model(self, override_model_id: str | None) -> LLMModel | None:
        """Reloads model config and resolves effective model (taking overrides into account)."""
        async with async_session() as _mdb:
            _agent_r = await _mdb.execute(select(Agent).where(Agent.id == self.agent_id))
            _agent_cur = _agent_r.scalar_one_or_none()
            if _agent_cur:
                if _agent_cur.primary_model_id:
                    _m_r = await _mdb.execute(select(LLMModel).where(LLMModel.id == _agent_cur.primary_model_id))
                    _m = _m_r.scalar_one_or_none()
                    self.llm_model = _m if (_m and _m.enabled) else None
                else:
                    self.llm_model = None

                if _agent_cur.fallback_model_id:
                    _fb_r = await _mdb.execute(select(LLMModel).where(LLMModel.id == _agent_cur.fallback_model_id))
                    _fb = _fb_r.scalar_one_or_none()
                    self.fallback_llm_model = _fb if (_fb and _fb.enabled) else None
                else:
                    self.fallback_llm_model = None

                if not self.llm_model and self.fallback_llm_model:
                    self.llm_model = self.fallback_llm_model
                    self.fallback_llm_model = None

        effective_llm_model = self.llm_model
        if override_model_id:
            try:
                _ovr_uuid = uuid.UUID(str(override_model_id))
                async with async_session() as _mdb:
                    _mr = await _mdb.execute(select(LLMModel).where(LLMModel.id == _ovr_uuid))
                    _ovr = _mr.scalar_one_or_none()
                    if (
                        _ovr
                        and _ovr.enabled
                        and self.user is not None
                        and _ovr.tenant_id in {None, self.user.tenant_id}
                    ):
                        effective_llm_model = _ovr
                    else:
                        logger.warning(
                            f"[WS] model override {override_model_id} rejected (missing/disabled/tenant mismatch)"
                        )
            except (ValueError, TypeError):
                logger.warning(f"[WS] model override {override_model_id!r} is not a valid UUID")

        return effective_llm_model

    async def _check_quotas(self) -> bool:
        """Checks conversation and agent LLM quotas. Sends message and returns False if exceeded."""
        try:
            await check_conversation_quota(self.user.id)
            await check_agent_expired(self.agent_id)
            return True
        except QuotaExceeded as qe:
            await self.websocket.send_json({"type": "done", "role": "assistant", "content": f"⚠️ {qe.message}"})
            return False
        except AgentExpired as ae:
            await self.websocket.send_json({"type": "done", "role": "assistant", "content": f"⚠️ {ae.message}"})
            return False

    async def _save_user_message(self, content: str, display_content: str, file_name: str, is_onboarding_trigger: bool):
        """Saves user message to the database and updates session title/time."""
        has_image_marker = "[image_data:" in content
        if has_image_marker:
            saved_content = f"[file:{file_name}]\n{content}" if file_name else content
        else:
            saved_content = display_content if display_content else content
            if file_name:
                saved_content = f"[file:{file_name}]\n{saved_content}"

        if is_onboarding_trigger:
            logger.info("[WS] Onboarding trigger — skipping user-message persistence")
            async with async_session() as _sdb:
                _sr = await _sdb.execute(select(ChatSession).where(ChatSession.id == uuid.UUID(self.conv_id)))
                _s = _sr.scalar_one_or_none()
                if _s and _s.title.startswith("Session "):
                    _s.title = "Onboarding"
                    await _sdb.commit()
        else:
            async with async_session() as db:
                user_msg = ChatMessage(
                    agent_id=self.agent_id,
                    user_id=self.user.id,
                    role="user",
                    content=saved_content,
                    conversation_id=self.conv_id,
                )
                db.add(user_msg)
                # Update session
                _now = datetime.now(tz.utc)
                _sess_r = await db.execute(select(ChatSession).where(ChatSession.id == uuid.UUID(self.conv_id)))
                _sess = _sess_r.scalar_one_or_none()
                if _sess:
                    _sess.last_message_at = _now
                    if not self.history_messages and _sess.title.startswith("Session "):
                        title_src = display_content if display_content else content
                        clean_title = title_src.replace("[图片] ", "📷 ").replace("[image_data:", "").strip()
                        if file_name and not clean_title:
                            clean_title = f"📎 {file_name}"
                        _sess.title = clean_title[:40] if clean_title else content[:40]
                await db.commit()
            logger.info("[WS] User message saved")

    async def _route_openclaw(self, content: str):
        """Enqueues message for OpenClaw edge node poll."""
        from app.models.gateway_message import GatewayMessage as GwMsg

        async with async_session() as db:
            gw_msg = GwMsg(
                agent_id=self.agent_id,
                sender_user_id=self.user.id,
                conversation_id=self.conv_id,
                content=content,
                status="pending",
            )
            db.add(gw_msg)
            await db.commit()
        logger.info("[WS] OpenClaw: message queued for gateway poll")
        await self.websocket.send_json(
            {
                "type": "done",
                "role": "assistant",
                "content": "Message forwarded to OpenClaw agent. Waiting for response...",
            }
        )

    async def _run_llm_and_stream(
        self, effective_llm_model: LLMModel, is_onboarding_trigger: bool
    ) -> tuple[str, list[str], list[dict]]:
        """Calls the LLM and streams response chunks to WebSocket."""
        start_gen = perf_counter()
        try:
            logger.info(f"[WS] Calling LLM {effective_llm_model.model} (streaming)...")

            # Accumulate partial content for abort handling
            partial_chunks: list[str] = []
            # Track how many characters of finish-tool content have been streamed
            finish_content_sent_len = 0

            # Set inside _call_with_failover when an onboarding prompt was injected
            needs_onboarding_mark = False
            onboarding_target_phase = "completed"
            onboarding_mark_done = False

            async def maybe_mark_onboarding_progress():
                nonlocal onboarding_mark_done
                if needs_onboarding_mark and not onboarding_mark_done:
                    onboarding_mark_done = True
                    try:
                        async with async_session() as _ob_db:
                            await mark_onboarding_phase(
                                _ob_db,
                                self.agent_id,
                                self.user.id,
                                onboarding_target_phase,
                            )
                        # Tell the frontend to refresh its cached agent record
                        await self.websocket.send_json(
                            {
                                "type": "onboarded",
                                "agent_id": str(self.agent_id),
                            }
                        )
                    except Exception as _ob_err:
                        logger.warning(f"[WS] mark_onboarded failed (non-fatal): {_ob_err}")

            async def stream_to_ws(text: str):
                """Send each chunk to client in real-time."""
                partial_chunks.append(text)
                await self.websocket.send_json({"type": "chunk", "content": text})
                await maybe_mark_onboarding_progress()

            async def tool_call_to_ws(data: dict):
                """Send tool call info to client and persist completed ones."""
                if data.get("status") in {"running", "done"}:
                    await maybe_mark_onboarding_progress()
                if data.get("status") == "done":
                    # Inject Live Preview & Workspace Activities
                    await self._inject_live_preview_and_workspace_metadata(data)

                await self.websocket.send_json({"type": "tool_call", **data})

                # Save completed tool calls to DB so they persist in chat history
                if data.get("status") == "done":
                    await self._save_completed_tool_call_to_db(data)

            # Track thinking content for storage
            thinking_content = []

            async def thinking_to_ws(text: str):
                """Send thinking chunks to client for collapsible display."""
                thinking_content.append(text)
                await self.websocket.send_json({"type": "thinking", "content": text})

            _workspace_draft_cache: dict[str, str] = {}

            async def tool_delta_to_ws(data: dict):
                """Stream workspace file-operation drafts while tool args are still arriving."""
                nonlocal finish_content_sent_len
                tool_name = data.get("name", "")

                # Stream finish tool content as real-time chunks
                if tool_name == "finish":
                    raw_args = data.get("arguments", "")
                    if isinstance(raw_args, str) and raw_args:
                        current_content = extract_partial_content(raw_args)
                        if len(current_content) > finish_content_sent_len:
                            delta = current_content[finish_content_sent_len:]
                            finish_content_sent_len = len(current_content)
                            await stream_to_ws(delta)
                    return

                _ws_tools = {
                    "write_file",
                    "edit_file",
                    "move_file",
                    "delete_file",
                    "convert_markdown_to_docx",
                    "convert_csv_to_xlsx",
                    "convert_markdown_to_pdf",
                    "convert_html_to_pdf",
                    "convert_html_to_pptx",
                }
                if tool_name not in _ws_tools:
                    return

                raw_args = data.get("arguments", "")
                if isinstance(raw_args, (dict, list)):
                    raw_args = json.dumps(raw_args, ensure_ascii=False)
                elif raw_args is None:
                    raw_args = ""
                else:
                    raw_args = str(raw_args)

                draft_id = str(data.get("id") or f"draft-{data.get('index', 0)}")
                if _workspace_draft_cache.get(draft_id) == raw_args:
                    return
                _workspace_draft_cache[draft_id] = raw_args

                await self.websocket.send_json(
                    {
                        "type": "workspace_draft",
                        "id": draft_id,
                        "index": data.get("index", 0),
                        "name": tool_name,
                        "arguments": raw_args,
                    }
                )

            # Run call_llm_with_failover as a cancellable task
            async def _call_with_failover():
                nonlocal needs_onboarding_mark, onboarding_target_phase

                async def _on_failover(reason: str):
                    await self.websocket.send_json({"type": "info", "content": f"Primary model error, {reason}"})

                _truncated = truncate_messages_with_pair_integrity(self.conversation, self.ctx_size)

                # Resolve onboarding prompt
                skip_tools_for_greeting = False
                try:
                    async with async_session() as _ob_db:
                        _onb = await resolve_onboarding_prompt(
                            _ob_db,
                            self.agent,
                            self.user.id,
                            user_name=self.user_display_name,
                            user_locale=self.lang,
                        )
                    if _onb:
                        _truncated = [{"role": "system", "content": _onb.prompt}] + _truncated
                        if _onb.lock_on_first_chunk:
                            needs_onboarding_mark = True
                            onboarding_target_phase = _onb.target_phase
                        if _onb.is_greeting_turn:
                            skip_tools_for_greeting = True
                except Exception as _onb_err:
                    logger.warning(f"[WS] Onboarding prompt resolve failed (non-fatal): {_onb_err}")

                live_code_chars_sent = 0
                live_code_truncated_sent = False

                async def code_output_to_ws(text: str, label: str = "stdout"):
                    """Stream execute_code output chunks to the frontend live panel in real-time."""
                    nonlocal live_code_chars_sent, live_code_truncated_sent
                    try:
                        remaining = MAX_LIVE_CODE_STREAM_CHARS - live_code_chars_sent
                        if remaining <= 0:
                            if not live_code_truncated_sent:
                                live_code_truncated_sent = True
                                await self.websocket.send_json(
                                    {
                                        "type": "agentbay_live",
                                        "env": "code",
                                        "output": LIVE_CODE_TRUNCATED_NOTICE,
                                        "stream": label,
                                    }
                                )
                            return

                        output = text[:remaining]
                        live_code_chars_sent += len(output)
                        await self.websocket.send_json(
                            {
                                "type": "agentbay_live",
                                "env": "code",
                                "output": output,
                                "stream": label,
                            }
                        )
                    except Exception:
                        pass

                return await call_llm_with_failover(
                    primary_model=effective_llm_model,
                    fallback_model=self.fallback_llm_model,
                    messages=_truncated,
                    agent_name=self.agent_name,
                    role_description=self.role_description,
                    agent_id=self.agent_id,
                    user_id=self.user.id,
                    session_id=self.conv_id,
                    on_chunk=stream_to_ws,
                    on_tool_call=tool_call_to_ws,
                    on_tool_delta=tool_delta_to_ws,
                    on_thinking=thinking_to_ws,
                    supports_vision=getattr(effective_llm_model, "supports_vision", False),
                    on_failover=_on_failover,
                    skip_tools=skip_tools_for_greeting,
                    on_code_output=code_output_to_ws,
                )

            llm_task = asyncio.create_task(_call_with_failover())

            # Listen for abort while LLM is running
            aborted = False
            queued_messages: list[dict] = []
            while not llm_task.done():
                try:
                    msg = await asyncio.wait_for(self.websocket.receive_json(), timeout=0.5)
                    if msg.get("type") == "abort":
                        logger.info("[WS] Abort received, cancelling LLM task")
                        llm_task.cancel()
                        aborted = True
                        break
                    else:
                        queued_messages.append(msg)
                except asyncio.TimeoutError:
                    continue
                except WebSocketDisconnect:
                    llm_task.cancel()
                    raise

            if aborted:
                try:
                    await llm_task
                except (asyncio.CancelledError, Exception):
                    pass
                partial_text = "".join(partial_chunks).strip()
                assistant_response = (
                    (partial_text + "\n\n*[Generation stopped]*") if partial_text else "*[Generation stopped]*"
                )
                logger.info(f"[WS] LLM aborted, partial: {assistant_response[:80]}")
            else:
                assistant_response = await llm_task
                logger.info(f"[WS] LLM response: {assistant_response[:80]}")

            # Raise error on prefix for failover matching
            _llm_error_prefixes = ("[LLM Error]", "[LLM call error]", "[Error]")
            if (
                not aborted
                and assistant_response
                and any(assistant_response.startswith(p) for p in _llm_error_prefixes)
            ):
                raise RuntimeError(assistant_response)

            # Post-success actions (last_active_at, quota usage increments, activity logs)
            await self._update_activity_and_quota(assistant_response)

            return assistant_response, thinking_content, queued_messages

        except WebSocketDisconnect:
            raise
        except Exception as e:
            gen_duration = perf_counter() - start_gen
            logger.exception(f"[WS] LLM error after {gen_duration:.3f}s: {e}")
            return f"[LLM call error] {str(e)[:200]}", [], []

    async def _inject_live_preview_and_workspace_metadata(self, data: dict):
        """Injects live previews and workspace panel activity tracking into tool results."""
        try:
            tool_name = data.get("name", "")
            env = detect_agentbay_env(tool_name)
            if env == "desktop":
                b64_url = await get_desktop_screenshot(self.agent_id, session_id=self.conv_id)
                if b64_url:
                    data["live_preview"] = {"env": env, "screenshot_url": b64_url}
                    logger.info(f"[WS][LivePreview] Embedded {env} base64 in tool_call")
            elif env == "browser":
                b64_url = await get_browser_snapshot(self.agent_id, session_id=self.conv_id)
                if b64_url:
                    data["live_preview"] = {"env": env, "screenshot_url": b64_url}
                    logger.info(f"[WS][LivePreview] Embedded {env} base64 in tool_call")
            elif env == "code":
                tool_result = data.get("result", "") or ""
                data["live_preview"] = {"env": "code", "output": tool_result[:5000]}
        except Exception as _lp_err:
            logger.warning(f"[WS][LivePreview] Embed failed: {_lp_err}")

        _workspace_tool_actions = {
            "write_file": "write",
            "edit_file": "edit",
            "move_file": "move",
            "delete_file": "delete",
            "convert_markdown_to_docx": "convert",
            "convert_csv_to_xlsx": "convert",
            "convert_markdown_to_pdf": "convert",
            "convert_html_to_pdf": "convert",
            "convert_html_to_pptx": "convert",
        }
        _done_tool_name = data.get("name", "")
        if _done_tool_name in _workspace_tool_actions:
            _ws_args = data.get("args") or {}
            if isinstance(_ws_args, str):
                try:
                    _ws_args = json.loads(_ws_args)
                except Exception:
                    _ws_args = {}
            _ws_path = _ws_args.get("output_path") or _ws_args.get("destination_path") or _ws_args.get("path", "")
            _ws_result = str(data.get("result") or "")
            _pending_approval = "requires approval" in _ws_result.lower()
            data["workspace_activity"] = {
                "action": _workspace_tool_actions[_done_tool_name],
                "path": _ws_path,
                "tool": _done_tool_name,
                "ok": not _pending_approval,
                "pendingApproval": _pending_approval,
            }
            logger.info(f"[WS][Workspace] activity: {_done_tool_name} → {_ws_path}")

    async def _save_completed_tool_call_to_db(self, data: dict):
        """Persist completed tool calls in ChatMessage DB logs."""
        try:
            from app.services.chat_session_service import save_tool_call_log
            await save_tool_call_log(
                agent_id=self.agent_id,
                user_id=self.user.id,
                conversation_id=self.conv_id,
                tool_name=data.get("name", ""),
                arguments=data.get("args"),
                result=(data.get("result") or "")[:500],
                status="done",
                tool_call_id=data.get("call_id"),
                reasoning_content=data.get("reasoning_content"),
            )
            async with async_session() as _tc_db:
                await maybe_mark_session_read_for_active_viewer(
                    _tc_db,
                    agent_id=self.agent_id,
                    session_id=self.conv_id,
                    user_id=self.user.id,
                )
                await _tc_db.commit()
        except Exception as _tc_err:
            logger.warning(f"[WS] Failed to save tool_call: {_tc_err}")

    async def _update_activity_and_quota(self, assistant_response: str):
        """Update last_active_at, conversation/agent LLM usage, and log activity."""
        try:
            async with async_session() as _db:
                _ar = await _db.execute(select(Agent).where(Agent.id == self.agent_id))
                _agent = _ar.scalar_one_or_none()
                if _agent:
                    _agent.last_active_at = datetime.now(tz.utc)
                    await _db.commit()
        except Exception as e:
            logger.warning(f"[WS] Failed to update last_active_at: {e}")

        try:
            await increment_conversation_usage(self.user.id)
            await increment_agent_llm_usage(self.agent_id)
        except Exception:
            pass

        try:
            user_text = getattr(self, "current_user_text", "")
            await log_activity(
                self.agent_id,
                "chat_reply",
                f"Replied to web chat: {assistant_response[:80]}",
                detail={"channel": "web", "user_text": user_text[:200], "reply": assistant_response[:500]},
            )
        except Exception as e:
            logger.warning(f"[WS] Failed to log activity: {e}")

    async def _create_task_record(self, task_title: str, assistant_response: str) -> str:
        """Creates a background execution task from task matching."""
        if not task_title:
            return assistant_response
        try:
            async with async_session() as db:
                task = Task(
                    agent_id=self.agent_id,
                    title=task_title,
                    created_by=self.user.id,
                    status="pending",
                    priority="medium",
                )
                db.add(task)
                await db.commit()
                await db.refresh(task)
                logger.info(f"[WS] Task created: {task.id}")
                task_id = task.id
            asyncio.create_task(execute_task(task_id, self.agent_id))
            assistant_response += f"\n\n📋 Task synced to task board: [{task_title}]"
        except Exception as te:
            logger.error(f"[WS] Task creation failed: {te}")
        return assistant_response

    async def _save_assistant_reply(self, assistant_response: str, thinking_content: list[str]):
        """Saves assistant reply to DB."""
        async with async_session() as db:
            assistant_msg = ChatMessage(
                agent_id=self.agent_id,
                user_id=self.user.id,
                role="assistant",
                content=assistant_response,
                conversation_id=self.conv_id,
                thinking="".join(thinking_content) if thinking_content else None,
            )
            db.add(assistant_msg)
            await maybe_mark_session_read_for_active_viewer(
                db,
                agent_id=self.agent_id,
                session_id=self.conv_id,
                user_id=self.user.id,
            )
            await db.commit()
        logger.info("[WS] Assistant message saved")
