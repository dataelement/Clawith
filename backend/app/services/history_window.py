"""Tool-block-safe conversation history truncation.

Replaces naive ``conversation[-N:]`` slicing with a walker that keeps
``assistant.tool_calls`` and their matching ``role="tool"`` messages as an
atomic block — never half a pair, never orphan tool messages.

Why: OpenAI Responses API and Chat Completions both reject input where a
``function_call_output`` / ``role="tool"`` message has no matching
``function_call`` / ``assistant.tool_calls`` earlier in the input. Naive
``[-N:]`` slicing can leave such orphans at the head when the cut lands
between an assistant message and its tool results. This is the failure mode
reported in issue #446.

Tool results must be in the contiguous tool-result run immediately after
their owning assistant. A tool message inserted elsewhere (from malformed
persistence or upstream truncation) is dropped, not folded into an adjacent
block. This makes the helper robust against orphans at any position, not just
at the slice head.

Incomplete assistant tool-call blocks are also dropped. If an assistant
declares multiple tool calls, every declared ``tool_call_id`` must have a
matching ``role="tool"`` result before the next non-tool message. This mirrors
the API contract enforced by OpenAI-compatible providers and avoids sending
synthetic/fake tool results into weaker models' context.

Input is expected to be in OpenAI chat-completion format (post-reorganization
from DB ``role="tool_call"`` rows).
"""

from __future__ import annotations

from typing import Any


def _assistant_tool_call_ids(message: dict[str, Any]) -> list[str]:
    """Return non-empty tool call ids declared by an assistant message."""
    if message.get("role") != "assistant":
        return []
    tool_calls = message.get("tool_calls")
    if not isinstance(tool_calls, list):
        return []

    ids: list[str] = []
    for tool_call in tool_calls:
        if isinstance(tool_call, dict):
            tool_call_id = tool_call.get("id")
            if isinstance(tool_call_id, str) and tool_call_id:
                ids.append(tool_call_id)
    return ids


def _safe_history_blocks(messages: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """Build API-safe message blocks in original order.

    A valid tool block is an assistant message with tool calls followed by
    contiguous matching ``role="tool"`` results. A missing result invalidates
    the whole block; orphan/duplicate tool results are consumed and dropped.
    """
    blocks: list[list[dict[str, Any]]] = []
    i = 0
    n = len(messages)

    while i < n:
        message = messages[i]
        role = message.get("role")

        if role == "tool":
            # Orphan or delayed tool result. It is invalid without the owning
            # assistant immediately before the tool-result run.
            i += 1
            continue

        tool_call_ids = _assistant_tool_call_ids(message)
        if not tool_call_ids:
            blocks.append([message])
            i += 1
            continue

        required = set(tool_call_ids)
        seen: set[str] = set()
        block = [message]
        j = i + 1

        while j < n and messages[j].get("role") == "tool":
            tool_message = messages[j]
            tool_call_id = tool_message.get("tool_call_id")
            if (
                isinstance(tool_call_id, str)
                and tool_call_id in required
                and tool_call_id not in seen
            ):
                seen.add(tool_call_id)
                block.append(tool_message)
            # Consume every contiguous tool message here. Non-matching or
            # duplicate tool results are invalid for this block and are dropped
            # instead of being allowed to become later orphan messages.
            j += 1

        if seen == required:
            blocks.append(block)
        # If incomplete, drop the assistant and any partial tool results. Old
        # history truncation should discard broken blocks rather than inventing
        # synthetic tool results.
        i = j

    return blocks


def truncate_by_message_count(
    messages: list[dict[str, Any]],
    max_messages: int,
) -> list[dict[str, Any]]:
    """Keep at most ``max_messages`` recent messages, preserving tool-call pairs.

    A "block" is either:
      - a single non-tool, non-tool-calling message (user / system / assistant text), or
      - an ``assistant`` with ``tool_calls`` plus every matching contiguous
        ``role="tool"`` message.

    Blocks are atomic: included whole or not at all. Orphan ``role="tool"``
    messages and incomplete assistant tool-call blocks are silently dropped
    regardless of budget. Sending either shape to OpenAI causes the #446 class
    of errors.

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

    blocks = _safe_history_blocks(messages)
    selected: list[list[dict[str, Any]]] = []
    budget = max_messages
    for block in reversed(blocks):
        size = len(block)
        if size <= budget:
            selected.append(block)
            budget -= size
        else:
            # Block doesn't fit — stop. Do NOT partial-include (would split pair).
            break

    return [message for block in reversed(selected) for message in block]
