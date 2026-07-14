"""Database connection and session management."""

from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import asynccontextmanager
from contextvars import ContextVar

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase
from loguru import logger

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


AfterCommitCallback = Callable[[], Awaitable[None]]
AfterRollbackCallback = Callable[[], Awaitable[None]]
_AFTER_COMMIT_CALLBACKS_KEY = "after_commit_callbacks"
_AFTER_ROLLBACK_CALLBACKS_KEY = "after_rollback_callbacks"


def add_after_commit_callback(
    session: AsyncSession,
    callback: AfterCommitCallback,
) -> None:
    """Stage a best-effort side effect that may only run after commit succeeds."""
    info = getattr(session, "info", None)
    if info is None:
        info = {}
        setattr(session, "info", info)
    info.setdefault(_AFTER_COMMIT_CALLBACKS_KEY, []).append(callback)


def add_after_rollback_callback(
    session: AsyncSession,
    callback: AfterRollbackCallback,
) -> None:
    """Stage a best-effort compensation that may only run after rollback."""
    info = getattr(session, "info", None)
    if info is None:
        info = {}
        setattr(session, "info", info)
    info.setdefault(_AFTER_ROLLBACK_CALLBACKS_KEY, []).append(callback)


def _clear_after_commit_callbacks(session: AsyncSession) -> None:
    info = getattr(session, "info", None)
    if info is not None:
        info.pop(_AFTER_COMMIT_CALLBACKS_KEY, None)


def _clear_after_rollback_callbacks(session: AsyncSession) -> None:
    info = getattr(session, "info", None)
    if info is not None:
        info.pop(_AFTER_ROLLBACK_CALLBACKS_KEY, None)


async def _run_after_commit_callbacks(session: AsyncSession) -> None:
    info = getattr(session, "info", None)
    callbacks = tuple(info.pop(_AFTER_COMMIT_CALLBACKS_KEY, ())) if info else ()
    for callback in callbacks:
        try:
            await callback()
        except Exception as exc:
            # The database transaction is already durable. Realtime notifications
            # are hints; clients recover any missed event with the message cursor.
            logger.warning(f"[DB] after-commit callback failed: {exc}")


async def _run_after_rollback_callbacks(session: AsyncSession) -> None:
    info = getattr(session, "info", None)
    callbacks = tuple(info.pop(_AFTER_ROLLBACK_CALLBACKS_KEY, ())) if info else ()
    # Compensations unwind external side effects in reverse registration order.
    # This also lets callers register resource cleanup first and state restore
    # second, so the resource stays held until restoration has completed.
    for callback in reversed(callbacks):
        try:
            await callback()
        except Exception as exc:
            # Compensation is best-effort and must never replace the database
            # exception that caused this rollback.
            logger.warning(f"[DB] after-rollback callback failed: {exc}")


async def _rollback_transaction(session: AsyncSession) -> None:
    """Rollback and run compensations without replacing the original error."""
    _clear_after_commit_callbacks(session)
    try:
        await session.rollback()
    except Exception as exc:
        logger.warning(f"[DB] rollback failed while handling transaction error: {exc}")
    await _run_after_rollback_callbacks(session)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """Dependency for getting async database sessions."""
    async with async_session() as session:
        token = _session_ctx.set(session)
        try:
            yield session
            await session.commit()
            _clear_after_rollback_callbacks(session)
            await _run_after_commit_callbacks(session)
        except Exception:
            await _rollback_transaction(session)
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
                _clear_after_rollback_callbacks(session)
                await _run_after_commit_callbacks(session)
        except Exception:
            if hasattr(session, "rollback"):
                await _rollback_transaction(session)
            else:
                _clear_after_commit_callbacks(session)
                await _run_after_rollback_callbacks(session)
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
            _clear_after_rollback_callbacks(session)
            await _run_after_commit_callbacks(session)
        except Exception:
            await _rollback_transaction(session)
            raise
        finally:
            _session_ctx.reset(token)
