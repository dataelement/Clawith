"""Pair-aware conversation history truncation.

Replaces naive ``conversation[-N:]`` slicing with a walker that keeps
``assistant.tool_calls`` and their matching ``role="tool"`` messages as an
atomic block — never half a pair, never orphan tool messages.

Why: OpenAI Responses API and Chat Completions both reject input where a
``function_call_output`` / ``role="tool"`` message has no matching
``function_call`` / ``assistant.tool_calls`` earlier in the input. Naive
``[-N:]`` slicing can leave such orphans at the head when the cut lands
between an assistant message and its tool results. This is the failure mode
reported in issue #446.

Two public entry points:
  - ``truncate_by_message_count`` — bound by message count
  - ``truncate_by_token_budget`` — bound by estimated token cost (and an
    optional message-count safety cap); preferred for production paths
    where one tool result can dwarf 50 short messages.

Input is expected to be in OpenAI chat-completion format (post-reorganization
from DB ``role="tool_call"`` rows). Helper is tolerant of malformed input —
unmatched tool messages at the head are silently dropped.
"""

from __future__ import annotations

import json
from typing import Any, Callable

from app.services.token_tracker import estimate_tokens_from_chars


# ── Block detection (shared between truncators) ─────────────────────────


def _identify_orphans(messages: list[dict[str, Any]]) -> set[int]:
    """Return indices of ``role="tool"`` messages whose ``tool_call_id`` has
    no matching ``assistant.tool_calls`` earlier in the conversation.

    OpenAI rejects the request the moment a ``function_call_output`` is
    sent without its matching ``function_call``, regardless of whether
    that tool message is at the head, middle, or end. So orphan detection
    is by ID matching, not by position.
    """
    orphans: set[int] = set()
    for i, msg in enumerate(messages):
        if msg.get("role") != "tool":
            continue
        tcid = msg.get("tool_call_id")
        if not tcid:
            orphans.add(i)
            continue
        # Search backward for an assistant whose tool_calls contains this id.
        # Walks past intervening user / system / other-assistant messages.
        found = False
        j = i - 1
        while j >= 0:
            m = messages[j]
            if m.get("role") == "assistant" and m.get("tool_calls"):
                ids = {tc.get("id") for tc in m["tool_calls"]}
                if tcid in ids:
                    found = True
                    break
            j -= 1
        if not found:
            orphans.add(i)
    return orphans


def _identify_blocks(messages: list[dict[str, Any]]) -> list[set[int]]:
    """Group conversation entries into atomic blocks.

    A block is a set of indices that must be kept (or dropped) together:
      - ``{i}`` for a single non-tool, non-tool-calling message
      - ``{asst_idx, tool_idx_1, tool_idx_2, ...}`` for an assistant that
        emitted N tool_calls plus its matching tool result messages,
        identified by ``tool_call_id`` (not by adjacency — orphan tools
        inserted between are dropped, not folded into the block).

    Returned tail-to-head: most recent block first. Orphan tool messages
    (those whose tool_call_id has no matching assistant.tool_calls) are
    silently dropped — never appear in any block.
    """
    orphans = _identify_orphans(messages)
    n = len(messages)
    blocks: list[set[int]] = []
    consumed: set[int] = set(orphans)  # orphans drop unconditionally

    for i in range(n - 1, -1, -1):
        if i in consumed:
            continue
        msg = messages[i]
        role = msg.get("role")

        if role == "tool":
            # Find this tool's owning assistant by matching tool_call_id
            tcid = msg.get("tool_call_id")
            asst_idx = -1
            j = i - 1
            while j >= 0:
                m = messages[j]
                if m.get("role") == "assistant" and m.get("tool_calls"):
                    ids = {tc.get("id") for tc in m["tool_calls"]}
                    if tcid in ids:
                        asst_idx = j
                        break
                j -= 1
            if asst_idx < 0:
                # Defensive — orphan detection should have caught this
                consumed.add(i)
                continue
            # Block = assistant + ALL of its matching tool messages (siblings)
            asst_tc_ids = {tc.get("id") for tc in messages[asst_idx]["tool_calls"]}
            block = {asst_idx}
            for k in range(asst_idx + 1, n):
                if k in consumed:
                    continue
                m = messages[k]
                if (
                    m.get("role") == "tool"
                    and m.get("tool_call_id") in asst_tc_ids
                ):
                    block.add(k)
            consumed |= block
            blocks.append(block)
        elif role == "assistant" and msg.get("tool_calls"):
            # Encountered the assistant before any of its tools (e.g. tools
            # were truncated upstream or are still in flight). Group with
            # whatever matching tools follow it.
            asst_tc_ids = {tc.get("id") for tc in msg["tool_calls"]}
            block = {i}
            for k in range(i + 1, n):
                if k in consumed:
                    continue
                m = messages[k]
                if (
                    m.get("role") == "tool"
                    and m.get("tool_call_id") in asst_tc_ids
                ):
                    block.add(k)
            consumed |= block
            blocks.append(block)
        else:
            consumed.add(i)
            blocks.append({i})

    return blocks


def _walk_blocks(
    messages: list[dict[str, Any]],
    budgets_ok: Callable[[int, int], bool],
    consume: Callable[[int, int], None],
) -> list[dict[str, Any]]:
    """Common walker used by both truncators.

    ``budgets_ok(block_msg_count, block_token_cost)`` returns True if the
    block fits. ``consume`` updates remaining budget when a block is taken.
    Stops on first non-fitting block (atomic — never partial-include).
    """
    blocks = _identify_blocks(messages)
    keep: set[int] = set()
    for block in blocks:
        size = len(block)
        token_cost = sum(_estimate_msg_tokens(messages[k]) for k in block)
        if not budgets_ok(size, token_cost):
            break
        keep |= block
        consume(size, token_cost)
    return [messages[k] for k in sorted(keep)]


def _estimate_msg_tokens(msg: dict[str, Any]) -> int:
    """Estimate token cost for one message via JSON-serialized char count.

    Slight overestimate (JSON keys/quotes inflate vs the tokenizer's view of
    the structured payload), which is the safe direction — better to truncate
    a bit early than send too much and OOM the model.
    """
    try:
        serialized = json.dumps(msg, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        # Fallback for unserializable payloads (shouldn't happen in practice)
        serialized = str(msg)
    return estimate_tokens_from_chars(len(serialized))


# ── Public API ──────────────────────────────────────────────────────────


def truncate_by_message_count(
    messages: list[dict[str, Any]],
    max_messages: int,
) -> list[dict[str, Any]]:
    """Keep at most ``max_messages`` recent messages, preserving tool-call pairs.

    A "block" is either:
      - a single non-tool message (``user``/``system``/``assistant`` text), or
      - an ``assistant`` with ``tool_calls`` plus every immediately-following
        ``role="tool"`` message (the assistant's tool results).

    Blocks are atomic: included whole or not at all. Orphan ``role="tool"``
    messages with no matching assistant are always dropped, regardless of
    budget — sending them to OpenAI causes the #446 error.

    Args:
        messages: Conversation list in OpenAI format. Empty list is fine.
        max_messages: Soft upper bound on the number of returned entries.
            Values ``<= 0`` return ``[]``.

    Returns:
        A new list (input is never mutated) of at most ``max_messages`` entries
        from the tail of ``messages``, with all tool-call pairs intact.
    """
    if max_messages <= 0 or not messages:
        return []
    remaining = [max_messages]

    def budgets_ok(size: int, _tok: int) -> bool:
        return size <= remaining[0]

    def consume(size: int, _tok: int) -> None:
        remaining[0] -= size

    return _walk_blocks(messages, budgets_ok, consume)


def truncate_by_token_budget(
    messages: list[dict[str, Any]],
    token_budget: int,
    *,
    message_cap: int | None = None,
) -> list[dict[str, Any]]:
    """Keep tail messages within both bounds, preserving tool-call pairs.

    The two bounds work together: a block is included only if both the
    remaining token budget and (when set) remaining message cap can absorb
    its full size. The first bound to be exhausted stops the walk.

    Token cost per message is an overestimate based on JSON-serialized char
    count divided by ~3 (see ``_estimate_msg_tokens``). This is intentional:
    for budget enforcement, overestimating is safe.

    Args:
        messages: Conversation list in OpenAI format.
        token_budget: Soft upper bound on cumulative estimated tokens.
            Values ``<= 0`` return ``[]``.
        message_cap: Optional secondary bound on entry count. When set, the
            walk stops as soon as either bound is exhausted.

    Returns:
        A new list of recent messages within the budget(s), with all
        tool-call pairs intact.
    """
    if token_budget <= 0 or not messages:
        return []
    if message_cap is not None and message_cap <= 0:
        return []

    tok_remaining = [token_budget]
    msg_remaining = [message_cap if message_cap is not None else len(messages) + 1]

    def budgets_ok(size: int, tok_cost: int) -> bool:
        return size <= msg_remaining[0] and tok_cost <= tok_remaining[0]

    def consume(size: int, tok_cost: int) -> None:
        msg_remaining[0] -= size
        tok_remaining[0] -= tok_cost

    return _walk_blocks(messages, budgets_ok, consume)
