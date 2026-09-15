"""One transaction shared by the owner services participating in an operation."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


@dataclass(frozen=True, slots=True)
class TransactionContext:
    """Owner repositories may flush; only the enclosing operation commits."""

    session: AsyncSession


@asynccontextmanager
async def transaction(sessions: async_sessionmaker[AsyncSession]) -> AsyncIterator[TransactionContext]:
    """Commit on success; rollback and close on failure, including cancellation."""
    async with sessions() as session, session.begin():
        yield TransactionContext(session)
