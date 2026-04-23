from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Awaitable, Callable, Literal, Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent import Agent


TOOLS_REQUIRING_ARGS: frozenset[str] = frozenset(
    {
        "write_file",
        "read_file",
        "delete_file",
        "read_document",
        "send_message_to_agent",
        "send_feishu_message",
        "send_email",
    }
)


@dataclass(frozen=True)
class CapabilityFlags:
    track_token_budget: bool
    enforce_quota: bool
    strip_images: bool
    inject_vision: bool
    listen_abort: bool
    dynamic_max_rounds: bool
    tool_arg_guard: bool
    persist_per_round: bool


FLAGS_WS_CHAT = CapabilityFlags(
    track_token_budget=True,
    enforce_quota=True,
    strip_images=True,
    inject_vision=True,
    listen_abort=True,
    dynamic_max_rounds=True,
    tool_arg_guard=True,
    persist_per_round=True,
)

FLAGS_TRIGGERED_TASK = CapabilityFlags(
    track_token_budget=True,
    enforce_quota=True,
    strip_images=True,
    inject_vision=True,
    listen_abort=True,
    dynamic_max_rounds=True,
    tool_arg_guard=True,
    persist_per_round=True,
)


@dataclass
class TokenUsage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0


class AbortSource(Protocol):
    async def is_aborted(self) -> bool: ...


class RoundLogSink(Protocol):
    async def write_round(
        self,
        round_idx: int,
        tool_calls: list[dict],
        tool_results: list[dict],
        usage: TokenUsage,
    ) -> None: ...


@dataclass
class RunContext:
    session: AsyncSession | None
    agent: Agent
    caller: Literal["ws", "executor"]
    abort_source: AbortSource | None = None
    round_log_sink: RoundLogSink | None = None
    max_rounds_override: int | None = None
    on_round_complete: Callable[["RoundOutcome"], Awaitable[None]] | None = None


class RunStatus(str, Enum):
    COMPLETED = "completed"
    ABORTED = "aborted"
    QUOTA_EXCEEDED = "quota_exceeded"
    MAX_ROUNDS = "max_rounds"
    ERROR = "error"


class AbortScope(str, Enum):
    USER = "user"
    SYSTEM = "system"


@dataclass
class RoundOutcome:
    round_index: int
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    assistant_text: str = ""
    token_usage: TokenUsage = field(default_factory=TokenUsage)
    latency_ms: int = 0
    error: str | None = None


@dataclass
class RunResult:
    status: RunStatus
    rounds: list[RoundOutcome] = field(default_factory=list)
    final_text: str = ""
    total_usage: TokenUsage = field(default_factory=TokenUsage)
    abort_scope: AbortScope | None = None
    scope: Literal["daily", "monthly"] | None = None
    error: str | None = None
