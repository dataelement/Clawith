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

Orphan detection is by ``tool_call_id`` matching, not by adjacency — a
tool message inserted between a valid pair and other messages (from
malformed persistence or upstream truncation) is dropped, not folded
into an adjacent block. This makes the helper robust against orphans
at any position, not just at the slice head.

Input is expected to be in OpenAI chat-completion format (post-reorganization
from DB ``role="tool_call"`` rows).
"""

from __future__ import annotations

from typing import Any


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


def truncate_by_message_count(
    messages: list[dict[str, Any]],
    max_messages: int,
) -> list[dict[str, Any]]:
    """Keep at most ``max_messages`` recent messages, preserving tool-call pairs.

    A "block" is either:
      - a single non-tool, non-tool-calling message (user / system / assistant text), or
      - an ``assistant`` with ``tool_calls`` plus every matching ``role="tool"``
        message (identified by ``tool_call_id``, not adjacency).

    Blocks are atomic: included whole or not at all. Orphan ``role="tool"``
    messages — those whose ``tool_call_id`` has no matching assistant — are
    silently dropped regardless of budget. Sending them to OpenAI causes the
    #446 error.

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

    orphans = _identify_orphans(messages)
    n = len(messages)
    consumed: set[int] = set(orphans)  # orphans drop unconditionally
    blocks: list[set[int]] = []  # tail-to-head order

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

    # Walk blocks tail-to-head, taking until budget exhausted.
    keep: set[int] = set()
    budget = max_messages
    for block in blocks:
        size = len(block)
        if size <= budget:
            keep |= block
            budget -= size
        else:
            # Block doesn't fit — stop. Do NOT partial-include (would split pair).
            break

    return [messages[k] for k in sorted(keep)]
