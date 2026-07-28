import json
import uuid
from types import SimpleNamespace

import pytest


class FakeStreamClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.messages_seen = []
        self.tools_seen = []
        self.closed = False

    async def stream(self, *, messages, tools=None, on_chunk=None, **_kwargs):
        self.messages_seen.append(list(messages))
        self.tools_seen.append(tools or [])
        response = self.responses.pop(0)
        if response.content and on_chunk:
            await on_chunk(response.content)
        return response

    async def close(self):
        self.closed = True


def _finish_response(content: str):
    from app.services.llm.client import LLMResponse

    return LLMResponse(
        content="",
        tool_calls=[
            {
                "id": "call_finish",
                "type": "function",
                "function": {
                    "name": "finish",
                    "arguments": json.dumps({"content": content}),
                },
            }
        ],
    )


def _finish_response_with_arguments(arguments):
    from app.services.llm.client import LLMResponse

    return LLMResponse(
        content="",
        tool_calls=[
            {
                "id": "call_finish",
                "type": "function",
                "function": {
                    "name": "finish",
                    "arguments": arguments,
                },
            }
        ],
    )


def _plain_response(content: str, *, finish_reason: str | None = "stop"):
    from app.services.llm.client import LLMResponse

    return LLMResponse(
        content=content,
        tool_calls=[],
        finish_reason=finish_reason,
    )


def _model():
    return SimpleNamespace(
        provider="openai",
        model="fake-model",
        base_url="https://example.invalid/v1",
        api_key_encrypted="",
        temperature=0,
        max_output_tokens=256,
        request_timeout=1,
        supports_tool_calling=True,
    )


def test_finish_is_not_seeded_or_model_facing():
    from app.services import tool_seeder
    from app.services.builtin_tool_definitions import (
        BUILTIN_TOOL_NAMES,
        BUILTIN_TOOL_SEEDS,
    )
    assert "finish" not in BUILTIN_TOOL_NAMES
    assert all(seed["name"] != "finish" for seed in BUILTIN_TOOL_SEEDS)
    assert "finish" not in tool_seeder.SYNC_IS_DEFAULT_TOOL_NAMES


def test_group_at_schema_contains_only_bounded_participant_ids() -> None:
    from app.services.agent_runtime.group_at import AT_TOOL_DEFINITION

    function = AT_TOOL_DEFINITION["function"]
    assert function["name"] == "at"
    parameters = function["parameters"]
    assert parameters["required"] == ["participant_ids"]
    assert parameters["additionalProperties"] is False
    assert set(parameters["properties"]) == {"participant_ids"}
    participant_ids = parameters["properties"]["participant_ids"]
    assert participant_ids["type"] == "array"
    assert participant_ids["maxItems"] == 100
    assert participant_ids["uniqueItems"] is True
    assert participant_ids["items"]["format"] == "uuid"


def test_group_at_parser_accepts_uuid_sets_and_rejects_ambiguous_targets() -> None:
    from app.services.agent_runtime.group_at import (
        GroupAtArgumentsError,
        parse_group_at_participant_ids,
    )

    target_id = uuid.uuid4()
    assert parse_group_at_participant_ids(
        {"participant_ids": [str(target_id)]}
    ) == (str(target_id),)
    assert parse_group_at_participant_ids({"participant_ids": []}) == ()

    with pytest.raises(GroupAtArgumentsError, match="unique"):
        parse_group_at_participant_ids(
            {"participant_ids": [str(target_id), str(target_id)]}
        )
    with pytest.raises(GroupAtArgumentsError, match="unsupported"):
        parse_group_at_participant_ids(
            {"participant_ids": [str(target_id)], "content": "not allowed"}
        )


def test_group_finish_parser_accepts_only_bounded_stable_participant_ids() -> None:
    from app.services.llm.finish import find_finish_call

    first = uuid.uuid4()
    second = uuid.uuid4()
    parsed = find_finish_call(
        [
            {
                "id": "call_group_finish",
                "function": {
                    "name": "finish",
                    "arguments": {
                        "content": "I have finished; please review the evidence.",
                        "mention_participant_ids": [
                            str(first),
                            str(second),
                            str(first),
                        ],
                    },
                },
            }
        ],
        allow_group_mentions=True,
    )

    assert parsed is not None and parsed.valid is True
    assert parsed.mention_participant_ids == (str(first), str(second))

    invalid_id = find_finish_call(
        [
            {
                "id": "call_invalid_group_finish",
                "function": {
                    "name": "finish",
                    "arguments": {
                        "content": "Done",
                        "mention_participant_ids": ["Analyst by display name"],
                    },
                },
            }
        ],
        allow_group_mentions=True,
    )
    assert invalid_id is not None and invalid_id.valid is False
    assert "UUID" in (invalid_id.error or "")


@pytest.mark.parametrize(
    "content",
    (
        "## Stage complete - Handoff to the integrator",
        "## 阶段完成 - Handoff 给整合者",
        "本轮已完成，后续工作交接给质量复核 Agent。",
        "Review complete. @Alice can continue.",
    ),
)
def test_group_finish_repairs_explicit_text_handoff_without_structured_mentions(
    content: str,
) -> None:
    from app.services.llm.finish import find_finish_call

    parsed = find_finish_call(
        [
            {
                "id": "call_text_only_handoff",
                "function": {
                    "name": "finish",
                    "arguments": {"content": content},
                },
            }
        ],
        allow_group_mentions=True,
    )

    assert parsed is not None and parsed.valid is False
    assert "mention_participant_ids" in (parsed.error or "")
    assert "Text alone never routes work" in (parsed.error or "")


def test_group_finish_allows_explicit_no_handoff_completion() -> None:
    from app.services.llm.finish import find_finish_call

    parsed = find_finish_call(
        [
            {
                "id": "call_no_handoff",
                "function": {
                    "name": "finish",
                    "arguments": {
                        "content": "Task complete. No handoff is needed.",
                    },
                },
            }
        ],
        allow_group_mentions=True,
    )

    assert parsed is not None and parsed.valid is True
    assert parsed.mention_participant_ids == ()


def test_non_group_finish_rejects_group_or_unknown_bypass_fields() -> None:
    from app.services.llm.finish import find_finish_call

    target = uuid.uuid4()
    group_bypass = find_finish_call(
        [
            {
                "id": "call_non_group_finish",
                "function": {
                    "name": "finish",
                    "arguments": {
                        "content": "Done",
                        "mention_participant_ids": [str(target)],
                    },
                },
            }
        ]
    )
    assert group_bypass is not None and group_bypass.valid is False
    assert "Group Agent Run" in (group_bypass.error or "")

    unknown = find_finish_call(
        [
            {
                "id": "call_unknown_finish_field",
                "function": {
                    "name": "finish",
                    "arguments": {"content": "Done", "artifact_refs": ["fake"]},
                },
            }
        ]
    )
    assert unknown is not None and unknown.valid is False
    assert "unsupported" in (unknown.error or "")


def test_find_finish_call_validates_arguments():
    from app.services.llm.finish import find_finish_call

    valid = find_finish_call([
        {
            "id": "call_1",
            "function": {
                "name": "finish",
                "arguments": {"content": "Done"},
            },
        }
    ])
    assert valid is not None
    assert valid.valid is True
    assert valid.content == "Done"

    missing_content = find_finish_call([
        {
            "id": "call_2",
            "function": {
                "name": "finish",
                "arguments": "{}",
            },
        }
    ])
    assert missing_content is not None
    assert missing_content.valid is False
    assert "content" in missing_content.error

    malformed = find_finish_call([
        {
            "id": "call_3",
            "function": {
                "name": "finish",
                "arguments": "{bad json",
            },
        }
    ])
    assert malformed is not None
    assert malformed.valid is False
    assert "valid JSON" in malformed.error


def test_legacy_group_finish_json_is_decoded_without_exposing_control_fields() -> None:
    from app.services.llm.finish import parse_legacy_finish_content

    target = uuid.uuid4()
    parsed = parse_legacy_finish_content(
        json.dumps(
            {
                "content": "@Reviewer please confirm.",
                "mention_participant_ids": [str(target)],
            }
        ),
        allow_group_mentions=True,
    )

    assert parsed is not None and parsed.valid is True
    assert parsed.content == "@Reviewer please confirm."
    assert parsed.mention_participant_ids == (str(target),)


def test_plain_content_json_is_not_mistaken_for_legacy_finish_control() -> None:
    from app.services.llm.finish import parse_legacy_finish_content

    assert (
        parse_legacy_finish_content(
            '{"content":"This is the JSON shape the user requested."}',
            allow_group_mentions=False,
        )
        is None
    )


@pytest.mark.asyncio
async def test_call_llm_returns_natural_assistant_stop_without_finish(monkeypatch):
    from app.services.llm import caller

    fake_client = FakeStreamClient([
        _plain_response("Final answer."),
    ])

    monkeypatch.setattr(caller, "_get_agent_config", lambda _agent_id: _async_return((3, None)))
    monkeypatch.setattr(caller, "_get_user_name", lambda _user_id: _async_return("Ray"))
    monkeypatch.setattr(
        "app.services.agent_context.build_agent_context",
        lambda *_args, **_kwargs: _async_return(("static", "dynamic")),
    )
    monkeypatch.setattr(caller, "get_agent_tools_for_llm", lambda _agent_id: _async_return([
        {
            "type": "function",
            "function": {
                "name": "finish",
                "description": "Finish",
                "parameters": {
                    "type": "object",
                    "properties": {"content": {"type": "string"}},
                    "required": ["content"],
                },
            },
        }
    ]))
    monkeypatch.setattr(caller, "create_llm_client", lambda **_kwargs: fake_client)
    monkeypatch.setattr(caller, "record_token_usage", lambda *_args, **_kwargs: _async_return(None))

    chunks = []
    result = await caller.call_llm(
        _model(),
        [{"role": "user", "content": "hello"}],
        "Agent",
        "",
        agent_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        on_chunk=lambda text: _async_append(chunks, text),
    )

    assert result == "Final answer."
    assert chunks == ["Final answer."]
    assert len(fake_client.messages_seen) == 1
    assert all(
        tool["function"]["name"] != "finish"
        for tool in fake_client.tools_seen[0]
    )
    assert fake_client.closed is True


@pytest.mark.asyncio
async def test_call_llm_routes_embedded_thinking_before_final_content(monkeypatch):
    from app.services.llm import caller

    fake_client = FakeStreamClient(
        [_plain_response("<think>Inspect the evidence.</think>\nFinal answer.")]
    )
    monkeypatch.setattr(
        caller,
        "_get_agent_config",
        lambda _agent_id: _async_return((3, None)),
    )
    monkeypatch.setattr(
        caller,
        "_get_user_name",
        lambda _user_id: _async_return("Ray"),
    )
    monkeypatch.setattr(
        "app.services.agent_context.build_agent_context",
        lambda *_args, **_kwargs: _async_return(("static", "dynamic")),
    )
    monkeypatch.setattr(
        caller,
        "get_agent_tools_for_llm",
        lambda _agent_id: _async_return([]),
    )
    monkeypatch.setattr(
        caller,
        "create_llm_client",
        lambda **_kwargs: fake_client,
    )
    monkeypatch.setattr(
        caller,
        "record_token_usage",
        lambda *_args, **_kwargs: _async_return(None),
    )
    chunks = []
    thoughts = []

    result = await caller.call_llm(
        _model(),
        [{"role": "user", "content": "hello"}],
        "Agent",
        "",
        agent_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        on_chunk=lambda text: _async_append(chunks, text),
        on_thinking=lambda text: _async_append(thoughts, text),
    )

    assert result == "Final answer."
    assert chunks == ["Final answer."]
    assert thoughts == ["Inspect the evidence."]


@pytest.mark.asyncio
async def test_call_llm_executes_exact_textual_tool_call_before_finishing(
    monkeypatch,
):
    from app.services.llm import caller

    fake_client = FakeStreamClient(
        [
            _plain_response(
                '<tool_call>{"name":"web_search",'
                '"arguments":{"query":"tariffs"}}</tool_call>'
            ),
            _plain_response("Verified result."),
        ]
    )
    monkeypatch.setattr(
        caller,
        "_get_agent_config",
        lambda _agent_id: _async_return((3, None)),
    )
    monkeypatch.setattr(
        caller,
        "_get_user_name",
        lambda _user_id: _async_return("Ray"),
    )
    monkeypatch.setattr(
        "app.services.agent_context.build_agent_context",
        lambda *_args, **_kwargs: _async_return(("static", "dynamic")),
    )
    monkeypatch.setattr(
        caller,
        "get_agent_tools_for_llm",
        lambda _agent_id: _async_return(
            [
                {
                    "type": "function",
                    "function": {
                        "name": "web_search",
                        "parameters": {"type": "object"},
                    },
                }
            ]
        ),
    )
    monkeypatch.setattr(
        caller,
        "execute_tool",
        lambda *_args, **_kwargs: _async_return('{"verified":true}'),
    )
    monkeypatch.setattr(
        caller,
        "create_llm_client",
        lambda **_kwargs: fake_client,
    )
    monkeypatch.setattr(
        caller,
        "record_token_usage",
        lambda *_args, **_kwargs: _async_return(None),
    )
    tool_events = []

    result = await caller.call_llm(
        _model(),
        [{"role": "user", "content": "search"}],
        "Agent",
        "",
        agent_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        on_tool_call=lambda event: _async_append(tool_events, event),
    )

    assert result == "Verified result."
    assert [event["status"] for event in tool_events] == ["running", "done"]
    assert all(event["name"] == "web_search" for event in tool_events)
    assert any(
        message.role == "assistant" and message.tool_calls
        for message in fake_client.messages_seen[1]
    )
    assert any(
        message.role == "tool" and message.content == '{"verified":true}'
        for message in fake_client.messages_seen[1]
    )


@pytest.mark.asyncio
async def test_call_llm_repairs_textual_result_instead_of_publishing_it(monkeypatch):
    from app.services.llm import caller

    fake_client = FakeStreamClient(
        [
            _plain_response(
                "I will search now.\n"
                '<result>{"results":[{"title":"fake"}]}</result>'
            ),
            _plain_response("Recovered final."),
        ]
    )
    monkeypatch.setattr(
        caller,
        "_get_agent_config",
        lambda _agent_id: _async_return((3, None)),
    )
    monkeypatch.setattr(
        caller,
        "_get_user_name",
        lambda _user_id: _async_return("Ray"),
    )
    monkeypatch.setattr(
        "app.services.agent_context.build_agent_context",
        lambda *_args, **_kwargs: _async_return(("static", "dynamic")),
    )
    monkeypatch.setattr(
        caller,
        "get_agent_tools_for_llm",
        lambda _agent_id: _async_return(
            [
                {
                    "type": "function",
                    "function": {
                        "name": "web_search",
                        "parameters": {"type": "object"},
                    },
                }
            ]
        ),
    )
    monkeypatch.setattr(
        caller,
        "create_llm_client",
        lambda **_kwargs: fake_client,
    )
    monkeypatch.setattr(
        caller,
        "record_token_usage",
        lambda *_args, **_kwargs: _async_return(None),
    )
    chunks = []

    result = await caller.call_llm(
        _model(),
        [{"role": "user", "content": "search"}],
        "Agent",
        "",
        agent_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        on_chunk=lambda text: _async_append(chunks, text),
    )

    assert result == "Recovered final."
    assert chunks == ["Recovered final."]
    assert any(
        message.role == "user"
        and "No tool was executed" in str(message.content)
        for message in fake_client.messages_seen[1]
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("supports_tool_calling", [None, False])
async def test_legacy_tool_loop_calls_saved_model_without_verified_tool_calling(
    monkeypatch,
    supports_tool_calling,
):
    from app.services.llm import caller

    model = _model()
    model.supports_tool_calling = supports_tool_calling
    fake_client = FakeStreamClient([_finish_response("Final answer.")])

    monkeypatch.setattr(caller, "_get_agent_config", lambda _agent_id: _async_return((50, None)))
    monkeypatch.setattr(caller, "_get_user_name", lambda _user_id: _async_return("Ray"))
    monkeypatch.setattr(
        "app.services.agent_context.build_agent_context",
        lambda *_args, **_kwargs: _async_return(("static", "dynamic")),
    )
    monkeypatch.setattr(
        caller,
        "get_agent_tools_for_llm",
        lambda _agent_id: _async_return([]),
    )
    monkeypatch.setattr(caller, "create_llm_client", lambda **_kwargs: fake_client)
    monkeypatch.setattr(
        caller,
        "record_token_usage",
        lambda *_args, **_kwargs: _async_return(None),
    )

    result = await caller.call_llm(
        model,
        [{"role": "user", "content": "hello"}],
        "Agent",
        "",
        agent_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
    )

    assert result == "Final answer."
    assert len(fake_client.messages_seen) == 1


@pytest.mark.asyncio
async def test_call_llm_truncated_output_repair_is_bounded(monkeypatch):
    from app.services.llm import caller

    fake_client = FakeStreamClient([
        _plain_response("First partial response.", finish_reason="length"),
        _plain_response("Second partial response.", finish_reason="length"),
    ])

    monkeypatch.setattr(caller, "_get_agent_config", lambda _agent_id: _async_return((50, None)))
    monkeypatch.setattr(caller, "_get_user_name", lambda _user_id: _async_return("Ray"))
    monkeypatch.setattr(
        "app.services.agent_context.build_agent_context",
        lambda *_args, **_kwargs: _async_return(("static", "dynamic")),
    )
    monkeypatch.setattr(caller, "get_agent_tools_for_llm", lambda _agent_id: _async_return([
        {
            "type": "function",
            "function": {
                "name": "finish",
                "description": "Finish",
                "parameters": {
                    "type": "object",
                    "properties": {"content": {"type": "string"}},
                    "required": ["content"],
                },
            },
        }
    ]))
    monkeypatch.setattr(caller, "create_llm_client", lambda **_kwargs: fake_client)
    monkeypatch.setattr(caller, "record_token_usage", lambda *_args, **_kwargs: _async_return(None))

    chunks = []
    result = await caller.call_llm(
        _model(),
        [{"role": "user", "content": "hello"}],
        "Agent",
        "",
        agent_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        on_chunk=lambda text: _async_append(chunks, text),
    )

    assert result.startswith("[Error] model_incomplete_output:")
    assert len(fake_client.messages_seen) == 2
    assert any(
        message.role == "user"
        and "truncated" in str(message.content).lower()
        for message in fake_client.messages_seen[-1]
    )
    assert chunks == []
    assert fake_client.closed is True


@pytest.mark.asyncio
async def test_invalid_finish_does_not_stop_and_is_returned_as_tool_error(monkeypatch):
    from app.services.llm import caller

    fake_client = FakeStreamClient([
        _finish_response_with_arguments("{}"),
        _finish_response("Recovered final."),
    ])

    monkeypatch.setattr(caller, "_get_agent_config", lambda _agent_id: _async_return((3, None)))
    monkeypatch.setattr(caller, "_get_user_name", lambda _user_id: _async_return("Ray"))
    monkeypatch.setattr(
        "app.services.agent_context.build_agent_context",
        lambda *_args, **_kwargs: _async_return(("static", "dynamic")),
    )
    monkeypatch.setattr(caller, "get_agent_tools_for_llm", lambda _agent_id: _async_return([
        {
            "type": "function",
            "function": {
                "name": "finish",
                "description": "Finish",
                "parameters": {
                    "type": "object",
                    "properties": {"content": {"type": "string"}},
                    "required": ["content"],
                },
            },
        }
    ]))
    monkeypatch.setattr(caller, "create_llm_client", lambda **_kwargs: fake_client)
    monkeypatch.setattr(caller, "record_token_usage", lambda *_args, **_kwargs: _async_return(None))

    result = await caller.call_llm(
        _model(),
        [{"role": "user", "content": "hello"}],
        "Agent",
        "",
        agent_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
    )

    assert result == "Recovered final."
    second_round_messages = fake_client.messages_seen[1]
    assert any(
        msg.role == "tool"
        and msg.tool_call_id == "call_finish"
        and "content" in str(msg.content)
        for msg in second_round_messages
    )


@pytest.mark.asyncio
async def test_repeated_invalid_finish_is_bounded_by_protocol_code(monkeypatch):
    from app.services.llm import caller

    fake_client = FakeStreamClient([
        _finish_response_with_arguments("{}"),
        _finish_response_with_arguments("{}"),
    ])
    monkeypatch.setattr(caller, "_get_agent_config", lambda _agent_id: _async_return((50, None)))
    monkeypatch.setattr(caller, "_get_user_name", lambda _user_id: _async_return("Ray"))
    monkeypatch.setattr(
        "app.services.agent_context.build_agent_context",
        lambda *_args, **_kwargs: _async_return(("static", "dynamic")),
    )
    monkeypatch.setattr(caller, "get_agent_tools_for_llm", lambda _agent_id: _async_return([
        {
            "type": "function",
            "function": {
                "name": "finish",
                "description": "Finish",
                "parameters": {"type": "object", "properties": {}},
            },
        }
    ]))
    monkeypatch.setattr(caller, "create_llm_client", lambda **_kwargs: fake_client)
    monkeypatch.setattr(caller, "record_token_usage", lambda *_args, **_kwargs: _async_return(None))

    result = await caller.call_llm(
        _model(),
        [{"role": "user", "content": "hello"}],
        "Agent",
        "",
        agent_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
    )

    assert result.startswith("[Error] invalid_finish_protocol_violation:")
    assert len(fake_client.messages_seen) == 2
    assert fake_client.closed is True


@pytest.mark.asyncio
async def test_repeated_invalid_tool_json_is_bounded_by_protocol_code(monkeypatch):
    from app.services.llm import caller
    from app.services.llm.client import LLMResponse

    invalid = LLMResponse(
        content="",
        tool_calls=[
            {
                "id": "call-bad-json",
                "type": "function",
                "function": {"name": "finish", "arguments": '{"content":'},
            }
        ],
    )
    fake_client = FakeStreamClient([invalid, invalid])
    monkeypatch.setattr(caller, "_get_agent_config", lambda _agent_id: _async_return((50, None)))
    monkeypatch.setattr(caller, "_get_user_name", lambda _user_id: _async_return("Ray"))
    monkeypatch.setattr(
        "app.services.agent_context.build_agent_context",
        lambda *_args, **_kwargs: _async_return(("static", "dynamic")),
    )
    monkeypatch.setattr(caller, "get_agent_tools_for_llm", lambda _agent_id: _async_return([
        {
            "type": "function",
            "function": {
                "name": "finish",
                "description": "Finish",
                "parameters": {"type": "object", "properties": {}},
            },
        }
    ]))
    monkeypatch.setattr(caller, "create_llm_client", lambda **_kwargs: fake_client)
    monkeypatch.setattr(caller, "record_token_usage", lambda *_args, **_kwargs: _async_return(None))

    result = await caller.call_llm(
        _model(),
        [{"role": "user", "content": "hello"}],
        "Agent",
        "",
        agent_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
    )

    assert result.startswith("[Error] invalid_tool_call_protocol_violation:")
    assert len(fake_client.messages_seen) == 2
    assert fake_client.closed is True


@pytest.mark.asyncio
async def test_invalid_write_file_json_gets_three_bounded_repairs(monkeypatch):
    from app.services.llm import caller
    from app.services.llm.client import LLMResponse

    invalid = LLMResponse(
        content="",
        tool_calls=[
            {
                "id": "call-bad-write-json",
                "type": "function",
                "function": {
                    "name": "write_file",
                    "arguments": '{"path":"page.html","content":"<html>',
                },
            }
        ],
    )
    fake_client = FakeStreamClient([invalid, invalid, invalid, invalid])
    monkeypatch.setattr(caller, "_get_agent_config", lambda _agent_id: _async_return((50, None)))
    monkeypatch.setattr(caller, "_get_user_name", lambda _user_id: _async_return("Ray"))
    monkeypatch.setattr(
        "app.services.agent_context.build_agent_context",
        lambda *_args, **_kwargs: _async_return(("static", "dynamic")),
    )
    monkeypatch.setattr(caller, "get_agent_tools_for_llm", lambda _agent_id: _async_return([
        {
            "type": "function",
            "function": {
                "name": "write_file",
                "description": "Write a file",
                "parameters": {"type": "object", "properties": {}},
            },
        }
    ]))
    monkeypatch.setattr(caller, "create_llm_client", lambda **_kwargs: fake_client)
    monkeypatch.setattr(caller, "record_token_usage", lambda *_args, **_kwargs: _async_return(None))

    result = await caller.call_llm(
        _model(),
        [{"role": "user", "content": "create a long page"}],
        "Agent",
        "",
        agent_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
    )

    assert result == (
        "[Error] invalid_tool_call_protocol_violation: "
        "本次文件生成未完成：write_file 工具参数无效或被截断，连续重试后仍无法执行。"
        "请回复「重新生成」，我会基于当前对话重新尝试。"
    )
    assert len(fake_client.messages_seen) == 4
    assert fake_client.closed is True


@pytest.mark.asyncio
async def test_skip_tools_uses_natural_completion_without_any_tools(monkeypatch):
    from app.services.llm import caller

    fake_client = FakeStreamClient([_plain_response("Onboarding done.")])

    monkeypatch.setattr(caller, "_get_agent_config", lambda _agent_id: _async_return((1, None)))
    monkeypatch.setattr(caller, "_get_user_name", lambda _user_id: _async_return("Ray"))
    monkeypatch.setattr(
        "app.services.agent_context.build_agent_context",
        lambda *_args, **_kwargs: _async_return(("static", "dynamic")),
    )
    monkeypatch.setattr(caller, "create_llm_client", lambda **_kwargs: fake_client)
    monkeypatch.setattr(caller, "record_token_usage", lambda *_args, **_kwargs: _async_return(None))

    result = await caller.call_llm(
        _model(),
        [{"role": "user", "content": "start"}],
        "Agent",
        "",
        agent_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        skip_tools=True,
    )

    assert result == "Onboarding done."
    tool_names = [tool["function"]["name"] for tool in fake_client.tools_seen[0]]
    assert tool_names == []


@pytest.mark.asyncio
async def test_execute_tool_finish_is_noop_control_signal(monkeypatch):
    from app.services import agent_tools

    result = await agent_tools.execute_tool(
        "finish",
        {"content": "Visible answer"},
        agent_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
    )

    assert result == "Visible answer"


def test_finish_is_not_in_always_available_core_tools():
    from app.services.agent_tools import _ALWAYS_INCLUDE_CORE

    assert "finish" not in _ALWAYS_INCLUDE_CORE


def test_tool_round_warning_only_names_tools_present_in_current_schema():
    from app.services.llm.caller import _tool_round_limit_warning

    without_continuation_tools = _tool_round_limit_warning(
        round_index=8,
        max_rounds=10,
        allowed_tool_names={"finish"},
        urgent=False,
    )
    assert "upsert_focus_item" not in without_continuation_tools
    assert "set_trigger" not in without_continuation_tools

    with_continuation_tools = _tool_round_limit_warning(
        round_index=8,
        max_rounds=10,
        allowed_tool_names={"finish", "upsert_focus_item", "set_trigger"},
        urgent=True,
    )
    assert "upsert_focus_item" in with_continuation_tools
    assert "set_trigger" in with_continuation_tools


@pytest.mark.asyncio
async def test_mid_loop_token_limit_checking(monkeypatch):
    from app.services.llm import caller
    from app.services.llm.client import LLMResponse

    # Setup FakeStreamClient with several rounds of dummy tool calls
    responses = [
        LLMResponse(
            content="",
            tool_calls=[{"id": f"call_{i}", "type": "function", "function": {"name": "dummy_tool", "arguments": "{}"}}],
            usage={"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150}
        )
        for i in range(4)
    ]
    fake_client = FakeStreamClient(responses)

    configs_called = 0
    async def mock_get_agent_config(agent_id):
        nonlocal configs_called
        configs_called += 1
        if configs_called > 1:
            return 50, "⚠️ Daily token usage limit exceeded"
        return 50, None

    monkeypatch.setattr(caller, "_get_agent_config", mock_get_agent_config)
    monkeypatch.setattr(caller, "_get_user_name", lambda _user_id: _async_return("Ray"))
    monkeypatch.setattr(
        "app.services.agent_context.build_agent_context",
        lambda *_args, **_kwargs: _async_return(("static", "dynamic")),
    )
    monkeypatch.setattr(caller, "get_agent_tools_for_llm", lambda _agent_id: _async_return([
        {"type": "function", "function": {"name": "dummy_tool", "description": "dummy"}}
    ]))
    monkeypatch.setattr(caller, "execute_tool", lambda *_args, **_kwargs: _async_return("Success"))
    monkeypatch.setattr(caller, "create_llm_client", lambda **_kwargs: fake_client)

    token_records = []
    async def mock_record_token_usage(agent_id, usage, **_kwargs):
        token_records.append(usage.total_tokens)

    monkeypatch.setattr(caller, "record_token_usage", mock_record_token_usage)

    result = await caller.call_llm(
        _model(),
        [{"role": "user", "content": "hello"}],
        "Agent",
        "",
        agent_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
    )

    # In round_i = 3 (the 4th round), it should trigger the mod-3 check,
    # find the limit is exceeded, break the loop and return the limit message.
    assert result == "⚠️ Daily token usage limit exceeded"
    # Should have called record_token_usage once in the mid-loop check after 3 rounds
    # round 0, 1, 2 usage is 150*3 = 450 tokens
    assert len(token_records) == 1
    assert token_records[0] == 450
    assert fake_client.closed is True


async def _async_return(value):
    return value


async def _async_append(items, value):
    items.append(value)
