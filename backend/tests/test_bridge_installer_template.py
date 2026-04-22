"""Tests for bridge installer template rendering.

The server-side installer template is the only place that decides which
bridge adapter (claude_code / openclaw / hermes) will be enabled on the
user's machine. A regression here would silently put the wrong runtime
into `~/.clawith-bridge.toml` and the bridge would advertise an adapter
the agent isn't configured for — visible only as a chat-time error.
"""

import pytest

from app.services.local_agent.installer_templates import (
    _KNOWN_ADAPTERS,
    _adapter_enabled_flags,
    derive_ws_url,
    render_installer,
)


# ── _adapter_enabled_flags ─────────────────────────────────────────────


@pytest.mark.parametrize("adapter,expected_true", [
    ("claude_code", "cc_enabled"),
    ("openclaw", "oc_enabled"),
    ("hermes", "hm_enabled"),
])
def test_adapter_flags_exactly_one_true(adapter, expected_true):
    flags = _adapter_enabled_flags(adapter)
    assert flags[expected_true] == "true"
    for k, v in flags.items():
        if k != expected_true:
            assert v == "false", f"expected {k}=false for adapter={adapter}, got {v}"


def test_adapter_flags_unknown_defaults_to_claude_code():
    # Defensive: if a caller passes a stale/unknown name we should not
    # end up with every adapter disabled — fall back to claude_code.
    flags = _adapter_enabled_flags("not_a_real_adapter")
    assert flags["cc_enabled"] == "true"
    assert flags["oc_enabled"] == "false"
    assert flags["hm_enabled"] == "false"


# ── Unix shell template (linux/macos) ──────────────────────────────────


@pytest.mark.parametrize("adapter", ["claude_code", "openclaw", "hermes"])
def test_render_installer_linux_only_selected_adapter_enabled(adapter):
    payload, filename, content_type = render_installer(
        platform="linux",
        server_url="ws://localhost:8000",
        api_key="oc-test-key",
        agent_name="test-agent",
        adapter=adapter,
    )

    script = payload.decode("utf-8")
    assert filename == "install-clawith-bridge.sh"
    assert content_type.startswith("text/x-shellscript")

    sections = {
        "claude_code": "[claude_code]\nenabled    = ",
        "hermes":      "[hermes]\nenabled  = ",
        "openclaw":    "[openclaw]\nenabled  = ",
    }
    for name, header in sections.items():
        expected = "true" if name == adapter else "false"
        line = header + expected
        assert line in script, f"expected {line!r} in generated TOML for adapter={adapter}"


def test_render_installer_agent_name_newlines_stripped():
    # agent_name lands in a bash `# Agent: ...` comment. CR/LF in a
    # user-controlled name must be replaced so the comment can't escape
    # onto a new line that bash would execute.
    payload, _, _ = render_installer(
        platform="linux",
        server_url="ws://localhost:8000",
        api_key="oc-x",
        agent_name="evil\nrm -rf /\r\necho pwned",
        adapter="claude_code",
    )
    script = payload.decode("utf-8")

    # Locate the single line containing "# Agent: " and verify the entire
    # injected payload is flattened onto that one line — no stray \n or \r.
    agent_line = next(line for line in script.splitlines() if line.startswith("# Agent:"))
    assert "rm -rf" in agent_line  # sanity: content survived
    assert "\n" not in agent_line  # splitlines guarantees this, kept for intent
    assert "\r" not in agent_line


# ── derive_ws_url ──────────────────────────────────────────────────────


@pytest.mark.parametrize("http_base,expected", [
    ("http://localhost:8000",  "ws://localhost:8000"),
    ("https://clawith.ai",     "wss://clawith.ai"),
    ("https://clawith.ai:443", "wss://clawith.ai:443"),
    ("ws://already",           "ws://already"),
    ("wss://already",          "wss://already"),
])
def test_derive_ws_url(http_base, expected):
    assert derive_ws_url(http_base) == expected


# ── sanity: _KNOWN_ADAPTERS matches the schema regex ───────────────────


def test_known_adapters_matches_schema_regex():
    # If someone adds a new adapter here but forgets to update
    # schemas.AgentCreate / AgentUpdate's regex, PATCH will 422.
    from app.schemas.schemas import AgentUpdate

    for adapter in _KNOWN_ADAPTERS:
        AgentUpdate(bridge_adapter=adapter)  # must not raise
