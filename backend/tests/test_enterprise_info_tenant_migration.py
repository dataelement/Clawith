"""Schema-state contracts for the enterprise_info tenant migration."""

from __future__ import annotations

import importlib.util
from pathlib import Path

MIGRATION_PATH = (
    Path(__file__).resolve().parents[1]
    / "alembic"
    / "versions"
    / "v1_0_0_f061_enterprise_info_tenant_id.py"
)


def _load_migration():
    spec = importlib.util.spec_from_file_location(
        "enterprise_info_tenant_migration",
        MIGRATION_PATH,
    )
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    return migration


def test_upgrade_is_noop_when_fresh_schema_already_has_target_shape(
    monkeypatch,
) -> None:
    migration = _load_migration()
    monkeypatch.setattr(
        migration,
        "_schema_names",
        lambda **_kwargs: (
            {"tenant_id", "info_type"},
            {"ix_enterprise_info_tenant_id"},
            {"uq_enterprise_info_tenant_type"},
        ),
    )
    for operation in (
        "add_column",
        "create_index",
        "drop_constraint",
        "create_unique_constraint",
    ):
        monkeypatch.setattr(
            migration.op,
            operation,
            lambda *args, _operation=operation, **kwargs: (_ for _ in ()).throw(
                AssertionError(
                    f"unexpected {_operation}: {args}, {kwargs}"
                )
            ),
        )

    migration.upgrade()


def test_upgrade_moves_legacy_schema_to_tenant_scoped_shape(monkeypatch) -> None:
    migration = _load_migration()
    monkeypatch.setattr(
        migration,
        "_schema_names",
        lambda **_kwargs: (
            {"info_type"},
            set(),
            {"enterprise_info_info_type_key"},
        ),
    )
    calls: list[tuple[str, tuple, dict]] = []
    for operation in (
        "add_column",
        "create_index",
        "drop_constraint",
        "create_unique_constraint",
    ):
        monkeypatch.setattr(
            migration.op,
            operation,
            lambda *args, _operation=operation, **kwargs: calls.append(
                (_operation, args, kwargs)
            ),
        )

    migration.upgrade()

    assert [operation for operation, _, _ in calls] == [
        "add_column",
        "create_index",
        "drop_constraint",
        "create_unique_constraint",
    ]


def test_downgrade_reverses_only_present_target_objects(monkeypatch) -> None:
    migration = _load_migration()
    monkeypatch.setattr(
        migration,
        "_schema_names",
        lambda **_kwargs: (
            {"tenant_id", "info_type"},
            {"ix_enterprise_info_tenant_id"},
            {"uq_enterprise_info_tenant_type"},
        ),
    )
    calls: list[tuple[str, tuple, dict]] = []
    for operation in (
        "drop_constraint",
        "create_unique_constraint",
        "drop_index",
        "drop_column",
    ):
        monkeypatch.setattr(
            migration.op,
            operation,
            lambda *args, _operation=operation, **kwargs: calls.append(
                (_operation, args, kwargs)
            ),
        )

    migration.downgrade()

    assert [operation for operation, _, _ in calls] == [
        "drop_constraint",
        "create_unique_constraint",
        "drop_index",
        "drop_column",
    ]
