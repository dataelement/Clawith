"""Database connection and session management."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from contextvars import ContextVar

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.config import get_settings

settings = get_settings()

engine = create_async_engine(
    settings.DATABASE_URL,
    echo=settings.DEBUG,
    pool_size=20,
    max_overflow=10,
)

async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


class Base(DeclarativeBase):
    """SQLAlchemy declarative base."""

    pass


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """Dependency for getting async database sessions."""
    async with async_session() as session:
        token = _session_ctx.set(session)
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            _session_ctx.reset(token)


_session_ctx: ContextVar[AsyncSession | None] = ContextVar("db_session_ctx", default=None)


@asynccontextmanager
async def transaction(session: AsyncSession | None = None) -> AsyncGenerator[AsyncSession, None]:
    """Provide a transactional boundary using contextvars."""
    if session is not None:
        token = _session_ctx.set(session)
        try:
            yield session
            if hasattr(session, "commit"):
                await session.commit()
        except Exception:
            if hasattr(session, "rollback"):
                await session.rollback()
            raise
        finally:
            _session_ctx.reset(token)
        return

    existing_session = _session_ctx.get()
    if existing_session is not None:
        yield existing_session
        return

    async with async_session() as session:
        token = _session_ctx.set(session)
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            _session_ctx.reset(token)
