"""Static metadata contracts for the product-owned Agent runtime tables."""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateIndex, CreateTable

# Register every referenced table so each Runtime table can be compiled in isolation.
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


def _constraint_names(table: sa.Table, constraint_type: type[sa.Constraint]) -> set[str | None]:
    return {
        constraint.name
        for constraint in table.constraints
        if isinstance(constraint, constraint_type)
    }


def _foreign_key_specs(
    table: sa.Table,
) -> dict[str | None, tuple[tuple[str, ...], tuple[str, ...], str | None]]:
    return {
        constraint.name: (
            tuple(element.parent.name for element in constraint.elements),
            tuple(element.target_fullname for element in constraint.elements),
            constraint.ondelete,
        )
        for constraint in table.constraints
        if isinstance(constraint, sa.ForeignKeyConstraint)
    }


def _check_sql(table: sa.Table) -> dict[str | None, str]:
    return {
        constraint.name: " ".join(str(constraint.sqltext).lower().split())
        for constraint in table.constraints
        if isinstance(constraint, sa.CheckConstraint)
    }


def test_agent_run_model_captures_registry_thread_budget_and_lane_contract():
    table = AgentRun.__table__

    assert set(table.columns.keys()) == {
        "id",
        "tenant_id",
        "agent_id",
        "session_id",
        "source_type",
        "source_id",
        "source_execution_id",
        "correlation_id",
        "origin_user_id",
        "origin_agent_id",
        "parent_run_id",
        "root_run_id",
        "goal",
        "run_kind",
        "system_role",
        "model_id",
        "model_turn_limit",
        "runtime_type",
        "runtime_thread_id",
        "graph_name",
        "graph_version",
        "scheduling_lane_key",
        "scheduling_position_created_at",
        "scheduling_position_id",
        "lane_held",
        "lane_claimed_at",
        "session_context_applied_checkpoint_id",
        "delivery_status",
        "delivery_target",
        "created_at",
        "updated_at",
    }
    assert table.primary_key.name == "pk_agent_runs"
    assert _constraint_names(table, sa.UniqueConstraint) == {"uq_agent_runs_tenant_id_id"}
    assert _constraint_names(table, sa.CheckConstraint) == {
        "ck_agent_runs_source_type",
        "ck_agent_runs_run_kind",
        "ck_agent_runs_runtime_type",
        "ck_agent_runs_delivery_status",
        "ck_agent_runs_langgraph_model",
        "ck_agent_runs_model_turn_limit",
        "ck_agent_runs_lane_holder_key",
        "ck_agent_runs_lane_position",
        "ck_agent_runs_orchestration_identity",
    }
    assert {index.name for index in table.indexes} == {
        "ix_agent_runs_tenant_thread_created_at",
        "ix_agent_runs_session_created_at",
        "ix_agent_runs_parent_run_id",
        "ix_agent_runs_root_run_id",
        "ix_agent_runs_source",
        "uq_agent_runs_source_execution",
        "uq_agent_runs_active_lane",
        "ix_agent_runs_lane_candidate_order",
    }
    assert table.c.agent_id.nullable is True
    assert table.c.correlation_id.nullable is True
    assert table.c.lane_held.nullable is False
    assert table.c.delivery_status.nullable is False
    assert table.c.runtime_thread_id.nullable is False
    assert table.c.model_turn_limit.nullable is True

    checks = _check_sql(table)
    assert "model_id is not null" in checks["ck_agent_runs_langgraph_model"]
    assert "scheduling_lane_key is not null" in checks["ck_agent_runs_lane_holder_key"]
    assert "system_role = 'group_planning'" in checks["ck_agent_runs_orchestration_identity"]
    assert "model_turn_limit is null" in checks["ck_agent_runs_model_turn_limit"]
    assert "model_turn_limit > 0" in checks["ck_agent_runs_model_turn_limit"]

    foreign_keys = _foreign_key_specs(table)
    assert foreign_keys["fk_agent_runs_tenant_session_chat_sessions"] == (
        ("tenant_id", "session_id"),
        ("chat_sessions.tenant_id", "chat_sessions.id"),
        None,
    )
    assert foreign_keys["fk_agent_runs_session_id_chat_sessions"] == (
        ("session_id",),
        ("chat_sessions.id",),
        "SET NULL",
    )

    indexes = {index.name: index for index in table.indexes}
    assert indexes["uq_agent_runs_source_execution"].unique is True
    assert indexes["uq_agent_runs_active_lane"].unique is True
    assert indexes["ix_agent_runs_tenant_thread_created_at"].unique is False
    assert indexes["uq_agent_runs_source_execution"].dialect_options["postgresql"]["where"] is not None
    assert indexes["uq_agent_runs_active_lane"].dialect_options["postgresql"]["where"] is not None
    assert indexes["ix_agent_runs_lane_candidate_order"].dialect_options["postgresql"]["where"] is not None


def test_agent_run_command_model_captures_reliable_input_contract():
    table = AgentRunCommand.__table__

    assert set(table.columns.keys()) == {
        "id",
        "tenant_id",
        "run_id",
        "command_type",
        "payload",
        "actor_user_id",
        "actor_agent_id",
        "idempotency_key",
        "status",
        "claimed_by",
        "claim_expires_at",
        "attempt_count",
        "applied_checkpoint_id",
        "error_code",
        "created_at",
        "applied_at",
    }
    assert table.primary_key.name == "pk_agent_run_commands"
    assert _constraint_names(table, sa.UniqueConstraint) == {
        "uq_agent_run_commands_run_idempotency"
    }
    assert _constraint_names(table, sa.CheckConstraint) == {
        "ck_agent_run_commands_command_type",
        "ck_agent_run_commands_status",
        "ck_agent_run_commands_attempt_count",
    }
    assert {index.name for index in table.indexes} == {
        "ix_agent_run_commands_status_claim_created",
        "ix_agent_run_commands_run_created",
    }
    assert table.c.attempt_count.nullable is False
    assert str(table.c.attempt_count.server_default.arg) == "0"
    assert _foreign_key_specs(table)["fk_agent_run_commands_tenant_run_agent_runs"] == (
        ("tenant_id", "run_id"),
        ("agent_runs.tenant_id", "agent_runs.id"),
        "CASCADE",
    )


def test_agent_run_event_model_captures_product_projection_contract():
    table = AgentRunEvent.__table__

    assert set(table.columns.keys()) == {
        "id",
        "run_id",
        "tenant_id",
        "agent_id",
        "event_type",
        "summary",
        "payload",
        "artifact_refs",
        "idempotency_key",
        "source_checkpoint_id",
        "created_at",
    }
    assert table.primary_key.name == "pk_agent_run_events"
    assert _constraint_names(table, sa.UniqueConstraint) == {
        "uq_agent_run_events_run_idempotency",
        "uq_agent_run_events_checkpoint_type",
    }
    assert _constraint_names(table, sa.CheckConstraint) == {
        "ck_agent_run_events_event_type"
    }
    assert {index.name for index in table.indexes} == {
        "ix_agent_run_events_run_created",
        "ix_agent_run_events_tenant_type_created",
    }
    assert table.c.agent_id.nullable is True
    assert str(table.c.artifact_refs.server_default.arg) == "'[]'::jsonb"
    assert _foreign_key_specs(table)["fk_agent_run_events_tenant_run_agent_runs"] == (
        ("tenant_id", "run_id"),
        ("agent_runs.tenant_id", "agent_runs.id"),
        "CASCADE",
    )


def test_session_context_state_model_captures_single_current_summary_contract():
    table = SessionContextState.__table__

    assert set(table.columns.keys()) == {
        "id",
        "tenant_id",
        "agent_id",
        "session_id",
        "summary",
        "requirements",
        "decisions",
        "open_items",
        "evidence_refs",
        "workspace_refs",
        "covered_through_message_id",
        "version",
        "created_at",
        "updated_at",
    }
    assert table.primary_key.name == "pk_session_context_states"
    assert _constraint_names(table, sa.UniqueConstraint) == {
        "uq_session_context_states_session_id"
    }
    assert _constraint_names(table, sa.CheckConstraint) == {
        "ck_session_context_states_version"
    }
    assert {index.name for index in table.indexes} == {
        "ix_session_context_states_tenant_agent_updated"
    }
    assert table.c.agent_id.nullable is True
    assert table.c.session_id.nullable is False
    assert table.c.version.nullable is False
    assert str(table.c.version.server_default.arg) == "1"
    assert _foreign_key_specs(table)["fk_session_context_states_tenant_session_chat_sessions"] == (
        ("tenant_id", "session_id"),
        ("chat_sessions.tenant_id", "chat_sessions.id"),
        "CASCADE",
    )


def test_agent_tool_execution_model_captures_idempotency_and_lease_contract():
    table = AgentToolExecution.__table__

    assert set(table.columns.keys()) == {
        "id",
        "tenant_id",
        "run_id",
        "tool_call_id",
        "tool_name",
        "assistant_message_id",
        "arguments_hash",
        "sanitized_arguments",
        "request_ref",
        "effect",
        "retry_policy",
        "attempt_count",
        "status",
        "result_summary",
        "result_ref",
        "result_metadata",
        "lease_owner",
        "lease_expires_at",
        "started_at",
        "completed_at",
        "updated_at",
    }
    assert table.primary_key.name == "pk_agent_tool_executions"
    assert _constraint_names(table, sa.UniqueConstraint) == {
        "uq_agent_tool_executions_run_tool_call"
    }
    assert _constraint_names(table, sa.CheckConstraint) == {
        "ck_agent_tool_executions_effect",
        "ck_agent_tool_executions_attempt_count",
        "ck_agent_tool_executions_retry_policy",
        "ck_agent_tool_executions_status",
    }
    assert {index.name for index in table.indexes} == {
        "ix_agent_tool_executions_tenant_status_started",
        "ix_agent_tool_executions_status_lease",
    }
    assert table.c.lease_owner.nullable is True
    assert table.c.lease_expires_at.nullable is True
    assert table.c.attempt_count.nullable is False
    assert str(table.c.attempt_count.server_default.arg) == "1"
    assert table.c.updated_at.nullable is False
    assert _foreign_key_specs(table)["fk_agent_tool_executions_tenant_run_agent_runs"] == (
        ("tenant_id", "run_id"),
        ("agent_runs.tenant_id", "agent_runs.id"),
        "CASCADE",
    )


def test_runtime_tables_and_indexes_compile_for_postgresql():
    dialect = postgresql.dialect()
    tables = (
        AgentRun.__table__,
        AgentRunCommand.__table__,
        AgentRunEvent.__table__,
        SessionContextState.__table__,
        AgentToolExecution.__table__,
    )

    for table in tables:
        ddl = str(CreateTable(table).compile(dialect=dialect))
        assert f"CREATE TABLE {table.name}" in ddl
        for index in table.indexes:
            index_ddl = str(CreateIndex(index).compile(dialect=dialect))
            assert index.name in index_ddl

    source_execution_ddl = str(
        CreateIndex(
            next(index for index in AgentRun.__table__.indexes if index.name == "uq_agent_runs_source_execution")
        ).compile(dialect=dialect)
    )
    active_lane_ddl = str(
        CreateIndex(next(index for index in AgentRun.__table__.indexes if index.name == "uq_agent_runs_active_lane")).compile(
            dialect=dialect
        )
    )
    assert "WHERE source_execution_id IS NOT NULL" in source_execution_ddl
    assert "WHERE scheduling_lane_key IS NOT NULL AND lane_held IS true" in active_lane_ddl
