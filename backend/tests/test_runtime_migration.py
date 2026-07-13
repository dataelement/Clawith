"""Static tests for the check-first Agent Runtime migration."""

from importlib import util
from pathlib import Path

import pytest
import sqlalchemy as sa

# Register Runtime tables and every referenced target in shared metadata.
from app.models.agent import Agent  # noqa: F401
from app.models.agent_run import AgentRun
from app.models.agent_run_command import AgentRunCommand
from app.models.agent_run_event import AgentRunEvent
from app.models.agent_tool_execution import AgentToolExecution
from app.models.audit import ChatMessage  # noqa: F401
from app.models.chat_session import ChatSession  # noqa: F401
from app.models.llm import LLMModel  # noqa: F401
from app.models.session_context_state import SessionContextState
from app.models.tenant import Tenant  # noqa: F401
from app.models.user import User  # noqa: F401


MIGRATION_PATH = (
    Path(__file__).resolve().parents[1]
    / "alembic"
    / "versions"
    / "202607131930_create_agent_runtime_schema.py"
)
RUNTIME_TABLES = (
    AgentRun.__table__,
    AgentRunCommand.__table__,
    AgentRunEvent.__table__,
    AgentToolExecution.__table__,
    SessionContextState.__table__,
)


def _load_migration():
    spec = util.spec_from_file_location("create_agent_runtime_schema", MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    module = util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class MetadataInspector:
    """Inspector double derived from the current Runtime ORM metadata."""

    def __init__(
        self,
        *,
        table_names: set[str] | None = None,
        missing_indexes: set[str] | None = None,
    ) -> None:
        selected = set(table_names) if table_names is not None else {table.name for table in RUNTIME_TABLES}
        self.tables = {table.name: table for table in RUNTIME_TABLES if table.name in selected}
        self.missing_indexes = missing_indexes or set()
        self.columns: dict[str, list[dict[str, object]]] = {}
        self.primary_keys: dict[str, dict[str, object]] = {}
        self.foreign_keys: dict[str, list[dict[str, object]]] = {}
        self.unique_constraints: dict[str, list[dict[str, object]]] = {}
        self.check_constraints: dict[str, list[dict[str, object]]] = {}
        self.indexes: dict[str, list[dict[str, object]]] = {}

        for table_name, table in self.tables.items():
            self.columns[table_name] = [
                {
                    "name": column.name,
                    "type": column.type,
                    "nullable": column.nullable,
                    "default": (
                        str(column.server_default.arg)
                        if column.server_default is not None
                        else None
                    ),
                }
                for column in table.columns
            ]
            self.primary_keys[table_name] = {
                "name": table.primary_key.name,
                "constrained_columns": list(table.primary_key.columns.keys()),
            }
            self.foreign_keys[table_name] = []
            self.unique_constraints[table_name] = []
            self.check_constraints[table_name] = []

            for constraint in table.constraints:
                if isinstance(constraint, sa.ForeignKeyConstraint):
                    targets = [element.target_fullname.rsplit(".", 1) for element in constraint.elements]
                    self.foreign_keys[table_name].append(
                        {
                            "name": constraint.name,
                            "constrained_columns": [
                                element.parent.name for element in constraint.elements
                            ],
                            "referred_table": targets[0][0],
                            "referred_columns": [target[1] for target in targets],
                            "options": {"ondelete": constraint.ondelete},
                        }
                    )
                elif isinstance(constraint, sa.UniqueConstraint):
                    self.unique_constraints[table_name].append(
                        {
                            "name": constraint.name,
                            "column_names": list(constraint.columns.keys()),
                        }
                    )
                elif isinstance(constraint, sa.CheckConstraint):
                    self.check_constraints[table_name].append(
                        {"name": constraint.name, "sqltext": str(constraint.sqltext)}
                    )

            self.indexes[table_name] = []
            for index in table.indexes:
                if index.name in self.missing_indexes:
                    continue
                where = index.dialect_options["postgresql"].get("where")
                where_text = None
                if where is not None:
                    where_text = str(where).replace(f"{table_name}.", "")
                column_sorting: dict[str, tuple[str, ...]] = {}
                for expression in index.expressions:
                    if str(expression).lower().endswith(" desc"):
                        column_name = getattr(getattr(expression, "element", None), "name", None)
                        if column_name:
                            column_sorting[column_name] = ("desc",)
                self.indexes[table_name].append(
                    {
                        "name": index.name,
                        "column_names": list(index.columns.keys()),
                        "unique": index.unique,
                        "dialect_options": {"postgresql_where": where_text},
                        "column_sorting": column_sorting,
                    }
                )

    def get_table_names(self) -> list[str]:
        return list(self.tables)

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


def _install_upgrade_doubles(monkeypatch, migration, inspector):
    created_tables: list[tuple[str, tuple[object, ...]]] = []
    created_indexes: list[tuple[str, str, tuple[str, ...], bool, object | None]] = []
    executed_statements: list[str] = []
    monkeypatch.setattr(migration.op, "get_bind", lambda: object())
    monkeypatch.setattr(migration.sa, "inspect", lambda _bind: inspector)
    monkeypatch.setattr(
        migration.op,
        "execute",
        lambda statement: executed_statements.append(str(statement)),
    )
    monkeypatch.setattr(
        migration.op,
        "create_table",
        lambda name, *elements, **_kwargs: created_tables.append((name, elements)),
    )

    def record_index(name, table, columns, unique=False, **kwargs):
        created_indexes.append(
            (
                name,
                table,
                tuple(str(column) for column in columns),
                unique,
                kwargs.get("postgresql_where"),
            )
        )

    monkeypatch.setattr(migration.op, "create_index", record_index)
    return created_tables, created_indexes, executed_statements


def test_runtime_migration_extends_model_capability_revision() -> None:
    migration = _load_migration()

    assert migration.revision == "create_agent_runtime_schema"
    assert migration.down_revision == "add_llm_runtime_capabilities"


def test_empty_schema_creates_parent_then_dependants_with_all_indexes(monkeypatch) -> None:
    migration = _load_migration()

    class EmptyInspector:
        @staticmethod
        def get_table_names() -> list[str]:
            return []

    tables, indexes, statements = _install_upgrade_doubles(
        monkeypatch, migration, EmptyInspector()
    )
    migration.upgrade()

    assert [name for name, _elements in tables] == [
        "agent_runs",
        "agent_run_commands",
        "agent_run_events",
        "agent_tool_executions",
        "session_context_states",
    ]
    assert {name for name, *_rest in indexes} == set(migration._INDEXES)
    assert len(indexes) == len(migration._INDEXES)
    assert statements == [migration._CREATE_CHECKPOINT_SCHEMA_SQL]

    run_constraints = {
        element.name
        for element in tables[0][1]
        if isinstance(element, sa.Constraint)
    }
    assert {
        "pk_agent_runs",
        "uq_agent_runs_tenant_id_id",
        "fk_agent_runs_session_id_chat_sessions",
        "fk_agent_runs_tenant_session_chat_sessions",
        "ck_agent_runs_orchestration_identity",
    } <= run_constraints

    run_session_foreign_keys = {
        constraint.name: constraint.ondelete
        for constraint in tables[0][1]
        if isinstance(constraint, sa.ForeignKeyConstraint)
        and constraint.name
        in {
            "fk_agent_runs_session_id_chat_sessions",
            "fk_agent_runs_tenant_session_chat_sessions",
        }
    }
    assert run_session_foreign_keys == {
        "fk_agent_runs_session_id_chat_sessions": "SET NULL",
        "fk_agent_runs_tenant_session_chat_sessions": None,
    }


def test_created_table_definitions_match_the_validated_contract(monkeypatch) -> None:
    migration = _load_migration()

    class EmptyInspector:
        @staticmethod
        def get_table_names() -> list[str]:
            return []

    tables, _indexes, _statements = _install_upgrade_doubles(
        monkeypatch, migration, EmptyInspector()
    )
    migration.upgrade()

    for table_name, elements in tables:
        table = sa.Table(table_name, sa.MetaData(), *elements)
        actual_columns = {
            column.name: (
                migration._type_signature(column.type),
                column.nullable,
                migration._canonical_default(
                    column.server_default.arg if column.server_default is not None else None
                ),
            )
            for column in table.columns
        }
        assert actual_columns == migration._COLUMNS_BY_TABLE[table_name]
        assert (
            table.primary_key.name,
            tuple(table.primary_key.columns.keys()),
        ) == migration._PRIMARY_KEYS[table_name]

        actual_foreign_keys = {}
        actual_uniques = {}
        actual_checks = {}
        for constraint in table.constraints:
            if isinstance(constraint, sa.ForeignKeyConstraint):
                targets = [element.target_fullname.rsplit(".", 1) for element in constraint.elements]
                actual_foreign_keys[constraint.name] = (
                    tuple(element.parent.name for element in constraint.elements),
                    targets[0][0],
                    tuple(target[1] for target in targets),
                    migration._normalized_ondelete(constraint.ondelete),
                )
            elif isinstance(constraint, sa.UniqueConstraint):
                actual_uniques[constraint.name] = tuple(constraint.columns.keys())
            elif isinstance(constraint, sa.CheckConstraint):
                actual_checks[constraint.name] = migration._canonical_sql(constraint.sqltext)

        assert actual_foreign_keys == migration._FOREIGN_KEYS[table_name]
        assert actual_uniques == migration._UNIQUE_CONSTRAINTS[table_name]
        assert actual_checks == {
            name: migration._canonical_sql(expression)
            for name, expression in migration._CHECK_CONSTRAINTS[table_name].items()
        }


def test_current_runtime_metadata_is_check_first_noop(monkeypatch) -> None:
    migration = _load_migration()
    tables, indexes, statements = _install_upgrade_doubles(
        monkeypatch, migration, MetadataInspector()
    )

    migration.upgrade()

    assert tables == []
    assert indexes == []
    assert statements == [migration._CREATE_CHECKPOINT_SCHEMA_SQL]


def test_subset_of_existing_tables_is_validated_and_missing_tables_are_created(monkeypatch) -> None:
    migration = _load_migration()
    inspector = MetadataInspector(table_names={"agent_runs", "agent_run_events"})
    tables, indexes, _statements = _install_upgrade_doubles(
        monkeypatch, migration, inspector
    )

    migration.upgrade()

    assert [name for name, _elements in tables] == [
        "agent_run_commands",
        "agent_tool_executions",
        "session_context_states",
    ]
    created_index_names = {name for name, *_rest in indexes}
    assert created_index_names == {
        name
        for name, spec in migration._INDEXES.items()
        if spec.table_name in {
            "agent_run_commands",
            "agent_tool_executions",
            "session_context_states",
        }
    }


def test_missing_index_is_repaired_without_other_writes(monkeypatch) -> None:
    migration = _load_migration()
    missing = "uq_agent_runs_source_execution"
    tables, indexes, _statements = _install_upgrade_doubles(
        monkeypatch,
        migration,
        MetadataInspector(missing_indexes={missing}),
    )

    migration.upgrade()

    assert tables == []
    assert [name for name, *_rest in indexes] == [missing]
    assert str(indexes[0][4]) == "source_execution_id IS NOT NULL"


@pytest.mark.parametrize(
    "broken_schema",
    [
        "column_type",
        "column_nullable",
        "column_default",
        "primary_key",
        "foreign_key_columns",
        "foreign_key_ondelete",
        "unique_constraint",
        "check_constraint",
        "index_columns",
        "index_unique",
        "index_predicate",
        "index_sorting",
    ],
)
def test_incompatible_existing_runtime_schema_is_rejected(monkeypatch, broken_schema: str) -> None:
    migration = _load_migration()
    inspector = MetadataInspector()
    run_columns = {column["name"]: column for column in inspector.columns["agent_runs"]}
    run_foreign_keys = {
        constraint["name"]: constraint
        for constraint in inspector.foreign_keys["agent_runs"]
    }
    run_uniques = {
        constraint["name"]: constraint
        for constraint in inspector.unique_constraints["agent_runs"]
    }
    run_checks = {
        constraint["name"]: constraint
        for constraint in inspector.check_constraints["agent_runs"]
    }
    run_indexes = {
        index["name"]: index for index in inspector.indexes["agent_runs"]
    }

    if broken_schema == "column_type":
        run_columns["runtime_thread_id"]["type"] = sa.String(100)
    elif broken_schema == "column_nullable":
        run_columns["runtime_thread_id"]["nullable"] = True
    elif broken_schema == "column_default":
        run_columns["lane_held"]["default"] = "true"
    elif broken_schema == "primary_key":
        inspector.primary_keys["agent_runs"]["constrained_columns"] = ["tenant_id"]
    elif broken_schema == "foreign_key_columns":
        run_foreign_keys["fk_agent_runs_tenant_session_chat_sessions"][
            "constrained_columns"
        ] = ["session_id"]
    elif broken_schema == "foreign_key_ondelete":
        run_foreign_keys["fk_agent_runs_session_id_chat_sessions"]["options"] = {
            "ondelete": "CASCADE"
        }
    elif broken_schema == "unique_constraint":
        run_uniques["uq_agent_runs_tenant_id_id"]["column_names"] = ["id"]
    elif broken_schema == "check_constraint":
        run_checks["ck_agent_runs_runtime_type"]["sqltext"] = (
            "runtime_type IN ('legacy', 'langgraph', 'other')"
        )
    elif broken_schema == "index_columns":
        run_indexes["ix_agent_runs_source"]["column_names"] = ["source_type"]
    elif broken_schema == "index_unique":
        run_indexes["ix_agent_runs_source"]["unique"] = True
    elif broken_schema == "index_predicate":
        run_indexes["uq_agent_runs_source_execution"]["dialect_options"] = {
            "postgresql_where": "source_execution_id IS NULL"
        }
    elif broken_schema == "index_sorting":
        run_indexes["ix_agent_runs_session_created_at"]["column_sorting"] = {}

    monkeypatch.setattr(migration.op, "get_bind", lambda: object())
    monkeypatch.setattr(migration.sa, "inspect", lambda _bind: inspector)
    monkeypatch.setattr(migration.op, "execute", lambda _statement: None)

    with pytest.raises(RuntimeError):
        migration.upgrade()


def test_postgres_casted_enum_check_normalizes_to_declared_expression() -> None:
    migration = _load_migration()
    postgres_expression = (
        "(runtime_type)::text = ANY "
        "(ARRAY['legacy'::character varying, 'langgraph'::character varying]::text[])"
    )

    assert migration._canonical_sql(postgres_expression) == migration._canonical_sql(
        "runtime_type IN ('legacy', 'langgraph')"
    )


class _Result:
    def __init__(self, row: tuple[int] | None = None) -> None:
        self.row = row

    def first(self):
        return self.row


class _Connection:
    def __init__(self, *, nonempty_table: str | None = None) -> None:
        self.nonempty_table = nonempty_table
        self.statements: list[str] = []

    def execute(self, statement):
        sql = str(statement)
        self.statements.append(sql)
        if sql == f'SELECT 1 FROM "{self.nonempty_table}" LIMIT 1':
            return _Result((1,))
        return _Result()


class _RuntimeTableInspector:
    @staticmethod
    def get_table_names() -> list[str]:
        return [table.name for table in RUNTIME_TABLES]


def test_downgrade_locks_and_drops_dependants_before_agent_runs(monkeypatch) -> None:
    migration = _load_migration()
    connection = _Connection()
    dropped: list[str] = []
    statements: list[str] = []
    monkeypatch.setattr(migration.op, "get_bind", lambda: connection)
    monkeypatch.setattr(migration.sa, "inspect", lambda _bind: _RuntimeTableInspector())
    monkeypatch.setattr(migration.op, "drop_table", dropped.append)
    monkeypatch.setattr(
        migration.op,
        "execute",
        lambda statement: statements.append(str(statement)),
    )

    migration.downgrade()

    assert dropped == [
        "agent_run_commands",
        "agent_run_events",
        "agent_tool_executions",
        "session_context_states",
        "agent_runs",
    ]
    assert connection.statements[0] == (
        'LOCK TABLE "agent_run_commands", "agent_run_events", '
        '"agent_tool_executions", "session_context_states", "agent_runs" '
        "IN ACCESS EXCLUSIVE MODE"
    )
    assert statements == [migration._DROP_CHECKPOINT_SCHEMA_SQL]


@pytest.mark.parametrize("nonempty_table", [table.name for table in RUNTIME_TABLES])
def test_downgrade_refuses_any_nonempty_runtime_table(monkeypatch, nonempty_table: str) -> None:
    migration = _load_migration()
    connection = _Connection(nonempty_table=nonempty_table)
    dropped: list[str] = []
    monkeypatch.setattr(migration.op, "get_bind", lambda: connection)
    monkeypatch.setattr(migration.sa, "inspect", lambda _bind: _RuntimeTableInspector())
    monkeypatch.setattr(migration.op, "drop_table", dropped.append)

    with pytest.raises(RuntimeError, match=f"{nonempty_table} contains data"):
        migration.downgrade()

    assert dropped == []
