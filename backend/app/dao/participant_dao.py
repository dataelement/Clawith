"""DAO for Participant model."""

from app.dao.base import BaseDAO
from app.models.participant import Participant


class ParticipantDAO(BaseDAO[Participant]):
    """DAO for Participant model."""

    def __init__(self) -> None:
        super().__init__(Participant)

    async def create_for_user(
        self,
        user_id,
        display_name: str | None = None,
        avatar_url: str | None = None,
    ) -> Participant:
        """Create a Participant record linked to a User."""
        async with self.session() as db:
            participant = Participant(
                type="user",
                ref_id=user_id,
                display_name=display_name,
                avatar_url=avatar_url,
            )
            db.add(participant)
            await db.flush()
            return participant


participant_dao = ParticipantDAO()
