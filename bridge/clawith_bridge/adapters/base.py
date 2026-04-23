"""Adapter base classes.

`BaseAdapter` is the surface the session manager sees. Two concrete bases:

- `SubprocessAdapter`: for CLI-shaped agents (Claude Code, Codex later). Spawns
  a child process on start_session; parses its stdout; terminates on cancel.
  Lifecycle is process-bound — process exit → session.done.

- `DaemonAdapter`: for long-running local daemons (Hermes, OpenClaw). Assumes
  HTTP-over-localhost; start = POST, stream = SSE / polling, cancel = DELETE.
  Daemons don't "die" per-session, so completion is a server-reported signal.

Most adapters should subclass one of these rather than BaseAdapter directly —
the two bases handle the messy plumbing (pipe reading, backoff, etc).
"""
from __future__ import annotations

import abc
import asyncio
import os
import shlex
import signal
import subprocess
import sys
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, ClassVar


@dataclass
class SessionEvent:
    """What an adapter yields for each observed event."""
    kind: str  # must match protocol.EventKind
    payload: dict[str, Any] = field(default_factory=dict)


class BaseAdapter(abc.ABC):
    name: ClassVar[str] = "base"
    capabilities: ClassVar[dict[str, Any]] = {}

    def __init__(self, config: Any = None) -> None:
        self.config = config

    @abc.abstractmethod
    async def start_session(
        self,
        session_id: str,
        prompt: str,
        params: dict[str, Any],
        cwd: str | None,
        env: dict[str, str],
        timeout_s: int,
    ) -> AsyncIterator[SessionEvent]:
        """Yield SessionEvent objects until the session ends.

        Must terminate (return) when the session completes so the session
        manager can emit session.done. Exceptions propagate as session.error.
        """
        raise NotImplementedError
        yield  # type: ignore[unreachable]  # pragma: no cover

    async def send_input(self, session_id: str, text: str) -> None:  # pragma: no cover
        raise NotImplementedError("adapter does not support interactive input")

    async def cancel(self, session_id: str, reason: str) -> None:  # pragma: no cover
        """Default: rely on asyncio task cancellation from the session manager."""
        return

    async def final_text(self, session_id: str) -> str:
        """Return the final assistant text for this session (after it's done).

        Default: empty. Adapters that accumulate a canonical final response
        (like Claude Code's terminal message) should override.
        """
        return ""

    async def stats(self, session_id: str) -> dict[str, Any]:
        return {}


# ── SubprocessAdapter ────────────────────────────────────────────────

class SubprocessAdapter(BaseAdapter):
    """Base for CLI-shaped adapters.

    Subclasses:
      - implement `build_command(prompt, params, cwd)` → (argv, stdin_bytes)
      - implement `parse_stdout_line(line)` → iterable[SessionEvent]
      - optionally override `parse_stderr_line(line)` and `final_text`

    The base class handles spawning, cancellation via terminate/kill,
    and collecting stdout/stderr line-by-line.
    """

    name = "subprocess"
    capabilities = {"interactive_input": False, "cancellation": True}

    # Grace period between terminate and kill when cancelling.
    KILL_GRACE_SEC = 5

    def __init__(self, config: Any = None) -> None:
        super().__init__(config)
        self._procs: dict[str, asyncio.subprocess.Process] = {}
        self._final_text: dict[str, str] = {}

    @abc.abstractmethod
    def build_command(
        self,
        prompt: str,
        params: dict[str, Any],
        cwd: str | None,
    ) -> tuple[list[str], bytes | None]:
        """Return (argv, stdin_bytes) for the CLI invocation."""

    def parse_stdout_line(self, line: str) -> list[SessionEvent]:
        """Default: emit each line as a stdout_chunk."""
        return [SessionEvent(kind="stdout_chunk", payload={"text": line})]

    def parse_stderr_line(self, line: str) -> list[SessionEvent]:
        """Default: emit as stderr_chunk."""
        return [SessionEvent(kind="stderr_chunk", payload={"text": line})]

    async def start_session(
        self,
        session_id: str,
        prompt: str,
        params: dict[str, Any],
        cwd: str | None,
        env: dict[str, str],
        timeout_s: int,
    ) -> AsyncIterator[SessionEvent]:
        argv, stdin_bytes = self.build_command(prompt, params, cwd)
        effective_env = {**os.environ, **(env or {})}
        queue: asyncio.Queue[SessionEvent | None] = asyncio.Queue()

        try:
            proc = await asyncio.create_subprocess_exec(
                *argv,
                cwd=cwd,
                env=effective_env,
                stdin=asyncio.subprocess.PIPE if stdin_bytes else None,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError as e:
            yield SessionEvent(kind="stderr_chunk", payload={"text": f"{argv[0]!r} not found: {e}"})
            return
        self._procs[session_id] = proc

        if stdin_bytes:
            try:
                assert proc.stdin is not None
                proc.stdin.write(stdin_bytes)
                await proc.stdin.drain()
                proc.stdin.close()
            except Exception:
                pass

        async def _drain(stream: asyncio.StreamReader | None, parser) -> None:
            if stream is None:
                return
            while True:
                raw = await stream.readline()
                if not raw:
                    return
                try:
                    line = raw.decode("utf-8", errors="replace").rstrip("\n")
                except Exception:
                    continue
                for ev in parser(line):
                    await queue.put(ev)

        readers = [
            asyncio.create_task(_drain(proc.stdout, self.parse_stdout_line)),
            asyncio.create_task(_drain(proc.stderr, self.parse_stderr_line)),
        ]

        async def _watcher() -> None:
            await asyncio.gather(*readers, return_exceptions=True)
            await queue.put(None)  # sentinel

        watcher = asyncio.create_task(_watcher())

        try:
            start_t = asyncio.get_event_loop().time()
            while True:
                remaining = timeout_s - (asyncio.get_event_loop().time() - start_t)
                if remaining <= 0:
                    yield SessionEvent(kind="stderr_chunk", payload={"text": f"timeout after {timeout_s}s"})
                    break
                try:
                    item = await asyncio.wait_for(queue.get(), timeout=remaining)
                except asyncio.TimeoutError:
                    yield SessionEvent(kind="stderr_chunk", payload={"text": f"timeout after {timeout_s}s"})
                    break
                if item is None:
                    break
                yield item
        finally:
            await self._cleanup(session_id)
            for r in readers:
                r.cancel()
            watcher.cancel()
            # If we exited the loop because of timeout (or any other
            # break above), the child may still be running. Without an
            # explicit terminate here, `proc.wait()` would block past
            # timeout_s and the session slot stays held until some
            # outer cancel fires. Bound the wait with terminate→kill.
            await self._terminate_proc(proc)

    async def _terminate_proc(self, proc: asyncio.subprocess.Process) -> None:
        """Ensure `proc` has exited. Idempotent; safe if proc already exited."""
        if proc.returncode is not None:
            return
        try:
            proc.terminate()
        except ProcessLookupError:
            return
        except Exception:
            pass
        try:
            await asyncio.wait_for(proc.wait(), timeout=self.KILL_GRACE_SEC)
            return
        except asyncio.TimeoutError:
            pass
        except Exception:
            return
        try:
            proc.kill()
        except ProcessLookupError:
            return
        except Exception:
            pass
        try:
            await proc.wait()
        except Exception:
            pass

    async def cancel(self, session_id: str, reason: str) -> None:
        proc = self._procs.get(session_id)
        if proc is None:
            return
        await self._terminate_proc(proc)

    async def _cleanup(self, session_id: str) -> None:
        self._procs.pop(session_id, None)

    async def final_text(self, session_id: str) -> str:
        return self._final_text.pop(session_id, "")


# ── DaemonAdapter ────────────────────────────────────────────────────

class DaemonAdapter(BaseAdapter):
    """Base for adapters that talk to a local HTTP daemon.

    Subclasses:
      - override `start_session_request(prompt, params, cwd)` → returns task_id
      - override `iter_events(task_id)` → async iter of SessionEvent
      - override `cancel_request(task_id)` for cancellation
      - optionally override `final_text_from_events` to capture final text

    The base class manages httpx.AsyncClient lifecycle and task_id tracking.
    """

    name = "daemon"
    capabilities = {"interactive_input": False, "cancellation": True}

    def __init__(self, config: Any = None) -> None:
        super().__init__(config)
        self._tasks: dict[str, str] = {}  # session_id -> daemon task_id
        self._final_text: dict[str, str] = {}
        self._client = None  # httpx.AsyncClient, lazy-created

    async def _ensure_client(self):
        if self._client is None:
            import httpx
            base_url = getattr(self.config, "base_url", None) if self.config else None
            headers = {}
            auth_header = getattr(self.config, "auth_header", None) if self.config else None
            if auth_header:
                headers["Authorization"] = auth_header
            self._client = httpx.AsyncClient(base_url=base_url or "", headers=headers, timeout=None)
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            try:
                await self._client.aclose()
            except Exception:
                pass
            self._client = None

    @abc.abstractmethod
    async def start_session_request(
        self,
        prompt: str,
        params: dict[str, Any],
        cwd: str | None,
    ) -> str:
        """POST to daemon, return daemon-local task_id."""

    @abc.abstractmethod
    async def iter_events(self, task_id: str) -> AsyncIterator[SessionEvent]:
        """Async iterate events until the daemon reports completion."""
        raise NotImplementedError
        yield  # type: ignore[unreachable]  # pragma: no cover

    async def cancel_request(self, task_id: str) -> None:  # pragma: no cover
        """Default noop. Override to DELETE / POST cancel to daemon."""
        return

    async def start_session(
        self,
        session_id: str,
        prompt: str,
        params: dict[str, Any],
        cwd: str | None,
        env: dict[str, str],  # daemons ignore env; local env is the daemon's own
        timeout_s: int,
    ) -> AsyncIterator[SessionEvent]:
        try:
            task_id = await self.start_session_request(prompt, params, cwd)
        except Exception as e:
            # Surface as an event (for visibility) AND re-raise so the session
            # manager emits session.error (non-zero exit) rather than a silent
            # session.done with empty final_text. See test_base_daemon_error_surfacing.
            yield SessionEvent(kind="stderr_chunk", payload={"text": f"daemon start failed: {e}"})
            raise
        self._tasks[session_id] = task_id
        final_accum: list[str] = []
        try:
            start_t = asyncio.get_event_loop().time()
            async for ev in self.iter_events(task_id):
                if asyncio.get_event_loop().time() - start_t > timeout_s:
                    yield SessionEvent(kind="stderr_chunk", payload={"text": f"timeout after {timeout_s}s"})
                    break
                if ev.kind == "assistant_text":
                    final_accum.append(str(ev.payload.get("text", "")))
                yield ev
        finally:
            self._tasks.pop(session_id, None)
            self._final_text[session_id] = "".join(final_accum)

    async def cancel(self, session_id: str, reason: str) -> None:
        task_id = self._tasks.get(session_id)
        if not task_id:
            return
        try:
            await self.cancel_request(task_id)
        except Exception:
            pass

    async def final_text(self, session_id: str) -> str:
        return self._final_text.pop(session_id, "")
