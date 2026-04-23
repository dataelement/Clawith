"""Unit tests for tool_call.arguments JSON recovery helpers."""
from app.services.llm.json_recovery import canonicalize_tool_arguments


def test_clean_json_passes_through():
    raw = '{"path": "foo.md", "content": "hello"}'
    parsed, canonical, method = canonicalize_tool_arguments(raw)
    assert parsed == {"path": "foo.md", "content": "hello"}
    assert method == "clean"
    # canonical is still valid JSON and round-trips
    import json
    assert json.loads(canonical) == parsed


def test_trailing_comma_in_object_is_repaired():
    raw = '{"path": "foo.md", "content": "hi",}'
    parsed, canonical, method = canonicalize_tool_arguments(raw)
    assert parsed == {"path": "foo.md", "content": "hi"}
    assert method == "trailing_comma"
    import json
    assert json.loads(canonical) == parsed


def test_trailing_comma_in_array_is_repaired():
    raw = '{"items": [1, 2, 3,]}'
    parsed, canonical, method = canonicalize_tool_arguments(raw)
    assert parsed == {"items": [1, 2, 3]}
    assert method == "trailing_comma"


def test_unescaped_newline_inside_string_is_repaired():
    # Qwen streaming sometimes produces raw \n inside a string value
    raw = '{"content": "line1\nline2"}'
    parsed, canonical, method = canonicalize_tool_arguments(raw)
    assert parsed == {"content": "line1\nline2"}
    assert method == "control_char_escape"
    import json
    # canonical round-trip preserves semantic content
    assert json.loads(canonical)["content"] == "line1\nline2"


def test_unescaped_tab_inside_string_is_repaired():
    raw = '{"content": "a\tb"}'
    parsed, canonical, method = canonicalize_tool_arguments(raw)
    assert parsed == {"content": "a\tb"}
    assert method == "control_char_escape"


def test_unicode_is_preserved_without_escaping():
    raw = '{"content": "你好世界测试"}'
    parsed, canonical, method = canonicalize_tool_arguments(raw)
    assert parsed == {"content": "你好世界测试"}
    assert method == "clean"
    # canonical must keep Chinese chars unescaped (ensure_ascii=False)
    assert "你好" in canonical


def test_empty_string_yields_empty_dict():
    parsed, canonical, method = canonicalize_tool_arguments("")
    assert parsed == {}
    assert canonical == "{}"
    assert method == "clean"


def test_hopelessly_broken_returns_failed():
    raw = '{"path": "foo" "content": }'  # totally broken
    parsed, canonical, method = canonicalize_tool_arguments(raw)
    assert parsed == {}
    assert canonical == "{}"
    assert method == "failed"


def test_canonical_is_always_valid_json_even_on_failure():
    """Invariant: canonical output must always be parseable JSON."""
    import json
    for raw in [
        '',
        '{"a": 1}',
        '{"a": 1,}',
        '{"a": "b\nc"}',
        'not json at all',
        '{"broken',
        None,
    ]:
        _, canonical, _ = canonicalize_tool_arguments(raw or "")
        # Must not raise
        json.loads(canonical)


def test_trailing_comma_inside_string_value_is_not_stripped():
    """Regression: regex-based stripping would silently corrupt
    `{"a": "hello,}", "b": 1,}` by eating the comma inside the string value.
    The string-aware walker must preserve string content exactly."""
    raw = '{"a": "hello,}", "b": 1,}'
    parsed, canonical, method = canonicalize_tool_arguments(raw)
    assert parsed == {"a": "hello,}", "b": 1}
    assert method == "trailing_comma"
    import json
    assert json.loads(canonical) == parsed


def test_trailing_comma_inside_escaped_string_not_stripped():
    """Escaped quote inside a string must not end the string prematurely."""
    raw = '{"a": "he said \\"hi,}\\"", "b": 1,}'
    parsed, canonical, method = canonicalize_tool_arguments(raw)
    assert parsed == {"a": 'he said "hi,}"', "b": 1}
    assert method == "trailing_comma"


def test_non_dict_top_level_is_coerced_with_explicit_method():
    """A top-level array or scalar is not a valid tool_call args object.
    Must be coerced to {} AND reported as non_dict_coerced (not "clean")
    so observability can flag the data-loss event."""
    for raw in ['[1, 2, 3]', '"just a string"', '42', 'null']:
        parsed, canonical, method = canonicalize_tool_arguments(raw)
        assert parsed == {}
        assert canonical == "{}"
        assert method == "non_dict_coerced", f"raw={raw!r} got method={method}"
