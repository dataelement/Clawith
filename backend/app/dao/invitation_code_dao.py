"""DAO for InvitationCode model."""

from sqlalchemy import select

from app.dao.base import BaseDAO
from app.models.invitation_code import InvitationCode


class InvitationCodeDAO(BaseDAO[InvitationCode]):
    """DAO for InvitationCode model."""

    def __init__(self) -> None:
        super().__init__(InvitationCode)

    async def get_active_by_code(self, code: str) -> InvitationCode | None:
        """Find an active invitation code with a tenant association."""
        async with self.session() as db:
            result = await db.execute(
                select(InvitationCode).where(
                    InvitationCode.code == code,
                    InvitationCode.is_active == True,
                    InvitationCode.tenant_id.is_not(None),
                )
            )
            return result.scalar_one_or_none()


invitation_code_dao = InvitationCodeDAO()
