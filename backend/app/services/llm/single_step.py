"""One-call LLM provider boundary for checkpointed Runtime nodes."""

from __future__ import annotations

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
from .client import LLMMessage
from .utils import create_llm_client, get_max_tokens, get_model_api_key

if TYPE_CHECKING:
    from app.models.llm import LLMModel


@dataclass(frozen=True, slots=True)
class LLMCompletionStep:
    """One normalized provider response with no tool or lifecycle side effects."""

    content: str | None
    tool_calls: tuple[dict, ...]
    reasoning_content: str | None
    retry_instruction: str | None
    usage: TokenUsage


async def complete_llm_once(
    model: LLMModel,
    messages: list[LLMMessage],
    *,
    tools: list[dict] | None = None,
    agent_id: uuid.UUID | None = None,
    supports_vision: bool = False,
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
    try:
        response = await client.complete(
            messages=api_messages,
            tools=tools or None,
            temperature=model.temperature,
            max_tokens=get_max_tokens(
                model.provider,
                model.model,
                getattr(model, "max_output_tokens", None),
            ),
        )
    finally:
        await client.close()

    usage = _usage_from_response_or_estimate(response, api_messages)
    if agent_id is not None and usage.total_tokens > 0:
        await record_token_usage(agent_id, usage)

    sanitized_tool_calls: list[dict] | None = []
    retry_instruction = None
    if response.tool_calls:
        sanitized_tool_calls, retry_instruction = _sanitize_tool_calls_for_context(response.tool_calls)
    return LLMCompletionStep(
        content=response.content,
        tool_calls=tuple(sanitized_tool_calls or ()),
        reasoning_content=response.reasoning_content,
        retry_instruction=retry_instruction,
        usage=usage,
    )


__all__ = ["LLMCompletionStep", "complete_llm_once"]
