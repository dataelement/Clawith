"""Target SQLAlchemy registry and connection factories."""

from dataclasses import dataclass

from sqlalchemy.engine import URL
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.infrastructure.config import Settings, get_settings, reveal_database_url


class Base(DeclarativeBase):
    """The single declarative base for every target owner."""


@dataclass(frozen=True, slots=True)
class DatabaseResources:
    """Application-owned control and execution database resources."""

    control_engine: AsyncEngine
    execution_engine: AsyncEngine
    control_sessions: async_sessionmaker[AsyncSession]
    execution_sessions: async_sessionmaker[AsyncSession]

    async def aclose(self) -> None:
        """Await disposal of both role-isolated connection pools."""
        try:
            await self.control_engine.dispose()
        finally:
            await self.execution_engine.dispose()


def _create_role_engine(
    database_url: URL,
    *,
    echo: bool,
    pool_size: int,
    max_overflow: int,
) -> AsyncEngine:
    return create_async_engine(
        database_url,
        echo=echo,
        pool_size=pool_size,
        max_overflow=max_overflow,
    )


async def create_database_resources(settings: Settings | None = None) -> DatabaseResources:
    """Create the application-owned control and execution pools."""
    database_settings = settings or get_settings()
    database_url = reveal_database_url(database_settings.DATABASE_URL)
    control_engine = _create_role_engine(
        database_url,
        echo=database_settings.DEBUG,
        pool_size=database_settings.CONTROL_DATABASE_POOL_SIZE,
        max_overflow=database_settings.DATABASE_POOL_MAX_OVERFLOW,
    )
    execution_engine: AsyncEngine | None = None
    try:
        execution_engine = _create_role_engine(
            database_url,
            echo=database_settings.DEBUG,
            pool_size=database_settings.EXECUTION_DATABASE_POOL_SIZE,
            max_overflow=database_settings.DATABASE_POOL_MAX_OVERFLOW,
        )
        return DatabaseResources(
            control_engine=control_engine,
            execution_engine=execution_engine,
            control_sessions=create_session_factory(control_engine),
            execution_sessions=create_session_factory(execution_engine),
        )
    except BaseException:
        try:
            await control_engine.dispose()
        finally:
            if execution_engine is not None:
                await execution_engine.dispose()
        raise


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Bind target sessions to the supplied engine."""
    return async_sessionmaker(engine, expire_on_commit=False)
