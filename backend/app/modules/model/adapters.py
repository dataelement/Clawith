"""Private bounded HTTP encoders/decoders for the four accepted protocol families."""

import base64
import json
import re
from dataclasses import replace
from typing import Any, cast
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit

import httpx

from app.infrastructure.http import require_stateless_http_client
from app.modules.model.execution import (
    FinishReason,
    ModelContent,
    ModelLimits,
    ModelMessage,
    ModelStepRequest,
    ModelStepResult,
    ModelStreamEvent,
    ModelToolCall,
    ModelUsage,
    PrivateModelPolicy,
    ProviderFailure,
    StreamObserver,
    json_object,
)


def _bad(message: str = "Model returned an invalid protocol response") -> ProviderFailure:
    return ProviderFailure("protocol_error", message)


def _reported_error(data: dict[str, Any]) -> ProviderFailure:
    """Classify explicit Provider codes, never message text or private payload values."""
    candidates = [data]
    if isinstance(data.get("error"), dict):
        candidates.append(data["error"])
    response = data.get("response")
    if isinstance(response, dict) and isinstance(response.get("error"), dict):
        candidates.append(response["error"])
    types = {value for item in candidates for key in ("type", "code")
             if isinstance(value := item.get(key), str)}
    statuses = [value for item in candidates for key in ("status", "status_code", "code")
                if type(value := item.get(key)) is int]
    if types & {"rate_limit_error", "rate_limit_exceeded"} or 429 in statuses:
        return ProviderFailure("rate_limited", "Model provider reported a rate limit")
    if types & {"overloaded_error", "server_error", "internal_server_error"} or any(500 <= value <= 599 for value in statuses):
        return ProviderFailure("provider_unavailable", "Model provider reported a service failure")
    return ProviderFailure("provider_error", "Model provider rejected the request")


def _object(value: Any) -> dict[str, Any]:
    # Provider JSON is the untyped trust boundary; each consumed structure is checked here.
    if not isinstance(value, dict):
        raise _bad()
    return value


def _array(value: Any) -> list[Any]:
    if not isinstance(value, list):
        raise _bad()
    return value


def _text(value: Any) -> str:
    if not isinstance(value, str):
        raise _bad()
    return value


def _dump(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    except (ValueError, TypeError, RecursionError):
        raise _bad() from None


def _image(value: str) -> tuple[str, str]:
    try:
        header, data = value.split(",", 1)
        if not header.startswith("data:image/") or not header.endswith(";base64"):
            raise ValueError()
        base64.b64decode(data, validate=True)
        return header[5:-7], data
    except ValueError:
        raise ProviderFailure("unsupported_capability", "This Model adapter requires base64 image content") from None


def _blocks(contents: tuple[ModelContent, ...], protocol: str) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for part in contents:
        if part.kind == "text":
            result.append({"type": "input_text" if protocol == "openai_responses" else "text", "text": part.value})
        elif protocol in {"openai_chat", "openai_responses"}:
            if not part.value.startswith(("https://", "http://", "data:image/")):
                raise ProviderFailure("invalid_input", "Image must be an HTTP URL or base64 data URL")
            result.append({"type": "input_image", "image_url": part.value} if protocol == "openai_responses"
                          else {"type": "image_url", "image_url": {"url": part.value}})
        else:
            mime, data = _image(part.value)
            result.append({"type": "image", "source": {"type": "base64", "media_type": mime, "data": data}})
    return result


def _chat_message(message: ModelMessage) -> dict[str, Any]:
    result: dict[str, Any] = {"role": message.role, "content": _blocks(message.content, "openai_chat")}
    if message.calls:
        result["tool_calls"] = [{"id": c.call_id, "type": "function", "function": {
            "name": c.name, "arguments": c.arguments_json,
        }} for c in message.calls]
    if message.role == "tool":
        result["tool_call_id"] = message.call_id
        if all(c.kind == "text" for c in message.content):
            result["content"] = "".join(c.value for c in message.content)
    return result


def _anthropic_message(message: ModelMessage) -> dict[str, Any]:
    blocks = _blocks(message.content, "anthropic")
    if message.role == "tool":
        return {"role": "user", "content": [{"type": "tool_result", "tool_use_id": message.call_id,
                                                "is_error": message.is_error, "content": blocks}]}
    blocks.extend({"type": "tool_use", "id": c.call_id, "name": c.name,
                   "input": json_object(c.arguments_json)} for c in message.calls)
    return {"role": message.role, "content": blocks}


def _tool_image_messages(messages: tuple[ModelMessage, ...], protocol: str) -> tuple[ModelMessage, ...]:
    """Map media after complete exchanges where Tool-result images are not portable.

    These are Provider messages derived from a Tool result, not new human inputs.
    The original logical messages, call correlation and persisted History remain unchanged.
    """
    if protocol not in {"openai_chat", "gemini"}:
        return messages
    result: list[ModelMessage] = []
    pending: set[str] = set()
    images: list[ModelContent] = []
    for message in messages:
        pending.update(call.call_id for call in message.calls)
        media = tuple(part for part in message.content if part.kind == "image")
        if message.role == "tool":
            if media:
                if message.call_id is None or message.call_id not in pending:
                    raise ProviderFailure("invalid_input", "Tool image requires a matching call")
                images.extend((ModelContent("text", f"Image output from tool call {message.call_id}:"), *media))
                text = tuple(part for part in message.content if part.kind != "image")
                message = replace(message, content=text + (ModelContent("text", "Image output is attached after this Tool exchange."),))
            pending.discard(message.call_id or "")
        result.append(message)
        if not pending and images:
            result.append(ModelMessage("user", tuple(images)))
            images.clear()
    if images:
        raise ProviderFailure("invalid_input", "Tool image exchange has unsettled calls")
    return tuple(result)


async def metadata_limits(
    client: httpx.AsyncClient, protocol: str, endpoint: str, model_name: str, secret: str, limits: ModelLimits,
) -> tuple[int, int] | None:
    """Only documented metadata protocols are queried; HTTP failures never select another source."""
    if protocol not in {"anthropic", "gemini"}:
        # OpenAI Models metadata declares identity/ownership, not hard token limits.
        return None
    parsed = urlsplit(endpoint)
    name = model_name.removeprefix("models/") if protocol == "gemini" else model_name
    url = urlunsplit((parsed.scheme, parsed.netloc, parsed.path.rstrip("/") + "/models/" + quote(name, safe=""),
                     parsed.query, parsed.fragment))
    headers = ({"x-api-key": secret, "anthropic-version": "2023-06-01"} if protocol == "anthropic"
               else {"x-goog-api-key": secret})
    request = httpx.Request("GET", url, headers=headers,
                            extensions={"timeout": httpx.Timeout(limits.timeout_seconds).as_dict()})
    require_stateless_http_client(client)
    response = await client.send(request, auth=None, follow_redirects=False, stream=True)
    try:
        if response.status_code in {404, 405}:
            return None
        if response.status_code >= 300:
            raise ProviderFailure("provider_error", "Model metadata request failed")
        body = bytearray()
        async for chunk in response.aiter_bytes():
            if len(body) + len(chunk) > limits.event_bytes:
                raise ProviderFailure("output_too_large", "Model metadata exceeds byte bound")
            body.extend(chunk)
        try:
            data = _object(json.loads(body))
        except (ValueError, RecursionError):
            raise _bad("Model metadata is invalid") from None
        if data.get("error") is not None:
            raise _reported_error(data)
        returned_name = data.get("name") if protocol == "gemini" else data.get("id")
        if returned_name not in {name, "models/" + name}:
            raise _bad("Model metadata identity does not match the requested Model")
        context = data.get("inputTokenLimit" if protocol == "gemini" else "max_input_tokens")
        output = data.get("outputTokenLimit" if protocol == "gemini" else "max_tokens")
        if context is None and output is None:
            return None
        if type(context) is not int or type(output) is not int or not 0 < output <= context:
            raise _bad("Model metadata hard limits are invalid")
        if protocol == "gemini" and "generateContent" not in _array(data.get("supportedGenerationMethods", [])):
            raise ProviderFailure("unsupported_capability", "Model does not support content generation")
        return context, output
    finally:
        await response.aclose()


def build_request(
    policy: PrivateModelPolicy, request: ModelStepRequest, state: dict[str, Any],
) -> tuple[str, dict[str, Any]]:
    protocol = policy.protocol
    request = replace(request, messages=_tool_image_messages(request.messages, protocol))
    settings = json_object(policy.settings_json)
    if settings.pop("protocol", protocol) != protocol:
        raise ProviderFailure("invalid_input", "Model Policy protocol differs from its configuration")
    allowed_settings = {"temperature", "top_p", "reasoning_effort", "thinking", "reasoning"}
    if set(settings) - allowed_settings:
        raise ProviderFailure("invalid_input", "Model contains unsupported execution settings")
    if protocol == "openai_chat":
        payload: dict[str, Any] = {
            "model": policy.model_name, "messages": [], "stream": request.stream,
            "max_tokens": request.output_tokens,
        }
        for message in request.messages:
            replay = state.get(message.interaction_id or "")
            encoded = _chat_message(message)
            if replay:
                encoded["reasoning_content"] = _text(_object(replay[0])["reasoning_content"])
            if (message.cache_boundary and json_object(policy.capabilities_json).get("supports_prompt_cache") is True
                    and isinstance(encoded["content"], list) and encoded["content"]):
                encoded["content"][-1]["cache_control"] = {"type": "ephemeral"}
            payload["messages"].append(encoded)
        if request.stream:
            payload["stream_options"] = {"include_usage": True}
        if request.tools:
            payload["tools"] = [{"type": "function", "function": {
                "name": t.name, "description": t.description, "parameters": json_object(t.schema_json),
            }} for t in request.tools]
        route = "/chat/completions"
    elif protocol == "openai_responses":
        inputs: list[dict[str, Any]] = []
        for message in request.messages:
            replay = state.get(message.interaction_id or "")
            if replay:
                inputs.extend(replay)
                continue
            if message.role == "tool":
                inputs.append({"type": "function_call_output", "call_id": message.call_id,
                               "output": _blocks(message.content, protocol)})
                continue
            if message.content:
                content = _blocks(message.content, protocol)
                if message.role == "assistant":
                    for part in content:
                        if part["type"] == "input_text":
                            part["type"] = "output_text"
                inputs.append({"role": message.role, "content": content})
            inputs.extend({"type": "function_call", "call_id": c.call_id, "name": c.name,
                           "arguments": c.arguments_json} for c in message.calls)
        payload = {"model": policy.model_name, "input": inputs, "store": False, "stream": request.stream,
                   "max_output_tokens": request.output_tokens, "include": ["reasoning.encrypted_content"]}
        if request.tools:
            payload["tools"] = [{"type": "function", "name": t.name, "description": t.description,
                                 "parameters": json_object(t.schema_json), "strict": False} for t in request.tools]
        route = "/responses"
    elif protocol == "anthropic":
        payload = {"model": policy.model_name, "messages": [], "stream": request.stream,
                   "max_tokens": request.output_tokens}
        for message in request.messages:
            if message.role == "system":
                payload["system"] = _blocks(message.content, protocol)
                if message.cache_boundary and payload["system"]:
                    payload["system"][-1]["cache_control"] = {"type": "ephemeral"}
                continue
            encoded = _anthropic_message(message)
            replay = state.get(message.interaction_id or "")
            if replay:
                encoded["content"] = [dict(_object(block)) for block in replay]
            if message.cache_boundary and encoded["content"]:
                encoded["content"][-1]["cache_control"] = {"type": "ephemeral"}
            payload["messages"].append(encoded)
        if request.tools:
            payload["tools"] = [{"name": t.name, "description": t.description,
                                 "input_schema": json_object(t.schema_json)} for t in request.tools]
        route = "/messages"
    else:
        contents: list[dict[str, Any]] = []
        call_names: dict[str, str] = {}
        payload = {"contents": contents, "generationConfig": {"maxOutputTokens": request.output_tokens}}
        for message in request.messages:
            parts: list[dict[str, Any]] = []
            for part in message.content:
                if part.kind == "text":
                    parts.append({"text": part.value})
                else:
                    mime, data = _image(part.value)
                    parts.append({"inlineData": {"mimeType": mime, "data": data}})
            if message.role == "system":
                payload["systemInstruction"] = {"parts": parts}
                continue
            for call in message.calls:
                call_names[call.call_id] = call.name
                parts.append({"functionCall": {"name": call.name, "args": json_object(call.arguments_json)}})
            if message.role == "tool":
                parts = [{"functionResponse": {"name": call_names.get(message.call_id or "", ""),
                          "response": {"error" if message.is_error else "output": parts}}}]
            replay = state.get(message.interaction_id or "")
            if replay:
                parts = replay
            contents.append({"role": "model" if message.role == "assistant" else "user", "parts": parts})
        if request.tools:
            payload["tools"] = [{"functionDeclarations": [{"name": t.name, "description": t.description,
                                 "parameters": json_object(t.schema_json)} for t in request.tools]}]
        route = f"/models/{quote(policy.model_name.removeprefix('models/'), safe='')}:" + (
            "streamGenerateContent" if request.stream else "generateContent"
        )
    if protocol == "gemini":
        if set(settings) - {"temperature", "top_p"}:
            raise ProviderFailure("invalid_input", "Unsupported Gemini generation setting")
        for key, value in settings.items():
            payload["generationConfig"]["topP" if key == "top_p" else key] = value
    else:
        compatible = {"temperature", "top_p"} | ({"thinking"} if protocol == "anthropic" else
                                               {"reasoning"} if protocol == "openai_responses" else {"reasoning_effort"})
        if set(settings) - compatible:
            raise ProviderFailure("invalid_input", "Model setting does not match selected protocol")
        payload.update(settings)
    endpoint = urlsplit(policy.endpoint)
    query = parse_qsl(endpoint.query, keep_blank_values=True)
    if protocol == "gemini" and request.stream:
        query = [(key, value) for key, value in query if key != "alt"]
        query.append(("alt", "sse"))
    # Endpoints are explicit API roots, never guessed from provider names or rewritten to another protocol.
    return urlunsplit((endpoint.scheme, endpoint.netloc, endpoint.path.rstrip("/") + route,
                       urlencode(query), endpoint.fragment)), payload


def _count_request(
    policy: PrivateModelPolicy, request: ModelStepRequest, state: dict[str, Any],
) -> tuple[str, dict[str, Any], str]:
    protocol = policy.protocol
    counted_policy, counted_request = policy, replace(request, stream=False)
    if protocol == "openai_chat":
        if json_object(policy.capabilities_json).get("image_token_counting") != "openai_responses":
            raise ProviderFailure("image_budget_unavailable", "This Model has no explicit image token counter")
        if any(state.get(message.interaction_id or "") for message in request.messages):
            raise ProviderFailure("image_budget_unavailable", "The configured counter cannot represent Chat continuation")
        # This adapter is explicitly selected, never inferred from endpoint or model name.
        counted_policy = replace(policy, protocol="openai_responses", settings_json='{"protocol":"openai_responses"}')
        counted_request = replace(counted_request, messages=_tool_image_messages(request.messages, protocol))
    _, generated = build_request(counted_policy, counted_request, state)
    if protocol == "anthropic":
        route = "/messages/count_tokens"
        payload = {key: value for key, value in generated.items() if key in {"model", "messages", "system", "tools", "thinking"}}
        field = "input_tokens"
    elif protocol == "gemini":
        name = policy.model_name.removeprefix("models/")
        route = f"/models/{quote(name, safe='')}:countTokens"
        payload = {"generateContentRequest": {"model": "models/" + name, **generated}}
        field = "totalTokens"
    else:
        route = "/responses/input_tokens"
        payload = {key: value for key, value in generated.items() if key in {"model", "input", "tools", "reasoning"}}
        field = "input_tokens"
    endpoint = urlsplit(policy.endpoint)
    url = urlunsplit((endpoint.scheme, endpoint.netloc, endpoint.path.rstrip("/") + route, endpoint.query, ""))
    return url, payload, field


async def count_input_tokens(
    client: httpx.AsyncClient, policy: PrivateModelPolicy, request: ModelStepRequest,
    secret: str, state: dict[str, Any], limits: ModelLimits,
) -> int:
    """Read Provider token estimates without generation, continuation writes or a fallback counter."""
    url, payload, field = _count_request(policy, request, state)
    encoded = _dump(payload).encode()
    if len(encoded) > limits.request_bytes:
        raise ProviderFailure("input_too_large", "Encoded token-count request exceeds byte bound")
    headers = {"content-type": "application/json"}
    if policy.protocol == "anthropic":
        headers.update({"x-api-key": secret, "anthropic-version": "2023-06-01"})
    elif policy.protocol == "gemini":
        headers["x-goog-api-key"] = secret
    else:
        headers["authorization"] = f"Bearer {secret}"
    outbound = httpx.Request("POST", url, headers=headers, content=encoded,
        extensions={"timeout": httpx.Timeout(min(10.0, limits.timeout_seconds)).as_dict()})
    require_stateless_http_client(client)
    response = await client.send(outbound, auth=None, follow_redirects=False, stream=True)
    try:
        if response.status_code in {404, 405, 501}:
            raise ProviderFailure("image_budget_unavailable", "Configured Model token counting is unavailable")
        if response.status_code >= 300:
            code = ("rate_limited" if response.status_code == 429 else
                    "provider_unavailable" if response.status_code >= 500 else "provider_rejected")
            raise ProviderFailure(code, f"Model token counter returned HTTP {response.status_code}")
        body = bytearray()
        maximum = min(limits.event_bytes, limits.response_bytes)
        async for chunk in response.aiter_bytes():
            if len(body) + len(chunk) > maximum:
                raise ProviderFailure("output_too_large", "Token-count response exceeds byte bound")
            body.extend(chunk)
        try:
            data = _object(json.loads(body))
        except (ValueError, RecursionError):
            raise _bad("Model returned invalid token-count data") from None
        if data.get("error") is not None:
            raise _reported_error(data)
        tokens = data.get(field)
        if type(tokens) is not int or not 0 <= tokens <= 2**63 - 1:
            raise _bad("Model returned an invalid input token count")
        return tokens
    finally:
        await response.aclose()


def _usage(data: dict[str, Any], protocol: str) -> ModelUsage:
    def number(key: str, source: dict[str, Any] = data) -> int | None:
        value = source.get(key)
        if value is not None and (type(value) is not int or value < 0):
            raise _bad("Model returned invalid usage")
        return value
    if protocol == "gemini":
        return ModelUsage(number("promptTokenCount"), number("candidatesTokenCount"),
                          number("cachedContentTokenCount"), None, number("thoughtsTokenCount"))
    if protocol == "anthropic":
        return ModelUsage(number("input_tokens"), number("output_tokens"),
                          number("cache_read_input_tokens"), number("cache_creation_input_tokens"))
    chat = protocol == "openai_chat"
    input_details = _object(data.get("prompt_tokens_details" if chat else "input_tokens_details", {}))
    output_details = _object(data.get("completion_tokens_details" if chat else "output_tokens_details", {}))
    return ModelUsage(number("prompt_tokens" if chat else "input_tokens"),
                      number("completion_tokens" if chat else "output_tokens"),
                      number("cached_tokens", input_details), None, number("reasoning_tokens", output_details))


def _finish(value: Any, calls: list[ModelToolCall]) -> FinishReason:
    reasons = {"stop": "stop", "end_turn": "stop", "stop_sequence": "stop", "completed": "stop",
               "tool_calls": "tool_calls", "tool_use": "tool_calls", "length": "length",
               "max_tokens": "length", "max_output_tokens": "length", "content_filter": "content_filter",
               "safety": "content_filter", "recitation": "content_filter", "refusal": "refusal"}
    reason = reasons.get(str(value).lower())
    if reason is None:
        raise _bad("Model did not return a recognized terminal reason")
    if calls and reason == "stop":
        reason = "tool_calls"
    if reason == "tool_calls" and not calls:
        raise _bad("Model ended with Tool Calls but supplied none")
    return cast(FinishReason, reason)


def _call(call_id: Any, name: Any, args: Any) -> ModelToolCall:
    call_id, name = _text(call_id), _text(name)
    if not call_id or not name:
        raise _bad("Model returned an incomplete Tool Call")
    arguments = args if isinstance(args, str) else _dump(args)
    json_object(arguments)
    return ModelToolCall(call_id, name, arguments)


def parse_result(
    data: dict[str, Any], policy: PrivateModelPolicy, request: ModelStepRequest,
) -> tuple[ModelStepResult, list[dict[str, Any]]]:
    calls: list[ModelToolCall] = []
    texts: list[str] = []
    replay: list[dict[str, Any]] = []
    protocol = policy.protocol
    if data.get("error") is not None:
        raise _reported_error(data)
    if protocol == "openai_chat":
        choices = _array(data.get("choices"))
        if len(choices) != 1:
            raise _bad()
        choice = _object(choices[0])
        message = _object(choice.get("message"))
        content = message.get("content")
        if content is not None:
            visible = _text(content)
            embedded = re.findall(r"<think>(.*?)</think>", visible, flags=re.DOTALL)
            texts.append(re.sub(r"<think>.*?</think>", "", visible, flags=re.DOTALL))
            if embedded and not message.get("reasoning_content"):
                message["reasoning_content"] = "".join(embedded)
        for raw in _array(message.get("tool_calls", [])):
            item = _object(raw)
            function = _object(item.get("function"))
            calls.append(_call(item.get("id"), function.get("name"), function.get("arguments")))
        if message.get("reasoning_content"):
            replay = [{"reasoning_content": _text(message["reasoning_content"])}]
        reason = "refusal" if message.get("refusal") else choice.get("finish_reason")
        usage = _object(data.get("usage") or {})
    elif protocol == "openai_responses":
        output = _array(data.get("output"))
        reason = data.get("status")
        if reason == "incomplete":
            reason = _object(data.get("incomplete_details")).get("reason")
        required = False
        for raw in output:
            item = _object(raw)
            kind = item.get("type")
            if kind == "message":
                for block in _array(item.get("content")):
                    block = _object(block)
                    if block.get("type") == "output_text":
                        texts.append(_text(block.get("text")))
                    elif block.get("type") == "refusal":
                        reason = "refusal"
                    else:
                        raise _bad("Unsupported Model output content")
            elif kind == "function_call":
                calls.append(_call(item.get("call_id"), item.get("name"), item.get("arguments")))
            elif kind == "reasoning":
                if not item.get("encrypted_content"):
                    raise _bad("Model omitted required encrypted reasoning")
                required = True
            else:
                raise _bad("Unsupported Model response item")
        if required:
            replay = output
        usage = _object(data.get("usage") or {})
    elif protocol == "anthropic":
        blocks = _array(data.get("content"))
        required = False
        for raw in blocks:
            block = _object(raw)
            kind = block.get("type")
            if kind == "text":
                texts.append(_text(block.get("text")))
            elif kind == "tool_use":
                calls.append(_call(block.get("id"), block.get("name"), block.get("input")))
            elif kind in {"thinking", "redacted_thinking"}:
                if kind == "thinking" and not block.get("signature"):
                    raise _bad("Model omitted required thinking signature")
                required = True
            else:
                raise _bad("Unsupported Model output block")
        if required:
            replay = blocks
        usage = _object(data.get("usage") or {})
        reason = data.get("stop_reason")
    else:
        candidates = _array(data.get("candidates", []))
        if len(candidates) != 1:
            raise _bad("Model returned no single candidate")
        candidate = _object(candidates[0])
        parts = _array(_object(candidate.get("content")).get("parts"))
        required = False
        for raw in parts:
            part = _object(raw)
            if "thoughtSignature" in part:
                _text(part["thoughtSignature"])
                required = True
            if "text" in part and not part.get("thought"):
                texts.append(_text(part["text"]))
            if "functionCall" in part:
                function = _object(part["functionCall"])
                calls.append(_call(function.get("id") or f"{request.step_id}:{len(calls)}",
                                   function.get("name"), function.get("args")))
        if required:
            replay = parts
        usage = _object(data.get("usageMetadata") or {})
        reason = candidate.get("finishReason")
    if len({call.call_id for call in calls}) != len(calls):
        raise _bad("Model returned duplicate Tool Call identities")
    exposed = {tool.name for tool in request.tools}
    if any(call.name not in exposed for call in calls):
        raise _bad("Model called a tool outside the exposed set")
    result = ModelStepResult("".join(texts), tuple(calls), _finish(reason, calls), _usage(usage, protocol),
                             request.step_id, bool(replay))
    return result, replay


async def execute(
    client: httpx.AsyncClient, policy: PrivateModelPolicy, request: ModelStepRequest, secret: str,
    state: dict[str, Any], limits: ModelLimits, observer: StreamObserver | None,
) -> tuple[ModelStepResult, list[dict[str, Any]]]:
    url, payload = build_request(policy, request, state)
    encoded = _dump(payload).encode()
    if len(encoded) > limits.request_bytes:
        raise ProviderFailure("input_too_large", "Encoded Model request exceeds byte bound")
    headers = {"content-type": "application/json"}
    if policy.protocol == "anthropic":
        headers.update({"x-api-key": secret, "anthropic-version": "2023-06-01"})
    elif policy.protocol == "gemini":
        headers["x-goog-api-key"] = secret
    else:
        headers["authorization"] = f"Bearer {secret}"
    outbound = httpx.Request("POST", url, headers=headers, content=encoded,
                             extensions={"timeout": httpx.Timeout(limits.timeout_seconds).as_dict()})
    require_stateless_http_client(client)
    response = await client.send(outbound, auth=None, follow_redirects=False, stream=True)
    try:
        if response.status_code >= 300:
            code = ("rate_limited" if response.status_code == 429 else
                    "provider_unavailable" if response.status_code >= 500 else "provider_rejected")
            raise ProviderFailure(code, f"Model provider returned HTTP {response.status_code}")
        if request.stream:
            data = await read_stream(response, policy.protocol, limits, observer)
        else:
            body = bytearray()
            async for chunk in response.aiter_bytes():
                if len(body) + len(chunk) > limits.response_bytes:
                    raise ProviderFailure("output_too_large", "Model response exceeds byte bound")
                body.extend(chunk)
            try:
                data = _object(json.loads(body))
            except (ValueError, RecursionError):
                raise _bad() from None
    finally:
        await response.aclose()
    return parse_result(data, policy, request)


async def read_stream(
    response: httpx.Response, protocol: str, limits: ModelLimits, observer: StreamObserver | None,
) -> dict[str, Any]:
    accumulator = StreamAccumulator(protocol)
    buffer = bytearray()
    total = 0
    # Framing is bounded before decoding. HTTP chunk boundaries are not SSE event boundaries.
    async for chunk in response.aiter_bytes():
        total += len(chunk)
        if total > limits.response_bytes:
            raise ProviderFailure("output_too_large", "Model stream exceeds byte bound")
        buffer.extend(chunk)
        while True:
            normalized = bytes(buffer).replace(b"\r\n", b"\n")
            boundary = normalized.find(b"\n\n")
            if boundary < 0:
                if len(buffer) > limits.event_bytes:
                    raise ProviderFailure("output_too_large", "Model stream event exceeds byte bound")
                break
            if boundary > limits.event_bytes:
                raise ProviderFailure("output_too_large", "Model stream event exceeds byte bound")
            frame, rest = normalized[:boundary], normalized[boundary + 2:]
            buffer = bytearray(rest)
            try:
                lines = frame.decode("utf-8").split("\n")
                data_text = "\n".join(line[5:].lstrip(" ") for line in lines if line.startswith("data:"))
                if not data_text:
                    continue
                if data_text == "[DONE]":
                    accumulator.done = True
                    continue
                data = _object(json.loads(data_text))
            except (ValueError, UnicodeDecodeError, RecursionError):
                raise _bad("Model returned malformed SSE data") from None
            events = accumulator.add(data)
            if observer:
                for event in events:
                    await observer(event)
    if buffer.strip() or not accumulator.done:
        raise _bad("Model stream ended before a complete terminal event")
    return accumulator.result()


class StreamAccumulator:
    def __init__(self, protocol: str) -> None:
        self.protocol = protocol
        self.done = False
        self.data: dict[str, Any] = {}
        self.text = ""
        self.reasoning = ""
        self.calls: dict[int, dict[str, Any]] = {}
        self.blocks: dict[int, dict[str, Any]] = {}
        self.args: dict[int, str] = {}
        self.parts: list[dict[str, Any]] = []
        self.open_blocks: set[int] = set()
        self.think_buffer = ""
        self.in_think = False

    def chat_text(self, text: str) -> list[ModelStreamEvent]:
        self.think_buffer += text
        events: list[ModelStreamEvent] = []
        while self.think_buffer:
            marker = "</think>" if self.in_think else "<think>"
            position = self.think_buffer.find(marker)
            if position >= 0:
                visible, self.think_buffer = self.think_buffer[:position], self.think_buffer[position + len(marker):]
            else:
                keep = 0
                for length in range(1, min(len(marker), len(self.think_buffer) + 1)):
                    if self.think_buffer.endswith(marker[:length]):
                        keep = length
                visible = self.think_buffer[:-keep] if keep else self.think_buffer
                self.think_buffer = self.think_buffer[-keep:] if keep else ""
            if visible:
                if self.in_think:
                    self.reasoning += visible
                else:
                    self.text += visible
                events.append(ModelStreamEvent("reasoning" if self.in_think else "text", visible))
            if position < 0:
                break
            self.in_think = not self.in_think
        return events

    def add(self, data: dict[str, Any]) -> list[ModelStreamEvent]:
        if data.get("error") is not None or data.get("type") in {"error", "response.failed"}:
            raise _reported_error(data)
        events: list[ModelStreamEvent] = []
        if self.protocol == "openai_chat":
            if data.get("usage"):
                self.data["usage"] = data["usage"]
            for raw in _array(data.get("choices", [])):
                choice = _object(raw)
                if choice.get("index", 0) != 0:
                    raise _bad("Only one Model candidate is supported")
                if choice.get("finish_reason"):
                    self.data["finish_reason"] = choice["finish_reason"]
                delta = _object(choice.get("delta", {}))
                if delta.get("content"):
                    text = _text(delta["content"])
                    events.extend(self.chat_text(text))
                if delta.get("reasoning_content"):
                    text = _text(delta["reasoning_content"])
                    self.reasoning += text
                    events.append(ModelStreamEvent("reasoning", text))
                for raw_call in _array(delta.get("tool_calls", [])):
                    item = _object(raw_call)
                    index = self._index(item)
                    current = self.calls.setdefault(index, {"id": "", "function": {"name": "", "arguments": ""}})
                    if item.get("id"):
                        current["id"] = _text(item["id"])
                    fn = _object(item.get("function", {}))
                    current["function"]["name"] += _text(fn.get("name", ""))
                    arguments = _text(fn.get("arguments", ""))
                    current["function"]["arguments"] += arguments
                    events.append(ModelStreamEvent("tool_arguments", arguments, index, current["id"],
                                                   current["function"]["name"]))
        elif self.protocol == "openai_responses":
            kind = data.get("type")
            if kind in {"response.completed", "response.incomplete"}:
                self.data = _object(data.get("response"))
                self.done = True
            elif kind == "response.output_text.delta":
                events.append(ModelStreamEvent("text", _text(data.get("delta"))))
            elif kind == "response.reasoning_summary_text.delta":
                events.append(ModelStreamEvent("reasoning", _text(data.get("delta"))))
            elif kind == "response.function_call_arguments.delta":
                events.append(ModelStreamEvent("tool_arguments", _text(data.get("delta")),
                                               self._index(data, "output_index")))
        elif self.protocol == "anthropic":
            kind = data.get("type")
            if kind == "message_start":
                self.data = _object(data.get("message"))
            elif kind == "content_block_start":
                index = self._index(data)
                if index in self.blocks:
                    raise _bad("Model repeated a content block")
                self.blocks[index] = _object(data.get("content_block"))
                self.open_blocks.add(index)
            elif kind == "content_block_delta":
                index = self._index(data)
                if index not in self.open_blocks:
                    raise _bad()
                block = self.blocks[index]
                delta = _object(data.get("delta"))
                kind = delta.get("type")
                if kind in {"text_delta", "thinking_delta", "signature_delta"}:
                    key = {"text_delta": "text", "thinking_delta": "thinking", "signature_delta": "signature"}[kind]
                    value = _text(delta.get(key))
                    block[key] = _text(block.get(key, "")) + value
                    if key != "signature":
                        events.append(ModelStreamEvent("text" if key == "text" else "reasoning", value, index))
                elif kind == "input_json_delta":
                    value = _text(delta.get("partial_json"))
                    self.args[index] = self.args.get(index, "") + value
                    events.append(ModelStreamEvent("tool_arguments", value, index, block.get("id"), block.get("name")))
                else:
                    raise _bad("Unsupported Model content delta")
            elif kind == "content_block_stop":
                index = self._index(data)
                if index not in self.open_blocks:
                    raise _bad("Model stopped an unopened content block")
                if index in self.args:
                    self.blocks[index]["input"] = json_object(self.args[index])
                self.open_blocks.remove(index)
            elif kind == "message_delta":
                self.data.update(_object(data.get("delta")))
                usage = _object(self.data.setdefault("usage", {}))
                usage.update(_object(data.get("usage", {})))
            elif kind == "message_stop":
                if self.open_blocks:
                    raise _bad("Model stopped before content blocks completed")
                self.done = True
        else:
            if data.get("usageMetadata"):
                self.data["usageMetadata"] = data["usageMetadata"]
            for raw in _array(data.get("candidates", [])):
                candidate = _object(raw)
                if candidate.get("index", 0) != 0:
                    raise _bad("Only one Model candidate is supported")
                if candidate.get("finishReason"):
                    self.data["finishReason"] = candidate["finishReason"]
                    self.done = True
                for raw_part in _array(_object(candidate.get("content", {})).get("parts", [])):
                    part = _object(raw_part)
                    self.parts.append(part)
                    if part.get("text"):
                        events.append(ModelStreamEvent("reasoning" if part.get("thought") else "text", _text(part["text"])))
        return events

    @staticmethod
    def _index(data: dict[str, Any], key: str = "index") -> int:
        value = data.get(key)
        if type(value) is not int or value < 0 or value > 4096:
            raise _bad("Model returned invalid block index")
        return value

    def result(self) -> dict[str, Any]:
        if self.protocol == "openai_chat":
            if self.in_think:
                self.reasoning += self.think_buffer
            else:
                self.text += self.think_buffer
            return {"choices": [{"finish_reason": self.data.get("finish_reason"), "message": {
                "content": self.text, "reasoning_content": self.reasoning,
                "tool_calls": [self.calls[k] for k in sorted(self.calls)],
            }}], "usage": self.data.get("usage", {})}
        if self.protocol == "anthropic":
            self.data["content"] = [self.blocks[k] for k in sorted(self.blocks)]
        if self.protocol == "gemini":
            return {"candidates": [{"content": {"parts": self.parts}, "finishReason": self.data.get("finishReason")}],
                    "usageMetadata": self.data.get("usageMetadata", {})}
        return self.data
