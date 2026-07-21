"""Pure Tool Exchange integrity and window-selection tests."""

import pytest

from app.services.agent_runtime.tool_exchange import (
    ToolExchangeIntegrityError,
    build_message_blocks,
    build_recent_tool_safe_window,
    validate_tool_exchange_integrity,
)


def normal(message_id: str, *, tokens: int = 1) -> dict:
    return {"id": message_id, "role": "user", "content": message_id, "tokens": tokens}


def assistant(message_id: str, call_ids: list[str], *, tokens: int = 1) -> dict:
    return {
        "id": message_id,
        "role": "assistant",
        "content": None,
        "tokens": tokens,
        "tool_calls": [
            {
                "id": call_id,
                "type": "function",
                "function": {"name": f"tool_{call_id}", "arguments": "{}"},
            }
            for call_id in call_ids
        ],
    }


def result(
    message_id: str,
    call_id: str,
    *,
    tokens: int = 1,
    result_ref: str | None = None,
) -> dict:
    message = {
        "id": message_id,
        "role": "tool",
        "tool_call_id": call_id,
        "content": f"result:{call_id}",
        "tokens": tokens,
    }
    if result_ref is not None:
        message["result_ref"] = result_ref
    return message


def token_counter(messages) -> int:
    return sum(message.get("tokens", 0) for message in messages)


@pytest.mark.parametrize(
    ("call_count", "expected_count"),
    [(1, 21), (2, 22), (3, 23)],
)
def test_recent_20_expands_to_keep_the_boundary_exchange_whole(
    call_count, expected_count
):
    call_ids = [f"call-{index}" for index in range(call_count)]
    exchange = [assistant("assistant-exchange", call_ids)] + [
        result(f"result-{index}", call_id)
        for index, call_id in enumerate(call_ids)
    ]
    messages = [*exchange, *[normal(f"recent-{index}") for index in range(19)]]

    selection = build_recent_tool_safe_window(messages, target_messages=20)

    assert len(selection.messages) == expected_count
    assert selection.messages[0]["id"] == "assistant-exchange"
    assert [message["id"] for message in selection.messages[: len(exchange)]] == [
        message["id"] for message in exchange
    ]
    validate_tool_exchange_integrity(selection.messages)


def test_pending_exchange_before_recent_20_still_blocks_runtime_progress():
    messages = [
        assistant("assistant-pending", ["call-pending"]),
        *[normal(f"recent-{index}") for index in range(20)],
    ]
    ledger = {"call-pending": {"status": "started"}}

    selection = build_recent_tool_safe_window(messages, ledger, target_messages=20)

    assert len(selection.messages) == 20
    assert selection.messages[0]["id"] == "recent-0"
    assert selection.blocked is True
    assert selection.retry_model is False
    assert selection.omitted_blocks[0].assistant_message_id == "assistant-pending"


def test_complete_over_budget_exchange_is_summarized_without_tool_retry():
    messages = [
        assistant("assistant-1", ["call-a", "call-b"], tokens=4),
        result("result-a", "call-a", tokens=4, result_ref="artifact://a"),
        result("result-b", "call-b", tokens=4, result_ref="artifact://b"),
    ]
    ledger = {
        "call-a": {
            "status": "succeeded",
            "tool_name": "send_message",
            "side_effect_classification": "external_write",
            "result_summary": "sent",
            "result_ref": "artifact://a",
        },
        "call-b": {
            "status": "succeeded",
            "tool_name": "write_file",
            "side_effect_classification": "workspace_write",
            "result_summary": "written",
            "result_ref": "artifact://b",
        },
    }

    selection = build_recent_tool_safe_window(
        messages,
        ledger,
        token_budget=5,
        token_counter=token_counter,
    )

    assert selection.messages == ()
    assert selection.retry_model is True
    assert selection.tool_reexecution_call_ids == ()
    assert len(selection.compaction_summaries) == 1
    summary = selection.compaction_summaries[0]
    assert summary.reason == "complete_exchange_over_token_budget"
    assert summary.tool_reexecution_allowed is False
    assert [call.tool_call_id for call in summary.calls] == ["call-a", "call-b"]
    assert [call.result_ref for call in summary.calls] == ["artifact://a", "artifact://b"]


@pytest.mark.parametrize("missing_index", [0, 1, 2])
def test_missing_first_middle_or_last_parallel_result_blocks_the_whole_group(
    missing_index,
):
    call_ids = ["call-a", "call-b", "call-c"]
    messages = [assistant("assistant-1", call_ids)] + [
        result(f"result-{call_id}", call_id)
        for index, call_id in enumerate(call_ids)
        if index != missing_index
    ]
    missing_call_id = call_ids[missing_index]
    ledger = {missing_call_id: {"status": "started"}}

    blocks = build_message_blocks(messages, ledger)

    assert len(blocks) == 1
    block = blocks[0]
    assert block.kind == "pending_tool_exchange"
    assert block.call_ids == tuple(call_ids)
    assert block.missing_call_ids == (missing_call_id,)
    assert block.action == "block_reconcile"
    assert block.blocked is True
    selection = build_recent_tool_safe_window(messages, ledger)
    assert selection.messages == ()
    assert selection.blocked is True


@pytest.mark.parametrize(
    ("status", "ledger_extra", "expected_action", "confirmation"),
    [
        ("started", {}, "block_reconcile", False),
        ("unknown", {"may_have_side_effect": False}, "block_reconcile", False),
        ("unknown", {"may_have_side_effect": True}, "require_confirmation", True),
        ("not_started", {}, "block_reconcile", False),
    ],
)
def test_complete_exchange_fails_closed_on_ledger_message_conflict(
    status,
    ledger_extra,
    expected_action,
    confirmation,
):
    messages = [assistant("assistant-1", ["call-1"]), result("result-1", "call-1")]
    ledger = {"call-1": {"status": status, **ledger_extra}}

    block = build_message_blocks(messages, ledger)[0]
    selection = build_recent_tool_safe_window(messages, ledger)

    assert block.action == expected_action
    assert block.blocked is True
    assert block.requires_confirmation is confirmation
    assert selection.messages == ()
    assert selection.blocked is True
    assert selection.requires_confirmation is confirmation


def test_parallel_partial_exchange_checks_observed_result_before_missing_call():
    messages = [
        assistant("assistant-1", ["call-a", "call-b"]),
        result("result-a", "call-a"),
    ]
    ledger = {
        "call-a": {"status": "unknown", "may_have_side_effect": True},
        "call-b": {"status": "not_started"},
    }

    block = build_message_blocks(messages, ledger)[0]
    selection = build_recent_tool_safe_window(messages, ledger)

    assert block.call_ids == ("call-a", "call-b")
    assert block.missing_call_ids == ("call-b",)
    assert block.action == "require_confirmation"
    assert block.blocked is True
    assert block.requires_confirmation is True
    assert selection.messages == ()
    assert selection.requires_confirmation is True
    assert selection.retry_model is False


def test_orphan_result_is_malformed_and_never_emitted():
    messages = [result("orphan-result", "call-orphan", result_ref="artifact://orphan")]
    ledger = {
        "call-orphan": {
            "status": "succeeded",
            "tool_name": "external_write",
            "result_ref": "artifact://orphan",
        }
    }

    blocks = build_message_blocks(messages, ledger)
    selection = build_recent_tool_safe_window(messages, ledger)

    assert blocks[0].kind == "malformed_tool_exchange"
    assert blocks[0].action == "summarize"
    assert blocks[0].tool_reexecution_allowed is False
    assert selection.messages == ()
    assert selection.tool_reexecution_call_ids == ()
    assert selection.compaction_summaries[0].reason == "orphan_result"
    with pytest.raises(ToolExchangeIntegrityError, match="incomplete or orphan"):
        validate_tool_exchange_integrity(messages)


@pytest.mark.parametrize(
    "ledger",
    [
        {},
        {"call-orphan": {"status": "not_started"}},
        {"call-orphan": {}},
        {"call-orphan": {"status": "garbage"}},
    ],
)
def test_orphan_result_without_consistent_ledger_blocks_for_reconciliation(ledger):
    messages = [result("orphan-result", "call-orphan")]

    block = build_message_blocks(messages, ledger)[0]
    selection = build_recent_tool_safe_window(messages, ledger)

    assert block.kind == "malformed_tool_exchange"
    assert block.action == "block_reconcile"
    assert block.blocked is True
    assert block.retry_model is False
    assert block.compaction_summary is None
    assert selection.messages == ()
    assert selection.blocked is True
    assert selection.retry_model is False
    assert selection.compaction_summaries == ()


@pytest.mark.parametrize(
    "messages",
    [
        [assistant("assistant-1", ["call-1", "call-1"])],
        [
            assistant("assistant-1", ["call-1"]),
            result("result-1", "call-1"),
            result("result-2", "call-1"),
        ],
    ],
)
def test_duplicate_call_or_result_ids_fail_closed(messages):
    with pytest.raises(ToolExchangeIntegrityError, match="duplicate"):
        build_message_blocks(messages, {})


@pytest.mark.parametrize(
    "messages",
    [
        [
            {
                "id": "assistant-1",
                "role": "assistant",
                "tool_calls": [{"function": {"name": "tool"}}],
            }
        ],
        [{"id": "result-1", "role": "tool", "content": "done"}],
        [{"role": "user", "content": "missing stable message id"}],
    ],
)
def test_missing_stable_message_or_call_ids_fail_closed(messages):
    with pytest.raises(ToolExchangeIntegrityError, match="stable non-empty"):
        build_message_blocks(messages, {})


@pytest.mark.parametrize(
    (
        "status",
        "ledger_extra",
        "expected_action",
        "retry_model",
        "blocked",
        "confirmation",
        "has_summary",
    ),
    [
        ("not_started", {}, "retry_model", True, False, False, False),
        (
            "not_started",
            {"cancelled_before_execution": True},
            "summarize",
            False,
            False,
            False,
            True,
        ),
        ("succeeded", {"result_ref": "artifact://done"}, "summarize", True, False, False, True),
        ("started", {}, "block_reconcile", False, True, False, False),
        ("unknown", {"may_have_side_effect": True}, "require_confirmation", False, True, True, False),
    ],
)
def test_missing_result_distinguishes_model_retry_from_tool_reexecution(
    status,
    ledger_extra,
    expected_action,
    retry_model,
    blocked,
    confirmation,
    has_summary,
):
    messages = [assistant("assistant-1", ["call-1"])]
    ledger = {"call-1": {"status": status, **ledger_extra}}

    block = build_message_blocks(messages, ledger)[0]
    selection = build_recent_tool_safe_window(messages, ledger)

    assert block.action == expected_action
    assert block.retry_model is retry_model
    assert block.blocked is blocked
    assert block.requires_confirmation is confirmation
    assert block.tool_reexecution_allowed is False
    assert (block.compaction_summary is not None) is has_summary
    assert selection.messages == ()
    assert selection.retry_model is retry_model
    assert selection.blocked is blocked
    assert selection.requires_confirmation is confirmation
    assert selection.tool_reexecution_call_ids == ()
