"""Frame schemas — mirror of backend/app/services/local_agent/protocol.py.

Vendored deliberately so the bridge package doesn't import anything from the
backend. Keep in sync with the server.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

PROTOCOL_VERSION = "1"

EventKind = Literal[
    "stdout_chunk",
    "stderr_chunk",
    "assistant_text",
    "thinking",
    "tool_call_start",
    "tool_call_result",
    "status",
    "file_change",
]


# ── Server → Bridge ───────────────────────────────────────────────────

class HelloFrame(BaseModel):
    type: Literal["hello"] = "hello"
    v: str = PROTOCOL_VERSION
    server_time: str


class SessionStartFrame(BaseModel):
    type: Literal["session.start"] = "session.start"
    session_id: str
    adapter: str
    prompt: str
    params: dict[str, Any] = Field(default_factory=dict)
    cwd: str | None = None
    env: dict[str, str] = Field(default_factory=dict)
    timeout_s: int = 1800


class SessionInputFrame(BaseModel):
    type: Literal["session.input"] = "session.input"
    session_id: str
    text: str


class SessionCancelFrame(BaseModel):
    type: Literal["session.cancel"] = "session.cancel"
    session_id: str
    reason: str = ""


class ToolResponseFrame(BaseModel):
    type: Literal["tool.response"] = "tool.response"
    session_id: str
    reverse_call_id: str
    result: str | None = None
    error: str | None = None


class PingFrame(BaseModel):
    type: Literal["ping"] = "ping"


# ── Bridge → Server ───────────────────────────────────────────────────

class BridgeRegisterFrame(BaseModel):
    type: Literal["bridge.register"] = "bridge.register"
    v: str = PROTOCOL_VERSION
    bridge_version: str
    adapters: list[str]
    capabilities: dict[str, Any] = Field(default_factory=dict)


class SessionAcceptedFrame(BaseModel):
    type: Literal["session.accepted"] = "session.accepted"
    session_id: str
    adapter: str
    local_session_id: str | None = None


class SessionEventFrame(BaseModel):
    type: Literal["session.event"] = "session.event"
    session_id: str
    kind: EventKind
    payload: dict[str, Any] = Field(default_factory=dict)


class DiffSummary(BaseModel):
    files_changed: int = 0
    insertions: int = 0
    deletions: int = 0
    files: list[dict[str, Any]] = Field(default_factory=list)


class SessionDoneFrame(BaseModel):
    type: Literal["session.done"] = "session.done"
    session_id: str
    final_text: str = ""
    exit_code: int | None = None
    stats: dict[str, Any] = Field(default_factory=dict)
    diff_summary: DiffSummary | None = None


class SessionErrorFrame(BaseModel):
    type: Literal["session.error"] = "session.error"
    session_id: str
    error: str
    retryable: bool = False


class ToolCallFrame(BaseModel):
    type: Literal["tool.call"] = "tool.call"
    session_id: str
    reverse_call_id: str
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class PongFrame(BaseModel):
    type: Literal["pong"] = "pong"


# ── Parse helpers ─────────────────────────────────────────────────────

_INBOUND_BY_TYPE: dict[str, type[BaseModel]] = {
    "hello": HelloFrame,
    "session.start": SessionStartFrame,
    "session.input": SessionInputFrame,
    "session.cancel": SessionCancelFrame,
    "tool.response": ToolResponseFrame,
    "ping": PingFrame,
}


def parse_inbound(data: dict[str, Any]) -> BaseModel | None:
    """Parse a frame received from the server. Returns None for unknown types."""
    t = data.get("type")
    if not isinstance(t, str):
        return None
    cls = _INBOUND_BY_TYPE.get(t)
    if cls is None:
        return None
    try:
        return cls.model_validate(data)
    except Exception:
        return None
