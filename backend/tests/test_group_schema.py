"""Static schema contract tests for the native group domain."""

from importlib import util
from pathlib import Path

import pytest
import sqlalchemy as sa

from app.models.group import Group, GroupMember


MIGRATION_PATH = (
    Path(__file__).resolve().parents[1] / "alembic" / "versions" / "202607131843_create_group_domain_schema.py"
)


def _load_migration():
    spec = util.spec_from_file_location("create_group_domain_schema", MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    module = util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _constraint_names(table: sa.Table, constraint_type: type[sa.Constraint]) -> set[str | None]:
    return {constraint.name for constraint in table.constraints if isinstance(constraint, constraint_type)}


class MetadataInspector:
    """Small Inspector double built from the current ORM metadata."""

    def __init__(self, *, missing_indexes: set[str] | None = None):
        missing_indexes = missing_indexes or set()
        tables = (Group.__table__, GroupMember.__table__)
        self.table_names = [table.name for table in tables]
        self.columns: dict[str, list[dict[str, object]]] = {}
        self.primary_keys: dict[str, dict[str, object]] = {}
        self.foreign_keys: dict[str, list[dict[str, object]]] = {}
        self.unique_constraints: dict[str, list[dict[str, object]]] = {}
        self.check_constraints: dict[str, list[dict[str, object]]] = {}
        self.indexes: dict[str, list[dict[str, object]]] = {}

        for table in tables:
            self.columns[table.name] = [
                {
                    "name": column.name,
                    "type": column.type,
                    "nullable": column.nullable,
                    "default": (str(column.server_default.arg) if column.server_default is not None else None),
                }
                for column in table.columns
            ]
            self.primary_keys[table.name] = {
                "name": table.primary_key.name,
                "constrained_columns": list(table.primary_key.columns.keys()),
            }
            self.foreign_keys[table.name] = []
            self.unique_constraints[table.name] = []
            self.check_constraints[table.name] = []

            for constraint in table.constraints:
                if isinstance(constraint, sa.ForeignKeyConstraint):
                    target_parts = [element.target_fullname.rsplit(".", 1) for element in constraint.elements]
                    self.foreign_keys[table.name].append(
                        {
                            "name": constraint.name,
                            "constrained_columns": [element.parent.name for element in constraint.elements],
                            "referred_table": target_parts[0][0],
                            "referred_columns": [parts[1] for parts in target_parts],
                            "options": {"ondelete": constraint.ondelete},
                        }
                    )
                elif isinstance(constraint, sa.UniqueConstraint):
                    self.unique_constraints[table.name].append(
                        {
                            "name": constraint.name,
                            "column_names": list(constraint.columns.keys()),
                        }
                    )
                elif isinstance(constraint, sa.CheckConstraint):
                    self.check_constraints[table.name].append(
                        {"name": constraint.name, "sqltext": str(constraint.sqltext)}
                    )

            self.indexes[table.name] = [
                {
                    "name": index.name,
                    "column_names": list(index.columns.keys()),
                    "unique": index.unique,
                }
                for index in table.indexes
                if index.name not in missing_indexes
            ]

    def get_table_names(self) -> list[str]:
        return self.table_names

    def get_columns(self, table_name: str) -> list[dict[str, object]]:
        return self.columns[table_name]

    def get_pk_constraint(self, table_name: str) -> dict[str, object]:
        return self.primary_keys[table_name]

    def get_foreign_keys(self, table_name: str) -> list[dict[str, object]]:
        return self.foreign_keys[table_name]

    def get_unique_constraints(self, table_name: str) -> list[dict[str, object]]:
        return self.unique_constraints[table_name]

    def get_check_constraints(self, table_name: str) -> list[dict[str, object]]:
        return self.check_constraints[table_name]

    def get_indexes(self, table_name: str) -> list[dict[str, object]]:
        return self.indexes[table_name]


def test_group_model_metadata_matches_domain_contract():
    table = Group.__table__

    assert set(table.columns.keys()) == {
        "id",
        "tenant_id",
        "name",
        "description",
        "created_by_participant_id",
        "deleted_at",
        "created_at",
        "updated_at",
    }
    assert table.primary_key.name == "pk_groups"
    assert _constraint_names(table, sa.ForeignKeyConstraint) == {
        "fk_groups_tenant_id_tenants",
        "fk_groups_created_by_participant_id_participants",
    }
    assert {index.name for index in table.indexes} == {"ix_groups_tenant_id_deleted_at"}
    assert table.c.tenant_id.nullable is False
    assert table.c.created_by_participant_id.nullable is False


def test_group_member_model_metadata_matches_membership_contract():
    table = GroupMember.__table__

    assert set(table.columns.keys()) == {
        "id",
        "group_id",
        "participant_id",
        "role",
        "joined_at",
        "removed_at",
        "session_read_state",
    }
    assert table.primary_key.name == "pk_group_members"
    assert _constraint_names(table, sa.ForeignKeyConstraint) == {
        "fk_group_members_group_id_groups",
        "fk_group_members_participant_id_participants",
    }
    assert _constraint_names(table, sa.UniqueConstraint) == {"uq_group_members_group_participant"}
    assert _constraint_names(table, sa.CheckConstraint) == {"ck_group_members_role"}
    assert {index.name for index in table.indexes} == {"ix_group_members_participant_id"}
    assert str(table.c.session_read_state.server_default.arg) == "'{}'::jsonb"


def test_group_migration_extends_the_current_static_head():
    migration = _load_migration()

    assert migration.revision == "create_group_domain_schema"
    assert migration.down_revision == "add_title_to_agent_focus_items"


def test_group_migration_builds_both_tables_without_a_database(monkeypatch):
    migration = _load_migration()
    created_tables: dict[str, tuple[object, ...]] = {}
    created_indexes: list[tuple[str, str, tuple[str, ...], bool]] = []

    class EmptyInspector:
        @staticmethod
        def get_table_names() -> list[str]:
            return []

    monkeypatch.setattr(migration.op, "get_bind", lambda: object())
    monkeypatch.setattr(migration.sa, "inspect", lambda _bind: EmptyInspector())
    monkeypatch.setattr(
        migration.op,
        "create_table",
        lambda name, *elements, **_kwargs: created_tables.setdefault(name, elements),
    )
    monkeypatch.setattr(
        migration.op,
        "create_index",
        lambda name, table, columns, unique=False, **_kwargs: created_indexes.append(
            (name, table, tuple(columns), unique)
        ),
    )

    migration.upgrade()

    assert set(created_tables) == {"groups", "group_members"}
    assert created_indexes == [
        ("ix_groups_tenant_id_deleted_at", "groups", ("tenant_id", "deleted_at"), False),
        ("ix_group_members_participant_id", "group_members", ("participant_id",), False),
    ]

    group_member_constraints = {
        element.name for element in created_tables["group_members"] if isinstance(element, sa.Constraint)
    }
    assert {
        "pk_group_members",
        "ck_group_members_role",
        "uq_group_members_group_participant",
        "fk_group_members_group_id_groups",
        "fk_group_members_participant_id_participants",
    } <= group_member_constraints


def test_group_migration_accepts_current_metadata_without_writes(monkeypatch):
    migration = _load_migration()
    inspector = MetadataInspector()
    writes: list[str] = []

    monkeypatch.setattr(migration.op, "get_bind", lambda: object())
    monkeypatch.setattr(migration.sa, "inspect", lambda _bind: inspector)
    monkeypatch.setattr(
        migration.op,
        "create_table",
        lambda name, *_elements, **_kwargs: writes.append(f"table:{name}"),
    )
    monkeypatch.setattr(
        migration.op,
        "create_index",
        lambda name, *_args, **_kwargs: writes.append(f"index:{name}"),
    )

    migration.upgrade()

    assert writes == []


def test_group_migration_adds_only_a_missing_index(monkeypatch):
    migration = _load_migration()
    inspector = MetadataInspector(missing_indexes={"ix_group_members_participant_id"})
    created_indexes: list[tuple[str, str, tuple[str, ...], bool]] = []

    monkeypatch.setattr(migration.op, "get_bind", lambda: object())
    monkeypatch.setattr(migration.sa, "inspect", lambda _bind: inspector)
    monkeypatch.setattr(
        migration.op,
        "create_index",
        lambda name, table, columns, unique=False, **_kwargs: created_indexes.append(
            (name, table, tuple(columns), unique)
        ),
    )

    migration.upgrade()

    assert created_indexes == [("ix_group_members_participant_id", "group_members", ("participant_id",), False)]


@pytest.mark.parametrize(
    "broken_schema",
    [
        "column_type",
        "column_nullable",
        "column_default",
        "primary_key_columns",
        "foreign_key_target",
        "foreign_key_ondelete",
        "unique_columns",
        "check_expression",
        "index_columns",
        "index_unique",
    ],
)
def test_group_migration_rejects_incompatible_existing_schema(monkeypatch, broken_schema):
    migration = _load_migration()
    inspector = MetadataInspector()

    role_column = next(column for column in inspector.columns["group_members"] if column["name"] == "role")
    group_foreign_key = next(
        constraint
        for constraint in inspector.foreign_keys["group_members"]
        if constraint["name"] == "fk_group_members_group_id_groups"
    )
    member_index = next(
        index for index in inspector.indexes["group_members"] if index["name"] == "ix_group_members_participant_id"
    )

    if broken_schema == "column_type":
        role_column["type"] = sa.String(50)
    elif broken_schema == "column_nullable":
        role_column["nullable"] = True
    elif broken_schema == "column_default":
        role_column["default"] = "'manager'"
    elif broken_schema == "primary_key_columns":
        inspector.primary_keys["groups"]["constrained_columns"] = ["tenant_id"]
    elif broken_schema == "foreign_key_target":
        group_foreign_key["referred_table"] = "participants"
    elif broken_schema == "foreign_key_ondelete":
        group_foreign_key["options"] = {"ondelete": "RESTRICT"}
    elif broken_schema == "unique_columns":
        inspector.unique_constraints["group_members"][0]["column_names"] = ["group_id", "role"]
    elif broken_schema == "check_expression":
        inspector.check_constraints["group_members"][0]["sqltext"] = "role IN ('manager', 'member', 'owner')"
    elif broken_schema == "index_columns":
        member_index["column_names"] = ["group_id"]
    elif broken_schema == "index_unique":
        member_index["unique"] = True

    monkeypatch.setattr(migration.op, "get_bind", lambda: object())
    monkeypatch.setattr(migration.sa, "inspect", lambda _bind: inspector)

    with pytest.raises(RuntimeError):
        migration.upgrade()


def test_group_migration_downgrade_drops_members_before_groups(monkeypatch):
    migration = _load_migration()
    dropped_tables: list[str] = []

    class EmptyResult:
        @staticmethod
        def first():
            return None

    class EmptyConnection:
        def __init__(self):
            self.statements: list[str] = []

        def execute(self, statement):
            self.statements.append(str(statement))
            return EmptyResult()

    class GroupInspector:
        @staticmethod
        def get_table_names() -> list[str]:
            return ["groups", "group_members"]

    connection = EmptyConnection()
    monkeypatch.setattr(migration.op, "get_bind", lambda: connection)
    monkeypatch.setattr(migration.sa, "inspect", lambda _bind: GroupInspector())
    monkeypatch.setattr(migration.op, "drop_table", dropped_tables.append)

    migration.downgrade()

    assert dropped_tables == ["group_members", "groups"]
    assert connection.statements == [
        'LOCK TABLE "group_members", "groups" IN ACCESS EXCLUSIVE MODE',
        'SELECT 1 FROM "group_members" LIMIT 1',
        'SELECT 1 FROM "groups" LIMIT 1',
    ]


@pytest.mark.parametrize("nonempty_table", ["group_members", "groups"])
def test_group_migration_downgrade_rejects_nonempty_tables(monkeypatch, nonempty_table):
    migration = _load_migration()
    dropped_tables: list[str] = []

    class Result:
        def __init__(self, row):
            self.row = row

        def first(self):
            return self.row

    class NonemptyConnection:
        @staticmethod
        def execute(statement):
            sql = str(statement)
            if sql == f'SELECT 1 FROM "{nonempty_table}" LIMIT 1':
                return Result((1,))
            return Result(None)

    class GroupInspector:
        @staticmethod
        def get_table_names() -> list[str]:
            return ["groups", "group_members"]

    monkeypatch.setattr(migration.op, "get_bind", lambda: NonemptyConnection())
    monkeypatch.setattr(migration.sa, "inspect", lambda _bind: GroupInspector())
    monkeypatch.setattr(migration.op, "drop_table", dropped_tables.append)

    with pytest.raises(RuntimeError, match=f"{nonempty_table} contains data"):
        migration.downgrade()

    assert dropped_tables == []
