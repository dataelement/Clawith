"""Shared authenticated committed-history WebSocket lifetime, with optional execution display."""

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import cast

from fastapi import HTTPException, WebSocket, WebSocketDisconnect
from fastapi.encoders import jsonable_encoder

from app.api.product_inputs.auth import bearer_token
from app.execution_dependencies.session_streams import StreamSubscription
from app.infrastructure.errors import DomainError
from app.modules.auth.public import AuthService
from app.modules.identity_tenant.public import TenantPrincipal


@dataclass(frozen=True, slots=True)
class HistoryFrame:
    payload: dict[str, object]
    next_position: int
    has_more: bool


async def serve_product_events(socket: WebSocket, *, after_position: int,
        authorize: Callable[[TenantPrincipal], Awaitable[None]],
        read_history: Callable[[TenantPrincipal, int], Awaitable[HistoryFrame]],
        subscribe: Callable[[TenantPrincipal], Awaitable[StreamSubscription]] | None = None,
        unsubscribe: Callable[[TenantPrincipal, StreamSubscription], None] | None = None,
        closing: asyncio.Event | None = None) -> None:
    if (subscribe is None) != (unsubscribe is None):
        raise ValueError("Transient subscription requires its cleanup callback")
    auth = cast(AuthService, socket.app.state.auth)
    protocols = [item.strip() for item in socket.headers.get("sec-websocket-protocol", "").split(",")]
    supplied = next((item[5:] for item in protocols if item.startswith("auth.")), None)
    subscription = None
    try:
        token = supplied or bearer_token(socket.headers.get("authorization"))
        access = await auth.authenticate_session(token)
        await authorize(access.principal)
        if subscribe is not None:
            subscription = await subscribe(access.principal)
    except (DomainError, HTTPException):
        await socket.close(code=1008)
        return
    cursor, receiver, closer = after_position, None, None

    async def disconnected() -> None:
        while True:
            message = await socket.receive()
            if message["type"] == "websocket.disconnect":
                return

    try:
        await socket.accept(subprotocol="clawith" if "clawith" in protocols else None)
        receiver = asyncio.create_task(disconnected(), name="product-websocket-disconnect")
        if closing is not None:
            closer = asyncio.create_task(closing.wait(), name="product-websocket-close")
        next_poll = 0.0
        loop = asyncio.get_running_loop()
        while (not receiver.done() and (closing is None or not closing.is_set())
                and (subscription is None or not subscription.closed)):
            remaining = (access.expires_at - datetime.now(UTC)).total_seconds()
            if remaining <= 0:
                await socket.close(code=1008)
                return
            if loop.time() >= next_poll:
                access = await auth.authenticate_session(token)
                page = await read_history(access.principal, cursor)
                if page.payload:
                    remaining = (access.expires_at - datetime.now(UTC)).total_seconds()
                    if remaining <= 0:
                        await socket.close(code=1008)
                        return
                    async with asyncio.timeout(min(5, remaining)):
                        await socket.send_json(jsonable_encoder(page.payload))
                cursor = page.next_position
                next_poll = loop.time() + (0 if page.has_more else 1.0)
            if subscription is not None:
                for _ in range(16):
                    payload = subscription.pop()
                    if payload is None:
                        break
                    remaining = (access.expires_at - datetime.now(UTC)).total_seconds()
                    if remaining <= 0:
                        await socket.close(code=1008)
                        return
                    async with asyncio.timeout(min(5, remaining)):
                        await socket.send_text(payload)
            notified = (asyncio.create_task(subscription.ready.wait(), name="product-websocket-stream-ready")
                if subscription is not None else None)
            try:
                waiters: set[asyncio.Task[object]] = {receiver}
                if notified is not None:
                    waiters.add(notified)
                if closer is not None:
                    waiters.add(closer)
                await asyncio.wait(waiters,
                    timeout=max(0, min(next_poll - loop.time(), remaining)), return_when=asyncio.FIRST_COMPLETED)
            finally:
                if notified is not None:
                    notified.cancel()
                    await asyncio.gather(notified, return_exceptions=True)
        if not receiver.done():
            await socket.close(code=1001)
    except (DomainError, WebSocketDisconnect, TimeoutError):
        if receiver is None or not receiver.done():
            await socket.close(code=1008)
    finally:
        if subscription is not None:
            assert unsubscribe is not None
            unsubscribe(access.principal, subscription)
        if receiver is not None:
            receiver.cancel()
            await asyncio.gather(receiver, return_exceptions=True)
        if closer is not None:
            closer.cancel()
            await asyncio.gather(closer, return_exceptions=True)
