"""Unified LLM client for multiple providers.

Supports OpenAI-compatible APIs, Anthropic native API, and streaming/non-streaming modes.
Provides a consistent interface for all LLM operations across the application.
"""

from __future__ import annotations

import asyncio
import json
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable, Coroutine, Literal

import httpx
from loguru import logger


# ============================================================================
# Data Models
# ============================================================================

@dataclass
class LLMMessage:
    """Unified message format."""

    role: Literal["system", "user", "assistant", "tool"]
    content: str | list | None = None
    tool_calls: list[dict] | None = None
    tool_call_id: str | None = None
    reasoning_content: str | None = None
    reasoning_signature: str | None = None
    dynamic_content: str | None = None

    def to_openai_format(self) -> dict:
        """Convert to OpenAI format."""
        msg: dict[str, Any] = {"role": self.role}
        
        content = self.content
        if self.role == "system" and self.dynamic_content:
            content = f"{content}\n\n{self.dynamic_content}"
            
        if content is not None:
            msg["content"] = content
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
            # Build tool_result content: support both string and vision array formats
            if isinstance(self.content, list):
                # Vision content array: extract text parts and image parts
                # Anthropic tool_result content supports [{type: "text", text: ...}, {type: "image", source: ...}]
                tool_content_blocks = []
                for part in self.content:
                    if part.get("type") == "text":
                        tool_content_blocks.append({"type": "text", "text": part.get("text", "")})
                    elif part.get("type") == "image_url":
                        # Convert OpenAI image_url format to Anthropic image source format
                        img_url = part.get("image_url", {}).get("url", "")
                        if img_url.startswith("data:image/"):
                            # Parse data URL: data:image/jpeg;base64,xxxxx
                            header, b64_data = img_url.split(",", 1)
                            media_type = header.split(":")[1].split(";")[0]  # e.g. image/jpeg
                            tool_content_blocks.append({
                                "type": "image",
                                "source": {
                                    "type": "base64",
                                    "media_type": media_type,
                                    "data": b64_data,
                                }
                            })
                result_content = tool_content_blocks if tool_content_blocks else (self.content or "")
            else:
                result_content = self.content or ""
            return {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": self.tool_call_id,
                        "content": result_content,
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
            if isinstance(self.content, list):
                for part in self.content:
                    if part.get("type") == "text":
                        content_blocks.append({"type": "text", "text": part.get("text", "")})
                    elif part.get("type") == "image_url":
                        img_url = part.get("image_url", {}).get("url", "")
                        if img_url.startswith("data:image/"):
                            header, b64_data = img_url.split(",", 1)
                            media_type = header.split(":")[1].split(";")[0]
                            content_blocks.append({
                                "type": "image",
                                "source": {
                                    "type": "base64",
                                    "media_type": media_type,
                                    "data": b64_data,
                                }
                            })
            else:
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
    usage: dict | None = None


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
        temperature: float | None = None,
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
        temperature: float | None = None,
        max_tokens: int | None = None,
        on_chunk: ChunkCallback | None = None,
        on_tool_delta: ToolCallback | None = None,
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
        supports_cache_control: bool = False,
    ):
        super().__init__(api_key, base_url or self.DEFAULT_BASE_URL, model, timeout)
        self.supports_tool_choice = supports_tool_choice
        self.supports_cache_control = supports_cache_control
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create HTTP client."""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=self.timeout, follow_redirects=True, proxy=None)
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
        temperature: float | None,
        max_tokens: int | None,
        stream: bool = False,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Build request payload."""
        messages_payload = self._messages_to_openai_payload(messages)
        logger.debug(f"[LLM-Debug] OpenAICompatibleClient payload messages for model {self.model}: {json.dumps(messages_payload, indent=2, ensure_ascii=False)}")
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages_payload,
            "stream": stream,
        }
        if temperature is not None:
            payload["temperature"] = temperature

        # Request usage stats in streaming responses (OpenAI extension)
        if stream:
            payload["stream_options"] = {"include_usage": True}

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

    def _messages_to_openai_payload(self, messages: list[LLMMessage]) -> list[dict[str, Any]]:
        """Convert messages, optionally adding DashScope/OpenAI-compatible cache hints."""
        if not self.supports_cache_control:
            return [m.to_openai_format() for m in messages]

        payload: list[dict[str, Any]] = []
        last_user_index = -1

        for msg in messages:
            if msg.role == "system":
                formatted: dict[str, Any] = {"role": "system"}
                content_blocks: list[dict[str, Any]] = []

                if isinstance(msg.content, str) and msg.content:
                    content_blocks.append({
                        "type": "text",
                        "text": msg.content,
                        "cache_control": {"type": "ephemeral"},
                    })
                elif isinstance(msg.content, list):
                    content_blocks = [dict(part) for part in msg.content if isinstance(part, dict)]
                    self._mark_last_text_block_cacheable(content_blocks)

                if msg.dynamic_content:
                    content_blocks.append({
                        "type": "text",
                        "text": f"\n\n{msg.dynamic_content}",
                    })

                if content_blocks:
                    formatted["content"] = content_blocks
                if msg.tool_calls:
                    formatted["tool_calls"] = msg.tool_calls
                payload.append(formatted)
                continue

            formatted = msg.to_openai_format()
            payload.append(formatted)
            if msg.role == "user":
                last_user_index = len(payload) - 1

        if last_user_index >= 0:
            payload[last_user_index] = self._with_cache_control_on_message(payload[last_user_index])

        return payload

    def _with_cache_control_on_message(self, message: dict[str, Any]) -> dict[str, Any]:
        content = message.get("content")
        if isinstance(content, str) and content:
            message = dict(message)
            message["content"] = [{
                "type": "text",
                "text": content,
                "cache_control": {"type": "ephemeral"},
            }]
            return message
        if isinstance(content, list):
            blocks = [dict(part) for part in content if isinstance(part, dict)]
            if self._mark_last_text_block_cacheable(blocks):
                message = dict(message)
                message["content"] = blocks
        return message

    def _mark_last_text_block_cacheable(self, blocks: list[dict[str, Any]]) -> bool:
        for part in reversed(blocks):
            if part.get("type") == "text" and part.get("text"):
                part["cache_control"] = {"type": "ephemeral"}
                return True
        return False

    def _parse_stream_line(
        self,
        line: str,
        in_think: bool,
        tag_buffer: str,
        json_buffer: str = "",
    ) -> tuple[LLMStreamChunk, bool, str, str]:
        """Parse a single SSE line from stream.

        Returns (chunk, new_in_think, new_tag_buffer, new_json_buffer).
        The json_buffer accumulates partial JSON from non-standard APIs that
        split a single JSON object across multiple data: lines.
        """
        chunk = LLMStreamChunk()

        # SSE spec: "data:" may or may not have a space after the colon
        if line.startswith("data: "):
            data_str = line[6:]
        elif line.startswith("data:"):
            data_str = line[5:]
        else:
            # Non-data lines (comments, event types, empty) — never buffer
            return chunk, in_think, tag_buffer, json_buffer

        data_str = data_str.strip()
        if not data_str:
            return chunk, in_think, tag_buffer, json_buffer

        if data_str == "[DONE]":
            chunk.is_finished = True
            return chunk, in_think, tag_buffer, ""

        # Accumulate into json_buffer for split JSON handling
        if json_buffer:
            json_buffer += data_str
        else:
            json_buffer = data_str

        try:
            data = json.loads(json_buffer)
            json_buffer = ""  # Reset on successful parse
        except json.JSONDecodeError:
            # Cap buffer at 64KB to prevent memory leaks
            if len(json_buffer) > 65536:
                logger.warning("[LLM] JSON buffer exceeded 64KB, discarding")
                json_buffer = ""
            return chunk, in_think, tag_buffer, json_buffer

        if "error" in data:
            raise LLMError(f"Stream error: {data['error']}")

        # Parse usage from stream (returned in the final chunk with include_usage)
        if data.get("usage"):
            chunk.usage = data["usage"]

        choices = data.get("choices", [])
        if not choices:
            return chunk, in_think, tag_buffer, json_buffer

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

        return chunk, in_think, tag_buffer, json_buffer

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
        temperature: float | None = None,
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
        temperature: float | None = None,
        max_tokens: int | None = None,
        on_chunk: ChunkCallback | None = None,
        on_tool_delta: ToolCallback | None = None,
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
        final_usage: dict | None = None

        in_think = False
        tag_buffer = ""
        json_buffer = ""  # Buffer for non-standard APIs with split JSON (inspired by PR #120)

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
                        chunk, in_think, tag_buffer, json_buffer = self._parse_stream_line(
                            line, in_think, tag_buffer, json_buffer
                        )

                        if chunk.is_finished:
                            break

                        if chunk.content:
                            full_content += chunk.content
                            if on_chunk:
                                await on_chunk(chunk.content)

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
                            if on_tool_delta and (
                                tc["function"].get("name")
                                or tc["function"].get("arguments")
                            ):
                                await on_tool_delta(
                                    {
                                        "id": tc.get("id") or f"draft-{idx}",
                                        "index": idx,
                                        "name": tc["function"].get("name", ""),
                                        "arguments": tc["function"].get("arguments", ""),
                                    }
                                )

                        if chunk.usage:
                            final_usage = chunk.usage

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
                    json_buffer = ""
                else:
                    raise LLMError(f"Connection failed after {max_retries} attempts: {e}")

        # Clean up any remaining think tags
        full_content = re.sub(r"<think>[\s\S]*?</think>\s*", "", full_content).strip()

        return LLMResponse(
            content=full_content,
            tool_calls=tool_calls_data,
            reasoning_content=full_reasoning or None,
            finish_reason=last_finish_reason,
            usage=final_usage,
            model=self.model,
        )

    async def close(self) -> None:
        """Close the HTTP client."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()


# ============================================================================
# OpenAI Responses API Client
# ============================================================================

class OpenAIResponsesClient(LLMClient):
    """Client for OpenAI Responses API (`/v1/responses`)."""

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
            self._client = httpx.AsyncClient(timeout=self.timeout, follow_redirects=True, proxy=None)
        return self._client

    def _get_headers(self) -> dict[str, str]:
        return {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }

    def _normalize_base_url(self) -> str:
        """Normalize base URL by stripping trailing /responses endpoint."""
        url = self.base_url.rstrip("/")
        if url.endswith("/responses"):
            url = url[: -len("/responses")]
        return url

    def _format_content_for_input(self, content: Any) -> Any:
        """Convert OpenAI chat-style content into Responses API input content."""
        if not isinstance(content, list):
            return content

        formatted: list[dict[str, Any]] = []
        for part in content:
            if not isinstance(part, dict):
                continue
            ptype = part.get("type")
            if ptype == "text":
                formatted.append({"type": "input_text", "text": part.get("text", "")})
            elif ptype == "image_url":
                img = part.get("image_url", {})
                if isinstance(img, dict):
                    formatted.append({"type": "input_image", "image_url": img.get("url", "")})
            else:
                formatted.append(part)
        return formatted if formatted else content

    def _messages_to_input(self, messages: list[LLMMessage]) -> list[dict[str, Any]]:
        """Convert canonical message format to Responses API input format."""
        input_items: list[dict[str, Any]] = []

        for msg in messages:
            if msg.role in {"system", "user", "assistant"} and msg.content is not None:
                item: dict[str, Any] = {"role": msg.role}
                item["content"] = self._format_content_for_input(msg.content)
                input_items.append(item)

            if msg.role == "assistant" and msg.tool_calls:
                for tc in msg.tool_calls:
                    fn = tc.get("function", {})
                    args = fn.get("arguments", "{}")
                    if isinstance(args, dict):
                        args = json.dumps(args, ensure_ascii=False)
                    input_items.append({
                        "type": "function_call",
                        "call_id": tc.get("id", ""),
                        "name": fn.get("name", ""),
                        "arguments": str(args or "{}"),
                    })

            if msg.role == "tool":
                input_items.append({
                    "type": "function_call_output",
                    "call_id": msg.tool_call_id or "",
                    "output": msg.content or "",
                })

        return input_items

    def _convert_tools(self, tools: list[dict] | None) -> list[dict] | None:
        """Convert OpenAI tool schema to Responses API function tool schema."""
        if not tools:
            return None

        converted: list[dict[str, Any]] = []
        for tool in tools:
            if tool.get("type") != "function":
                continue
            fn = tool.get("function", {})
            converted.append({
                "type": "function",
                "name": fn.get("name", ""),
                "description": fn.get("description", ""),
                "parameters": fn.get("parameters", {"type": "object"}),
            })
        return converted or None

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
            "input": self._messages_to_input(messages),
            "temperature": temperature,
            "stream": stream,
        }

        if max_tokens:
            payload["max_output_tokens"] = max_tokens

        converted_tools = self._convert_tools(tools)
        if converted_tools:
            payload["tools"] = converted_tools
            if self.supports_tool_choice:
                payload["tool_choice"] = "auto"

        payload.update(kwargs)
        return payload

    def _parse_response_data(self, data: dict[str, Any]) -> LLMResponse:
        """Convert Responses API payload into canonical LLMResponse."""
        content_parts: list[str] = []
        reasoning_parts: list[str] = []
        tool_calls: list[dict[str, Any]] = []

        for item in data.get("output", []) or []:
            item_type = item.get("type")
            if item_type == "message":
                for c in item.get("content", []) or []:
                    c_type = c.get("type")
                    if c_type in {"output_text", "text"}:
                        content_parts.append(c.get("text", ""))
                    elif c_type == "reasoning":
                        reasoning_parts.append(c.get("summary", "") or c.get("text", ""))
            elif item_type == "function_call":
                args = item.get("arguments", "{}")
                if isinstance(args, dict):
                    args = json.dumps(args, ensure_ascii=False)
                tool_calls.append({
                    "id": item.get("call_id") or item.get("id", ""),
                    "type": "function",
                    "function": {
                        "name": item.get("name", ""),
                        "arguments": str(args or "{}"),
                    },
                })

        # Some Responses payloads include a pre-aggregated output_text field.
        # Use it as a fallback when output blocks are empty.
        if not content_parts and data.get("output_text"):
            content_parts.append(str(data.get("output_text", "")))

        usage = data.get("usage")
        finish_reason = "tool_calls" if tool_calls else "stop"

        return LLMResponse(
            content="".join(content_parts),
            tool_calls=tool_calls,
            reasoning_content="".join(reasoning_parts) or None,
            finish_reason=finish_reason,
            usage=usage if isinstance(usage, dict) else None,
            model=data.get("model"),
        )

    def _extract_api_error(self, data: dict[str, Any]) -> str | None:
        """Extract meaningful error message from Responses API payload."""
        # OpenAI Responses often returns `"error": null` on success,
        # so we must only treat it as error when it's truthy.
        err = data.get("error")
        if err:
            if isinstance(err, dict):
                msg = err.get("message") or str(err)
                err_type = err.get("type")
                err_code = err.get("code")
                extra = []
                if err_type:
                    extra.append(f"type={err_type}")
                if err_code:
                    extra.append(f"code={err_code}")
                suffix = f" ({', '.join(extra)})" if extra else ""
                return f"{msg}{suffix}"
            return str(err)

        status = str(data.get("status") or "").lower()
        if status in {"failed", "incomplete", "cancelled"}:
            last_error = data.get("last_error")
            incomplete = data.get("incomplete_details")
            rid = data.get("id")
            details: list[str] = [f"status={status}"]
            if rid:
                details.append(f"id={rid}")
            if last_error:
                details.append(f"last_error={last_error}")
            if incomplete:
                details.append(f"incomplete_details={incomplete}")
            return "Responses API returned non-success status: " + "; ".join(details)

        return None

    def _build_error_log_context(self, data: dict[str, Any]) -> dict[str, Any]:
        """Build compact context for error logs."""
        return {
            "provider": "openai-response",
            "model": self.model,
            "response_id": data.get("id"),
            "status": data.get("status"),
            "incomplete_details": data.get("incomplete_details"),
            "last_error": data.get("last_error"),
            "has_output": bool(data.get("output")),
        }

    async def complete(
        self,
        messages: list[LLMMessage],
        tools: list[dict] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        **kwargs: Any,
    ) -> LLMResponse:
        """Non-streaming completion."""
        url = f"{self._normalize_base_url()}/responses"
        payload = self._build_payload(messages, tools, temperature, max_tokens, stream=False, **kwargs)

        client = await self._get_client()
        response = await client.post(url, json=payload, headers=self._get_headers())

        if response.status_code >= 400:
            error_text = response.text[:500]
            raise LLMError(f"HTTP {response.status_code}: {error_text}")

        data = response.json()
        api_error = self._extract_api_error(data)
        if api_error:
            ctx = self._build_error_log_context(data)
            logger.error(
                "OpenAIResponses API error: %s | context=%s",
                api_error,
                ctx,
            )
            raise LLMError(api_error)

        return self._parse_response_data(data)

    async def stream(
        self,
        messages: list[LLMMessage],
        tools: list[dict] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        on_chunk: ChunkCallback | None = None,
        on_tool_delta: ToolCallback | None = None,
        on_thinking: ThinkingCallback | None = None,
        **kwargs: Any,
    ) -> LLMResponse:
        """Streaming completion.

        Minimal implementation: fallback to non-streaming and forward final text.
        """
        response = await self.complete(
            messages=messages,
            tools=tools,
            temperature=temperature,
            max_tokens=max_tokens,
            **kwargs,
        )
        if on_chunk and response.content:
            await on_chunk(response.content)
        if on_thinking and response.reasoning_content:
            await on_thinking(response.reasoning_content)
        return response

    async def close(self) -> None:
        """Close the HTTP client."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()


# ============================================================================
# Gemini Native Client
# ============================================================================

class GeminiClient(LLMClient):
    """Client for Gemini native API (`generateContent` / `streamGenerateContent`)."""

    DEFAULT_BASE_URL = "https://generativelanguage.googleapis.com/v1beta"

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
        self._openai_fallback_client: OpenAICompatibleClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create HTTP client."""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=self.timeout, follow_redirects=True, proxy=None)
        return self._client

    async def _get_openai_fallback_client(self) -> OpenAICompatibleClient:
        """Fallback for legacy `/openai` base URL deployments."""
        if self._openai_fallback_client is None:
            self._openai_fallback_client = OpenAICompatibleClient(
                api_key=self.api_key,
                base_url=self.base_url,
                model=self.model,
                timeout=self.timeout,
                supports_tool_choice=self.supports_tool_choice,
                supports_cache_control=False,
            )
        return self._openai_fallback_client

    def _is_openai_compatible_base(self) -> bool:
        """Detect legacy OpenAI-compatible Gemini gateway endpoint."""
        url = self.base_url.rstrip("/").lower()
        return url.endswith("/openai") or "/openai/" in url

    def _get_headers(self) -> dict[str, str]:
        return {
            "Content-Type": "application/json",
            "x-goog-api-key": self.api_key,
        }

    def _normalize_base_url(self) -> str:
        """Normalize base URL for Gemini native endpoints."""
        url = self.base_url.rstrip("/")
        if "/models/" in url and (url.endswith(":generateContent") or url.endswith(":streamGenerateContent")):
            url = url.split("/models/")[0]
        return url

    def _normalize_model_name(self) -> str:
        """Normalize model id for native Gemini endpoint path."""
        model = (self.model or "").strip()
        if model.startswith("models/"):
            model = model[len("models/"):]
        return model

    def _parse_data_url_image(self, data_url: str) -> tuple[str, str] | None:
        """Parse data URL into (mime_type, base64_data)."""
        m = re.match(r"^data:([^;]+);base64,([A-Za-z0-9+/=]+)$", data_url or "")
        if not m:
            return None
        return m.group(1), m.group(2)

    def _content_to_gemini_parts(self, content: Any) -> list[dict[str, Any]]:
        """Convert canonical content into Gemini `parts`."""
        if content is None:
            return []

        if isinstance(content, str):
            return [{"text": content}]

        if isinstance(content, list):
            parts: list[dict[str, Any]] = []
            for part in content:
                if not isinstance(part, dict):
                    continue
                ptype = part.get("type")
                if ptype == "text":
                    text = part.get("text", "")
                    if text:
                        parts.append({"text": text})
                elif ptype == "image_url":
                    image_obj = part.get("image_url", {})
                    image_url = image_obj.get("url", "") if isinstance(image_obj, dict) else ""
                    parsed = self._parse_data_url_image(image_url)
                    if parsed:
                        mime_type, b64_data = parsed
                        parts.append({
                            "inlineData": {
                                "mimeType": mime_type,
                                "data": b64_data,
                            }
                        })
                    elif image_url:
                        # Gemini native API requires uploaded files or inline data;
                        # preserve reference in text when URL cannot be inlined.
                        parts.append({"text": f"[image_url:{image_url}]"})
            return parts

        return [{"text": str(content)}]

    def _extract_tool_name_map(self, messages: list[LLMMessage]) -> dict[str, str]:
        """Build tool_call_id -> function_name map from assistant messages."""
        out: dict[str, str] = {}
        for msg in messages:
            if msg.role != "assistant" or not msg.tool_calls:
                continue
            for tc in msg.tool_calls:
                tc_id = tc.get("id")
                tc_name = tc.get("function", {}).get("name")
                if tc_id and tc_name:
                    out[tc_id] = tc_name
        return out

    def _convert_tools(self, tools: list[dict] | None) -> tuple[list[dict[str, Any]] | None, dict[str, Any] | None]:
        """Convert OpenAI-style tools to Gemini function declarations."""
        if not tools:
            return None, None

        declarations: list[dict[str, Any]] = []
        for tool in tools:
            if tool.get("type") != "function":
                continue
            fn = tool.get("function", {})
            decl: dict[str, Any] = {
                "name": fn.get("name", ""),
                "description": fn.get("description", ""),
            }
            params = fn.get("parameters")
            if isinstance(params, dict):
                decl["parameters"] = params
            declarations.append(decl)

        if not declarations:
            return None, None

        tools_payload = [{"functionDeclarations": declarations}]
        tool_config = None
        if self.supports_tool_choice:
            tool_config = {"functionCallingConfig": {"mode": "AUTO"}}
        return tools_payload, tool_config

    def _build_payload(
        self,
        messages: list[LLMMessage],
        tools: list[dict] | None,
        temperature: float,
        max_tokens: int | None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Build Gemini request payload."""
        system_blocks: list[str] = []
        contents: list[dict[str, Any]] = []
        tool_name_map = self._extract_tool_name_map(messages)

        for msg in messages:
            if msg.role == "system":
                parts = self._content_to_gemini_parts(msg.content)
                text_chunks = [p.get("text", "") for p in parts if p.get("text")]
                if text_chunks:
                    system_blocks.append("\n".join(text_chunks))
                continue

            if msg.role == "user":
                parts = self._content_to_gemini_parts(msg.content)
                if parts:
                    contents.append({"role": "user", "parts": parts})
                continue

            if msg.role == "assistant":
                parts = self._content_to_gemini_parts(msg.content)
                if msg.tool_calls:
                    for tc in msg.tool_calls:
                        fn = tc.get("function", {})
                        args = fn.get("arguments", "{}")
                        if isinstance(args, str):
                            try:
                                parsed_args = json.loads(args)
                            except json.JSONDecodeError:
                                parsed_args = {}
                        elif isinstance(args, dict):
                            parsed_args = args
                        else:
                            parsed_args = {}
                        parts.append({
                            "functionCall": {
                                "name": fn.get("name", ""),
                                "args": parsed_args,
                            }
                        })
                if parts:
                    contents.append({"role": "model", "parts": parts})
                continue

            if msg.role == "tool":
                name = tool_name_map.get(msg.tool_call_id or "", msg.tool_call_id or "tool_result")
                response_content = msg.content or ""
                if isinstance(response_content, str):
                    try:
                        parsed = json.loads(response_content)
                        if isinstance(parsed, dict):
                            response_obj: dict[str, Any] = parsed
                        else:
                            response_obj = {"result": parsed}
                    except json.JSONDecodeError:
                        response_obj = {"result": response_content}
                elif isinstance(response_content, dict):
                    response_obj = response_content
                else:
                    response_obj = {"result": str(response_content)}

                contents.append({
                    "role": "user",
                    "parts": [{
                        "functionResponse": {
                            "name": name,
                            "response": response_obj,
                        }
                    }],
                })

        payload: dict[str, Any] = {
            "contents": contents or [{"role": "user", "parts": [{"text": ""}]}],
            "generationConfig": {
                "temperature": temperature,
            },
        }

        if max_tokens:
            payload["generationConfig"]["maxOutputTokens"] = max_tokens

        if system_blocks:
            payload["systemInstruction"] = {
                "parts": [{"text": "\n\n".join(system_blocks)}]
            }

        tools_payload, tool_config = self._convert_tools(tools)
        if tools_payload:
            payload["tools"] = tools_payload
        if tool_config:
            payload["toolConfig"] = tool_config

        payload.update(kwargs)
        return payload

    def _normalize_usage(self, usage: dict[str, Any] | None) -> dict[str, int] | None:
        """Normalize Gemini usage metadata to unified usage dict."""
        if not isinstance(usage, dict):
            return None
        input_tokens = int(usage.get("promptTokenCount", 0) or 0)
        output_tokens = int(usage.get("candidatesTokenCount", 0) or 0)
        total_tokens = int(usage.get("totalTokenCount", input_tokens + output_tokens) or 0)
        return {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": total_tokens,
        }

    def _normalize_finish_reason(self, finish_reason: str | None, tool_calls: list[dict]) -> str | None:
        """Normalize Gemini finish reason to OpenAI-style labels."""
        if tool_calls:
            return "tool_calls"
        if not finish_reason:
            return None
        mapping = {
            "STOP": "stop",
            "MAX_TOKENS": "length",
            "SAFETY": "content_filter",
            "RECITATION": "content_filter",
        }
        return mapping.get(finish_reason, "stop")

    def _parse_response_data(self, data: dict[str, Any]) -> LLMResponse:
        """Convert Gemini native response into canonical LLMResponse."""
        content_chunks: list[str] = []
        tool_calls: list[dict[str, Any]] = []
        seen_tool_calls: set[str] = set()
        finish_reason = None

        candidates = data.get("candidates") or []
        if candidates:
            candidate = candidates[0]
            finish_reason = candidate.get("finishReason")
            content_obj = candidate.get("content", {}) or {}
            for part in content_obj.get("parts", []) or []:
                text = part.get("text")
                if text:
                    content_chunks.append(text)
                function_call = part.get("functionCall")
                if function_call:
                    name = function_call.get("name", "")
                    args = function_call.get("args", {})
                    args_str = json.dumps(args if isinstance(args, dict) else {}, ensure_ascii=False)
                    dedup_key = f"{name}:{args_str}"
                    if dedup_key in seen_tool_calls:
                        continue
                    seen_tool_calls.add(dedup_key)
                    tool_calls.append({
                        "id": f"call_{len(tool_calls) + 1}",
                        "type": "function",
                        "function": {
                            "name": name,
                            "arguments": args_str,
                        },
                    })

        usage = self._normalize_usage(data.get("usageMetadata"))

        return LLMResponse(
            content="".join(content_chunks),
            tool_calls=tool_calls,
            finish_reason=self._normalize_finish_reason(finish_reason, tool_calls),
            usage=usage,
            model=data.get("modelVersion") or self.model,
        )

    async def complete(
        self,
        messages: list[LLMMessage],
        tools: list[dict] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        **kwargs: Any,
    ) -> LLMResponse:
        """Non-streaming completion."""
        if self._is_openai_compatible_base():
            fallback = await self._get_openai_fallback_client()
            return await fallback.complete(
                messages=messages,
                tools=tools,
                temperature=temperature,
                max_tokens=max_tokens,
                **kwargs,
            )

        model_name = self._normalize_model_name()
        url = f"{self._normalize_base_url()}/models/{model_name}:generateContent"
        payload = self._build_payload(messages, tools, temperature, max_tokens, **kwargs)

        client = await self._get_client()
        response = await client.post(url, json=payload, headers=self._get_headers())

        if response.status_code >= 400:
            error_text = response.text[:500]
            raise LLMError(f"HTTP {response.status_code}: {error_text}")

        data = response.json()
        if isinstance(data, dict) and data.get("error"):
            raise LLMError(f"API error: {data['error']}")

        return self._parse_response_data(data)

    async def stream(
        self,
        messages: list[LLMMessage],
        tools: list[dict] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        on_chunk: ChunkCallback | None = None,
        on_tool_delta: ToolCallback | None = None,
        on_thinking: ThinkingCallback | None = None,
        **kwargs: Any,
    ) -> LLMResponse:
        """Streaming completion using Gemini SSE endpoint."""
        if self._is_openai_compatible_base():
            fallback = await self._get_openai_fallback_client()
            return await fallback.stream(
                messages=messages,
                tools=tools,
                temperature=temperature,
                max_tokens=max_tokens,
                on_chunk=on_chunk,
                on_tool_delta=on_tool_delta,
                on_thinking=on_thinking,
                **kwargs,
            )

        model_name = self._normalize_model_name()
        url = f"{self._normalize_base_url()}/models/{model_name}:streamGenerateContent"
        payload = self._build_payload(messages, tools, temperature, max_tokens, **kwargs)

        full_text = ""
        tool_calls: list[dict[str, Any]] = []
        seen_tool_calls: set[str] = set()
        final_usage: dict[str, int] | None = None
        final_finish_reason: str | None = None

        client = await self._get_client()

        try:
            async with client.stream(
                "POST",
                url,
                params={"alt": "sse"},
                json=payload,
                headers=self._get_headers(),
            ) as resp:
                if resp.status_code >= 400:
                    error_body = ""
                    async for chunk in resp.aiter_bytes():
                        error_body += chunk.decode(errors="replace")
                    raise LLMError(f"HTTP {resp.status_code}: {error_body[:500]}")

                async for line in resp.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    data_str = line[len("data:"):].strip()
                    if not data_str or data_str == "[DONE]":
                        continue

                    try:
                        data = json.loads(data_str)
                    except json.JSONDecodeError:
                        continue

                    if isinstance(data, dict) and data.get("error"):
                        raise LLMError(f"API error: {data['error']}")

                    usage = self._normalize_usage(data.get("usageMetadata"))
                    if usage:
                        final_usage = usage

                    candidates = data.get("candidates") or []
                    if not candidates:
                        continue
                    candidate = candidates[0]
                    final_finish_reason = candidate.get("finishReason") or final_finish_reason
                    content_obj = candidate.get("content", {}) or {}
                    for part in content_obj.get("parts", []) or []:
                        text = part.get("text")
                        if text:
                            full_text += text
                            if on_chunk:
                                await on_chunk(text)

                        function_call = part.get("functionCall")
                        if function_call:
                            name = function_call.get("name", "")
                            args = function_call.get("args", {})
                            args_str = json.dumps(args if isinstance(args, dict) else {}, ensure_ascii=False)
                            dedup_key = f"{name}:{args_str}"
                            if dedup_key in seen_tool_calls:
                                continue
                            seen_tool_calls.add(dedup_key)
                            tool_calls.append({
                                "id": f"call_{len(tool_calls) + 1}",
                                "type": "function",
                                "function": {
                                    "name": name,
                                    "arguments": args_str,
                                },
                            })

        except (httpx.ConnectError, httpx.ReadError, httpx.ConnectTimeout) as e:
            raise LLMError(f"Connection failed: {e}")

        return LLMResponse(
            content=full_text,
            tool_calls=tool_calls,
            finish_reason=self._normalize_finish_reason(final_finish_reason, tool_calls),
            usage=final_usage,
            model=self.model,
        )

    async def close(self) -> None:
        """Close the HTTP client."""
        if self._openai_fallback_client:
            await self._openai_fallback_client.close()
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
            self._client = httpx.AsyncClient(timeout=self.timeout, follow_redirects=True, proxy=None)
        return self._client

    def _get_headers(self) -> dict[str, str]:
        return {
            "Content-Type": "application/json",
            "x-api-key": self.api_key,
            "anthropic-version": self.API_VERSION,
            "anthropic-beta": "prompt-caching-2024-07-31",
        }

    def _normalize_base_url(self) -> str:
        """Normalize base URL by stripping trailing API paths."""
        url = self.base_url.rstrip("/")
        if url.endswith("/v1/messages"):
            url = url[: -len("/v1/messages")]
        elif url.endswith("/v1/chat/completions"):
            url = url[: -len("/v1/chat/completions")]
        elif url.endswith("/v1"):
            url = url[: -len("/v1")]
        return url

    def _build_payload(
        self,
        messages: list[LLMMessage],
        tools: list[dict] | None,
        temperature: float | None,
        max_tokens: int | None,
        stream: bool = False,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Build Anthropic request payload."""
        system_blocks = []
        anthropic_messages = []

        for msg in messages:
            if msg.role == "system":
                if msg.content:
                    system_blocks.append({
                        "type": "text",
                        "text": msg.content,
                        "cache_control": {"type": "ephemeral"}
                    })
                if msg.dynamic_content:
                    system_blocks.append({
                        "type": "text",
                        "text": f"\n{msg.dynamic_content}"
                    })
            else:
                formatted = msg.to_anthropic_format()
                if formatted:
                    anthropic_messages.append(formatted)

        # In Anthropic prompt caching, we also want to cache_control the last user message
        # So we add cache_control to the very last message in the history if it's a user message
        if anthropic_messages and anthropic_messages[-1]["role"] == "user":
            user_msg = anthropic_messages[-1]
            if isinstance(user_msg["content"], list) and user_msg["content"]:
                # Ensure the last block of the user message has cache_control
                user_msg["content"][-1]["cache_control"] = {"type": "ephemeral"}
            elif isinstance(user_msg["content"], str):
                user_msg["content"] = [
                    {
                        "type": "text",
                        "text": user_msg["content"],
                        "cache_control": {"type": "ephemeral"}
                    }
                ]

        payload: dict[str, Any] = {
            "model": self.model,
            "messages": anthropic_messages,
            "max_tokens": max_tokens or 4096,
            "stream": stream,
        }
        if temperature is not None:
            payload["temperature"] = temperature

        if system_blocks:
            payload["system"] = system_blocks

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
            if anthropic_tools:
                anthropic_tools[-1]["cache_control"] = {"type": "ephemeral"}
            payload["tools"] = anthropic_tools

        payload.update(kwargs)
        return payload

    async def complete(
        self,
        messages: list[LLMMessage],
        tools: list[dict] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        **kwargs: Any,
    ) -> LLMResponse:
        """Non-streaming completion."""
        url = f"{self._normalize_base_url()}/v1/messages"
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
                "cache_creation_input_tokens": data["usage"].get("cache_creation_input_tokens", 0),
                "cache_read_input_tokens": data["usage"].get("cache_read_input_tokens", 0),
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
        temperature: float | None = None,
        max_tokens: int | None = None,
        on_chunk: ChunkCallback | None = None,
        on_tool_delta: ToolCallback | None = None,
        on_thinking: ThinkingCallback | None = None,
        **kwargs: Any,
    ) -> LLMResponse:
        """Streaming completion."""
        url = f"{self._normalize_base_url()}/v1/messages"
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
                            if on_tool_delta:
                                await on_tool_delta(
                                    {
                                        "id": block.get("id") or f"draft-{idx}",
                                        "index": idx,
                                        "name": block.get("name", ""),
                                        "arguments": "",
                                    }
                                )

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
                                if on_tool_delta:
                                    await on_tool_delta(
                                        {
                                            "id": tool_calls_data[tc_idx].get("id") or f"draft-{idx}",
                                            "index": idx,
                                            "name": tool_calls_data[tc_idx]["function"].get("name", ""),
                                            "arguments": tool_calls_data[tc_idx]["function"].get("arguments", ""),
                                        }
                                    )

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

@dataclass(frozen=True)
class ProviderSpec:
    """Provider registry entry."""

    provider: str
    display_name: str
    protocol: Literal["openai_compatible", "anthropic", "openai_responses", "gemini"]
    default_base_url: str | None
    supports_tool_choice: bool = True
    default_max_tokens: int = 4096
    model_max_tokens: dict[str, int] = field(default_factory=dict)


# Provider aliases accepted for compatibility
PROVIDER_ALIASES: dict[str, str] = {
    "openai_response": "openai-response",
    "openairesponses": "openai-response",
}


# Canonical provider registry (single source of truth)
PROVIDER_REGISTRY: dict[str, ProviderSpec] = {
    "anthropic": ProviderSpec(
        provider="anthropic",
        display_name="Anthropic",
        protocol="anthropic",
        default_base_url="https://api.anthropic.com",
        supports_tool_choice=False,
        default_max_tokens=8192,
    ),
    "openai": ProviderSpec(
        provider="openai",
        display_name="OpenAI",
        protocol="openai_compatible",
        default_base_url="https://api.openai.com/v1",
        default_max_tokens=16384,
    ),
    "openai-response": ProviderSpec(
        provider="openai-response",
        display_name="OpenAI Responses",
        protocol="openai_responses",
        default_base_url="https://api.openai.com/v1",
        default_max_tokens=16384,
    ),
    "azure": ProviderSpec(
        provider="azure",
        display_name="Azure OpenAI",
        protocol="openai_compatible",
        default_base_url=None,
        default_max_tokens=16384,
    ),
    "deepseek": ProviderSpec(
        provider="deepseek",
        display_name="DeepSeek",
        protocol="openai_compatible",
        default_base_url="https://api.deepseek.com/v1",
        default_max_tokens=8192,
    ),
    "qwen": ProviderSpec(
        provider="qwen",
        display_name="Qwen (DashScope)",
        protocol="openai_compatible",
        default_base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        default_max_tokens=8192,
        model_max_tokens={
            "qwen-plus": 16384,
            "qwen-long": 16384,
            "qwen-turbo": 8192,
            "qwen-max": 8192,
        },
    ),
    "minimax": ProviderSpec(
        provider="minimax",
        display_name="MiniMax",
        protocol="openai_compatible",
        default_base_url="https://api.minimaxi.com/v1",
        default_max_tokens=16384,
    ),
    "openrouter": ProviderSpec(
        provider="openrouter",
        display_name="OpenRouter",
        protocol="openai_compatible",
        default_base_url="https://openrouter.ai/api/v1",
        default_max_tokens=4096,
    ),
    "zhipu": ProviderSpec(
        provider="zhipu",
        display_name="Zhipu",
        protocol="openai_compatible",
        default_base_url="https://open.bigmodel.cn/api/paas/v4",
        default_max_tokens=8192,
    ),
    "baidu": ProviderSpec(
        provider="baidu",
        display_name="Baidu (Qianfan)",
        protocol="openai_compatible",
        default_base_url="https://qianfan.baidubce.com/v2",
        supports_tool_choice=False,
        default_max_tokens=4096,
    ),
    "gemini": ProviderSpec(
        provider="gemini",
        display_name="Gemini",
        protocol="gemini",
        default_base_url="https://generativelanguage.googleapis.com/v1beta",
        default_max_tokens=8192,
    ),
    "kimi": ProviderSpec(
        provider="kimi",
        display_name="Kimi (Moonshot)",
        protocol="openai_compatible",
        default_base_url="https://api.moonshot.cn/v1",
        default_max_tokens=8192,
    ),
    "vllm": ProviderSpec(
        provider="vllm",
        display_name="vLLM",
        protocol="openai_compatible",
        default_base_url="http://localhost:8000/v1",
        default_max_tokens=4096,
    ),
    "ollama": ProviderSpec(
        provider="ollama",
        display_name="Ollama",
        protocol="openai_compatible",
        default_base_url="http://localhost:11434/v1",
        default_max_tokens=4096,
    ),
    "sglang": ProviderSpec(
        provider="sglang",
        display_name="SGLang",
        protocol="openai_compatible",
        default_base_url="http://localhost:30000/v1",
        default_max_tokens=4096,
    ),
    "custom": ProviderSpec(
        provider="custom",
        display_name="Custom",
        protocol="openai_compatible",
        default_base_url=None,
        default_max_tokens=4096,
    ),
}


def normalize_provider(provider: str) -> str:
    """Normalize provider id with aliases and lowercase."""
    p = (provider or "").strip().lower()
    return PROVIDER_ALIASES.get(p, p)


def get_provider_spec(provider: str) -> ProviderSpec | None:
    """Get provider spec from registry."""
    return PROVIDER_REGISTRY.get(normalize_provider(provider))


def get_provider_manifest() -> list[dict[str, Any]]:
    """List supported providers and capabilities for UI/config discovery."""
    out: list[dict[str, Any]] = []
    for spec in PROVIDER_REGISTRY.values():
        out.append({
            "provider": spec.provider,
            "display_name": spec.display_name,
            "protocol": spec.protocol,
            "default_base_url": spec.default_base_url,
            "supports_tool_choice": spec.supports_tool_choice,
            "default_max_tokens": spec.default_max_tokens,
            "model_max_tokens": spec.model_max_tokens,
            "aliases": [k for k, v in PROVIDER_ALIASES.items() if v == spec.provider],
        })
    return out


# Backward-compatible constants derived from registry
PROVIDER_CLIENTS: dict[str, type[LLMClient]] = {
    spec.provider: (
        AnthropicClient
        if spec.protocol == "anthropic"
        else OpenAIResponsesClient
        if spec.protocol == "openai_responses"
        else GeminiClient
        if spec.protocol == "gemini"
        else OpenAICompatibleClient
    )
    for spec in PROVIDER_REGISTRY.values()
}

PROVIDER_URLS: dict[str, str | None] = {
    spec.provider: spec.default_base_url for spec in PROVIDER_REGISTRY.values()
}

TOOL_CHOICE_PROVIDERS = {
    spec.provider for spec in PROVIDER_REGISTRY.values() if spec.supports_tool_choice
}

MAX_TOKENS_BY_PROVIDER: dict[str, int] = {
    spec.provider: spec.default_max_tokens for spec in PROVIDER_REGISTRY.values()
}

MAX_TOKENS_BY_MODEL: dict[str, int] = {
    prefix: limit
    for spec in PROVIDER_REGISTRY.values()
    for prefix, limit in spec.model_max_tokens.items()
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
    spec = get_provider_spec(provider)
    if spec:
        return spec.default_base_url
    return PROVIDER_URLS.get(normalize_provider(provider))


def get_max_tokens(provider: str, model: str | None = None, max_output_tokens: int | None = None) -> int:
    """Return a safe max_tokens value for the given provider/model pair.

    Priority: max_output_tokens (DB override) > model prefix > provider default > 4096
    """
    spec = get_provider_spec(provider)
    model_limits = spec.model_max_tokens if spec else MAX_TOKENS_BY_MODEL

    # Highest priority: per-model DB override
    if max_output_tokens and max_output_tokens > 0:
        return max_output_tokens

    # Check model-specific limits
    if model:
        for prefix, limit in model_limits.items():
            if model.lower().startswith(prefix):
                return limit

    if spec:
        return spec.default_max_tokens

    # Provider default, falling back to safe 4096
    return MAX_TOKENS_BY_PROVIDER.get(normalize_provider(provider), 4096)


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
    normalized_provider = normalize_provider(provider)
    spec = get_provider_spec(normalized_provider)

    # Get base URL
    final_base_url = get_provider_base_url(normalized_provider, base_url)

    # Create appropriate client
    if spec and spec.protocol == "anthropic":
        return AnthropicClient(
            api_key=api_key,
            base_url=final_base_url,
            model=model,
            timeout=timeout,
        )
    elif spec and spec.protocol == "openai_responses":
        return OpenAIResponsesClient(
            api_key=api_key,
            base_url=final_base_url,
            model=model,
            timeout=timeout,
            supports_tool_choice=spec.supports_tool_choice,
        )
    elif spec and spec.protocol == "gemini":
        return GeminiClient(
            api_key=api_key,
            base_url=final_base_url,
            model=model,
            timeout=timeout,
            supports_tool_choice=spec.supports_tool_choice,
        )
    elif normalized_provider in PROVIDER_CLIENTS:
        supports_tool_choice = normalized_provider in TOOL_CHOICE_PROVIDERS
        return OpenAICompatibleClient(
            api_key=api_key,
            base_url=final_base_url,
            model=model,
            timeout=timeout,
            supports_tool_choice=supports_tool_choice,
            supports_cache_control=normalized_provider == "qwen",
        )
    else:
        # Default to OpenAI-compatible for unknown providers
        return OpenAICompatibleClient(
            api_key=api_key,
            base_url=final_base_url or PROVIDER_URLS["openai"],
            model=model,
            timeout=timeout,
            supports_tool_choice=True,
            supports_cache_control=False,
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
    temperature: float | None = None,
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
    temperature: float | None = None,
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
