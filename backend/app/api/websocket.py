"""WebSocket chat endpoint for real-time agent conversations."""

import json
import uuid
from datetime import datetime, timezone as tz

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Query
from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.security import decode_access_token
from app.core.permissions import check_agent_access, is_agent_expired
from app.database import async_session
from app.models.agent import Agent
from app.models.audit import ChatMessage
from app.models.chat_session import ChatSession
from app.models.llm import LLMModel
from app.models.user import User
from app.services.chat_session_service import ensure_primary_platform_session
from app.services.llm import call_llm, call_llm_with_failover

router = APIRouter(tags=["websocket"])


class ConnectionManager:
    """Manage WebSocket connections per agent."""

    def __init__(self):
        # agent_id_str -> list of (WebSocket, session_id_str | None, user_id_str | None)
        self.active_connections: dict[str, list[tuple]] = {}

    async def connect(self, agent_id: str, websocket: WebSocket, session_id: str = None, user_id: str | None = None):
        await websocket.accept()
        if agent_id not in self.active_connections:
            self.active_connections[agent_id] = []
        self.active_connections[agent_id].append((websocket, session_id, user_id))

    def disconnect(self, agent_id: str, websocket: WebSocket):
        if agent_id in self.active_connections:
            self.active_connections[agent_id] = [
                (ws, sid, uid) for ws, sid, uid in self.active_connections[agent_id] if ws != websocket
            ]

    async def send_message(self, agent_id: str, message: dict):
        if agent_id in self.active_connections:
            for ws, _sid, _uid in self.active_connections[agent_id]:
                try:
                    await ws.send_json(message)
                except Exception:
                    pass

    async def send_to_session(self, agent_id: str, session_id: str, message: dict):
        """Send message only to WebSocket connections matching the given session_id."""
        if agent_id in self.active_connections:
            for ws, sid, _uid in self.active_connections[agent_id]:
                if sid == session_id:
                    try:
                        await ws.send_json(message)
                    except Exception:
                        pass

    async def send_to_user(self, agent_id: str, user_id: str, message: dict):
        """Send message to all live WebSocket sessions of a given platform user for an agent."""
        if agent_id in self.active_connections:
            for ws, _sid, uid in self.active_connections[agent_id]:
                if uid == user_id:
                    try:
                        await ws.send_json(message)
                    except Exception:
                        pass

    def get_active_session_ids(self, agent_id: str) -> list[str]:
        """Return distinct session IDs for all active WS connections of an agent."""
        if agent_id not in self.active_connections:
            return []
        return list(set(sid for _ws, sid, _uid in self.active_connections[agent_id] if sid))

    def is_user_viewing_session(self, agent_id: str, session_id: str, user_id: str) -> bool:
        """Return True if the given platform user currently has this exact session open."""
        if agent_id not in self.active_connections:
            return False
        for _ws, sid, uid in self.active_connections[agent_id]:
            if sid == session_id and uid == user_id:
                return True
        return False


manager = ConnectionManager()


async def maybe_mark_session_read_for_active_viewer(
    db: AsyncSession,
    *,
    agent_id: uuid.UUID,
    session_id: str,
    user_id: uuid.UUID,
) -> bool:
    """Advance last_read_at_by_user if the owner is actively viewing this exact session."""
    if not manager.is_user_viewing_session(str(agent_id), session_id, str(user_id)):
        return False

    session = await db.get(ChatSession, uuid.UUID(session_id))
    if not session:
        return False

    session.last_read_at_by_user = datetime.now(tz.utc)
    return True


from fastapi import Depends
from app.core.security import get_current_user
from app.database import get_db
from app.models.user import User


@router.get("/api/chat/{agent_id}/history")
async def get_chat_history(
    agent_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Return web chat message history for this user + agent."""
    conv_id = f"web_{current_user.id}"
    result = await db.execute(
        select(ChatMessage)
        .where(ChatMessage.agent_id == agent_id, ChatMessage.conversation_id == conv_id)
        .order_by(ChatMessage.created_at.asc())
        .limit(200)
    )
    messages = result.scalars().all()
    out = []
    for m in messages:
        entry: dict = {"role": m.role, "content": m.content, "created_at": m.created_at.isoformat() if m.created_at else None}
        if getattr(m, 'thinking', None):
            entry["thinking"] = m.thinking
        if m.role == "tool_call":
            # Parse JSON-encoded tool call data
            try:
                import json
                data = json.loads(m.content)
                entry["content"] = ""
                entry["toolName"] = data.get("name", "")
                entry["toolArgs"] = data.get("args")
                entry["toolStatus"] = data.get("status", "done")
                entry["toolResult"] = data.get("result", "")
            except Exception:
                pass
        out.append(entry)
    return out


@router.websocket("/ws/chat/{agent_id}")
async def websocket_chat(
    websocket: WebSocket,
    agent_id: uuid.UUID,
    token: str = Query(...),
    session_id: str = Query(None),
    lang: str = Query("en"),
):
    """WebSocket endpoint for real-time chat with an agent.

    Flow:
    1. Client connects with JWT token + optional session_id as query params
    2. Server accepts immediately so browser onopen fires quickly
    3. Server authenticates and checks agent access
    4. If session_id provided, uses it; otherwise finds/creates the user's latest session
    5. Client sends messages as JSON: {"content": "..."}
    6. Server calls the agent's configured LLM and sends response back
    7. Messages are persisted to chat_messages table under the session
    """
    # Accept immediately so browser sees onopen without waiting for DB setup
    await websocket.accept()

    # Authenticate
    try:
        payload = decode_access_token(token)
        user_id = uuid.UUID(payload["sub"])
    except Exception:
        await websocket.send_json({"type": "error", "content": "Authentication failed"})
        await websocket.close(code=4001)
        return

    # Verify access and load agent + model
    agent_name = ""
    agent_type = ""  # Track agent type for OpenClaw routing
    role_description = ""
    welcome_message = ""
    llm_model = None
    fallback_llm_model = None
    history_messages = []

    try:
        async with async_session() as db:
            logger.info(f"[WS] Looking up user {user_id}")
            result = await db.execute(select(User).where(User.id == user_id))
            user = result.scalar_one_or_none()
            if not user:
                logger.info("[WS] User not found")
                await websocket.send_json({"type": "error", "content": "User not found"})
                await websocket.close(code=4001)
                return

            logger.info(f"[WS] Checking agent access for {agent_id}")
            agent, _ = await check_agent_access(db, user, agent_id)
            # Check agent expiry
            if is_agent_expired(agent):
                await websocket.send_json({"type": "error", "content": "This Agent has expired and is off duty. Please contact your admin to extend its service."})
                await websocket.close(code=4003)
                return
            agent_name = agent.name
            agent_type = agent.agent_type or ""
            role_description = agent.role_description or ""
            welcome_message = agent.welcome_message or ""
            ctx_size = agent.context_window_size or 100
            # Captured for onboarding lookups — the DB-bound `agent` goes out
            # of scope when this session block closes.
            agent_snapshot = agent
            user_display_name = (user.display_name or "").strip() or "there"
            logger.info(f"[WS] Agent: {agent_name}, type: {agent_type}, model_id: {agent.primary_model_id}, ctx: {ctx_size}")

            # Load the agent's primary model
            if agent.primary_model_id:
                model_result = await db.execute(
                    select(LLMModel).where(LLMModel.id == agent.primary_model_id)
                )
                llm_model = model_result.scalar_one_or_none()
                if llm_model and not llm_model.enabled:
                    logger.info(f"[WS] Primary model {llm_model.model} is disabled, skipping")
                    llm_model = None
                else:
                    logger.info(f"[WS] Primary model loaded: {llm_model.model if llm_model else 'None'}")

            # Load fallback model
            if agent.fallback_model_id:
                fb_result = await db.execute(
                    select(LLMModel).where(LLMModel.id == agent.fallback_model_id)
                )
                fallback_llm_model = fb_result.scalar_one_or_none()
                if fallback_llm_model and not fallback_llm_model.enabled:
                    logger.info(f"[WS] Fallback model {fallback_llm_model.model} is disabled, skipping")
                    fallback_llm_model = None
                elif fallback_llm_model:
                    logger.info(f"[WS] Fallback model loaded: {fallback_llm_model.model}")

            # Config-level fallback: primary missing -> use fallback
            if not llm_model and fallback_llm_model:
                llm_model = fallback_llm_model
                fallback_llm_model = None  # No further fallback available
                logger.info(f"[WS] Primary model unavailable, using fallback: {llm_model.model}")

            # Resolve or create chat session
            from app.models.chat_session import ChatSession
            from sqlalchemy import select as _sel
            from datetime import datetime as _dt, timezone as _tz
            conv_id = session_id
            if conv_id:
                # Validate the session belongs to this agent and to this user.
                try:
                    _sid = uuid.UUID(conv_id)
                except (ValueError, TypeError):
                    conv_id = None
                    _existing = None
                else:
                    _sr = await db.execute(
                        _sel(ChatSession).where(
                            ChatSession.id == _sid,
                            ChatSession.agent_id == agent_id,
                        )
                    )
                    _existing = _sr.scalar_one_or_none()
                    if not _existing:
                        conv_id = None
                    elif _existing.source_channel != "agent" and str(_existing.user_id) != str(user_id):
                        await websocket.send_json({"type": "error", "content": "Not authorized for this session"})
                        await websocket.close(code=4003)
                        return
            if not conv_id:
                # Prefer the user's designated primary platform session. This keeps agent-initiated
                # conversations and ongoing long-form context anchored in one stable thread, while
                # user-created side sessions remain temporary.
                _sr = await db.execute(
                    _sel(ChatSession)
                    .where(
                        ChatSession.agent_id == agent_id,
                        ChatSession.user_id == user_id,
                        ChatSession.source_channel == "web",
                        ChatSession.is_group == False,
                        ChatSession.is_primary == True,
                    )
                    .order_by(ChatSession.last_message_at.desc().nulls_last(), ChatSession.created_at.desc())
                    .limit(1)
                )
                _latest = _sr.scalar_one_or_none()
                if _latest:
                    conv_id = str(_latest.id)
                else:
                    # Lazily elect or create the primary session only when it is actually needed.
                    _new_session = await ensure_primary_platform_session(db, agent_id, user_id)
                    await db.commit()
                    await db.refresh(_new_session)
                    conv_id = str(_new_session.id)
                    logger.info(f"[WS] Selected primary session {conv_id}")

            try:
                history_result = await db.execute(
                    select(ChatMessage)
                    .where(ChatMessage.agent_id == agent_id, ChatMessage.conversation_id == conv_id)
                    .order_by(ChatMessage.created_at.desc())
                    .limit(ctx_size)
                )
                history_messages = list(reversed(history_result.scalars().all()))
                logger.info(f"[WS] Loaded {len(history_messages)} history messages for session {conv_id}")
            except Exception as e:
                logger.warning(f"[WS] History load failed (non-fatal): {e}")
    except Exception as e:
        logger.error(f"[WS] Setup error: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()
        await websocket.send_json({"type": "error", "content": "Setup failed"})
        await websocket.close(code=4002)  # Config error — client should NOT retry
        return

    agent_id_str = str(agent_id)
    if agent_id_str not in manager.active_connections:
        manager.active_connections[agent_id_str] = []
    manager.active_connections[agent_id_str].append((websocket, conv_id, str(user_id)))
    logger.info(f"[WS] Ready! Agent={agent_name}")

    # Send session_id to frontend so Take Control can reference the correct session.
    await websocket.send_json({"type": "connected", "session_id": conv_id})

    # Build conversation context from history
    conversation: list[dict] = []
    for msg in history_messages:
        if msg.role == "tool_call":
            # Convert stored tool_call JSON into OpenAI-format assistant+tool pair
            try:
                import json as _j_hist
                tc_data = _j_hist.loads(msg.content)
                tc_name = tc_data.get("name", "unknown")
                tc_args = tc_data.get("args", {})
                tc_result = tc_data.get("result", "")
                tc_id = f"call_{msg.id}"  # synthetic tool_call_id
                # Assistant message with tool_calls array
                asst_msg = {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [{
                        "id": tc_id,
                        "type": "function",
                        "function": {"name": tc_name, "arguments": _j_hist.dumps(tc_args, ensure_ascii=False)},
                    }],
                }
                if tc_data.get("reasoning_content"):
                    asst_msg["reasoning_content"] = tc_data["reasoning_content"]
                conversation.append(asst_msg)
                # Tool result message.
                from app.services.vision_inject import sanitize_history_tool_result
                sanitized_result = sanitize_history_tool_result(str(tc_result))
                conversation.append({
                    "role": "tool",
                    "tool_call_id": tc_id,
                    "content": sanitized_result[:500],
                })
            except Exception:
                continue  # Skip malformed tool_call records
        else:
            entry = {"role": msg.role, "content": msg.content}
            if hasattr(msg, 'thinking') and msg.thinking:
                entry["thinking"] = msg.thinking
            conversation.append(entry)

    try:
        # Send welcome message on new session (no history)
        if welcome_message and not history_messages:
            await websocket.send_json({"type": "done", "role": "assistant", "content": welcome_message})

        while True:
            logger.info(f"[WS] Waiting for message from {agent_name}...")
            data = await websocket.receive_json()

            # Set a unique trace ID for this specific message processing.
            from app.core.logging_config import set_trace_id
            import uuid as _trace_uuid
            trace_id = str(_trace_uuid.uuid4())[:12]
            set_trace_id(trace_id)

            content = data.get("content", "")
            display_content = data.get("display_content", "")  # User-facing display text
            file_name = data.get("file_name", "")  # Original file name for attachment display
            override_model_id = data.get("model_id")  # Optional per-turn model switcher
            # When the frontend fires an onboarding trigger for a (user, agent)
            # pair that hasn't met before, it tags the message so the server can
            # (a) skip persisting a user-side turn and (b) not echo any user
            # bubble — the agent opens the conversation itself.
            is_onboarding_trigger = data.get("kind") == "onboarding_trigger"
            logger.info(f"[WS] Received: {content[:50]}" + (" [onboarding]" if is_onboarding_trigger else ""))

            if not content and not is_onboarding_trigger:
                continue
            if is_onboarding_trigger:
                # Guard against stale triggers. A frontend with a cached
                # agent query from before the ritual completed can fire an
                # onboarding_trigger on a new session even though the pair
                # is already locked. In that case the resolver would return
                # no prompt, but the placeholder "Please begin the
                # onboarding" would still reach the LLM and the agent would
                # dutifully restart the ritual. Short-circuit here, emit an
                # event so the frontend refreshes its cache, and move on.
                from app.services.onboarding import is_onboarded as _is_onboarded
                async with async_session() as _gdb:
                    if await _is_onboarded(_gdb, agent_id, user_id):
                        logger.info("[WS] Onboarding trigger ignored — pair already onboarded")
                        await websocket.send_json({
                            "type": "onboarded",
                            "agent_id": str(agent_id),
                        })
                        continue
                # Minimal placeholder so the LLM has a valid user turn to anchor
                # its greeting. The onboarding system prompt is what actually
                # drives the reply; this text is never shown or saved.
                content = "Please begin the onboarding."

            # Per-message model override — the chat dropdown lets users pick a
            # different tenant-scoped model for this session. Override only the
            # current turn; nothing is persisted, and it resets when Chat.tsx
            # remounts.
            effective_llm_model = llm_model
            if override_model_id:
                try:
                    _ovr_uuid = uuid.UUID(str(override_model_id))
                    async with async_session() as _mdb:
                        _mr = await _mdb.execute(select(LLMModel).where(LLMModel.id == _ovr_uuid))
                        _ovr = _mr.scalar_one_or_none()
                        if _ovr and _ovr.enabled and _ovr.tenant_id and (
                            not llm_model or _ovr.tenant_id == llm_model.tenant_id
                        ):
                            effective_llm_model = _ovr
                        else:
                            logger.warning(f"[WS] model override {override_model_id} rejected (missing/disabled/tenant mismatch)")
                except (ValueError, TypeError):
                    logger.warning(f"[WS] model override {override_model_id!r} is not a valid UUID")

            # ── Quota checks ──
            try:
                from app.services.quota_guard import (
                    check_conversation_quota, increment_conversation_usage,
                    check_agent_expired, check_agent_llm_quota, increment_agent_llm_usage,
                    QuotaExceeded, AgentExpired,
                )
                await check_conversation_quota(user_id)
                await check_agent_expired(agent_id)
            except QuotaExceeded as qe:
                await websocket.send_json({"type": "done", "role": "assistant", "content": f"⚠️ {qe.message}"})
                continue
            except AgentExpired as ae:
                await websocket.send_json({"type": "done", "role": "assistant", "content": f"⚠️ {ae.message}"})
                continue

            # Add user message to conversation (full LLM context)
            conversation.append({"role": "user", "content": content})

            # Save user message to DB.
            #
            # Bootstrap trigger: the user never sent anything — the frontend
            # fired a synthetic turn so the agent could greet first. Don't
            # persist and don't title the session from it.
            #
            # If the LLM content contains [image_data:...] markers, persist the full
            # payload so subsequent turns can still forward the image to the model.
            has_image_marker = "[image_data:" in content
            if has_image_marker:
                saved_content = f"[file:{file_name}]\n{content}" if file_name else content
            else:
                saved_content = display_content if display_content else content
                if file_name:
                    saved_content = f"[file:{file_name}]\n{saved_content}"
            if is_onboarding_trigger:
                logger.info("[WS] Onboarding trigger — skipping user-message persistence")
                # Title this session "Onboarding" up front so it's identifiable
                # in the session list even before the user has typed anything.
                # The auto-title logic in the normal path only overwrites titles
                # that start with "Session ", so this stays sticky.
                async with async_session() as _sdb:
                    from app.models.chat_session import ChatSession as _CS
                    _sr = await _sdb.execute(
                        select(_CS).where(_CS.id == uuid.UUID(conv_id))
                    )
                    _s = _sr.scalar_one_or_none()
                    if _s and _s.title.startswith("Session "):
                        _s.title = "Onboarding"
                        await _sdb.commit()
            else:
                async with async_session() as db:
                    user_msg = ChatMessage(
                        agent_id=agent_id,
                        user_id=user_id,
                        role="user",
                        content=saved_content,
                        conversation_id=conv_id,
                    )
                    db.add(user_msg)
                    # Update session last_message_at + auto-title on first message
                    from app.models.chat_session import ChatSession as _CS
                    from datetime import datetime as _dt2, timezone as _tz2
                    _now = _dt2.now(_tz2.utc)
                    _sess_r = await db.execute(
                        select(_CS).where(_CS.id == uuid.UUID(conv_id))
                    )
                    _sess = _sess_r.scalar_one_or_none()
                    if _sess:
                        _sess.last_message_at = _now
                        if not history_messages and _sess.title.startswith("Session "):
                            # Use display_content for title (avoids raw base64/markers)
                            title_src = display_content if display_content else content
                            # Clean up common prefixes from image/file messages
                            clean_title = title_src.replace("[图片] ", "📷 ").replace("[image_data:", "").strip()
                            if file_name and not clean_title:
                                clean_title = f"📎 {file_name}"
                            _sess.title = clean_title[:40] if clean_title else content[:40]
                    await db.commit()
                logger.info("[WS] User message saved")

            # ── OpenClaw routing: insert into gateway_messages instead of LLM ──
            if agent_type == "openclaw":
                from app.models.gateway_message import GatewayMessage as GwMsg
                async with async_session() as db:
                    gw_msg = GwMsg(
                        agent_id=agent_id,
                        sender_user_id=user_id,
                        conversation_id=conv_id,
                        content=content,
                        status="pending",
                    )
                    db.add(gw_msg)
                    await db.commit()
                logger.info("[WS] OpenClaw: message queued for gateway poll")
                await websocket.send_json({
                    "type": "done",
                    "role": "assistant",
                    "content": "Message forwarded to OpenClaw agent. Waiting for response..."
                })
                continue

            # Detect task creation intent
            import re
            task_match = re.search(
                r'(?:创建|新建|添加|建一个|帮我建|create|add)(?:一个|a )?(?:任务|待办|todo|task)[，,：：:\\s]*(.+)',
                content, re.IGNORECASE
            )

            # Track thinking content for storage (initialize before condition)
            thinking_content = []

            # Reload model config on every message so Settings changes take effect
            # immediately without requiring a page refresh / WebSocket reconnect.
            async with async_session() as _mdb:
                _agent_r = await _mdb.execute(select(Agent).where(Agent.id == agent_id))
                _agent_cur = _agent_r.scalar_one_or_none()
                if _agent_cur:
                    if _agent_cur.primary_model_id:
                        _m_r = await _mdb.execute(select(LLMModel).where(LLMModel.id == _agent_cur.primary_model_id))
                        _m = _m_r.scalar_one_or_none()
                        llm_model = _m if (_m and _m.enabled) else None
                    else:
                        llm_model = None
                    if _agent_cur.fallback_model_id:
                        _fb_r = await _mdb.execute(select(LLMModel).where(LLMModel.id == _agent_cur.fallback_model_id))
                        _fb = _fb_r.scalar_one_or_none()
                        fallback_llm_model = _fb if (_fb and _fb.enabled) else None
                    else:
                        fallback_llm_model = None
                    # Config-level fallback: primary missing → use fallback immediately
                    if not llm_model and fallback_llm_model:
                        llm_model = fallback_llm_model
                        fallback_llm_model = None

            # Call LLM with streaming
            if effective_llm_model:
                try:
                    logger.info(f"[WS] Calling LLM {effective_llm_model.model} (streaming)...")
                    
                    # Accumulate partial content for abort handling
                    partial_chunks: list[str] = []

                    # Flipped to True inside _call_with_failover when an
                    # onboarding prompt was injected for this turn. The first
                    # streamed chunk then commits the junction-table row so
                    # future sessions see this user as already onboarded, even
                    # if they disconnect before the greeting finishes.
                    needs_onboarding_mark = False
                    onboarding_mark_done = False

                    async def stream_to_ws(text: str):
                        """Send each chunk to client in real-time."""
                        nonlocal onboarding_mark_done
                        partial_chunks.append(text)
                        await websocket.send_json({"type": "chunk", "content": text})
                        if needs_onboarding_mark and not onboarding_mark_done:
                            onboarding_mark_done = True
                            try:
                                from app.services.onboarding import mark_onboarded
                                async with async_session() as _ob_db:
                                    await mark_onboarded(_ob_db, agent_id, user_id)
                                # Tell the frontend to refresh its cached agent
                                # record so subsequent sessions (or other open
                                # tabs) see onboarded_for_me=true and skip the
                                # kickoff effect.
                                await websocket.send_json({
                                    "type": "onboarded",
                                    "agent_id": str(agent_id),
                                })
                            except Exception as _ob_err:
                                logger.warning(f"[WS] mark_onboarded failed (non-fatal): {_ob_err}")
                    
                    async def tool_call_to_ws(data: dict):
                        """Send tool call info to client and persist completed ones."""
                        if data.get("status") == "done":
                            try:
                                from app.services.agentbay_live import detect_agentbay_env, get_desktop_screenshot, get_browser_snapshot

                                tool_name = data.get("name", "")
                                env = detect_agentbay_env(tool_name)
                                if env == "desktop":
                                    b64_url = await get_desktop_screenshot(agent_id, session_id=conv_id)
                                    if b64_url:
                                        data["live_preview"] = {"env": env, "screenshot_url": b64_url}
                                        logger.info(f"[WS][LivePreview] Embedded {env} base64 in tool_call")
                                elif env == "browser":
                                    b64_url = await get_browser_snapshot(agent_id, session_id=conv_id)
                                    if b64_url:
                                        data["live_preview"] = {"env": env, "screenshot_url": b64_url}
                                        logger.info(f"[WS][LivePreview] Embedded {env} base64 in tool_call")
                                elif env == "code":
                                    tool_result = data.get("result", "") or ""
                                    data["live_preview"] = {"env": "code", "output": tool_result[:5000]}
                            except Exception as _lp_err:
                                logger.warning(f"[WS][LivePreview] Embed failed: {_lp_err}")

                            # Attach workspace_activity so the frontend WorkspaceOperationPanel
                            # auto-opens when the agent writes, edits, or converts a file.
                            # PR #419 added the frontend logic but the backend never emitted
                            # this event — this is the missing piece.
                            _WORKSPACE_TOOL_ACTIONS: dict[str, str] = {
                                "write_file": "write",
                                "edit_file": "edit",
                                "delete_file": "delete",
                                "convert_markdown_to_docx": "convert",
                                "convert_csv_to_xlsx": "convert",
                                "convert_markdown_to_pdf": "convert",
                                "convert_html_to_pdf": "convert",
                                "convert_html_to_pptx": "convert",
                            }
                            _done_tool_name = data.get("name", "")
                            if _done_tool_name in _WORKSPACE_TOOL_ACTIONS:
                                _ws_args = data.get("args") or {}
                                if isinstance(_ws_args, str):
                                    try:
                                        import json as _json_wsa
                                        _ws_args = _json_wsa.loads(_ws_args)
                                    except Exception:
                                        _ws_args = {}
                                _ws_path = _ws_args.get("output_path") or _ws_args.get("path", "")
                                _ws_result = str(data.get("result") or "")
                                _pending_approval = "requires approval" in _ws_result.lower()
                                data["workspace_activity"] = {
                                    "action": _WORKSPACE_TOOL_ACTIONS[_done_tool_name],
                                    "path": _ws_path,
                                    "tool": _done_tool_name,
                                    "ok": not _pending_approval,
                                    "pendingApproval": _pending_approval,
                                }
                                logger.info(f"[WS][Workspace] activity: {_done_tool_name} → {_ws_path}")

                        await websocket.send_json({"type": "tool_call", **data})
                        # Save completed tool calls to DB so they persist in chat history
                        if data.get("status") == "done":
                            try:
                                import json as _json_tc
                                async with async_session() as _tc_db:
                                    tc_msg = ChatMessage(
                                        agent_id=agent_id,
                                        user_id=user_id,
                                        role="tool_call",
                                        content=_json_tc.dumps({
                                            "name": data.get("name", ""),
                                            "args": data.get("args"),
                                            "status": "done",
                                            "result": (data.get("result") or "")[:500],
                                            "reasoning_content": data.get("reasoning_content"),
                                        }),
                                        conversation_id=conv_id,
                                    )
                                    _tc_db.add(tc_msg)
                                    await maybe_mark_session_read_for_active_viewer(
                                        _tc_db,
                                        agent_id=agent_id,
                                        session_id=conv_id,
                                        user_id=user_id,
                                    )
                                    await _tc_db.commit()
                            except Exception as _tc_err:
                                logger.warning(f"[WS] Failed to save tool_call: {_tc_err}")
                    
                    # Track thinking content for storage
                    thinking_content = []
                    
                    async def thinking_to_ws(text: str):
                        """Send thinking chunks to client for collapsible display."""
                        thinking_content.append(text)
                        await websocket.send_json({"type": "thinking", "content": text})

                    _workspace_draft_cache: dict[str, str] = {}

                    async def tool_delta_to_ws(data: dict):
                        """Stream workspace file-operation drafts while tool args are still arriving."""
                        tool_name = data.get("name", "")
                        if tool_name not in {
                            "write_file",
                            "edit_file",
                            "delete_file",
                            "convert_markdown_to_docx",
                            "convert_csv_to_xlsx",
                            "convert_markdown_to_pdf",
                            "convert_html_to_pdf",
                            "convert_html_to_pptx",
                        }:
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

                        await websocket.send_json(
                            {
                                "type": "workspace_draft",
                                "id": draft_id,
                                "index": data.get("index", 0),
                                "name": tool_name,
                                "arguments": raw_args,
                            }
                        )

                    import asyncio as _aio

                    # Run call_llm_with_failover as a cancellable task
                    async def _call_with_failover():
                        nonlocal needs_onboarding_mark

                        async def _on_failover(reason: str):
                            await websocket.send_json({"type": "info", "content": f"Primary model error, {reason}"})

                        # To prevent tool call message pairs(assistant + tool) from being broken down.
                        _truncated = conversation[-ctx_size:]
                        while _truncated and _truncated[0].get("role") == "tool":
                            _truncated.pop(0)

                        # Per-(user, agent) onboarding: if the junction table
                        # has no row for this pair yet, prepend a system prompt.
                        # The prompt is turn-aware — on the greeting turn it
                        # tells the agent to greet + ask one question; on the
                        # deliverable turn it tells the agent to drop question
                        # mode and immediately produce a concrete output. The
                        # junction row is only committed on the deliverable
                        # turn (see lock_on_first_chunk below), so the full
                        # two-step ritual stays guarded.
                        from app.services.onboarding import resolve_onboarding_prompt
                        skip_tools_for_greeting = False
                        try:
                            async with async_session() as _ob_db:
                                _onb = await resolve_onboarding_prompt(
                                    _ob_db, agent_snapshot, user_id,
                                    user_name=user_display_name,
                                    user_locale=lang,
                                )
                            if _onb:
                                _truncated = [{"role": "system", "content": _onb.prompt}] + _truncated
                                if _onb.lock_on_first_chunk:
                                    needs_onboarding_mark = True
                                # Greeting turn produces a templated reply that
                                # never calls tools, so suppress the tool list
                                # to cut prompt size by ~50% and improve TTFT.
                                if _onb.is_greeting_turn:
                                    skip_tools_for_greeting = True
                        except Exception as _onb_err:
                            logger.warning(f"[WS] Onboarding prompt resolve failed (non-fatal): {_onb_err}")

                        return await call_llm_with_failover(
                            primary_model=effective_llm_model,
                            fallback_model=fallback_llm_model,
                            messages=_truncated,
                            agent_name=agent_name,
                            role_description=role_description,
                            agent_id=agent_id,
                            user_id=user_id,
                            session_id=conv_id,
                            on_chunk=stream_to_ws,
                            on_tool_call=tool_call_to_ws,
                            on_tool_delta=tool_delta_to_ws,
                            on_thinking=thinking_to_ws,
                            supports_vision=getattr(effective_llm_model, 'supports_vision', False),
                            on_failover=_on_failover,
                            skip_tools=skip_tools_for_greeting,
                        )

                    llm_task = _aio.create_task(_call_with_failover())

                    # Listen for abort while LLM is running
                    aborted = False
                    queued_messages: list[dict] = []
                    while not llm_task.done():
                        try:
                            msg = await _aio.wait_for(
                                websocket.receive_json(), timeout=0.5
                            )
                            if msg.get("type") == "abort":
                                logger.info(f"[WS] Abort received, cancelling LLM task")
                                llm_task.cancel()
                                aborted = True
                                break
                            else:
                                # Queue non-abort messages for later
                                queued_messages.append(msg)
                        except _aio.TimeoutError:
                            continue
                        except WebSocketDisconnect:
                            llm_task.cancel()
                            raise

                    if aborted:
                        # Wait for task to finish cancelling
                        try:
                            await llm_task
                        except (_aio.CancelledError, Exception):
                            pass
                        partial_text = "".join(partial_chunks).strip()
                        if partial_text:
                            assistant_response = partial_text + "\n\n*[Generation stopped]*"
                        else:
                            assistant_response = "*[Generation stopped]*"
                        logger.info(f"[WS] LLM aborted, partial: {assistant_response[:80]}")
                    else:
                        assistant_response = await llm_task
                        logger.info(f"[WS] LLM response: {assistant_response[:80]}")

                    # call_llm returns error strings instead of raising — detect and
                    # re-raise so the fallback model logic below can trigger correctly.
                    _LLM_ERROR_PREFIXES = ("[LLM Error]", "[LLM call error]", "[Error]")
                    if not aborted and assistant_response and any(
                        assistant_response.startswith(p) for p in _LLM_ERROR_PREFIXES
                    ):
                        raise RuntimeError(assistant_response)

                    # Update last_active_at. The onboarding lock is handled
                    # earlier in stream_to_ws on the first streamed chunk, so
                    # there's nothing to reconcile here anymore.
                    from datetime import datetime, timezone as tz
                    async with async_session() as _db:
                        from app.models.agent import Agent as AgentModel
                        _ar = await _db.execute(select(AgentModel).where(AgentModel.id == agent_id))
                        _agent = _ar.scalar_one_or_none()
                        if _agent:
                            _agent.last_active_at = datetime.now(tz.utc)
                            await _db.commit()

                    # Increment quota usage
                    try:
                        await increment_conversation_usage(user_id)
                        await increment_agent_llm_usage(agent_id)
                    except Exception:
                        pass

                    # Log activity
                    from app.services.activity_logger import log_activity
                    await log_activity(agent_id, "chat_reply", f"Replied to web chat: {assistant_response[:80]}", detail={"channel": "web", "user_text": content[:200], "reply": assistant_response[:500]})
                except WebSocketDisconnect:
                    raise
                except Exception as e:
                    logger.error(f"[WS] LLM error: {e}")
                    import traceback
                    traceback.print_exc()
                    assistant_response = f"[LLM call error] {str(e)[:200]}"
            else:
                assistant_response = f"⚠️ {agent_name} has no LLM model configured. Please select a model in the agent's Settings tab."

            # If task creation detected, create a real Task record
            if task_match:
                task_title = task_match.group(1).strip()
                if task_title:
                    try:
                        from app.models.task import Task
                        from app.services.task_executor import execute_task
                        import asyncio as _asyncio
                        async with async_session() as db:
                            task = Task(
                                agent_id=agent_id,
                                title=task_title,
                                created_by=user_id,
                                status="pending",
                                priority="medium",
                            )
                            db.add(task)
                            await db.commit()
                            await db.refresh(task)
                            logger.info(f"[WS] Task created: {task.id}")
                            # Trigger background execution
                            task_id = task.id
                        _asyncio.create_task(execute_task(task_id, agent_id))
                        assistant_response += f"\n\n📋 Task synced to task board: [{task_title}]"
                    except Exception as te:
                        logger.error(f"[WS] Task creation failed: {te}")

            # Add assistant response to in-memory conversation for subsequent turns.
            conversation.append({"role": "assistant", "content": assistant_response})

            # Save assistant reply
            async with async_session() as db:
                assistant_msg = ChatMessage(
                    agent_id=agent_id,
                    user_id=user_id,
                    role="assistant",
                    content=assistant_response,
                    conversation_id=conv_id,
                    thinking="".join(thinking_content) if thinking_content else None,
                )
                db.add(assistant_msg)
                await maybe_mark_session_read_for_active_viewer(
                    db,
                    agent_id=agent_id,
                    session_id=conv_id,
                    user_id=user_id,
                )
                await db.commit()
            logger.info("[WS] Assistant message saved")

            # Final 'done' packet
            await websocket.send_json({"type": "done", "role": "assistant", "content": assistant_response})

            # Re-process any queued messages (if user sent something during generation)
            for qm in queued_messages:
                # In a real implementation, you might want to push these back to the main loop
                pass

    except WebSocketDisconnect:
        logger.info(f"[WS] Client disconnected: {user_id}")
        manager.disconnect(str(agent_id), websocket)
    except Exception as e:
        logger.error(f"[WS] Unexpected error: {e}")
        import traceback
        traceback.print_exc()
        manager.disconnect(str(agent_id), websocket)
