"""Pydantic-level tests for the bridge_adapter field on AgentCreate/AgentUpdate.

The field is constrained by a regex pattern to prevent arbitrary strings
from reaching the DB — a typo like "claud_code" would silently save and
only blow up at chat-time when the dispatcher couldn't find a matching
adapter.
"""

import pytest
from pydantic import ValidationError

from app.schemas.schemas import AgentCreate, AgentUpdate


_VALID = ["claude_code", "openclaw", "hermes"]
_INVALID = [
    "claud_code",   # typo
    "CLAUDE_CODE",  # wrong case — regex is case-sensitive
    "claude-code",  # wrong separator
    "random",
    " claude_code",  # leading whitespace
    "claude_code ",  # trailing whitespace
    "",              # empty string
    "claude_code;drop",  # injection-style
]


# ── AgentUpdate.bridge_adapter ─────────────────────────────────────────


@pytest.mark.parametrize("value", _VALID)
def test_agent_update_accepts_valid_adapter(value):
    m = AgentUpdate(bridge_adapter=value)
    assert m.bridge_adapter == value


@pytest.mark.parametrize("value", _INVALID)
def test_agent_update_rejects_invalid_adapter(value):
    with pytest.raises(ValidationError):
        AgentUpdate(bridge_adapter=value)


def test_agent_update_allows_none():
    # None is the "don't change it" sentinel — must not trip the regex.
    m = AgentUpdate(bridge_adapter=None)
    assert m.bridge_adapter is None


def test_agent_update_allows_field_absent():
    # Equivalent to None — exclude_unset semantics in the route rely on this.
    m = AgentUpdate()
    assert m.bridge_adapter is None


# ── AgentCreate.bridge_adapter (same pattern) ──────────────────────────


@pytest.mark.parametrize("value", _VALID)
def test_agent_create_accepts_valid_adapter(value):
    m = AgentCreate(name="test", bridge_adapter=value)
    assert m.bridge_adapter == value


@pytest.mark.parametrize("value", _INVALID)
def test_agent_create_rejects_invalid_adapter(value):
    with pytest.raises(ValidationError):
        AgentCreate(name="test", bridge_adapter=value)
