import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy import String, create_engine, select
from sqlalchemy.orm import Mapped, Session, mapped_column

from app.dao.base import BaseDAO, tenant_context
from app.database import Base, _session_ctx


class DummyModel:
    id = "id"


class TenantScopedRecord(Base):
    """Small mapped record proving the session-level isolation hook."""

    __tablename__ = "test_tenant_scoped_records"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String, nullable=False)


class RecordingSession:
    def __init__(self):
        self.added = []
        self.deleted = []
        self.flushed = False
        self.committed = False
        self.rolled_back = False
        self.get_calls = []
        self.execute_calls = 0
        self.object_to_get = SimpleNamespace(id="row-1")

    def add(self, obj):
        self.added.append(obj)

    async def flush(self):
        self.flushed = True

    async def commit(self):
        self.committed = True

    async def rollback(self):
        self.rolled_back = True

    async def get(self, model, id):
        self.get_calls.append((model, id))
        return self.object_to_get

    async def delete(self, obj):
        self.deleted.append(obj)


class SessionFactory:
    def __init__(self, session):
        self.session = session

    def __call__(self):
        return self

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, exc_type, exc, tb):
        return False


@pytest.mark.asyncio
async def test_standalone_dao_session_sets_context_and_commits(monkeypatch):
    session = RecordingSession()
    monkeypatch.setattr("app.dao.base.async_session", SessionFactory(session))

    dao = BaseDAO(DummyModel)

    async with dao.session() as db:
        assert db is session
        assert _session_ctx.get() is session

    assert session.committed is True
    assert session.rolled_back is False
    assert _session_ctx.get() is None


@pytest.mark.asyncio
async def test_standalone_dao_session_rolls_back_on_error(monkeypatch):
    session = RecordingSession()
    monkeypatch.setattr("app.dao.base.async_session", SessionFactory(session))

    dao = BaseDAO(DummyModel)

    with pytest.raises(RuntimeError):
        async with dao.session():
            raise RuntimeError("boom")

    assert session.committed is False
    assert session.rolled_back is True
    assert _session_ctx.get() is None


@pytest.mark.asyncio
async def test_delete_uses_current_session_without_nested_lookup(monkeypatch):
    session = RecordingSession()
    monkeypatch.setattr("app.dao.base.async_session", SessionFactory(session))

    dao = BaseDAO(DummyModel)

    deleted = await dao.delete(id="row-1")

    assert deleted is session.object_to_get
    assert session.get_calls == [(DummyModel, "row-1")]
    assert session.execute_calls == 0
    assert session.deleted == [session.object_to_get]
    assert session.flushed is True
    assert session.committed is True


def test_orm_session_injects_tenant_filter_for_direct_queries():
    """Direct ORM access cannot bypass tenant isolation by omitting WHERE."""
    engine = create_engine("sqlite://")
    TenantScopedRecord.__table__.create(engine)
    tenant_a = str(uuid.uuid4())
    tenant_b = str(uuid.uuid4())

    with Session(engine) as session:
        session.add_all(
            [
                TenantScopedRecord(id="a", tenant_id=tenant_a),
                TenantScopedRecord(id="b", tenant_id=tenant_b),
            ]
        )
        session.commit()

        with tenant_context(tenant_a):
            records = session.scalars(select(TenantScopedRecord).order_by(TenantScopedRecord.id)).all()

    assert [record.id for record in records] == ["a"]
