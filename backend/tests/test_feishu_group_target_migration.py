"""Schema guards and optional real-PostgreSQL coverage for f065."""

from __future__ import annotations

import importlib.util
import os
import shlex
import subprocess
import sys
import tempfile
import uuid
from itertools import product
from pathlib import Path
from unittest.mock import Mock

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

BACKEND = Path(__file__).resolve().parents[1]
MIGRATION = BACKEND / "alembic/versions/202608181600_schedule_trigger_feishu_group_target.py"
TABLES = ("agent_schedules", "agent_triggers")
COLUMN = "delivery_target_id"
PRESENCE_CASES = list(product((False, True), repeat=2))


def load_migration():
    spec = importlib.util.spec_from_file_location("feishu_group_target_migration", MIGRATION)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("present", PRESENCE_CASES)
@pytest.mark.parametrize("direction", ("upgrade", "downgrade"))
def test_only_missing_additions_or_existing_removals(monkeypatch, present, direction):
    migration = load_migration()
    columns = dict(zip(TABLES, present, strict=True))
    inspector = Mock()
    inspector.get_columns.side_effect = lambda table: (
        [{"name": "id"}, {"name": COLUMN}] if columns[table] else [{"name": "id"}]
    )
    monkeypatch.setattr(migration.op, "get_bind", Mock())
    monkeypatch.setattr(migration.sa, "inspect", lambda _bind: inspector)
    add = Mock()
    drop = Mock()
    monkeypatch.setattr(migration.op, "add_column", add)
    monkeypatch.setattr(migration.op, "drop_column", drop)

    getattr(migration, direction)()

    if direction == "upgrade":
        assert [call.args[0] for call in add.call_args_list] == [
            table for table in TABLES if not columns[table]
        ]
        for call in add.call_args_list:
            column = call.args[1]
            assert column.name == COLUMN
            assert isinstance(column.type, sa.UUID)
            assert column.nullable
        drop.assert_not_called()
    else:
        assert [call.args for call in drop.call_args_list] == [
            (table, COLUMN) for table in reversed(TABLES) if columns[table]
        ]
        add.assert_not_called()


def test_revision_graph_is_unchanged():
    migration = load_migration()
    assert migration.revision == "f065_feishu_group_target"
    assert migration.down_revision == "f064_tool_call_tenants"


@pytest.mark.parametrize("direction", ("upgrade", "downgrade"))
def test_inspection_errors_are_not_silently_ignored(monkeypatch, direction):
    migration = load_migration()
    monkeypatch.setattr(migration.op, "get_bind", Mock())
    inspector = Mock()
    inspector.get_columns.side_effect = sa.exc.NoSuchTableError(TABLES[0])
    monkeypatch.setattr(migration.sa, "inspect", lambda _bind: inspector)
    monkeypatch.setattr(migration.op, "add_column", Mock())
    monkeypatch.setattr(migration.op, "drop_column", Mock())
    with pytest.raises(sa.exc.NoSuchTableError):
        getattr(migration, direction)()


@pytest.fixture(scope="module")
def postgres_cluster():
    """Opt-in disposable Unix-socket cluster; never connects to an existing DB."""
    binary_dir = os.environ.get("CLAWITH_TEST_POSTGRES_BIN")
    if not binary_dir:
        pytest.skip("set CLAWITH_TEST_POSTGRES_BIN to run isolated PostgreSQL integration tests")
    binaries = Path(binary_dir)
    with tempfile.TemporaryDirectory(prefix="clawith-pg-", dir="/tmp") as temporary:
        root = Path(temporary)
        data = root / "data"
        subprocess.run(
            [str(binaries / "initdb"), "-D", str(data), "-A", "trust", "-U", "clawith", "--no-locale", "--encoding=UTF8"],
            check=True, capture_output=True, text=True, timeout=60,
        )
        options = shlex.join(["-F", "-c", "listen_addresses=", "-k", str(root)])
        control = [str(binaries / "pg_ctl"), "-D", str(data)]
        try:
            subprocess.run(
                [*control, "-l", str(root / "postgres.log"), "-o", options, "-w", "start"],
                check=True, capture_output=True, text=True, timeout=60,
            )
            yield str(root)
        finally:
            subprocess.run([*control, "-m", "immediate", "-w", "stop"], check=True, capture_output=True, timeout=60)


@pytest.fixture
def postgres_url(postgres_cluster):
    database = f"migration_{uuid.uuid4().hex}"
    url = sa.URL.create("postgresql+psycopg", username="clawith", database="postgres", query={"host": postgres_cluster})
    admin = sa.create_engine(url, isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as connection:
            connection.exec_driver_sql(f'CREATE DATABASE "{database}"')
    finally:
        admin.dispose()
    return url.set(database=database)


@pytest.mark.parametrize("present", PRESENCE_CASES)
def test_real_upgrade_preserves_existing_targets_and_repeats(postgres_url, present):
    engine = sa.create_engine(postgres_url)
    target = uuid.uuid4()
    try:
        with engine.begin() as connection:
            for table, exists in zip(TABLES, present, strict=True):
                column = f", {COLUMN} UUID" if exists else ""
                connection.exec_driver_sql(f"CREATE TABLE {table} (id INTEGER PRIMARY KEY{column})")
                connection.exec_driver_sql(f"INSERT INTO {table} (id) VALUES (1)")
                if exists:
                    connection.execute(sa.text(f"UPDATE {table} SET {COLUMN} = :target"), {"target": target})
            migration = load_migration()
            with Operations.context(MigrationContext.configure(connection)):
                migration.upgrade()
                migration.upgrade()
                for table, existed in zip(TABLES, present, strict=True):
                    value = connection.exec_driver_sql(f"SELECT {COLUMN} FROM {table}").scalar_one()
                    assert value == (target if existed else None)
                migration.downgrade()
                migration.downgrade()
                for table in TABLES:
                    assert [column["name"] for column in sa.inspect(connection).get_columns(table)] == ["id"]
                migration.upgrade()
    finally:
        engine.dispose()


def test_real_fresh_database_reaches_head_and_round_trips(postgres_url):
    # Slashes are legal query characters; keep the generated socket path literal
    # rather than introducing percent escapes into Alembic's ConfigParser.
    url = f"postgresql+asyncpg://clawith@/{postgres_url.database}?host={postgres_url.query['host']}"

    def alembic(*arguments):
        result = subprocess.run(
            [sys.executable, "-m", "alembic", *arguments], cwd=BACKEND,
            env={**os.environ, "DATABASE_URL": url}, capture_output=True, text=True, check=False, timeout=60,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        return result.stdout

    alembic("upgrade", "head")
    assert "f065_feishu_group_target" in alembic("current", "--check-heads")
    alembic("downgrade", "-1")
    engine = sa.create_engine(postgres_url)
    try:
        with engine.connect() as connection:
            for table in TABLES:
                assert COLUMN not in {column["name"] for column in sa.inspect(connection).get_columns(table)}
        alembic("upgrade", "head")
        with engine.connect() as connection:
            for table in TABLES:
                columns = {column["name"]: column for column in sa.inspect(connection).get_columns(table)}
                assert isinstance(columns[COLUMN]["type"], sa.UUID)
                assert columns[COLUMN]["nullable"]
        alembic("current", "--check-heads")
    finally:
        engine.dispose()
