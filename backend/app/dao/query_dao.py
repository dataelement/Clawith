"""Generic DAO bridge for legacy SQLAlchemy statements.

This module is intentionally small: it lets large legacy modules route database
I/O through the DAO layer while domain-specific DAOs are introduced
incrementally.
"""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.database import _session_ctx, async_session


class QueryDAO:
    """Low-level database operation wrapper used during DAO migration."""

    @asynccontextmanager
    async def session(self, *, readonly: bool = False) -> AsyncGenerator[AsyncSession, None]:
        """Yield a short-lived session, reusing the current context session when present."""
        context_session = _session_ctx.get()
        if context_session is not None:
            yield context_session
            return

        async with async_session() as session:
            token = _session_ctx.set(session)
            try:
                yield session
                if not readonly:
                    await session.commit()
            except Exception:
                await session.rollback()
                raise
            finally:
                _session_ctx.reset(token)

    async def execute(
        self,
        db: AsyncSession,
        statement: Any,
        params: Any | None = None,
        *,
        execution_options: dict[str, Any] | None = None,
    ) -> Any:
        """Execute a SQLAlchemy statement on a caller-owned session."""
        if execution_options is not None:
            return await db.execute(statement, params, execution_options=execution_options)
        if params is not None:
            return await db.execute(statement, params)
        return await db.execute(statement)

    async def scalar(self, db: AsyncSession, statement: Any, params: Any | None = None) -> Any:
        """Execute a scalar SQLAlchemy statement on a caller-owned session."""
        if params is not None:
            return await db.scalar(statement, params)
        return await db.scalar(statement)

    async def get(self, db: AsyncSession, model: Any, ident: Any) -> Any:
        """Load one ORM object by primary key."""
        return await db.get(model, ident)

    def add(self, db: AsyncSession, instance: Any) -> None:
        """Add an ORM object to a caller-owned session."""
        db.add(instance)

    def add_all(self, db: AsyncSession, instances: list[Any]) -> None:
        """Add multiple ORM objects to a caller-owned session."""
        db.add_all(instances)

    async def delete(self, db: AsyncSession, instance: Any) -> None:
        """Delete an ORM object from a caller-owned session."""
        await db.delete(instance)

    async def flush(self, db: AsyncSession) -> None:
        """Flush pending changes."""
        await db.flush()

    async def refresh(self, db: AsyncSession, instance: Any) -> None:
        """Refresh an ORM object from the database."""
        await db.refresh(instance)

    async def commit(self, db: AsyncSession) -> None:
        """Commit a caller-owned session."""
        await db.commit()

    async def rollback(self, db: AsyncSession) -> None:
        """Rollback a caller-owned session."""
        await db.rollback()


query_dao = QueryDAO()
