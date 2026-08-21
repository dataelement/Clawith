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
    extract_embedded_reasoning,
)
from app.services.llm import single_step
from app.services.llm.utils import get_tool_params


_TINY_PNG_DATA_URL = (
    "data:image/png;base64,"
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/"
    "x8AAusB9Wl2ZQAAAABJRU5ErkJggg=="
)


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


def test_provider_parallel_capability_is_independent_from_tool_choice() -> None:
    tools = [
        {
            "type": "function",
            "function": {
                "name": "read_file",
                "description": "Read a file.",
                "parameters": {"type": "object", "properties": {}},
            },
        }
    ]
    messages = [LLMMessage(role="user", content="Read it")]

    serial_payload = OpenAICompatibleClient(
        api_key="test",
        model="serial-provider",
        supports_tool_choice=True,
        supports_parallel_tool_calls=False,
    )._build_payload(messages, tools, 0.2, 256)
    parallel_payload = OpenAICompatibleClient(
        api_key="test",
        model="parallel-provider",
        supports_tool_choice=True,
        supports_parallel_tool_calls=True,
    )._build_payload(messages, tools, 0.2, 256)

    assert serial_payload["tool_choice"] == "auto"
    assert "parallel_tool_calls" not in serial_payload
    assert parallel_payload["parallel_tool_calls"] is True
    assert get_tool_params("deepseek") == {"tool_choice": "auto"}
    assert get_tool_params("openai") == {
        "tool_choice": "auto",
        "parallel_tool_calls": True,
    }


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


def test_native_gemini_pairs_reused_tool_call_ids_with_their_assistant_turn() -> None:
    client = GeminiClient(api_key="test", model="gemini-test")

    payload = client._build_payload(
        [
            LLMMessage(role="user", content="Inspect and then update the record"),
            LLMMessage(
                role="assistant",
                tool_calls=[
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": "lookup_record", "arguments": "{}"},
                        "_gemini_extra": {"id": "provider-call-1"},
                    },
                    {
                        "id": "call_2",
                        "type": "function",
                        "function": {"name": "read_policy", "arguments": "{}"},
                        "_gemini_extra": {"id": "provider-call-2"},
                    },
                ],
            ),
            LLMMessage(role="tool", tool_call_id="call_1", content='{"record_id":"r1"}'),
            LLMMessage(role="tool", tool_call_id="call_2", content='{"allowed":true}'),
            LLMMessage(
                role="assistant",
                tool_calls=[
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": "update_record", "arguments": '{"id":"r1"}'},
                        "_gemini_extra": {"id": "provider-call-1"},
                    }
                ],
            ),
            LLMMessage(role="tool", tool_call_id="call_1", content='{"updated":true}'),
        ],
        tools=None,
        temperature=0.2,
        max_tokens=1024,
    )

    function_response_names = [
        content["parts"][0]["functionResponse"]["name"]
        for content in payload["contents"]
        if "functionResponse" in content["parts"][0]
    ]
    assert function_response_names == ["lookup_record", "read_policy", "update_record"]
    function_call_ids = [
        part["functionCall"]["id"]
        for content in payload["contents"]
        for part in content["parts"]
        if "functionCall" in part
    ]
    assert function_call_ids == ["provider-call-1", "provider-call-2", "provider-call-1"]


def test_tool_failure_uses_provider_native_error_signals() -> None:
    tool_result = LLMMessage(
        role="tool",
        tool_call_id="call_1",
        content="Tool failed: path is required",
        is_error=True,
    )

    anthropic = tool_result.to_anthropic_format()
    assert anthropic is not None
    assert anthropic["content"][0]["is_error"] is True

    gemini = GeminiClient(api_key="test", model="gemini-test")._build_payload(
        [
            LLMMessage(
                role="assistant",
                tool_calls=[
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": "write_file", "arguments": "{}"},
                    }
                ],
            ),
            tool_result,
        ],
        tools=None,
        temperature=0.2,
        max_tokens=1024,
    )
    response = gemini["contents"][-1]["parts"][0]["functionResponse"]["response"]
    assert response == {"error": "Tool failed: path is required"}

    gemini_success = GeminiClient(
        api_key="test",
        model="gemini-test",
    )._build_payload(
        [
            LLMMessage(
                role="assistant",
                tool_calls=[
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": "read_file", "arguments": "{}"},
                    }
                ],
            ),
            LLMMessage(
                role="tool",
                tool_call_id="call_1",
                content='{"path":"README.md"}',
            ),
        ],
        tools=None,
        temperature=0.2,
        max_tokens=1024,
    )
    success_response = gemini_success["contents"][-1]["parts"][0][
        "functionResponse"
    ]["response"]
    assert success_response == {"output": {"path": "README.md"}}

    openai = tool_result.to_openai_format()
    assert openai == {
        "role": "tool",
        "content": "Tool failed: path is required",
        "tool_call_id": "call_1",
    }


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


def test_openai_responses_preserves_truncation_and_refusal_stop_reasons() -> None:
    client = OpenAIResponsesClient(api_key="test", model="responses-test")
    incomplete = {
        "status": "incomplete",
        "incomplete_details": {"reason": "max_output_tokens"},
        "output": [
            {
                "type": "message",
                "content": [{"type": "output_text", "text": "partial"}],
            }
        ],
    }
    refusal = {
        "status": "completed",
        "output": [
            {
                "type": "message",
                "content": [{"type": "refusal", "refusal": "cannot comply"}],
            }
        ],
    }
    filtered = {
        "status": "incomplete",
        "incomplete_details": {"reason": "content_filter"},
        "output": [],
    }

    assert client._extract_api_error(incomplete) is None
    assert client._parse_response_data(incomplete).finish_reason == "length"
    assert client._parse_response_data(refusal).finish_reason == "refusal"
    assert client._extract_api_error(filtered) is None
    assert client._parse_response_data(filtered).finish_reason == "content_filter"


def test_extract_embedded_reasoning_moves_complete_think_blocks_out_of_content() -> None:
    content, reasoning = extract_embedded_reasoning(
        "<think>Check the latest sources.</think>\nFinal answer.",
        "Provider reasoning.",
    )

    assert content == "Final answer."
    assert reasoning == "Provider reasoning.\n\nCheck the latest sources."


def test_extract_embedded_reasoning_hides_unclosed_leading_think_block() -> None:
    content, reasoning = extract_embedded_reasoning(
        "<think>The model never closed this reasoning block.",
        None,
    )

    assert content == ""
    assert reasoning == "The model never closed this reasoning block."


def test_stream_think_filter_preserves_reasoning_across_split_tags() -> None:
    client = OpenAICompatibleClient(api_key="test", model="test")
    visible = ""
    reasoning = ""
    in_think = False
    tag_buffer = ""

    for part in ("<thi", "nk>Inspect", " evidence.</thi", "nk>Final answer."):
        emitted, thought, in_think, tag_buffer = client._filter_think_tags(
            part,
            in_think,
            tag_buffer,
        )
        visible += emitted
        reasoning += thought

    assert visible == "Final answer."
    assert reasoning == "Inspect evidence."
    assert in_think is False
    assert tag_buffer == ""


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
    assert result.finish_reason == "tool_calls"
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
async def test_complete_once_uses_explicit_max_output_tokens_override(
    monkeypatch,
) -> None:
    client = _Client(LLMResponse(content="bounded", finish_reason="stop"))
    monkeypatch.setattr(single_step, "create_llm_client", lambda **kwargs: client)
    monkeypatch.setattr(single_step, "get_model_api_key", lambda model: "secret")
    observed_limits: list[int | None] = []

    def resolve_max_tokens(_provider, _model, configured_limit):
        observed_limits.append(configured_limit)
        return configured_limit

    monkeypatch.setattr(single_step, "get_max_tokens", resolve_max_tokens)

    result = await single_step.complete_llm_once(
        _model(),
        [LLMMessage(role="user", content="Summarize")],
        max_output_tokens=4096,
    )

    assert result.content == "bounded"
    assert observed_limits == [4096]
    assert client.calls[0]["max_tokens"] == 4096


@pytest.mark.asyncio
async def test_complete_once_routes_embedded_thinking_to_reasoning_content(
    monkeypatch,
) -> None:
    client = _Client(
        LLMResponse(
            content="<think>Inspect the evidence.</think>\nThe evidence is valid.",
            finish_reason="stop",
        )
    )
    _patch_client(monkeypatch, client)

    result = await single_step.complete_llm_once(
        _model(),
        [LLMMessage(role="user", content="Check it")],
    )

    assert result.content == "The evidence is valid."
    assert result.reasoning_content == "Inspect the evidence."
    assert result.finish_reason == "stop"


@pytest.mark.asyncio
async def test_complete_once_normalizes_exact_textual_tool_call_json(
    monkeypatch,
) -> None:
    client = _Client(
        LLMResponse(
            content=(
                '<tool_call>{"name":"read_file",'
                '"arguments":{"path":"notes.md"}}</tool_call>'
            ),
            finish_reason="stop",
        )
    )
    _patch_client(monkeypatch, client)
    tools = [
        {
            "type": "function",
            "function": {
                "name": "read_file",
                "parameters": {"type": "object"},
            },
        }
    ]

    result = await single_step.complete_llm_once(
        _model(),
        [LLMMessage(role="user", content="Read notes")],
        tools=tools,
    )

    assert result.content == ""
    assert result.retry_instruction is None
    assert result.finish_reason == "tool_calls"
    assert len(result.tool_calls) == 1
    assert result.tool_calls[0]["function"] == {
        "name": "read_file",
        "arguments": '{"path": "notes.md"}',
    }


@pytest.mark.asyncio
async def test_complete_once_repairs_unverified_textual_tool_result(
    monkeypatch,
) -> None:
    client = _Client(
        LLMResponse(
            content=(
                "I will search now.\n"
                '<result>{"results":[{"title":"fabricated"}]}</result>'
            ),
            finish_reason="stop",
        )
    )
    _patch_client(monkeypatch, client)

    result = await single_step.complete_llm_once(
        _model(),
        [LLMMessage(role="user", content="Search")],
        tools=[
            {
                "type": "function",
                "function": {
                    "name": "web_search",
                    "parameters": {"type": "object"},
                },
            }
        ],
    )

    assert result.content == ""
    assert result.tool_calls == ()
    assert result.retry_tool_name is None
    assert result.retry_instruction is not None
    assert "No tool was executed" in result.retry_instruction
    assert "native tool call" in result.retry_instruction


@pytest.mark.asyncio
async def test_complete_once_keeps_ordinary_json_as_user_facing_content(
    monkeypatch,
) -> None:
    content = '{"content":"This is the JSON shape the user requested."}'
    client = _Client(LLMResponse(content=content, finish_reason="stop"))
    _patch_client(monkeypatch, client)

    result = await single_step.complete_llm_once(
        _model(),
        [LLMMessage(role="user", content="Return one JSON object")],
        tools=[
            {
                "type": "function",
                "function": {
                    "name": "read_file",
                    "parameters": {"type": "object"},
                },
            }
        ],
    )

    assert result.content == content
    assert result.tool_calls == ()
    assert result.retry_instruction is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("provider_reason", "expected_reason"),
    [
        ("stop", "stop"),
        ("end_turn", "stop"),
        ("max_tokens", "length"),
        ("content_filter", "content_filter"),
        ("refusal", "refusal"),
        ("provider_specific_reason", "unknown"),
        (None, None),
    ],
)
async def test_complete_once_normalizes_provider_finish_reason(
    monkeypatch,
    provider_reason,
    expected_reason,
) -> None:
    client = _Client(
        LLMResponse(
            content="Final response",
            tool_calls=[],
            finish_reason=provider_reason,
        )
    )
    _patch_client(monkeypatch, client)
    monkeypatch.setattr(
        single_step,
        "record_token_usage",
        lambda *_args, **_kwargs: None,
    )

    result = await single_step.complete_llm_once(
        _model(),
        [LLMMessage(role="user", content="Hello")],
    )

    assert result.finish_reason == expected_reason


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
    assert "not executed" in result.retry_instruction
    assert "Do not retry the entire file" in result.retry_instruction
    assert "6000 characters" in result.retry_instruction
    assert "mode=overwrite" in result.retry_instruction
    assert "mode=append" in result.retry_instruction
    assert result.retry_tool_name == "write_file"
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


@pytest.mark.asyncio
async def test_complete_once_sends_standard_multimodal_content_to_vision_provider(
    monkeypatch,
) -> None:
    client = _Client(LLMResponse(content="described"))
    _patch_client(monkeypatch, client)
    original = LLMMessage(
        role="user",
        content=f"[image_data:{_TINY_PNG_DATA_URL}] Describe it",
    )

    result = await single_step.complete_llm_once(
        _model(),
        [original],
        supports_vision=True,
    )

    sent = client.calls[0]["messages"][0]
    assert sent.content == [
        {
            "type": "image_url",
            "image_url": {"url": _TINY_PNG_DATA_URL},
        },
        {"type": "text", "text": "Describe it"},
    ]
    assert isinstance(original.content, str)
    assert result.content == "described"
