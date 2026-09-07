"""Real adapters against controlled HTTP transport; no hosted-provider claims."""

import asyncio
import json
from dataclasses import replace
from uuid import uuid4

import httpx
import pytest

from app.infrastructure.http import create_stateless_http_client
from app.modules.model.adapters import build_request, execute
from app.modules.model.execution import ProviderFailure
from app.modules.model.public import (
    ModelContent,
    ModelLimits,
    ModelMessage,
    ModelStepRequest,
    ModelToolCall,
    ModelToolDefinition,
    PrivateModelPolicy,
)


def policy(protocol="openai_chat"):
    return PrivateModelPolicy(uuid4(), uuid4(), "provider", protocol, "model", "https://provider.invalid/v1",
                              uuid4(), 8192, 1024, json.dumps({"supports_tool_calling": True,
                              "supports_images": True, "supports_streaming": True}), "{}")


def request(*, stream=False):
    return ModelStepRequest(uuid4(), "step-1", (ModelMessage("user", (ModelContent("text", "Hi"),)),),
                            (ModelToolDefinition("search", "Search", '{"type":"object"}'),), 20, 100, stream)


def response(protocol):
    if protocol == "openai_chat":
        return {"choices": [{"finish_reason": "tool_calls", "message": {"content": "", "tool_calls": [
            {"id": "call-1", "function": {"name": "search", "arguments": '{"q":"test"}'}}]}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5}}
    if protocol == "openai_responses":
        return {"status": "completed", "output": [{"type": "function_call", "call_id": "call-1",
            "name": "search", "arguments": '{"q":"test"}'}], "usage": {"input_tokens": 10, "output_tokens": 5}}
    if protocol == "anthropic":
        return {"stop_reason": "tool_use", "content": [{"type": "tool_use", "id": "call-1", "name": "search",
            "input": {"q": "test"}}], "usage": {"input_tokens": 10, "output_tokens": 5}}
    return {"candidates": [{"finishReason": "STOP", "content": {"parts": [
        {"functionCall": {"name": "search", "args": {"q": "test"}}}]}}],
        "usageMetadata": {"promptTokenCount": 10, "candidatesTokenCount": 5}}


@pytest.mark.parametrize("protocol,route", [("openai_chat", "/chat/completions"),
    ("openai_responses", "/responses"), ("anthropic", "/messages"), ("gemini", "/models/model:generateContent")])
async def test_four_adapter_request_and_result(protocol, route):
    captured = []
    def handler(req):
        captured.append(req)
        return httpx.Response(200, json=response(protocol))
    async with create_stateless_http_client(transport=httpx.MockTransport(handler)) as client:
        result, replay = await execute(client, policy(protocol), request(), "secret", {}, ModelLimits(), None)
    assert len(captured) == 1 and captured[0].url.path.endswith(route)
    assert result.calls[0].name == "search" and json.loads(result.calls[0].arguments_json) == {"q": "test"}
    assert result.finish_reason == "tool_calls" and result.usage.input_tokens == 10 and result.usage.output_tokens == 5
    assert replay == [] and "secret" not in repr(result)


class Chunks(httpx.AsyncByteStream):
    def __init__(self, body, size=7):
        self.body, self.size, self.closed = body, size, False

    async def __aiter__(self):
        for index in range(0, len(self.body), self.size):
            yield self.body[index:index+self.size]

    async def aclose(self):
        self.closed = True


def sse(*events, done=False):
    return ("".join("data: " + json.dumps(e) + "\r\n\r\n" for e in events)
            + ("data: [DONE]\r\n\r\n" if done else "")).encode()


async def test_chat_multiple_tools_same_delta_and_fragmented_usage():
    chunks = Chunks(sse({"choices": [{"delta": {"tool_calls": [
        {"index": 0, "id": "a", "function": {"name": "search", "arguments": "{}"}},
        {"index": 1, "id": "b", "function": {"name": "search", "arguments": "{}"}},
    ]}, "finish_reason": "tool_calls"}]}, {"choices": [], "usage": {"prompt_tokens": 17, "completion_tokens": 9}}, done=True))
    events = []
    async def observe(event):
        events.append(event)
    async with create_stateless_http_client(transport=httpx.MockTransport(lambda _: httpx.Response(200, stream=chunks))) as client:
        result, _ = await execute(client, policy(), request(stream=True), "secret", {}, ModelLimits(), observe)
    assert [c.call_id for c in result.calls] == ["a", "b"]
    assert [e.index for e in events] == [0, 1] and result.usage.input_tokens == 17 and chunks.closed


async def test_anthropic_stream_merges_usage_and_preserves_signed_blocks():
    chunks = Chunks(sse(
        {"type": "message_start", "message": {"usage": {"input_tokens": 15, "cache_read_input_tokens": 12}}},
        {"type": "content_block_start", "index": 0, "content_block": {"type": "thinking", "thinking": "", "signature": ""}},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "thinking_delta", "thinking": "private"}},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "signature_delta", "signature": "sig"}},
        {"type": "content_block_stop", "index": 0},
        {"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": 4}},
        {"type": "message_stop"},
    ))
    async with create_stateless_http_client(transport=httpx.MockTransport(lambda _: httpx.Response(200, stream=chunks))) as client:
        result, replay = await execute(client, policy("anthropic"), request(stream=True), "secret", {}, ModelLimits(), None)
    assert result.content == "" and result.requires_continuation and result.usage.cache_read_tokens == 12
    assert result.usage.input_tokens == 15 and result.usage.output_tokens == 4
    assert replay == [{"type": "thinking", "thinking": "private", "signature": "sig"}]


@pytest.mark.parametrize("protocol", ["openai_responses", "gemini"])
async def test_responses_and_gemini_true_stream(protocol):
    final = response(protocol)
    body = sse({"type": "response.completed", "response": final}) if protocol == "openai_responses" else sse(final)
    chunks = Chunks(body)
    async with create_stateless_http_client(transport=httpx.MockTransport(lambda _: httpx.Response(200, stream=chunks))) as client:
        result, _ = await execute(client, policy(protocol), request(stream=True), "secret", {}, ModelLimits(), None)
    assert result.finish_reason == "tool_calls" and chunks.closed


@pytest.mark.parametrize("body", [b'data: not-json\n\n', sse({"choices": [{"delta": {"content": "partial"}}]}),
    sse({"choices": [{"delta": {}, "finish_reason": "unrecognized"}]}, done=True)])
async def test_invalid_or_unterminated_stream_fails_without_retry(body):
    calls = []
    chunks = Chunks(body)
    def handler(req):
        calls.append(req)
        return httpx.Response(200, stream=chunks)
    async with create_stateless_http_client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ProviderFailure):
            await execute(client, policy(), request(stream=True), "secret", {}, ModelLimits(), None)
    assert len(calls) == 1 and chunks.closed


@pytest.mark.parametrize("limit,body", [(20, b"x" * 21), (20, "你".encode() * 7)])
async def test_encoded_response_byte_bound(limit, body):
    async with create_stateless_http_client(transport=httpx.MockTransport(lambda _: httpx.Response(200, content=body))) as client:
        with pytest.raises(ProviderFailure, match="byte bound"):
            await execute(client, policy(), request(), "secret", {}, ModelLimits(response_bytes=limit), None)


async def test_cancellation_closes_transport():
    started = asyncio.Event()
    class Hanging(httpx.AsyncByteStream):
        closed = False
        async def __aiter__(self):
            started.set()
            await asyncio.Event().wait()
            yield b""
        async def aclose(self):
            self.closed = True
    stream = Hanging()
    async with create_stateless_http_client(transport=httpx.MockTransport(lambda _: httpx.Response(200, stream=stream))) as client:
        task = asyncio.create_task(execute(client, policy(), request(stream=True), "secret", {}, ModelLimits(), None))
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert stream.closed


@pytest.mark.parametrize("protocol", ["openai_chat", "openai_responses", "anthropic", "gemini"])
def test_images_and_tool_result_correlation(protocol):
    image = ModelContent("image", "data:image/png;base64,aGVsbG8=")
    messages = (ModelMessage("user", (image,)),
        ModelMessage("assistant", calls=(ModelToolCall("a", "search", "{}"),)),
        ModelMessage("tool", (ModelContent("text", "answer"), image), call_id="a"))
    _, payload = build_request(policy(protocol), replace(request(), messages=messages), {})
    encoded = json.dumps(payload)
    assert "aGVsbG8=" in encoded and "search" in encoded and "answer" in encoded


@pytest.mark.parametrize("protocol,item", [
    ("openai_responses", {"type": "reasoning", "id": "r", "encrypted_content": "opaque", "summary": []}),
    ("anthropic", {"type": "thinking", "thinking": "thought", "signature": "signature"}),
    ("gemini", {"functionCall": {"name": "search", "args": {}}, "thoughtSignature": "signature"}),
])
def test_exact_replay_position_and_separation(protocol, item):
    messages = (ModelMessage("assistant", interaction_id="prior", requires_continuation=True),
                ModelMessage("user", (ModelContent("text", "continue"),)))
    _, payload = build_request(policy(protocol), replace(request(), messages=messages), {"prior": [item]})
    if protocol == "openai_responses":
        assert payload["input"][0] == item
    elif protocol == "anthropic":
        assert payload["messages"][0]["content"] == [item]
    else:
        assert payload["contents"][0]["parts"] == [item]


async def test_chat_thinking_tags_split_across_events():
    body = sse(*[{"choices": [{"delta": {"content": value}}]} for value in
                 ("<thi", "nk>private", "</think", ">answer")],
               {"choices": [{"delta": {}, "finish_reason": "stop"}]}, done=True)
    events = []
    async def observe(event):
        events.append(event)
    async with create_stateless_http_client(transport=httpx.MockTransport(
        lambda _: httpx.Response(200, stream=Chunks(body))
    )) as client:
        result, replay = await execute(client, policy(), request(stream=True), "secret", {}, ModelLimits(), observe)
    assert result.content == "answer" and replay == [{"reasoning_content": "private"}]
    assert "".join(event.text for event in events if event.kind == "text") == "answer"


@pytest.mark.parametrize("adjustment", [-1, 0, 1])
async def test_response_limit_below_at_above(adjustment):
    body = json.dumps(response("openai_chat")).encode()
    async with create_stateless_http_client(transport=httpx.MockTransport(lambda _: httpx.Response(200, content=body))) as client:
        if adjustment < 0:
            with pytest.raises(ProviderFailure):
                await execute(client, policy(), request(), "secret", {},
                              ModelLimits(response_bytes=len(body) + adjustment), None)
        else:
            result, _ = await execute(client, policy(), request(), "secret", {},
                                      ModelLimits(response_bytes=len(body) + adjustment), None)
            assert result.calls


async def test_anthropic_unclosed_tool_block_is_not_a_result():
    body = sse({"type": "message_start", "message": {}},
        {"type": "content_block_start", "index": 0,
         "content_block": {"type": "tool_use", "id": "a", "name": "search", "input": {}}},
        {"type": "message_delta", "delta": {"stop_reason": "tool_use"}}, {"type": "message_stop"})
    async with create_stateless_http_client(transport=httpx.MockTransport(
        lambda _: httpx.Response(200, stream=Chunks(body))
    )) as client:
        with pytest.raises(ProviderFailure):
            await execute(client, policy("anthropic"), request(stream=True), "secret", {}, ModelLimits(), None)


async def test_shared_client_defaults_and_response_cookies_never_cross_credentials():
    captured = []
    def handler(outbound):
        captured.append(outbound)
        return httpx.Response(200, json=response("openai_chat"),
                              headers={"set-cookie": "provider_session=private; Path=/"})
    async with create_stateless_http_client(
        transport=httpx.MockTransport(handler),
    ) as client:
        client.auth = httpx.BasicAuth("default-user", "default-password")
        client.headers.update({"x-client-secret": "default-header", "authorization": "Bearer wrong"})
        client.cookies.set("session", "default-cookie")
        assert not list(client.cookies)
        first, second = policy(), policy()
        await execute(client, first, request(), "tenant-one-key", {}, ModelLimits(), None)
        await execute(client, second, request(), "tenant-two-key", {}, ModelLimits(), None)
        assert not list(client.cookies)
    assert [outbound.headers["authorization"] for outbound in captured] == [
        "Bearer tenant-one-key", "Bearer tenant-two-key",
    ]
    assert all("cookie" not in outbound.headers and "x-client-secret" not in outbound.headers for outbound in captured)


@pytest.mark.parametrize("protocol,route", [("openai_chat", "/chat/completions"),
    ("openai_responses", "/responses"), ("anthropic", "/messages"),
    ("gemini", "/models/model:streamGenerateContent")])
def test_endpoint_path_and_query_are_separate(protocol, route):
    fixed = replace(policy(protocol), endpoint="https://provider.invalid/v1?api-version=2025-01-01&tag=a&tag=b&alt=json")
    url, _ = build_request(fixed, request(stream=True), {})
    parsed = httpx.URL(url)
    assert parsed.path == "/v1" + route
    assert parsed.params["api-version"] == "2025-01-01"
    assert parsed.params.get_list("tag") == ["a", "b"]
    assert parsed.params["alt"] == ("sse" if protocol == "gemini" else "json")


async def test_replaced_cookie_jar_rejected_before_send():
    async with create_stateless_http_client(transport=httpx.MockTransport(lambda _: pytest.fail("no HTTP"))) as client:
        client.cookies = httpx.Cookies()
        with pytest.raises(TypeError, match="stateless"):
            await execute(client, policy(), request(), "secret", {}, ModelLimits(), None)
