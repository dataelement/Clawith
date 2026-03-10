"""WebSocket chat endpoint for real-time agent conversations."""

import json
import uuid

import httpx
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Query
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
                await ws.send_json(message)

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
    on_thinking=None,
    supports_vision=False,
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
    # Look up current user's display name so the agent knows who it's talking to
    _current_user_name = None
    if user_id:
        try:
            from app.models.user import User as _UserModel
            async with async_session() as _udb:
                _ur = await _udb.execute(select(_UserModel).where(_UserModel.id == user_id))
                _u = _ur.scalar_one_or_none()
                if _u:
                    _current_user_name = _u.display_name or _u.username
        except Exception:
            pass
    system_prompt = await build_agent_context(agent_id, agent_name, role_description, current_user_name=_current_user_name)

    # Load tools dynamically from DB
    tools_for_llm = await get_agent_tools_for_llm(agent_id) if agent_id else AGENT_TOOLS

    api_messages = [{"role": "system", "content": system_prompt}]
    api_messages.extend(messages)

    # ── Vision format conversion ──
    # If the model supports vision, convert image markers in user messages
    # to OpenAI Vision API format: content becomes an array of parts.
    if supports_vision:
        import re as _re_v
        for i, msg in enumerate(api_messages):
            if msg.get("role") != "user" or not isinstance(msg.get("content"), str):
                continue
            content_str = msg["content"]
            # Find [image_data:data:image/...;base64,...] markers
            pattern = r'\[image_data:(data:image/[^;]+;base64,[A-Za-z0-9+/=]+)\]'
            images = _re_v.findall(pattern, content_str)
            if not images:
                continue
            # Build content array
            text = _re_v.sub(pattern, '', content_str).strip()
            parts = []
            for img_url in images:
                parts.append({"type": "image_url", "image_url": {"url": img_url}})
            if text:
                parts.append({"type": "text", "text": text})
            api_messages[i] = {**msg, "content": parts}
    else:
        # Strip base64 image markers for non-vision models to avoid wasting tokens
        import re as _re_strip
        _img_pattern = r'\[image_data:data:image/[^;]+;base64,[A-Za-z0-9+/=]+\]'
        for i, msg in enumerate(api_messages):
            if msg.get("role") != "user" or not isinstance(msg.get("content"), str):
                continue
            if "[image_data:" in msg["content"]:
                _n_imgs = len(_re_strip.findall(_img_pattern, msg["content"]))
                cleaned = _re_strip.sub('', msg["content"]).strip()
                if _n_imgs > 0:
                    cleaned += f"\n[用户发送了 {_n_imgs} 张图片，但当前模型不支持视觉，无法查看图片内容]"
                api_messages[i] = {**msg, "content": cleaned}

    # Determine base URL
    from app.services.llm_utils import get_provider_base_url, get_tool_params, get_max_tokens
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
        # ── Dynamic tool-call limit warning (Pulse engine) ──
        # Don't tell the agent about limits at the start — only warn when approaching.
        # This prevents models from rushing to complete tasks prematurely.
        _warn_threshold_80 = int(_max_tool_rounds * 0.8)
        _warn_threshold_96 = _max_tool_rounds - 2
        if round_i == _warn_threshold_80:
            api_messages.append({
                "role": "system",
                "content": (
                    f"⚠️ 你已使用 {round_i}/{_max_tool_rounds} 轮工具调用。"
                    "如果当前任务尚未完成，请尽快保存进度到 agenda.md，"
                    "并使用 set_trigger 设置续接触发器，在剩余轮次中做好收尾。"
                ),
            })
        elif round_i == _warn_threshold_96:
            api_messages.append({
                "role": "system",
                "content": f"🚨 仅剩 2 轮工具调用。请立即保存进度到 agenda.md 并设置续接触发器。",
            })
        payload = {
            "model": model.model,
            "messages": api_messages,
            "temperature": 0.7,
            "max_tokens": get_max_tokens(model.provider, model.model),  # provider-safe limit
            "tools": tools_for_llm,
            "stream": True,
            **get_tool_params(model.provider),
        }

        # Stream the response (with retry for connection errors & empty responses)
        full_content = ""
        tool_calls_data = []  # accumulate tool calls from stream
        last_finish_reason = None
        _max_retries = 3
        _RETRIABLE_HTTP_CODES = {429, 500, 502, 503}

        # ── Streaming <think> tag filter state ──
        # Some reasoning models (MiniMax, DeepSeek-R1) wrap internal
        # chain-of-thought in <think>...</think> tags inside the content
        # field.  We suppress these from being streamed to the user.
        _in_think = False        # True while inside <think>...</think>
        _tag_buffer = ""         # buffer for partial tag matching
        for _attempt in range(_max_retries):
            try:
                async with httpx.AsyncClient(timeout=120) as client:
                    async with client.stream("POST", url, json=payload, headers=headers) as resp:
                        # Debug: log HTTP status and request ID
                        _req_id = resp.headers.get("x-request-id") or resp.headers.get("request-id") or resp.headers.get("cf-ray") or ""
                        print(f"[LLM-DBG] HTTP status={resp.status_code}, url={url}, request_id={_req_id}", flush=True)
                        if resp.status_code in _RETRIABLE_HTTP_CODES:
                            error_body = ""
                            async for chunk in resp.aiter_bytes():
                                error_body += chunk.decode(errors="replace")
                            print(f"[LLM-DBG] Retriable HTTP {resp.status_code}: {error_body[:300]}", flush=True)
                            if _attempt < _max_retries - 1:
                                import asyncio as _aio
                                wait = (_attempt + 1) * 2  # 2s, 4s
                                print(f"[LLM-RETRY] HTTP {resp.status_code}, retrying in {wait}s (attempt {_attempt+1}/{_max_retries})...", flush=True)
                                await _aio.sleep(wait)
                                full_content = ""
                                tool_calls_data = []
                                last_finish_reason = None
                                continue
                            return f"[LLM Error] HTTP {resp.status_code} after {_max_retries} retries: {error_body[:200]}"
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
                                err_msg = chunk['error'].get('message', str(chunk['error']))[:200]
                                # Retry on server-side errors in SSE body
                                if _attempt < _max_retries - 1 and any(kw in err_msg.lower() for kw in ["overloaded", "rate_limit", "capacity", "temporarily"]):
                                    import asyncio as _aio
                                    wait = (_attempt + 1) * 2
                                    print(f"[LLM-RETRY] SSE error '{err_msg[:80]}', retrying in {wait}s...", flush=True)
                                    await _aio.sleep(wait)
                                    full_content = ""
                                    tool_calls_data = []
                                    last_finish_reason = None
                                    break  # break inner loop to retry
                                return f"[LLM Error] {err_msg}"

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

                            # Text content — filter out <think>...</think> blocks
                            if delta.get("content"):
                                text = delta["content"]
                                full_content += text

                                # ── streaming think-tag filter ──
                                _tag_buffer += text
                                emit = ""
                                think_emit = ""
                                i = 0
                                buf = _tag_buffer
                                while i < len(buf):
                                    if not _in_think:
                                        if buf[i] == '<':
                                            tag_candidate = buf[i:]
                                            open_tag = "<think>"
                                            if tag_candidate.startswith(open_tag):
                                                _in_think = True
                                                i += len(open_tag)
                                                continue
                                            elif open_tag.startswith(tag_candidate):
                                                _tag_buffer = buf[i:]
                                                break
                                            else:
                                                emit += buf[i]
                                                i += 1
                                        else:
                                            emit += buf[i]
                                            i += 1
                                    else:
                                        if buf[i] == '<':
                                            tag_candidate = buf[i:]
                                            close_tag = "</think>"
                                            if tag_candidate.startswith(close_tag):
                                                _in_think = False
                                                i += len(close_tag)
                                                continue
                                            elif close_tag.startswith(tag_candidate):
                                                _tag_buffer = buf[i:]
                                                break
                                            else:
                                                think_emit += buf[i]
                                                i += 1
                                        else:
                                            think_emit += buf[i]
                                            i += 1
                                else:
                                    _tag_buffer = ""  # fully consumed

                                if think_emit and on_thinking:
                                    try:
                                        await on_thinking(think_emit)
                                    except Exception:
                                        pass
                                if emit and on_chunk:
                                    try:
                                        await on_chunk(emit)
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

                # Check for empty response — retry if LLM returned nothing
                if not full_content.strip() and not tool_calls_data:
                    if _attempt < _max_retries - 1:
                        import asyncio as _aio
                        wait = (_attempt + 1) * 2  # 2s, 4s
                        print(f"[LLM-RETRY] Empty response from LLM (no content, no tool_calls), retrying in {wait}s (attempt {_attempt+1}/{_max_retries})...", flush=True)
                        await _aio.sleep(wait)
                        full_content = ""
                        tool_calls_data = []
                        last_finish_reason = None
                        _in_think = False
                        _tag_buffer = ""
                        continue
                    print(f"[LLM-WARN] Empty response after {_max_retries} attempts", flush=True)

                break  # Success — exit retry loop

            except (httpx.ConnectError, httpx.ReadError, httpx.ConnectTimeout, httpx.ReadTimeout) as e:
                if _attempt < _max_retries - 1:
                    import asyncio as _aio
                    wait = (_attempt + 1) * 2  # 2s, 4s
                    print(f"[LLM-RETRY] Attempt {_attempt+1} failed ({type(e).__name__}), retrying in {wait}s...", flush=True)
                    await _aio.sleep(wait)
                    full_content = ""
                    tool_calls_data = []
                    last_finish_reason = None
                    _in_think = False
                    _tag_buffer = ""
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
            # Strip <think>...</think> from final content (reasoning models)
            import re as _re
            full_content = _re.sub(r'<think>[\s\S]*?</think>\s*', '', full_content).strip()
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
                    await on_tool_call({"name": tool_name, "args": args, "status": "done", "result": result})
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
    role_description = ""
    welcome_message = ""
    llm_model = None
    history_messages = []

    try:
        async with async_session() as db:
            print(f"[WS] Looking up user {user_id}")
            result = await db.execute(select(User).where(User.id == user_id))
            user = result.scalar_one_or_none()
            if not user:
                print("[WS] User not found")
                await websocket.send_json({"type": "error", "content": "User not found"})
                await websocket.close(code=4001)
                return

            print(f"[WS] Checking agent access for {agent_id}")
            agent, _ = await check_agent_access(db, user, agent_id)
            # Check agent expiry
            if is_agent_expired(agent):
                await websocket.send_json({"type": "error", "content": "This Agent has expired and is off duty. Please contact your admin to extend its service."})
                await websocket.close(code=4003)
                return
            agent_name = agent.name
            role_description = agent.role_description or ""
            welcome_message = agent.welcome_message or ""
            ctx_size = agent.context_window_size or 100
            print(f"[WS] Agent: {agent_name}, model_id: {agent.primary_model_id}, ctx: {ctx_size}")

            # Load the agent's primary model
            if agent.primary_model_id:
                model_result = await db.execute(
                    select(LLMModel).where(LLMModel.id == agent.primary_model_id)
                )
                llm_model = model_result.scalar_one_or_none()
                print(f"[WS] Model loaded: {llm_model.model if llm_model else 'None'}")

            # Resolve or create chat session
            from app.models.chat_session import ChatSession
            from sqlalchemy import select as _sel
            from datetime import datetime as _dt, timezone as _tz
            conv_id = session_id
            if conv_id:
                # Validate the session belongs to this agent
                _sr = await db.execute(
                    _sel(ChatSession).where(
                        ChatSession.id == uuid.UUID(conv_id),
                        ChatSession.agent_id == agent_id,
                    )
                )
                _existing = _sr.scalar_one_or_none()
                if not _existing:
                    conv_id = None  # fall through to create
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
                    print(f"[WS] Created default session {conv_id}")

            try:
                history_result = await db.execute(
                    select(ChatMessage)
                    .where(ChatMessage.agent_id == agent_id, ChatMessage.conversation_id == conv_id)
                    .order_by(ChatMessage.created_at.desc())
                    .limit(20)
                )
                history_messages = list(reversed(history_result.scalars().all()))
                print(f"[WS] Loaded {len(history_messages)} history messages for session {conv_id}")
            except Exception as e:
                print(f"[WS] History load failed (non-fatal): {e}")
    except Exception as e:
        print(f"[WS] Setup error: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()
        await websocket.send_json({"type": "error", "content": "Setup failed"})
        await websocket.close(code=4002)  # Config error — client should NOT retry
        return

    agent_id_str = str(agent_id)
    if agent_id_str not in manager.active_connections:
        manager.active_connections[agent_id_str] = []
    manager.active_connections[agent_id_str].append((websocket, conv_id))
    print(f"[WS] Ready! Agent={agent_name}")

    # Build conversation context from history
    # IMPORTANT: Include tool_call messages so the LLM maintains tool-calling behavior.
    # Without them, Claude sees user→assistant-text patterns and learns to skip tools.
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
                conversation.append({
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [{
                        "id": tc_id,
                        "type": "function",
                        "function": {"name": tc_name, "arguments": _j_hist.dumps(tc_args, ensure_ascii=False)},
                    }],
                })
                # Tool result message
                conversation.append({
                    "role": "tool",
                    "tool_call_id": tc_id,
                    "content": str(tc_result)[:500],
                })
            except Exception:
                continue  # Skip malformed tool_call records
        else:
            conversation.append({"role": msg.role, "content": msg.content})

    try:
        # Send welcome message on new session (no history)
        if welcome_message and not history_messages:
            await websocket.send_json({"type": "done", "role": "assistant", "content": welcome_message})

        while True:
            print(f"[WS] Waiting for message from {agent_name}...")
            data = await websocket.receive_json()
            content = data.get("content", "")
            display_content = data.get("display_content", "")  # User-facing display text
            file_name = data.get("file_name", "")  # Original file name for attachment display
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

            # Add user message to conversation (full LLM context)
            conversation.append({"role": "user", "content": content})

            # Save user message — display_content for history display, content for LLM
            # Prefix with [file:name] if there's a file attachment so history can show it
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
                    
                    async def thinking_to_ws(text: str):
                        """Send thinking chunks to client for collapsible display."""
                        await websocket.send_json({"type": "thinking", "content": text})

                    assistant_response = await call_llm(
                        llm_model,
                        conversation[-ctx_size:],
                        agent_name,
                        role_description,
                        agent_id=agent_id,
                        user_id=user_id,
                        on_chunk=stream_to_ws,
                        on_tool_call=tool_call_to_ws,
                        on_thinking=thinking_to_ws,
                        supports_vision=getattr(llm_model, 'supports_vision', False),
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

