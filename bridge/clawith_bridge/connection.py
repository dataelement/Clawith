"""WebSocket connection + reconnect loop.

One long-lived task per process. On disconnect, all in-flight sessions are
cancelled (server will fail them to the LLM loop with BridgeDisconnected).
Reconnects with exponential backoff, then re-advertises via bridge.register.
"""
from __future__ import annotations

import asyncio
import json
import random
from typing import Any

from loguru import logger
from pydantic import BaseModel

try:
    import websockets
    from websockets.exceptions import ConnectionClosed
except ImportError as _e:  # pragma: no cover
    raise SystemExit("pip install websockets>=12") from _e

from . import PROTOCOL_VERSION, __version__
from .config import BridgeConfig
from .protocol import (
    BridgeRegisterFrame,
    HelloFrame,
    PongFrame,
    SessionCancelFrame,
    SessionInputFrame,
    SessionStartFrame,
    ToolResponseFrame,
    parse_inbound,
)
from .reverse_tools import ReverseCallClient
from .session_manager import SessionManager


class BridgeClient:
    def __init__(self, config: BridgeConfig) -> None:
        self.config = config
        self._ws = None
        self._stopped = asyncio.Event()
        self._reverse = ReverseCallClient()
        self._session_mgr: SessionManager | None = None

    async def run_forever(self) -> None:
        backoff = self.config.reconnect_min
        while not self._stopped.is_set():
            try:
                await self._connect_and_run()
                backoff = self.config.reconnect_min  # reset on clean close
            except asyncio.CancelledError:
                raise
            except ConnectionClosed as e:
                logger.warning(f"connection closed: code={e.code} reason={e.reason!r}")
            except OSError as e:
                logger.warning(f"network error: {e}")
            except Exception as e:
                logger.exception(f"bridge loop error: {e}")

            if self._stopped.is_set():
                break

            # Exponential backoff with jitter
            sleep_for = min(backoff, self.config.reconnect_max)
            jitter = sleep_for * 0.2 * random.random()
            logger.info(f"reconnecting in {sleep_for + jitter:.1f}s …")
            try:
                await asyncio.wait_for(self._stopped.wait(), timeout=sleep_for + jitter)
                break  # stop requested
            except asyncio.TimeoutError:
                pass
            backoff = min(backoff * 2, self.config.reconnect_max)

    def stop(self) -> None:
        self._stopped.set()

    # ── Internals ─────────────────────────────────────────────────────

    async def _connect_and_run(self) -> None:
        url = f"{self.config.server.rstrip('/')}/ws/bridge?token={self.config.token}"
        logger.info(f"dialing: {url.replace(self.config.token, '***')}")
        async with websockets.connect(
            url,
            max_size=2 * 1024 * 1024,
            ping_interval=self.config.ping_interval,
            ping_timeout=self.config.ping_interval * 2,
        ) as ws:
            self._ws = ws
            self._session_mgr = SessionManager(self.config, self._send_model)

            # 1. Expect server hello
            hello_raw = await asyncio.wait_for(ws.recv(), timeout=15)
            hello = json.loads(hello_raw)
            if hello.get("type") != "hello":
                logger.error(f"unexpected first frame from server: {hello}")
                return
            logger.info(f"server hello: v={hello.get('v')} server_time={hello.get('server_time')}")

            # 2. Send bridge.register
            adapters = self.config.enabled_adapters()
            register = BridgeRegisterFrame(
                bridge_version=f"clawith-bridge/{__version__}",
                adapters=adapters,
                capabilities={
                    "interactive_input": False,
                    "cancellation": True,
                    "reverse_tools": True,
                    "protocol_version": PROTOCOL_VERSION,
                },
            )
            await self._send_model(register)
            logger.info(f"registered: adapters={adapters}")

            # 3. Enter read loop
            await self._read_loop(ws)

    async def _read_loop(self, ws: Any) -> None:
        try:
            async for raw in ws:
                try:
                    data = json.loads(raw)
                except json.JSONDecodeError:
                    logger.warning("received non-JSON frame, ignoring")
                    continue
                await self._handle_frame(data)
        finally:
            if self._session_mgr:
                await self._session_mgr.fail_all("bridge disconnected")
            self._reverse.fail_all(ConnectionError("bridge disconnected"))
            self._ws = None
            self._session_mgr = None

    async def _handle_frame(self, data: dict[str, Any]) -> None:
        assert self._session_mgr is not None
        frame = parse_inbound(data)
        if frame is None:
            t = data.get("type")
            if t == "ping":
                await self._send_raw({"type": "pong"})
            else:
                logger.debug(f"unknown frame from server: type={t!r}")
            return

        if isinstance(frame, HelloFrame):
            logger.info(f"second hello received (server resynced?): {frame.server_time}")
            return

        if isinstance(frame, SessionStartFrame):
            await self._session_mgr.start(
                session_id=frame.session_id,
                adapter=frame.adapter,
                prompt=frame.prompt,
                params=frame.params,
                cwd=frame.cwd,
                env=frame.env,
                timeout_s=frame.timeout_s,
            )
            return

        if isinstance(frame, SessionInputFrame):
            await self._session_mgr.send_input(frame.session_id, frame.text)
            return

        if isinstance(frame, SessionCancelFrame):
            await self._session_mgr.cancel(frame.session_id, frame.reason)
            return

        if isinstance(frame, ToolResponseFrame):
            self._reverse.resolve(frame.reverse_call_id, frame.result, frame.error)
            return

        logger.debug(f"unhandled inbound frame: {type(frame).__name__}")

    async def _send_model(self, model: BaseModel) -> None:
        await self._send_raw(model.model_dump(mode="json"))

    async def _send_raw(self, payload: dict[str, Any]) -> None:
        ws = self._ws
        if ws is None:
            raise ConnectionError("not connected")
        await ws.send(json.dumps(payload))
