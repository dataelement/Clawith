"""WebSocket chat endpoint for real-time agent conversations."""

import json
import uuid

import httpx
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.security import decode_access_token
from app.core.permissions import check_agent_access
from app.database import async_session
from app.models.agent import Agent
from app.models.audit import ChatMessage
from app.models.llm import LLMModel
from app.models.user import User

router = APIRouter(tags=["websocket"])


class ConnectionManager:
    """Manage WebSocket connections per agent."""

    def __init__(self):
        self.active_connections: dict[str, list[WebSocket]] = {}

    async def connect(self, agent_id: str, websocket: WebSocket):
        await websocket.accept()
        if agent_id not in self.active_connections:
            self.active_connections[agent_id] = []
        self.active_connections[agent_id].append(websocket)

    def disconnect(self, agent_id: str, websocket: WebSocket):
        if agent_id in self.active_connections:
            self.active_connections[agent_id].remove(websocket)

    async def send_message(self, agent_id: str, message: dict):
        if agent_id in self.active_connections:
            for ws in self.active_connections[agent_id]:
                await ws.send_json(message)


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
        entry: dict = {"role": m.role, "content": m.content}
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


async def call_llm(
    model: LLMModel,
    messages: list[dict],
    agent_name: str,
    role_description: str,
    agent_id=None,
    user_id=None,
    on_chunk=None,
    on_tool_call=None,
) -> str:
    """Call LLM via OpenAI-compatible API with function-calling tool loop.
    
    Args:
        on_chunk: Optional async callback(text: str) for streaming chunks to client.
    """
    import json as _json
    import httpx
    from app.services.agent_tools import AGENT_TOOLS, execute_tool, get_agent_tools_for_llm

    # ── Token limit check & config ──
    _max_tool_rounds = 50  # default
    if agent_id:
        try:
            from app.models.agent import Agent as AgentModel
            async with async_session() as _db:
                _ar = await _db.execute(select(AgentModel).where(AgentModel.id == agent_id))
                _agent = _ar.scalar_one_or_none()
                if _agent:
                    _max_tool_rounds = _agent.max_tool_rounds or 50
                    if _agent.max_tokens_per_day and _agent.tokens_used_today >= _agent.max_tokens_per_day:
                        return f"⚠️ Daily token usage has reached the limit ({_agent.tokens_used_today:,}/{_agent.max_tokens_per_day:,}). Please try again tomorrow or ask admin to increase the limit."
                    if _agent.max_tokens_per_month and _agent.tokens_used_month >= _agent.max_tokens_per_month:
                        return f"⚠️ Monthly token usage has reached the limit ({_agent.tokens_used_month:,}/{_agent.max_tokens_per_month:,}). Please ask admin to increase the limit."
        except Exception:
            pass

    # Build rich prompt with soul, memory, skills, relationships
    from app.services.agent_context import build_agent_context
    system_prompt = await build_agent_context(agent_id, agent_name, role_description)

    # Load tools dynamically from DB
    tools_for_llm = await get_agent_tools_for_llm(agent_id) if agent_id else AGENT_TOOLS

    api_messages = [{"role": "system", "content": system_prompt}]
    api_messages.extend(messages)

    # Determine base URL
    from app.services.llm_utils import get_provider_base_url, get_tool_params
    base_url = get_provider_base_url(model.provider, model.base_url)

    if not base_url:
        return f"[Error] No API endpoint configured for {model.provider}"

    url_base = base_url.rstrip('/')
    if url_base.endswith('/chat/completions'):
        url = url_base
    else:
        url = f"{url_base}/chat/completions"
    api_key = model.api_key_encrypted
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"}

    # Tool-calling loop (configurable per agent, default 50)
    for round_i in range(_max_tool_rounds):
        payload = {
            "model": model.model,
            "messages": api_messages,
            "temperature": 0.7,
            "max_tokens": 16384,  # Large enough for tool calls with big content args
            "tools": tools_for_llm,
            "stream": True,
            **get_tool_params(model.provider),
        }

        # Stream the response (with retry for connection errors)
        full_content = ""
        tool_calls_data = []  # accumulate tool calls from stream
        last_finish_reason = None
        _max_retries = 3
        for _attempt in range(_max_retries):
            try:
                async with httpx.AsyncClient(timeout=120) as client:
                    async with client.stream("POST", url, json=payload, headers=headers) as resp:
                        # Debug: log HTTP status
                        print(f"[LLM-DBG] HTTP status={resp.status_code}, url={url}", flush=True)
                        if resp.status_code >= 400:
                            error_body = ""
                            async for chunk in resp.aiter_bytes():
                                error_body += chunk.decode(errors="replace")
                            print(f"[LLM-DBG] Error body: {error_body[:500]}", flush=True)
                            return f"[LLM Error] HTTP {resp.status_code}: {error_body[:200]}"
                        line_count = 0
                        async for line in resp.aiter_lines():
                            line_count += 1
                            if line_count <= 3:
                                print(f"[LLM-DBG] line#{line_count}: {line[:200]}", flush=True)
                            if not line.startswith("data: "):
                                continue
                            data_str = line[6:]
                            if data_str.strip() == "[DONE]":
                                break
                            try:
                                chunk = _json.loads(data_str)
                            except _json.JSONDecodeError:
                                continue

                            if "error" in chunk:
                                return f"[LLM Error] {chunk['error'].get('message', str(chunk['error']))[:200]}"

                            choices = chunk.get("choices", [])
                            if not choices:
                                continue
                            choice = choices[0]
                            delta = choice.get("delta", {})
                            fr = choice.get("finish_reason")
                            if fr:
                                last_finish_reason = fr

                            # Debug: log first few chunks to diagnose empty content
                            if len(full_content) == 0 and not tool_calls_data:
                                print(f"[LLM-DBG] chunk: delta={delta}, finish={fr}", flush=True)

                            # Text content
                            if delta.get("content"):
                                text = delta["content"]
                                full_content += text
                                if on_chunk:
                                    try:
                                        await on_chunk(text)
                                    except Exception:
                                        pass

                            # Tool calls (accumulate across streaming chunks)
                            if delta.get("tool_calls"):
                                for tc_delta in delta["tool_calls"]:
                                    idx = tc_delta.get("index", 0)
                                    while len(tool_calls_data) <= idx:
                                        tool_calls_data.append({"id": "", "function": {"name": "", "arguments": ""}})
                                    tc = tool_calls_data[idx]
                                    if tc_delta.get("id"):
                                        tc["id"] = tc_delta["id"]
                                    fn_delta = tc_delta.get("function") or {}
                                    if fn_delta.get("name"):
                                        tc["function"]["name"] += fn_delta["name"]
                                    # Accumulate arguments — must use 'is not None' since '' is valid
                                    arg_chunk = fn_delta.get("arguments")
                                    if arg_chunk is not None:
                                        if isinstance(arg_chunk, dict):
                                            # Some providers send pre-parsed JSON
                                            import json as _j2
                                            tc["function"]["arguments"] = _j2.dumps(arg_chunk, ensure_ascii=False)
                                        else:
                                            tc["function"]["arguments"] += str(arg_chunk)
                                    # Also check for 'input' field (Anthropic/Bedrock native format)
                                    if "input" in fn_delta:
                                        inp = fn_delta["input"]
                                        if isinstance(inp, dict):
                                            import json as _j3
                                            tc["function"]["arguments"] = _j3.dumps(inp, ensure_ascii=False)
                                        elif isinstance(inp, str) and inp:
                                            tc["function"]["arguments"] += inp

                break  # Success — exit retry loop

            except (httpx.ConnectError, httpx.ReadError, httpx.ConnectTimeout) as e:
                if _attempt < _max_retries - 1:
                    import asyncio as _aio
                    wait = (_attempt + 1) * 1  # 1s, 2s
                    print(f"[LLM-RETRY] Attempt {_attempt+1} failed ({type(e).__name__}), retrying in {wait}s...", flush=True)
                    await _aio.sleep(wait)
                    full_content = ""
                    tool_calls_data = []
                    last_finish_reason = None
                    continue
                import traceback
                traceback.print_exc()
                return f"[LLM call error] {type(e).__name__}: Connection failed after {_max_retries} attempts"
            except Exception as e:
                import traceback
                traceback.print_exc()
                return f"[LLM call error] {type(e).__name__}: {str(e)[:200]}"

        # Detect truncation
        if last_finish_reason == "length":
            print(f"[LLM-WARN] Stream ended with finish_reason='length' — output was likely truncated by max_tokens!", flush=True)

        # If no tool calls, return final text
        if not tool_calls_data:
            # Track token usage
            if agent_id:
                try:
                    async with async_session() as _db:
                        from app.models.agent import Agent as AgentModel
                        _ar = await _db.execute(select(AgentModel).where(AgentModel.id == agent_id))
                        _agent = _ar.scalar_one_or_none()
                        if _agent:
                            total_chars = sum(len(m.get('content', '') or '') for m in api_messages) + len(full_content or '')
                            estimated_tokens = max(total_chars // 3, 1)
                            _agent.tokens_used_today = (_agent.tokens_used_today or 0) + estimated_tokens
                            _agent.tokens_used_month = (_agent.tokens_used_month or 0) + estimated_tokens
                            await _db.commit()
                except Exception:
                    pass
            return full_content or "[LLM returned empty content]"

        # Execute tool calls
        print(f"[LLM] Round {round_i+1}: {len(tool_calls_data)} tool call(s)")
        msg_with_tools = {"role": "assistant", "content": full_content or None, "tool_calls": [
            {"id": tc["id"], "type": "function", "function": tc["function"]} for tc in tool_calls_data
        ]}
        api_messages.append(msg_with_tools)

        for tc in tool_calls_data:
            fn = tc["function"]
            tool_name = fn["name"]
            raw_args = fn.get("arguments", "{}")
            print(f"[LLM] Raw arguments for {tool_name}: {repr(raw_args[:300])}", flush=True)
            try:
                args = _json.loads(raw_args) if raw_args else {}
            except (_json.JSONDecodeError, TypeError):
                args = {}

            print(f"[LLM] Calling tool: {tool_name}({args})")
            # Notify client about tool call (in-progress)
            if on_tool_call:
                try:
                    await on_tool_call({"name": tool_name, "args": args, "status": "running"})
                except Exception:
                    pass

            result = await execute_tool(
                tool_name, args,
                agent_id=agent_id,
                user_id=user_id or agent_id,
            )
            print(f"[LLM] Tool result: {result[:100]}")

            # Notify client about tool call result
            if on_tool_call:
                try:
                    await on_tool_call({"name": tool_name, "args": args, "status": "done", "result": result[:500]})
                except Exception:
                    pass

            api_messages.append({
                "role": "tool",
                "tool_call_id": tc["id"],
                "content": result,
            })

    return "[Error] Too many tool call rounds"


@router.websocket("/ws/chat/{agent_id}")
async def websocket_chat(
    websocket: WebSocket,
    agent_id: uuid.UUID,
    token: str = Query(...),
):
    """WebSocket endpoint for real-time chat with an agent.

    Flow:
    1. Client connects with JWT token as query param
    2. Server authenticates and checks agent access
    3. Client sends messages as JSON: {"content": "..."}
    4. Server calls the agent's configured LLM and sends response back
    5. Messages are persisted to chat_messages table
    """
    # Authenticate
    try:
        payload = decode_access_token(token)
        user_id = uuid.UUID(payload["sub"])
    except Exception:
        await websocket.close(code=4001, reason="Authentication failed")
        return

    # Verify access and load agent + model
    agent_name = ""
    role_description = ""
    llm_model = None
    history_messages = []

    try:
        async with async_session() as db:
            print(f"[WS] Looking up user {user_id}")
            result = await db.execute(select(User).where(User.id == user_id))
            user = result.scalar_one_or_none()
            if not user:
                print("[WS] User not found")
                await websocket.close(code=4001, reason="User not found")
                return

            print(f"[WS] Checking agent access for {agent_id}")
            agent, _ = await check_agent_access(db, user, agent_id)
            agent_name = agent.name
            role_description = agent.role_description or ""
            ctx_size = agent.context_window_size or 100
            print(f"[WS] Agent: {agent_name}, model_id: {agent.primary_model_id}, ctx: {ctx_size}")

            # Load the agent's primary model
            if agent.primary_model_id:
                model_result = await db.execute(
                    select(LLMModel).where(LLMModel.id == agent.primary_model_id)
                )
                llm_model = model_result.scalar_one_or_none()
                print(f"[WS] Model loaded: {llm_model.model if llm_model else 'None'}")

            # Load recent chat history for context (non-fatal if fails)
            conv_id = f"web_{user_id}"
            try:
                history_result = await db.execute(
                    select(ChatMessage)
                    .where(ChatMessage.agent_id == agent_id, ChatMessage.conversation_id == conv_id)
                    .order_by(ChatMessage.created_at.desc())
                    .limit(20)
                )
                history_messages = list(reversed(history_result.scalars().all()))
                print(f"[WS] Loaded {len(history_messages)} history messages")
            except Exception as e:
                print(f"[WS] History load failed (non-fatal): {e}")
    except Exception as e:
        print(f"[WS] Setup error: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()
        await websocket.close(code=1011, reason="Setup failed")
        return

    agent_id_str = str(agent_id)
    await manager.connect(agent_id_str, websocket)
    print(f"[WS] Connected! Agent={agent_name}")

    # Build conversation context from history (skip tool_call entries — LLM only accepts system/assistant/user/tool)
    conversation: list[dict] = []
    for msg in history_messages:
        if msg.role in ("tool_call",):
            continue
        conversation.append({"role": msg.role, "content": msg.content})

    try:
        while True:
            print(f"[WS] Waiting for message from {agent_name}...")
            data = await websocket.receive_json()
            content = data.get("content", "")
            print(f"[WS] Received: {content[:50]}")

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

            # Add user message to conversation
            conversation.append({"role": "user", "content": content})

            # Save user message
            async with async_session() as db:
                user_msg = ChatMessage(
                    agent_id=agent_id,
                    user_id=user_id,
                    role="user",
                    content=content,
                    conversation_id=conv_id,
                )
                db.add(user_msg)
                await db.commit()
            print("[WS] User message saved")

            # Detect task creation intent
            import re
            task_match = re.search(
                r'(?:创建|新建|添加|建一个|帮我建|create|add)(?:一个|a )?(?:任务|待办|todo|task)[，,：：:\\s]*(.+)',
                content, re.IGNORECASE
            )

            # Call LLM with streaming
            if llm_model:
                try:
                    print(f"[WS] Calling LLM {llm_model.model} (streaming)...")
                    
                    async def stream_to_ws(text: str):
                        """Send each chunk to client in real-time."""
                        await websocket.send_json({"type": "chunk", "content": text})
                    
                    async def tool_call_to_ws(data: dict):
                        """Send tool call info to client and persist completed ones."""
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
                                        }),
                                        conversation_id=conv_id,
                                    )
                                    _tc_db.add(tc_msg)
                                    await _tc_db.commit()
                            except Exception as _tc_err:
                                print(f"[WS] Failed to save tool_call: {_tc_err}")
                    
                    assistant_response = await call_llm(
                        llm_model,
                        conversation[-ctx_size:],
                        agent_name,
                        role_description,
                        agent_id=agent_id,
                        user_id=user_id,
                        on_chunk=stream_to_ws,
                        on_tool_call=tool_call_to_ws,
                    )
                    print(f"[WS] LLM response: {assistant_response[:80]}")

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
                except Exception as e:
                    print(f"[WS] LLM error: {e}")
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
                            task_id = task.id
                        _asyncio.create_task(execute_task(task_id, agent_id))
                        assistant_response += f"\n\n📋 Task synced to task board: [{task_title}]"
                        print(f"[WS] Created task: {task_title}")
                    except Exception as e:
                        print(f"[WS] Failed to create task: {e}")

            # Add assistant response to conversation
            conversation.append({"role": "assistant", "content": assistant_response})

            # Save assistant message
            async with async_session() as db:
                asst_msg = ChatMessage(
                    agent_id=agent_id,
                    user_id=user_id,
                    role="assistant",
                    content=assistant_response,
                    conversation_id=conv_id,
                )
                db.add(asst_msg)
                await db.commit()
            print("[WS] Assistant message saved")

            # Send done signal with final content (for non-streaming clients)
            await websocket.send_json({
                "type": "done",
                "role": "assistant",
                "content": assistant_response,
            })
            print("[WS] Response done sent to client")

    except WebSocketDisconnect:
        print(f"[WS] Client disconnected: {agent_name}")
        manager.disconnect(agent_id_str, websocket)
    except Exception as e:
        print(f"[WS] Error in message loop: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()
        manager.disconnect(agent_id_str, websocket)
        try:
            await websocket.close(code=1011)
        except Exception:
            pass

