"""Regression tests for the bridge enum migrations.

`ALTER TYPE ... ADD VALUE` on PostgreSQL is NOT transactional and NOT
idempotent without the `IF NOT EXISTS` clause. We run migrations on
every backend startup, so dropping that clause would make the backend
fail to boot on every restart after the first — a silent footgun that's
easy to introduce by copy-pasting.

Instead of spinning up a real Postgres here, we mock `op.execute` to
capture every SQL string the migration emits, then assert the safety
clauses are there.
"""

import importlib.util
import sys
from pathlib import Path
from unittest import mock

import pytest


# `alembic/versions` is not an importable package (no __init__.py), so we
# load each migration file directly by path.
_VERSIONS_DIR = Path(__file__).resolve().parent.parent / "alembic" / "versions"

_MIGRATIONS_UNDER_TEST = [
    ("add_bridge_activity_enum", 2),
    ("add_bridge_session_enum", 6),
]


def _load(name: str):
    path = _VERSIONS_DIR / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"_test_mig_{name}", path)
    mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    sys.modules[spec.name] = mod  # type: ignore[union-attr]
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


@pytest.mark.parametrize("module_path,expected_stmts", _MIGRATIONS_UNDER_TEST)
def test_upgrade_emits_only_idempotent_alters(module_path, expected_stmts):
    mod = _load(module_path)
    with mock.patch.object(mod.op, "execute") as m_exec:
        mod.upgrade()

    calls = [c.args[0] for c in m_exec.call_args_list]
    assert len(calls) == expected_stmts, (
        f"{module_path}.upgrade() emitted {len(calls)} statements; "
        f"expected {expected_stmts}. If you added a new enum value, "
        "bump the expected count here."
    )

    for sql in calls:
        # Every statement must be a no-op when the value already exists —
        # that's what lets us replay the migration on every backend boot.
        assert sql.startswith("ALTER TYPE "), f"unexpected non-enum stmt: {sql!r}"
        assert "ADD VALUE IF NOT EXISTS" in sql, (
            f"migration statement is not idempotent (missing "
            f"'IF NOT EXISTS'): {sql!r}"
        )


@pytest.mark.parametrize("module_path,_n", _MIGRATIONS_UNDER_TEST)
def test_upgrade_can_be_invoked_twice(module_path, _n):
    # Because every statement carries IF NOT EXISTS, calling upgrade()
    # twice back-to-back must hit op.execute twice the emit count
    # without raising.
    mod = _load(module_path)
    with mock.patch.object(mod.op, "execute") as m_exec:
        mod.upgrade()
        mod.upgrade()
    assert m_exec.call_count >= 2


@pytest.mark.parametrize("module_path,_n", _MIGRATIONS_UNDER_TEST)
def test_downgrade_is_a_no_op(module_path, _n):
    # Postgres can't remove enum values; the migration doc acknowledges
    # this and downgrade() must not emit DDL that would fail.
    mod = _load(module_path)
    with mock.patch.object(mod.op, "execute") as m_exec:
        mod.downgrade()
    assert m_exec.call_count == 0


def test_session_enum_covers_all_bridge_session_events():
    # Mirror check: every action_type produced by the bridge session code
    # must be present in the enum migration. If someone introduces a new
    # event type (e.g. local_session_timeout) this test will remind them
    # to migrate the enum before shipping.
    from app.models.activity_log import AgentActivityLog

    enum_values = AgentActivityLog.__table__.c.action_type.type.enums
    assert set(enum_values).issuperset({
        "bridge_attached", "bridge_detached",
        "bridge_installer_download",
        "local_session_start", "local_session_done", "local_session_error",
        "reverse_tool_call", "reverse_tool_result",
    })
