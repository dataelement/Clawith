"""WebSocket endpoint for local-agent bridges.

    wss://<host>/ws/bridge?token=<agent_api_key>

Flow:
  1. Bridge dials in with agent's OpenClaw API key.
  2. Server authenticates via shared `_get_agent_by_key` from gateway.py.
  3. Server sends `hello` frame.
  4. Bridge sends `bridge.register` advertising adapters + capabilities.
  5. Server registers the bridge with `session_dispatcher`.
  6. Read loop parses inbound frames and routes to dispatcher.
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, HTTPException, Query, WebSocket, WebSocketDisconnect
from loguru import logger

from app.api.gateway import _get_agent_by_key
from app.database import async_session
from app.services.local_agent.protocol import (
    BridgeRegisterFrame,
    PongFrame,
    parse_inbound,
    PROTOCOL_VERSION,
)
from app.services.local_agent.session_dispatcher import dispatcher


router = APIRouter(tags=["bridge-ws"])


PING_INTERVAL_SEC = 30
PONG_TIMEOUT_SEC = 60


@router.websocket("/ws/bridge")
async def websocket_bridge(
    websocket: WebSocket,
    token: str = Query(..., description="Agent API key (oc-xxx)"),
):
    """Long-lived bridge channel. One bridge per agent_id."""
    await websocket.accept()

    # ── Authenticate ──
    agent = None
    try:
        async with async_session() as db:
            agent = await _get_agent_by_key(token, db)
    except HTTPException as e:
        await _close_with_error(websocket, f"auth: {e.detail}", code=4001)
        return
    except Exception as e:
        logger.exception(f"[BridgeWS] auth failed: {e}")
        await _close_with_error(websocket, "auth error", code=4001)
        return

    agent_id = str(agent.id)

    # ── Agent lifecycle gate ──
    # Reject agents that have been explicitly stopped or whose lease has
    # expired. `creating` and `error` are both recoverable — don't block.
    agent_status = getattr(agent, "status", None)
    if agent_status == "stopped":
        logger.warning(f"[BridgeWS] agent {agent_id} status=stopped, rejecting bridge")
        await _close_with_error(
            websocket,
            "agent is stopped; re-enable it before connecting a bridge",
            code=4003,
        )
        return
    if getattr(agent, "is_expired", False):
        logger.warning(f"[BridgeWS] agent {agent_id} is_expired=True, rejecting bridge")
        await _close_with_error(
            websocket,
            "agent lease has expired",
            code=4003,
        )
        return

    # ── Bridge_mode gate ──
    mode = getattr(agent, "bridge_mode", "disabled") or "disabled"
    if mode == "disabled":
        logger.warning(f"[BridgeWS] agent {agent_id} has bridge_mode=disabled, rejecting")
        await _close_with_error(
            websocket,
            "bridge_mode is disabled for this agent; enable it in agent settings",
            code=4003,
        )
        return

    # ── Send hello ──
    try:
        await dispatcher.send_hello(websocket)
    except Exception as e:
        logger.warning(f"[BridgeWS] send hello failed: {e}")
        return

    # ── Wait for bridge.register ──
    try:
        register_raw = await asyncio.wait_for(websocket.receive_json(), timeout=15)
    except asyncio.TimeoutError:
        await _close_with_error(websocket, "timeout waiting for bridge.register", code=4002)
        return
    except (WebSocketDisconnect, RuntimeError):
        return

    register_frame = parse_inbound(register_raw)
    if not isinstance(register_frame, BridgeRegisterFrame):
        await _close_with_error(websocket, "first frame must be bridge.register", code=4002)
        return

    if register_frame.v != PROTOCOL_VERSION:
        await _close_with_error(
            websocket,
            f"protocol version mismatch: server={PROTOCOL_VERSION} bridge={register_frame.v}",
            code=4002,
        )
        return

    attached = await dispatcher.attach_bridge(agent_id, websocket, register_frame)
    if not attached:
        await _close_with_error(
            websocket,
            "another bridge is already connected for this agent",
            code=4003,
        )
        return

    logger.info(
        f"[BridgeWS] attached agent={agent_id} bridge_version={register_frame.bridge_version} "
        f"adapters={register_frame.adapters}"
    )

    # ── Ping loop (keepalive) ──
    ping_task = asyncio.create_task(_ping_loop(websocket))

    # ── Read loop ──
    try:
        while True:
            try:
                raw = await websocket.receive_json()
            except WebSocketDisconnect:
                logger.info(f"[BridgeWS] bridge disconnected agent={agent_id}")
                break
            except Exception as e:
                logger.warning(f"[BridgeWS] receive error: {e}")
                break

            frame = parse_inbound(raw)
            if frame is None:
                logger.debug(
                    f"[BridgeWS] unknown/invalid frame from agent={agent_id}: type={raw.get('type')!r}"
                )
                continue

            try:
                await dispatcher.handle_inbound_frame(agent_id, frame)
            except Exception as e:
                logger.exception(f"[BridgeWS] dispatch error: {e}")
    finally:
        ping_task.cancel()
        await dispatcher.detach_bridge(agent_id)


async def _ping_loop(ws: WebSocket) -> None:
    """Periodic ping to keep WS alive through idle firewalls."""
    try:
        while True:
            await asyncio.sleep(PING_INTERVAL_SEC)
            try:
                await ws.send_json({"type": "ping"})
            except Exception:
                return
    except asyncio.CancelledError:
        return


async def _close_with_error(ws: WebSocket, message: str, code: int = 4000) -> None:
    try:
        await ws.send_json({"type": "error", "message": message})
    except Exception:
        pass
    try:
        await ws.close(code=code)
    except Exception:
        pass
