"""Best-effort realtime notification for already committed group messages."""

from __future__ import annotations

import uuid

from loguru import logger

from app.database import async_session
from app.services.group_message_projection import load_committed_group_message_projection
from app.services.realtime_runtime import realtime_router


GROUP_MEMBERSHIP_REVOKED_EVENT = "membership.revoked"
GROUP_MEMBERSHIP_REVOKED_CLOSE_CODE = 4003


async def publish_committed_group_message(
    *,
    tenant_id: uuid.UUID,
    message_id: uuid.UUID,
) -> None:
    """Publish one committed ``message.created`` hint without affecting delivery.

    The database remains authoritative. Missing rows, projection failures, and
    Redis failures are logged and swallowed because reconnect ``after`` backfill
    repairs every missed best-effort WebSocket event.
    """
    try:
        async with async_session() as db:
            projection = await load_committed_group_message_projection(
                db,
                tenant_id=tenant_id,
                message_id=message_id,
            )
        if projection is None:
            # Runtime delivery uses one shared receipt type for direct and group
            # sessions. A direct message is therefore an expected no-op here.
            logger.debug(
                "[GroupRealtime] Message is not an active group projection tenant={} message={}",
                tenant_id,
                message_id,
            )
            return
        await realtime_router.route_scope_message(
            scope_type="group",
            scope_id=str(projection.group_id),
            message=projection.event,
            tenant_id=str(projection.tenant_id),
            participant_allowlist=[
                str(participant_id)
                for participant_id in projection.active_participant_ids
            ],
        )
    except Exception as exc:
        logger.opt(exception=True).warning(
            "[GroupRealtime] Publish failed tenant={} message={}: {}",
            tenant_id,
            message_id,
            exc,
        )


async def publish_group_membership_revoked(
    *,
    tenant_id: uuid.UUID,
    group_id: uuid.UUID,
    participant_id: uuid.UUID,
) -> None:
    """Close one removed participant's group sockets on every live instance.

    The membership row is already durably revoked when this best-effort hint is
    published. Redis or socket failures are therefore swallowed; the group
    socket heartbeat remains the eventual authorization fallback.
    """
    event = {
        "type": GROUP_MEMBERSHIP_REVOKED_EVENT,
        "group_id": str(group_id),
        "participant_id": str(participant_id),
        "code": GROUP_MEMBERSHIP_REVOKED_CLOSE_CODE,
        "content": "Group membership was revoked",
    }
    try:
        await realtime_router.route_scope_message(
            scope_type="group",
            scope_id=str(group_id),
            message=event,
            participant_id=str(participant_id),
            tenant_id=str(tenant_id),
        )
    except Exception as exc:
        logger.opt(exception=True).warning(
            "[GroupRealtime] Membership revoke publish failed tenant={} "
            "group={} participant={}: {}",
            tenant_id,
            group_id,
            participant_id,
            exc,
        )


__all__ = [
    "GROUP_MEMBERSHIP_REVOKED_CLOSE_CODE",
    "GROUP_MEMBERSHIP_REVOKED_EVENT",
    "publish_committed_group_message",
    "publish_group_membership_revoked",
]
