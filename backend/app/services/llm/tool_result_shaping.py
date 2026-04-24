"""Shape oversized tool results to stay within per-call size budget.

A single tool result (e.g. a long a long tool output JSON, or an
`execute_code` stdout dump) can exceed 50KB. Accumulating many such results
across 10+ tool rounds blows past Qwen3.5-plus's ~983k-char input limit and
causes `HTTP 400: Range of input length should be [1, 983616]`.

This module applies a head+tail truncation with an explicit marker so the
LLM can see that truncation happened and ask for more if needed.

A degenerate budget (``max_chars <= 0``) returns an empty string, with
``was_truncated=True`` iff the input was non-empty.
"""
from __future__ import annotations


def shape_tool_result(result, max_chars: int) -> tuple[str, bool]:
    """Return (possibly-truncated string, was_truncated).

    Strategy for oversized results: keep ~60% head and ~30% tail, with a
    marker in between describing how much was dropped. Total output stays
    within max_chars plus a small marker overhead (~120 chars).

    Edge case: if ``max_chars <= 0`` the budget is degenerate — there is no
    room for any content (nor for the marker itself), so an empty string is
    returned, with ``was_truncated=True`` iff the input was non-empty.
    """
    s = str(result) if not isinstance(result, str) else result
    if max_chars <= 0:
        # Degenerate budget — treat as "drop everything", no marker (it would
        # exceed max_chars itself). was_truncated reflects whether any content
        # was actually dropped.
        return "", len(s) > 0
    if len(s) <= max_chars:
        return s, False

    # Budget split: 60% head, 30% tail, 10% safety
    head_budget = int(max_chars * 0.60)
    tail_budget = int(max_chars * 0.30)
    dropped = len(s) - head_budget - tail_budget
    marker = f"\n\n[... truncated: {dropped:,} chars omitted (head {head_budget:,} + tail {tail_budget:,} kept) ...]\n\n"

    return s[:head_budget] + marker + s[-tail_budget:], True
