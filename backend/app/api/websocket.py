"""WebSocket chat endpoint for real-time agent conversations."""

import json
import uuid

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
from app.models.llm import LLMModel
from app.models.user import User
from app.services.llm import call_llm, call_llm_with_failover

router = APIRouter(tags=["websocket"])


class ConnectionManager:
    """Manage WebSocket connections per agent."""

    def __init__(self):
        # agent_id_str -> list of (WebSocket, session_id_str | None)
        self.active_connections: dict[str, list[tuple]] = {}

    async def connect(self, agent_id: str, websocket: WebSocket, session_id: str = None):
        await websocket.accept()
        if agent_id not in self.active_connections:
            self.active_connections[agent_id] = []
        self.active_connections[agent_id].append((websocket, session_id))

    def disconnect(self, agent_id: str, websocket: WebSocket):
        if agent_id in self.active_connections:
            self.active_connections[agent_id] = [
                (ws, sid) for ws, sid in self.active_connections[agent_id] if ws != websocket
            ]

    async def send_message(self, agent_id: str, message: dict):
        if agent_id in self.active_connections:
            for ws, _sid in self.active_connections[agent_id]:
                try:
                    await ws.send_json(message)
                except Exception:
                    pass

    async def send_to_session(self, agent_id: str, session_id: str, message: dict):
        """Send message only to WebSocket connections matching the given session_id."""
        if agent_id in self.active_connections:
            for ws, sid in self.active_connections[agent_id]:
                if sid == session_id:
                    try:
                        await ws.send_json(message)
                    except Exception:
                        pass

    def get_active_session_ids(self, agent_id: str) -> list[str]:
        """Return distinct session IDs for all active WS connections of an agent."""
        if agent_id not in self.active_connections:
            return []
        return list(set(sid for _ws, sid in self.active_connections[agent_id] if sid))


manager = ConnectionManager()


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
                # Find most recent session for this user+agent
                _sr = await db.execute(
                    _sel(ChatSession)
                    .where(ChatSession.agent_id == agent_id, ChatSession.user_id == user_id)
                    .order_by(ChatSession.last_message_at.desc().nulls_last(), ChatSession.created_at.desc())
                    .limit(1)
                )
                _latest = _sr.scalar_one_or_none()
                if _latest:
                    conv_id = str(_latest.id)
                else:
                    # Create a default session
                    now = _dt.now(_tz.utc)
                    _new_session = ChatSession(
                        agent_id=agent_id, user_id=user_id,
                        title=f"Session {now.strftime('%m-%d %H:%M')}",
                        source_channel="web",
                        created_at=now,
                    )
                    db.add(_new_session)
                    await db.commit()
                    await db.refresh(_new_session)
                    conv_id = str(_new_session.id)
                    logger.info(f"[WS] Created default session {conv_id}")

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
    manager.active_connections[agent_id_str].append((websocket, conv_id))
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
            logger.info(f"[WS] Received: {content[:50]}")

            if not content:
                continue

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
            # If the LLM content contains [image_data:...] markers, persist the full
            # payload so subsequent turns can still forward the image to the model.
            has_image_marker = "[image_data:" in content
            if has_image_marker:
                saved_content = f"[file:{file_name}]\n{content}" if file_name else content
            else:
                saved_content = display_content if display_content else content
                if file_name:
                    saved_content = f"[file:{file_name}]\n{saved_content}"
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

            # Call LLM with streaming
            if llm_model:
                try:
                    logger.info(f"[WS] Calling LLM {llm_model.model} (streaming)...")
                    
                    # Accumulate partial content for abort handling
                    partial_chunks: list[str] = []
                    
                    async def stream_to_ws(text: str):
                        """Send each chunk to client in real-time."""
                        partial_chunks.append(text)
                        await websocket.send_json({"type": "chunk", "content": text})
                    
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
                                    await _tc_db.commit()
                            except Exception as _tc_err:
                                logger.warning(f"[WS] Failed to save tool_call: {_tc_err}")
                    
                    # Track thinking content for storage
                    thinking_content = []
                    
                    async def thinking_to_ws(text: str):
                        """Send thinking chunks to client for collapsible display."""
                        thinking_content.append(text)
                        await websocket.send_json({"type": "thinking", "content": text})

                    import asyncio as _aio

                    # Run call_llm_with_failover as a cancellable task
                    async def _call_with_failover():
                        async def _on_failover(reason: str):
                            await websocket.send_json({"type": "info", "content": f"Primary model error, {reason}"})

                        return await call_llm_with_failover(
                            primary_model=llm_model,
                            fallback_model=fallback_llm_model,
                            messages=conversation[-ctx_size:],
                            agent_name=agent_name,
                            role_description=role_description,
                            agent_id=agent_id,
                            user_id=user_id,
                            session_id=conv_id,
                            on_chunk=stream_to_ws,
                            on_tool_call=tool_call_to_ws,
                            on_thinking=thinking_to_ws,
                            supports_vision=getattr(llm_model, 'supports_vision', False),
                            on_failover=_on_failover,
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

                    # Update last_active_at
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
