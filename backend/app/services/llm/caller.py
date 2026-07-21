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
from app.config import get_settings
from app.database import async_session

from app.services.token_tracker import (
    TokenUsage,
    record_token_usage,
    extract_token_usage,
    estimate_token_usage_from_chars,
)

from .client import LLMError
from .failover import classify_error, FailoverErrorType
from .finish import FINISH_PROTOCOL_REMINDER, FINISH_TOOL_DEFINITION, find_finish_call
from .utils import LLMMessage, create_llm_client, get_max_tokens, get_model_api_key

if TYPE_CHECKING:
    from app.models.agent import Agent
    from app.models.llm import LLMModel


# NOTE: agent_tools imports are deferred to function bodies to avoid circular
# import: agent_tools → llm.finish → llm/__init__ → caller → agent_tools
async def get_agent_tools_for_llm(*args, **kwargs):
    from app.services.agent_tools import get_agent_tools_for_llm as _impl

    return await _impl(*args, **kwargs)


async def execute_tool(*args, **kwargs):
    from app.services.agent_tools import execute_tool as _impl

    return await _impl(*args, **kwargs)


TOOLS_REQUIRING_ARGS = frozenset({
    "write_file", "read_file", "move_file", "delete_file", "read_document",
    "send_message_to_agent", "send_feishu_message", "send_email"
})


def _sanitize_tool_calls_for_context(tool_calls: list[dict]) -> tuple[list[dict] | None, str | None]:
    """Return OpenAI-compatible tool calls, or a retry instruction if args are invalid."""
    sanitized: list[dict] = []
    for tc in tool_calls:
        fn = tc.get("function") or {}
        tool_name = fn.get("name") or ""
        raw_args = fn.get("arguments", "{}")

        if raw_args is None or raw_args == "":
            args_str = "{}"
        elif isinstance(raw_args, str):
            try:
                json.loads(raw_args)
            except json.JSONDecodeError as exc:
                logger.warning(
                    "[LLM] Invalid tool arguments JSON for {}: {} at pos {}",
                    tool_name or "<unknown>",
                    exc.msg,
                    exc.pos,
                )
                return None, (
                    "Your previous tool call arguments were not valid JSON. "
                    f"The affected tool was `{tool_name or 'unknown'}`. "
                    "Retry the tool call now with `function.arguments` as one valid JSON object string. "
                    "Escape all quotes and newlines inside long HTML, CSS, JavaScript, or markdown content. "
                    "Do not explain; only retry with a valid tool call."
                )
            args_str = raw_args
        elif isinstance(raw_args, (dict, list)):
            args_str = json.dumps(raw_args, ensure_ascii=False)
        else:
            return None, (
                "Your previous tool call arguments had an unsupported type. "
                f"The affected tool was `{tool_name or 'unknown'}`. "
                "Retry the tool call with `function.arguments` as one valid JSON object string."
            )

        new_tc = {
            "id": tc.get("id", ""),
            "type": tc.get("type") or "function",
            "function": {
                "name": tool_name,
                "arguments": args_str,
            },
        }
        if "_gemini_extra" in tc:
            new_tc["_gemini_extra"] = tc["_gemini_extra"]
        sanitized.append(new_tc)

    return sanitized, None


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


def _usage_from_response_or_estimate(response, api_messages: list[LLMMessage]) -> TokenUsage:
    usage = extract_token_usage(response.usage)
    if usage:
        return usage
    round_chars = sum(len(m.content or '') if isinstance(m.content, str) else 0 for m in api_messages)
    round_chars += len(response.content or '')
    return estimate_token_usage_from_chars(round_chars)


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
        from app.models.agent import Agent as _AgentModel
        async with async_session() as _udb:
            _ur = await _udb.execute(select(_UserModel).where(_UserModel.id == user_id))
            _u = _ur.scalar_one_or_none()
            if _u:
                return _u.display_name or _u.username
            # Check Agent name fallback
            _ar = await _udb.execute(select(_AgentModel).where(_AgentModel.id == user_id))
            _a = _ar.scalar_one_or_none()
            if _a:
                return _a.name
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


def _allowed_tool_names(tools_for_llm: list[dict] | None) -> set[str]:
    names: set[str] = set()
    for tool in tools_for_llm or []:
        name = ((tool.get("function") or {}).get("name") or "").strip()
        if name:
            names.add(name)
    return names


def _tool_round_limit_warning(
    *,
    round_index: int,
    max_rounds: int,
    allowed_tool_names: set[str],
    urgent: bool,
) -> str:
    """Build a warning that never advertises unavailable continuation tools."""
    prefix = (
        f"🚨 仅剩 {max_rounds - round_index} 轮模型决策。"
        if urgent
        else f"⚠️ 你已使用 {round_index}/{max_rounds} 轮模型决策。"
    )
    actions: list[str] = []
    if "upsert_focus_item" in allowed_tool_names:
        actions.append("使用 `upsert_focus_item` 保存需要续接的工作状态")
    if "set_trigger" in allowed_tool_names:
        actions.append("仅在确实需要未来唤醒时使用 `set_trigger` 安排续接")
    if not actions:
        return f"{prefix}请立即完成关键步骤、验证结果并收尾。"
    return f"{prefix}请立即完成关键步骤并验证结果；" + "；".join(actions) + "。"


def _tool_not_enabled_message(tool_name: str) -> str:
    return (
        f"Tool `{tool_name}` is not enabled for this agent. "
        "Do not call it again. Use only the tools currently available to you, "
        "or explain that the required capability is not enabled."
    )


async def _process_tool_call(
    tc: dict,
    api_messages: list,
    agent_id,
    user_id,
    session_id: str,
    supports_vision: bool,
    on_tool_call,
    full_reasoning_content: str,
    allowed_tool_names: set[str],
    on_code_output=None,
) -> str:
    """Process a single tool call and return result."""
    fn = tc["function"]
    tool_name = fn["name"]
    raw_args = fn.get("arguments", "{}")
    try:
        args = json.loads(raw_args) if raw_args else {}
    except json.JSONDecodeError:
        args = {}

    try:
        from app.services.agent_runtime.tool_execution import sanitize_tool_arguments
        from app.services.builtin_tool_definitions import builtin_sensitive_paths

        logged_args = sanitize_tool_arguments(
            args,
            sensitive_paths=builtin_sensitive_paths(tool_name),
        )
    except Exception:
        logged_args = {"_redacted": "tool arguments could not be safely serialized"}
    logger.info("[LLM] Calling tool: {}({})", tool_name, logged_args)

    # Enforce the resolved workset before inspecting tool-specific arguments.
    # A disabled tool must not bypass this guard via another validation path.
    if tool_name not in allowed_tool_names:
        result = _tool_not_enabled_message(tool_name)
        logger.warning(
            f"[LLM] Blocked disabled tool call: {tool_name} agent_id={agent_id}"
        )
        if on_tool_call:
            try:
                await on_tool_call(
                    {
                        "name": tool_name,
                        "call_id": tc.get("id", ""),
                        "args": args,
                        "status": "done",
                        "result": result,
                        "reasoning_content": full_reasoning_content,
                    }
                )
            except Exception:
                pass
        api_messages.append(
            LLMMessage(
                role="tool",
                tool_call_id=tc["id"],
                content=result,
            )
        )
        return ""

    # Guard: check if an enabled tool requires arguments.
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

    # Execute tool — pass on_output for execute_code streaming
    _on_output = on_code_output if tool_name in ("execute_code", "execute_code_e2b") else None
    result = await execute_tool(
        tool_name, args,
        agent_id=agent_id,
        user_id=user_id or agent_id,
        session_id=session_id,
        on_output=_on_output,
    )
    logger.debug(f"[LLM] Tool result: {result[:100]}")

    # ── Vision injection for screenshot tools ──
    tool_content: str | list = str(result)
    if supports_vision and agent_id:
        try:
            from app.services.vision_inject import try_inject_screenshot_vision
            settings = get_settings()
            ws_path = Path(settings.STORAGE_LOCAL_ROOT or settings.AGENT_DATA_DIR) / str(agent_id)
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
    skip_tools: bool = False,
    on_code_output=None,
    current_user_name_override: str | None = None,
    system_prompt_suffix: str | None = None,
) -> str:
    """Call LLM via unified client with function-calling tool loop."""
    # Get agent config for tool rounds
    _max_tool_rounds, _token_limit_msg = await _get_agent_config(agent_id)
    if _token_limit_msg:
        return _token_limit_msg
    from app.services.agent_runtime.model_capabilities import (
        ModelCapabilityError,
        ModelCapabilityResolver,
    )
    try:
        ModelCapabilityResolver.require_native_tool_calling(model)
    except ModelCapabilityError as exc:
        return f"[Error] {exc.code}: {exc}"
    if max_tool_rounds_override and max_tool_rounds_override < _max_tool_rounds:
        _max_tool_rounds = max_tool_rounds_override

    # Get user's name for personalized context
    if current_user_name_override:
        _user_name = current_user_name_override
    else:
        _user_name = await _get_user_name(user_id)

    # Auto-assign fallback tool call logger if none provided but conversation context exists
    if on_tool_call is None and session_id:
        from app.services.chat_session_service import save_tool_call_log
        async def _default_on_tool_call(data: dict):
            if data.get("status") == "done" and agent_id:
                await save_tool_call_log(
                    agent_id=agent_id,
                    user_id=user_id or agent_id,
                    conversation_id=session_id,
                    tool_name=data.get("name", ""),
                    arguments=data.get("args"),
                    result=data.get("result"),
                    status="done",
                    tool_call_id=data.get("call_id"),
                    reasoning_content=data.get("reasoning_content"),
                )
        on_tool_call = _default_on_tool_call

    # Resolve the effective Tool Schema before the prompt so capability policies
    # and Skill discovery cannot advertise tools absent from this model step.
    # `skip_tools=True` is set by the WS handler on the onboarding greeting turn;
    # keep `finish` available so the turn still has an explicit stop signal.
    if skip_tools:
        tools_for_llm = [FINISH_TOOL_DEFINITION]
    else:
        from app.services.agent_tools import AGENT_TOOLS
        tools_for_llm = await get_agent_tools_for_llm(agent_id) if agent_id else AGENT_TOOLS
    allowed_tool_names = _allowed_tool_names(tools_for_llm)

    from app.services.agent_context import build_agent_context

    static_prompt, dynamic_prompt = await build_agent_context(
        agent_id,
        agent_name,
        "",
        current_user_name=_user_name,
        allowed_tool_names=allowed_tool_names,
    )
    if system_prompt_suffix:
        dynamic_prompt = f"{dynamic_prompt}\n\n{system_prompt_suffix.strip()}"

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
    _accumulated_usage = TokenUsage()
    _unsaved_usage = TokenUsage()
    _protocol_repairs: set[str] = set()

    async def _protocol_violation(repair_code: str) -> str:
        if agent_id and _unsaved_usage.total_tokens > 0:
            await record_token_usage(agent_id, _unsaved_usage)
        await client.close()
        error_code = (
            "finish_protocol_violation"
            if repair_code == "missing_finish"
            else f"{repair_code}_protocol_violation"
        )
        return (
            f"[Error] {error_code}: The model repeated the {repair_code!r} "
            "tool protocol error after one bounded repair. Native tool calling "
            "is not working for this request."
        )

    # Tool-calling loop
    for round_i in range(_max_tool_rounds):
        # Dynamic tool-call limit warning
        _warn_threshold_80 = int(_max_tool_rounds * 0.8)
        _warn_threshold_96 = _max_tool_rounds - 2
        if round_i == _warn_threshold_80:
            api_messages.append(
                LLMMessage(
                    role="user",
                    content=_tool_round_limit_warning(
                        round_index=round_i,
                        max_rounds=_max_tool_rounds,
                        allowed_tool_names=allowed_tool_names,
                        urgent=False,
                    ),
                )
            )
        elif round_i == _warn_threshold_96:
            api_messages.append(
                LLMMessage(
                    role="user",
                    content=_tool_round_limit_warning(
                        round_index=round_i,
                        max_rounds=_max_tool_rounds,
                        allowed_tool_names=allowed_tool_names,
                        urgent=True,
                    ),
                )
            )

        # Check token usage limit mid-loop (every 3 rounds)
        if round_i > 0 and round_i % 3 == 0:
            if agent_id and _unsaved_usage.total_tokens > 0:
                await record_token_usage(agent_id, _unsaved_usage)
                _unsaved_usage = TokenUsage()
                _, _token_limit_msg = await _get_agent_config(agent_id)
                if _token_limit_msg:
                    logger.warning(f"[LLM] Token limit exceeded mid-loop: {_token_limit_msg}")
                    await client.close()
                    return _token_limit_msg

        try:
            # Use streaming API for real-time responses
            async def _buffer_chunk(_text: str) -> None:
                # Final user-facing text must come through finish(content=...).
                return None

            response = await client.stream(
                messages=api_messages,
                tools=tools_for_llm if tools_for_llm else None,
                temperature=model.temperature,
                max_tokens=max_tokens,
                on_chunk=_buffer_chunk,
                on_tool_delta=on_tool_delta,
                on_thinking=on_thinking,
            )
        except LLMError as e:
            logger.error(f"[LLM] LLMError: provider={getattr(model, 'provider', '?')} model={getattr(model, 'model', '?')} {e}")
            if agent_id and _unsaved_usage.total_tokens > 0:
                await record_token_usage(agent_id, _unsaved_usage)
            await client.close()
            return f"[LLM Error] {e}"
        except Exception as e:
            logger.exception(f"[LLM] Unexpected error: {type(e).__name__}: {str(e)[:300]}")
            if agent_id and _unsaved_usage.total_tokens > 0:
                await record_token_usage(agent_id, _unsaved_usage)
            await client.close()
            return f"[LLM call error] {type(e).__name__}: {str(e)[:200]}"

        # Track tokens for this round
        _usage_this_round = _usage_from_response_or_estimate(response, api_messages)
        _accumulated_usage.add(_usage_this_round)
        _unsaved_usage.add(_usage_this_round)

        # Plain assistant text is not a stop condition. The model must finish
        # explicitly via finish(content=...).
        if not response.tool_calls:
            if response.content:
                api_messages.append(LLMMessage(role="assistant", content=response.content))
            if "missing_finish" in _protocol_repairs:
                return await _protocol_violation("missing_finish")
            api_messages.append(LLMMessage(role="user", content=FINISH_PROTOCOL_REMINDER))
            _protocol_repairs.add("missing_finish")
            continue

        # Execute tool calls
        logger.info(f"[LLM] Round {round_i+1}: {len(response.tool_calls)} tool call(s)")
        sanitized_tool_calls, retry_instruction = _sanitize_tool_calls_for_context(response.tool_calls)
        if retry_instruction:
            if "invalid_tool_call" in _protocol_repairs:
                return await _protocol_violation("invalid_tool_call")
            _protocol_repairs.add("invalid_tool_call")
            api_messages.append(LLMMessage(role="user", content=retry_instruction))
            continue

        finish_call = find_finish_call(sanitized_tool_calls)
        if finish_call:
            if finish_call.valid:
                if agent_id and _unsaved_usage.total_tokens > 0:
                    await record_token_usage(agent_id, _unsaved_usage)
                await client.close()
                return finish_call.content

            if "invalid_finish" in _protocol_repairs:
                return await _protocol_violation("invalid_finish")
            _protocol_repairs.add("invalid_finish")

            api_messages.append(LLMMessage(
                role="assistant",
                content=response.content or None,
                tool_calls=sanitized_tool_calls,
                reasoning_content=response.reasoning_content,
            ))
            api_messages.append(LLMMessage(
                role="tool",
                content=finish_call.error or "`finish` was invalid.",
                tool_call_id=finish_call.call_id,
            ))
            continue

        # Add assistant message with tool calls
        api_messages.append(LLMMessage(
            role="assistant",
            content=response.content or None,
            tool_calls=sanitized_tool_calls,
            reasoning_content=response.reasoning_content,
        ))

        full_reasoning_content = response.reasoning_content or ""

        for tc in sanitized_tool_calls or []:
            tool_error = await _process_tool_call(
                tc=tc,
                api_messages=api_messages,
                agent_id=agent_id,
                user_id=user_id,
                session_id=session_id,
                supports_vision=supports_vision,
                on_tool_call=on_tool_call,
                on_code_output=on_code_output,
                full_reasoning_content=full_reasoning_content,
                allowed_tool_names=allowed_tool_names,
            )
            if tool_error:
                api_messages.append(LLMMessage(
                    role="tool",
                    content=tool_error,
                    tool_call_id=tc.get("id", ""),
                ))

    # Record tokens even on "too many rounds" exit
    if agent_id and _unsaved_usage.total_tokens > 0:
        await record_token_usage(agent_id, _unsaved_usage)
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
    skip_tools: bool = False,
    on_code_output=None,
    current_user_name_override: str | None = None,
    system_prompt_suffix: str | None = None,
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
        skip_tools=skip_tools,
        on_code_output=on_code_output,
        current_user_name_override=current_user_name_override,
        system_prompt_suffix=system_prompt_suffix,
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
        skip_tools=skip_tools,
        on_code_output=on_code_output,
        current_user_name_override=current_user_name_override,
        system_prompt_suffix=system_prompt_suffix,
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


__all__ = [
    "call_llm",
    "call_llm_with_failover",
    "call_agent_llm",
    "FailoverGuard",
    "is_retryable_error",
]
