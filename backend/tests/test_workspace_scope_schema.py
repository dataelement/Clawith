"""Static schema contract for agent and group workspace scopes."""

from importlib import util
from pathlib import Path

import sqlalchemy as sa

from app.models.workspace import WorkspaceEditLock, WorkspaceFileRevision


MIGRATION_PATH = (
    Path(__file__).resolve().parents[1]
    / "alembic"
    / "versions"
    / "202607161200_unify_runtime_group_schema.py"
)


def _load_migration():
    spec = util.spec_from_file_location("unify_runtime_group_schema", MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    module = util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _check_names(table: sa.Table) -> set[str | None]:
    return {
        constraint.name
        for constraint in table.constraints
        if isinstance(constraint, sa.CheckConstraint)
    }


def test_workspace_models_expose_one_shared_scope_contract() -> None:
    revision = WorkspaceFileRevision.__table__
    edit_lock = WorkspaceEditLock.__table__

    for table in (revision, edit_lock):
        assert {"agent_id", "scope_type", "scope_id", "path"}.issubset(
            table.columns.keys()
        )
        assert table.c.agent_id.nullable is True
        assert table.c.scope_type.nullable is False
        assert table.c.scope_id.nullable is False
        assert f"ck_{table.name}_scope_type" in _check_names(table)
        assert f"ck_{table.name}_scope_identity" in _check_names(table)

    unique_names = {
        constraint.name
        for constraint in edit_lock.constraints
        if isinstance(constraint, sa.UniqueConstraint)
    }
    assert unique_names == {"uq_workspace_edit_locks_scope_path"}
    assert "ix_workspace_file_revisions_scope_path" in {
        index.name for index in revision.indexes
    }


def test_workspace_scope_migration_follows_the_runtime_schema() -> None:
    migration = _load_migration()

    assert migration.revision == "unify_runtime_group_schema"
    assert migration.down_revision == "add_title_to_agent_focus_items"
