"""WebSocket chat endpoint for real-time agent conversations."""

import asyncio
from collections import deque
from dataclasses import dataclass
import uuid
from datetime import datetime, timezone as tz


from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect
from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging_config import set_trace_id
from app.core.permissions import check_agent_access, is_agent_expired
from app.core.security import decode_access_token
from app.database import async_session
from app.models.agent import Agent
from app.models.agent_run import AgentRun
from app.models.agent_run_command import AgentRunCommand
from app.models.audit import ChatMessage
from app.models.chat_session import ChatSession
from app.models.llm import LLMModel
from app.models.user import User
from app.services.activity_logger import log_activity
from app.services.agent_runtime.adapter import RuntimeCommandIntake
from app.services.agent_runtime.chat_intake import (
    ChatRuntimeIntake,
    ChatRuntimeIntakeError,
    enqueue_chat_runtime,
    onboarding_source_execution_id,
)
from app.services.agent_runtime.chat_stream import (
    ChatRuntimeStreamOutcome,
    stream_web_chat_run,
)
from app.services.agent_runtime.contracts import CancelRunCommand, RunHandle, RuntimeEventCursor
from app.services.agent_runtime.run_state_reader import RunStateReadError, open_run_state_reader
from app.services.chat_session_service import ensure_primary_platform_session
from app.services.llm.utils import convert_chat_messages_to_llm_format
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

router = APIRouter(tags=["websocket"])

@dataclass(frozen=True, slots=True)
class WebChatRuntimeIntake:
    """Runtime intake plus the Web-only onboarding phase notification."""

    run: ChatRuntimeIntake
    onboarding_target_phase: str | None = None


@dataclass(frozen=True, slots=True)
class AcceptedWebChatMessage:
    """One client message already persisted as a durable Runtime command."""

    runtime: WebChatRuntimeIntake
    user_content: str
    is_onboarding_trigger: bool = False


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
        if self.agent is None or self.agent.tenant_id is None:
            await self.websocket.send_json(
                {
                    "type": "error",
                    "content": "Agent chat scope is unavailable",
                    "code": "chat_connection_not_ready",
                }
            )
            await self.websocket.close(code=4002)
            return None
        if self.session_id_param is not None:
            try:
                session_id = uuid.UUID(self.session_id_param)
            except (ValueError, TypeError):
                await self.websocket.send_json(
                    {
                        "type": "error",
                        "content": "Invalid chat session",
                        "code": "invalid_chat_session",
                    }
                )
                await self.websocket.close(code=4002)
                return None
            result = await db.execute(
                select(ChatSession).where(
                    ChatSession.id == session_id,
                    ChatSession.tenant_id == self.agent.tenant_id,
                    ChatSession.agent_id == self.agent_id,
                    ChatSession.user_id == user_id,
                    ChatSession.session_type == "direct",
                    ChatSession.group_id.is_(None),
                    ChatSession.source_channel == "web",
                    ChatSession.is_group.is_(False),
                    ChatSession.deleted_at.is_(None),
                )
            )
            existing = result.scalar_one_or_none()
            if (
                existing is None
                or existing.tenant_id != self.agent.tenant_id
                or existing.agent_id != self.agent_id
                or existing.user_id != user_id
                or existing.session_type != "direct"
                or existing.group_id is not None
                or existing.source_channel != "web"
                or existing.is_group
                or existing.deleted_at is not None
            ):
                await self.websocket.send_json(
                    {
                        "type": "error",
                        "content": "Not authorized for this session",
                        "code": "chat_session_scope_mismatch",
                    }
                )
                await self.websocket.close(code=4002)
                return None
            return str(existing.id)

        result = await db.execute(
                select(ChatSession)
                .where(
                    ChatSession.tenant_id == self.agent.tenant_id,
                    ChatSession.agent_id == self.agent_id,
                    ChatSession.user_id == user_id,
                    ChatSession.source_channel == "web",
                    ChatSession.session_type == "direct",
                    ChatSession.group_id.is_(None),
                    ChatSession.is_group.is_(False),
                    ChatSession.deleted_at.is_(None),
                    ChatSession.is_primary,
                )
                .order_by(ChatSession.last_message_at.desc().nulls_last(), ChatSession.created_at.desc())
                .limit(1)
            )
        latest = result.scalar_one_or_none()
        if latest:
            return str(latest.id)
        new_session = await ensure_primary_platform_session(db, self.agent_id, user_id)
        await db.commit()
        await db.refresh(new_session)
        logger.info(f"[WS] Selected primary session {new_session.id}")
        return str(new_session.id)

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

        pending_runs: deque[AcceptedWebChatMessage] = deque()
        while True:
            if pending_runs:
                accepted = pending_runs.popleft()
            else:
                data = await self.websocket.receive_json()
                if data.get("type") == "abort":
                    if self.agent_type == "openclaw":
                        continue
                    await self._handle_cancel_packet(data)
                    continue
                if data.get("type") == "attach_run":
                    attached = await self._attach_runtime_run(data)
                    if attached is None:
                        continue
                    outcome, queued_messages = await self._run_runtime_and_stream(
                        attached,
                        user_content="",
                    )
                    pending_runs.extend(queued_messages)
                    if outcome is not None:
                        self.conversation.append(
                            {"role": "assistant", "content": outcome.content}
                        )
                    continue
                accepted = await self._accept_client_message(data)
                if accepted is None:
                    continue

            outcome, queued_messages = await self._run_runtime_and_stream(
                accepted.runtime.run,
                user_content=accepted.user_content,
            )
            pending_runs.extend(queued_messages)
            if outcome is not None:
                if not accepted.is_onboarding_trigger:
                    self.conversation.append(
                        {"role": "user", "content": accepted.user_content}
                    )
                self.conversation.append(
                    {"role": "assistant", "content": outcome.content}
                )
                if (
                    outcome.status == "completed"
                    and accepted.runtime.onboarding_target_phase is not None
                ):
                    await self._mark_onboarding_runtime_phase(
                        accepted.runtime.onboarding_target_phase
                    )
            continue

    @staticmethod
    def _event_cursor(value: object) -> RuntimeEventCursor | None:
        if value is None or value == "":
            return None
        if not isinstance(value, str) or "|" not in value:
            raise ChatRuntimeIntakeError(
                "invalid_event_cursor",
                "attach_run cursor must be '<created_at>|<event_id>'",
            )
        created_at_raw, event_id_raw = value.rsplit("|", 1)
        try:
            created_at = datetime.fromisoformat(created_at_raw)
            event_id = uuid.UUID(event_id_raw)
        except (TypeError, ValueError) as exc:
            raise ChatRuntimeIntakeError(
                "invalid_event_cursor",
                "attach_run cursor is invalid",
            ) from exc
        if created_at.tzinfo is None:
            raise ChatRuntimeIntakeError(
                "invalid_event_cursor",
                "attach_run cursor timestamp must include a timezone",
            )
        return RuntimeEventCursor(created_at, event_id)

    async def _attach_runtime_run(self, data: dict) -> ChatRuntimeIntake | None:
        """Reattach this exact Direct Chat socket to an already-running Run."""
        if self.user is None or self.agent is None or self.conv_id is None:
            return None
        try:
            run_id = self._optional_client_uuid(data.get("run_id"), field="run_id")
            if run_id is None:
                raise ChatRuntimeIntakeError("missing_run_id", "attach_run requires run_id")
            after = self._event_cursor(data.get("cursor"))
            session_id = uuid.UUID(self.conv_id)
        except (ChatRuntimeIntakeError, ValueError) as exc:
            code = getattr(exc, "code", "invalid_chat_session")
            await self.websocket.send_json({"type": "error", "content": str(exc), "code": code})
            return None

        async with async_session() as db:
            result = await db.execute(
                select(AgentRun).where(
                    AgentRun.tenant_id == self.agent.tenant_id,
                    AgentRun.id == run_id,
                    AgentRun.agent_id == self.agent_id,
                    AgentRun.session_id == session_id,
                    AgentRun.origin_user_id == self.user.id,
                    AgentRun.source_type == "chat",
                    AgentRun.run_kind == "foreground",
                    AgentRun.runtime_type == "langgraph",
                    AgentRun.runtime_thread_id == str(session_id),
                    AgentRun.scheduling_lane_key
                    == f"direct_chat_thread:{self.agent.tenant_id}:{session_id}",
                    AgentRun.lane_held.is_(True),
                )
            )
            run = result.scalar_one_or_none()
            if run is None:
                await self.websocket.send_json(
                    {
                        "type": "error",
                        "content": "Run is not active in this Direct Chat session.",
                        "code": "chat_attach_scope_mismatch",
                    }
                )
                return None
            command_result = await db.execute(
                select(AgentRunCommand)
                .where(
                    AgentRunCommand.tenant_id == run.tenant_id,
                    AgentRunCommand.run_id == run.id,
                )
                .order_by(AgentRunCommand.created_at.desc(), AgentRunCommand.id.desc())
                .limit(1)
            )
            command = command_result.scalar_one_or_none()
            if command is None:
                await self.websocket.send_json(
                    {"type": "error", "content": "Run command is unavailable.", "code": "chat_attach_command_missing"}
                )
                return None
        source_id = run.source_id or ""
        try:
            message_id = uuid.UUID(source_id)
        except ValueError:
            message_id = uuid.uuid5(run.id, "attached-chat-message")
        return ChatRuntimeIntake(
            handle=RunHandle(
                tenant_id=run.tenant_id,
                run_id=run.id,
                thread_id=run.runtime_thread_id,
                command_id=command.id,
                runtime_type="langgraph",
                created=False,
            ),
            message_id=message_id,
            resumed=False,
            stream_after=after,
        )

    async def _accept_client_message(
        self,
        data: dict,
    ) -> AcceptedWebChatMessage | None:
        """Validate and durably enqueue one explicit client input."""
        set_trace_id(str(uuid.uuid4())[:12])
        content = data.get("content", "")
        display_content = data.get("display_content", "")
        file_name = data.get("file_name", "")
        override_model_id = data.get("model_id")
        is_onboarding_trigger = data.get("kind") == "onboarding_trigger"
        logger.info(
            f"[WS] Received: {str(content)[:50]}"
            + (" [onboarding]" if is_onboarding_trigger else "")
        )
        if not isinstance(content, str) or (not content and not is_onboarding_trigger):
            return None
        onboarding_source_execution: str | None = None
        if is_onboarding_trigger:
            onboarding_source_execution = await self._handle_onboarding_trigger_guard()
            if onboarding_source_execution is None:
                return None
            content = "Please begin the onboarding."

        try:
            message_id = self._optional_client_uuid(
                data.get("message_id"),
                field="message_id",
            )
            resume_run_id = self._optional_client_uuid(
                data.get("run_id"),
                field="run_id",
            )
        except ChatRuntimeIntakeError as exc:
            await self.websocket.send_json(
                {"type": "error", "content": str(exc), "code": exc.code}
            )
            return None
        resume_correlation_id = data.get("correlation_id")
        if resume_correlation_id is not None and not isinstance(
            resume_correlation_id,
            str,
        ):
            await self.websocket.send_json(
                {
                    "type": "error",
                    "content": "correlation_id must be a string",
                    "code": "invalid_chat_resume_correlation",
                }
            )
            return None

        self.current_user_text = content
        effective_llm_model = await self._resolve_effective_model(override_model_id)
        if not await self._check_quotas():
            return None
        if self.agent_type == "openclaw":
            self.conversation.append({"role": "user", "content": content})
            await self._save_user_message(
                content,
                display_content,
                file_name,
                is_onboarding_trigger,
            )
            await self._route_openclaw(content)
            return None
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
            return None

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
                onboarding_source_execution_id=onboarding_source_execution,
            )
        except ChatRuntimeIntakeError as exc:
            logger.warning(f"[WS] Runtime chat intake rejected ({exc.code}): {exc}")
            await self.websocket.send_json(
                {"type": "error", "content": str(exc), "code": exc.code}
            )
            return None
        except Exception as exc:
            error_code = getattr(exc, "code", "runtime_intake_failed")
            if is_onboarding_trigger and error_code in {
                "source_idempotency_mismatch",
                "command_idempotency_mismatch",
            }:
                # A concurrent socket for the same pair won the durable source
                # identity. Re-read it and acknowledge the stale trigger
                # instead of surfacing a false chat failure.
                await self._handle_onboarding_trigger_guard()
                return None
            logger.exception(f"[WS] Runtime chat intake failed ({error_code}): {exc}")
            await self.websocket.send_json(
                {
                    "type": "error",
                    "content": "Message could not be accepted by the durable Runtime.",
                    "code": error_code,
                }
            )
            return None
        if web_intake is None:
            await self.websocket.send_json(
                {
                    "type": "error",
                    "content": "Durable Runtime is not enabled for native Web Chat.",
                    "code": "runtime_disabled",
                }
            )
            return None
        return AcceptedWebChatMessage(
            runtime=web_intake,
            user_content=content,
            is_onboarding_trigger=is_onboarding_trigger,
        )

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
        onboarding_source_execution_id: str | None = None,
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
                async with open_run_state_reader(db) as run_state_reader:
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
                        source_execution_id_override=(
                            onboarding_source_execution_id
                            if is_onboarding_trigger
                            else None
                        ),
                        application_tools_enabled=not (
                            onboarding is not None and onboarding.is_greeting_turn
                        ),
                        run_state_reader=run_state_reader,
                    )
                if intake is None:
                    return None
                if is_onboarding_trigger and session.title.startswith("Session "):
                    session.title = "Onboarding"
                return WebChatRuntimeIntake(
                    run=intake,
                    onboarding_target_phase=target_phase,
                )

    async def _cancel_runtime_run(self, run_id: uuid.UUID) -> RunHandle:
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
        idempotency_key = f"cancel:web:{run_id}"
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
                if (
                    session is None
                    or session.tenant_id != agent.tenant_id
                    or session.agent_id != agent.id
                    or session.user_id != user.id
                    or session.session_type != "direct"
                    or session.group_id is not None
                    or session.source_channel != "web"
                    or session.deleted_at is not None
                ):
                    raise ChatRuntimeIntakeError(
                        "chat_cancel_scope_mismatch",
                        "Cancel target is outside this Direct Chat Session",
                    )
                run_result = await db.execute(
                    select(AgentRun).where(
                        AgentRun.tenant_id == agent.tenant_id,
                        AgentRun.id == run_id,
                    )
                )
                run = run_result.scalar_one_or_none()
                if (
                    run is None
                    or run.agent_id != agent.id
                    or run.session_id != session.id
                    or run.origin_user_id != user.id
                    or run.source_type != "chat"
                    or run.run_kind != "foreground"
                    or run.runtime_type != "langgraph"
                    or run.runtime_thread_id != str(session.id)
                    or run.scheduling_lane_key
                    != f"direct_chat_thread:{agent.tenant_id}:{session.id}"
                ):
                    raise ChatRuntimeIntakeError(
                        "chat_cancel_scope_mismatch",
                        "Cancel target is not a Run in this Direct Chat Session",
                    )
                existing_result = await db.execute(
                    select(AgentRunCommand).where(
                        AgentRunCommand.tenant_id == agent.tenant_id,
                        AgentRunCommand.run_id == run.id,
                        AgentRunCommand.command_type == "cancel",
                        AgentRunCommand.idempotency_key == idempotency_key,
                    )
                )
                existing = existing_result.scalar_one_or_none()
                if not run.lane_held and existing is None:
                    raise ChatRuntimeIntakeError(
                        "chat_cancel_not_lane_holder",
                        "Cancel target is no longer the active Direct Chat Run",
                    )
                return await RuntimeCommandIntake(db).cancel_run(
                    CancelRunCommand(
                        tenant_id=agent.tenant_id,
                        run_id=run.id,
                        idempotency_key=idempotency_key,
                        reason="cancelled_by_user",
                        actor_user_id=user.id,
                    )
                )

    async def _handle_cancel_packet(
        self,
        data: dict,
        *,
        expected_run_id: uuid.UUID | None = None,
    ) -> None:
        try:
            run_id = self._optional_client_uuid(data.get("run_id"), field="run_id")
            if run_id is None:
                raise ChatRuntimeIntakeError(
                    "missing_cancel_run_id",
                    "Cancellation requires an explicit run_id",
                )
            if expected_run_id is not None and run_id != expected_run_id:
                raise ChatRuntimeIntakeError(
                    "chat_cancel_run_mismatch",
                    "Cancellation does not target the currently attached Run",
                )
            handle = await self._cancel_runtime_run(run_id)
        except ChatRuntimeIntakeError as exc:
            await self.websocket.send_json(
                {"type": "error", "content": str(exc), "code": exc.code}
            )
            return
        except Exception as exc:
            logger.warning(f"[WS] Runtime cancel enqueue failed: {exc}")
            await self.websocket.send_json(
                {
                    "type": "error",
                    "content": "Cancellation could not be accepted.",
                    "code": getattr(exc, "code", "runtime_cancel_failed"),
                }
            )
            return
        await self.websocket.send_json(
            {
                "type": "runtime_status",
                "run_id": str(handle.run_id),
                "event": "cancel_requested",
                "status": "cancelling",
            }
        )

    async def _run_runtime_and_stream(
        self,
        intake: ChatRuntimeIntake,
        *,
        user_content: str,
    ) -> tuple[ChatRuntimeStreamOutcome | None, list[AcceptedWebChatMessage]]:
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
        queued_messages: list[AcceptedWebChatMessage] = []
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
                    await self._handle_cancel_packet(
                        message,
                        expected_run_id=intake.handle.run_id,
                    )
                    continue
                accepted = await self._accept_client_message(message)
                if accepted is None:
                    continue
                queued_messages.append(accepted)
                await self.websocket.send_json(
                    {
                        "type": "runtime_status",
                        "run_id": str(accepted.runtime.run.handle.run_id),
                        "event": "queued",
                        "status": "queued",
                    }
                )
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

    async def _handle_onboarding_trigger_guard(self) -> str | None:
        """Reserve the next pair-scoped onboarding attempt or reject a stale trigger."""
        if self.user is None or self.agent is None or self.agent.tenant_id is None:
            raise ChatRuntimeIntakeError(
                "chat_connection_not_ready",
                "Web Chat connection has no authenticated onboarding scope",
            )
        tenant_id = self.agent.tenant_id
        first_execution_id = onboarding_source_execution_id(
            tenant_id,
            self.agent_id,
            self.user.id,
            attempt=1,
        )
        source_prefix = first_execution_id.rsplit(":", 1)[0]
        async with async_session() as db:
            if await is_onboarded(db, self.agent_id, self.user.id):
                logger.info("[WS] Onboarding trigger ignored — pair already onboarded")
                await self.websocket.send_json(
                    {
                        "type": "onboarded",
                        "agent_id": str(self.agent_id),
                    }
                )
                return None
            result = await db.execute(
                select(AgentRun)
                .where(
                    AgentRun.tenant_id == tenant_id,
                    AgentRun.agent_id == self.agent_id,
                    AgentRun.origin_user_id == self.user.id,
                    AgentRun.source_type == "chat",
                    AgentRun.source_execution_id.like(f"{source_prefix}:%"),
                )
                .order_by(AgentRun.created_at.desc(), AgentRun.id.desc())
            )
            runs = list(result.scalars().all())
            attempts: list[tuple[int, AgentRun]] = []
            for run in runs:
                raw_attempt = (run.source_execution_id or "").removeprefix(
                    f"{source_prefix}:"
                )
                if raw_attempt.isdigit() and int(raw_attempt) > 0:
                    attempts.append((int(raw_attempt), run))
            if not attempts:
                return first_execution_id
            attempt, latest = max(attempts, key=lambda item: item[0])
            try:
                async with open_run_state_reader(db) as reader:
                    view = await reader.get_run_state(tenant_id, latest.id)
            except RunStateReadError as exc:
                logger.warning(
                    f"[WS] Onboarding trigger held by unreadable Run {latest.id}: {exc.code}"
                )
                view = None
            except Exception as exc:
                logger.exception(
                    f"[WS] Onboarding trigger held while Run {latest.id} state is unavailable: {exc}"
                )
                view = None

            status = view.execution_status if view is not None else None
            if status in {"failed", "cancelled"}:
                return onboarding_source_execution_id(
                    tenant_id,
                    self.agent_id,
                    self.user.id,
                    attempt=attempt + 1,
                )
            if status == "completed":
                # Completion normally reconciles this row in the worker. Repair
                # the narrow crash window so future mounts also stop triggering.
                await mark_onboarding_phase(
                    db,
                    self.agent_id,
                    self.user.id,
                    "greeted",
                )
                await self.websocket.send_json(
                    {"type": "onboarded", "agent_id": str(self.agent_id)}
                )
                return None
            await self.websocket.send_json(
                {
                    "type": "onboarding_pending",
                    "agent_id": str(self.agent_id),
                    "run_id": str(latest.id),
                }
            )
            return None

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
