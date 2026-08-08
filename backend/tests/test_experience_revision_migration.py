"""Deployment contract for Experience revision drafts."""

from __future__ import annotations

import importlib.util
from pathlib import Path


MIGRATION_PATH = (
    Path(__file__).resolve().parents[1]
    / "alembic"
    / "versions"
    / "202607171530_add_experience_revision_drafts.py"
)


def _load_migration():
    spec = importlib.util.spec_from_file_location(
        "experience_revision_migration",
        MIGRATION_PATH,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeInspector:
    def __init__(self, *, complete: bool):
        self.complete = complete

    def get_columns(self, _table):
        return [{"name": "id"}, *([{"name": "draft_of_id"}] if self.complete else [])]

    def get_foreign_keys(self, _table):
        if not self.complete:
            return []
        return [{
            "name": "fk_experience_entries_draft_of_id",
            "constrained_columns": ["draft_of_id"],
        }]

    def get_indexes(self, _table):
        if not self.complete:
            return []
        return [{"name": "ix_experience_entries_draft_of_id"}]


def test_revision_migration_follows_the_unified_schema_head() -> None:
    migration = _load_migration()

    assert migration.revision == "add_experience_revision_drafts"
    assert migration.down_revision == "unify_runtime_group_schema"


def test_revision_migration_adds_every_missing_object(monkeypatch) -> None:
    migration = _load_migration()
    calls = []
    monkeypatch.setattr(migration, "_inspector", lambda: FakeInspector(complete=False))
    monkeypatch.setattr(migration.op, "add_column", lambda *args, **kwargs: calls.append(("column", args, kwargs)))
    monkeypatch.setattr(migration.op, "create_foreign_key", lambda *args, **kwargs: calls.append(("foreign_key", args, kwargs)))
    monkeypatch.setattr(migration.op, "create_index", lambda *args, **kwargs: calls.append(("index", args, kwargs)))

    migration.upgrade()

    assert [call[0] for call in calls] == ["column", "foreign_key", "index"]


def test_revision_migration_is_a_noop_for_fresh_databases(monkeypatch) -> None:
    migration = _load_migration()
    monkeypatch.setattr(migration, "_inspector", lambda: FakeInspector(complete=True))
    monkeypatch.setattr(migration.op, "add_column", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("unexpected add_column")))
    monkeypatch.setattr(migration.op, "create_foreign_key", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("unexpected create_foreign_key")))
    monkeypatch.setattr(migration.op, "create_index", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("unexpected create_index")))

    migration.upgrade()
