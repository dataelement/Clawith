"""Regression tests for Clawith #1028: Gemini 3 thought signatures.

Gemini returns a part-level ``thoughtSignature`` next to each ``functionCall``
and rejects the follow-up request with HTTP 400 when the signature is not
sent back in the same part. The signature must survive the whole round trip:
GeminiClient parse -> context sanitizer -> runtime Call Instance storage
(``additional_kwargs``) -> prompt rebuild -> GeminiClient payload.
"""

from __future__ import annotations

import json
import uuid

import httpx
import pytest

from app.services.agent_runtime.context_builder import RuntimeContextBuild
from app.services.agent_runtime.model_step_service import (
    _prompt_messages,
    _with_call_instances,
)
from app.services.agent_runtime.node_executor import ModelStepResult
from app.services.agent_runtime.state import RuntimeContext
from app.services.llm.caller import _sanitize_tool_calls_for_context
from app.services.llm.client import GeminiClient, LLMMessage

SIGNATURE = "CsUB-thought-signature"


def _gemini_response() -> dict:
    return {
        "candidates": [
            {
                "finishReason": "STOP",
                "content": {
                    "parts": [
                        {
                            "functionCall": {
                                "name": "read_file",
                                "args": {"path": "notes.md"},
                            },
                            "thoughtSignature": SIGNATURE,
                        }
                    ]
                },
            }
        ]
    }


def test_parse_response_keeps_thought_signature_on_tool_call() -> None:
    client = GeminiClient(api_key="<redacted>", model="gemini-3.8-flash")

    reply = client._parse_response_data(_gemini_response())

    assert reply.tool_calls[0]["_gemini_thought_signature"] == SIGNATURE


def test_parse_response_without_signature_has_no_signature_key() -> None:
    client = GeminiClient(api_key="<redacted>", model="gemini-3.8-flash")
    data = _gemini_response()
    del data["candidates"][0]["content"]["parts"][0]["thoughtSignature"]

    reply = client._parse_response_data(data)

    assert "_gemini_thought_signature" not in reply.tool_calls[0]


def test_payload_writes_thought_signature_back_on_the_part() -> None:
    client = GeminiClient(api_key="<redacted>", model="gemini-3.8-flash")
    reply = client._parse_response_data(_gemini_response())
    history = [
        LLMMessage(role="user", content="read notes.md"),
        LLMMessage(role="assistant", content=reply.content, tool_calls=reply.tool_calls),
        LLMMessage(
            role="tool",
            tool_call_id=reply.tool_calls[0]["id"],
            content="hello",
        ),
    ]

    payload = client._build_payload(history, None, 0.0, None)

    model_parts = payload["contents"][1]["parts"]
    function_parts = [p for p in model_parts if "functionCall" in p]
    assert function_parts == [
        {
            "functionCall": {
                "name": "read_file",
                "args": {"path": "notes.md"},
            },
            "thoughtSignature": SIGNATURE,
        }
    ]


def test_payload_without_signature_is_unchanged() -> None:
    client = GeminiClient(api_key="<redacted>", model="gemini-3.8-flash")
    history = [
        LLMMessage(role="user", content="read notes.md"),
        LLMMessage(
            role="assistant",
            content="",
            tool_calls=[
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {
                        "name": "read_file",
                        "arguments": '{"path": "notes.md"}',
                    },
                    "_gemini_extra": {},
                }
            ],
        ),
        LLMMessage(role="tool", tool_call_id="call_1", content="hello"),
    ]

    payload = client._build_payload(history, None, 0.0, None)

    function_parts = [p for p in payload["contents"][1]["parts"] if "functionCall" in p]
    assert function_parts == [{"functionCall": {"name": "read_file", "args": {"path": "notes.md"}}}]


async def test_stream_keeps_thought_signature_on_tool_call() -> None:
    event = {
        "candidates": [
            {
                "finishReason": "STOP",
                "content": {
                    "parts": [
                        {
                            "functionCall": {
                                "name": "read_file",
                                "args": {"path": "notes.md"},
                            },
                            "thoughtSignature": SIGNATURE,
                        }
                    ]
                },
            }
        ]
    }
    body = f"data: {json.dumps(event)}\n\ndata: [DONE]\n\n"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            text=body,
            headers={"Content-Type": "text/event-stream"},
        )

    client = GeminiClient(api_key="<redacted>", model="gemini-3.8-flash")
    client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        reply = await client.stream(
            [LLMMessage(role="user", content="read notes.md")],
            tools=None,
            temperature=0.0,
            max_tokens=None,
        )
    finally:
        await client.close()

    assert reply.tool_calls[0]["_gemini_thought_signature"] == SIGNATURE


def test_sanitizer_carries_thought_signature() -> None:
    sanitized, retry_instruction, _ = _sanitize_tool_calls_for_context(
        [
            {
                "id": "call_1",
                "type": "function",
                "function": {
                    "name": "read_file",
                    "arguments": '{"path": "notes.md"}',
                },
                "_gemini_extra": {},
                "_gemini_thought_signature": SIGNATURE,
            }
        ]
    )

    assert retry_instruction is None
    assert sanitized is not None
    assert sanitized[0]["_gemini_thought_signature"] == SIGNATURE


def _context() -> RuntimeContext:
    return RuntimeContext(
        tenant_id="tenant-1",
        run_id=str(uuid.uuid4()),
        command_id="command-1",
        executor=object(),  # type: ignore[arg-type]
    )


def _result_with_signature() -> ModelStepResult:
    return ModelStepResult(
        intent="tool_calls",  # type: ignore[arg-type]
        assistant_message={
            "id": "assistant-1",
            "role": "assistant",
            "content": "",
        },
        tool_calls=(
            {
                "id": "call_1",
                "type": "function",
                "function": {
                    "name": "read_file",
                    "arguments": '{"path": "notes.md"}',
                },
                "_gemini_extra": {},
                "_gemini_thought_signature": SIGNATURE,
            },
        ),
    )


def test_call_instances_store_signatures_next_to_provider_call_ids() -> None:
    result = _with_call_instances(_context(), _result_with_signature())

    assert result.assistant_message is not None
    additional_kwargs = result.assistant_message["additional_kwargs"]
    assert isinstance(additional_kwargs, dict)
    provider_call_ids = additional_kwargs["provider_call_ids"]
    assert isinstance(provider_call_ids, dict)
    instance_id = next(iter(provider_call_ids))
    assert provider_call_ids[instance_id] == "call_1"
    signatures = additional_kwargs["provider_thought_signatures"]
    assert signatures == {instance_id: SIGNATURE}
    message_calls = result.assistant_message["tool_calls"]
    assert isinstance(message_calls, list)
    assert "_gemini_thought_signature" not in message_calls[0]
    assert "provider_call_id" not in message_calls[0]


def test_call_instances_without_signatures_keep_existing_shape() -> None:
    result = ModelStepResult(
        intent="tool_calls",  # type: ignore[arg-type]
        assistant_message={"id": "assistant-1", "role": "assistant", "content": ""},
        tool_calls=(
            {
                "id": "call_1",
                "type": "function",
                "function": {"name": "read_file", "arguments": "{}"},
            },
        ),
    )

    updated = _with_call_instances(_context(), result)

    assert updated.assistant_message is not None
    additional_kwargs = updated.assistant_message["additional_kwargs"]
    assert isinstance(additional_kwargs, dict)
    assert set(additional_kwargs) == {"provider_call_ids"}
    provider_call_ids = additional_kwargs["provider_call_ids"]
    assert isinstance(provider_call_ids, dict)
    assert list(provider_call_ids.values()) == ["call_1"]


def _build(**overrides) -> RuntimeContextBuild:
    values = {
        "session_context_snapshot": {"version": 1, "summary": "shared"},
        "current_run": {"run_id": str(uuid.uuid4()), "goal": "Read"},
        "related_run_summaries": (),
        "pending_session_messages_snapshot": (),
        "recent_session_messages_snapshot": (),
        "thread_running_summary": None,
        "recent_thread_messages": (),
        "initial_input": {"input_content": "Continue"},
        "resume_input": None,
        "omitted_tool_exchanges": (),
        "retry_model": False,
        "blocked": False,
        "requires_confirmation": False,
    }
    values.update(overrides)
    return RuntimeContextBuild(**values)


def test_prompt_messages_restore_thought_signature() -> None:
    build = _build(
        recent_thread_messages=(
            {
                "id": "assistant-1",
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "call-instance-1",
                        "type": "function",
                        "function": {
                            "name": "read_file",
                            "arguments": '{"path":"notes.md"}',
                        },
                    }
                ],
                "additional_kwargs": {
                    "provider_call_ids": {"call-instance-1": "call_1"},
                    "provider_thought_signatures": {"call-instance-1": SIGNATURE},
                },
            },
            {
                "id": "tool-result-1",
                "role": "tool",
                "tool_call_id": "call-instance-1",
                "content": "hello",
            },
        ),
    )

    messages = _prompt_messages(
        static_prompt="Static",
        dynamic_prompt="Dynamic",
        build=build,
    )

    assistant = next(m for m in messages if m.role == "assistant")
    assert assistant.tool_calls is not None
    assert assistant.tool_calls[0]["id"] == "call_1"
    assert assistant.tool_calls[0]["_gemini_thought_signature"] == SIGNATURE

    client = GeminiClient(api_key="<redacted>", model="gemini-3.8-flash")
    payload = client._build_payload(messages, None, 0.0, None)
    function_parts = [part for content in payload["contents"] for part in content["parts"] if "functionCall" in part]
    assert function_parts[0]["thoughtSignature"] == SIGNATURE


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
