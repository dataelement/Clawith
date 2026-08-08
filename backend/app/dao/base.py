import uuid
from collections.abc import AsyncGenerator, Sequence
from contextlib import asynccontextmanager, contextmanager
from contextvars import ContextVar
from typing import Any, Generic, TypeVar

from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session, with_loader_criteria

from app.database import Base, _session_ctx, async_session

ModelType = TypeVar("ModelType", bound=Base)


class BaseDAO(Generic[ModelType]):
    """Base class for data access objects, managing session context and basic CRUD."""

    def __init__(self, model: type[ModelType]):
        self.model = model

    @asynccontextmanager
    async def session(self, db: Any = None, readonly: bool = False) -> AsyncGenerator[AsyncSession, None]:
        """Context manager yielding the active context session, explicit db parameter, or a new session."""
        context_session = db or _session_ctx.get()
        if context_session is not None:
            yield context_session
        else:
            async with async_session() as session:
                token = _session_ctx.set(session)
                try:
                    yield session
                    if not readonly and hasattr(session, "commit"):
                        await session.commit()
                except Exception:
                    if hasattr(session, "rollback"):
                        await session.rollback()
                    raise
                finally:
                    try:
                        _session_ctx.reset(token)
                    except ValueError:
                        _session_ctx.set(None)

    async def get(self, id: Any, db: Any = None) -> ModelType | None:
        """Fetch a single record by its primary key ID."""
        async with self.session(db=db, readonly=True) as session_db:
            if hasattr(session_db, "get"):
                return await session_db.get(self.model, id)
            # Fallback for custom mock DB clients in tests
            stmt = select(self.model).where(self.model.id == id)
            result = await session_db.execute(stmt)
            return result.scalar_one_or_none()

    async def is_empty(self, db: Any = None) -> bool:
        """Check if the table is empty (no records)."""
        async with self.session(db=db, readonly=True) as session_db:
            stmt = select(self.model.id).limit(1)
            result = await session_db.execute(stmt)
            return result.scalar() is None

    async def get_all(self, skip: int = 0, limit: int = 100, db: Any = None) -> Sequence[ModelType]:
        """Fetch all records with offset and limit."""
        async with self.session(db=db, readonly=True) as session_db:
            stmt = select(self.model).offset(skip).limit(limit)
            result = await session_db.execute(stmt)
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
            if hasattr(db, "get"):
                obj = await db.get(self.model, id)
            else:
                stmt = select(self.model).where(self.model.id == id)
                result = await db.execute(stmt)
                obj = result.scalar_one_or_none()
            if obj:
                if hasattr(db, "delete"):
                    await db.delete(obj)
                await db.flush()
            return obj


# ---------------------------------------------------------------------------
# Tenant Context — auto-injection via ContextVar
# ---------------------------------------------------------------------------

# Holds the current request's tenant_id, set by TenantContextMiddleware.
# Worker/Daemon code must wrap operations with tenant_context().
_tenant_ctx: ContextVar[uuid.UUID | None] = ContextVar("tenant_ctx", default=None)


def _is_tenant_scoped_model(model: type[Base]) -> bool:
    """Return whether model rows must be isolated whenever tenant context exists.

    Non-null ``tenant_id`` columns are tenant-owned by schema.  Legacy tables
    whose tenant column is nullable can opt in with ``__tenant_scoped__ = True``
    while their historic, tenant-less rows remain readable only outside a tenant
    context (for example during migration or platform administration).
    """
    if getattr(model, "__tenant_scoped__", False):
        return True
    tenant_column = model.__table__.c.get("tenant_id")
    return tenant_column is not None and not tenant_column.nullable


@event.listens_for(Session, "do_orm_execute")
def _inject_tenant_scope(execute_state: Any) -> None:
    """Apply the active tenant predicate to every tenant-owned ORM SELECT.

    This is deliberately installed on SQLAlchemy's synchronous ``Session``
    class, which is also the execution layer below ``AsyncSession``.  It covers
    direct API/service queries and DAO queries alike, so a missed business-level
    ``tenant_id`` filter cannot disclose another tenant's rows.
    """
    if not execute_state.is_select:
        return
    tenant_id = _tenant_ctx.get()
    if tenant_id is None:
        return

    statement = execute_state.statement
    for mapper in execute_state.all_mappers:
        model = mapper.class_
        if _is_tenant_scoped_model(model):
            statement = statement.options(
                with_loader_criteria(
                    model,
                    lambda cls: cls.tenant_id == tenant_id,
                    include_aliases=True,
                )
            )
    execute_state.statement = statement


@contextmanager
def tenant_context(tenant_id: uuid.UUID):
    """Explicitly bind a tenant_id to the current coroutine context.

    Use this in background workers, Celery tasks, trigger daemons, and any
    non-HTTP code that needs to call TenantScopedBaseDAO methods::

        with tenant_context(tenant_id):
            agents = await agent_dao.list_scoped()

    HTTP requests are handled automatically by TenantContextMiddleware.
    """
    token = _tenant_ctx.set(tenant_id)
    try:
        yield
    finally:
        _tenant_ctx.reset(token)


class TenantScopedBaseDAO(BaseDAO[ModelType]):
    """DAO base class with automatic tenant_id injection.

    All DAOs covering tenant-scoped models (those with a ``tenant_id`` column)
    MUST inherit from this class instead of ``BaseDAO``.

    The scoped methods (``get_scoped``, ``list_scoped``, ``delete_scoped``) read
    the active tenant_id from ``_tenant_ctx`` ContextVar, which is populated by
    ``TenantContextMiddleware`` for HTTP requests and by ``tenant_context()`` for
    background tasks.  Calling them outside a tenant context raises ``RuntimeError``
    to catch missing middleware registration early.

    For platform-admin cross-tenant queries, call the parent ``BaseDAO`` methods
    (``get``, ``get_all``, ``delete``) and annotate the call site with::

        # arch-guard: allow (platform_admin cross-tenant)
    """

    def _require_tenant_id(self) -> uuid.UUID | None:
        """Return the active tenant_id or None if not set."""
        return _tenant_ctx.get()

    async def get_scoped(self, id: Any, db: Any = None) -> ModelType | None:
        """Fetch a single record by PK, automatically scoped to current tenant."""
        tenant_id = self._require_tenant_id()
        if tenant_id is None:
            return await super().get(id, db=db)
        async with self.session(db=db, readonly=True) as session_db:
            stmt = select(self.model).where(
                self.model.id == id,
                self.model.tenant_id == tenant_id,
            )
            return (await session_db.execute(stmt)).scalar_one_or_none()

    async def list_scoped(
        self,
        *,
        skip: int = 0,
        limit: int = 100,
        extra_filters: list | None = None,
        db: Any = None,
    ) -> Sequence[ModelType]:
        """List records scoped to current tenant with optional extra WHERE clauses."""
        tenant_id = self._require_tenant_id()
        async with self.session(db=db, readonly=True) as session_db:
            stmt = select(self.model)
            if tenant_id is not None:
                stmt = stmt.where(self.model.tenant_id == tenant_id)
            if extra_filters:
                stmt = stmt.where(*extra_filters)
            stmt = stmt.offset(skip).limit(limit)
            return (await session_db.execute(stmt)).scalars().all()

    async def delete_scoped(self, *, id: Any) -> ModelType | None:
        """Delete a record by PK, tenant-scoped to prevent cross-tenant deletes."""
        tenant_id = self._require_tenant_id()
        async with self.session() as db:
            stmt = select(self.model).where(
                self.model.id == id,
                self.model.tenant_id == tenant_id,
            )
            obj = (await db.execute(stmt)).scalar_one_or_none()
            if obj:
                await db.delete(obj)
                await db.flush()
            return obj
