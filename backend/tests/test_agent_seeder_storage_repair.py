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


def _agent(name: str = "Morty") -> SimpleNamespace:
    return SimpleNamespace(id=uuid.uuid4(), name=name)


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
    session = SimpleNamespace(
        execute=AsyncMock(
            side_effect=[
                _Result(scalar=admin),
                _Result(scalars=[morty, meeseeks]),
                _Result(scalars=[]),
                _Result(scalars=[]),
                _Result(scalar=None),
                _Result(scalar=None),
            ]
        ),
        flush=AsyncMock(),
        commit=AsyncMock(),
        add=lambda value: None,
    )
    repair = AsyncMock(return_value=False)
    storage = SimpleNamespace(write_text=AsyncMock())
    monkeypatch.setattr(agent_seeder, "async_session", lambda: _SessionContext(session))
    monkeypatch.setattr(agent_seeder, "_repair_default_agent_storage", repair)
    monkeypatch.setattr(agent_seeder, "get_storage_backend", lambda: storage)

    await agent_seeder.seed_default_agents()

    assert repair.await_count == 2
    assert [call.args[1].name for call in repair.await_args_list] == ["Morty", "Meeseeks"]
    session.commit.assert_awaited_once()
    storage.write_text.assert_awaited_once()
