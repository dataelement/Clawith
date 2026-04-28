"""Unified LLM calling service with failover support for all execution paths.

This module provides a shared entry point for all LLM calls across:
- WebSocket chat
- IM channels (Feishu, Slack, Teams, Discord, WeCom, DingTalk)
- Background services (task executor, scheduler, heartbeat, etc.)

All paths now support:
1. Config-level fallback: if primary missing, use fallback directly
2. Runtime failover: if primary fails with retryable error, try fallback once
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import async_session
from app.services.agent_tools import AGENT_TOOLS, execute_tool, get_agent_tools_for_llm
from app.services.token_tracker import record_token_usage, extract_usage_tokens, estimate_tokens_from_chars

from .client import LLMError
from .failover import classify_error, FailoverErrorType
from .utils import LLMMessage, create_llm_client, get_max_tokens, get_model_api_key

if TYPE_CHECKING:
    from app.models.agent import Agent
    from app.models.llm import LLMModel


TOOLS_REQUIRING_ARGS = frozenset({
    "write_file", "read_file", "delete_file", "read_document",
    "send_message_to_agent", "send_feishu_message", "send_email"
})


# ═══════════════════════════════════════════════════════════════════════════════
# Failover Guard
# ═══════════════════════════════════════════════════════════════════════════════

class FailoverGuard:
    """Guard state for failover decisions."""

    def __init__(self):
        self.tool_executed = False
        self.streaming_started = False
        self.failover_done = False

    def mark_tool_executed(self):
        """Mark that a side-effecting tool has been executed."""
        self.tool_executed = True

    def mark_streaming_started(self):
        """Mark that streaming output has started."""
        self.streaming_started = True

    def mark_failover_done(self):
        """Mark that failover has already happened once."""
        self.failover_done = True

    def can_failover(self) -> bool:
        """Check if failover is allowed based on guard rules."""
        if self.failover_done:
            return False  # Only failover once
        if self.tool_executed:
            return False  # Don't failover after side effects
        if self.streaming_started:
            return False  # Don't failover after streaming started
        return True


def is_retryable_error(result: str) -> bool:
    """Check if an error result is retryable.
    
    Uses unified classification from failover.py.
    """
    if not (result.startswith("[LLM Error]") or result.startswith("[LLM call error]") or result.startswith("[Error]")):
        return False
        
    return classify_error(Exception(result)) != FailoverErrorType.NON_RETRYABLE


def _get_model_timeout(model: "LLMModel") -> float:
    """Return the effective request timeout for a model."""
    return float(getattr(model, "request_timeout", None) or 120.0)


# ═══════════════════════════════════════════════════════════════════════════════
# Helper Functions
# ═══════════════════════════════════════════════════════════════════════════════

async def _get_agent_config(agent_id) -> tuple[int, str | None]:
    """Get agent config: max_tool_rounds and token limit status."""
    if not agent_id:
        return 50, None

    try:
        from app.models.agent import Agent as AgentModel
        async with async_session() as _db:
            _ar = await _db.execute(select(AgentModel).where(AgentModel.id == agent_id))
            _agent = _ar.scalar_one_or_none()
            if _agent:
                max_rounds = _agent.max_tool_rounds or 50
                if _agent.max_tokens_per_day and _agent.tokens_used_today >= _agent.max_tokens_per_day:
                    return max_rounds, f"⚠️ Daily token usage has reached the limit ({_agent.tokens_used_today:,}/{_agent.max_tokens_per_day:,}). Please try again tomorrow or ask admin to increase the limit."
                if _agent.max_tokens_per_month and _agent.tokens_used_month >= _agent.max_tokens_per_month:
                    return max_rounds, f"⚠️ Monthly token usage has reached the limit ({_agent.tokens_used_month:,}/{_agent.max_tokens_per_month:,}). Please ask admin to increase the limit."
                return max_rounds, None
    except Exception:
        pass
    return 50, None


async def _get_user_name(user_id) -> str | None:
    """Get user's display name for personalized context."""
    if not user_id:
        return None
    try:
        from app.models.user import User as _UserModel
        async with async_session() as _udb:
            _ur = await _udb.execute(select(_UserModel).where(_UserModel.id == user_id))
            _u = _ur.scalar_one_or_none()
            if _u:
                return _u.display_name or _u.username
    except Exception:
        pass
    return None


def _convert_messages_for_vision(
    api_messages: list, supports_vision: bool
) -> list:
    """Convert image markers to vision format if supported, or strip them."""
    import re as _re_v
    import copy

    # Deep copy to avoid modifying the original list in place
    new_messages = copy.deepcopy(api_messages)

    if supports_vision:
        # Vision format: convert image markers in strings to OpenAI Vision API list format
        for i, msg in enumerate(new_messages):
            if msg.role != "user" or not msg.content or not isinstance(msg.content, str):
                continue
            
            content_str = msg.content
            pattern = r'\[image_data:(data:image/[^;]+;base64,[A-Za-z0-9+/=]+)\]'
            images = _re_v.findall(pattern, content_str)
            
            if not images:
                continue

            text = _re_v.sub(pattern, '', content_str).strip()
            parts = [{"type": "image_url", "image_url": {"url": img}} for img in images]
            if text:
                # Per OpenAI spec, text part should come after image parts
                parts.append({"type": "text", "text": text})
            
            new_messages[i] = type(msg)(role=msg.role, content=parts, tool_calls=msg.tool_calls, tool_call_id=msg.tool_call_id)
    else:
        # Non-vision format: ensure content is a string for all roles, stripping image data.
        _img_marker_pattern = r'\[image_data:data:image/[^;]+;base64,[A-Za-z0-9+/=]+\]'
        for i, msg in enumerate(new_messages):
            
            if isinstance(msg.content, list):
                # It's a list, join all text parts. This handles user messages
                # with vision content and tool messages from vision_inject.
                text_parts = [part.get("text", "") for part in msg.content if part.get("type") == "text"]
                content_str = "\n".join(text_parts).strip()
                new_messages[i] = type(msg)(role=msg.role, content=content_str, tool_calls=msg.tool_calls, tool_call_id=msg.tool_call_id)

            elif isinstance(msg.content, str) and "[image_data:" in msg.content:
                # It's a string with image markers, strip them
                _n_imgs = len(_re_v.findall(_img_marker_pattern, msg.content))
                cleaned = _re_v.sub(_img_marker_pattern, '', msg.content).strip()
                if _n_imgs > 0:
                    cleaned += f"\n[用户发送了 {_n_imgs} 张图片，但当前模型不支持视觉，无法查看图片内容]"
                new_messages[i] = type(msg)(role=msg.role, content=cleaned, tool_calls=msg.tool_calls, tool_call_id=msg.tool_call_id)

    return new_messages


def _check_tool_requires_args(tool_name: str, args: dict) -> tuple[bool, str]:
    """Check if tool requires arguments and return (should_execute, result_or_error)."""
    if not args and tool_name in TOOLS_REQUIRING_ARGS:
        return False, f"Error: {tool_name} was called with empty arguments. You must provide the required parameters. Please retry with the correct arguments."
    return True, ""


async def _process_tool_call(
    tc: dict,
    api_messages: list,
    agent_id,
    user_id,
    session_id: str,
    supports_vision: bool,
    on_tool_call,
    full_reasoning_content: str,
) -> str:
    """Process a single tool call and return result."""
    fn = tc["function"]
    tool_name = fn["name"]
    raw_args = fn.get("arguments", "{}")
    logger.info(f"[LLM] Calling tool: {tool_name}({json.dumps(raw_args, ensure_ascii=False)[:100]})")

    try:
        args = json.loads(raw_args) if raw_args else {}
    except json.JSONDecodeError:
        args = {}

    # Guard: check if tool requires arguments
    should_execute, error_msg = _check_tool_requires_args(tool_name, args)
    if not should_execute:
        return error_msg

    # Notify client about tool call (in-progress)
    if on_tool_call:
        try:
            await on_tool_call({
                "name": tool_name,
                "call_id": tc.get("id", ""),
                "args": args,
                "status": "running",
                "reasoning_content": full_reasoning_content
            })
        except Exception:
            pass

    # Execute tool
    result = await execute_tool(
        tool_name, args,
        agent_id=agent_id,
        user_id=user_id or agent_id,
        session_id=session_id,
    )
    logger.debug(f"[LLM] Tool result: {result[:100]}")

    # ── Vision injection for screenshot tools ──
    tool_content: str | list = str(result)
    if supports_vision and agent_id:
        try:
            from app.services.vision_inject import try_inject_screenshot_vision
            from app.config import get_settings
            settings = get_settings()
            ws_path = Path(settings.AGENT_DATA_DIR) / str(agent_id)
            vision_content = try_inject_screenshot_vision(tool_name, str(result), ws_path)
            if vision_content:
                tool_content = vision_content
                logger.info(f"[LLM] Injected screenshot vision for {tool_name}")
        except Exception as e:
            logger.warning(f"[LLM] Vision injection failed for {tool_name}: {e}")

    # Notify client about tool call result
    if on_tool_call:
        try:
            await on_tool_call({
                "name": tool_name,
                "call_id": tc.get("id", ""),
                "args": args,
                "status": "done",
                "result": result,
                "reasoning_content": full_reasoning_content
            })
        except Exception:
            pass
    
    api_messages.append(LLMMessage(
        role="tool",
        tool_call_id=tc["id"],
        content=tool_content,
    ))
    return ""



# ═══════════════════════════════════════════════════════════════════════════════
# Core LLM Call Functions
# ═══════════════════════════════════════════════════════════════════════════════

async def call_llm(
    model: LLMModel,
    messages: list[dict],
    agent_name: str,
    role_description: str,
    agent_id=None,
    user_id=None,
    session_id: str = "",
    on_chunk=None,
    on_tool_call=None,
    on_tool_delta=None,
    on_thinking=None,
    supports_vision=False,
    max_tool_rounds_override: int | None = None,
) -> str:
    """Call LLM via unified client with function-calling tool loop."""
    # Get agent config for tool rounds
    _max_tool_rounds, _token_limit_msg = await _get_agent_config(agent_id)
    if _token_limit_msg:
        return _token_limit_msg
    if max_tool_rounds_override and max_tool_rounds_override < _max_tool_rounds:
        _max_tool_rounds = max_tool_rounds_override

    # Get user's name for personalized context
    _user_name = await _get_user_name(user_id)

    # Build rich prompt with soul, memory, skills, relationships
    from app.services.agent_context import build_agent_context
    # Look up current user's display name so the agent knows who it's talking to
    static_prompt, dynamic_prompt = await build_agent_context(agent_id, agent_name, role_description, current_user_name=_user_name)

    # Load tools dynamically from DB
    tools_for_llm = await get_agent_tools_for_llm(agent_id) if agent_id else AGENT_TOOLS

    # Convert messages to LLMMessage format
    api_messages = [LLMMessage(role="system", content=static_prompt, dynamic_content=dynamic_prompt)]
    for msg in messages:
        api_messages.append(LLMMessage(
            role=msg.get("role", "user"),
            content=msg.get("content"),
            tool_calls=msg.get("tool_calls"),
            tool_call_id=msg.get("tool_call_id"),
        ))

    # Vision format conversion
    api_messages = _convert_messages_for_vision(api_messages, supports_vision)

    # Create the unified LLM client
    try:
        client = create_llm_client(
            provider=model.provider,
            api_key=get_model_api_key(model),
            model=model.model,
            base_url=model.base_url,
            timeout=_get_model_timeout(model),
        )
    except Exception as e:
        return f"[Error] Failed to create LLM client: {e}"

    max_tokens = get_max_tokens(model.provider, model.model, getattr(model, 'max_output_tokens', None))
    _accumulated_tokens = 0

    # Tool-calling loop
    for round_i in range(_max_tool_rounds):
        # Dynamic tool-call limit warning
        _warn_threshold_80 = int(_max_tool_rounds * 0.8)
        _warn_threshold_96 = _max_tool_rounds - 2
        if round_i == _warn_threshold_80:
            api_messages.append(LLMMessage(
                role="user",
                content=(
                    f"⚠️ 你已使用 {round_i}/{_max_tool_rounds} 轮工具调用。"
                    "如果当前任务尚未完成，请尽快保存进度到 focus.md，"
                    "并使用 set_trigger 设置续接触发器，在剩余轮次中做好收尾。"
                ),
            ))
        elif round_i == _warn_threshold_96:
            api_messages.append(LLMMessage(
                role="user",
                content=f"🚨 仅剩 2 轮工具调用。请立即保存进度到 focus.md 并设置续接触发器。",
            ))

        try:
            # Use streaming API for real-time responses
            response = await client.stream(
                messages=api_messages,
                tools=tools_for_llm if tools_for_llm else None,
                temperature=model.temperature,
                max_tokens=max_tokens,
                on_chunk=on_chunk,
                on_tool_delta=on_tool_delta,
                on_thinking=on_thinking,
            )
        except LLMError as e:
            logger.error(f"[LLM] LLMError: provider={getattr(model, 'provider', '?')} model={getattr(model, 'model', '?')} {e}")
            if agent_id and _accumulated_tokens > 0:
                await record_token_usage(agent_id, _accumulated_tokens)
            await client.close()
            return f"[LLM Error] {e}"
        except Exception as e:
            logger.exception(f"[LLM] Unexpected error: {type(e).__name__}: {str(e)[:300]}")
            if agent_id and _accumulated_tokens > 0:
                await record_token_usage(agent_id, _accumulated_tokens)
            await client.close()
            return f"[LLM call error] {type(e).__name__}: {str(e)[:200]}"

        # Track tokens for this round
        real_tokens = extract_usage_tokens(response.usage)
        if real_tokens:
            _accumulated_tokens += real_tokens
        else:
            round_chars = sum(len(m.content or '') if isinstance(m.content, str) else 0 for m in api_messages) + len(response.content or '')
            _accumulated_tokens += estimate_tokens_from_chars(round_chars)

        # If no tool calls, return the final content
        if not response.tool_calls:
            if agent_id and _accumulated_tokens > 0:
                await record_token_usage(agent_id, _accumulated_tokens)
            await client.close()
            return response.content or "[LLM returned empty content]"

        # Execute tool calls
        logger.info(f"[LLM] Round {round_i+1}: {len(response.tool_calls)} tool call(s)")

        # Add assistant message with tool calls
        api_messages.append(LLMMessage(
            role="assistant",
            content=response.content or None,
            tool_calls=[{
                "id": tc["id"],
                "type": "function",
                "function": tc["function"],
            } for tc in response.tool_calls],
            reasoning_content=response.reasoning_content,
        ))

        full_reasoning_content = response.reasoning_content or ""

        for tc in response.tool_calls:
            tool_error = await _process_tool_call(
                tc=tc,
                api_messages=api_messages,
                agent_id=agent_id,
                user_id=user_id,
                session_id=session_id,
                supports_vision=supports_vision,
                on_tool_call=on_tool_call,
                full_reasoning_content=full_reasoning_content,
            )
            if tool_error:
                api_messages.append(LLMMessage(
                    role="tool",
                    content=tool_error,
                    tool_call_id=tc.get("id", ""),
                ))

    # Record tokens even on "too many rounds" exit
    if agent_id and _accumulated_tokens > 0:
        await record_token_usage(agent_id, _accumulated_tokens)
    await client.close()
    return "[Error] Too many tool call rounds"


async def call_llm_with_failover(
    primary_model,
    fallback_model,
    messages: list[dict],
    agent_name: str,
    role_description: str,
    agent_id=None,
    user_id=None,
    session_id: str = "",
    on_chunk=None,
    on_thinking=None,
    on_tool_call=None,
    on_tool_delta=None,
    supports_vision=False,
    on_failover=None,
) -> str:
    """Call LLM with automatic failover support."""
    guard = FailoverGuard()

    # Config-level fallback: if no primary, use fallback directly
    if primary_model is None and fallback_model is not None:
        logger.info("[Failover] Primary model not configured, using fallback directly")
        primary_model = fallback_model
        fallback_model = None

    if primary_model is None:
        return "⚠️ 未配置 LLM 模型"

    # Wrapper callbacks to track state for guard checks
    async def _wrapped_on_chunk(text: str):
        guard.mark_streaming_started()
        if on_chunk:
            await on_chunk(text)

    async def _wrapped_on_tool_call(data: dict):
        if data.get("status") == "done":
            guard.mark_tool_executed()
        if on_tool_call:
            await on_tool_call(data)

    # Try primary model
    primary_result = await call_llm(
        primary_model,
        messages,
        agent_name,
        role_description,
        agent_id=agent_id,
        user_id=user_id,
        session_id=session_id,
        on_chunk=_wrapped_on_chunk,
        on_tool_call=_wrapped_on_tool_call,
        on_tool_delta=on_tool_delta,
        on_thinking=on_thinking,
        supports_vision=supports_vision,
    )

    # Check if we need to failover
    if not is_retryable_error(primary_result):
        logger.warning(f"[Failover] Canceled: Primary model returned a non-retryable error: {primary_result[:150]}")
        return primary_result

    # Check guard conditions
    if not guard.can_failover():
        if guard.tool_executed:
            logger.warning("[Failover] Blocked: side-effecting tool already executed")
        elif guard.streaming_started:
            logger.warning("[Failover] Blocked: streaming already started")
        elif guard.failover_done:
            logger.warning("[Failover] Blocked: failover already done once")
        return primary_result

    # No fallback available
    if fallback_model is None:
        logger.warning("[Failover] No fallback model available")
        return primary_result

    # Runtime failover: retry with fallback model
    logger.info(f"[Failover] Retrying with fallback model: {fallback_model.provider}/{fallback_model.model}")

    if on_failover:
        try:
            await on_failover(f"Switched to fallback model: {fallback_model.model}")
        except Exception:
            pass

    guard.mark_failover_done()

    # Call fallback with fresh callbacks
    fallback_guard = FailoverGuard()
    fallback_guard.mark_failover_done()

    async def _fallback_on_chunk(text: str):
        fallback_guard.mark_streaming_started()
        if on_chunk:
            await on_chunk(text)

    async def _fallback_on_tool_call(data: dict):
        if data.get("status") == "done":
            fallback_guard.mark_tool_executed()
        if on_tool_call:
            await on_tool_call(data)

    fallback_result = await call_llm(
        fallback_model,
        messages,
        agent_name,
        role_description,
        agent_id=agent_id,
        user_id=user_id,
        session_id=session_id,
        on_chunk=_fallback_on_chunk,
        on_tool_call=_fallback_on_tool_call,
        on_tool_delta=on_tool_delta,
        on_thinking=on_thinking,
        supports_vision=getattr(fallback_model, 'supports_vision', False),
    )

    # Combine error messages if fallback also failed
    if is_retryable_error(fallback_result) or fallback_result.startswith("⚠️") or fallback_result.startswith("[Error]"):
        return f"⚠️ 调用模型出错: Primary: {primary_result[:80]} | Fallback: {fallback_result[:80]}"

    return fallback_result


# ═══════════════════════════════════════════════════════════════════════════════
# High-level Agent Call Functions
# ═══════════════════════════════════════════════════════════════════════════════

async def call_agent_llm(
    db: AsyncSession,
    agent_id: uuid.UUID,
    user_text: str,
    history: list[dict] | None = None,
    user_id: uuid.UUID | None = None,
    session_id: str = "",
    on_chunk=None,
    on_thinking=None,
    supports_vision: bool = False,
) -> str:
    """Call the agent's LLM with automatic failover support."""
    from app.models.agent import Agent
    from app.models.llm import LLMModel
    from app.core.permissions import is_agent_expired

    # Load agent
    agent_result = await db.execute(select(Agent).where(Agent.id == agent_id))
    agent: Agent | None = agent_result.scalar_one_or_none()
    if not agent:
        return "⚠️ 数字员工未找到"

    if is_agent_expired(agent):
        return "This Agent has expired and is off duty. Please contact your admin to extend its service."

    # Load primary model
    primary_model: LLMModel | None = None
    if agent.primary_model_id:
        model_result = await db.execute(select(LLMModel).where(LLMModel.id == agent.primary_model_id))
        primary_model = model_result.scalar_one_or_none()

    # Load fallback model
    fallback_model: LLMModel | None = None
    if agent.fallback_model_id:
        fb_result = await db.execute(select(LLMModel).where(LLMModel.id == agent.fallback_model_id))
        fallback_model = fb_result.scalar_one_or_none()

    # Config-level fallback: primary missing -> use fallback
    if not primary_model and fallback_model:
        primary_model = fallback_model
        fallback_model = None
        logger.warning(f"[call_agent_llm] Primary model unavailable, using fallback: {primary_model.model}")

    if not primary_model:
        return f"⚠️ {agent.name} 未配置 LLM 模型，请在管理后台设置。"

    # Build conversation messages
    messages: list[dict] = []
    if history:
        messages.extend(history[-10:])
    messages.append({"role": "user", "content": user_text})

    # Use unified call_llm_with_failover
    try:
        reply = await call_llm_with_failover(
            primary_model=primary_model,
            fallback_model=fallback_model,
            messages=messages,
            agent_name=agent.name,
            role_description=agent.role_description or "",
            agent_id=agent_id,
            user_id=user_id or agent_id,
            session_id=session_id,
            on_chunk=on_chunk,
            on_thinking=on_thinking,
            supports_vision=supports_vision or getattr(primary_model, 'supports_vision', False),
        )
        return reply
    except Exception as e:
        error_msg = str(e) or repr(e)
        logger.error(f"[call_agent_llm] Unexpected error: {error_msg}")
        return f"⚠️ 调用模型出错: {error_msg[:150]}"


async def call_agent_llm_with_tools(
    db: AsyncSession,
    agent_id: uuid.UUID,
    system_prompt: str,
    user_prompt: str,
    max_rounds: int = 50,
    session_id: str = "",
) -> str:
    """Call agent LLM with tool-calling loop (for background services)."""
    from app.models.agent import Agent
    from app.models.llm import LLMModel

    # Load agent and models
    agent_result = await db.execute(select(Agent).where(Agent.id == agent_id))
    agent: Agent | None = agent_result.scalar_one_or_none()
    if not agent:
        return "⚠️ Agent not found"

    # Load models
    primary_model: LLMModel | None = None
    if agent.primary_model_id:
        model_result = await db.execute(select(LLMModel).where(LLMModel.id == agent.primary_model_id))
        primary_model = model_result.scalar_one_or_none()

    fallback_model: LLMModel | None = None
    if agent.fallback_model_id:
        fb_result = await db.execute(select(LLMModel).where(LLMModel.id == agent.fallback_model_id))
        fallback_model = fb_result.scalar_one_or_none()

    # Config-level fallback
    if not primary_model and fallback_model:
        primary_model = fallback_model
        fallback_model = None

    if not primary_model:
        return f"⚠️ {agent.name} has no LLM model configured"

    # Build messages
    messages = [
        LLMMessage(role="system", content=system_prompt),
        LLMMessage(role="user", content=user_prompt),
    ]

    # Load tools
    tools_for_llm = await get_agent_tools_for_llm(agent_id)

    async def _try_model(model: LLMModel) -> tuple[str, bool, bool]:
        """Try to complete with a model. Returns (response, success, tool_executed)."""
        _accumulated_tokens = 0
        tool_executed = False
        try:
            client = create_llm_client(
                provider=model.provider,
                api_key=get_model_api_key(model),
                model=model.model,
                base_url=model.base_url,
                timeout=_get_model_timeout(model),
            )

            max_tokens = get_max_tokens(
                model.provider, model.model,
                getattr(model, 'max_output_tokens', None)
            )

            # Tool-calling loop
            api_messages = list(messages)
            for round_i in range(max_rounds):
                try:
                    response = await client.complete(
                        messages=api_messages,
                        tools=tools_for_llm if tools_for_llm else None,
                        temperature=model.temperature,
                        max_tokens=max_tokens,
                    )
                except Exception as e:
                    logger.error(f"[call_agent_llm_with_tools] Agent {agent_id}: LLM call error: {e}")
                    await client.close()
                    if agent_id and _accumulated_tokens > 0:
                        await record_token_usage(agent_id, _accumulated_tokens)
                    raise

                # Track tokens for this round
                real_tokens = extract_usage_tokens(response.usage)
                if real_tokens:
                    _accumulated_tokens += real_tokens
                else:
                    round_chars = sum(len(m.content or '') if isinstance(m.content, str) else 0 for m in api_messages) + len(response.content or '')
                    _accumulated_tokens += estimate_tokens_from_chars(round_chars)

                if not response.tool_calls:
                    if agent_id and _accumulated_tokens > 0:
                        await record_token_usage(agent_id, _accumulated_tokens)
                    await client.close()
                    return response.content or "[Empty response]", True

                # Execute tool calls
                api_messages.append(LLMMessage(
                    role="assistant",
                    content=response.content or None,
                    tool_calls=[{
                        "id": tc["id"],
                        "type": "function",
                        "function": tc["function"],
                    } for tc in response.tool_calls],
                    reasoning_content=response.reasoning_content,
                ))

                for tc in response.tool_calls:
                    fn = tc["function"]
                    tool_name = fn["name"]
                    raw_args = fn.get("arguments", "{}")
                    try:
                        args = json.loads(raw_args) if raw_args else {}
                    except json.JSONDecodeError:
                        args = {}

                    tool_executed = True
                    result = await execute_tool(
                        tool_name, args,
                        agent_id=agent_id,
                        user_id=agent.creator_id,
                        session_id=session_id,
                    )
                    api_messages.append(LLMMessage(
                        role="tool",
                        tool_call_id=tc["id"],
                        content=str(result),
                    ))

            if agent_id and _accumulated_tokens > 0:
                await record_token_usage(agent_id, _accumulated_tokens)
            await client.close()
            return "[Error] Too many tool call rounds", False, tool_executed

        except Exception as e:
            if agent_id and _accumulated_tokens > 0:
                await record_token_usage(agent_id, _accumulated_tokens)
            return f"[Error] {e}", False, tool_executed

    # Try primary model
    reply, success, primary_tool_executed = await _try_model(primary_model)
    if success:
        return reply

    # Primary failed - check if retryable
    error_type = classify_error(Exception(reply))
    if error_type == FailoverErrorType.NON_RETRYABLE or not fallback_model:
        return reply

    if primary_tool_executed:
        logger.warning("[call_agent_llm_with_tools] Blocked fallback: side-effecting tool already executed")
        return reply

    # Try fallback model
    logger.info(f"[call_agent_llm_with_tools] Retrying with fallback: {fallback_model.model}")
    reply2, success2, _fallback_tool_executed = await _try_model(fallback_model)
    if success2:
        return reply2

    return f"⚠️ Both models failed | Primary: {reply[:80]} | Fallback: {reply2[:80]}"


__all__ = [
    "call_llm",
    "call_llm_with_failover",
    "call_agent_llm",
    "call_agent_llm_with_tools",
    "FailoverGuard",
    "is_retryable_error",
]
