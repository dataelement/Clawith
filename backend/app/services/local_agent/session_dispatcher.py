"""Session dispatcher for local-agent bridges.

Module-level singleton. Owns:
  - `active_bridges`: one bridge connection per agent_id
  - `pending_sessions`: asyncio.Future per session for final result
  - `session_events`: asyncio.Queue per session for streaming events
  - `reverse_calls`: Future per reverse-call so bridge can await server result

Shape of the blocking call used by the LLM tool loop::

    queue, future = await dispatcher.start_session(
        agent_id, session_id, adapter, prompt, params, timeout_s
    )
    # consumer (e.g. WS streamer) drains `queue` until SENTINEL;
    # caller awaits `future` for the final string result.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from fastapi import WebSocket
from loguru import logger

from app.services.activity_logger import log_activity
from app.services.local_agent import adapters as _adapters
from app.services.local_agent.protocol import (
    PROTOCOL_VERSION,
    BridgeRegisterFrame,
    HelloFrame,
    SessionCancelFrame,
    SessionDoneFrame,
    SessionErrorFrame,
    SessionEventFrame,
    SessionInputFrame,
    SessionStartFrame,
    ToolCallFrame,
    ToolResponseFrame,
)


# Reverse-call allowlist. Bridge-initiated tool calls are restricted to
# these names to prevent local agents from driving Clawith's sandbox.
ALLOWED_REVERSE_TOOLS: frozenset[str] = frozenset({
    "send_message_to_agent",
    "send_file_to_agent",
    "manage_tasks",
    "send_feishu_message",
})

# Default per-bridge concurrent session cap.
DEFAULT_MAX_CONCURRENT_SESSIONS = 4

# Sentinel yielded into event queues to signal terminal state to the
# consumer. After seeing this, consumer should stop iterating and
# await the session's Future for the final result.
EVENT_QUEUE_SENTINEL = object()


class BridgeDisconnected(Exception):
    """Raised when a bridge vanishes while a session is in flight."""


class BridgeUnavailable(Exception):
    """Raised when no bridge is attached for an agent at start_session time."""


class SessionRejected(Exception):
    """Raised when session start is refused (unknown adapter, over cap, etc)."""


@dataclass
class _Session:
    session_id: str
    agent_id: str
    adapter: str
    started_at: datetime
    future: asyncio.Future  # type: ignore[type-arg]
    events: asyncio.Queue  # type: ignore[type-arg]
    # Reverse-call futures keyed by reverse_call_id
    reverse_calls: dict[str, asyncio.Future] = field(default_factory=dict)


@dataclass
class _Bridge:
    agent_id: str
    ws: WebSocket
    bridge_version: str
    adapters: list[str]
    capabilities: dict[str, Any]
    connected_at: datetime
    sessions: dict[str, _Session] = field(default_factory=dict)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class SessionDispatcher:
    def __init__(self) -> None:
        self._bridges: dict[str, _Bridge] = {}

    # ── Bridge lifecycle ──────────────────────────────────────────────

    async def attach_bridge(
        self,
        agent_id: str,
        ws: WebSocket,
        register: BridgeRegisterFrame,
    ) -> bool:
        """Register a new bridge. Returns False if one is already attached."""
        if agent_id in self._bridges:
            logger.warning(
                f"[Dispatcher] Bridge already attached for agent {agent_id}, rejecting new connection"
            )
            return False
        self._bridges[agent_id] = _Bridge(
            agent_id=agent_id,
            ws=ws,
            bridge_version=register.bridge_version,
            adapters=list(register.adapters),
            capabilities=dict(register.capabilities),
            connected_at=datetime.now(timezone.utc),
        )
        logger.info(
            f"[Dispatcher] Bridge attached: agent={agent_id} "
            f"version={register.bridge_version} adapters={register.adapters}"
        )
        try:
            await log_activity(
                agent_id=uuid.UUID(agent_id),
                action_type="bridge_attached",
                summary=f"本地 agent bridge 已连接 (v{register.bridge_version})",
                detail={
                    "bridge_version": register.bridge_version,
                    "adapters": list(register.adapters),
                    "capabilities": dict(register.capabilities),
                },
            )
        except Exception:
            pass
        return True

    async def detach_bridge(
        self,
        agent_id: str,
        close_ws: bool = False,
        reason: str = "",
    ) -> None:
        """Remove a bridge from the registry and fail its in-flight sessions.

        `close_ws=True` additionally closes the bridge's WebSocket from the
        server side. This is used for server-initiated eviction — notably API
        key rotation, which must revoke the bridge's authenticated socket
        because auth is checked only at the initial `/ws/bridge` upgrade.
        The default (False) preserves the prior behavior, where detach runs
        from the bridge's own read-loop `finally` after the socket is already
        closing.
        """
        bridge = self._bridges.pop(agent_id, None)
        if not bridge:
            return
        if close_ws:
            # code 4001 matches the "auth failed" class used at upgrade; the
            # bridge treats it as a terminal credential problem rather than a
            # transient network blip. `reason` is cropped to the 123-byte WS
            # limit by starlette, but trim here to keep logs readable.
            try:
                await bridge.ws.close(
                    code=4001,
                    reason=(reason or "bridge detached by server")[:120],
                )
            except Exception as e:
                logger.debug(f"[Dispatcher] ws.close during detach failed: {e}")
        abandoned = list(bridge.sessions.values())
        logger.info(
            f"[Dispatcher] Bridge detached: agent={agent_id} "
            f"sessions_abandoned={len(abandoned)} reason={reason!r}"
        )
        # Fail all pending sessions for this bridge.
        for session in abandoned:
            self._fail_session(session, BridgeDisconnected("bridge disconnected"))
        bridge.sessions.clear()
        try:
            await log_activity(
                agent_id=uuid.UUID(agent_id),
                action_type="bridge_detached",
                summary=(
                    f"本地 agent bridge 断开 (放弃中 session={len(abandoned)})"
                    if abandoned else "本地 agent bridge 断开"
                ),
                detail={
                    "abandoned_sessions": [s.session_id for s in abandoned],
                    "bridge_version": bridge.bridge_version,
                },
            )
        except Exception:
            pass

    def has_bridge(self, agent_id: str) -> bool:
        return agent_id in self._bridges

    def get_bridge_info(self, agent_id: str) -> dict[str, Any] | None:
        bridge = self._bridges.get(agent_id)
        if not bridge:
            return None
        return {
            "agent_id": agent_id,
            "bridge_version": bridge.bridge_version,
            "adapters": list(bridge.adapters),
            "capabilities": dict(bridge.capabilities),
            "connected_at": bridge.connected_at.isoformat(),
            "active_sessions": list(bridge.sessions.keys()),
        }

    def list_connected(self) -> list[str]:
        return list(self._bridges.keys())

    # ── Session lifecycle (called from LLM loop) ──────────────────────

    async def start_session(
        self,
        agent_id: str,
        session_id: str,
        adapter: str,
        prompt: str,
        params: dict[str, Any] | None = None,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        timeout_s: int = 1800,
    ) -> tuple[asyncio.Queue, asyncio.Future]:
        """Start a session on the attached bridge. Returns (event_queue, future).

        Caller should:
          - consume `event_queue` until it yields `EVENT_QUEUE_SENTINEL`
          - await `future` for the final string result (raises on failure)
        """
        bridge = self._bridges.get(agent_id)
        if bridge is None:
            raise BridgeUnavailable(f"no bridge attached for agent {agent_id}")

        if not _adapters.is_known_adapter(adapter):
            raise SessionRejected(f"unknown adapter: {adapter}")

        if adapter not in bridge.adapters:
            raise SessionRejected(
                f"bridge does not advertise adapter {adapter!r} (has {bridge.adapters})"
            )

        if len(bridge.sessions) >= DEFAULT_MAX_CONCURRENT_SESSIONS:
            raise SessionRejected(
                f"bridge at concurrency cap ({DEFAULT_MAX_CONCURRENT_SESSIONS})"
            )

        if session_id in bridge.sessions:
            raise SessionRejected(f"session {session_id} already active")

        loop = asyncio.get_event_loop()
        future: asyncio.Future = loop.create_future()
        events: asyncio.Queue = asyncio.Queue()
        session = _Session(
            session_id=session_id,
            agent_id=agent_id,
            adapter=adapter,
            started_at=datetime.now(timezone.utc),
            future=future,
            events=events,
        )
        bridge.sessions[session_id] = session

        frame = SessionStartFrame(
            session_id=session_id,
            adapter=adapter,
            prompt=prompt,
            params=params or {},
            cwd=cwd,
            env=env or {},
            timeout_s=timeout_s,
        )
        try:
            await self._send(bridge, frame.model_dump(mode="json"))
        except Exception as e:
            bridge.sessions.pop(session_id, None)
            raise BridgeDisconnected(f"failed to send session.start: {e}")

        logger.info(
            f"[Dispatcher] session.start sent: agent={agent_id} "
            f"session={session_id} adapter={adapter}"
        )
        try:
            await log_activity(
                agent_id=uuid.UUID(agent_id),
                action_type="local_session_start",
                summary=f"派发本地 agent session ({adapter})",
                detail={
                    "session_id": session_id,
                    "adapter": adapter,
                    "prompt_preview": (prompt or "")[:200],
                    "cwd": cwd,
                    "timeout_s": timeout_s,
                },
            )
        except Exception:
            pass
        return events, future

    async def send_input(self, agent_id: str, session_id: str, text: str) -> None:
        bridge = self._bridges.get(agent_id)
        if not bridge or session_id not in bridge.sessions:
            raise BridgeDisconnected(f"session {session_id} not active")
        frame = SessionInputFrame(session_id=session_id, text=text)
        await self._send(bridge, frame.model_dump(mode="json"))

    async def cancel_session(self, agent_id: str, session_id: str, reason: str = "") -> None:
        bridge = self._bridges.get(agent_id)
        if not bridge or session_id not in bridge.sessions:
            return
        frame = SessionCancelFrame(session_id=session_id, reason=reason)
        try:
            await self._send(bridge, frame.model_dump(mode="json"))
        except Exception:
            pass
        session = bridge.sessions.pop(session_id, None)
        if session:
            self._fail_session(session, asyncio.CancelledError(f"cancelled: {reason}"))

    async def wait_for_completion(
        self,
        agent_id: str,
        session_id: str,
        timeout_s: float | None = None,
    ) -> str:
        """Block until session's Future resolves. Returns final_text."""
        bridge = self._bridges.get(agent_id)
        if not bridge or session_id not in bridge.sessions:
            raise BridgeDisconnected(f"session {session_id} not found")
        future = bridge.sessions[session_id].future
        if timeout_s is not None:
            return await asyncio.wait_for(future, timeout=timeout_s)
        return await future

    # ── Inbound frame routing (called from bridge_ws reader) ──────────

    async def handle_inbound_frame(self, agent_id: str, frame: Any) -> None:
        """Route a parsed inbound frame to its session."""
        bridge = self._bridges.get(agent_id)
        if bridge is None:
            logger.warning(f"[Dispatcher] frame from unknown agent {agent_id}: {type(frame).__name__}")
            return

        if isinstance(frame, SessionEventFrame):
            session = bridge.sessions.get(frame.session_id)
            if session is None:
                logger.warning(f"[Dispatcher] event for unknown session {frame.session_id}")
                return
            await session.events.put({"kind": frame.kind, "payload": frame.payload})

        elif isinstance(frame, SessionDoneFrame):
            session = bridge.sessions.pop(frame.session_id, None)
            if session is None:
                return
            diff_dict = frame.diff_summary.model_dump() if frame.diff_summary else None
            await session.events.put({
                "kind": "status",
                "payload": {
                    "state": "done",
                    "exit_code": frame.exit_code,
                    "stats": frame.stats,
                    "diff_summary": diff_dict,
                },
            })
            await session.events.put(EVENT_QUEUE_SENTINEL)
            if not session.future.done():
                session.future.set_result(frame.final_text)
            logger.info(
                f"[Dispatcher] session done: {frame.session_id} "
                f"final_len={len(frame.final_text)} exit={frame.exit_code}"
            )
            try:
                files_changed = diff_dict.get("files_changed") if diff_dict else 0
                insertions = diff_dict.get("insertions") if diff_dict else 0
                deletions = diff_dict.get("deletions") if diff_dict else 0
                duration_s = (
                    datetime.now(timezone.utc) - session.started_at
                ).total_seconds()
                summary = (
                    f"本地 agent session 完成 ({session.adapter}, exit={frame.exit_code})"
                    + (
                        f"，改动 {files_changed} 个文件 (+{insertions}/-{deletions})"
                        if diff_dict and files_changed
                        else ""
                    )
                )
                await log_activity(
                    agent_id=uuid.UUID(agent_id),
                    action_type="local_session_done",
                    summary=summary,
                    detail={
                        "session_id": frame.session_id,
                        "adapter": session.adapter,
                        "exit_code": frame.exit_code,
                        "final_len": len(frame.final_text or ""),
                        "duration_s": round(duration_s, 2),
                        "stats": frame.stats,
                        "diff_summary": diff_dict,
                    },
                )
            except Exception:
                pass

        elif isinstance(frame, SessionErrorFrame):
            session = bridge.sessions.pop(frame.session_id, None)
            if session is None:
                return
            self._fail_session(session, RuntimeError(frame.error))
            logger.warning(
                f"[Dispatcher] session error: {frame.session_id} err={frame.error!r}"
            )
            try:
                duration_s = (
                    datetime.now(timezone.utc) - session.started_at
                ).total_seconds()
                await log_activity(
                    agent_id=uuid.UUID(agent_id),
                    action_type="local_session_error",
                    summary=f"本地 agent session 报错 ({session.adapter}): {frame.error[:120]}",
                    detail={
                        "session_id": frame.session_id,
                        "adapter": session.adapter,
                        "error": frame.error,
                        "duration_s": round(duration_s, 2),
                    },
                )
            except Exception:
                pass

        elif isinstance(frame, ToolCallFrame):
            # Reverse call: local agent is asking Clawith to run a tool.
            asyncio.create_task(self._run_reverse_call(bridge, frame))

        # SessionAcceptedFrame / PongFrame: no action needed (logged only)

    # ── Reverse tool call (bridge → server) ───────────────────────────

    async def _run_reverse_call(self, bridge: _Bridge, frame: ToolCallFrame) -> None:
        """Execute a bridge-initiated tool call, then send tool.response back."""
        session = bridge.sessions.get(frame.session_id)
        if session is None:
            logger.warning(f"[Dispatcher] reverse call for unknown session {frame.session_id}")
            return

        # Allowlist enforcement.
        if frame.name not in ALLOWED_REVERSE_TOOLS:
            resp = ToolResponseFrame(
                session_id=frame.session_id,
                reverse_call_id=frame.reverse_call_id,
                error=f"tool {frame.name!r} not allowed as reverse call",
            )
            try:
                await self._send(bridge, resp.model_dump(mode="json"))
            except Exception:
                pass
            return

        # Look up the agent's creator_id to use as user_id for execute_tool.
        # Reverse calls originate from the agent; we attribute them to the owner.
        user_id = await self._lookup_agent_creator(bridge.agent_id)

        # Audit: record the reverse call intent BEFORE execution, so an
        # attempt is logged even if execute_tool hangs or crashes mid-way.
        # The tool itself (send_message_to_agent etc.) is already tenant-scoped;
        # this extra log makes the reverse-path attribution explicit for ops.
        try:
            await log_activity(
                agent_id=uuid.UUID(bridge.agent_id),
                action_type="reverse_tool_call",
                summary=f"本地 agent 通过 bridge 发起反向调用: {frame.name}",
                detail={
                    "session_id": frame.session_id,
                    "reverse_call_id": frame.reverse_call_id,
                    "tool_name": frame.name,
                    "argument_keys": sorted(list(frame.arguments.keys()))[:20],
                    "adapter": session.adapter,
                },
            )
        except Exception:
            pass

        result: str
        error: str | None = None
        try:
            from app.services.agent_tools import execute_tool as _execute_tool
            result = await _execute_tool(
                tool_name=frame.name,
                arguments=frame.arguments,
                agent_id=uuid.UUID(bridge.agent_id),
                user_id=user_id,
                session_id=frame.session_id,
            )
        except Exception as e:
            logger.exception(f"[Dispatcher] reverse tool {frame.name!r} failed: {e}")
            result = ""
            error = str(e)

        # Audit: record outcome so the log pair (intent + outcome) is
        # complete and easy to correlate by reverse_call_id.
        try:
            await log_activity(
                agent_id=uuid.UUID(bridge.agent_id),
                action_type="reverse_tool_result",
                summary=(
                    f"反向调用 {frame.name} 失败: {error[:100]}"
                    if error else f"反向调用 {frame.name} 完成"
                ),
                detail={
                    "session_id": frame.session_id,
                    "reverse_call_id": frame.reverse_call_id,
                    "tool_name": frame.name,
                    "error": error,
                    "result_preview": (result or "")[:200] if error is None else None,
                },
            )
        except Exception:
            pass

        resp = ToolResponseFrame(
            session_id=frame.session_id,
            reverse_call_id=frame.reverse_call_id,
            result=result if error is None else None,
            error=error,
        )
        try:
            await self._send(bridge, resp.model_dump(mode="json"))
        except Exception as e:
            logger.warning(f"[Dispatcher] failed to deliver tool.response: {e}")

    async def _lookup_agent_creator(self, agent_id: str) -> uuid.UUID:
        """Resolve the creator_id for an agent (used as user_id for reverse calls)."""
        try:
            from sqlalchemy import select
            from app.database import async_session
            from app.models.agent import Agent

            async with async_session() as db:
                r = await db.execute(select(Agent.creator_id).where(Agent.id == uuid.UUID(agent_id)))
                creator_id = r.scalar_one_or_none()
                if creator_id:
                    return creator_id
        except Exception as e:
            logger.warning(f"[Dispatcher] creator lookup failed: {e}")
        # Last-resort: use agent_id as a surrogate so execute_tool doesn't crash on None.
        return uuid.UUID(agent_id)

    # ── Helpers ───────────────────────────────────────────────────────

    async def _send(self, bridge: _Bridge, payload: dict[str, Any]) -> None:
        """Serialize bridge writes via lock (asyncio.WebSocket isn't goroutine-safe)."""
        async with bridge.lock:
            await bridge.ws.send_json(payload)

    def _fail_session(self, session: _Session, exc: BaseException) -> None:
        """Mark a session as failed and tell the consumer to stop."""
        if not session.future.done():
            session.future.set_exception(exc)
        try:
            session.events.put_nowait({"kind": "status", "payload": {"state": "error", "error": str(exc)}})
        except Exception:
            pass
        try:
            session.events.put_nowait(EVENT_QUEUE_SENTINEL)
        except Exception:
            pass

    async def send_hello(self, bridge_ws: WebSocket) -> None:
        """Send initial hello frame to a freshly-accepted bridge WS."""
        hello = HelloFrame(server_time=datetime.now(timezone.utc).isoformat())
        await bridge_ws.send_json(hello.model_dump(mode="json"))


# Module-level singleton.
dispatcher = SessionDispatcher()


__all__ = [
    "ALLOWED_REVERSE_TOOLS",
    "BridgeDisconnected",
    "BridgeUnavailable",
    "EVENT_QUEUE_SENTINEL",
    "SessionDispatcher",
    "SessionRejected",
    "dispatcher",
    "PROTOCOL_VERSION",
]
