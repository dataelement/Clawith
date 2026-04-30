"""Unit tests for tool result size-shaping."""
from app.services.llm.tool_result_shaping import shape_tool_result


def test_short_result_passes_through_unchanged():
    short = "hello world"
    out, truncated = shape_tool_result(short, max_chars=1000)
    assert out == short
    assert truncated is False


def test_exactly_at_limit_passes_through():
    s = "x" * 1000
    out, truncated = shape_tool_result(s, max_chars=1000)
    assert out == s
    assert truncated is False


def test_oversized_result_is_truncated_with_marker():
    s = "A" * 500 + "B" * 2000 + "C" * 500
    out, truncated = shape_tool_result(s, max_chars=1000)
    assert truncated is True
    assert len(out) < len(s)
    # Marker is present and mentions how much was dropped
    assert "truncated" in out.lower()
    # Head (starts with A) and tail (ends with C) both preserved
    assert out.startswith("A")
    assert out.endswith("C")


def test_marker_reports_dropped_char_count():
    s = "A" * 10_000
    out, truncated = shape_tool_result(s, max_chars=1000)
    assert truncated is True
    # The marker should contain the number of dropped characters
    assert "9" in out  # ~9000 dropped


def test_zero_budget_returns_empty():
    """max_chars=0 is a degenerate budget; return empty string and
    report truncation when any content was dropped."""
    out, truncated = shape_tool_result("hello", 0)
    assert out == ""
    assert truncated is True


def test_zero_budget_with_empty_input():
    """Empty input under a zero budget is still not truncation."""
    out, truncated = shape_tool_result("", 0)
    assert out == ""
    assert truncated is False


def test_negative_budget_degenerates_gracefully():
    """Negative max_chars should not silently produce garbage.
    Regression: prior regex-free slicing produced overlapping slices
    with a lying marker (output longer than input)."""
    out, truncated = shape_tool_result("hello world", -5)
    assert out == ""
    assert truncated is True


def test_output_length_respects_budget():
    s = "x" * 100_000
    out, truncated = shape_tool_result(s, max_chars=1000)
    # Output should be <= max_chars + reasonable marker overhead (~200 chars)
    assert len(out) <= 1000 + 200
    assert truncated is True


def test_empty_result():
    out, truncated = shape_tool_result("", max_chars=1000)
    assert out == ""
    assert truncated is False


def test_non_string_coerced_to_string():
    out, truncated = shape_tool_result(12345, max_chars=1000)
    assert out == "12345"
    assert truncated is False
