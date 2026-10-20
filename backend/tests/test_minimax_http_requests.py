"""Exercise MiniMax requests at the serialized HTTP transport boundary."""

import json

import httpx
import pytest
from app.services.llm.client import (
    LLMMessage,
    chat_complete,
    chat_stream,
    create_llm_client,
    get_provider_manifest,
)


@pytest.fixture
def requests_sent(monkeypatch):
    requests = []
    async_client = httpx.AsyncClient

    def respond(request):
        payload = json.loads(request.content)
        requests.append((request, payload))
        anthropic = request.url.path.endswith("/messages")
        if payload["stream"]:
            if anthropic:
                events = [
                    {"type": "message_start", "message": {"model": payload["model"], "usage": {}}},
                    {"type": "content_block_delta", "index": 0,
                     "delta": {"type": "text_delta", "text": "OK"}},
                    {"type": "message_stop"},
                ]
            else:
                events = [{"choices": [{"delta": {"content": "OK"}, "finish_reason": "stop"}]}]
            body = "".join(
                (f"event: {event['type']}\n" if anthropic else "")
                + f"data: {json.dumps(event)}\n\n" for event in events
            )
            return httpx.Response(200, text=body, headers={"content-type": "text/event-stream"})
        if anthropic:
            return httpx.Response(200, json={
                "content": [{"type": "text", "text": "OK"}], "stop_reason": "end_turn",
            })
        return httpx.Response(200, json={
            "choices": [{"message": {"content": "OK"}, "finish_reason": "stop"}],
        })

    def mock_client(**kwargs):
        return async_client(**kwargs, transport=httpx.MockTransport(respond))

    monkeypatch.setattr(httpx, "AsyncClient", mock_client)
    return requests


@pytest.mark.parametrize("host", ["api.minimax.io", "api.minimaxi.com"])
@pytest.mark.parametrize("protocol", ["openai", "anthropic"])
@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("saved_model", [None, "MiniMax-M3", "MiniMax-M2.7", "custom-model"])
async def test_minimax_default_and_saved_requests(requests_sent, host, protocol, stream, saved_model):
    manifest = next(p for p in get_provider_manifest() if p["provider"] == "minimax")
    model = saved_model or manifest["default_model_id"]
    base_url = f"https://{host}/" + ("anthropic/v1/messages" if protocol == "anthropic" else "v1")
    call = chat_stream if stream else chat_complete
    response = await call(
        provider="minimax", api_key="test-key", model=model, base_url=base_url,
        messages=[{"role": "user", "content": "Hello"}],
    )
    request, payload = requests_sent.pop()
    assert not requests_sent
    assert response["choices"][0]["message"]["content"] == "OK"
    assert request.method == "POST"
    assert request.url.host == host
    assert request.url.path == ("/anthropic/v1/messages" if protocol == "anthropic" else "/v1/chat/completions")
    assert payload["model"] == (saved_model or "MiniMax-M3.1-Flash")
    assert payload["max_tokens"] == 16384
    assert payload["stream"] is stream
    # Omission uses the API's per-model defaults; no unsupported Flash options are injected.
    assert "thinking" not in payload
    assert "reasoning_effort" not in payload
    assert "output_config" not in payload
    assert "service_tier" not in payload
    assert "reasoning_split" not in payload


@pytest.mark.parametrize("host", ["api.minimax.io", "api.minimaxi.com"])
@pytest.mark.parametrize("protocol", ["openai", "anthropic"])
@pytest.mark.parametrize("effort", ["low", "medium", "high", "xhigh", "max"])
async def test_minimax_explicit_effort_and_history(requests_sent, host, protocol, effort):
    anthropic = protocol == "anthropic"
    client = create_llm_client(
        "minimax", "test-key", "MiniMax-M3.1-Flash",
        f"https://{host}/" + ("anthropic" if anthropic else "v1"),
    )
    image = {"type": "image_url", "image_url": {"url": "data:image/png;base64,aW1hZ2U="}}
    content = [{"type": "text", "text": "Inspect this"}, image]
    if not anthropic:
        content.append({"type": "video_url", "video_url": {"url": "mm_file://video"}})
    tools = [{"type": "function", "function": {
        "name": "lookup", "parameters": {"type": "object", "properties": {}},
    }}]
    messages = [
        LLMMessage(role="user", content=content),
        LLMMessage(role="assistant", content="<think>Keep this history</think>",
                   reasoning_content="Preserved reasoning", reasoning_signature="original-signature",
                   tool_calls=[{"id": "call-1", "type": "function", "function": {
                       "name": "lookup", "arguments": "{}",
                   }}]),
        LLMMessage(role="tool", tool_call_id="call-1", content="Found"),
    ]
    options = {"output_config": {"effort": effort}} if anthropic else {"reasoning_effort": effort}
    try:
        await client.complete(messages, tools=tools, thinking={"type": "adaptive"}, **options)
    finally:
        await client.close()
    _, payload = requests_sent.pop()
    assert payload["thinking"] == {"type": "adaptive"}
    assert all(payload[k] == v for k, v in options.items())
    assert "service_tier" not in payload
    assert "reasoning_split" not in payload
    if anthropic:
        assert payload["messages"][0]["content"][1]["source"] == {
            "type": "base64", "media_type": "image/png", "data": "aW1hZ2U=",
        }
        assert payload["messages"][1]["content"][0] == {
            "type": "thinking", "thinking": "Preserved reasoning", "signature": "original-signature",
        }
        assert payload["messages"][1]["content"][-1]["id"] == "call-1"
        assert payload["messages"][2]["content"][0]["tool_use_id"] == "call-1"
        assert payload["tools"][0]["name"] == "lookup"
    else:
        assert payload["messages"] == [m.to_openai_format() for m in messages]
        assert payload["tools"] == tools


@pytest.mark.parametrize("protocol", ["openai", "anthropic"])
async def test_minimax_m3_can_disable_thinking(requests_sent, protocol):
    base_url = "https://api.minimax.io/" + ("anthropic" if protocol == "anthropic" else "v1")
    client = create_llm_client("minimax", "test-key", "MiniMax-M3", base_url)
    try:
        await client.complete([LLMMessage(role="user", content="Hello")], thinking={"type": "disabled"})
    finally:
        await client.close()
    assert requests_sent[0][1]["thinking"] == {"type": "disabled"}


async def test_minimax_new_provider_uses_manifest_defaults(requests_sent):
    manifest = next(p for p in get_provider_manifest() if p["provider"] == "minimax")
    await chat_complete(
        provider=manifest["provider"], api_key="test-key", model=manifest["default_model_id"],
        messages=[{"role": "user", "content": "Hello"}],
    )
    request, payload = requests_sent.pop()
    assert str(request.url) == "https://api.minimax.io/v1/chat/completions"
    assert payload["model"] == "MiniMax-M3.1-Flash"
    assert payload["max_tokens"] == 16384


async def test_minimax_custom_endpoint_and_model_are_not_replaced(requests_sent):
    await chat_complete(
        provider="minimax", api_key="test-key", model="saved-deployment",
        base_url="https://gateway.example/custom/v1", messages=[{"role": "user", "content": "Hello"}],
    )
    request, payload = requests_sent.pop()
    assert str(request.url) == "https://gateway.example/custom/v1/chat/completions"
    assert payload["model"] == "saved-deployment"
