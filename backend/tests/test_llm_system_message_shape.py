"""Provider-boundary regression tests for canonical system-message shape."""

from __future__ import annotations

from typing import Any

import pytest

from app.services.llm.client import (
    AnthropicClient,
    GeminiClient,
    LLMMessage,
    LLMRequestShapeError,
    OpenAICompatibleClient,
    OpenAIResponsesClient,
    create_llm_client,
)


def _messages_with_legacy_system_history() -> list[LLMMessage]:
    return [
        LLMMessage(role="user", content="Earlier user turn"),
        LLMMessage(
            role="system",
            content="Static Base Prompt",
            dynamic_content="Dynamic Runtime Context",
        ),
        LLMMessage(role="system", content="Legacy onboarding instruction"),
        LLMMessage(role="user", content="Current user turn"),
    ]


def _system_text(message: dict[str, Any]) -> str:
    content = message.get("content", "")
    if isinstance(content, str):
        return content
    return "\n".join(
        str(part.get("text", ""))
        for part in content
        if isinstance(part, dict) and part.get("type") == "text"
    )


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("supports_cache_control", [False, True])
def test_openai_compatible_final_payload_has_one_leading_system_message(
    stream: bool,
    supports_cache_control: bool,
) -> None:
    client = OpenAICompatibleClient(
        api_key="test",
        model="local-model",
        supports_cache_control=supports_cache_control,
    )

    payload = client._build_payload(
        _messages_with_legacy_system_history(),
        tools=None,
        temperature=0.2,
        max_tokens=1024,
        stream=stream,
    )

    system_messages = [message for message in payload["messages"] if message.get("role") == "system"]
    assert len(system_messages) == 1
    assert payload["messages"][0] is system_messages[0]
    assert payload["stream"] is stream

    system_text = _system_text(system_messages[0])
    assert system_text.index("Static Base Prompt") < system_text.index("Dynamic Runtime Context")
    assert system_text.index("Dynamic Runtime Context") < system_text.index("Legacy onboarding instruction")
    assert [_system_text(message) for message in payload["messages"] if message.get("role") == "user"] == [
        "Earlier user turn",
        "Current user turn",
    ]


@pytest.mark.parametrize("stream", [False, True])
def test_openai_responses_final_input_has_one_leading_system_message(stream: bool) -> None:
    client = OpenAIResponsesClient(api_key="test", model="responses-model")

    payload = client._build_payload(
        _messages_with_legacy_system_history(),
        tools=None,
        temperature=0.2,
        max_tokens=1024,
        stream=stream,
    )

    system_items = [item for item in payload["input"] if item.get("role") == "system"]
    assert len(system_items) == 1
    assert payload["input"][0] is system_items[0]
    assert payload["stream"] is stream
    system_text = _system_text(system_items[0])
    assert system_text.index("Static Base Prompt") < system_text.index("Dynamic Runtime Context")
    assert system_text.index("Dynamic Runtime Context") < system_text.index("Legacy onboarding instruction")


def test_native_provider_payloads_fold_later_system_records_in_order() -> None:
    messages = _messages_with_legacy_system_history()
    gemini_payload = GeminiClient(api_key="test", model="gemini-model")._build_payload(
        messages,
        tools=None,
        temperature=0.2,
        max_tokens=1024,
    )
    anthropic_payload = AnthropicClient(api_key="test", model="anthropic-model")._build_payload(
        messages,
        tools=None,
        temperature=0.2,
        max_tokens=1024,
    )

    gemini_system = gemini_payload["systemInstruction"]["parts"][0]["text"]
    anthropic_system = "\n".join(block["text"] for block in anthropic_payload["system"])
    for system_text in (gemini_system, anthropic_system):
        assert system_text.index("Static Base Prompt") < system_text.index("Dynamic Runtime Context")
        assert system_text.index("Dynamic Runtime Context") < system_text.index("Legacy onboarding instruction")

    assert all(item.get("role") != "system" for item in gemini_payload["contents"])
    assert all(item.get("role") != "system" for item in anthropic_payload["messages"])


@pytest.mark.asyncio
async def test_legacy_gemini_openai_fallback_uses_the_same_system_normalization() -> None:
    gemini = GeminiClient(
        api_key="test",
        base_url="https://example.invalid/v1beta/openai",
        model="gemini-openai-model",
    )
    fallback = await gemini._get_openai_fallback_client()

    payload = fallback._build_payload(
        _messages_with_legacy_system_history(),
        tools=None,
        temperature=0.2,
        max_tokens=1024,
    )

    assert [message.get("role") for message in payload["messages"]].count("system") == 1
    assert payload["messages"][0]["role"] == "system"
    await gemini.close()


@pytest.mark.parametrize("provider", ["ollama", "vllm", "sglang", "custom"])
def test_local_openai_compatible_providers_share_the_system_normalization(provider: str) -> None:
    client = create_llm_client(
        provider=provider,
        api_key="test",
        model="local-model",
        base_url="http://localhost.invalid/v1",
    )

    assert isinstance(client, OpenAICompatibleClient)
    payload = client._build_payload(
        _messages_with_legacy_system_history(),
        tools=None,
        temperature=0.2,
        max_tokens=1024,
    )
    assert [message.get("role") for message in payload["messages"]].count("system") == 1
    assert payload["messages"][0]["role"] == "system"


@pytest.mark.parametrize(
    "invalid_messages, expected_error",
    [
        (
            [
                {"role": "system", "content": "one"},
                {"role": "system", "content": "two"},
            ],
            "multiple system messages",
        ),
        (
            [
                {"role": "user", "content": "hello"},
                {"role": "system", "content": "late"},
            ],
            "system message must be the first item",
        ),
    ],
)
def test_openai_compatible_rejects_an_invalid_final_provider_shape(
    monkeypatch: pytest.MonkeyPatch,
    invalid_messages: list[dict[str, Any]],
    expected_error: str,
) -> None:
    client = OpenAICompatibleClient(api_key="test", model="local-model")
    monkeypatch.setattr(client, "_messages_to_openai_payload", lambda _messages: invalid_messages)

    with pytest.raises(LLMRequestShapeError, match=expected_error):
        client._build_payload(
            [LLMMessage(role="system", content="valid before conversion")],
            tools=None,
            temperature=0.2,
            max_tokens=1024,
        )
