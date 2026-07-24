"""Regression coverage for the logical-delete migration's schema drift handling."""

import importlib.util
from pathlib import Path


MIGRATION_PATH = (
    Path(__file__).resolve().parents[1]
    / "alembic"
    / "versions"
    / "202607221500_add_agent_model_deleted_at.py"
)


def _migration_module():
    spec = importlib.util.spec_from_file_location("agent_model_deleted_at_migration", MIGRATION_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_upgrade_skips_schema_elements_that_already_exist(monkeypatch) -> None:
    migration = _migration_module()
    columns = {
        "agents": {"id", "deleted_at"},
        "llm_models": {"id"},
    }
    indexes = {
        "agents": {"ix_agents_active_tenant_created_at"},
        "llm_models": set(),
    }
    added_columns: list[str] = []
    added_indexes: list[str] = []

    monkeypatch.setattr(migration, "_columns", lambda table: columns[table])
    monkeypatch.setattr(migration, "_indexes", lambda table: indexes[table])
    monkeypatch.setattr(migration.op, "add_column", lambda table, column: added_columns.append(table))
    monkeypatch.setattr(migration.op, "create_index", lambda name, table, *args, **kwargs: added_indexes.append(name))

    migration.upgrade()

    assert added_columns == ["llm_models"]
    assert added_indexes == ["ix_llm_models_active_tenant_created_at"]
