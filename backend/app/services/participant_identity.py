"""Transaction-scoped helpers for User and Agent participant identities."""

import uuid
from typing import Literal, cast

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.participant import Participant

ParticipantType = Literal["user", "agent"]
_PARTICIPANT_TYPES = frozenset({"user", "agent"})


def _sync_non_empty_identity_fields(
    participant: Participant,
    *,
    display_name: str | None,
    avatar_url: str | None,
) -> bool:
    """Apply supplied identity fields without erasing known values."""
    changed = False
    if display_name and participant.display_name != display_name:
        participant.display_name = display_name
        changed = True
    if avatar_url and participant.avatar_url != avatar_url:
        participant.avatar_url = avatar_url
        changed = True
    return changed


async def _find_participant(
    db: AsyncSession,
    participant_type: ParticipantType,
    ref_id: uuid.UUID,
) -> Participant | None:
    result = await db.execute(
        select(Participant).where(
            Participant.type == participant_type,
            Participant.ref_id == ref_id,
        )
    )
    return result.scalar_one_or_none()


async def get_or_create_participant(
    db: AsyncSession,
    participant_type: ParticipantType | str,
    ref_id: uuid.UUID,
    display_name: str,
    avatar_url: str | None = None,
) -> Participant:
    """Return one Participant identity without owning the caller's transaction.

    Creation happens inside a savepoint. If another transaction creates the same
    ``(type, ref_id)`` identity concurrently, only that savepoint is rolled back
    before the winning row is read. This helper never commits or rolls back the
    caller's outer transaction.
    """
    if participant_type not in _PARTICIPANT_TYPES:
        raise ValueError("participant_type must be 'user' or 'agent'")
    if not display_name:
        raise ValueError("display_name is required when creating a participant")

    typed_participant_type = cast(ParticipantType, participant_type)
    participant = await _find_participant(db, typed_participant_type, ref_id)
    if participant is not None:
        if _sync_non_empty_identity_fields(
            participant,
            display_name=display_name,
            avatar_url=avatar_url,
        ):
            await db.flush()
        return participant

    participant = Participant(
        type=typed_participant_type,
        ref_id=ref_id,
        display_name=display_name,
        avatar_url=avatar_url,
    )
    try:
        async with db.begin_nested():
            db.add(participant)
            await db.flush()
        return participant
    except IntegrityError:
        concurrent_participant = await _find_participant(
            db,
            typed_participant_type,
            ref_id,
        )
        if concurrent_participant is None:
            raise
        if _sync_non_empty_identity_fields(
            concurrent_participant,
            display_name=display_name,
            avatar_url=avatar_url,
        ):
            await db.flush()
        return concurrent_participant


async def get_or_create_user_participant(
    db: AsyncSession,
    user_id: uuid.UUID,
    display_name: str,
    avatar_url: str | None = None,
) -> Participant:
    """Return the Participant identity for a User."""
    return await get_or_create_participant(
        db,
        "user",
        user_id,
        display_name,
        avatar_url,
    )


async def get_or_create_agent_participant(
    db: AsyncSession,
    agent_id: uuid.UUID,
    display_name: str,
    avatar_url: str | None = None,
) -> Participant:
    """Return the Participant identity for an Agent."""
    return await get_or_create_participant(
        db,
        "agent",
        agent_id,
        display_name,
        avatar_url,
    )
