"""Best-effort realtime notification for already committed group messages."""

from __future__ import annotations

import uuid

from loguru import logger
from sqlalchemy import select

from app.database import async_session
from app.models.group import Group, GroupMember
from app.services.group_message_projection import load_committed_group_message_projection
from app.services.realtime_runtime import realtime_router


GROUP_MEMBERSHIP_REVOKED_EVENT = "membership.revoked"
GROUP_MEMBERSHIP_REVOKED_CLOSE_CODE = 4003
GROUP_RUNTIME_STATUS_EVENT = "runtime.status"


async def publish_group_runtime_status(
    *,
    tenant_id: uuid.UUID,
    group_id: uuid.UUID,
    session_id: uuid.UUID,
    run_id: uuid.UUID,
    status: str,
    agent_id: uuid.UUID | None,
    candidate_agent_ids: tuple[uuid.UUID, ...] = (),
) -> None:
    """Best-effort ephemeral activity for a group Runtime Run.

    Unlike ``message.created``, this event has no ChatMessage projection and is
    therefore absent from history, cursor backfill, and model context.
    """
    try:
        async with async_session() as db:
            result = await db.execute(
                select(GroupMember.participant_id)
                .join(Group, Group.id == GroupMember.group_id)
                .where(
                    Group.id == group_id,
                    Group.tenant_id == tenant_id,
                    Group.deleted_at.is_(None),
                    GroupMember.removed_at.is_(None),
                )
                .order_by(GroupMember.participant_id)
            )
            participant_ids = tuple(result.scalars().all())
        if not participant_ids:
            return
        await realtime_router.route_scope_message(
            scope_type="group",
            scope_id=str(group_id),
            message={
                "type": GROUP_RUNTIME_STATUS_EVENT,
                "group_id": str(group_id),
                "session_id": str(session_id),
                "run_id": str(run_id),
                "status": status,
                "agent_id": str(agent_id) if agent_id is not None else None,
                "candidate_agent_ids": [str(value) for value in candidate_agent_ids],
            },
            tenant_id=str(tenant_id),
            participant_allowlist=[str(value) for value in participant_ids],
        )
    except Exception as exc:
        logger.opt(exception=True).warning(
            "[GroupRealtime] Runtime status publish failed tenant={} group={} run={}: {}",
            tenant_id,
            group_id,
            run_id,
            exc,
        )


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
    "GROUP_RUNTIME_STATUS_EVENT",
    "publish_committed_group_message",
    "publish_group_membership_revoked",
    "publish_group_runtime_status",
]
