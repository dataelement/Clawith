"""One-call LLM provider boundary tests for the durable Runtime."""

from types import SimpleNamespace
import uuid

import pytest

from app.services.llm.client import (
    AnthropicClient,
    GeminiClient,
    LLMMessage,
    LLMResponse,
    OpenAICompatibleClient,
    OpenAIResponsesClient,
)
from app.services.llm import single_step


class _Client:
    def __init__(self, response: LLMResponse | Exception) -> None:
        self.response = response
        self.calls = []
        self.closed = False

    async def complete(self, **kwargs):
        self.calls.append(kwargs)
        if isinstance(self.response, Exception):
            raise self.response
        return self.response

    async def close(self) -> None:
        self.closed = True


def _model():
    return SimpleNamespace(
        provider="openai",
        model="runtime-model",
        base_url="https://example.invalid",
        request_timeout=17,
        temperature=0.2,
        max_output_tokens=1024,
    )


def _patch_client(monkeypatch, client: _Client) -> None:
    monkeypatch.setattr(single_step, "create_llm_client", lambda **kwargs: client)
    monkeypatch.setattr(single_step, "get_model_api_key", lambda model: "secret")
    monkeypatch.setattr(single_step, "get_max_tokens", lambda *args: 1024)


def test_native_gemini_preserves_dynamic_system_context_once() -> None:
    client = GeminiClient(api_key="test", model="gemini-test")

    payload = client._build_payload(
        [
            LLMMessage(
                role="system",
                content="Static Base Prompt",
                dynamic_content="Dynamic Runtime Context",
            ),
            LLMMessage(role="user", content="Do the task"),
        ],
        tools=None,
        temperature=0.2,
        max_tokens=1024,
    )

    system_text = payload["systemInstruction"]["parts"][0]["text"]
    assert system_text.count("Static Base Prompt") == 1
    assert system_text.count("Dynamic Runtime Context") == 1
    assert payload["contents"] == [
        {"role": "user", "parts": [{"text": "Do the task"}]}
    ]


def test_provider_payloads_preserve_static_and_dynamic_system_context_once() -> None:
    messages = [
        LLMMessage(
            role="system",
            content="Static Base Prompt",
            dynamic_content="Dynamic Runtime Context",
        ),
        LLMMessage(role="user", content="Do the task"),
    ]
    openai_payload = OpenAICompatibleClient(
        api_key="test",
        model="openai-test",
    )._build_payload(messages, None, 0.2, 1024)
    responses_payload = OpenAIResponsesClient(
        api_key="test",
        model="responses-test",
    )._build_payload(messages, None, 0.2, 1024)
    anthropic_payload = AnthropicClient(
        api_key="test",
        model="anthropic-test",
    )._build_payload(messages, None, 0.2, 1024)
    gemini_payload = GeminiClient(
        api_key="test",
        model="gemini-test",
    )._build_payload(messages, None, 0.2, 1024)

    serialized_systems = (
        str(openai_payload["messages"][0]["content"]),
        str(responses_payload["input"][0]["content"]),
        "\n".join(block["text"] for block in anthropic_payload["system"]),
        gemini_payload["systemInstruction"]["parts"][0]["text"],
    )
    for system_content in serialized_systems:
        assert system_content.count("Static Base Prompt") == 1
        assert system_content.count("Dynamic Runtime Context") == 1


@pytest.mark.asyncio
async def test_complete_once_normalizes_tools_and_records_usage_without_executing_them(
    monkeypatch,
) -> None:
    client = _Client(
        LLMResponse(
            content="",
            tool_calls=[
                {
                    "id": "call-1",
                    "type": "function",
                    "function": {
                        "name": "read_file",
                        "arguments": {"path": "notes.md"},
                    },
                }
            ],
            reasoning_content="inspect the file",
            usage={
                "prompt_tokens": 20,
                "completion_tokens": 5,
                "total_tokens": 25,
            },
        )
    )
    _patch_client(monkeypatch, client)
    recorded = []

    async def record(agent_id, usage):
        recorded.append((agent_id, usage))

    monkeypatch.setattr(single_step, "record_token_usage", record)
    agent_id = uuid.uuid4()
    messages = [LLMMessage(role="user", content="Read notes")]
    tools = [{"type": "function", "function": {"name": "read_file"}}]

    result = await single_step.complete_llm_once(
        _model(),
        messages,
        tools=tools,
        agent_id=agent_id,
    )

    assert result.content == ""
    assert result.reasoning_content == "inspect the file"
    assert result.retry_instruction is None
    assert result.tool_calls == (
        {
            "id": "call-1",
            "type": "function",
            "function": {
                "name": "read_file",
                "arguments": '{"path": "notes.md"}',
            },
        },
    )
    assert result.usage.total_tokens == 25
    assert len(client.calls) == 1
    assert client.calls[0]["messages"] == messages
    assert client.calls[0]["tools"] == tools
    assert client.closed is True
    assert recorded[0][0] == agent_id
    assert recorded[0][1].total_tokens == 25


@pytest.mark.asyncio
async def test_complete_once_returns_a_bounded_repair_instruction_for_invalid_arguments(
    monkeypatch,
) -> None:
    client = _Client(
        LLMResponse(
            content="",
            tool_calls=[
                {
                    "id": "call-bad",
                    "type": "function",
                    "function": {
                        "name": "write_file",
                        "arguments": '{"path":',
                    },
                }
            ],
        )
    )
    _patch_client(monkeypatch, client)
    result = await single_step.complete_llm_once(
        _model(),
        [LLMMessage(role="user", content="Write")],
    )

    assert result.tool_calls == ()
    assert result.retry_instruction is not None
    assert "valid JSON" in result.retry_instruction
    assert client.closed is True


@pytest.mark.asyncio
async def test_complete_once_closes_the_provider_client_when_the_request_fails(
    monkeypatch,
) -> None:
    client = _Client(RuntimeError("provider unavailable"))
    _patch_client(monkeypatch, client)

    with pytest.raises(RuntimeError, match="provider unavailable"):
        await single_step.complete_llm_once(
            _model(),
            [LLMMessage(role="user", content="Hello")],
        )

    assert client.closed is True
    assert len(client.calls) == 1
