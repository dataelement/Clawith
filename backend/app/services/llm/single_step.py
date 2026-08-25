"""One-call LLM provider boundary for checkpointed Runtime nodes."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING
import uuid

from app.services.token_tracker import TokenUsage, record_token_usage

from .caller import (
    _convert_messages_for_vision,
    _get_model_timeout,
    _sanitize_tool_calls_for_context,
    _usage_from_response_or_estimate,
)
from .client import (
    LLMMessage,
    OpenAIResponsesClient,
    extract_embedded_reasoning,
    normalize_llm_finish_reason,
    normalize_textual_tool_protocol,
)
from .utils import create_llm_client, get_max_tokens, get_model_api_key

if TYPE_CHECKING:
    from app.models.llm import LLMModel


VisibleDeltaCallback = Callable[[str], Awaitable[None]]


class _VisibleDeltaGate:
    """Hold protocol-looking prefixes while forwarding ordinary visible text."""

    _PROTOCOL_MARKERS = ("<tool_call", "<tool_result", "<result")
    _TAIL_CHARS = max(len(marker) for marker in _PROTOCOL_MARKERS) - 1

    def __init__(self, callback: VisibleDeltaCallback) -> None:
        self._callback = callback
        self._buffer = ""
        self._forwarding = False
        self._held_protocol = False
        self._blocked_protocol = False

    @staticmethod
    def _must_hold(value: str) -> bool:
        probe = value.lstrip().lower()
        if not probe:
            return True
        if probe[0] in "{[":
            return True
        return "<tool_call>".startswith(probe) or probe.startswith("<tool_call")

    async def push(self, delta: str) -> bool:
        if not delta or self._blocked_protocol:
            return False
        self._buffer += delta
        if not self._forwarding and self._must_hold(self._buffer):
            self._held_protocol = True
            return False
        self._forwarding = True
        lowered = self._buffer.lower()
        marker_positions = [
            position
            for marker in self._PROTOCOL_MARKERS
            if (position := lowered.find(marker)) >= 0
        ]
        if marker_positions:
            position = min(marker_positions)
            safe = self._buffer[:position]
            self._buffer = self._buffer[position:]
            self._blocked_protocol = True
            if safe:
                await self._callback(safe)
                return True
            return False
        if len(self._buffer) <= self._TAIL_CHARS:
            return False
        safe = self._buffer[:-self._TAIL_CHARS]
        self._buffer = self._buffer[-self._TAIL_CHARS :]
        await self._callback(safe)
        return True

    async def finish(
        self,
        *,
        content: str,
        tool_calls: list[dict],
        retry_instruction: str | None,
    ) -> None:
        if self._blocked_protocol:
            self._buffer = ""
            return
        if (
            self._held_protocol
            and not self._forwarding
            and content
            and not tool_calls
            and retry_instruction is None
        ):
            await self._callback(content)
        elif self._forwarding and self._buffer:
            await self._callback(self._buffer)
        self._buffer = ""


@dataclass(frozen=True, slots=True)
class LLMCompletionStep:
    """One normalized provider response with no tool or lifecycle side effects."""

    content: str | None
    tool_calls: tuple[dict, ...]
    reasoning_content: str | None
    retry_instruction: str | None
    usage: TokenUsage
    retry_tool_name: str | None = None
    finish_reason: str | None = None
    visible_streamed: bool = False


async def complete_llm_once(
    model: LLMModel,
    messages: list[LLMMessage],
    *,
    tools: list[dict] | None = None,
    agent_id: uuid.UUID | None = None,
    supports_vision: bool = False,
    max_output_tokens: int | None = None,
    on_visible_delta: VisibleDeltaCallback | None = None,
) -> LLMCompletionStep:
    """Call one pinned model exactly once and normalize its tool proposals.

    This function never executes tools, retries, appends repair prompts, or
    advances a lifecycle. Those decisions belong to the durable Graph.
    """
    api_messages = _convert_messages_for_vision(messages, supports_vision)
    client = create_llm_client(
        provider=model.provider,
        api_key=get_model_api_key(model),
        model=model.model,
        base_url=model.base_url,
        timeout=_get_model_timeout(model),
    )
    max_tokens = get_max_tokens(
        model.provider,
        model.model,
        (
            max_output_tokens
            if max_output_tokens is not None
            else getattr(model, "max_output_tokens", None)
        ),
    )
    delta_gate = (
        _VisibleDeltaGate(on_visible_delta)
        if on_visible_delta and not isinstance(client, OpenAIResponsesClient)
        else None
    )
    try:
        if delta_gate is None:
            response = await client.complete(
                messages=api_messages,
                tools=tools or None,
                temperature=model.temperature,
                max_tokens=max_tokens,
            )
        else:
            response = await client.stream(
                messages=api_messages,
                tools=tools or None,
                temperature=model.temperature,
                max_tokens=max_tokens,
                on_chunk=delta_gate.push,
            )
    finally:
        await client.close()

    usage = _usage_from_response_or_estimate(response, api_messages)
    if agent_id is not None and usage.total_tokens > 0:
        await record_token_usage(agent_id, usage)

    content, reasoning_content = extract_embedded_reasoning(
        response.content,
        response.reasoning_content,
    )
    textual_tool_calls: list[dict] = []
    textual_retry_instruction = None
    if not response.tool_calls:
        content, textual_tool_calls, textual_retry_instruction = (
            normalize_textual_tool_protocol(content, tools)
        )

    proposed_tool_calls = response.tool_calls or textual_tool_calls
    sanitized_tool_calls: list[dict] | None = []
    retry_instruction = None
    retry_tool_name = None
    if proposed_tool_calls:
        sanitized_tool_calls, retry_instruction, retry_tool_name = (
            _sanitize_tool_calls_for_context(proposed_tool_calls)
        )
    if textual_retry_instruction is not None:
        retry_instruction = textual_retry_instruction
        retry_tool_name = None
    if delta_gate is not None:
        await delta_gate.finish(
            content=content or "",
            tool_calls=list(sanitized_tool_calls or ()),
            retry_instruction=retry_instruction,
        )
    return LLMCompletionStep(
        content=content,
        tool_calls=tuple(sanitized_tool_calls or ()),
        reasoning_content=reasoning_content,
        retry_instruction=retry_instruction,
        usage=usage,
        retry_tool_name=retry_tool_name,
        finish_reason=normalize_llm_finish_reason(
            response.finish_reason,
            tuple(sanitized_tool_calls or ()),
        ),
    )


__all__ = ["LLMCompletionStep", "VisibleDeltaCallback", "complete_llm_once"]
