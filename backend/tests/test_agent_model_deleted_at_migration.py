"""Deployment contract for Agent and LLM model logical deletion schema."""

from __future__ import annotations

import importlib.util
from pathlib import Path


MIGRATION_PATH = (
    Path(__file__).resolve().parents[1]
    / "alembic"
    / "versions"
    / "202607221500_add_agent_model_deleted_at.py"
)


def _load_migration():
    spec = importlib.util.spec_from_file_location(
        "agent_model_deleted_at_migration",
        MIGRATION_PATH,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeInspector:
    def __init__(
        self,
        *,
        columns: dict[str, set[str]],
        indexes: dict[str, set[str]],
    ):
        self.columns = columns
        self.indexes = indexes

    def get_columns(self, table_name):
        return [{"name": name} for name in self.columns.get(table_name, set())]

    def get_indexes(self, table_name):
        return [{"name": name} for name in self.indexes.get(table_name, set())]


def _install_inspector(monkeypatch, migration, *, columns, indexes):
    inspector = FakeInspector(columns=columns, indexes=indexes)
    monkeypatch.setattr(migration, "_inspector", lambda: inspector)


def test_revision_follows_experience_revision_head() -> None:
    migration = _load_migration()

    assert migration.revision == "add_agent_model_deleted_at"
    assert migration.down_revision == "add_experience_revision_drafts"


def test_upgrade_adds_all_missing_columns_and_indexes(monkeypatch) -> None:
    migration = _load_migration()
    calls = []
    _install_inspector(
        monkeypatch,
        migration,
        columns={"agents": {"id"}, "llm_models": {"id"}},
        indexes={"agents": set(), "llm_models": set()},
    )
    monkeypatch.setattr(
        migration.op,
        "add_column",
        lambda *args, **kwargs: calls.append(("add_column", args, kwargs)),
    )
    monkeypatch.setattr(
        migration.op,
        "create_index",
        lambda *args, **kwargs: calls.append(("create_index", args, kwargs)),
    )

    migration.upgrade()

    assert [(kind, args[0]) for kind, args, _ in calls] == [
        ("add_column", "agents"),
        ("add_column", "llm_models"),
        ("create_index", "ix_agents_active_tenant_created_at"),
        ("create_index", "ix_llm_models_active_tenant_created_at"),
    ]
    assert calls[2][1][1:3] == ("agents", ["tenant_id", "created_at"])
    assert calls[3][1][1:3] == ("llm_models", ["tenant_id", "created_at"])


def test_upgrade_is_noop_when_fresh_metadata_already_created_schema(
    monkeypatch,
) -> None:
    migration = _load_migration()
    _install_inspector(
        monkeypatch,
        migration,
        columns={
            "agents": {"id", "deleted_at"},
            "llm_models": {"id", "deleted_at"},
        },
        indexes={
            "agents": {"ix_agents_active_tenant_created_at"},
            "llm_models": {"ix_llm_models_active_tenant_created_at"},
        },
    )
    monkeypatch.setattr(
        migration.op,
        "add_column",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("unexpected add_column")
        ),
    )
    monkeypatch.setattr(
        migration.op,
        "create_index",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("unexpected create_index")
        ),
    )

    migration.upgrade()


def test_upgrade_repairs_partial_schema_independently(monkeypatch) -> None:
    migration = _load_migration()
    calls = []
    _install_inspector(
        monkeypatch,
        migration,
        columns={
            "agents": {"id", "deleted_at"},
            "llm_models": {"id"},
        },
        indexes={
            "agents": set(),
            "llm_models": {"ix_llm_models_active_tenant_created_at"},
        },
    )
    monkeypatch.setattr(
        migration.op,
        "add_column",
        lambda *args, **kwargs: calls.append(("add_column", args, kwargs)),
    )
    monkeypatch.setattr(
        migration.op,
        "create_index",
        lambda *args, **kwargs: calls.append(("create_index", args, kwargs)),
    )

    migration.upgrade()

    assert [(kind, args[0]) for kind, args, _ in calls] == [
        ("add_column", "llm_models"),
        ("create_index", "ix_agents_active_tenant_created_at"),
    ]


def test_downgrade_only_drops_existing_objects(monkeypatch) -> None:
    migration = _load_migration()
    calls = []
    _install_inspector(
        monkeypatch,
        migration,
        columns={
            "agents": {"id", "deleted_at"},
            "llm_models": {"id"},
        },
        indexes={
            "agents": {"ix_agents_active_tenant_created_at"},
            "llm_models": set(),
        },
    )
    monkeypatch.setattr(
        migration.op,
        "drop_index",
        lambda *args, **kwargs: calls.append(("drop_index", args, kwargs)),
    )
    monkeypatch.setattr(
        migration.op,
        "drop_column",
        lambda *args, **kwargs: calls.append(("drop_column", args, kwargs)),
    )

    migration.downgrade()

    assert [(kind, args[0]) for kind, args, _ in calls] == [
        ("drop_index", "ix_agents_active_tenant_created_at"),
        ("drop_column", "agents"),
    ]
