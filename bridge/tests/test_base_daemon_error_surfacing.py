"""Regression test for DaemonAdapter silent-failure bug.

Before fix: if `start_session_request` raised (e.g. connection refused), the
adapter yielded a `stderr_chunk` and *returned* — which the session manager
interpreted as a normal completion and sent `SessionDoneFrame(exit=0, final="")`.
From the user's point of view: a green status light and an empty reply.

After fix: the exception is re-raised after emitting the diagnostic event, so
the session manager emits `SessionErrorFrame` with non-zero exit — the user
sees a clear error instead of silent empty success.
"""
from __future__ import annotations

from typing import Any, AsyncIterator

import pytest

from clawith_bridge.adapters.base import DaemonAdapter, SessionEvent


class _BrokenDaemonAdapter(DaemonAdapter):
    name = "broken"

    async def start_session_request(
        self, prompt: str, params: dict[str, Any], cwd: str | None,
    ) -> str:
        raise ConnectionRefusedError("127.0.0.1:9000 refused")

    async def iter_events(self, task_id: str) -> AsyncIterator[SessionEvent]:  # pragma: no cover
        # never called
        return
        yield  # type: ignore[unreachable]


@pytest.mark.asyncio
async def test_daemon_start_failure_raises_not_silent():
    adapter = _BrokenDaemonAdapter()

    events: list[SessionEvent] = []
    with pytest.raises(ConnectionRefusedError):
        async for ev in adapter.start_session(
            session_id="s-err",
            prompt="hello",
            params={},
            cwd=None,
            env={},
            timeout_s=10,
        ):
            events.append(ev)

    # We still want the user-visible diagnostic — the error is surfaced as a
    # stderr_chunk event before the generator raises.
    assert len(events) == 1
    assert events[0].kind == "stderr_chunk"
    assert "daemon start failed" in events[0].payload["text"]
    assert "127.0.0.1:9000" in events[0].payload["text"]
