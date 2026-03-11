"""Unified LLM client for multiple providers.

Supports OpenAI-compatible APIs, Anthropic native API, and streaming/non-streaming modes.
Provides a consistent interface for all LLM operations across the application.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Callable, Coroutine, Literal, Protocol

import httpx

logger = logging.getLogger(__name__)


# ============================================================================
# Data Models
# ============================================================================

@dataclass
class LLMMessage:
    """Unified message format."""

    role: Literal["system", "user", "assistant", "tool"]
    content: str | None = None
    tool_calls: list[dict] | None = None
    tool_call_id: str | None = None
    reasoning_content: str | None = None
    reasoning_signature: str | None = None

    def to_openai_format(self) -> dict:
        """Convert to OpenAI format."""
        msg: dict[str, Any] = {"role": self.role}
        if self.content is not None:
            msg["content"] = self.content
        if self.tool_calls:
            msg["tool_calls"] = self.tool_calls
        if self.tool_call_id:
            msg["tool_call_id"] = self.tool_call_id
        if self.reasoning_content:
            msg["reasoning_content"] = self.reasoning_content
        return msg

    def to_anthropic_format(self) -> dict | None:
        """Convert to Anthropic format (returns None for system messages)."""
        if self.role == "system":
            return None
            
        role = self.role
        
        # Tool response (from user to assistant)
        if role == "tool":
            return {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": self.tool_call_id,
                        "content": self.content or ""
                    }
                ]
            }
            
        content_blocks = []
        
        # Add reasoning/thinking content if present
        if self.role == "assistant" and self.reasoning_content:
            content_blocks.append({
                "type": "thinking",
                "thinking": self.reasoning_content,
                "signature": self.reasoning_signature or "synthetic_signature" 
            })

        if self.content:
            content_blocks.append({"type": "text", "text": self.content})
            
        # Tool requests (from assistant to user)
        if self.tool_calls:
            for tc in self.tool_calls:
                function_call = tc.get("function", {})
                args = function_call.get("arguments", "{}")
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except json.JSONDecodeError:
                        args = {}
                
                content_blocks.append({
                    "type": "tool_use",
                    "id": tc.get("id", ""),
                    "name": function_call.get("name", ""),
                    "input": args
                })
                
        # Handle the structure
        if len(content_blocks) == 1 and content_blocks[0]["type"] == "text":
            content = content_blocks[0]["text"]
        else:
            content = content_blocks

        return {"role": role, "content": content}


@dataclass
class LLMResponse:
    """Unified response format."""

    content: str
    tool_calls: list[dict] = field(default_factory=list)
    reasoning_content: str | None = None
    reasoning_signature: str | None = None
    finish_reason: str | None = None
    usage: dict[str, int] | None = None
    model: str | None = None


@dataclass
class LLMStreamChunk:
    """Stream chunk format."""

    content: str = ""
    reasoning_content: str = ""
    tool_call: dict | None = None
    finish_reason: str | None = None
    is_finished: bool = False


# ============================================================================
# Type Definitions
# ============================================================================

ChunkCallback = Callable[[str], Coroutine[Any, Any, None]]
ToolCallback = Callable[[dict], Coroutine[Any, Any, None]]
ThinkingCallback = Callable[[str], Coroutine[Any, Any, None]]


# ============================================================================
# Base Client Interface
# ============================================================================

class LLMClient(ABC):
    """Abstract base class for LLM clients."""

    def __init__(
        self,
        api_key: str,
        base_url: str | None = None,
        model: str | None = None,
        timeout: float = 120.0,
    ):
        self.api_key = api_key
        self.base_url = base_url
        self.model = model
        self.timeout = timeout

    @abstractmethod
    async def complete(
        self,
        messages: list[LLMMessage],
        tools: list[dict] | None = None,
        temperature: float = 0.7,
        max_tokens: int | None = None,
        **kwargs: Any,
    ) -> LLMResponse:
        """Send a completion request and return the full response."""
        pass

    @abstractmethod
    async def stream(
        self,
        messages: list[LLMMessage],
        tools: list[dict] | None = None,
        temperature: float = 0.7,
        max_tokens: int | None = None,
        on_chunk: ChunkCallback | None = None,
        on_thinking: ThinkingCallback | None = None,
        **kwargs: Any,
    ) -> LLMResponse:
        """Send a streaming request and return the aggregated response."""
        pass

    @abstractmethod
    def _get_headers(self) -> dict[str, str]:
        """Get request headers."""
        pass


# ============================================================================
# OpenAI-Compatible Client
# ============================================================================

class OpenAICompatibleClient(LLMClient):
    """Client for OpenAI-compatible APIs (OpenAI, DeepSeek, Qwen, etc.)."""

    DEFAULT_BASE_URL = "https://api.openai.com/v1"

    def __init__(
        self,
        api_key: str,
        base_url: str | None = None,
        model: str | None = None,
        timeout: float = 120.0,
        supports_tool_choice: bool = True,
    ):
        super().__init__(api_key, base_url or self.DEFAULT_BASE_URL, model, timeout)
        self.supports_tool_choice = supports_tool_choice
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create HTTP client."""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=self.timeout, follow_redirects=True)
        return self._client

    def _get_headers(self) -> dict[str, str]:
        return {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }

    def _normalize_base_url(self) -> str:
        """Normalize base URL by stripping trailing /chat/completions."""
        url = self.base_url.rstrip("/")
        if url.endswith("/chat/completions"):
            url = url[: -len("/chat/completions")]
        return url

    def _build_payload(
        self,
        messages: list[LLMMessage],
        tools: list[dict] | None,
        temperature: float,
        max_tokens: int | None,
        stream: bool = False,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Build request payload."""
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [m.to_openai_format() for m in messages],
            "temperature": temperature,
            "stream": stream,
        }

        if max_tokens:
            payload["max_tokens"] = max_tokens

        if tools:
            payload["tools"] = tools
            if self.supports_tool_choice:
                payload["tool_choice"] = "auto"
                payload["parallel_tool_calls"] = True

        # Add any additional kwargs
        payload.update(kwargs)

        return payload

    def _parse_stream_line(
        self,
        line: str,
        in_think: bool,
        tag_buffer: str,
    ) -> tuple[LLMStreamChunk, bool, str]:
        """Parse a single SSE line from stream.

        Returns (chunk, new_in_think, new_tag_buffer).
        """
        chunk = LLMStreamChunk()

        if not line.startswith("data: "):
            return chunk, in_think, tag_buffer

        data_str = line[6:].strip()
        if data_str == "[DONE]":
            chunk.is_finished = True
            return chunk, in_think, tag_buffer

        try:
            data = json.loads(data_str)
        except json.JSONDecodeError:
            return chunk, in_think, tag_buffer

        if "error" in data:
            raise LLMError(f"Stream error: {data['error']}")

        choices = data.get("choices", [])
        if not choices:
            return chunk, in_think, tag_buffer

        choice = choices[0]
        delta = choice.get("delta", {})

        if choice.get("finish_reason"):
            chunk.finish_reason = choice["finish_reason"]

        # Reasoning content (DeepSeek R1)
        if delta.get("reasoning_content"):
            chunk.reasoning_content = delta["reasoning_content"]

        # Regular content with think tag filtering
        if delta.get("content"):
            text = delta["content"]
            chunk.content, in_think, tag_buffer = self._filter_think_tags(
                text, in_think, tag_buffer
            )

        # Tool calls
        if delta.get("tool_calls"):
            for tc_delta in delta["tool_calls"]:
                chunk.tool_call = tc_delta
                break  # Return one at a time

        return chunk, in_think, tag_buffer

    def _filter_think_tags(
        self, text: str, in_think: bool, tag_buffer: str
    ) -> tuple[str, bool, str]:
        """Filter out <think>...</think> tags from content.

        Returns (filtered_content, new_in_think, new_tag_buffer).
        """
        tag_buffer += text
        emit = ""
        i = 0
        buf = tag_buffer

        while i < len(buf):
            if not in_think:
                # Look for <think open tag
                if buf[i] == "<":
                    tag_candidate = buf[i:]
                    if tag_candidate.startswith("<think>"):
                        in_think = True
                        i += len("<think>")
                        continue
                    elif "<think>".startswith(tag_candidate):
                        # Partial match - keep in buffer
                        break
                    else:
                        emit += buf[i]
                        i += 1
                else:
                    emit += buf[i]
                    i += 1
            else:
                # Inside think - look for </think> close tag
                if buf[i] == "<":
                    tag_candidate = buf[i:]
                    if tag_candidate.startswith("</think>"):
                        in_think = False
                        i += len("</think>")
                        continue
                    elif "</think>".startswith(tag_candidate):
                        break
                i += 1

        tag_buffer = buf[i:]
        return emit, in_think, tag_buffer

    async def complete(
        self,
        messages: list[LLMMessage],
        tools: list[dict] | None = None,
        temperature: float = 0.7,
        max_tokens: int | None = None,
        **kwargs: Any,
    ) -> LLMResponse:
        """Non-streaming completion."""
        url = f"{self._normalize_base_url()}/chat/completions"
        payload = self._build_payload(messages, tools, temperature, max_tokens, stream=False, **kwargs)

        client = await self._get_client()
        response = await client.post(url, json=payload, headers=self._get_headers())

        if response.status_code >= 400:
            error_text = response.text[:500]
            raise LLMError(f"HTTP {response.status_code}: {error_text}")

        data = response.json()

        if "error" in data:
            raise LLMError(f"API error: {data['error']}")

        choice = data.get("choices", [{}])[0]
        msg = choice.get("message", {})

        return LLMResponse(
            content=msg.get("content", ""),
            tool_calls=msg.get("tool_calls", []),
            finish_reason=choice.get("finish_reason"),
            usage=data.get("usage"),
            model=data.get("model"),
        )

    async def stream(
        self,
        messages: list[LLMMessage],
        tools: list[dict] | None = None,
        temperature: float = 0.7,
        max_tokens: int | None = None,
        on_chunk: ChunkCallback | None = None,
        on_thinking: ThinkingCallback | None = None,
        **kwargs: Any,
    ) -> LLMResponse:
        """Streaming completion."""
        url = f"{self._normalize_base_url()}/chat/completions"
        payload = self._build_payload(messages, tools, temperature, max_tokens, stream=True, **kwargs)

        full_content = ""
        full_reasoning = ""
        tool_calls_data: list[dict] = []
        last_finish_reason: str | None = None

        in_think = False
        tag_buffer = ""

        max_retries = 3
        client = await self._get_client()

        for attempt in range(max_retries):
            try:
                async with client.stream("POST", url, json=payload, headers=self._get_headers()) as resp:
                    if resp.status_code >= 400:
                        error_body = ""
                        async for chunk in resp.aiter_bytes():
                            error_body += chunk.decode(errors="replace")
                        raise LLMError(f"HTTP {resp.status_code}: {error_body[:500]}")

                    async for line in resp.aiter_lines():
                        chunk, in_think, tag_buffer = self._parse_stream_line(
                            line, in_think, tag_buffer
                        )

                        if chunk.is_finished:
                            break

                        if chunk.content and on_chunk:
                            await on_chunk(chunk.content)
                            full_content += chunk.content

                        if chunk.reasoning_content:
                            full_reasoning += chunk.reasoning_content
                            if on_thinking:
                                await on_thinking(chunk.reasoning_content)

                        if chunk.tool_call:
                            idx = chunk.tool_call.get("index", 0)
                            while len(tool_calls_data) <= idx:
                                tool_calls_data.append({"id": "", "function": {"name": "", "arguments": ""}})
                            tc = tool_calls_data[idx]
                            if chunk.tool_call.get("id"):
                                tc["id"] = chunk.tool_call["id"]
                            fn_delta = chunk.tool_call.get("function", {})
                            if fn_delta.get("name"):
                                tc["function"]["name"] += fn_delta["name"]
                            if fn_delta.get("arguments") is not None:
                                arg_chunk = fn_delta["arguments"]
                                if isinstance(arg_chunk, dict):
                                    tc["function"]["arguments"] = json.dumps(arg_chunk, ensure_ascii=False)
                                else:
                                    tc["function"]["arguments"] += str(arg_chunk)

                        if chunk.finish_reason:
                            last_finish_reason = chunk.finish_reason

                break  # Success

            except (httpx.ConnectError, httpx.ReadError, httpx.ConnectTimeout) as e:
                if attempt < max_retries - 1:
                    wait = (attempt + 1) * 1
                    logger.warning(f"Stream attempt {attempt + 1} failed ({type(e).__name__}), retrying in {wait}s...")
                    await asyncio.sleep(wait)
                    full_content = ""
                    full_reasoning = ""
                    tool_calls_data = []
                    in_think = False
                    tag_buffer = ""
                else:
                    raise LLMError(f"Connection failed after {max_retries} attempts: {e}")

        # Clean up any remaining think tags
        full_content = re.sub(r"<think>[\s\S]*?</think>\s*", "", full_content).strip()

        return LLMResponse(
            content=full_content,
            tool_calls=tool_calls_data,
            reasoning_content=full_reasoning or None,
            finish_reason=last_finish_reason,
            model=self.model,
        )

    async def close(self) -> None:
        """Close the HTTP client."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()


# ============================================================================
# Anthropic Native Client
# ============================================================================

class AnthropicClient(LLMClient):
    """Client for Anthropic's native Messages API.
    
    Supports Claude 3.x and Claude 3.7+ with extended thinking.
    """

    DEFAULT_BASE_URL = "https://api.anthropic.com"
    API_VERSION = "2023-06-01"

    def __init__(
        self,
        api_key: str,
        base_url: str | None = None,
        model: str | None = None,
        timeout: float = 120.0,
    ):
        super().__init__(api_key, base_url or self.DEFAULT_BASE_URL, model, timeout)
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create HTTP client."""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=self.timeout, follow_redirects=True)
        return self._client

    def _get_headers(self) -> dict[str, str]:
        return {
            "Content-Type": "application/json",
            "x-api-key": self.api_key,
            "anthropic-version": self.API_VERSION,
        }

    def _build_payload(
        self,
        messages: list[LLMMessage],
        tools: list[dict] | None,
        temperature: float,
        max_tokens: int | None,
        stream: bool = False,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Build Anthropic request payload."""
        system_content = None
        anthropic_messages = []

        for msg in messages:
            if msg.role == "system":
                system_content = msg.content
            else:
                formatted = msg.to_anthropic_format()
                if formatted:
                    anthropic_messages.append(formatted)

        payload: dict[str, Any] = {
            "model": self.model,
            "messages": anthropic_messages,
            "max_tokens": max_tokens or 4096,
            "temperature": temperature,
            "stream": stream,
        }

        if system_content:
            payload["system"] = system_content

        # Handle Extended Thinking
        thinking = kwargs.pop("thinking", None)
        if thinking:
            payload["thinking"] = thinking
            # For thinking models, temperature must be 1.0 or omitted in some cases
            # But usually it's best to let user specify or default to 1.0 if not set
            if "temperature" not in kwargs:
                payload["temperature"] = 1.0

        if tools:
            anthropic_tools = []
            for tool in tools:
                if tool.get("type") == "function":
                    func = tool["function"]
                    anthropic_tools.append({
                        "name": func["name"],
                        "description": func.get("description", ""),
                        "input_schema": func.get("parameters", {"type": "object"}),
                    })
            payload["tools"] = anthropic_tools

        payload.update(kwargs)
        return payload

    async def complete(
        self,
        messages: list[LLMMessage],
        tools: list[dict] | None = None,
        temperature: float = 0.7,
        max_tokens: int | None = None,
        **kwargs: Any,
    ) -> LLMResponse:
        """Non-streaming completion."""
        url = f"{self.base_url.rstrip('/')}/v1/messages"
        payload = self._build_payload(messages, tools, temperature, max_tokens, stream=False, **kwargs)

        client = await self._get_client()
        response = await client.post(url, json=payload, headers=self._get_headers())

        if response.status_code >= 400:
            error_text = response.text[:500]
            raise LLMError(f"HTTP {response.status_code}: {error_text}")

        data = response.json()
        if data.get("type") == "error":
            raise LLMError(f"API error: {data.get('error', {})}")

        full_content = ""
        full_reasoning = ""
        full_signature = None
        tool_calls = []
        
        for block in data.get("content", []):
            if block.get("type") == "text":
                full_content += block.get("text", "")
            elif block.get("type") == "thinking":
                full_reasoning += block.get("thinking", "")
                full_signature = block.get("signature")
            elif block.get("type") == "tool_use":
                tool_calls.append({
                    "id": block.get("id"),
                    "type": "function",
                    "function": {
                        "name": block.get("name"),
                        "arguments": json.dumps(block.get("input", {}), ensure_ascii=False)
                    }
                })

        usage = None
        if "usage" in data:
            usage = {
                "input_tokens": data["usage"].get("input_tokens", 0),
                "output_tokens": data["usage"].get("output_tokens", 0),
            }

        return LLMResponse(
            content=full_content,
            tool_calls=tool_calls,
            reasoning_content=full_reasoning or None,
            reasoning_signature=full_signature,
            finish_reason=data.get("stop_reason"),
            usage=usage,
            model=data.get("model"),
        )

    async def stream(
        self,
        messages: list[LLMMessage],
        tools: list[dict] | None = None,
        temperature: float = 0.7,
        max_tokens: int | None = None,
        on_chunk: ChunkCallback | None = None,
        on_thinking: ThinkingCallback | None = None,
        **kwargs: Any,
    ) -> LLMResponse:
        """Streaming completion."""
        url = f"{self.base_url.rstrip('/')}/v1/messages"
        payload = self._build_payload(messages, tools, temperature, max_tokens, stream=True, **kwargs)

        full_content = ""
        full_reasoning = ""
        full_signature = None
        tool_calls_data: list[dict] = []
        tool_call_index_map: dict[int, int] = {}
        last_finish_reason: str | None = None
        final_usage = None
        final_model = self.model

        client = await self._get_client()
        
        try:
            async with client.stream("POST", url, json=payload, headers=self._get_headers()) as resp:
                if resp.status_code >= 400:
                    error_body = ""
                    async for chunk in resp.aiter_bytes():
                        error_body += chunk.decode(errors="replace")
                    raise LLMError(f"HTTP {resp.status_code}: {error_body[:500]}")

                current_event = None
                
                async for line in resp.aiter_lines():
                    if not line.strip():
                        continue
                        
                    if line.startswith("event:"):
                        current_event = line[len("event:"):].strip()
                        continue
                        
                    if not line.startswith("data:"):
                        continue
                        
                    data_str = line[len("data:"):].strip()
                    if data_str == "[DONE]":
                        break
                        
                    try:
                        data = json.loads(data_str)
                    except json.JSONDecodeError:
                        continue

                    # Handle events
                    if current_event == "message_start":
                        msg = data.get("message", {})
                        if msg.get("model"):
                            final_model = msg["model"]
                        if msg.get("usage"):
                            final_usage = msg["usage"]
                            
                    elif current_event == "content_block_start":
                        block = data.get("content_block", {})
                        idx = data.get("index", 0)
                        if block.get("type") == "tool_use":
                            tool_call_index_map[idx] = len(tool_calls_data)
                            tool_calls_data.append({
                                "id": block.get("id"),
                                "type": "function",
                                "function": {"name": block.get("name"), "arguments": ""}
                            })
                            
                    elif current_event == "content_block_delta":
                        idx = data.get("index", 0)
                        delta = data.get("delta", {})
                        delta_type = delta.get("type")
                        
                        if delta_type == "text_delta":
                            text = delta.get("text", "")
                            full_content += text
                            if on_chunk:
                                await on_chunk(text)
                                
                        elif delta_type == "thinking_delta":
                            thought = delta.get("thinking", "")
                            full_reasoning += thought
                            if on_thinking:
                                await on_thinking(thought)
                        
                        elif delta_type == "signature_delta":
                            full_signature = delta.get("signature")
                                
                        elif delta_type == "input_json_delta":
                            if idx in tool_call_index_map:
                                tc_idx = tool_call_index_map[idx]
                                tool_calls_data[tc_idx]["function"]["arguments"] += delta.get("partial_json", "")
                                
                    elif current_event == "message_delta":
                        delta = data.get("delta", {})
                        if delta.get("stop_reason"):
                            last_finish_reason = delta["stop_reason"]
                        if data.get("usage"):
                            # message_delta usage is cumulative
                            final_usage = data["usage"]
                            
                    elif current_event == "error":
                        error_info = data.get("error", {})
                        raise LLMError(f"Anthropic stream error ({error_info.get('type')}): {error_info.get('message')}")

                    elif current_event == "message_stop":
                        break

        except (httpx.ConnectError, httpx.ReadError, httpx.ConnectTimeout) as e:
            raise LLMError(f"Connection failed: {e}")

        # Normalize stop reason to OpenAI style (optional but helpful for consistency)
        if last_finish_reason == "end_turn":
            last_finish_reason = "stop"
        elif last_finish_reason == "tool_use":
            last_finish_reason = "tool_calls"

        return LLMResponse(
            content=full_content,
            tool_calls=tool_calls_data,
            reasoning_content=full_reasoning or None,
            reasoning_signature=full_signature,
            finish_reason=last_finish_reason,
            usage=final_usage,
            model=final_model,
        )

    async def close(self) -> None:
        """Close the HTTP client."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()


# ============================================================================
# Factory and Utilities
# ============================================================================

# Provider to client class mapping
PROVIDER_CLIENTS: dict[str, type[LLMClient]] = {
    "openai": OpenAICompatibleClient,
    "anthropic": AnthropicClient,
    "deepseek": OpenAICompatibleClient,
    "qwen": OpenAICompatibleClient,
    "minimax": OpenAICompatibleClient,
    "openrouter": OpenAICompatibleClient,
    "custom": OpenAICompatibleClient,
}

# Default base URLs for providers
PROVIDER_URLS: dict[str, str | None] = {
    "openai": "https://api.openai.com/v1",
    "anthropic": "https://api.anthropic.com",
    "deepseek": "https://api.deepseek.com/v1",
    "qwen": "https://dashscope.aliyuncs.com/compatible-mode/v1",
    "minimax": "https://api.minimaxi.com/v1",
    "openrouter": "https://openrouter.ai/api/v1",
    "custom": None,
}

# Providers that support tool_choice
TOOL_CHOICE_PROVIDERS = {"openai", "qwen", "deepseek", "minimax", "openrouter", "custom"}

# Max tokens by provider
MAX_TOKENS_BY_PROVIDER: dict[str, int] = {
    "qwen": 8192,
    "anthropic": 4096,
    "minimax": 16384,
}

# Max tokens by model prefix
MAX_TOKENS_BY_MODEL: dict[str, int] = {
    "qwen-plus": 16384,
    "qwen-long": 16384,
    "qwen-turbo": 8192,
    "qwen-max": 8192,
}


class LLMError(Exception):
    """Base exception for LLM client errors."""
    pass


def get_provider_base_url(provider: str, custom_base_url: str | None = None) -> str | None:
    """Return the API base URL for a provider.

    If a custom base_url is provided, it takes precedence.
    Otherwise falls back to the default URL for the provider.
    """
    if custom_base_url:
        return custom_base_url
    return PROVIDER_URLS.get(provider)


def get_max_tokens(provider: str, model: str | None = None) -> int:
    """Return a safe max_tokens value for the given provider/model pair."""
    if model:
        for prefix, limit in MAX_TOKENS_BY_MODEL.items():
            if model.lower().startswith(prefix):
                return limit
    return MAX_TOKENS_BY_PROVIDER.get(provider, 16384)


def create_llm_client(
    provider: str,
    api_key: str,
    model: str,
    base_url: str | None = None,
    timeout: float = 120.0,
) -> LLMClient:
    """Create an LLM client for the given provider.

    Args:
        provider: Provider name (openai, anthropic, deepseek, etc.)
        api_key: API key for authentication
        model: Model name
        base_url: Optional custom base URL
        timeout: Request timeout in seconds

    Returns:
        An instance of the appropriate LLMClient subclass

    Raises:
        ValueError: If provider is not supported
    """
    # Get base URL
    final_base_url = get_provider_base_url(provider, base_url)

    # Create appropriate client
    if provider == "anthropic":
        return AnthropicClient(
            api_key=api_key,
            base_url=final_base_url,
            model=model,
            timeout=timeout,
        )
    elif provider in PROVIDER_CLIENTS:
        supports_tool_choice = provider in TOOL_CHOICE_PROVIDERS
        return OpenAICompatibleClient(
            api_key=api_key,
            base_url=final_base_url,
            model=model,
            timeout=timeout,
            supports_tool_choice=supports_tool_choice,
        )
    else:
        # Default to OpenAI-compatible for unknown providers
        return OpenAICompatibleClient(
            api_key=api_key,
            base_url=final_base_url or PROVIDER_URLS["openai"],
            model=model,
            timeout=timeout,
            supports_tool_choice=True,
        )


# ============================================================================
# High-level Convenience Functions
# ============================================================================

async def chat_complete(
    provider: str,
    api_key: str,
    model: str,
    messages: list[dict],
    base_url: str | None = None,
    tools: list[dict] | None = None,
    temperature: float = 0.7,
    max_tokens: int | None = None,
    timeout: float = 120.0,
) -> dict:
    """High-level function for non-streaming chat completion.

    Returns response in OpenAI-compatible format for backward compatibility.
    """
    client = create_llm_client(provider, api_key, model, base_url, timeout)

    try:
        llm_messages = [LLMMessage(**m) for m in messages]
        response = await client.complete(
            messages=llm_messages,
            tools=tools,
            temperature=temperature,
            max_tokens=max_tokens or get_max_tokens(provider, model),
        )

        return {
            "choices": [{
                "message": {
                    "role": "assistant",
                    "content": response.content,
                    "tool_calls": response.tool_calls or None,
                },
                "finish_reason": response.finish_reason or "stop",
            }],
            "model": response.model or model,
            "usage": response.usage or {},
        }
    finally:
        await client.close()


async def chat_stream(
    provider: str,
    api_key: str,
    model: str,
    messages: list[dict],
    base_url: str | None = None,
    tools: list[dict] | None = None,
    temperature: float = 0.7,
    max_tokens: int | None = None,
    timeout: float = 120.0,
    on_chunk: ChunkCallback | None = None,
    on_thinking: ThinkingCallback | None = None,
) -> dict:
    """High-level function for streaming chat completion.

    Returns aggregated response in OpenAI-compatible format.
    """
    client = create_llm_client(provider, api_key, model, base_url, timeout)

    try:
        llm_messages = [LLMMessage(**m) for m in messages]
        response = await client.stream(
            messages=llm_messages,
            tools=tools,
            temperature=temperature,
            max_tokens=max_tokens or get_max_tokens(provider, model),
            on_chunk=on_chunk,
            on_thinking=on_thinking,
        )

        return {
            "choices": [{
                "message": {
                    "role": "assistant",
                    "content": response.content,
                    "tool_calls": response.tool_calls or None,
                },
                "finish_reason": response.finish_reason or "stop",
            }],
            "model": response.model or model,
            "usage": response.usage or {},
        }
    finally:
        await client.close()
