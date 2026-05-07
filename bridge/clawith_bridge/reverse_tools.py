"""Reverse tool-call helpers.

Local agents (Claude Code, etc.) can initiate calls to Clawith's own tools
(send a Feishu message, update a task, etc.) by emitting a `tool.call` frame.
This module provides a thin client so in-process code in the bridge can also
initiate a reverse call and await its result.

In V1 we only *forward* reverse calls — local agents emit frames the bridge
relays verbatim. In-bridge helpers can use this when needed (e.g. diff_capture
could theoretically notify a channel on big diffs).
"""
from __future__ import annotations

import asyncio
import uuid
from typing import Any

from .protocol import ToolCallFrame


class ReverseCallClient:
    """Dispatches ToolCallFrame and awaits matching ToolResponseFrame.

    Connection owner drives this: when a `tool.response` frame arrives, the
    connection layer calls `resolve(reverse_call_id, result, error)`.
    """

    def __init__(self) -> None:
        self._pending: dict[str, asyncio.Future] = {}

    def next_id(self) -> str:
        return uuid.uuid4().hex

    def build_frame(self, session_id: str, name: str, arguments: dict[str, Any]) -> tuple[ToolCallFrame, asyncio.Future]:
        call_id = self.next_id()
        fut: asyncio.Future = asyncio.get_event_loop().create_future()
        self._pending[call_id] = fut
        frame = ToolCallFrame(
            session_id=session_id,
            reverse_call_id=call_id,
            name=name,
            arguments=arguments,
        )
        return frame, fut

    def resolve(self, call_id: str, result: str | None, error: str | None) -> None:
        fut = self._pending.pop(call_id, None)
        if fut is None or fut.done():
            return
        if error:
            fut.set_exception(RuntimeError(error))
        else:
            fut.set_result(result or "")

    def fail_all(self, exc: BaseException) -> None:
        for call_id, fut in list(self._pending.items()):
            if not fut.done():
                fut.set_exception(exc)
        self._pending.clear()
