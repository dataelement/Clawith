"""Static contract for the one upstream-main based schema migration."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import re

import pytest
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# Import every model whose table is created by the unified revision.  The
# migration is required to describe the current ORM schema directly, rather
# than replaying branch-local intermediate shapes.
from app.models.agent_run import AgentRun
from app.models.agent_run_command import AgentRunCommand
from app.models.agent_run_event import AgentRunEvent
from app.models.agent_tool_execution import AgentToolExecution
from app.models.audit import ChatMessage
from app.models.channel_delivery import ChannelDelivery
from app.models.chat_session import ChatSession
from app.models.experience import ExperienceEntry
from app.models.experience_reference import ExperienceReference
from app.models.gateway_message import GatewayMessage
from app.models.group import Group, GroupMember
from app.models.llm import LLMModel
from app.models.notification import Notification
from app.models.session_context_state import SessionContextState
from app.models.tenant_setting import TenantSetting
from app.models.trigger_execution import TriggerExecution
from app.models.workspace import WorkspaceEditLock, WorkspaceFileRevision


VERSIONS_DIR = Path(__file__).resolve().parents[1] / "alembic" / "versions"
MIGRATION_PATH = VERSIONS_DIR / "202607161200_unify_runtime_group_schema.py"
LEGACY_BRANCH_REVISIONS = {
    "060_agent_directory_indexes.py",
    "060_experience_library.py",
    "061_add_retired_at_to_experience.py",
    "062_experience_markdown_body.py",
    "202607131843_create_group_domain_schema.py",
    "202607131910_unify_chat_schema.py",
    "202607131920_add_llm_runtime_capabilities.py",
    "202607131930_create_agent_runtime_schema.py",
    "202607141430_add_group_workspace_scope.py",
    "202607141500_create_channel_delivery_outbox.py",
    "202607141530_add_chat_message_cursor_index.py",
    "202607141600_add_tenant_planning_model.py",
    "202607151730_merge_directory_experience_runtime_heads.py",
}

EXPECTED_UPGRADE_PHASES = (
    "directory_indexes",
    "baseline_orm_tables",
    "experience_library",
    "group_domain",
    "unified_chat",
    "llm_capabilities",
    "runtime_schema",
    "group_workspace_scope",
    "channel_delivery_outbox",
    "chat_message_cursor",
    "remove_template_bootstrap",
)

BASELINE_MODEL_TABLES = (
    GatewayMessage.__table__,
    Notification.__table__,
    TenantSetting.__table__,
    TriggerExecution.__table__,
)

CREATED_MODEL_TABLES = {
    table.name: table
    for table in (
        *BASELINE_MODEL_TABLES,
        ExperienceEntry.__table__,
        ExperienceReference.__table__,
        Group.__table__,
        GroupMember.__table__,
        AgentRun.__table__,
        AgentRunCommand.__table__,
        AgentRunEvent.__table__,
        AgentToolExecution.__table__,
        SessionContextState.__table__,
        ChannelDelivery.__table__,
    )
}
DURABLE_GUARDED_TABLES = (
    "agent_runs",
    "agent_run_commands",
    "agent_run_events",
    "agent_tool_executions",
    "session_context_states",
    "groups",
    "group_members",
)


def _load_migration():
    spec = importlib.util.spec_from_file_location(
        "unified_runtime_group_migration",
        MIGRATION_PATH,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _canonical_sql(value: object, *, table_name: str) -> str:
    sql = str(value).lower()
    sql = re.sub(rf'(?<![\w])"?{re.escape(table_name)}"?\.', "", sql)
    return " ".join(sql.split())


def _canonical_default(column: sa.Column) -> str | None:
    if column.server_default is None:
        return None
    value = str(column.server_default.arg).strip()
    if len(value) >= 2 and value.startswith("'") and value.endswith("'"):
        value = value[1:-1]
    return " ".join(value.lower().split())


def _column_signature(column: sa.Column) -> tuple[str, bool, str | None]:
    return (
        str(column.type.compile(dialect=postgresql.dialect())).lower(),
        bool(column.nullable),
        _canonical_default(column),
    )


def _constraint_signatures(table: sa.Table) -> dict[str, set[tuple[object, ...]]]:
    signatures: dict[str, set[tuple[object, ...]]] = {
        "foreign_keys": set(),
        "uniques": set(),
        "checks": set(),
    }
    for constraint in table.constraints:
        if isinstance(constraint, sa.ForeignKeyConstraint):
            signatures["foreign_keys"].add(
                (
                    constraint.name,
                    tuple(element.parent.name for element in constraint.elements),
                    tuple(element.target_fullname for element in constraint.elements),
                    constraint.ondelete,
                )
            )
        elif isinstance(constraint, sa.UniqueConstraint):
            signatures["uniques"].add(
                (constraint.name, tuple(constraint.columns.keys()))
            )
        elif isinstance(constraint, sa.CheckConstraint):
            signatures["checks"].add(
                (
                    constraint.name,
                    _canonical_sql(constraint.sqltext, table_name=table.name),
                )
            )
    return signatures


def _model_index_signatures(table: sa.Table) -> set[tuple[object, ...]]:
    signatures: set[tuple[object, ...]] = set()
    for index in table.indexes:
        where = index.dialect_options["postgresql"].get("where")
        signatures.add(
            (
                index.name,
                tuple(
                    _canonical_sql(expression, table_name=table.name)
                    for expression in index.expressions
                ),
                bool(index.unique),
                (
                    _canonical_sql(where, table_name=table.name)
                    if where is not None
                    else None
                ),
            )
        )
    return signatures


def _capture_created_schema(monkeypatch, migration):
    tables: dict[str, tuple[object, ...]] = {}
    indexes: dict[str, set[tuple[object, ...]]] = {}

    monkeypatch.setattr(migration.op, "execute", lambda _statement: None)
    monkeypatch.setattr(migration.op, "get_bind", lambda: object())
    monkeypatch.setattr(migration, "_schema_object_names", lambda _bind: set())
    monkeypatch.setattr(
        migration.op,
        "create_table",
        lambda name, *elements, **_kwargs: tables.setdefault(name, elements),
    )

    def record_index(name, table_name, columns, unique=False, **kwargs):
        indexes.setdefault(table_name, set()).add(
            (
                name,
                tuple(
                    _canonical_sql(column, table_name=table_name)
                    for column in columns
                ),
                bool(unique),
                (
                    _canonical_sql(
                        kwargs["postgresql_where"],
                        table_name=table_name,
                    )
                    if kwargs.get("postgresql_where") is not None
                    else None
                ),
            )
        )

    monkeypatch.setattr(migration.op, "create_index", record_index)
    migration._upgrade_baseline_orm_tables()
    migration._upgrade_experience_library()
    migration._upgrade_group_domain()
    migration._upgrade_runtime_schema()
    migration._upgrade_channel_delivery_outbox()
    return tables, indexes


class _ZeroScalarResult:
    def __init__(self, value: int = 0) -> None:
        self.value = value

    def scalar_one(self) -> int:
        return self.value


class _RecordingBind:
    def __init__(self, counts: list[int] | None = None) -> None:
        self.statements: list[str] = []
        self.counts = list(counts or [])

    def execute(self, statement):
        sql = str(statement)
        self.statements.append(sql)
        normalized = " ".join(sql.split()).upper()
        value = (
            self.counts.pop(0)
            if normalized.startswith("SELECT COUNT") and self.counts
            else 0
        )
        return _ZeroScalarResult(value)


class _ProbeResult:
    def __init__(self, populated: bool = False) -> None:
        self.populated = populated

    def first(self) -> tuple[int] | None:
        return (1,) if self.populated else None


class _TableProbeBind:
    def __init__(self, populated_table: str) -> None:
        self.populated_table = populated_table
        self.statements: list[str] = []

    def execute(self, statement):
        sql = str(statement)
        self.statements.append(sql)
        return _ProbeResult(
            sql == f'SELECT 1 FROM "{self.populated_table}" LIMIT 1'
        )


def test_one_revision_replaces_all_branch_only_revisions() -> None:
    migration = _load_migration()

    assert migration.revision == "unify_runtime_group_schema"
    assert migration.down_revision == "add_title_to_agent_focus_items"
    assert migration.UPGRADE_PHASES == EXPECTED_UPGRADE_PHASES
    assert migration.DOWNGRADE_PHASES == tuple(reversed(EXPECTED_UPGRADE_PHASES))
    assert all(not (VERSIONS_DIR / name).exists() for name in LEGACY_BRANCH_REVISIONS)


def test_final_runtime_shape_is_declared_directly() -> None:
    migration = _load_migration()
    run_columns = migration.RUNTIME_COLUMNS["agent_runs"]
    tool_columns = migration.RUNTIME_COLUMNS["agent_tool_executions"]

    assert "model_turn_limit" in run_columns
    assert "runtime_thread_id" in run_columns
    assert not any(name.startswith("projected_") for name in run_columns)
    assert "projection_checkpoint_id" not in run_columns
    assert "projection_updated_at" not in run_columns
    assert "uq_agent_runs_runtime_thread_id" not in migration.RUNTIME_UNIQUES["agent_runs"]
    assert migration.RUNTIME_INDEXES["ix_agent_runs_tenant_thread_created_at"] == (
        "agent_runs",
        ("tenant_id", "runtime_thread_id", "created_at", "id"),
    )

    assert {
        "effect",
        "retry_policy",
        "attempt_count",
        "result_metadata",
    }.issubset(tool_columns)
    assert migration.RUNTIME_CHECKS["agent_tool_executions"] == {
        "ck_agent_tool_executions_status": (
            "status IN ('started', 'succeeded', 'failed', 'unknown')"
        ),
        "ck_agent_tool_executions_effect": (
            "effect IN ('read', 'write', 'external_write')"
        ),
        "ck_agent_tool_executions_retry_policy": (
            "retry_policy IN ('safe', 'conditional', 'never')"
        ),
        "ck_agent_tool_executions_attempt_count": "attempt_count >= 1",
    }
    assert not {
        "agent_run_projections",
        "agent_run_execution_jobs",
        "tool_results",
    }.intersection(migration.RUNTIME_TABLES)


def test_directory_and_chat_cursor_indexes_are_preserved(monkeypatch) -> None:
    migration = _load_migration()
    directory_index_names = tuple(
        statement.split(" ", 3)[2] for statement in migration._DIRECTORY_INDEX_SQL
    )
    assert directory_index_names == (
        "ix_agents_tenant_access_status_name",
        "ix_agents_tenant_creator_access",
        "ix_agent_permissions_agent_scope_scopeid_level",
        "ix_agent_permissions_scopeid_scope_agent",
        "ix_agent_agent_relationships_agent_target",
        "ix_org_members_tenant_status_name",
        "ix_org_members_tenant_user",
    )

    indexes: list[tuple[str, str, tuple[str, ...], bool]] = []
    monkeypatch.setattr(
        migration.op,
        "create_index",
        lambda name, table_name, columns, unique=False, **_kwargs: indexes.append(
            (name, table_name, tuple(columns), bool(unique))
        ),
    )
    migration._upgrade_chat_message_cursor()

    assert indexes == [
        (
            "ix_chat_messages_conversation_created_id",
            "chat_messages",
            ("conversation_id", "created_at", "id"),
            False,
        )
    ]


def test_every_created_table_matches_current_orm_metadata(monkeypatch) -> None:
    migration = _load_migration()
    created, created_indexes = _capture_created_schema(monkeypatch, migration)

    assert set(created) == set(CREATED_MODEL_TABLES)
    for table_name, model_table in CREATED_MODEL_TABLES.items():
        migration_table = sa.Table(
            table_name,
            sa.MetaData(),
            *created[table_name],
        )
        assert {
            column.name: _column_signature(column)
            for column in migration_table.columns
        } == {
            column.name: _column_signature(column)
            for column in model_table.columns
        }
        assert (
            migration_table.primary_key.name,
            tuple(migration_table.primary_key.columns.keys()),
        ) == (
            model_table.primary_key.name,
            tuple(model_table.primary_key.columns.keys()),
        )
        assert _constraint_signatures(migration_table) == _constraint_signatures(
            model_table
        )
        assert created_indexes.get(table_name, set()) == _model_index_signatures(
            model_table
        )


def test_unified_chat_phase_matches_final_models_and_runs_audits_first(
    monkeypatch,
) -> None:
    migration = _load_migration()
    bind = _RecordingBind()
    added: dict[tuple[str, str], sa.Column] = {}
    altered: dict[tuple[str, str], dict[str, object]] = {}
    foreign_keys: dict[str, tuple[object, ...]] = {}
    uniques: dict[str, tuple[str, ...]] = {}
    checks: dict[str, str] = {}
    indexes: dict[str, tuple[object, ...]] = {}
    dropped_indexes: list[str] = []

    monkeypatch.setattr(migration.op, "get_bind", lambda: bind)
    monkeypatch.setattr(
        migration.op,
        "add_column",
        lambda table_name, column: added.setdefault(
            (table_name, column.name), column
        ),
    )
    monkeypatch.setattr(
        migration.op,
        "alter_column",
        lambda table_name, column_name, **kwargs: altered.setdefault(
            (table_name, column_name), kwargs
        ),
    )
    monkeypatch.setattr(
        migration.op,
        "create_foreign_key",
        lambda name, source, target, local, remote, **kwargs: foreign_keys.setdefault(
            name,
            (source, target, tuple(local), tuple(remote), kwargs.get("ondelete")),
        ),
    )
    monkeypatch.setattr(
        migration.op,
        "create_unique_constraint",
        lambda name, _table, columns: uniques.setdefault(name, tuple(columns)),
    )
    monkeypatch.setattr(
        migration.op,
        "create_check_constraint",
        lambda name, table_name, expression: checks.setdefault(
            name,
            _canonical_sql(expression, table_name=table_name),
        ),
    )
    monkeypatch.setattr(
        migration.op,
        "drop_index",
        lambda name, **_kwargs: dropped_indexes.append(name),
    )

    def record_index(name, table_name, columns, unique=False, **kwargs):
        indexes[name] = (
            table_name,
            tuple(columns),
            bool(unique),
            (
                _canonical_sql(kwargs["postgresql_where"], table_name=table_name)
                if kwargs.get("postgresql_where") is not None
                else None
            ),
        )

    monkeypatch.setattr(migration.op, "create_index", record_index)
    migration._upgrade_unified_chat()

    session_model = ChatSession.__table__
    message_model = ChatMessage.__table__
    for column_name in (
        "tenant_id",
        "session_type",
        "group_id",
        "created_by_participant_id",
        "deleted_at",
        "updated_at",
    ):
        migration_column = added[("chat_sessions", column_name)]
        model_column = session_model.c[column_name]
        assert str(
            migration_column.type.compile(dialect=postgresql.dialect())
        ).lower() == str(
            model_column.type.compile(dialect=postgresql.dialect())
        ).lower()
    assert _column_signature(added[("chat_messages", "mentions")])[:1] == (
        _column_signature(message_model.c.mentions)[0],
    )

    for table_name, model_table, column_name in (
        ("chat_sessions", session_model, "tenant_id"),
        ("chat_sessions", session_model, "session_type"),
        ("chat_sessions", session_model, "agent_id"),
        ("chat_sessions", session_model, "user_id"),
        ("chat_sessions", session_model, "updated_at"),
        ("chat_messages", message_model, "agent_id"),
        ("chat_messages", message_model, "user_id"),
        ("chat_messages", message_model, "mentions"),
    ):
        assert altered[(table_name, column_name)]["nullable"] is model_table.c[
            column_name
        ].nullable

    assert set(foreign_keys) == {
        "fk_chat_sessions_tenant_id_tenants",
        "fk_chat_sessions_group_id_groups",
        "fk_chat_sessions_created_by_participant_id_participants",
    }
    assert uniques == {"uq_chat_sessions_tenant_id_id": ("tenant_id", "id")}
    assert checks == {
        "ck_chat_sessions_session_type": (
            "session_type in ('direct', 'group', 'a2a', 'trigger')"
        )
    }
    assert set(indexes) == {
        "ix_chat_sessions_tenant_id",
        "ix_chat_sessions_group_id",
        "uq_chat_sessions_primary_direct",
        "uq_chat_sessions_primary_group",
    }
    assert dropped_indexes == ["uq_chat_sessions_primary_platform"]
    assert bind.statements[0] == (
        "LOCK TABLE chat_sessions, chat_messages IN ACCESS EXCLUSIVE MODE"
    )
    first_update = next(
        index
        for index, statement in enumerate(bind.statements)
        if statement.lstrip().upper().startswith("UPDATE")
    )
    assert first_update == 4


def test_llm_and_workspace_alterations_match_current_models(monkeypatch) -> None:
    migration = _load_migration()
    added: dict[tuple[str, str], sa.Column] = {}
    altered: dict[tuple[str, str], dict[str, object]] = {}
    checks: dict[tuple[str, str], str] = {}
    uniques: dict[str, tuple[str, ...]] = {}
    indexes: dict[str, tuple[str, tuple[str, ...], bool]] = {}
    statements: list[str] = []

    monkeypatch.setattr(
        migration.op,
        "add_column",
        lambda table_name, column: added.setdefault(
            (table_name, column.name), column
        ),
    )
    monkeypatch.setattr(
        migration.op,
        "alter_column",
        lambda table_name, column_name, **kwargs: altered.setdefault(
            (table_name, column_name), kwargs
        ),
    )
    monkeypatch.setattr(
        migration.op,
        "create_check_constraint",
        lambda name, table_name, expression: checks.setdefault(
            (table_name, name),
            _canonical_sql(expression, table_name=table_name),
        ),
    )
    monkeypatch.setattr(
        migration.op,
        "create_unique_constraint",
        lambda name, _table, columns: uniques.setdefault(name, tuple(columns)),
    )
    monkeypatch.setattr(
        migration.op,
        "create_index",
        lambda name, table_name, columns, unique=False, **_kwargs: indexes.setdefault(
            name,
            (table_name, tuple(columns), bool(unique)),
        ),
    )
    monkeypatch.setattr(migration.op, "execute", lambda statement: statements.append(str(statement)))
    monkeypatch.setattr(migration.op, "drop_constraint", lambda *_args, **_kwargs: None)

    migration._upgrade_llm_capabilities()
    migration._upgrade_group_workspace_scope()

    assert set(migration._LEGACY_TOOL_CALLING_PROVIDERS).isdisjoint(
        {"ollama", "vllm", "sglang", "custom"}
    )
    assert {
        "anthropic",
        "openai",
        "openai-response",
        "openai_response",
        "openairesponses",
        "azure",
        "deepseek",
        "qwen",
        "minimax",
        "openrouter",
        "zhipu",
        "baidu",
        "gemini",
        "kimi",
    } == set(migration._LEGACY_TOOL_CALLING_PROVIDERS)
    assert any(
        "UPDATE llm_models" in statement
        and "tool_calling_capability_source" in statement
        and "builtin_registry" in statement
        for statement in statements
    )

    llm_table = LLMModel.__table__
    assert set(migration._LLM_CAPABILITY_COLUMNS) == {
        column_name
        for table_name, column_name in added
        if table_name == "llm_models"
    }
    for column_name in migration._LLM_CAPABILITY_COLUMNS:
        assert _column_signature(added[("llm_models", column_name)]) == (
            _column_signature(llm_table.c[column_name])
        )
    assert {
        name: expression
        for (table_name, name), expression in checks.items()
        if table_name == "llm_models"
    } == {
        constraint.name: _canonical_sql(
            constraint.sqltext,
            table_name="llm_models",
        )
        for constraint in llm_table.constraints
        if isinstance(constraint, sa.CheckConstraint)
    }

    for model_table in (
        WorkspaceFileRevision.__table__,
        WorkspaceEditLock.__table__,
    ):
        for column_name in ("scope_type", "scope_id"):
            migration_column = added[(model_table.name, column_name)]
            model_column = model_table.c[column_name]
            assert str(
                migration_column.type.compile(dialect=postgresql.dialect())
            ).lower() == str(
                model_column.type.compile(dialect=postgresql.dialect())
            ).lower()
            assert altered[(model_table.name, column_name)]["nullable"] is False
        assert altered[(model_table.name, "agent_id")]["nullable"] is True
        assert {
            name: expression
            for (table_name, name), expression in checks.items()
            if table_name == model_table.name
        } == {
            constraint.name: _canonical_sql(
                constraint.sqltext,
                table_name=model_table.name,
            )
            for constraint in model_table.constraints
            if isinstance(constraint, sa.CheckConstraint)
        }

    assert uniques == {
        "uq_workspace_edit_locks_scope_path": (
            "scope_type",
            "scope_id",
            "path",
        )
    }
    assert indexes == {
        "ix_workspace_file_revisions_scope_path": (
            "workspace_file_revisions",
            ("scope_type", "scope_id", "path"),
            False,
        )
    }
    assert statements[-2:] == [
        "UPDATE workspace_file_revisions SET scope_type = 'agent', "
        "scope_id = agent_id WHERE scope_type IS NULL OR scope_id IS NULL",
        "UPDATE workspace_edit_locks SET scope_type = 'agent', "
        "scope_id = agent_id WHERE scope_type IS NULL OR scope_id IS NULL",
    ]


def test_upgrade_and_downgrade_use_exact_inverse_phase_order(monkeypatch) -> None:
    migration = _load_migration()
    calls: list[str] = []

    monkeypatch.setattr(
        migration,
        "_run_phase",
        lambda phase, *, downgrade: calls.append(
            f"{'down' if downgrade else 'up'}:{phase}"
        ),
    )

    migration.upgrade()
    migration.downgrade()

    assert calls == [
        *(f"up:{phase}" for phase in EXPECTED_UPGRADE_PHASES),
        *(f"down:{phase}" for phase in reversed(EXPECTED_UPGRADE_PHASES)),
    ]


@pytest.mark.parametrize(
    "phase",
    (
        "experience_library",
        "group_domain",
        "unified_chat",
        "llm_capabilities",
        "runtime_schema",
    ),
)
def test_fresh_metadata_precreation_is_all_or_nothing(phase: str) -> None:
    migration = _load_migration()
    expected = migration._PRECREATED_PHASE_OBJECTS[phase]

    assert migration._precreated_phase_state(phase, set()) is False
    assert migration._precreated_phase_state(phase, set(expected)) is True

    with pytest.raises(RuntimeError, match=f"partially precreated {phase}"):
        migration._precreated_phase_state(phase, {next(iter(expected))})


@pytest.mark.parametrize(
    ("table_name", "complete_action"),
    (
        ("gateway_messages", "keep"),
        ("notifications", "normalize_notifications"),
        ("tenant_settings", "keep"),
        ("trigger_executions", "keep"),
    ),
)
def test_baseline_orm_tables_are_classified_independently(
    table_name: str,
    complete_action: str,
) -> None:
    migration = _load_migration()
    expected = migration._BASELINE_ORM_TABLE_OBJECTS[table_name]

    assert migration._baseline_orm_table_plan(table_name, set()) == ("create", ())
    assert migration._baseline_orm_table_plan(table_name, set(expected)) == (
        complete_action,
        (),
    )

    with pytest.raises(
        RuntimeError,
        match=f"unknown partial baseline ORM table {table_name}",
    ):
        migration._baseline_orm_table_plan(
            table_name,
            {f"table:{table_name}"},
        )


def test_baseline_orm_upgrade_keeps_complete_tables_and_creates_missing_ones(
    monkeypatch,
) -> None:
    migration = _load_migration()
    preserved = "tenant_settings"
    actual = set(migration._BASELINE_ORM_TABLE_OBJECTS[preserved])
    created: list[str] = []

    monkeypatch.setattr(migration.op, "get_bind", lambda: object())
    monkeypatch.setattr(migration, "_schema_object_names", lambda _bind: actual)
    monkeypatch.setattr(
        migration,
        "_BASELINE_ORM_CREATE",
        {
            table_name: (lambda name=table_name: created.append(name))
            for table_name in migration.BASELINE_ORM_TABLES
        },
    )

    migration._upgrade_baseline_orm_tables()

    assert created == [
        table_name
        for table_name in migration.BASELINE_ORM_TABLES
        if table_name != preserved
    ]


def test_gateway_messages_known_legacy_shape_adds_conversation_id(
    monkeypatch,
) -> None:
    migration = _load_migration()
    actual = set(migration._GATEWAY_MESSAGES_LEGACY_OBJECTS)
    for table_name in migration.BASELINE_ORM_TABLES:
        if table_name != "gateway_messages":
            actual.update(migration._BASELINE_ORM_TABLE_OBJECTS[table_name])
    added: list[tuple[str, sa.Column]] = []

    monkeypatch.setattr(migration.op, "get_bind", lambda: object())
    monkeypatch.setattr(migration, "_schema_object_names", lambda _bind: actual)
    monkeypatch.setattr(
        migration.op,
        "add_column",
        lambda table_name, column: added.append((table_name, column)),
    )
    monkeypatch.setattr(migration.op, "alter_column", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        migration,
        "_BASELINE_ORM_CREATE",
        {
            table_name: lambda: pytest.fail("known tables must not be recreated")
            for table_name in migration.BASELINE_ORM_TABLES
        },
    )

    migration._upgrade_baseline_orm_tables()

    assert [(table_name, column.name) for table_name, column in added] == [
        ("gateway_messages", "conversation_id")
    ]
    assert added[0][1].nullable is True
    assert str(added[0][1].type) == "VARCHAR(100)"


def test_notifications_original_shape_gets_lossless_016_repair(
    monkeypatch,
) -> None:
    migration = _load_migration()
    actual: set[str] = set()
    for table_name in migration.BASELINE_ORM_TABLES:
        actual.update(migration._BASELINE_ORM_TABLE_OBJECTS[table_name])
    actual.difference_update(migration._NOTIFICATION_EXTENSION_OBJECTS)
    added: list[tuple[str, sa.Column]] = []
    altered: dict[tuple[str, str], dict[str, object]] = {}
    foreign_keys: dict[str, tuple[object, ...]] = {}
    indexes: dict[str, tuple[object, ...]] = {}

    monkeypatch.setattr(migration.op, "get_bind", lambda: object())
    monkeypatch.setattr(migration, "_schema_object_names", lambda _bind: actual)
    monkeypatch.setattr(
        migration.op,
        "add_column",
        lambda table_name, column: added.append((table_name, column)),
    )
    monkeypatch.setattr(
        migration.op,
        "alter_column",
        lambda table_name, column_name, **kwargs: altered.setdefault(
            (table_name, column_name), kwargs
        ),
    )
    monkeypatch.setattr(
        migration.op,
        "create_foreign_key",
        lambda name, source, target, local, remote, **kwargs: foreign_keys.setdefault(
            name,
            (source, target, tuple(local), tuple(remote), kwargs),
        ),
    )

    def record_index(name, table_name, columns, unique=False, **kwargs):
        indexes[name] = (
            table_name,
            tuple(columns),
            bool(unique),
            (
                _canonical_sql(kwargs["postgresql_where"], table_name=table_name)
                if kwargs.get("postgresql_where") is not None
                else None
            ),
        )

    monkeypatch.setattr(migration.op, "create_index", record_index)

    migration._upgrade_baseline_orm_tables()

    assert [(table_name, column.name) for table_name, column in added] == [
        ("notifications", "agent_id"),
        ("notifications", "sender_name"),
    ]
    assert altered[("notifications", "user_id")]["nullable"] is True
    assert foreign_keys["notifications_agent_id_fkey"][:4] == (
        "notifications",
        "agents",
        ("agent_id",),
        ("id",),
    )
    assert indexes == {
        "ix_notifications_agent_id": (
            "notifications",
            ("agent_id",),
            False,
            "agent_id is not null",
        )
    }


def test_trigger_executions_missing_safe_index_is_repaired(monkeypatch) -> None:
    migration = _load_migration()
    actual: set[str] = set()
    for table_name in migration.BASELINE_ORM_TABLES:
        actual.update(migration._BASELINE_ORM_TABLE_OBJECTS[table_name])
    missing_index = "ix_trigger_executions_status_scheduled"
    actual.remove(f"index:{missing_index}")
    indexes: list[tuple[str, str, tuple[str, ...]]] = []

    monkeypatch.setattr(migration.op, "get_bind", lambda: object())
    monkeypatch.setattr(migration, "_schema_object_names", lambda _bind: actual)
    monkeypatch.setattr(migration.op, "alter_column", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        migration.op,
        "create_index",
        lambda name, table_name, columns, **_kwargs: indexes.append(
            (name, table_name, tuple(columns))
        ),
    )

    migration._upgrade_baseline_orm_tables()

    assert indexes == [
        (
            missing_index,
            "trigger_executions",
            ("status", "scheduled_at"),
        )
    ]


def test_baseline_orm_partial_table_fails_before_any_ddl(monkeypatch) -> None:
    migration = _load_migration()
    # Put the invalid table last so the assertion proves the first three
    # create plans were classified, but no DDL ran before global preflight.
    partial_table = "trigger_executions"
    created: list[str] = []

    monkeypatch.setattr(migration.op, "get_bind", lambda: object())
    monkeypatch.setattr(
        migration,
        "_schema_object_names",
        lambda _bind: {f"table:{partial_table}"},
    )
    monkeypatch.setattr(
        migration,
        "_BASELINE_ORM_CREATE",
        {
            table_name: (lambda name=table_name: created.append(name))
            for table_name in migration.BASELINE_ORM_TABLES
        },
    )

    with pytest.raises(
        RuntimeError,
        match=f"unknown partial baseline ORM table {partial_table}",
    ):
        migration._upgrade_baseline_orm_tables()

    assert created == []


def test_baseline_orm_downgrade_never_deletes_historical_tables(
    monkeypatch,
) -> None:
    migration = _load_migration()
    monkeypatch.setattr(
        migration.op,
        "drop_table",
        lambda _name: pytest.fail("baseline production tables must be preserved"),
    )

    migration._downgrade_baseline_orm_tables()


def test_fresh_metadata_phase_skips_duplicate_ddl_and_runs_reconciliation(
    monkeypatch,
) -> None:
    migration = _load_migration()
    bind = object()
    reconciled: list[tuple[str, object]] = []

    monkeypatch.setattr(migration.op, "get_bind", lambda: bind)
    monkeypatch.setattr(
        migration,
        "_schema_object_names",
        lambda _bind: set(migration._PRECREATED_PHASE_OBJECTS["unified_chat"]),
    )
    monkeypatch.setattr(
        migration,
        "_finish_precreated_phase",
        lambda phase, phase_bind: reconciled.append((phase, phase_bind)),
    )
    monkeypatch.setattr(
        migration,
        "_upgrade_unified_chat",
        lambda: pytest.fail("duplicate unified-chat DDL must be skipped"),
    )

    migration._run_phase("unified_chat", downgrade=False)

    assert reconciled == [("unified_chat", bind)]


def test_chat_backfill_and_downgrade_audits_remain_fail_closed() -> None:
    migration = _load_migration()

    assert "LOCK TABLE chat_sessions, chat_messages IN ACCESS EXCLUSIVE MODE" in (
        migration.UNIFIED_CHAT_UPGRADE_SQL
    )
    assert "Agent and User tenants disagree" in migration.UNIFIED_CHAT_AUDIT_MESSAGES
    assert "messages contain mentions" in migration.UNIFIED_CHAT_DOWNGRADE_AUDIT_MESSAGES
    assert "session_type IS DISTINCT FROM" in migration.UNIFIED_CHAT_DOWNGRADE_SQL


def test_chat_upgrade_audit_rejects_bad_identity_before_backfill() -> None:
    migration = _load_migration()
    bind = _RecordingBind(counts=[1])

    with pytest.raises(RuntimeError, match="source Agent tenant is missing"):
        migration._audit_unified_chat_upgrade(bind)

    assert len(bind.statements) == 1
    assert bind.statements[0].lstrip().upper().startswith("SELECT COUNT")
    assert not any(
        statement.lstrip().upper().startswith("UPDATE")
        for statement in bind.statements
    )


def test_chat_downgrade_rejects_new_semantics_before_destructive_ddl(
    monkeypatch,
) -> None:
    migration = _load_migration()
    bind = _RecordingBind(counts=[1])
    destructive_calls: list[str] = []

    monkeypatch.setattr(migration.op, "get_bind", lambda: bind)
    monkeypatch.setattr(
        migration.op,
        "drop_index",
        lambda name, **_kwargs: destructive_calls.append(f"index:{name}"),
    )
    monkeypatch.setattr(
        migration.op,
        "drop_column",
        lambda table_name, column_name: destructive_calls.append(
            f"column:{table_name}.{column_name}"
        ),
    )

    with pytest.raises(RuntimeError, match="new-only semantics"):
        migration._downgrade_unified_chat()

    assert destructive_calls == []
    assert bind.statements[0] == (
        "LOCK TABLE chat_sessions, chat_messages IN ACCESS EXCLUSIVE MODE"
    )


@pytest.mark.parametrize("populated_table", DURABLE_GUARDED_TABLES)
def test_destructive_table_downgrades_refuse_populated_runtime_or_group_tables(
    monkeypatch,
    populated_table: str,
) -> None:
    migration = _load_migration()
    bind = _TableProbeBind(populated_table)
    dropped: list[str] = []
    monkeypatch.setattr(migration.op, "get_bind", lambda: bind)
    monkeypatch.setattr(migration.op, "drop_table", dropped.append)

    downgrade = (
        migration._downgrade_group_domain
        if populated_table in {"groups", "group_members"}
        else migration._downgrade_runtime_schema
    )
    with pytest.raises(RuntimeError, match=f"{populated_table} contains data"):
        downgrade()

    assert dropped == []


def test_bootstrap_column_is_removed_and_tenant_planning_override_is_rejected() -> None:
    migration = _load_migration()

    assert migration.REMOVED_TEMPLATE_COLUMNS == ("bootstrap_content",)
    assert migration.RESTORE_TEMPLATE_COLUMNS == {"bootstrap_content": "TEXT"}
    assert "planning_model_id" not in migration.TENANT_COLUMNS_ADDED


@pytest.mark.parametrize(
    "phase",
    EXPECTED_UPGRADE_PHASES,
)
def test_every_upgrade_phase_has_a_matching_downgrade(phase: str) -> None:
    migration = _load_migration()

    assert callable(getattr(migration, f"_upgrade_{phase}"))
    assert callable(getattr(migration, f"_downgrade_{phase}"))
