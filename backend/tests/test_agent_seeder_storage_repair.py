from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
import uuid

import pytest

from app.services import agent_seeder


class _Result:
    def __init__(self, *, scalar=None, scalars=None):
        self._scalar = scalar
        self._scalars = list(scalars or [])

    def scalar_one_or_none(self):
        return self._scalar

    def scalars(self):
        return SimpleNamespace(all=lambda: self._scalars)


class _SessionContext:
    def __init__(self, session):
        self.session = session

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, exc_type, exc, traceback):
        return False


def _agent(
    name: str = "Morty",
    *,
    status: str = "idle",
    deleted_at=None,
) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        name=name,
        status=status,
        deleted_at=deleted_at,
    )


def _skill(folder_name: str = "skill-creator", *, is_default: bool = True) -> SimpleNamespace:
    return SimpleNamespace(
        folder_name=folder_name,
        is_default=is_default,
        files=[SimpleNamespace(path="SKILL.md", content="# Skill\n")],
    )


@pytest.mark.asyncio
async def test_repair_default_agent_storage_restores_missing_root_and_skills(monkeypatch):
    agent = _agent()
    prefix = str(agent.id)
    storage = SimpleNamespace(
        exists=AsyncMock(return_value=False),
        is_dir=AsyncMock(return_value=False),
        is_file=AsyncMock(return_value=False),
        write_text=AsyncMock(),
    )
    initialize = AsyncMock()
    store_bytes = AsyncMock()
    monkeypatch.setattr(agent_seeder, "get_storage_backend", lambda: storage)
    monkeypatch.setattr(agent_seeder.agent_manager, "initialize_agent_files", initialize)
    monkeypatch.setattr(agent_seeder, "store_agent_bytes", store_bytes)

    repaired = await agent_seeder._repair_default_agent_storage(
        db=SimpleNamespace(),
        agent=agent,
        soul_content="# Morty\n",
        skill_folders=["skill-creator"],
        all_skills={"skill-creator": _skill()},
    )

    assert repaired is True
    initialize.assert_awaited_once()
    storage.write_text.assert_awaited_once_with(f"{prefix}/skills/.gitkeep", "", encoding="utf-8")
    written_paths = [call.args[1] for call in store_bytes.await_args_list]
    assert written_paths == ["soul.md", "skills/skill-creator/SKILL.md"]


@pytest.mark.asyncio
async def test_repair_default_agent_storage_only_restores_missing_skills(monkeypatch):
    agent = _agent()
    prefix = str(agent.id)

    async def exists(key: str) -> bool:
        return key == prefix

    storage = SimpleNamespace(
        exists=AsyncMock(side_effect=exists),
        is_dir=AsyncMock(side_effect=lambda key: key == prefix),
        is_file=AsyncMock(return_value=False),
        write_text=AsyncMock(),
    )
    initialize = AsyncMock()
    store_bytes = AsyncMock()
    monkeypatch.setattr(agent_seeder, "get_storage_backend", lambda: storage)
    monkeypatch.setattr(agent_seeder.agent_manager, "initialize_agent_files", initialize)
    monkeypatch.setattr(agent_seeder, "store_agent_bytes", store_bytes)

    repaired = await agent_seeder._repair_default_agent_storage(
        db=SimpleNamespace(),
        agent=agent,
        soul_content="# Morty\n",
        skill_folders=["skill-creator"],
        all_skills={"skill-creator": _skill()},
    )

    assert repaired is True
    initialize.assert_not_awaited()
    storage.write_text.assert_awaited_once_with(f"{prefix}/skills/.gitkeep", "", encoding="utf-8")
    assert [call.args[1] for call in store_bytes.await_args_list] == ["skills/skill-creator/SKILL.md"]


@pytest.mark.asyncio
async def test_repair_default_agent_storage_leaves_healthy_storage_untouched(monkeypatch):
    agent = _agent()
    storage = SimpleNamespace(
        exists=AsyncMock(return_value=True),
        is_dir=AsyncMock(return_value=True),
        is_file=AsyncMock(return_value=True),
        write_text=AsyncMock(),
    )
    initialize = AsyncMock()
    store_bytes = AsyncMock()
    monkeypatch.setattr(agent_seeder, "get_storage_backend", lambda: storage)
    monkeypatch.setattr(agent_seeder.agent_manager, "initialize_agent_files", initialize)
    monkeypatch.setattr(agent_seeder, "store_agent_bytes", store_bytes)

    repaired = await agent_seeder._repair_default_agent_storage(
        db=SimpleNamespace(),
        agent=agent,
        soul_content="# Morty\n",
        skill_folders=["skill-creator"],
        all_skills={"skill-creator": _skill()},
    )

    assert repaired is False
    initialize.assert_not_awaited()
    storage.write_text.assert_not_awaited()
    store_bytes.assert_not_awaited()


@pytest.mark.asyncio
async def test_seed_existing_default_agents_still_runs_storage_repair(monkeypatch):
    admin = SimpleNamespace(id=uuid.uuid4(), tenant_id=uuid.uuid4())
    morty = _agent("Morty")
    meeseeks = _agent("Meeseeks")
    added = []

    async def execute(statement):
        sql = str(statement)
        if "FROM users" in sql:
            return _Result(scalar=admin)
        if "pg_advisory_xact_lock" in sql:
            return _Result()
        if "FROM tenant_settings" in sql:
            return _Result(scalar=None)
        if "FROM agents" in sql and "agents.id IN" in sql:
            return _Result(scalars=[])
        if "FROM agents" in sql:
            return _Result(scalars=[morty, meeseeks])
        return _Result(scalars=[])

    session = SimpleNamespace(
        execute=AsyncMock(side_effect=execute),
        flush=AsyncMock(),
        commit=AsyncMock(),
        add=added.append,
    )
    repair = AsyncMock(return_value=False)
    storage = _empty_storage()
    monkeypatch.setattr(agent_seeder, "async_session", lambda: _SessionContext(session))
    monkeypatch.setattr(agent_seeder, "_repair_default_agent_storage", repair)
    monkeypatch.setattr(agent_seeder, "get_storage_backend", lambda: storage)

    await agent_seeder.seed_default_agents()

    assert repair.await_count == 2
    assert [call.args[1].name for call in repair.await_args_list] == ["Morty", "Meeseeks"]
    assert any(value.__class__.__name__ == "TenantSetting" for value in added)
    session.commit.assert_awaited_once()
    assert storage.write_text.await_count >= 1


def _empty_storage(*, marker: str = "") -> SimpleNamespace:
    return SimpleNamespace(
        exists=AsyncMock(return_value=bool(marker)),
        read_text=AsyncMock(return_value=marker),
        write_text=AsyncMock(),
    )


@pytest.mark.asyncio
async def test_append_default_agent_marker_preserves_other_seed_entries(monkeypatch):
    content = "seeded\nokr_agent=existing\n"

    async def read_text(*_args, **_kwargs):
        return storage.content

    async def write_text(_key, value, **_kwargs):
        storage.content = value

    storage = SimpleNamespace(
        content=content,
        exists=AsyncMock(return_value=True),
        read_text=AsyncMock(side_effect=read_text),
        write_text=AsyncMock(side_effect=write_text),
    )
    monkeypatch.setattr(agent_seeder, "get_storage_backend", lambda: storage)
    morty_id = uuid.uuid4()
    meeseeks_id = uuid.uuid4()

    await agent_seeder._append_default_agent_seed_marker(
        {"morty": morty_id, "meeseeks": meeseeks_id}
    )

    assert "okr_agent=existing\n" in storage.content
    assert f"morty={morty_id}\n" in storage.content
    assert f"meeseeks={meeseeks_id}\n" in storage.content


@pytest.mark.asyncio
async def test_seed_deleted_default_agents_backfills_without_recreating(monkeypatch):
    admin = SimpleNamespace(id=uuid.uuid4(), tenant_id=uuid.uuid4())
    deleted_at = datetime.now(timezone.utc)
    deleted_agents = [
        _agent("Morty", status="stopped", deleted_at=deleted_at),
        _agent("Meeseeks", status="stopped", deleted_at=deleted_at),
    ]
    added = []

    async def execute(statement):
        sql = str(statement)
        if "FROM users" in sql:
            return _Result(scalar=admin)
        if "pg_advisory_xact_lock" in sql:
            return _Result()
        if "FROM tenant_settings" in sql:
            return _Result(scalar=None)
        if "FROM agents" in sql:
            if "agents.status !=" in sql:
                return _Result(scalars=[])
            return _Result(scalars=deleted_agents)
        return _Result(scalars=[])

    session = SimpleNamespace(
        execute=AsyncMock(side_effect=execute),
        flush=AsyncMock(),
        commit=AsyncMock(),
        add=added.append,
    )
    repair = AsyncMock(return_value=False)
    storage = _empty_storage()
    monkeypatch.setattr(agent_seeder, "async_session", lambda: _SessionContext(session))
    monkeypatch.setattr(agent_seeder, "_repair_default_agent_storage", repair)
    monkeypatch.setattr(agent_seeder, "get_storage_backend", lambda: storage)

    await agent_seeder.seed_default_agents()

    assert not any(isinstance(value, agent_seeder.Agent) for value in added)
    assert any(value.__class__.__name__ == "TenantSetting" for value in added)
    repair.assert_not_awaited()


@pytest.mark.asyncio
async def test_seed_legacy_marker_backfills_renamed_agents_without_recreating(monkeypatch):
    admin = SimpleNamespace(id=uuid.uuid4(), tenant_id=uuid.uuid4())
    renamed_morty = _agent("Researcher")
    renamed_meeseeks = _agent("Executor")
    marker = (
        "seeded\n"
        f"morty={renamed_morty.id}\n"
        f"meeseeks={renamed_meeseeks.id}\n"
    )
    added = []

    async def execute(statement):
        sql = str(statement)
        if "FROM users" in sql:
            return _Result(scalar=admin)
        if "pg_advisory_xact_lock" in sql:
            return _Result()
        if "FROM tenant_settings" in sql:
            return _Result(scalar=None)
        if "FROM agents" in sql and "agents.id IN" in sql:
            return _Result(scalars=[renamed_morty, renamed_meeseeks])
        if "FROM agents" in sql:
            return _Result(scalars=[])
        return _Result(scalars=[])

    session = SimpleNamespace(
        execute=AsyncMock(side_effect=execute),
        flush=AsyncMock(),
        commit=AsyncMock(),
        add=added.append,
    )
    repair = AsyncMock(return_value=False)
    storage = _empty_storage(marker=marker)
    monkeypatch.setattr(agent_seeder, "async_session", lambda: _SessionContext(session))
    monkeypatch.setattr(agent_seeder, "_repair_default_agent_storage", repair)
    monkeypatch.setattr(agent_seeder, "get_storage_backend", lambda: storage)

    await agent_seeder.seed_default_agents()

    assert not any(isinstance(value, agent_seeder.Agent) for value in added)
    assert any(value.__class__.__name__ == "TenantSetting" for value in added)
    assert [call.args[1].id for call in repair.await_args_list] == [
        renamed_morty.id,
        renamed_meeseeks.id,
    ]


@pytest.mark.asyncio
async def test_seed_database_marker_skips_deleted_and_repairs_stopped_survivor(monkeypatch):
    admin = SimpleNamespace(id=uuid.uuid4(), tenant_id=uuid.uuid4())
    deleted_morty = _agent(
        "Morty",
        status="stopped",
        deleted_at=datetime.now(timezone.utc),
    )
    stopped_meeseeks = _agent("Meeseeks", status="stopped")
    setting = SimpleNamespace(
        value={
            "initialized": True,
            "agents": {
                "morty": str(deleted_morty.id),
                "meeseeks": str(stopped_meeseeks.id),
            },
            "source": "created",
        }
    )
    added = []

    async def execute(statement):
        sql = str(statement)
        if "FROM users" in sql:
            return _Result(scalar=admin)
        if "pg_advisory_xact_lock" in sql:
            return _Result()
        if "FROM tenant_settings" in sql:
            return _Result(scalar=setting)
        if "FROM agents" in sql:
            return _Result(scalars=[deleted_morty, stopped_meeseeks])
        return _Result(scalars=[])

    session = SimpleNamespace(
        execute=AsyncMock(side_effect=execute),
        flush=AsyncMock(),
        commit=AsyncMock(),
        add=added.append,
    )
    repair = AsyncMock(return_value=False)
    storage = _empty_storage()
    monkeypatch.setattr(agent_seeder, "async_session", lambda: _SessionContext(session))
    monkeypatch.setattr(agent_seeder, "_repair_default_agent_storage", repair)
    monkeypatch.setattr(agent_seeder, "get_storage_backend", lambda: storage)

    await agent_seeder.seed_default_agents()

    assert not any(isinstance(value, agent_seeder.Agent) for value in added)
    repair.assert_awaited_once()
    assert repair.await_args.args[1].id == stopped_meeseeks.id
    storage.write_text.assert_not_awaited()


@pytest.mark.asyncio
async def test_seed_malformed_database_marker_never_recreates(monkeypatch):
    admin = SimpleNamespace(id=uuid.uuid4(), tenant_id=uuid.uuid4())
    setting = SimpleNamespace(value={"unexpected": "value"})
    added = []

    async def execute(statement):
        sql = str(statement)
        if "FROM users" in sql:
            return _Result(scalar=admin)
        if "pg_advisory_xact_lock" in sql:
            return _Result()
        if "FROM tenant_settings" in sql:
            return _Result(scalar=setting)
        return _Result(scalars=[])

    session = SimpleNamespace(
        execute=AsyncMock(side_effect=execute),
        flush=AsyncMock(),
        commit=AsyncMock(),
        add=added.append,
    )
    repair = AsyncMock(return_value=False)
    monkeypatch.setattr(agent_seeder, "async_session", lambda: _SessionContext(session))
    monkeypatch.setattr(agent_seeder, "_repair_default_agent_storage", repair)

    await agent_seeder.seed_default_agents()

    assert not any(isinstance(value, agent_seeder.Agent) for value in added)
    repair.assert_not_awaited()
    session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_seed_fresh_tenant_creates_agents_and_database_marker(monkeypatch):
    admin = SimpleNamespace(id=uuid.uuid4(), tenant_id=uuid.uuid4())
    added = []

    async def execute(statement):
        sql = str(statement)
        if "FROM users" in sql:
            return _Result(scalar=admin)
        if "pg_advisory_xact_lock" in sql:
            return _Result()
        if "FROM tenant_settings" in sql:
            return _Result(scalar=None)
        return _Result(scalars=[])

    async def flush():
        for value in added:
            if isinstance(value, agent_seeder.Agent) and value.id is None:
                value.id = uuid.uuid4()

    session = SimpleNamespace(
        execute=AsyncMock(side_effect=execute),
        flush=AsyncMock(side_effect=flush),
        commit=AsyncMock(),
        add=added.append,
    )
    repair = AsyncMock(return_value=False)
    storage = _empty_storage()
    monkeypatch.setattr(agent_seeder, "async_session", lambda: _SessionContext(session))
    monkeypatch.setattr(agent_seeder, "_repair_default_agent_storage", repair)
    monkeypatch.setattr(agent_seeder, "get_storage_backend", lambda: storage)

    await agent_seeder.seed_default_agents()

    created_agents = [value for value in added if isinstance(value, agent_seeder.Agent)]
    settings = [value for value in added if value.__class__.__name__ == "TenantSetting"]
    assert [agent.name for agent in created_agents] == ["Morty", "Meeseeks"]
    assert len(settings) == 1
    assert settings[0].value["initialized"] is True
    assert settings[0].value["source"] == "created"
    assert all(settings[0].value["agents"].values())
    assert repair.await_count == 2
    session.commit.assert_awaited_once()
    executed_sql = "\n".join(str(call.args[0]) for call in session.execute.await_args_list)
    assert "pg_advisory_xact_lock" in executed_sql
