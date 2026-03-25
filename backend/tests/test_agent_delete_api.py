import json
import uuid
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from sqlalchemy.exc import IntegrityError

from app.api import agents as agents_api
from app.models.agent import Agent
from app.models.user import User


class _NestedTransaction:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


class DummyResult:
    def __init__(self, values=None):
        self._values = list(values or [])

    def scalar_one_or_none(self):
        return self._values[0] if self._values else None

    def scalars(self):
        return self

    def all(self):
        return list(self._values)


class RecordingDB:
    def __init__(self, *, required_cleanup: list[str], responses=None):
        self.required_cleanup = required_cleanup
        self.responses = list(responses or [])
        self.executed_sql: list[str] = []
        self.deleted: list[object] = []
        self.committed = False

    def begin_nested(self):
        return _NestedTransaction()

    async def execute(self, statement, params=None):
        sql = getattr(statement, "text", str(statement))
        self.executed_sql.append(sql)
        if self.responses:
            return self.responses.pop(0)
        return DummyResult()

    async def delete(self, obj):
        self.deleted.append(obj)
        missing_cleanup = [sql for sql in self.required_cleanup if sql not in self.executed_sql]
        if missing_cleanup:
            raise IntegrityError(
                statement="DELETE FROM agents WHERE id = :aid",
                params={"aid": getattr(obj, "id", None)},
                orig=Exception(f"missing cleanup: {missing_cleanup}"),
            )

    async def commit(self):
        self.committed = True


class TaskCleanupDB(RecordingDB):
    def __init__(self):
        super().__init__(
            required_cleanup=[
                "DELETE FROM task_logs WHERE task_id IN (SELECT id FROM tasks WHERE agent_id = :aid)",
                "DELETE FROM tasks WHERE agent_id = :aid",
                "DELETE FROM published_pages WHERE agent_id = :aid",
                "DELETE FROM notifications WHERE agent_id = :aid",
            ]
        )
        self.task_rows_remaining = 1
        self.task_logs_remaining = 1

    async def execute(self, statement, params=None):
        sql = getattr(statement, "text", str(statement))
        self.executed_sql.append(sql)

        if sql == "DELETE FROM task_logs WHERE task_id IN (SELECT id FROM tasks WHERE agent_id = :aid)":
            self.task_logs_remaining = 0
        elif sql == "DELETE FROM tasks WHERE agent_id = :aid":
            if self.task_logs_remaining:
                raise IntegrityError(
                    statement=sql,
                    params=params,
                    orig=Exception("task_logs.task_id foreign key still blocks task deletion"),
                )
            self.task_rows_remaining = 0

        if self.responses:
            return self.responses.pop(0)
        return DummyResult()

    async def delete(self, obj):
        if self.task_rows_remaining:
            raise IntegrityError(
                statement="DELETE FROM agents WHERE id = :aid",
                params={"aid": getattr(obj, "id", None)},
                orig=Exception("tasks.agent_id foreign key still blocks agent deletion"),
            )

        await super().delete(obj)


def make_user(**overrides):
    values = {
        "id": uuid.uuid4(),
        "username": "alice",
        "email": "alice@example.com",
        "password_hash": "hashed",
        "display_name": "Alice",
        "role": "member",
        "tenant_id": uuid.uuid4(),
        "is_active": True,
    }
    values.update(overrides)
    return User(**values)


def make_agent(creator_id: uuid.UUID, **overrides):
    values = {
        "id": uuid.uuid4(),
        "name": "Ops Bot",
        "role_description": "assistant",
        "creator_id": creator_id,
        "status": "idle",
        "agent_type": "native",
    }
    values.update(overrides)
    return Agent(**values)


@pytest.mark.asyncio
async def test_delete_agent_cleans_remaining_foreign_key_rows(monkeypatch):
    creator = make_user()
    agent = make_agent(creator.id)
    db = TaskCleanupDB()

    async def fake_check_agent_access(_db, _current_user, _agent_id):
        return agent, "manage"

    class FakeAgentManager:
        async def remove_container(self, _agent):
            return None

        async def archive_agent_files(self, _agent_id):
            return None

    monkeypatch.setattr(agents_api, "check_agent_access", fake_check_agent_access)
    monkeypatch.setattr(agents_api, "is_agent_creator", lambda _user, _agent: True)
    monkeypatch.setattr("app.services.agent_manager.agent_manager", FakeAgentManager())

    await agents_api.delete_agent(
        agent_id=agent.id,
        current_user=creator,
        db=db,
    )

    assert db.deleted == [agent]
    assert db.committed is True
    assert db.executed_sql.index("DELETE FROM task_logs WHERE task_id IN (SELECT id FROM tasks WHERE agent_id = :aid)") < (
        db.executed_sql.index("DELETE FROM tasks WHERE agent_id = :aid")
    )


@pytest.mark.asyncio
async def test_archive_agent_task_history_writes_json_snapshot(tmp_path):
    agent_id = uuid.uuid4()
    task_id = uuid.uuid4()
    created_at = datetime.now(UTC)

    task = SimpleNamespace(
        id=task_id,
        title="Review PR",
        description="Check lore trailers",
        type="todo",
        status="done",
        priority="high",
        assignee="self",
        created_by=uuid.uuid4(),
        due_date=None,
        supervision_target_user_id=None,
        supervision_target_name=None,
        supervision_channel=None,
        remind_schedule=None,
        created_at=created_at,
        updated_at=created_at,
        completed_at=created_at,
    )
    log = SimpleNamespace(
        id=uuid.uuid4(),
        content="Completed review and left comments",
        created_at=created_at,
    )

    db = RecordingDB(
        required_cleanup=[],
        responses=[
            DummyResult([task]),
            DummyResult([log]),
        ],
    )

    archive_dir = tmp_path / "_archived" / f"{agent_id}_20260325_120000"
    archive_path = await agents_api._archive_agent_task_history(db, agent_id, archive_dir)

    assert archive_path == archive_dir / "task_history.json"
    payload = json.loads(archive_path.read_text(encoding="utf-8"))
    assert payload["agent_id"] == str(agent_id)
    assert payload["tasks"][0]["id"] == str(task_id)
    assert payload["tasks"][0]["logs"][0]["content"] == "Completed review and left comments"
