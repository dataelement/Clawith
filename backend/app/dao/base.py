from collections.abc import AsyncGenerator, Sequence
from contextlib import asynccontextmanager
from typing import Any, Generic, Type, TypeVar

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import Base, _session_ctx, async_session

ModelType = TypeVar("ModelType", bound=Base)


class BaseDAO(Generic[ModelType]):
    """Base class for data access objects, managing session context and basic CRUD."""

    def __init__(self, model: Type[ModelType]):
        self.model = model

    @asynccontextmanager
    async def session(self) -> AsyncGenerator[AsyncSession, None]:
        """Context manager yielding the active context session or a new one."""
        context_session = _session_ctx.get()
        if context_session is not None:
            yield context_session
        else:
            async with async_session() as session:
                yield session

    async def get(self, id: Any) -> ModelType | None:
        """Fetch a single record by its primary key ID."""
        async with self.session() as db:
            if hasattr(db, "get"):
                return await db.get(self.model, id)
            # Fallback for custom mock DB clients in tests
            stmt = select(self.model).where(self.model.id == id)
            result = await db.execute(stmt)
            return result.scalar_one_or_none()

    async def is_empty(self) -> bool:
        """Check if the table is empty (no records)."""
        async with self.session() as db:
            stmt = select(self.model.id).limit(1)
            result = await db.execute(stmt)
            return result.scalar() is None

    async def get_all(self, skip: int = 0, limit: int = 100) -> Sequence[ModelType]:
        """Fetch all records with offset and limit."""
        async with self.session() as db:
            stmt = select(self.model).offset(skip).limit(limit)
            result = await db.execute(stmt)
            return result.scalars().all()

    async def create(self, *, obj_in: dict[str, Any]) -> ModelType:
        """Create a new record."""
        async with self.session() as db:
            db_obj = self.model(**obj_in)
            db.add(db_obj)
            await db.flush()
            return db_obj

    async def update(self, *, db_obj: ModelType, obj_in: dict[str, Any]) -> ModelType:
        """Update an existing record."""
        async with self.session() as db:
            for field, value in obj_in.items():
                if hasattr(db_obj, field):
                    setattr(db_obj, field, value)
            db.add(db_obj)
            await db.flush()
            return db_obj

    async def delete(self, *, id: Any) -> ModelType | None:
        """Delete a record by ID."""
        async with self.session() as db:
            obj = await self.get(id)
            if obj:
                if hasattr(db, "delete"):
                    await db.delete(obj)
                await db.flush()
            return obj

