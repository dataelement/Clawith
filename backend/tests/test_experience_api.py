"""Regression tests for the human-facing Experience Library API."""

from collections import deque
from datetime import UTC, datetime
from types import SimpleNamespace
import uuid

import pytest
from fastapi import HTTPException
from sqlalchemy.dialects import postgresql

from app.api import experience as experience_api
from app.models.experience import ExperienceEntry


class DummyResult:
    def __init__(self, values=None, scalar_value=None):
        self._values = list(values or [])
        self._scalar_value = scalar_value

    def scalar_one_or_none(self):
        if self._values:
            return self._values[0]
        return self._scalar_value

    def scalar(self):
        return self._scalar_value

    def scalars(self):
        return self

    def all(self):
        return list(self._values)


class RecordingDB:
    def __init__(self, *responses):
        self.responses = deque(responses)
        self.statements = []
        self.added = []
        self.deleted = []
        self.committed = False

    async def execute(self, statement):
        self.statements.append(statement)
        if not self.responses:
            raise AssertionError("unexpected execute() call")
        return self.responses.popleft()

    def add(self, value):
        self.added.append(value)

    async def delete(self, value):
        self.deleted.append(value)

    async def commit(self):
        self.committed = True

    async def refresh(self, value):
        # Emulate the server-side/default values populated by PostgreSQL on insert.
        if getattr(value, "id", None) is None:
            value.id = uuid.uuid4()
        if getattr(value, "created_at", None) is None:
            value.created_at = datetime(2026, 7, 15, 12, 0, tzinfo=UTC)


class QueryAwareDB(RecordingDB):
    """Return org-membership rows separately from the actual entry query.

    This keeps the regression test useful against the old visibility-scoped
    implementation while asserting that the new implementation drops that query.
    """

    def __init__(self, entries):
        super().__init__()
        self.entries = entries

    async def execute(self, statement):
        self.statements.append(statement)
        sql = _sql(statement)
        if "FROM org_members" in sql:
            return DummyResult([])
        return DummyResult(self.entries)


class AsyncSessionFactory:
    def __init__(self, db):
        self.db = db

    def __call__(self):
        return self

    async def __aenter__(self):
        return self.db

    async def __aexit__(self, exc_type, exc, tb):
        return False


def _sql(statement) -> str:
    return str(statement.compile(dialect=postgresql.dialect()))


def _user(*, role="member", tenant_id=None, user_id=None):
    return SimpleNamespace(
        id=user_id or uuid.uuid4(),
        tenant_id=tenant_id or uuid.uuid4(),
        role=role,
        display_name="Current User",
    )


def _entry(
    tenant_id,
    *,
    status="published",
    created_by=None,
    draft_of_id=None,
    title="Regression entry",
    body="Body",
    applicability="Use this in regression tests",
    visibility_scope="company",
    visibility_scope_id=None,
    origin_agent_id=None,
    tags=None,
):
    now = datetime(2026, 7, 15, 12, 0, tzinfo=UTC)
    return ExperienceEntry(
        id=uuid.uuid4(),
        draft_of_id=draft_of_id,
        tenant_id=tenant_id,
        title=title,
        body=body,
        applicability=applicability,
        status=status,
        tags=list(tags or []),
        visibility_scope=visibility_scope,
        visibility_scope_id=visibility_scope_id,
        origin="chat",
        origin_session_id=None,
        origin_agent_id=origin_agent_id,
        created_by=created_by or uuid.uuid4(),
        reviewed_by=None,
        last_reviewed_at=now,
        retired_at=now if status == "retired" else None,
        created_at=now,
        updated_at=now,
    )


async def _identity_serialize(_db, entries):
    return entries


@pytest.mark.asyncio
async def test_team_lists_every_same_tenant_published_entry_regardless_of_legacy_visibility(monkeypatch):
    current_user = _user()
    entry = _entry(
        current_user.tenant_id,
        visibility_scope="user",
        visibility_scope_id=uuid.uuid4(),
    )
    db = QueryAwareDB([entry])
    monkeypatch.setattr(experience_api, "async_session", AsyncSessionFactory(db))
    monkeypatch.setattr(experience_api, "_serialize_entries", _identity_serialize)

    entries = await experience_api.list_entries(
        view="team",
        status=None,
        tag=None,
        q=None,
        limit=50,
        offset=0,
        current_user=current_user,
    )

    assert entries == [entry]
    entry_queries = [statement for statement in db.statements if "FROM experience_entries" in _sql(statement)]
    assert len(entry_queries) == 1
    sql = _sql(entry_queries[0])
    assert "experience_entries.status =" in sql
    where_sql = sql.split("WHERE", 1)[1].split("ORDER BY", 1)[0]
    assert "experience_entries.visibility_scope" not in where_sql
    assert not any("FROM org_members" in _sql(statement) for statement in db.statements)


@pytest.mark.asyncio
async def test_non_admin_cannot_enumerate_all_entries(monkeypatch):
    current_user = _user()
    db = RecordingDB()
    monkeypatch.setattr(experience_api, "async_session", AsyncSessionFactory(db))

    with pytest.raises(HTTPException) as error:
        await experience_api.list_entries(
            view="all",
            status=None,
            tag=None,
            q=None,
            limit=50,
            offset=0,
            current_user=current_user,
        )

    assert error.value.status_code == 403
    assert db.statements == []


@pytest.mark.asyncio
async def test_tag_filter_is_in_sql_before_pagination(monkeypatch):
    current_user = _user(role="org_admin")
    entry = _entry(current_user.tenant_id, tags=["target"])
    db = RecordingDB(DummyResult([entry]))
    monkeypatch.setattr(experience_api, "async_session", AsyncSessionFactory(db))
    monkeypatch.setattr(experience_api, "_serialize_entries", _identity_serialize)

    entries = await experience_api.list_entries(
        view="team",
        status=None,
        tag="target",
        q=None,
        limit=1,
        offset=0,
        current_user=current_user,
    )

    assert entries == [entry]
    sql = _sql(db.statements[0])
    assert "CAST(experience_entries.tags AS JSONB) @>" in sql
    assert sql.index("CAST(experience_entries.tags AS JSONB) @>") < sql.index("LIMIT")
    assert "experience_entries.id DESC" in sql


@pytest.mark.asyncio
async def test_library_stats_uses_the_same_tenant_wide_published_scope_for_members(monkeypatch):
    current_user = _user()
    db = RecordingDB(
        DummyResult(scalar_value=2),
        DummyResult(scalar_value=1),
        DummyResult(scalar_value=0),
        DummyResult([]),
    )
    monkeypatch.setattr(experience_api, "async_session", AsyncSessionFactory(db))

    stats = await experience_api.library_stats(current_user=current_user)

    assert stats.total == 2
    assert stats.today == 1
    assert stats.cited == 0
    assert stats.top_contributors == []
    assert not any("FROM org_members" in _sql(statement) for statement in db.statements)
    for statement in db.statements:
        sql = _sql(statement)
        if "experience_entries" in sql:
            where_sql = sql.split("WHERE", 1)[1]
            assert "experience_entries.visibility_scope" not in where_sql


@pytest.mark.asyncio
async def test_member_can_read_any_same_tenant_published_entry(monkeypatch):
    current_user = _user()
    entry = _entry(
        current_user.tenant_id,
        visibility_scope="user",
        visibility_scope_id=uuid.uuid4(),
    )
    db = RecordingDB(DummyResult([entry]))
    monkeypatch.setattr(experience_api, "async_session", AsyncSessionFactory(db))
    monkeypatch.setattr(experience_api, "_serialize_entries", _identity_serialize)

    result = await experience_api.get_entry(entry.id, current_user=current_user)

    assert result is entry
    assert result.can_manage is False


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["draft", "retired"])
async def test_member_cannot_read_someone_elses_unpublished_entry(monkeypatch, status):
    current_user = _user()
    entry = _entry(current_user.tenant_id, status=status)
    db = RecordingDB(DummyResult([entry]))
    monkeypatch.setattr(experience_api, "async_session", AsyncSessionFactory(db))
    monkeypatch.setattr(experience_api, "_serialize_entries", _identity_serialize)

    with pytest.raises(HTTPException) as error:
        await experience_api.get_entry(entry.id, current_user=current_user)

    assert error.value.status_code == 404


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["member", "org_admin", "platform_admin"])
async def test_creator_and_admins_can_read_unpublished_entries(monkeypatch, role):
    current_user = _user(role=role)
    creator_id = current_user.id if role == "member" else uuid.uuid4()
    entry = _entry(current_user.tenant_id, status="retired", created_by=creator_id)
    db = RecordingDB(DummyResult([entry]))
    monkeypatch.setattr(experience_api, "async_session", AsyncSessionFactory(db))
    monkeypatch.setattr(experience_api, "_serialize_entries", _identity_serialize)

    result = await experience_api.get_entry(entry.id, current_user=current_user)

    assert result is entry
    assert result.can_manage is True


@pytest.mark.asyncio
async def test_existing_source_manager_can_read_unpublished_entry(monkeypatch):
    current_user = _user()
    entry = _entry(
        current_user.tenant_id,
        status="draft",
        created_by=uuid.uuid4(),
        origin_agent_id=uuid.uuid4(),
    )
    db = RecordingDB(DummyResult([entry]))
    monkeypatch.setattr(experience_api, "async_session", AsyncSessionFactory(db))
    monkeypatch.setattr(experience_api, "_serialize_entries", _identity_serialize)

    async def manager_is_current_user(_db, _agent_id):
        return current_user.id

    monkeypatch.setattr(experience_api, "_agent_creator_id", manager_is_current_user)

    result = await experience_api.get_entry(entry.id, current_user=current_user)

    assert result is entry
    assert result.can_manage is True


@pytest.mark.asyncio
async def test_create_ignores_legacy_private_visibility_input(monkeypatch):
    current_user = _user()
    db = RecordingDB(DummyResult([]))
    monkeypatch.setattr(experience_api, "async_session", AsyncSessionFactory(db))
    payload = experience_api.EntryCreate(
        title="New entry",
        body="Body",
        applicability="Use when testing",
        visibility_scope="user",
        visibility_scope_id=uuid.uuid4(),
    )

    await experience_api.create_entry(payload, current_user=current_user)

    created = db.added[0]
    assert created.visibility_scope == "company"
    assert created.visibility_scope_id is None


@pytest.mark.asyncio
async def test_editing_a_published_entry_creates_an_independent_revision_draft(monkeypatch):
    current_user = _user()
    source = _entry(current_user.tenant_id, created_by=current_user.id)
    db = RecordingDB(DummyResult([source]))
    monkeypatch.setattr(experience_api, "async_session", AsyncSessionFactory(db))

    result = await experience_api.create_revision_draft(
        source.id,
        experience_api.EntryUpdate(title="Edited title", body="Edited body"),
        current_user=current_user,
    )

    revision = db.added[0]
    assert result.id == revision.id
    assert revision.id != source.id
    assert revision.draft_of_id == source.id
    assert revision.status == "draft"
    assert revision.title == "Edited title"
    assert revision.body == "Edited body"
    assert source.status == "published"
    assert source.title == "Regression entry"


@pytest.mark.asyncio
async def test_deleting_a_revision_draft_does_not_delete_its_published_source(monkeypatch):
    current_user = _user()
    source = _entry(current_user.tenant_id, created_by=current_user.id)
    revision = _entry(
        current_user.tenant_id,
        status="draft",
        created_by=current_user.id,
        draft_of_id=source.id,
    )
    db = RecordingDB(DummyResult([revision]))
    monkeypatch.setattr(experience_api, "async_session", AsyncSessionFactory(db))

    result = await experience_api.delete_entry(revision.id, current_user=current_user)

    assert result == {"deleted": True}
    assert db.deleted == [revision]
    assert source not in db.deleted
    assert source.status == "published"


@pytest.mark.asyncio
async def test_publishing_a_revision_updates_the_source_id_and_removes_the_draft(monkeypatch):
    current_user = _user()
    source = _entry(current_user.tenant_id, created_by=current_user.id)
    revision = _entry(
        current_user.tenant_id,
        status="draft",
        created_by=current_user.id,
        draft_of_id=source.id,
        title="Edited title",
        body="Edited body",
        applicability="Edited applicability",
        tags=["edited"],
    )
    db = RecordingDB(DummyResult([revision]), DummyResult([source]))
    monkeypatch.setattr(experience_api, "async_session", AsyncSessionFactory(db))

    result = await experience_api.publish_entry(revision.id, current_user=current_user)

    assert result.id == source.id
    assert source.title == "Edited title"
    assert source.body == "Edited body"
    assert source.applicability == "Edited applicability"
    assert source.tags == ["edited"]
    assert source.status == "published"
    assert source.retired_at is None
    assert db.deleted == [revision]


@pytest.mark.asyncio
async def test_update_cannot_make_a_published_entry_private(monkeypatch):
    current_user = _user()
    entry = _entry(current_user.tenant_id, created_by=current_user.id)
    db = RecordingDB(DummyResult([entry]))
    monkeypatch.setattr(experience_api, "async_session", AsyncSessionFactory(db))
    payload = experience_api.EntryUpdate(
        visibility_scope="user",
        visibility_scope_id=uuid.uuid4(),
    )

    await experience_api.update_entry(entry.id, payload, current_user=current_user)

    assert entry.visibility_scope == "company"
    assert entry.visibility_scope_id is None


@pytest.mark.asyncio
async def test_publish_normalizes_legacy_visibility_to_company(monkeypatch):
    current_user = _user()
    entry = _entry(
        current_user.tenant_id,
        status="draft",
        created_by=current_user.id,
        visibility_scope="user",
        visibility_scope_id=uuid.uuid4(),
    )
    db = RecordingDB(DummyResult([entry]))
    monkeypatch.setattr(experience_api, "async_session", AsyncSessionFactory(db))

    await experience_api.publish_entry(entry.id, current_user=current_user)

    assert entry.status == "published"
    assert entry.visibility_scope == "company"
    assert entry.visibility_scope_id is None
