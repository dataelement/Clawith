"""Create the product-owned Agent Runtime schema.

Revision ID: create_agent_runtime_schema
Revises: add_llm_runtime_capabilities
Create Date: 2026-07-13 19:30:00
"""

from collections.abc import Sequence
from dataclasses import dataclass
import re

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "create_agent_runtime_schema"
down_revision: str | None = "add_llm_runtime_capabilities"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_CHECKPOINT_SCHEMA = "langgraph_checkpoint"
_CREATE_CHECKPOINT_SCHEMA_SQL = (
    f'CREATE SCHEMA IF NOT EXISTS "{_CHECKPOINT_SCHEMA}"'
)
_DROP_CHECKPOINT_SCHEMA_SQL = f'DROP SCHEMA IF EXISTS "{_CHECKPOINT_SCHEMA}"'


ColumnSpec = tuple[tuple[object, ...], bool, str | None]
ForeignKeySpec = tuple[tuple[str, ...], str, tuple[str, ...], str | None]


@dataclass(frozen=True, slots=True)
class IndexSpec:
    table_name: str
    columns: tuple[str, ...]
    unique: bool = False
    where: str | None = None
    descending: tuple[str, ...] = ()


_AGENT_RUN_COLUMNS: dict[str, ColumnSpec] = {
    "id": (("uuid", True), False, None),
    "tenant_id": (("uuid", True), False, None),
    "agent_id": (("uuid", True), True, None),
    "session_id": (("uuid", True), True, None),
    "source_type": (("string", 32), False, None),
    "source_id": (("string", 200), True, None),
    "source_execution_id": (("string", 200), True, None),
    "correlation_id": (("string", 200), True, None),
    "origin_user_id": (("uuid", True), True, None),
    "origin_agent_id": (("uuid", True), True, None),
    "parent_run_id": (("uuid", True), True, None),
    "root_run_id": (("uuid", True), True, None),
    "goal": (("text",), False, None),
    "run_kind": (("string", 24), False, None),
    "system_role": (("string", 32), True, None),
    "model_id": (("uuid", True), True, None),
    "runtime_type": (("string", 24), False, None),
    "runtime_thread_id": (("string", 255), False, None),
    "graph_name": (("string", 100), False, None),
    "graph_version": (("string", 64), False, None),
    "scheduling_lane_key": (("string", 255), True, None),
    "scheduling_position_created_at": (("datetime", True), True, None),
    "scheduling_position_id": (("uuid", True), True, None),
    "lane_held": (("boolean",), False, "false"),
    "lane_claimed_at": (("datetime", True), True, None),
    "projected_execution_status": (("string", 32), True, None),
    "projected_waiting_type": (("string", 24), True, None),
    "projected_waiting_reason": (("text",), True, None),
    "projected_result_summary": (("text",), True, None),
    "projected_error_code": (("string", 100), True, None),
    "projected_last_error": (("text",), True, None),
    "projected_checkpoint_id": (("string", 255), True, None),
    "projection_updated_at": (("datetime", True), True, None),
    "delivery_status": (("string", 24), False, None),
    "delivery_target": (("jsonb",), True, None),
    "projected_started_at": (("datetime", True), True, None),
    "projected_completed_at": (("datetime", True), True, None),
    "created_at": (("datetime", True), False, "now"),
    "updated_at": (("datetime", True), False, "now"),
}

_AGENT_RUN_COMMAND_COLUMNS: dict[str, ColumnSpec] = {
    "id": (("uuid", True), False, None),
    "tenant_id": (("uuid", True), False, None),
    "run_id": (("uuid", True), False, None),
    "command_type": (("string", 24), False, None),
    "payload": (("jsonb",), False, "empty_json_object"),
    "actor_user_id": (("uuid", True), True, None),
    "actor_agent_id": (("uuid", True), True, None),
    "idempotency_key": (("string", 255), False, None),
    "status": (("string", 24), False, "pending"),
    "claimed_by": (("string", 128), True, None),
    "claim_expires_at": (("datetime", True), True, None),
    "attempt_count": (("integer",), False, "0"),
    "applied_checkpoint_id": (("string", 255), True, None),
    "error_code": (("string", 100), True, None),
    "created_at": (("datetime", True), False, "now"),
    "applied_at": (("datetime", True), True, None),
}

_AGENT_RUN_EVENT_COLUMNS: dict[str, ColumnSpec] = {
    "id": (("uuid", True), False, None),
    "run_id": (("uuid", True), False, None),
    "tenant_id": (("uuid", True), False, None),
    "agent_id": (("uuid", True), True, None),
    "event_type": (("string", 40), False, None),
    "summary": (("text",), False, None),
    "payload": (("jsonb",), False, "empty_json_object"),
    "artifact_refs": (("jsonb",), False, "empty_json_array"),
    "idempotency_key": (("string", 255), False, None),
    "source_checkpoint_id": (("string", 255), True, None),
    "created_at": (("datetime", True), False, "now"),
}

_AGENT_TOOL_EXECUTION_COLUMNS: dict[str, ColumnSpec] = {
    "id": (("uuid", True), False, None),
    "tenant_id": (("uuid", True), False, None),
    "run_id": (("uuid", True), False, None),
    "tool_call_id": (("string", 255), False, None),
    "tool_name": (("string", 200), False, None),
    "assistant_message_id": (("string", 255), False, None),
    "arguments_hash": (("string", 128), False, None),
    "sanitized_arguments": (("jsonb",), True, None),
    "request_ref": (("string", 500), True, None),
    "status": (("string", 24), False, None),
    "result_summary": (("text",), True, None),
    "result_ref": (("string", 500), True, None),
    "lease_owner": (("string", 128), True, None),
    "lease_expires_at": (("datetime", True), True, None),
    "started_at": (("datetime", True), False, "now"),
    "completed_at": (("datetime", True), True, None),
    "updated_at": (("datetime", True), False, "now"),
}

_SESSION_CONTEXT_STATE_COLUMNS: dict[str, ColumnSpec] = {
    "id": (("uuid", True), False, None),
    "tenant_id": (("uuid", True), False, None),
    "agent_id": (("uuid", True), True, None),
    "session_id": (("uuid", True), False, None),
    "summary": (("text",), False, "empty_string"),
    "requirements": (("jsonb",), False, "empty_json_array"),
    "decisions": (("jsonb",), False, "empty_json_array"),
    "open_items": (("jsonb",), False, "empty_json_array"),
    "evidence_refs": (("jsonb",), False, "empty_json_array"),
    "workspace_refs": (("jsonb",), False, "empty_json_array"),
    "covered_through_message_id": (("uuid", True), True, None),
    "version": (("integer",), False, "1"),
    "created_at": (("datetime", True), False, "now"),
    "updated_at": (("datetime", True), False, "now"),
}

_COLUMNS_BY_TABLE = {
    "agent_runs": _AGENT_RUN_COLUMNS,
    "agent_run_commands": _AGENT_RUN_COMMAND_COLUMNS,
    "agent_run_events": _AGENT_RUN_EVENT_COLUMNS,
    "agent_tool_executions": _AGENT_TOOL_EXECUTION_COLUMNS,
    "session_context_states": _SESSION_CONTEXT_STATE_COLUMNS,
}

_PRIMARY_KEYS = {
    "agent_runs": ("pk_agent_runs", ("id",)),
    "agent_run_commands": ("pk_agent_run_commands", ("id",)),
    "agent_run_events": ("pk_agent_run_events", ("id",)),
    "agent_tool_executions": ("pk_agent_tool_executions", ("id",)),
    "session_context_states": ("pk_session_context_states", ("id",)),
}

_FOREIGN_KEYS: dict[str, dict[str, ForeignKeySpec]] = {
    "agent_runs": {
        "fk_agent_runs_tenant_id_tenants": (("tenant_id",), "tenants", ("id",), "CASCADE"),
        "fk_agent_runs_agent_id_agents": (("agent_id",), "agents", ("id",), "CASCADE"),
        "fk_agent_runs_session_id_chat_sessions": (
            ("session_id",),
            "chat_sessions",
            ("id",),
            "SET NULL",
        ),
        "fk_agent_runs_tenant_session_chat_sessions": (
            ("tenant_id", "session_id"),
            "chat_sessions",
            ("tenant_id", "id"),
            None,
        ),
        "fk_agent_runs_origin_user_id_users": (("origin_user_id",), "users", ("id",), "SET NULL"),
        "fk_agent_runs_origin_agent_id_agents": (
            ("origin_agent_id",),
            "agents",
            ("id",),
            "SET NULL",
        ),
        "fk_agent_runs_parent_run_id_agent_runs": (
            ("parent_run_id",),
            "agent_runs",
            ("id",),
            "SET NULL",
        ),
        "fk_agent_runs_root_run_id_agent_runs": (
            ("root_run_id",),
            "agent_runs",
            ("id",),
            "SET NULL",
        ),
        "fk_agent_runs_model_id_llm_models": (("model_id",), "llm_models", ("id",), "RESTRICT"),
    },
    "agent_run_commands": {
        "fk_agent_run_commands_tenant_id_tenants": (
            ("tenant_id",),
            "tenants",
            ("id",),
            "CASCADE",
        ),
        "fk_agent_run_commands_tenant_run_agent_runs": (
            ("tenant_id", "run_id"),
            "agent_runs",
            ("tenant_id", "id"),
            "CASCADE",
        ),
        "fk_agent_run_commands_actor_user_id_users": (
            ("actor_user_id",),
            "users",
            ("id",),
            "SET NULL",
        ),
        "fk_agent_run_commands_actor_agent_id_agents": (
            ("actor_agent_id",),
            "agents",
            ("id",),
            "SET NULL",
        ),
    },
    "agent_run_events": {
        "fk_agent_run_events_tenant_id_tenants": (("tenant_id",), "tenants", ("id",), "CASCADE"),
        "fk_agent_run_events_tenant_run_agent_runs": (
            ("tenant_id", "run_id"),
            "agent_runs",
            ("tenant_id", "id"),
            "CASCADE",
        ),
        "fk_agent_run_events_agent_id_agents": (("agent_id",), "agents", ("id",), "SET NULL"),
    },
    "agent_tool_executions": {
        "fk_agent_tool_executions_tenant_id_tenants": (
            ("tenant_id",),
            "tenants",
            ("id",),
            "CASCADE",
        ),
        "fk_agent_tool_executions_tenant_run_agent_runs": (
            ("tenant_id", "run_id"),
            "agent_runs",
            ("tenant_id", "id"),
            "CASCADE",
        ),
    },
    "session_context_states": {
        "fk_session_context_states_tenant_id_tenants": (
            ("tenant_id",),
            "tenants",
            ("id",),
            "CASCADE",
        ),
        "fk_session_context_states_agent_id_agents": (
            ("agent_id",),
            "agents",
            ("id",),
            "SET NULL",
        ),
        "fk_session_context_states_tenant_session_chat_sessions": (
            ("tenant_id", "session_id"),
            "chat_sessions",
            ("tenant_id", "id"),
            "CASCADE",
        ),
        "fk_session_context_states_covered_message_id_chat_messages": (
            ("covered_through_message_id",),
            "chat_messages",
            ("id",),
            "SET NULL",
        ),
    },
}

_UNIQUE_CONSTRAINTS = {
    "agent_runs": {
        "uq_agent_runs_runtime_thread_id": ("runtime_thread_id",),
        "uq_agent_runs_tenant_id_id": ("tenant_id", "id"),
    },
    "agent_run_commands": {
        "uq_agent_run_commands_run_idempotency": ("run_id", "idempotency_key"),
    },
    "agent_run_events": {
        "uq_agent_run_events_run_idempotency": ("run_id", "idempotency_key"),
        "uq_agent_run_events_checkpoint_type": (
            "run_id",
            "source_checkpoint_id",
            "event_type",
        ),
    },
    "agent_tool_executions": {
        "uq_agent_tool_executions_run_tool_call": ("run_id", "tool_call_id"),
    },
    "session_context_states": {
        "uq_session_context_states_session_id": ("session_id",),
    },
}

_CHECK_CONSTRAINTS = {
    "agent_runs": {
        "ck_agent_runs_source_type": "source_type IN ('chat', 'trigger', 'task', 'a2a', 'heartbeat')",
        "ck_agent_runs_run_kind": (
            "run_kind IN ('foreground', 'background', 'delegated', 'orchestration')"
        ),
        "ck_agent_runs_runtime_type": "runtime_type IN ('legacy', 'langgraph')",
        "ck_agent_runs_projected_execution_status": (
            "projected_execution_status IS NULL OR projected_execution_status IN "
            "('created', 'queued', 'running', 'waiting_user', 'waiting_external', "
            "'waiting_agent', 'verifying', 'completed', 'failed', 'cancelled')"
        ),
        "ck_agent_runs_delivery_status": (
            "delivery_status IN ('not_required', 'pending', 'delivered', 'failed')"
        ),
        "ck_agent_runs_langgraph_model": "runtime_type <> 'langgraph' OR model_id IS NOT NULL",
        "ck_agent_runs_lane_holder_key": "lane_held = false OR scheduling_lane_key IS NOT NULL",
        "ck_agent_runs_lane_position": (
            "(scheduling_lane_key IS NULL AND scheduling_position_created_at IS NULL "
            "AND scheduling_position_id IS NULL) OR "
            "(scheduling_lane_key IS NOT NULL AND scheduling_position_created_at IS NOT NULL "
            "AND scheduling_position_id IS NOT NULL)"
        ),
        "ck_agent_runs_orchestration_identity": (
            "(run_kind = 'orchestration' AND agent_id IS NULL "
            "AND system_role = 'group_planning' AND model_id IS NOT NULL) OR "
            "(run_kind <> 'orchestration' AND agent_id IS NOT NULL AND system_role IS NULL)"
        ),
    },
    "agent_run_commands": {
        "ck_agent_run_commands_command_type": "command_type IN ('start', 'resume', 'cancel')",
        "ck_agent_run_commands_status": "status IN ('pending', 'claimed', 'applied', 'rejected')",
        "ck_agent_run_commands_attempt_count": "attempt_count >= 0",
    },
    "agent_run_events": {
        "ck_agent_run_events_event_type": (
            "event_type IN ('run_created', 'status_changed', 'waiting_started', 'resumed', "
            "'evidence_added', 'verification_updated', 'run_completed', 'run_failed', "
            "'run_cancelled', 'delivery_succeeded', 'delivery_failed')"
        ),
    },
    "agent_tool_executions": {
        "ck_agent_tool_executions_status": "status IN ('started', 'succeeded', 'failed', 'unknown')",
    },
    "session_context_states": {
        "ck_session_context_states_version": "version >= 1",
    },
}

_INDEXES = {
    "ix_agent_runs_tenant_agent_projected_status": IndexSpec(
        "agent_runs", ("tenant_id", "agent_id", "projected_execution_status")
    ),
    "ix_agent_runs_session_created_at": IndexSpec(
        "agent_runs", ("session_id", "created_at"), descending=("created_at",)
    ),
    "ix_agent_runs_parent_run_id": IndexSpec("agent_runs", ("parent_run_id",)),
    "ix_agent_runs_root_run_id": IndexSpec("agent_runs", ("root_run_id",)),
    "ix_agent_runs_source": IndexSpec("agent_runs", ("source_type", "source_id")),
    "uq_agent_runs_source_execution": IndexSpec(
        "agent_runs",
        ("source_type", "source_execution_id"),
        unique=True,
        where="source_execution_id IS NOT NULL",
    ),
    "uq_agent_runs_active_lane": IndexSpec(
        "agent_runs",
        ("scheduling_lane_key",),
        unique=True,
        where="scheduling_lane_key IS NOT NULL AND lane_held IS true",
    ),
    "ix_agent_runs_lane_candidate_order": IndexSpec(
        "agent_runs",
        (
            "scheduling_lane_key",
            "scheduling_position_created_at",
            "scheduling_position_id",
            "created_at",
            "id",
        ),
        where="scheduling_lane_key IS NOT NULL",
    ),
    "ix_agent_run_commands_status_claim_created": IndexSpec(
        "agent_run_commands", ("status", "claim_expires_at", "created_at")
    ),
    "ix_agent_run_commands_run_created": IndexSpec(
        "agent_run_commands", ("run_id", "created_at", "id")
    ),
    "ix_agent_run_events_run_created": IndexSpec(
        "agent_run_events", ("run_id", "created_at")
    ),
    "ix_agent_run_events_tenant_type_created": IndexSpec(
        "agent_run_events", ("tenant_id", "event_type", "created_at")
    ),
    "ix_agent_tool_executions_tenant_status_started": IndexSpec(
        "agent_tool_executions", ("tenant_id", "status", "started_at")
    ),
    "ix_agent_tool_executions_status_lease": IndexSpec(
        "agent_tool_executions", ("status", "lease_expires_at")
    ),
    "ix_session_context_states_tenant_agent_updated": IndexSpec(
        "session_context_states",
        ("tenant_id", "agent_id", "updated_at"),
        descending=("updated_at",),
    ),
}


def _type_signature(column_type: sa.types.TypeEngine) -> tuple[object, ...]:
    if isinstance(column_type, postgresql.JSONB):
        return ("jsonb",)
    if isinstance(column_type, sa.Uuid):
        return ("uuid", bool(column_type.as_uuid))
    if isinstance(column_type, sa.DateTime):
        return ("datetime", bool(column_type.timezone))
    if isinstance(column_type, sa.Text):
        return ("text",)
    if isinstance(column_type, sa.String):
        return ("string", column_type.length)
    if isinstance(column_type, sa.Boolean):
        return ("boolean",)
    if isinstance(column_type, sa.Integer):
        return ("integer",)
    return (type(column_type).__name__.lower(),)


def _without_casts(value: str) -> str:
    return re.sub(
        r"::\s*(?:character\s+varying(?:\(\d+\))?|text|bpchar|jsonb|boolean|uuid|integer)"
        r"(?:\[\])?",
        "",
        value,
        flags=re.IGNORECASE,
    )


def _canonical_default(value: object | None) -> str | None:
    if value is None:
        return None
    normalized = _without_casts(str(value).strip().lower())
    while normalized.startswith("(") and normalized.endswith(")"):
        normalized = normalized[1:-1].strip()
    normalized = re.sub(r"\s+", " ", normalized)
    if normalized in {"now()", "current_timestamp", "current_timestamp()"}:
        return "now"
    if normalized in {"false", "'false'"}:
        return "false"
    if normalized == "'{}'":
        return "empty_json_object"
    if normalized == "'[]'":
        return "empty_json_array"
    if normalized == "''":
        return "empty_string"
    if normalized == "'pending'":
        return "pending"
    return normalized


def _canonical_sql(value: object | None) -> str | None:
    if value is None:
        return None
    normalized = _without_casts(str(value).strip().lower()).replace('"', "")
    normalized = re.sub(r"=\s*any\s*\(\s*array\s*\[", " in (", normalized)
    normalized = re.sub(r"\]\s*\)", ")", normalized)
    return re.sub(r"[\s()]", "", normalized)


def _require_columns(
    inspector: sa.Inspector,
    table_name: str,
    expected: dict[str, ColumnSpec],
) -> None:
    actual = {column["name"]: column for column in inspector.get_columns(table_name)}
    missing = set(expected) - set(actual)
    if missing:
        raise RuntimeError(
            f"Existing {table_name} table is missing required columns: {', '.join(sorted(missing))}"
        )
    for column_name, (expected_type, expected_nullable, expected_default) in expected.items():
        column = actual[column_name]
        actual_spec = (
            _type_signature(column["type"]),
            bool(column["nullable"]),
            _canonical_default(column.get("default")),
        )
        expected_spec = (expected_type, expected_nullable, expected_default)
        if actual_spec != expected_spec:
            raise RuntimeError(
                f"Existing {table_name}.{column_name} has schema {actual_spec!r}; "
                f"expected {expected_spec!r}"
            )


def _require_primary_key(
    inspector: sa.Inspector,
    table_name: str,
    expected: tuple[str, tuple[str, ...]],
) -> None:
    actual = inspector.get_pk_constraint(table_name)
    actual_spec = (actual.get("name"), tuple(actual.get("constrained_columns") or ()))
    if actual_spec != expected:
        raise RuntimeError(
            f"Existing {table_name} table has primary key {actual_spec!r}; expected {expected!r}"
        )


def _normalized_ondelete(value: object | None) -> str | None:
    if value is None or str(value).upper() == "NO ACTION":
        return None
    return str(value).upper()


def _require_foreign_keys(
    inspector: sa.Inspector,
    table_name: str,
    expected: dict[str, ForeignKeySpec],
) -> None:
    actual_by_name = {
        constraint.get("name"): constraint
        for constraint in inspector.get_foreign_keys(table_name)
    }
    for name, expected_spec in expected.items():
        constraint = actual_by_name.get(name)
        if constraint is None:
            raise RuntimeError(f"Existing {table_name} table is missing foreign key: {name}")
        options = constraint.get("options") or {}
        actual_spec = (
            tuple(constraint.get("constrained_columns") or ()),
            constraint.get("referred_table"),
            tuple(constraint.get("referred_columns") or ()),
            _normalized_ondelete(options.get("ondelete") or constraint.get("ondelete")),
        )
        if actual_spec != expected_spec:
            raise RuntimeError(
                f"Existing {table_name} foreign key {name} has schema {actual_spec!r}; "
                f"expected {expected_spec!r}"
            )


def _require_unique_constraints(
    inspector: sa.Inspector,
    table_name: str,
    expected: dict[str, tuple[str, ...]],
) -> None:
    actual_by_name = {
        constraint.get("name"): tuple(constraint.get("column_names") or ())
        for constraint in inspector.get_unique_constraints(table_name)
    }
    for name, expected_columns in expected.items():
        actual_columns = actual_by_name.get(name)
        if actual_columns != expected_columns:
            raise RuntimeError(
                f"Existing {table_name} unique constraint {name} has columns "
                f"{actual_columns!r}; expected {expected_columns!r}"
            )


def _require_checks(
    inspector: sa.Inspector,
    table_name: str,
    expected: dict[str, str],
) -> None:
    actual_by_name = {
        constraint.get("name"): constraint.get("sqltext")
        for constraint in inspector.get_check_constraints(table_name)
    }
    for name, expected_sql in expected.items():
        actual_sql = actual_by_name.get(name)
        if actual_sql is None:
            raise RuntimeError(f"Existing {table_name} table is missing check constraint: {name}")
        if _canonical_sql(actual_sql) != _canonical_sql(expected_sql):
            raise RuntimeError(
                f"Existing {table_name} check constraint {name} is incompatible: {actual_sql!r}"
            )


def _index_where(index: dict[str, object]) -> object | None:
    dialect_options = index.get("dialect_options") or {}
    where = dialect_options.get("postgresql_where")
    if where is None:
        where = index.get("postgresql_where")
    return where


def _descending_columns(index: dict[str, object]) -> tuple[str, ...]:
    sorting = index.get("column_sorting") or {}
    return tuple(
        column
        for column, options in sorting.items()
        if "desc" in {str(option).lower() for option in options}
    )


def _create_index(name: str, spec: IndexSpec) -> None:
    columns: list[str | sa.TextClause] = [
        sa.text(f"{column} DESC") if column in spec.descending else column
        for column in spec.columns
    ]
    kwargs: dict[str, object] = {}
    if spec.where is not None:
        kwargs["postgresql_where"] = sa.text(spec.where)
    op.create_index(name, spec.table_name, columns, unique=spec.unique, **kwargs)


def _ensure_indexes(inspector: sa.Inspector, table_name: str) -> None:
    actual_by_name = {
        index.get("name"): index for index in inspector.get_indexes(table_name)
    }
    for name, spec in _INDEXES.items():
        if spec.table_name != table_name:
            continue
        index = actual_by_name.get(name)
        if index is None:
            _create_index(name, spec)
            continue
        actual_spec = (
            tuple(index.get("column_names") or ()),
            bool(index.get("unique")),
            _canonical_sql(_index_where(index)),
            _descending_columns(index),
        )
        expected_spec = (
            spec.columns,
            spec.unique,
            _canonical_sql(spec.where),
            spec.descending,
        )
        if actual_spec != expected_spec:
            raise RuntimeError(
                f"Existing {table_name} index {name} has schema {actual_spec!r}; "
                f"expected {expected_spec!r}"
            )


def _validate_table(inspector: sa.Inspector, table_name: str) -> None:
    _require_columns(inspector, table_name, _COLUMNS_BY_TABLE[table_name])
    _require_primary_key(inspector, table_name, _PRIMARY_KEYS[table_name])
    _require_foreign_keys(inspector, table_name, _FOREIGN_KEYS[table_name])
    _require_unique_constraints(inspector, table_name, _UNIQUE_CONSTRAINTS[table_name])
    _require_checks(inspector, table_name, _CHECK_CONSTRAINTS[table_name])
    _ensure_indexes(inspector, table_name)


def _create_agent_runs() -> None:
    op.create_table(
        "agent_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("agent_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("session_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("source_type", sa.String(length=32), nullable=False),
        sa.Column("source_id", sa.String(length=200), nullable=True),
        sa.Column("source_execution_id", sa.String(length=200), nullable=True),
        sa.Column("correlation_id", sa.String(length=200), nullable=True),
        sa.Column("origin_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("origin_agent_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("parent_run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("root_run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("goal", sa.Text(), nullable=False),
        sa.Column("run_kind", sa.String(length=24), nullable=False),
        sa.Column("system_role", sa.String(length=32), nullable=True),
        sa.Column("model_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("runtime_type", sa.String(length=24), nullable=False),
        sa.Column("runtime_thread_id", sa.String(length=255), nullable=False),
        sa.Column("graph_name", sa.String(length=100), nullable=False),
        sa.Column("graph_version", sa.String(length=64), nullable=False),
        sa.Column("scheduling_lane_key", sa.String(length=255), nullable=True),
        sa.Column("scheduling_position_created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("scheduling_position_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("lane_held", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("lane_claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("projected_execution_status", sa.String(length=32), nullable=True),
        sa.Column("projected_waiting_type", sa.String(length=24), nullable=True),
        sa.Column("projected_waiting_reason", sa.Text(), nullable=True),
        sa.Column("projected_result_summary", sa.Text(), nullable=True),
        sa.Column("projected_error_code", sa.String(length=100), nullable=True),
        sa.Column("projected_last_error", sa.Text(), nullable=True),
        sa.Column("projected_checkpoint_id", sa.String(length=255), nullable=True),
        sa.Column("projection_updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("delivery_status", sa.String(length=24), nullable=False),
        sa.Column("delivery_target", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("projected_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("projected_completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        *[
            sa.CheckConstraint(expression, name=name)
            for name, expression in _CHECK_CONSTRAINTS["agent_runs"].items()
        ],
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name="fk_agent_runs_tenant_id_tenants", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["agent_id"], ["agents.id"], name="fk_agent_runs_agent_id_agents", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["chat_sessions.id"],
            name="fk_agent_runs_session_id_chat_sessions",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "session_id"],
            ["chat_sessions.tenant_id", "chat_sessions.id"],
            name="fk_agent_runs_tenant_session_chat_sessions",
        ),
        sa.ForeignKeyConstraint(
            ["origin_user_id"],
            ["users.id"],
            name="fk_agent_runs_origin_user_id_users",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["origin_agent_id"],
            ["agents.id"],
            name="fk_agent_runs_origin_agent_id_agents",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["parent_run_id"],
            ["agent_runs.id"],
            name="fk_agent_runs_parent_run_id_agent_runs",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["root_run_id"],
            ["agent_runs.id"],
            name="fk_agent_runs_root_run_id_agent_runs",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["model_id"],
            ["llm_models.id"],
            name="fk_agent_runs_model_id_llm_models",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_agent_runs"),
        sa.UniqueConstraint("runtime_thread_id", name="uq_agent_runs_runtime_thread_id"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_agent_runs_tenant_id_id"),
    )


def _create_agent_run_commands() -> None:
    op.create_table(
        "agent_run_commands",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("command_type", sa.String(length=24), nullable=False),
        sa.Column(
            "payload",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("actor_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("actor_agent_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("idempotency_key", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=24), server_default=sa.text("'pending'"), nullable=False),
        sa.Column("claimed_by", sa.String(length=128), nullable=True),
        sa.Column("claim_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempt_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("applied_checkpoint_id", sa.String(length=255), nullable=True),
        sa.Column("error_code", sa.String(length=100), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("applied_at", sa.DateTime(timezone=True), nullable=True),
        *[
            sa.CheckConstraint(expression, name=name)
            for name, expression in _CHECK_CONSTRAINTS["agent_run_commands"].items()
        ],
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name="fk_agent_run_commands_tenant_id_tenants",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "run_id"],
            ["agent_runs.tenant_id", "agent_runs.id"],
            name="fk_agent_run_commands_tenant_run_agent_runs",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["actor_user_id"],
            ["users.id"],
            name="fk_agent_run_commands_actor_user_id_users",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["actor_agent_id"],
            ["agents.id"],
            name="fk_agent_run_commands_actor_agent_id_agents",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_agent_run_commands"),
        sa.UniqueConstraint("run_id", "idempotency_key", name="uq_agent_run_commands_run_idempotency"),
    )


def _create_agent_run_events() -> None:
    op.create_table(
        "agent_run_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("agent_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("event_type", sa.String(length=40), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column(
            "payload",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "artifact_refs",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("idempotency_key", sa.String(length=255), nullable=False),
        sa.Column("source_checkpoint_id", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            _CHECK_CONSTRAINTS["agent_run_events"]["ck_agent_run_events_event_type"],
            name="ck_agent_run_events_event_type",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name="fk_agent_run_events_tenant_id_tenants",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "run_id"],
            ["agent_runs.tenant_id", "agent_runs.id"],
            name="fk_agent_run_events_tenant_run_agent_runs",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["agent_id"],
            ["agents.id"],
            name="fk_agent_run_events_agent_id_agents",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_agent_run_events"),
        sa.UniqueConstraint("run_id", "idempotency_key", name="uq_agent_run_events_run_idempotency"),
        sa.UniqueConstraint(
            "run_id",
            "source_checkpoint_id",
            "event_type",
            name="uq_agent_run_events_checkpoint_type",
        ),
    )


def _create_agent_tool_executions() -> None:
    op.create_table(
        "agent_tool_executions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tool_call_id", sa.String(length=255), nullable=False),
        sa.Column("tool_name", sa.String(length=200), nullable=False),
        sa.Column("assistant_message_id", sa.String(length=255), nullable=False),
        sa.Column("arguments_hash", sa.String(length=128), nullable=False),
        sa.Column("sanitized_arguments", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("request_ref", sa.String(length=500), nullable=True),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("result_summary", sa.Text(), nullable=True),
        sa.Column("result_ref", sa.String(length=500), nullable=True),
        sa.Column("lease_owner", sa.String(length=128), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            _CHECK_CONSTRAINTS["agent_tool_executions"]["ck_agent_tool_executions_status"],
            name="ck_agent_tool_executions_status",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name="fk_agent_tool_executions_tenant_id_tenants",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "run_id"],
            ["agent_runs.tenant_id", "agent_runs.id"],
            name="fk_agent_tool_executions_tenant_run_agent_runs",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_agent_tool_executions"),
        sa.UniqueConstraint("run_id", "tool_call_id", name="uq_agent_tool_executions_run_tool_call"),
    )


def _create_session_context_states() -> None:
    op.create_table(
        "session_context_states",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("agent_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("session_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("summary", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column(
            "requirements",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "decisions",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "open_items",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "evidence_refs",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "workspace_refs",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("covered_through_message_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("version >= 1", name="ck_session_context_states_version"),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name="fk_session_context_states_tenant_id_tenants",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["agent_id"],
            ["agents.id"],
            name="fk_session_context_states_agent_id_agents",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "session_id"],
            ["chat_sessions.tenant_id", "chat_sessions.id"],
            name="fk_session_context_states_tenant_session_chat_sessions",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["covered_through_message_id"],
            ["chat_messages.id"],
            name="fk_session_context_states_covered_message_id_chat_messages",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_session_context_states"),
        sa.UniqueConstraint("session_id", name="uq_session_context_states_session_id"),
    )


_CREATE_TABLE = {
    "agent_runs": _create_agent_runs,
    "agent_run_commands": _create_agent_run_commands,
    "agent_run_events": _create_agent_run_events,
    "agent_tool_executions": _create_agent_tool_executions,
    "session_context_states": _create_session_context_states,
}

_CREATE_ORDER = (
    "agent_runs",
    "agent_run_commands",
    "agent_run_events",
    "agent_tool_executions",
    "session_context_states",
)


def upgrade() -> None:
    # The pinned LangGraph saver owns its tables and migration ledger. Alembic
    # only guarantees that its isolated target schema exists before saver.setup().
    op.execute(sa.text(_CREATE_CHECKPOINT_SCHEMA_SQL))
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = set(inspector.get_table_names())

    for table_name in _CREATE_ORDER:
        if table_name in existing_tables:
            _validate_table(inspector, table_name)
            continue
        _CREATE_TABLE[table_name]()
        for index_name, index_spec in _INDEXES.items():
            if index_spec.table_name == table_name:
                _create_index(index_name, index_spec)


def _require_empty_tables(bind: sa.Connection, table_names: list[str]) -> None:
    if not table_names:
        return
    quoted_tables = ", ".join(f'"{table_name}"' for table_name in table_names)
    bind.execute(sa.text(f"LOCK TABLE {quoted_tables} IN ACCESS EXCLUSIVE MODE"))
    for table_name in table_names:
        result = bind.execute(sa.text(f'SELECT 1 FROM "{table_name}" LIMIT 1'))
        if result.first() is not None:
            raise RuntimeError(
                "Refusing to downgrade Agent Runtime schema because "
                f"{table_name} contains data"
            )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = set(inspector.get_table_names())
    drop_order = (
        "agent_run_commands",
        "agent_run_events",
        "agent_tool_executions",
        "session_context_states",
        "agent_runs",
    )
    tables_to_drop = [table_name for table_name in drop_order if table_name in existing_tables]
    _require_empty_tables(bind, tables_to_drop)
    for table_name in tables_to_drop:
        op.drop_table(table_name)
    # Intentionally omit CASCADE. PostgreSQL must refuse the downgrade while
    # saver-owned checkpoints still exist instead of deleting resumable state.
    op.execute(sa.text(_DROP_CHECKPOINT_SCHEMA_SQL))
