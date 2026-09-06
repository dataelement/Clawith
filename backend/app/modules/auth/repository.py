"""Private local-login persistence operations."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.auth.models import LoginSessionRecord, LoginVerifierRecord


class AuthRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    def add_verifier(self, record: LoginVerifierRecord) -> None:
        self._session.add(record)

    def add_session(self, record: LoginSessionRecord) -> None:
        self._session.add(record)

    async def flush(self) -> None:
        await self._session.flush()

    async def get_verifier_by_login_name(
        self, login_name: str, *, for_update: bool = False
    ) -> LoginVerifierRecord | None:
        statement = select(LoginVerifierRecord).where(LoginVerifierRecord.login_name == login_name)
        if for_update:
            statement = statement.with_for_update()
        return (await self._session.scalars(statement)).one_or_none()

    async def get_verifier_for_account(self, account_id: UUID) -> LoginVerifierRecord | None:
        statement = select(LoginVerifierRecord).where(LoginVerifierRecord.account_id == account_id)
        return (await self._session.scalars(statement)).one_or_none()

    async def get_session_by_token_hash(self, token_hash: str) -> LoginSessionRecord | None:
        statement = select(LoginSessionRecord).where(LoginSessionRecord.token_hash == token_hash)
        return (await self._session.scalars(statement)).one_or_none()
