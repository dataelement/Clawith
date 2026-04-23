"""Tool-call JSON argument recovery and canonicalization.

LLM streaming sometimes produces slightly malformed JSON for tool_call.arguments:
trailing commas, unescaped control characters in string values, truncated tokens.
DashScope validates this field strictly server-side and rejects the request with
HTTP 400 `function.arguments parameter must be in JSON format` on the NEXT round.

`canonicalize_tool_arguments` accepts any raw string and returns a parsed dict
plus a canonical JSON string that is guaranteed to round-trip through
`json.loads`. It never raises.
"""
from __future__ import annotations

import json
import re
from typing import Any

# Control characters that are illegal unescaped in a JSON string
_UNESCAPED_CONTROL_RE = re.compile(r'(?<!\\)[\x00-\x1f]')


def _strip_trailing_commas(s: str) -> str:
    """Remove trailing commas before } or ] — the most common LLM mistake."""
    # Match ',' followed by optional whitespace and then ] or }
    return re.sub(r',(\s*[}\]])', r'\1', s)


def _escape_control_chars_in_strings(s: str) -> str:
    """Scan through string and escape unescaped control chars inside JSON string values.

    We can't do this by simple regex because we only want to escape control
    chars *inside string values*, not outside. Walk char by char tracking
    whether we're inside a string.
    """
    out: list[str] = []
    in_string = False
    escape_next = False
    for ch in s:
        if escape_next:
            out.append(ch)
            escape_next = False
            continue
        if ch == '\\' and in_string:
            out.append(ch)
            escape_next = True
            continue
        if ch == '"':
            in_string = not in_string
            out.append(ch)
            continue
        if in_string and ord(ch) < 0x20:
            # Escape control chars per JSON spec
            if ch == '\n':
                out.append('\\n')
            elif ch == '\r':
                out.append('\\r')
            elif ch == '\t':
                out.append('\\t')
            elif ch == '\b':
                out.append('\\b')
            elif ch == '\f':
                out.append('\\f')
            else:
                out.append(f'\\u{ord(ch):04x}')
            continue
        out.append(ch)
    return ''.join(out)


def canonicalize_tool_arguments(raw: str) -> tuple[dict[str, Any], str, str]:
    """Parse and canonicalize a raw tool_call.arguments string.

    Returns:
        (parsed_dict, canonical_json_string, repair_method)

    repair_method is one of: "clean", "trailing_comma", "control_char_escape",
    "failed". Never raises.
    """
    if not raw:
        return {}, "{}", "clean"

    # Attempt 1: clean parse
    try:
        parsed = json.loads(raw)
        if not isinstance(parsed, dict):
            parsed = {}
        canonical = json.dumps(parsed, ensure_ascii=False)
        return parsed, canonical, "clean"
    except json.JSONDecodeError:
        pass

    # Attempt 2: strip trailing commas
    cleaned = _strip_trailing_commas(raw)
    try:
        parsed = json.loads(cleaned)
        if not isinstance(parsed, dict):
            parsed = {}
        canonical = json.dumps(parsed, ensure_ascii=False)
        return parsed, canonical, "trailing_comma"
    except json.JSONDecodeError:
        pass

    # Attempt 3: escape unescaped control chars in strings, then retry
    escaped = _escape_control_chars_in_strings(cleaned)
    try:
        parsed = json.loads(escaped)
        if not isinstance(parsed, dict):
            parsed = {}
        canonical = json.dumps(parsed, ensure_ascii=False)
        return parsed, canonical, "control_char_escape"
    except json.JSONDecodeError:
        pass

    # Gave up — return safe empty
    return {}, "{}", "failed"
