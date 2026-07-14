"""Read-only group WebSocket endpoint for committed message notifications."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import uuid

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect
from loguru import logger
from sqlalchemy import select

from app.api.websocket import manager
from app.core.security import decode_access_token
from app.database import async_session
from app.models.participant import Participant
from app.models.user import User
from app.services import group_chat_service
from app.services.group_chat_service import GroupChatServiceError
from app.services.realtime_runtime import realtime_router

router = APIRouter(tags=["websocket"])


@dataclass(frozen=True, slots=True)
class GroupSocketViewer:
    user_id: uuid.UUID
    tenant_id: uuid.UUID
    participant_id: uuid.UUID


class GroupSocketAuthorizationError(RuntimeError):
    def __init__(self, close_code: int, message: str) -> None:
        super().__init__(message)
        self.close_code = close_code


def _user_id_from_token(token: str) -> uuid.UUID:
    try:
        payload = decode_access_token(token)
        subject = payload.get("sub")
        if not subject:
            raise ValueError("JWT has no subject")
        return uuid.UUID(str(subject))
    except Exception as exc:
        raise GroupSocketAuthorizationError(4001, "Authentication failed") from exc


async def authorize_group_socket_viewer(
    *,
    group_id: uuid.UUID,
    token: str,
) -> GroupSocketViewer:
    """Resolve one active tenant user with an active human group membership."""
    user_id = _user_id_from_token(token)
    async with async_session() as db:
        user = await db.get(User, user_id)
        if user is None or not user.is_active:
            raise GroupSocketAuthorizationError(4001, "User is unavailable")
        if user.tenant_id is None:
            raise GroupSocketAuthorizationError(4003, "A tenant group membership is required")

        participant_result = await db.execute(
            select(Participant).where(
                Participant.type == "user",
                Participant.ref_id == user.id,
            )
        )
        participant = participant_result.scalar_one_or_none()
        if participant is None:
            raise GroupSocketAuthorizationError(4003, "Active group membership is required")
        try:
            await group_chat_service.authorize_group_member(
                db,
                tenant_id=user.tenant_id,
                group_id=group_id,
                participant_id=participant.id,
                human_only=True,
            )
        except GroupChatServiceError as exc:
            if exc.code == "group_not_found":
                raise GroupSocketAuthorizationError(4002, "Group not found") from exc
            raise GroupSocketAuthorizationError(4003, "Active group membership is required") from exc
        return GroupSocketViewer(
            user_id=user.id,
            tenant_id=user.tenant_id,
            participant_id=participant.id,
        )


class GroupWebSocketHandler:
    """Own one group-level push socket; all writes remain on REST endpoints."""

    def __init__(self, websocket: WebSocket, group_id: uuid.UUID, token: str) -> None:
        self.websocket = websocket
        self.group_id = group_id
        self.token = token
        self.viewer: GroupSocketViewer | None = None
        self.registered = False

    async def run(self) -> None:
        await self.websocket.accept()
        try:
            try:
                self.viewer = await authorize_group_socket_viewer(
                    group_id=self.group_id,
                    token=self.token,
                )
            except GroupSocketAuthorizationError as exc:
                await self._close_with_error(exc.close_code, str(exc))
                return
            except Exception as exc:
                logger.exception(f"[GroupWS] Initial authorization failed: {exc}")
                await self._close_with_error(1011, "Group WebSocket setup failed")
                return

            try:
                await manager.connect_scope(
                    scope_type="group",
                    scope_id=str(self.group_id),
                    websocket=self.websocket,
                    user_id=str(self.viewer.user_id),
                    participant_id=str(self.viewer.participant_id),
                    tenant_id=str(self.viewer.tenant_id),
                    auto_refresh=False,
                )
                self.registered = True
            except Exception as exc:
                logger.exception(f"[GroupWS] Presence registration failed: {exc}")
                await self._close_with_error(1011, "Group WebSocket setup failed")
                return

            await self.websocket.send_json(
                {
                    "type": "connected",
                    "group_id": str(self.group_id),
                }
            )
            await self._serve_connected()
        except WebSocketDisconnect:
            pass
        except Exception as exc:
            logger.exception(f"[GroupWS] Unexpected socket failure: {exc}")
            await self._close_with_error(1011, "Group WebSocket interrupted")
        finally:
            if self.registered:
                try:
                    await manager.disconnect_scope(
                        scope_type="group",
                        scope_id=str(self.group_id),
                        websocket=self.websocket,
                    )
                except Exception as exc:
                    logger.warning(f"[GroupWS] Presence cleanup failed: {exc}")
                self.registered = False

    async def _serve_connected(self) -> None:
        receive_task = asyncio.create_task(
            self._receive_until_disconnect(),
            name=f"group-ws-receive-{self.group_id}",
        )
        heartbeat_task = asyncio.create_task(
            self._heartbeat_loop(),
            name=f"group-ws-heartbeat-{self.group_id}",
        )
        done, pending = await asyncio.wait(
            {receive_task, heartbeat_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        for task in done:
            exception = task.exception()
            if exception is not None:
                raise exception

    async def _receive_until_disconnect(self) -> None:
        while True:
            event = await self.websocket.receive()
            if event.get("type") == "websocket.disconnect":
                raise WebSocketDisconnect(event.get("code", 1000))
            # Group chat uses REST for all writes. Inbound socket frames are ignored.

    async def _heartbeat_loop(self) -> None:
        while True:
            await asyncio.sleep(realtime_router.refresh_interval_seconds)
            try:
                current = await authorize_group_socket_viewer(
                    group_id=self.group_id,
                    token=self.token,
                )
                if self.viewer is None or current != self.viewer:
                    raise GroupSocketAuthorizationError(
                        4003,
                        "Group membership identity changed",
                    )
                refreshed = await manager.refresh_scope(
                    scope_type="group",
                    scope_id=str(self.group_id),
                    websocket=self.websocket,
                )
                if not refreshed:
                    raise RuntimeError("Group presence lease metadata is missing")
            except GroupSocketAuthorizationError as exc:
                await self._close_with_error(exc.close_code, str(exc))
                return
            except Exception as exc:
                logger.warning(f"[GroupWS] Heartbeat failed: {exc}")
                await self._close_with_error(1011, "Group WebSocket heartbeat failed")
                return

    async def _close_with_error(self, code: int, message: str) -> None:
        try:
            await self.websocket.send_json(
                {
                    "type": "error",
                    "code": code,
                    "content": message,
                }
            )
        except Exception:
            pass
        try:
            await self.websocket.close(code=code)
        except Exception:
            pass


@router.websocket("/ws/group/{group_id}")
async def websocket_group(
    websocket: WebSocket,
    group_id: uuid.UUID,
    token: str = Query(""),
) -> None:
    await GroupWebSocketHandler(websocket, group_id, token).run()


__all__ = [
    "GroupSocketAuthorizationError",
    "GroupSocketViewer",
    "GroupWebSocketHandler",
    "authorize_group_socket_viewer",
    "router",
]
