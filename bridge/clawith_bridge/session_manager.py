"""Local session manager — owns running adapter sessions inside the bridge.

One instance per live WS connection. Methods `start`, `send_input`, `cancel`
are called from the connection's read loop as server frames arrive; events
yielded by adapters are forwarded to the server via the `send_frame` callback
passed in.

Each adapter class is instantiated per-session (not per-bridge) so adapters
can freely keep per-session state on self. Cheap because these are lightweight.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from loguru import logger

from .adapters import BaseAdapter
from .adapters.claude_code import ClaudeCodeAdapter
from .adapters.hermes import HermesAdapter
from .adapters.openclaw import OpenClawAdapter
from .config import BridgeConfig
from .diff_capture import diff_summary, snapshot
from .protocol import (
    SessionAcceptedFrame,
    SessionDoneFrame,
    SessionErrorFrame,
    SessionEventFrame,
    DiffSummary,
)


SendFrame = Callable[[Any], Awaitable[None]]


_ADAPTER_REGISTRY: dict[str, tuple[type[BaseAdapter], str]] = {
    "claude_code": (ClaudeCodeAdapter, "claude_code"),
    "hermes": (HermesAdapter, "hermes"),
    "openclaw": (OpenClawAdapter, "openclaw"),
}


@dataclass
class _Running:
    session_id: str
    adapter_name: str
    adapter: BaseAdapter
    task: asyncio.Task
    cwd: str | None = None
    snapshot_obj: Any = None
    final_chunks: list[str] = field(default_factory=list)
    exit_code: int | None = None


class SessionManager:
    def __init__(self, config: BridgeConfig, send_frame: SendFrame) -> None:
        self.config = config
        self._send = send_frame
        self._sessions: dict[str, _Running] = {}
        self._lock = asyncio.Lock()

    # ── Lifecycle ─────────────────────────────────────────────────────

    async def start(
        self,
        session_id: str,
        adapter: str,
        prompt: str,
        params: dict[str, Any],
        cwd: str | None,
        env: dict[str, str],
        timeout_s: int,
    ) -> None:
        async with self._lock:
            if session_id in self._sessions:
                logger.warning(f"duplicate session.start ignored: {session_id}")
                return
            if len(self._sessions) >= self.config.max_concurrent_sessions:
                await self._send_error(session_id, "bridge at concurrency cap", retryable=True)
                return
            reg = _ADAPTER_REGISTRY.get(adapter)
            if reg is None:
                await self._send_error(session_id, f"unknown adapter: {adapter}")
                return
            cls, cfg_attr = reg
            if not getattr(self.config, cfg_attr).enabled:
                await self._send_error(session_id, f"adapter disabled by local config: {adapter}")
                return
            adapter_instance = cls(getattr(self.config, cfg_attr))

        task = asyncio.create_task(
            self._run(session_id, adapter, adapter_instance, prompt, params, cwd, env, timeout_s)
        )
        self._sessions[session_id] = _Running(
            session_id=session_id,
            adapter_name=adapter,
            adapter=adapter_instance,
            task=task,
            cwd=cwd,
        )

    async def send_input(self, session_id: str, text: str) -> None:
        running = self._sessions.get(session_id)
        if running is None:
            return
        try:
            await running.adapter.send_input(session_id, text)
        except NotImplementedError:
            await self._send_error(session_id, "adapter does not support interactive input")
        except Exception as e:
            logger.warning(f"send_input failed: {e}")

    async def cancel(self, session_id: str, reason: str) -> None:
        running = self._sessions.get(session_id)
        if running is None:
            return
        try:
            await running.adapter.cancel(session_id, reason)
        except Exception as e:
            logger.warning(f"adapter.cancel failed: {e}")
        running.task.cancel()

    async def fail_all(self, reason: str) -> None:
        for sid, running in list(self._sessions.items()):
            running.task.cancel()
        self._sessions.clear()

    # ── Internals ─────────────────────────────────────────────────────

    async def _run(
        self,
        session_id: str,
        adapter_name: str,
        adapter: BaseAdapter,
        prompt: str,
        params: dict[str, Any],
        cwd: str | None,
        env: dict[str, str],
        timeout_s: int,
    ) -> None:
        # Send session.accepted
        await self._send(
            SessionAcceptedFrame(session_id=session_id, adapter=adapter_name)
        )

        # Snapshot cwd for diff_summary
        snap = None
        try:
            snap = await snapshot(cwd)
        except Exception as e:
            logger.debug(f"snapshot failed (non-fatal): {e}")

        running = self._sessions.get(session_id)
        if running is not None:
            running.snapshot_obj = snap

        error: str | None = None
        final_accum: list[str] = []
        try:
            async for ev in adapter.start_session(
                session_id, prompt, params, cwd, env, timeout_s
            ):
                # Accumulate assistant_text for fallback final_text
                if ev.kind == "assistant_text":
                    txt = ev.payload.get("text") or ""
                    if txt:
                        final_accum.append(str(txt))
                try:
                    await self._send(SessionEventFrame(
                        session_id=session_id,
                        kind=ev.kind,
                        payload=ev.payload,
                    ))
                except Exception as send_err:
                    logger.warning(f"failed to forward event, aborting session: {send_err}")
                    error = f"bridge send failed: {send_err}"
                    break
        except asyncio.CancelledError:
            error = "cancelled"
        except Exception as e:
            logger.exception(f"[session {session_id}] adapter crashed")
            error = f"{type(e).__name__}: {e}"

        # Final text: adapter-provided first, fallback to accumulator
        final_text = ""
        try:
            final_text = await adapter.final_text(session_id)
        except Exception:
            pass
        if not final_text:
            final_text = "".join(final_accum)

        # Compute diff_summary
        diff_obj: DiffSummary | None = None
        try:
            ds = await diff_summary(snap)
            if ds:
                diff_obj = DiffSummary(**{k: v for k, v in ds.items() if k in DiffSummary.model_fields})
        except Exception as e:
            logger.debug(f"diff_summary failed: {e}")

        # Send terminal frame
        if error and error != "cancelled":
            try:
                await self._send(SessionErrorFrame(
                    session_id=session_id, error=error, retryable=False
                ))
            except Exception:
                pass
        else:
            try:
                await self._send(SessionDoneFrame(
                    session_id=session_id,
                    final_text=final_text,
                    exit_code=0 if error is None else 1,
                    stats=await _safe_stats(adapter, session_id),
                    diff_summary=diff_obj,
                ))
            except Exception:
                pass

        # Clean up DaemonAdapter http client
        try:
            if hasattr(adapter, "aclose"):
                await adapter.aclose()
        except Exception:
            pass

        self._sessions.pop(session_id, None)

    async def _send_error(self, session_id: str, error: str, retryable: bool = False) -> None:
        try:
            await self._send(SessionErrorFrame(
                session_id=session_id, error=error, retryable=retryable
            ))
        except Exception:
            pass


async def _safe_stats(adapter: BaseAdapter, session_id: str) -> dict[str, Any]:
    try:
        return await adapter.stats(session_id) or {}
    except Exception:
        return {}
