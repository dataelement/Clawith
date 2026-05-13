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


def _plain_response(content: str):
    from app.services.llm.client import LLMResponse

    return LLMResponse(content=content, tool_calls=[])


def _model():
    return SimpleNamespace(
        provider="openai",
        model="fake-model",
        base_url="https://example.invalid/v1",
        api_key_encrypted="",
        temperature=0,
        max_output_tokens=256,
        request_timeout=1,
    )


def test_finish_tool_schema_is_default_and_requires_content():
    from app.services.llm.finish import FINISH_TOOL_DEFINITION, FINISH_TOOL_SEED

    assert FINISH_TOOL_DEFINITION["function"]["name"] == "finish"
    assert FINISH_TOOL_DEFINITION["function"]["parameters"]["required"] == ["content"]
    assert FINISH_TOOL_SEED["name"] == "finish"
    assert FINISH_TOOL_SEED["is_default"] is True
    assert FINISH_TOOL_SEED["parameters_schema"]["required"] == ["content"]


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


@pytest.mark.asyncio
async def test_call_llm_requires_finish_tool_to_stop(monkeypatch):
    from app.services.llm import caller
    from app.services.llm.finish import FINISH_PROTOCOL_REMINDER

    fake_client = FakeStreamClient([
        _plain_response("This should not stop."),
        _finish_response("Final answer."),
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
    assert chunks == []
    second_round_messages = fake_client.messages_seen[1]
    assert any(
        msg.role == "user" and msg.content == FINISH_PROTOCOL_REMINDER
        for msg in second_round_messages
    )
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
async def test_skip_tools_still_exposes_finish(monkeypatch):
    from app.services.llm import caller

    fake_client = FakeStreamClient([_finish_response("Onboarding done.")])

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
    assert tool_names == ["finish"]


@pytest.mark.asyncio
async def test_execute_tool_finish_is_noop_control_signal(monkeypatch):
    from app.services import agent_tools

    async def fail_ensure_workspace(*_args, **_kwargs):
        raise AssertionError("finish should not touch workspace setup")

    monkeypatch.setattr(agent_tools, "ensure_workspace", fail_ensure_workspace)

    result = await agent_tools.execute_tool(
        "finish",
        {"content": "Visible answer"},
        agent_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
    )

    assert result == "Visible answer"


def test_finish_is_in_always_available_core_tools():
    from app.services.agent_tools import _ALWAYS_INCLUDE_CORE

    assert "finish" in _ALWAYS_INCLUDE_CORE


async def _async_return(value):
    return value


async def _async_append(items, value):
    items.append(value)
