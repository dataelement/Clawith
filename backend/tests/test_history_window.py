"""Unit tests for pair-aware conversation history truncation.

Validates that ``truncate_by_message_count`` preserves
``assistant.tool_calls`` ↔ ``role="tool"`` blocks atomically — never produces
orphan tool messages that would trigger the OpenAI #446 failure mode.
"""

from app.services.history_window import truncate_by_message_count


# ── Helpers ─────────────────────────────────────────────────────────────


def _u(text: str) -> dict:
    return {"role": "user", "content": text}


def _a(text: str | None = None, tool_calls: list[dict] | None = None) -> dict:
    msg: dict = {"role": "assistant", "content": text}
    if tool_calls:
        msg["tool_calls"] = tool_calls
    return msg


def _tc(call_id: str, name: str = "noop", args: str = "{}") -> dict:
    return {"id": call_id, "type": "function", "function": {"name": name, "arguments": args}}


def _t(call_id: str, content: str = "ok") -> dict:
    return {"role": "tool", "tool_call_id": call_id, "content": content}


def _roles(msgs: list[dict]) -> list[str]:
    return [m.get("role", "?") for m in msgs]


# ── Edge cases ──────────────────────────────────────────────────────────


def test_empty_input_returns_empty():
    assert truncate_by_message_count([], 10) == []


def test_zero_or_negative_budget_returns_empty():
    msgs = [_u("hi"), _u("there")]
    assert truncate_by_message_count(msgs, 0) == []
    assert truncate_by_message_count(msgs, -5) == []


def test_within_budget_returns_all():
    msgs = [_u("a"), _a("b"), _u("c")]
    out = truncate_by_message_count(msgs, 10)
    assert out == msgs
    assert out is not msgs  # new list


def test_input_not_mutated():
    msgs = [_u("a"), _a("b"), _u("c"), _u("d")]
    snapshot = list(msgs)
    truncate_by_message_count(msgs, 2)
    assert msgs == snapshot


# ── Core pair-preservation behavior ─────────────────────────────────────


def test_keeps_assistant_tool_pair_intact():
    """Slicing must not split assistant.tool_calls from its tool result."""
    msgs = [
        _u("hi"),
        _a(None, tool_calls=[_tc("X")]),
        _t("X"),
        _u("done?"),
    ]
    # Budget 3 — would naively keep [a+tc(X), t(X), u("done?")], that's clean
    out = truncate_by_message_count(msgs, 3)
    assert _roles(out) == ["assistant", "tool", "user"]
    assert out[0]["tool_calls"][0]["id"] == "X"
    assert out[1]["tool_call_id"] == "X"


def test_drops_pair_entirely_when_budget_too_small():
    """If budget can't fit the whole pair, drop it — never half."""
    msgs = [
        _u("hi"),
        _a(None, tool_calls=[_tc("X")]),
        _t("X"),
        _u("done?"),
    ]
    # Budget 2 — can't fit pair (needs 2) + final user, must drop pair
    out = truncate_by_message_count(msgs, 2)
    # Only the trailing user fits as a single block; pair (size 2) doesn't fit
    # in remaining budget=1 after taking user.
    assert _roles(out) == ["user"]
    assert out[0]["content"] == "done?"


def test_drops_orphan_tool_at_head():
    """A role=tool with no preceding assistant.tool_calls is dropped."""
    msgs = [
        _t("X"),  # orphan — no assistant before
        _u("hi"),
        _a("ok"),
    ]
    out = truncate_by_message_count(msgs, 10)
    assert _roles(out) == ["user", "assistant"]


def test_drops_orphan_tool_at_head_after_slicing():
    """Slicing produces an orphan tool at head — must be dropped (the
    classic #446 failure mode)."""
    msgs = [
        _u("u1"),
        _a(None, tool_calls=[_tc("X")]),
        _t("X"),       # ← naive slice [-3:] would start here as orphan
        _u("u2"),
        _a("final"),
    ]
    # Budget 3: take from end. _a("final") block. _u("u2") block. Then t(X)
    # alone — orphan, dropped. Pair (a+tc, t) doesn't get full chance because
    # we'd need budget 5 to include from start. Result: [u("u2"), a("final")].
    out = truncate_by_message_count(msgs, 3)
    assert "tool" not in _roles(out)
    # No orphan tool_call_id reaches output
    for m in out:
        if m.get("role") == "tool":
            raise AssertionError(f"Orphan tool leaked: {m}")


def test_multiple_parallel_tool_calls_in_one_assistant():
    """Assistant with N tool_calls followed by N tools is one atomic block."""
    msgs = [
        _u("u1"),
        _a(None, tool_calls=[_tc("X"), _tc("Y"), _tc("Z")]),
        _t("X"),
        _t("Y"),
        _t("Z"),
        _u("u2"),
    ]
    # Budget 5: take u("u2"), then the 4-entry block (a + 3 tools). budget=5-1-4=0
    out = truncate_by_message_count(msgs, 5)
    assert _roles(out) == ["assistant", "tool", "tool", "tool", "user"]
    # Verify the pair came through whole
    assert out[0]["tool_calls"][0]["id"] == "X"
    assert out[3]["tool_call_id"] == "Z"


def test_parallel_tool_pair_dropped_if_too_big():
    msgs = [
        _u("u1"),
        _a(None, tool_calls=[_tc("X"), _tc("Y"), _tc("Z")]),
        _t("X"),
        _t("Y"),
        _t("Z"),
        _u("u2"),
    ]
    # Budget 3: take u("u2"). Pair size 4, doesn't fit budget 2. Stop. Output [u].
    out = truncate_by_message_count(msgs, 3)
    assert _roles(out) == ["user"]


def test_multiple_pairs_some_drop():
    msgs = [
        _u("u1"),
        _a(None, tool_calls=[_tc("A")]),
        _t("A"),
        _u("u2"),
        _a(None, tool_calls=[_tc("B")]),
        _t("B"),
        _u("u3"),
    ]
    # 7 entries. Budget 5: take u("u3") (1), pair B (2) → budget=2, take u("u2") (1) → budget=1, pair A (2) doesn't fit. Output: u2, a+B, t(B), u3.
    out = truncate_by_message_count(msgs, 5)
    assert _roles(out) == ["user", "assistant", "tool", "user"]
    assert out[1]["tool_calls"][0]["id"] == "B"
    assert out[2]["tool_call_id"] == "B"


def test_no_partial_pair_when_budget_exactly_one_short():
    """Exactly one short of fitting a pair → drop the pair, don't include
    just the assistant."""
    msgs = [
        _u("u1"),
        _a(None, tool_calls=[_tc("X")]),
        _t("X"),
    ]
    # Budget 2: pair size 2, fits → [a+tc, t]. (u dropped to fit pair? No — walk
    # from end: t(X) goes back to a(tc=X) → pair block (1,2) size 2. Then u (0,0)
    # size 1. Take pair first, budget=0. Stop. Output: [a+tc, t]
    out = truncate_by_message_count(msgs, 2)
    assert _roles(out) == ["assistant", "tool"]
    # If only budget 1: pair size 2 doesn't fit. Then look at u (size 1, fits).
    # But blocks order is [(1,2), (0,0)] from walk. We try pair first, doesn't
    # fit, BREAK. Output: [].
    out2 = truncate_by_message_count(msgs, 1)
    assert out2 == []


def test_mid_orphan_tool_dropped():
    """A tool whose tool_call_id has no matching assistant nearby — defensive
    drop. (Shouldn't happen with current persistence, but be robust.)"""
    msgs = [
        _u("u1"),
        _t("ORPHAN_X"),  # malformed — no preceding assistant.tool_calls
        _u("u2"),
    ]
    out = truncate_by_message_count(msgs, 10)
    # Orphan dropped
    assert "tool" not in _roles(out)
    assert _roles(out) == ["user", "user"]


def test_orphan_adjacent_to_valid_pair_still_dropped():
    """Orphan tool message inserted right after a legitimate tool-call pair
    must be dropped — adjacency to a valid pair does not legitimize it.

    This is the bug class that triggers OpenAI #446 even when slice cut
    boundaries would otherwise be safe: any orphan reaching the wire,
    regardless of position, makes the request invalid."""
    msgs = [
        _u("u1"),
        _a(None, tool_calls=[_tc("VALID")]),
        _t("VALID", "real result"),
        _t("ORPHAN_id", "ghost result"),  # no assistant emits ORPHAN_id
        _u("u2"),
    ]
    out = truncate_by_message_count(msgs, 10)

    # The orphan must NOT survive — even though it's adjacent to a valid pair
    orphan_present = any(
        m.get("role") == "tool" and m.get("tool_call_id") == "ORPHAN_id"
        for m in out
    )
    assert not orphan_present, "Orphan tool adjacent to valid pair must be dropped"

    # The valid pair survives intact
    valid_assistant = any(
        m.get("role") == "assistant"
        and m.get("tool_calls")
        and any(tc["id"] == "VALID" for tc in m["tool_calls"])
        for m in out
    )
    valid_tool = any(
        m.get("role") == "tool" and m.get("tool_call_id") == "VALID"
        for m in out
    )
    assert valid_assistant and valid_tool


def test_system_message_treated_as_normal_block():
    msgs = [
        {"role": "system", "content": "you are an agent"},
        _u("hi"),
        _a("hello"),
    ]
    out = truncate_by_message_count(msgs, 2)
    # Walk from end: a (size 1), u (size 1). budget 2: take both. system dropped.
    assert _roles(out) == ["user", "assistant"]


def test_realistic_long_conversation_truncation():
    """End-to-end: simulate a long chat with many tool-call turns and ensure
    the output never has orphan tools."""
    msgs: list[dict] = [_u("start")]
    for k in range(20):
        msgs.append(_a(None, tool_calls=[_tc(f"call_{k}")]))
        msgs.append(_t(f"call_{k}", content=f"result {k}"))
        msgs.append(_u(f"next {k}"))
    msgs.append(_a("final answer"))

    # Truncate to 30 messages
    out = truncate_by_message_count(msgs, 30)

    # Sanity: budget respected
    assert len(out) <= 30

    # Critical invariant: no orphan tool messages anywhere
    seen_tool_call_ids: set[str] = set()
    for m in out:
        if m.get("role") == "assistant" and m.get("tool_calls"):
            for tc in m["tool_calls"]:
                seen_tool_call_ids.add(tc["id"])
    for m in out:
        if m.get("role") == "tool":
            tcid = m.get("tool_call_id")
            assert tcid in seen_tool_call_ids, (
                f"Orphan tool {tcid!r} in output without matching assistant.tool_calls"
            )
