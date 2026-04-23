"""Integration tests for caller._process_tool_call normalization."""
import json
import pytest
from unittest.mock import patch

from app.services.llm.caller import _process_tool_call


@pytest.mark.asyncio
async def test_process_tool_call_canonicalizes_malformed_arguments():
    """Malformed arguments (trailing comma) must be rewritten to valid JSON
    on tc['function']['arguments'] so later LLM rounds get clean history."""
    tc = {
        "id": "call_1",
        "function": {
            "name": "read_file",
            # Trailing comma — Qwen streaming produces this sometimes
            "arguments": '{"path": "foo.md",}',
        },
    }
    api_messages: list = []

    async def fake_execute_tool(name, args, **kwargs):
        assert name == "read_file"
        assert args == {"path": "foo.md"}
        return "file contents here"

    with patch("app.services.llm.caller.execute_tool", side_effect=fake_execute_tool):
        await _process_tool_call(
            tc=tc,
            api_messages=api_messages,
            agent_id="agent-1",
            user_id="user-1",
            session_id="sess-1",
            supports_vision=False,
            on_tool_call=None,
            full_reasoning_content="",
        )

    # CRITICAL: arguments on the tc object must now be valid JSON
    repaired = tc["function"]["arguments"]
    parsed = json.loads(repaired)
    assert parsed == {"path": "foo.md"}
    # And it must not have a trailing comma
    assert ",}" not in repaired.replace(" ", "")


@pytest.mark.asyncio
async def test_process_tool_call_truncates_oversized_result():
    """Tool results over the cap must be head+tail truncated in the
    api_messages tool-result entry."""
    tc = {
        "id": "call_1",
        "function": {"name": "example_tool", "arguments": '{"command": ["report", "list"]}'},
    }
    api_messages: list = []
    huge_result = "A" * 200_000  # 200KB result

    async def fake_execute_tool(name, args, **kwargs):
        return huge_result

    with patch("app.services.llm.caller.execute_tool", side_effect=fake_execute_tool):
        await _process_tool_call(
            tc=tc,
            api_messages=api_messages,
            agent_id="agent-1",
            user_id="user-1",
            session_id="sess-1",
            supports_vision=False,
            on_tool_call=None,
            full_reasoning_content="",
        )

    tool_msg = api_messages[-1]
    # Stored content should be capped and contain the truncation marker
    content = tool_msg.content if isinstance(tool_msg.content, str) else str(tool_msg.content)
    from app.services.llm.caller import TOOL_RESULT_MAX_CHARS
    # Cap is TOOL_RESULT_MAX_CHARS + small marker overhead (~120 chars).
    assert len(content) <= TOOL_RESULT_MAX_CHARS + 500
    assert "truncated" in content.lower()


@pytest.mark.asyncio
async def test_process_tool_call_clean_arguments_pass_through_unchanged_semantic():
    """Clean JSON must still work exactly as before (backwards compat)."""
    tc = {
        "id": "call_1",
        "function": {
            "name": "read_file",
            "arguments": '{"path": "foo.md"}',
        },
    }
    api_messages: list = []

    async def fake_execute_tool(name, args, **kwargs):
        assert args == {"path": "foo.md"}
        return "ok"

    with patch("app.services.llm.caller.execute_tool", side_effect=fake_execute_tool):
        await _process_tool_call(
            tc=tc, api_messages=api_messages,
            agent_id="agent-1", user_id="user-1", session_id="sess-1",
            supports_vision=False, on_tool_call=None, full_reasoning_content="",
        )

    # Semantic equivalence (key order / spacing may differ)
    assert json.loads(tc["function"]["arguments"]) == {"path": "foo.md"}


def test_canonicalize_tc_arguments_helper_rewrites_tc_inplace():
    """Unit test the helper directly — exercised by both _process_tool_call
    and call_agent_llm_with_tools._try_model."""
    from app.services.llm.caller import _canonicalize_tc_arguments
    tc = {
        "id": "call_1",
        "function": {"name": "read_file", "arguments": '{"path": "foo.md",}'},
    }
    args = _canonicalize_tc_arguments(tc, session_id="sess-x")
    assert args == {"path": "foo.md"}
    # In-place mutation: tc now carries canonical JSON
    import json
    parsed = json.loads(tc["function"]["arguments"])
    assert parsed == {"path": "foo.md"}


def test_shape_tool_content_for_context_bypasses_list_content():
    """Vision content (list of parts) must pass through untouched."""
    from app.services.llm.caller import _shape_tool_content_for_context
    vision_content = [{"type": "text", "text": "see image"}, {"type": "image_url", "image_url": {"url": "data:image/png;base64,..."}}]
    out = _shape_tool_content_for_context(vision_content, "screenshot", "sess-x")
    assert out is vision_content  # identity — no copy, no mutation


def test_shape_tool_content_for_context_truncates_long_string():
    """Oversized string content is capped."""
    from app.services.llm.caller import _shape_tool_content_for_context, TOOL_RESULT_MAX_CHARS
    out = _shape_tool_content_for_context("A" * 200_000, "example_tool", "sess-x")
    assert isinstance(out, str)
    assert len(out) <= TOOL_RESULT_MAX_CHARS + 500
    assert "truncated" in out.lower()
