"""Authenticated realtime socket for native Group chat activity."""

from __future__ import annotations

import asyncio
import uuid

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect
from loguru import logger
from sqlalchemy import select

from app.api.websocket import manager
from app.core.security import decode_access_token
from app.database import async_session
from app.models.group import Group, GroupMember
from app.models.participant import Participant
from app.models.user import User
from app.services.group_realtime import group_connection_key


router = APIRouter(tags=["websocket"])
_MEMBERSHIP_REVALIDATE_SECONDS = 30.0


async def _active_group_user(group_id: uuid.UUID, user_id: uuid.UUID) -> bool:
    async with async_session() as db:
        result = await db.execute(
            select(User.id)
            .join(
                Participant,
                (Participant.type == "user") & (Participant.ref_id == User.id),
            )
            .join(
                GroupMember,
                GroupMember.participant_id == Participant.id,
            )
            .join(Group, Group.id == GroupMember.group_id)
            .where(
                User.id == user_id,
                User.is_active.is_(True),
                User.tenant_id.is_not(None),
                Group.id == group_id,
                Group.tenant_id == User.tenant_id,
                Group.deleted_at.is_(None),
                GroupMember.removed_at.is_(None),
            )
        )
        return result.scalar_one_or_none() is not None


@router.websocket("/ws/group/{group_id}")
async def websocket_group(
    websocket: WebSocket,
    group_id: uuid.UUID,
    token: str = Query(...),
) -> None:
    """Push committed public messages to active human members of one native Group."""
    await websocket.accept()
    try:
        try:
            payload = decode_access_token(token)
            user_id = uuid.UUID(str(payload["sub"]))
        except Exception:
            await websocket.send_json({"type": "error", "content": "Authentication failed"})
            await websocket.close(code=4001)
            return

        try:
            allowed = await _active_group_user(group_id, user_id)
        except Exception:
            logger.exception("[GroupWS] Membership lookup failed")
            await websocket.send_json({"type": "error", "content": "Setup failed"})
            await websocket.close(code=4002)
            return
        if not allowed:
            await websocket.send_json({"type": "error", "content": "Group membership required"})
            await websocket.close(code=4003)
            return

        connection_key = group_connection_key(group_id)
        await manager.connect(connection_key, websocket, user_id=str(user_id))
        await websocket.send_json({"type": "connected", "group_id": str(group_id)})
        try:
            while True:
                try:
                    packet = await asyncio.wait_for(
                        websocket.receive_json(),
                        timeout=_MEMBERSHIP_REVALIDATE_SECONDS,
                    )
                except TimeoutError:
                    packet = None
                try:
                    still_allowed = await _active_group_user(group_id, user_id)
                except Exception:
                    logger.exception("[GroupWS] Membership revalidation failed")
                    await websocket.send_json({"type": "error", "content": "Setup failed"})
                    await websocket.close(code=4002)
                    break
                if not still_allowed:
                    await websocket.send_json(
                        {"type": "error", "content": "Group membership required"}
                    )
                    await websocket.close(code=4003)
                    break
                if packet is None:
                    continue
                if packet.get("type") == "ping":
                    await websocket.send_json({"type": "pong"})
        except WebSocketDisconnect:
            pass
        finally:
            await manager.disconnect(connection_key, websocket)
    except WebSocketDisconnect:
        return


__all__ = ["router", "websocket_group"]
