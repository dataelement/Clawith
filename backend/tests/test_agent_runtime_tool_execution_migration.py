"""Migration contract for Runtime Tool identity separation."""

from __future__ import annotations

import importlib.util
from pathlib import Path

MIGRATION_PATH = (
    Path(__file__).resolve().parents[1]
    / "alembic"
    / "versions"
    / "v1_11_3_f062_tool_execution_identity.py"
)


def _load_migration():
    spec = importlib.util.spec_from_file_location(
        "tool_execution_identity_migration",
        MIGRATION_PATH,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_revision_extends_the_current_single_head() -> None:
    migration = _load_migration()

    assert migration.revision == "f062_tool_execution_identity"
    assert migration.down_revision == "f061_enterprise_info_tenant_id"


def test_upgrade_adds_only_missing_nullable_identity_columns(monkeypatch) -> None:
    migration = _load_migration()
    calls = []
    monkeypatch.setattr(
        migration,
        "_column_names",
        lambda **_kwargs: {"provider_call_id"},
    )
    monkeypatch.setattr(
        migration.op,
        "add_column",
        lambda *args, **kwargs: calls.append((args, kwargs)),
    )

    migration.upgrade()

    assert len(calls) == 1
    table_name, column = calls[0][0]
    assert table_name == "agent_tool_executions"
    assert column.name == "contract_version"
    assert column.nullable is True
    assert str(column.type) == "VARCHAR(255)"


def test_upgrade_is_compatible_with_rows_created_before_the_migration(
    monkeypatch,
) -> None:
    migration = _load_migration()
    monkeypatch.setattr(
        migration,
        "_column_names",
        lambda **_kwargs: {"provider_call_id", "contract_version"},
    )
    monkeypatch.setattr(
        migration.op,
        "add_column",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError(f"unexpected add_column: {args}, {kwargs}")
        ),
    )

    migration.upgrade()


def test_downgrade_drops_both_identity_columns_in_reverse_order(monkeypatch) -> None:
    migration = _load_migration()
    calls = []
    monkeypatch.setattr(
        migration,
        "_column_names",
        lambda **_kwargs: {"provider_call_id", "contract_version"},
    )
    monkeypatch.setattr(
        migration.op,
        "drop_column",
        lambda *args, **kwargs: calls.append((args, kwargs)),
    )

    migration.downgrade()

    assert [args for args, _ in calls] == [
        ("agent_tool_executions", "contract_version"),
        ("agent_tool_executions", "provider_call_id"),
    ]
