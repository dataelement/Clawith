"""Unit tests for tool result truncation + workspace spill."""

import json

from app.services.tool_result_truncation import (
    TOOL_RESULT_TOKEN_THRESHOLD,
    _safe_call_id,
    _smart_head,
    _truncate_json_dict,
    maybe_truncate_tool_result,
)


# ── Pass-through cases ──────────────────────────────────────────────────


def test_short_string_unchanged(tmp_path):
    content = "tiny result"
    out = maybe_truncate_tool_result(content, call_id="c1", agent_workspace=tmp_path)
    assert out == content
    # No spill file created
    assert not (tmp_path / "_tool_results").exists()


def test_list_payload_passes_through(tmp_path):
    """Vision-injected multimodal content must not be touched."""
    payload = [
        {"type": "text", "text": "see image:"},
        {"type": "image", "source": {"type": "base64", "data": "..."}},
    ]
    out = maybe_truncate_tool_result(payload, call_id="c1", agent_workspace=tmp_path)
    assert out is payload  # same object


def test_non_string_non_list_coerced(tmp_path):
    """Defensive: dict payload (shouldn't happen) is coerced to str."""
    out = maybe_truncate_tool_result(
        {"key": "value"},  # type: ignore[arg-type]
        call_id="c1",
        agent_workspace=tmp_path,
    )
    # Coerced to str then passed through (small enough)
    assert isinstance(out, str)


# ── Spill behavior ──────────────────────────────────────────────────────


def _huge_payload(chars: int = 60000) -> str:
    return "x" * chars


def test_huge_payload_spilled_and_truncated(tmp_path):
    payload = _huge_payload(60000)
    out = maybe_truncate_tool_result(
        payload, call_id="abc-123", agent_workspace=tmp_path
    )
    # In-context is now much shorter than original
    assert isinstance(out, str)
    assert len(out) < len(payload)
    # Marker present
    assert "[truncated. Full output" in out
    assert "_tool_results/abc-123.txt" in out
    assert "use the read_file tool" in out
    # Spill file written with full payload
    spill = tmp_path / "_tool_results" / "abc-123.txt"
    assert spill.exists()
    assert spill.read_text(encoding="utf-8") == payload


def test_spill_path_uses_utf8(tmp_path):
    """Mixed CJK + emoji payload should round-trip through utf-8."""
    payload = ("中文测试 🎉 " + "x" * 30000)
    out = maybe_truncate_tool_result(
        payload, call_id="cjk", agent_workspace=tmp_path
    )
    spill = tmp_path / "_tool_results" / "cjk.txt"
    assert spill.exists()
    assert spill.read_text(encoding="utf-8") == payload
    # Marker still present
    assert "[truncated" in out


def test_spill_failure_returns_inline_marker(tmp_path):
    """If we can't write to disk, still truncate but mark as unrecoverable."""
    # Use a workspace path that can't be written (a file, not a dir)
    blocker = tmp_path / "blocker"
    blocker.write_text("this is a file, not a dir")
    payload = _huge_payload(40000)
    out = maybe_truncate_tool_result(
        payload, call_id="fail", agent_workspace=blocker
    )
    assert isinstance(out, str)
    assert "could not be spilled to disk" in out


def test_at_threshold_boundary(tmp_path):
    """Just below threshold passes through; just above triggers spill."""
    chars_at_threshold = TOOL_RESULT_TOKEN_THRESHOLD * 3  # chars/3 ratio
    # Below threshold (slightly)
    below = "y" * (chars_at_threshold - 100)
    out = maybe_truncate_tool_result(below, call_id="b", agent_workspace=tmp_path)
    assert out == below

    # Above threshold
    above = "y" * (chars_at_threshold + 1000)
    out2 = maybe_truncate_tool_result(above, call_id="a", agent_workspace=tmp_path)
    assert "[truncated" in out2


# ── Smart head: JSON-shape preservation ─────────────────────────────────


def test_smart_head_preserves_results_array():
    """jina_search-style response: keep metadata + first 5 items."""
    # Each result is bulky enough that full payload exceeds max_chars.
    bulky_snippet = "Lorem ipsum dolor sit amet, consectetur adipiscing elit. " * 5
    payload = json.dumps({
        "query": "what is rust",
        "total_results": 50,
        "results": [
            {"url": f"https://ex.com/{i}", "title": f"Result {i}", "snippet": bulky_snippet}
            for i in range(50)
        ],
    })
    assert len(payload) > 5000  # sanity: max_chars below forces truncation
    out = _smart_head(payload, max_chars=5000)
    parsed = json.loads(out)
    assert parsed["query"] == "what is rust"
    assert parsed["total_results"] == 50
    assert len(parsed["results"]) == 5
    assert parsed["_truncated_results_count"] == 45


def test_smart_head_recognizes_items_key():
    bulky_item = "x" * 200
    payload = json.dumps({
        "items": [f"{bulky_item}-{i}" for i in range(30)],
        "next_cursor": "abc",
    })
    assert len(payload) > 2000  # sanity
    out = _smart_head(payload, max_chars=2000)
    parsed = json.loads(out)
    assert len(parsed["items"]) == 5
    assert parsed["next_cursor"] == "abc"


def test_smart_head_falls_back_for_non_array_json():
    """JSON without a known array key falls back to head-cut."""
    payload = json.dumps({"big_field": "x" * 10000, "other": "y"})
    out = _smart_head(payload, max_chars=2000)
    # Either trimmed JSON dict or plain head-cut, but length ≤ 2000
    assert len(out) <= 2000


def test_smart_head_plain_text_head_cut():
    payload = "line1\n" + ("plain text content " * 1000)
    out = _smart_head(payload, max_chars=500)
    assert len(out) <= 500
    # Head-cut should prefer line boundary if available
    assert out.startswith("line1")


def test_truncate_json_dict_no_array_key_returns_none():
    """Helper returns None when no recognizable array shape."""
    assert _truncate_json_dict({"foo": "bar", "baz": 123}) is None


def test_truncate_json_dict_short_array_returns_none():
    """Don't bother truncating a 3-item array."""
    assert _truncate_json_dict({"results": [1, 2, 3]}) is None


def test_truncate_json_dict_first_known_key_wins():
    """If multiple known keys present, first match wins (results > items)."""
    data = {"results": list(range(20)), "items": list(range(30))}
    out = _truncate_json_dict(data)
    assert out is not None
    assert "_truncated_results_count" in out
    assert "_truncated_items_count" not in out  # items left untouched


# ── End-to-end integration ──────────────────────────────────────────────


def test_realistic_jina_search_response_truncated_to_useful_excerpt(tmp_path):
    """A 50-result jina_search dump fits into the in-context head with
    enough metadata + samples for the model to decide whether to read_file."""
    full_response = json.dumps({
        "query": "best practices for python async",
        "total_results": 50,
        "search_time_ms": 142,
        "results": [
            {
                "url": f"https://example.com/article-{i}",
                "title": f"Article {i} about async",
                "snippet": "Lorem ipsum dolor sit amet " * 30,
            }
            for i in range(50)
        ],
    })

    out = maybe_truncate_tool_result(
        full_response, call_id="search-1", agent_workspace=tmp_path
    )
    assert isinstance(out, str)

    # Full content preserved on disk
    spill = tmp_path / "_tool_results" / "search-1.txt"
    assert spill.read_text(encoding="utf-8") == full_response

    # Model can see metadata + sample
    assert "best practices for python async" in out
    assert "total_results" in out
    assert "search-1.txt" in out  # marker tells model where the rest is


def test_marker_includes_token_count(tmp_path):
    payload = "z" * 30000  # ~10000 tokens
    out = maybe_truncate_tool_result(
        payload, call_id="big", agent_workspace=tmp_path
    )
    # Marker should mention the original token count
    assert "tokens" in out
    # The number itself appears (10000)
    import re
    m = re.search(r"\((\d+)\s+tokens\)", out)
    assert m is not None
    assert int(m.group(1)) > 5000


# ── Sandbox safety: call_id sanitization ────────────────────────────────


def test_safe_call_id_passes_provider_format():
    """Real Anthropic / OpenAI / synthetic IDs all pass through unchanged."""
    for cid in (
        "toolu_01ABC123def456GHI789jkl",
        "call_f6db199d-c470-4bb3-8188-1c9eeb43cc60",
        "call_msg_uuid_v4",
        "abc-123_XYZ",
    ):
        assert _safe_call_id(cid) == cid, f"Should pass through: {cid!r}"


def test_safe_call_id_slugs_path_traversal():
    """A prompt-injected ../-style call_id gets neutralized."""
    assert _safe_call_id("../../../etc/passwd") != "../../../etc/passwd"
    # No path separator survives
    out = _safe_call_id("../../../etc/passwd")
    assert "/" not in out
    assert "\\" not in out
    assert ".." not in out or out.replace(".", "_") == out


def test_safe_call_id_handles_empty_and_unicode():
    assert _safe_call_id("") == "unknown"
    assert _safe_call_id(None) == "unknown"  # type: ignore[arg-type]
    out = _safe_call_id("中文 with spaces!")
    # Unicode and special chars get replaced with underscore
    assert all(c.isalnum() or c in "_-" for c in out)


def test_safe_call_id_caps_length():
    """Pathological 10K-char call_id is capped at 128."""
    huge = "a" * 10_000
    out = _safe_call_id(huge)
    assert len(out) <= 128


def test_path_traversal_call_id_writes_inside_sandbox(tmp_path):
    """Hostile call_id like '../../foo' must NOT escape _tool_results/."""
    payload = "x" * 60000  # forces spill
    hostile_id = "../../../../../etc/passwd"

    out = maybe_truncate_tool_result(
        payload, call_id=hostile_id, agent_workspace=tmp_path
    )
    assert isinstance(out, str)

    # Truncation marker still appears with the SLUGGED name (not the hostile one)
    assert "[truncated" in out
    assert "etc/passwd" not in out  # original hostile path doesn't surface

    # Walk the workspace and verify NO file got written outside _tool_results/
    spill_root = tmp_path / "_tool_results"
    for path in tmp_path.rglob("*"):
        if path.is_file():
            assert spill_root in path.resolve().parents or path.resolve() == spill_root, (
                f"File leaked outside _tool_results/: {path}"
            )


def test_call_id_with_separator_in_filename(tmp_path):
    """Path separator in call_id should not create subdirectories under
    _tool_results/."""
    payload = "y" * 60000
    out = maybe_truncate_tool_result(
        payload, call_id="evil/sub/path", agent_workspace=tmp_path
    )
    assert isinstance(out, str)

    # No nested directory should appear under _tool_results/
    spill_root = tmp_path / "_tool_results"
    nested_dirs = [p for p in spill_root.rglob("*") if p.is_dir()]
    assert nested_dirs == [], f"Unexpected subdirectories: {nested_dirs}"

    # Exactly one .txt file at the top level of _tool_results/
    txt_files = list(spill_root.glob("*.txt"))
    assert len(txt_files) == 1
    assert txt_files[0].read_text(encoding="utf-8") == payload
