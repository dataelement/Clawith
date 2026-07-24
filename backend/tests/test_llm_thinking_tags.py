"""Regression tests for provider reasoning markup normalization."""

import json

import httpx
import pytest

from app.services.llm.client import LLMMessage, OpenAICompatibleClient, _normalize_response_text


def test_normalizer_supports_text_and_reasoning_content_blocks() -> None:
    content, reasoning = _normalize_response_text([
        {"type": "reasoning", "text": "Structured reasoning. "},
        {"type": "text", "text": '<think>Inline reasoning.</think>{"answer": true}'},
    ])

    assert json.loads(content) == {"answer": True}
    assert reasoning == "Structured reasoning. Inline reasoning."


@pytest.mark.asyncio
async def test_non_streaming_response_removes_thinking_tags_before_json_is_consumed() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/chat/completions"
        return httpx.Response(
            200,
            json={
                "model": "MiniMax-M2.5",
                "choices": [{
                    "finish_reason": "stop",
                    "message": {
                        "content": '<think>Work out the answer first.</think>{"answer": true}',
                        "reasoning_content": "Provider supplied reasoning. ",
                    },
                }],
            },
        )

    client = OpenAICompatibleClient(api_key="test", base_url="https://example.test/v1", model="minimax")
    client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        response = await client.complete([LLMMessage(role="user", content="Return JSON")])
    finally:
        await client.close()

    assert json.loads(response.content) == {"answer": True}
    assert "<think>" not in response.content
    assert response.reasoning_content == "Work out the answer first.Provider supplied reasoning. "


@pytest.mark.asyncio
async def test_streaming_thinking_tags_are_forwarded_only_as_collapsed_reasoning() -> None:
    events = [
        {"choices": [{"delta": {"content": "<thi"}}]},
        {"choices": [{"delta": {"content": "nk>Plan the response"}}]},
        {"choices": [{"delta": {"content": "</think>{\"answer\":"}}]},
        {"choices": [{"delta": {"content": "true}"}, "finish_reason": "stop"}]},
    ]
    sse_body = "".join(f"data: {json.dumps(event)}\n\n" for event in events) + "data: [DONE]\n\n"

    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/chat/completions"
        return httpx.Response(200, content=sse_body, headers={"content-type": "text/event-stream"})

    client = OpenAICompatibleClient(api_key="test", base_url="https://example.test/v1", model="minimax")
    client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    visible_chunks: list[str] = []
    thinking_chunks: list[str] = []

    async def on_chunk(content: str) -> None:
        visible_chunks.append(content)

    async def on_thinking(content: str) -> None:
        thinking_chunks.append(content)

    try:
        response = await client.stream(
            [LLMMessage(role="user", content="Return JSON")],
            on_chunk=on_chunk,
            on_thinking=on_thinking,
        )
    finally:
        await client.close()

    assert "".join(visible_chunks) == '{"answer":true}'
    assert "".join(thinking_chunks) == "Plan the response"
    assert json.loads(response.content) == {"answer": True}
    assert response.reasoning_content == "Plan the response"
