"""Apply the reviewed post-main schema as one reversible revision.

Revision ID: unify_runtime_group_schema
Revises: add_title_to_agent_focus_items
Create Date: 2026-07-16 12:00:00

This revision is intentionally based directly on upstream/main.  It replaces
the feature branch's former Directory, Experience, Group, unified-chat, model
capability, Runtime, Workspace, delivery, cursor, and merge revisions, and it
brings four historical ORM baseline tables under explicit Alembic ownership.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "unify_runtime_group_schema"
down_revision: str | None = "add_title_to_agent_focus_items"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


UPGRADE_PHASES = (
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
DOWNGRADE_PHASES = tuple(reversed(UPGRADE_PHASES))

REMOVED_TEMPLATE_COLUMNS = ("bootstrap_content",)
RESTORE_TEMPLATE_COLUMNS = {"bootstrap_content": "TEXT"}
# A former branch migration added tenants.planning_model_id.  The frozen Group
# contract and current code use the platform MULTI_AGENT_PLANNING_MODEL_ID;
# there is no tenant override in the ORM or Runtime resolver.
TENANT_COLUMNS_ADDED: tuple[str, ...] = ()

RUNTIME_TABLES = (
    "agent_runs",
    "agent_run_commands",
    "agent_run_events",
    "agent_tool_executions",
    "session_context_states",
)
RUNTIME_COLUMNS = {
    "agent_runs": (
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
    ),
    "agent_run_commands": (
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
    ),
    "agent_run_events": (
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
    ),
    "agent_tool_executions": (
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
    ),
    "session_context_states": (
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
    ),
}
RUNTIME_UNIQUES = {
    "agent_runs": {"uq_agent_runs_tenant_id_id": ("tenant_id", "id")},
    "agent_run_commands": {
        "uq_agent_run_commands_run_idempotency": ("run_id", "idempotency_key")
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
        "uq_agent_tool_executions_run_tool_call": ("run_id", "tool_call_id")
    },
    "session_context_states": {
        "uq_session_context_states_session_id": ("session_id",)
    },
}
RUNTIME_CHECKS = {
    "agent_runs": {
        "ck_agent_runs_source_type": (
            "source_type IN ('chat', 'trigger', 'task', 'a2a', 'heartbeat')"
        ),
        "ck_agent_runs_run_kind": (
            "run_kind IN ('foreground', 'background', 'delegated', 'orchestration')"
        ),
        "ck_agent_runs_runtime_type": "runtime_type IN ('legacy', 'langgraph')",
        "ck_agent_runs_delivery_status": (
            "delivery_status IN ('not_required', 'pending', 'delivered', 'failed')"
        ),
        "ck_agent_runs_langgraph_model": (
            "runtime_type <> 'langgraph' OR model_id IS NOT NULL"
        ),
        "ck_agent_runs_lane_holder_key": (
            "lane_held = false OR scheduling_lane_key IS NOT NULL"
        ),
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
        "ck_agent_runs_model_turn_limit": (
            "(run_kind = 'orchestration' AND model_turn_limit IS NULL) OR "
            "(run_kind <> 'orchestration' AND model_turn_limit > 0)"
        ),
    },
    "agent_run_commands": {
        "ck_agent_run_commands_command_type": (
            "command_type IN ('start', 'resume', 'cancel')"
        ),
        "ck_agent_run_commands_status": (
            "status IN ('pending', 'claimed', 'applied', 'rejected')"
        ),
        "ck_agent_run_commands_attempt_count": "attempt_count >= 0",
    },
    "agent_run_events": {
        "ck_agent_run_events_event_type": (
            "event_type IN ('run_created', 'status_changed', 'waiting_started', 'resumed', "
            "'evidence_added', 'verification_updated', 'run_completed', 'run_failed', "
            "'run_cancelled', 'delivery_succeeded', 'delivery_failed', "
            "'channel_delivery_delivered', 'channel_delivery_failed')"
        )
    },
    "agent_tool_executions": {
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
    },
    "session_context_states": {
        "ck_session_context_states_version": "version >= 1"
    },
}
RUNTIME_INDEXES = {
    "ix_agent_runs_tenant_thread_created_at": (
        "agent_runs",
        ("tenant_id", "runtime_thread_id", "created_at", "id"),
    ),
    "ix_agent_runs_session_created_at": (
        "agent_runs",
        ("session_id", "created_at"),
    ),
    "ix_agent_runs_parent_run_id": ("agent_runs", ("parent_run_id",)),
    "ix_agent_runs_root_run_id": ("agent_runs", ("root_run_id",)),
    "ix_agent_runs_source": ("agent_runs", ("source_type", "source_id")),
    "uq_agent_runs_source_execution": (
        "agent_runs",
        ("source_type", "source_execution_id"),
    ),
    "uq_agent_runs_active_lane": ("agent_runs", ("scheduling_lane_key",)),
    "ix_agent_runs_lane_candidate_order": (
        "agent_runs",
        (
            "scheduling_lane_key",
            "scheduling_position_created_at",
            "scheduling_position_id",
            "created_at",
            "id",
        ),
    ),
    "ix_agent_run_commands_status_claim_created": (
        "agent_run_commands",
        ("status", "claim_expires_at", "created_at"),
    ),
    "ix_agent_run_commands_run_created": (
        "agent_run_commands",
        ("run_id", "created_at", "id"),
    ),
    "ix_agent_run_events_run_created": (
        "agent_run_events",
        ("run_id", "created_at"),
    ),
    "ix_agent_run_events_tenant_type_created": (
        "agent_run_events",
        ("tenant_id", "event_type", "created_at"),
    ),
    "ix_agent_tool_executions_tenant_status_started": (
        "agent_tool_executions",
        ("tenant_id", "status", "started_at"),
    ),
    "ix_agent_tool_executions_status_lease": (
        "agent_tool_executions",
        ("status", "lease_expires_at"),
    ),
    "ix_session_context_states_tenant_agent_updated": (
        "session_context_states",
        ("tenant_id", "agent_id", "updated_at"),
    ),
}

BASELINE_ORM_TABLES = (
    "gateway_messages",
    "notifications",
    "tenant_settings",
    "trigger_executions",
)
BASELINE_ORM_COLUMNS = {
    "gateway_messages": (
        "id",
        "agent_id",
        "sender_agent_id",
        "sender_user_id",
        "conversation_id",
        "content",
        "status",
        "result",
        "created_at",
        "delivered_at",
        "completed_at",
    ),
    "notifications": (
        "id",
        "user_id",
        "agent_id",
        "type",
        "title",
        "body",
        "link",
        "ref_id",
        "sender_name",
        "is_read",
        "created_at",
    ),
    "tenant_settings": (
        "tenant_id",
        "key",
        "value",
        "updated_at",
    ),
    "trigger_executions": (
        "id",
        "trigger_id",
        "agent_id",
        "source",
        "status",
        "idempotency_key",
        "payload",
        "payload_text",
        "lease_owner",
        "lease_expires_at",
        "scheduled_at",
        "started_at",
        "finished_at",
        "last_error",
        "created_at",
    ),
}
BASELINE_ORM_INDEXES = {
    "gateway_messages": (),
    "notifications": (
        "ix_notifications_user_id",
        "ix_notifications_agent_id",
        "ix_notifications_created_at",
    ),
    "tenant_settings": (),
    "trigger_executions": (
        "ix_trigger_executions_trigger_id",
        "ix_trigger_executions_agent_id",
        "ix_trigger_executions_status_scheduled",
    ),
}
BASELINE_ORM_CONSTRAINTS = {
    "gateway_messages": (
        "gateway_messages_pkey",
        "gateway_messages_agent_id_fkey",
        "gateway_messages_sender_agent_id_fkey",
        "gateway_messages_sender_user_id_fkey",
    ),
    "notifications": (
        "notifications_pkey",
        "notifications_user_id_fkey",
        "notifications_agent_id_fkey",
    ),
    "tenant_settings": (
        "tenant_settings_pkey",
        "tenant_settings_tenant_id_fkey",
    ),
    "trigger_executions": (
        "trigger_executions_pkey",
        "trigger_executions_trigger_id_fkey",
        "trigger_executions_agent_id_fkey",
        "uq_trigger_execution_idempotency",
    ),
}

_BASELINE_ORM_TABLE_OBJECTS = {
    table_name: {
        f"table:{table_name}",
        *(f"column:{table_name}.{name}" for name in BASELINE_ORM_COLUMNS[table_name]),
        *(f"index:{name}" for name in BASELINE_ORM_INDEXES[table_name]),
        *(
            f"constraint:{name}"
            for name in BASELINE_ORM_CONSTRAINTS[table_name]
        ),
    }
    for table_name in BASELINE_ORM_TABLES
}

_GATEWAY_MESSAGES_LEGACY_OBJECTS = (
    _BASELINE_ORM_TABLE_OBJECTS["gateway_messages"]
    - {"column:gateway_messages.conversation_id"}
)
_NOTIFICATION_EXTENSION_OBJECTS = {
    "column:notifications.agent_id",
    "column:notifications.sender_name",
    "index:ix_notifications_agent_id",
    "constraint:notifications_agent_id_fkey",
}
_NOTIFICATION_CORE_OBJECTS = (
    _BASELINE_ORM_TABLE_OBJECTS["notifications"]
    - {f"index:{name}" for name in BASELINE_ORM_INDEXES["notifications"]}
)
_NOTIFICATION_LEGACY_CORE_OBJECTS = (
    _NOTIFICATION_CORE_OBJECTS - _NOTIFICATION_EXTENSION_OBJECTS
)
_TRIGGER_EXECUTION_CORE_OBJECTS = (
    _BASELINE_ORM_TABLE_OBJECTS["trigger_executions"]
    - {
        f"index:{name}"
        for name in BASELINE_ORM_INDEXES["trigger_executions"]
    }
)

_PRECREATED_PHASE_OBJECTS = {
    "experience_library": {
        "table:experience_entries",
        "table:experience_references",
        "index:ix_experience_entries_draft_of_id",
        "index:ix_experience_entries_tenant_id",
        "index:ix_experience_entries_status",
        "index:ix_experience_entries_visibility_scope",
        "index:ix_experience_entries_origin",
        "index:ix_experience_entries_created_at",
        "index:ix_experience_references_entry_id",
        "index:ix_experience_references_kind",
        "index:ix_experience_references_tenant_id",
        "index:ix_experience_references_agent_id",
        "index:ix_experience_references_created_at",
    },
    "group_domain": {
        "table:groups",
        "table:group_members",
        "index:ix_groups_tenant_id_deleted_at",
        "index:ix_group_members_participant_id",
    },
    "unified_chat": {
        "column:chat_sessions.tenant_id",
        "column:chat_sessions.session_type",
        "column:chat_sessions.group_id",
        "column:chat_sessions.created_by_participant_id",
        "column:chat_sessions.deleted_at",
        "column:chat_sessions.updated_at",
        "column:chat_messages.mentions",
        "index:ix_chat_sessions_tenant_id",
        "index:ix_chat_sessions_group_id",
        "index:uq_chat_sessions_primary_direct",
        "index:uq_chat_sessions_primary_group",
        "constraint:fk_chat_sessions_tenant_id_tenants",
        "constraint:fk_chat_sessions_group_id_groups",
        "constraint:fk_chat_sessions_created_by_participant_id_participants",
        "constraint:uq_chat_sessions_tenant_id_id",
        "constraint:ck_chat_sessions_session_type",
    },
    "llm_capabilities": {
        "column:llm_models.context_window_tokens",
        "column:llm_models.context_window_tokens_override",
        "column:llm_models.max_input_tokens",
        "column:llm_models.max_input_tokens_override",
        "column:llm_models.capability_source",
        "column:llm_models.capability_checked_at",
        "column:llm_models.supports_tool_calling",
        "column:llm_models.tool_calling_capability_source",
        "column:llm_models.tool_calling_checked_at",
        "column:llm_models.tool_calling_error",
        "constraint:ck_llm_models_context_window_tokens_positive",
        "constraint:ck_llm_models_context_window_tokens_override_positive",
        "constraint:ck_llm_models_max_input_tokens_positive",
        "constraint:ck_llm_models_max_input_tokens_override_positive",
        "constraint:ck_llm_models_capability_source",
        "constraint:ck_llm_models_tool_calling_capability_source",
    },
    "runtime_schema": {
        *(f"table:{name}" for name in RUNTIME_TABLES),
        *(f"index:{name}" for name in RUNTIME_INDEXES),
        *(
            f"constraint:{name}"
            for checks in RUNTIME_CHECKS.values()
            for name in checks
        ),
        *(
            f"constraint:{name}"
            for uniques in RUNTIME_UNIQUES.values()
            for name in uniques
        ),
    },
}

_DERIVED_SESSION_TYPE = """
CASE
    WHEN cs.source_channel = 'agent' THEN 'a2a'
    WHEN cs.source_channel = 'trigger' THEN 'trigger'
    WHEN COALESCE(cs.is_group, false) = true THEN 'group'
    ELSE 'direct'
END
""".strip()

UNIFIED_CHAT_UPGRADE_SQL = (
    "LOCK TABLE chat_sessions, chat_messages IN ACCESS EXCLUSIVE MODE",
    "UPDATE chat_sessions FROM agents/users/participants with audited tenant and type backfill",
    "UPDATE chat_messages SET mentions = '[]'::jsonb WHERE mentions IS NULL",
)
UNIFIED_CHAT_AUDIT_MESSAGES = (
    "source Agent tenant is missing",
    "User tenant is missing",
    "Agent and User tenants disagree",
    "duplicate direct primary sessions",
    "duplicate group primary sessions",
)
UNIFIED_CHAT_DOWNGRADE_SQL = (
    f"session_type IS DISTINCT FROM ({_DERIVED_SESSION_TYPE})"
)
UNIFIED_CHAT_DOWNGRADE_AUDIT_MESSAGES = (
    "sessions use new-only semantics",
    "session_type is not legacy-representable",
    "messages contain mentions",
    "messages use nullable legacy identities",
)


def _scalar_count(bind: sa.Connection, sql: str) -> int:
    result = bind.execute(sa.text(sql))
    scalar_one = getattr(result, "scalar_one", None)
    if callable(scalar_one):
        return int(scalar_one())
    scalar = getattr(result, "scalar", None)
    if callable(scalar):
        return int(scalar())
    raise RuntimeError("Migration audit query did not return a scalar")


def _require_zero(bind: sa.Connection, sql: str, message: str) -> None:
    count = _scalar_count(bind, sql)
    if count:
        raise RuntimeError(f"{message}: {count} row(s)")


def _require_empty_tables(
    bind: sa.Connection,
    table_names: Sequence[str],
    *,
    domain: str,
) -> None:
    if not table_names:
        return
    quoted = ", ".join(f'"{table_name}"' for table_name in table_names)
    bind.execute(sa.text(f"LOCK TABLE {quoted} IN ACCESS EXCLUSIVE MODE"))
    for table_name in table_names:
        result = bind.execute(sa.text(f'SELECT 1 FROM "{table_name}" LIMIT 1'))
        if result.first() is not None:
            raise RuntimeError(
                f"Refusing to downgrade {domain} because {table_name} contains data"
            )


def _schema_object_names(bind: sa.Connection) -> set[str]:
    """Return the named public-schema objects used by the fresh-DB guard."""
    result = bind.execute(
        sa.text(
            """
            SELECT 'table:' || tablename
            FROM pg_catalog.pg_tables
            WHERE schemaname = current_schema()
            UNION ALL
            SELECT 'column:' || table_name || '.' || column_name
            FROM information_schema.columns
            WHERE table_schema = current_schema()
            UNION ALL
            SELECT 'index:' || indexname
            FROM pg_catalog.pg_indexes
            WHERE schemaname = current_schema()
            UNION ALL
            SELECT 'constraint:' || constraint_name
            FROM information_schema.table_constraints
            WHERE constraint_schema = current_schema()
            """
        )
    )
    return {str(value) for value in result.scalars().all()}


def _precreated_phase_state(phase: str, actual: set[str]) -> bool:
    """Accept only an entirely absent or entirely metadata-precreated phase."""
    expected = _PRECREATED_PHASE_OBJECTS[phase]
    present = expected.intersection(actual)
    if not present:
        return False
    if present == expected:
        return True
    missing = sorted(expected - present)
    raise RuntimeError(
        f"Refusing partially precreated {phase} schema; missing: {', '.join(missing)}"
    )


def _baseline_orm_table_plan(
    table_name: str,
    actual: set[str],
) -> tuple[str, tuple[str, ...]]:
    """Classify one historical baseline table without changing the schema."""
    expected = _BASELINE_ORM_TABLE_OBJECTS[table_name]
    present = expected.intersection(actual)
    if not present:
        return ("create", ())

    if table_name == "gateway_messages":
        if present == expected:
            return ("keep", ())
        if present == _GATEWAY_MESSAGES_LEGACY_OBJECTS:
            return ("upgrade_gateway_messages", ())

    elif table_name == "notifications":
        missing_indexes = tuple(
            name
            for name in BASELINE_ORM_INDEXES[table_name]
            if f"index:{name}" not in present
        )
        if _NOTIFICATION_CORE_OBJECTS.issubset(present):
            return ("normalize_notifications", missing_indexes)
        if (
            _NOTIFICATION_LEGACY_CORE_OBJECTS.issubset(present)
            and not _NOTIFICATION_EXTENSION_OBJECTS.intersection(present)
        ):
            return ("upgrade_legacy_notifications", missing_indexes)

    elif table_name == "tenant_settings":
        if present == expected:
            return ("keep", ())

    elif table_name == "trigger_executions":
        if _TRIGGER_EXECUTION_CORE_OBJECTS.issubset(present):
            missing_indexes = tuple(
                name
                for name in BASELINE_ORM_INDEXES[table_name]
                if f"index:{name}" not in present
            )
            return ("keep", missing_indexes)

    missing = sorted(expected - present)
    raise RuntimeError(
        f"Refusing unknown partial baseline ORM table {table_name}; "
        f"missing: {', '.join(missing)}"
    )


def _finish_precreated_phase(phase: str, bind: sa.Connection) -> None:
    """Apply only non-metadata work when 001 already created the final shape."""
    if phase == "unified_chat":
        bind.execute(
            sa.text("LOCK TABLE chat_sessions, chat_messages IN ACCESS EXCLUSIVE MODE")
        )
        _audit_unified_chat_final(bind)
        bind.execute(sa.text("DROP INDEX IF EXISTS uq_chat_sessions_primary_platform"))
    elif phase == "llm_capabilities":
        _backfill_legacy_tool_calling_capabilities()
    elif phase == "runtime_schema":
        op.execute(sa.text(f'CREATE SCHEMA IF NOT EXISTS "{_CHECKPOINT_SCHEMA}"'))


def _run_phase(phase: str, *, downgrade: bool) -> None:
    direction = "downgrade" if downgrade else "upgrade"
    if not downgrade and phase in _PRECREATED_PHASE_OBJECTS:
        bind = op.get_bind()
        if _precreated_phase_state(phase, _schema_object_names(bind)):
            _finish_precreated_phase(phase, bind)
            return
    function = globals().get(f"_{direction}_{phase}")
    if not callable(function):
        raise RuntimeError(f"Missing migration phase: {direction} {phase}")
    function()


def upgrade() -> None:
    for phase in UPGRADE_PHASES:
        _run_phase(phase, downgrade=False)


def downgrade() -> None:
    for phase in DOWNGRADE_PHASES:
        _run_phase(phase, downgrade=True)


# ---------------------------------------------------------------------------
# Directory search indexes


_DIRECTORY_INDEX_SQL = (
    "CREATE INDEX ix_agents_tenant_access_status_name "
    "ON agents (tenant_id, access_mode, status, name)",
    "CREATE INDEX ix_agents_tenant_creator_access "
    "ON agents (tenant_id, creator_id, access_mode)",
    "CREATE INDEX ix_agent_permissions_agent_scope_scopeid_level "
    "ON agent_permissions (agent_id, scope_type, scope_id, access_level)",
    "CREATE INDEX ix_agent_permissions_scopeid_scope_agent "
    "ON agent_permissions (scope_id, scope_type, agent_id)",
    "CREATE INDEX ix_agent_agent_relationships_agent_target "
    "ON agent_agent_relationships (agent_id, target_agent_id)",
    "CREATE INDEX ix_org_members_tenant_status_name "
    "ON org_members (tenant_id, status, name)",
    "CREATE INDEX ix_org_members_tenant_user "
    "ON org_members (tenant_id, user_id)",
)


def _upgrade_directory_indexes() -> None:
    for statement in _DIRECTORY_INDEX_SQL:
        op.execute(sa.text(statement))


def _downgrade_directory_indexes() -> None:
    for statement in reversed(_DIRECTORY_INDEX_SQL):
        index_name = statement.split(" ", 3)[2]
        op.execute(sa.text(f"DROP INDEX IF EXISTS {index_name}"))


# ---------------------------------------------------------------------------
# Historical baseline ORM tables omitted by the original Alembic bootstrap


def _create_gateway_messages() -> None:
    op.create_table(
        "gateway_messages",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("agent_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("sender_agent_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("sender_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("conversation_id", sa.String(length=100), nullable=True),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("result", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["agent_id"], ["agents.id"]),
        sa.ForeignKeyConstraint(["sender_agent_id"], ["agents.id"]),
        sa.ForeignKeyConstraint(["sender_user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )


def _create_notifications() -> None:
    op.create_table(
        "notifications",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("agent_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("type", sa.String(length=50), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("link", sa.String(length=500), nullable=True),
        sa.Column("ref_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("sender_name", sa.String(length=100), nullable=True),
        sa.Column("is_read", sa.Boolean(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["agent_id"], ["agents.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    for name, columns in (
        ("ix_notifications_user_id", ["user_id"]),
        ("ix_notifications_agent_id", ["agent_id"]),
        ("ix_notifications_created_at", ["created_at"]),
    ):
        op.create_index(name, "notifications", columns, unique=False)


def _create_tenant_settings() -> None:
    op.create_table(
        "tenant_settings",
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("key", sa.String(length=100), nullable=False),
        sa.Column(
            "value",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("tenant_id", "key"),
    )


def _create_trigger_executions() -> None:
    op.create_table(
        "trigger_executions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("trigger_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("agent_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("idempotency_key", sa.String(length=255), nullable=False),
        sa.Column(
            "payload",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("payload_text", sa.Text(), nullable=False),
        sa.Column("lease_owner", sa.String(length=128), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "scheduled_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["trigger_id"],
            ["agent_triggers.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["agent_id"],
            ["agents.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "trigger_id",
            "idempotency_key",
            name="uq_trigger_execution_idempotency",
        ),
    )
    for name, columns in (
        ("ix_trigger_executions_trigger_id", ["trigger_id"]),
        ("ix_trigger_executions_agent_id", ["agent_id"]),
        (
            "ix_trigger_executions_status_scheduled",
            ["status", "scheduled_at"],
        ),
    ):
        op.create_index(name, "trigger_executions", columns, unique=False)


_BASELINE_ORM_CREATE = {
    "gateway_messages": _create_gateway_messages,
    "notifications": _create_notifications,
    "tenant_settings": _create_tenant_settings,
    "trigger_executions": _create_trigger_executions,
}


def _create_missing_baseline_index(table_name: str, index_name: str) -> None:
    columns = {
        "ix_notifications_user_id": ("user_id",),
        "ix_notifications_agent_id": ("agent_id",),
        "ix_notifications_created_at": ("created_at",),
        "ix_trigger_executions_trigger_id": ("trigger_id",),
        "ix_trigger_executions_agent_id": ("agent_id",),
        "ix_trigger_executions_status_scheduled": ("status", "scheduled_at"),
    }[index_name]
    kwargs: dict[str, object] = {}
    if index_name == "ix_notifications_agent_id":
        # Preserve the established 016 migration semantics for upgraded
        # installations. Fresh databases use the current ORM's full index.
        kwargs["postgresql_where"] = sa.text("agent_id IS NOT NULL")
    op.create_index(
        index_name,
        table_name,
        list(columns),
        unique=False,
        **kwargs,
    )


def _upgrade_gateway_messages_legacy_shape() -> None:
    op.add_column(
        "gateway_messages",
        sa.Column("conversation_id", sa.String(length=100), nullable=True),
    )


def _normalize_notifications_legacy_shape(*, add_extension: bool) -> None:
    if add_extension:
        op.add_column(
            "notifications",
            sa.Column("agent_id", postgresql.UUID(as_uuid=True), nullable=True),
        )
        op.add_column(
            "notifications",
            sa.Column("sender_name", sa.String(length=100), nullable=True),
        )
        op.create_foreign_key(
            "notifications_agent_id_fkey",
            "notifications",
            "agents",
            ["agent_id"],
            ["id"],
        )
    # Migration 016 made user_id optional. Repeating DROP NOT NULL is safe and
    # also repairs databases where the columns were added outside Alembic.
    op.alter_column(
        "notifications",
        "user_id",
        existing_type=postgresql.UUID(as_uuid=True),
        nullable=True,
    )


def _upgrade_baseline_orm_tables() -> None:
    actual = _schema_object_names(op.get_bind())
    # Classify every independent table before the first DDL statement. An
    # unknown partial shape must not leave the other baseline tables half-made.
    plans = {
        table_name: _baseline_orm_table_plan(table_name, actual)
        for table_name in BASELINE_ORM_TABLES
    }
    for table_name in BASELINE_ORM_TABLES:
        action, missing_indexes = plans[table_name]
        if action == "create":
            _BASELINE_ORM_CREATE[table_name]()
            continue
        if action == "upgrade_gateway_messages":
            _upgrade_gateway_messages_legacy_shape()
        elif action == "upgrade_legacy_notifications":
            _normalize_notifications_legacy_shape(add_extension=True)
        elif action == "normalize_notifications":
            _normalize_notifications_legacy_shape(add_extension=False)
        for index_name in missing_indexes:
            _create_missing_baseline_index(table_name, index_name)


def _downgrade_baseline_orm_tables() -> None:
    # These tables belong to the historical application baseline, not this
    # feature revision. Never delete pre-existing production data on downgrade.
    pass


# ---------------------------------------------------------------------------
# Experience library (final schema, no intermediate narrative columns)


def _upgrade_experience_library() -> None:
    op.create_table(
        "experience_entries",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("draft_of_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("applicability", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("tags", postgresql.JSON(), nullable=False),
        sa.Column("visibility_scope", sa.String(length=16), nullable=False),
        sa.Column("visibility_scope_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("origin", sa.String(length=20), nullable=False),
        sa.Column("origin_session_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("origin_agent_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("reviewed_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("last_reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("retired_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["draft_of_id"],
            ["experience_entries.id"],
            name="fk_experience_entries_draft_of_id",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    for name, columns in {
        "ix_experience_entries_draft_of_id": ["draft_of_id"],
        "ix_experience_entries_tenant_id": ["tenant_id"],
        "ix_experience_entries_status": ["status"],
        "ix_experience_entries_visibility_scope": ["visibility_scope"],
        "ix_experience_entries_origin": ["origin"],
        "ix_experience_entries_created_at": ["created_at"],
    }.items():
        op.create_index(name, "experience_entries", columns, unique=False)

    op.create_table(
        "experience_references",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("entry_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("kind", sa.String(length=10), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("agent_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("session_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("message_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["entry_id"],
            ["experience_entries.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    for name, columns in {
        "ix_experience_references_entry_id": ["entry_id"],
        "ix_experience_references_kind": ["kind"],
        "ix_experience_references_tenant_id": ["tenant_id"],
        "ix_experience_references_agent_id": ["agent_id"],
        "ix_experience_references_created_at": ["created_at"],
    }.items():
        op.create_index(name, "experience_references", columns, unique=False)


def _downgrade_experience_library() -> None:
    op.drop_table("experience_references")
    op.drop_table("experience_entries")


# ---------------------------------------------------------------------------
# Native Group domain


def _upgrade_group_domain() -> None:
    op.create_table(
        "groups",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "created_by_participant_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name="fk_groups_tenant_id_tenants",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_participant_id"],
            ["participants.id"],
            name="fk_groups_created_by_participant_id_participants",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_groups"),
    )
    op.create_index(
        "ix_groups_tenant_id_deleted_at",
        "groups",
        ["tenant_id", "deleted_at"],
        unique=False,
    )

    op.create_table(
        "group_members",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("group_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("participant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "role",
            sa.String(length=20),
            server_default=sa.text("'member'"),
            nullable=False,
        ),
        sa.Column(
            "joined_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("removed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "session_read_state",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "role IN ('manager', 'member')",
            name="ck_group_members_role",
        ),
        sa.ForeignKeyConstraint(
            ["group_id"],
            ["groups.id"],
            name="fk_group_members_group_id_groups",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["participant_id"],
            ["participants.id"],
            name="fk_group_members_participant_id_participants",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_group_members"),
        sa.UniqueConstraint(
            "group_id",
            "participant_id",
            name="uq_group_members_group_participant",
        ),
    )
    op.create_index(
        "ix_group_members_participant_id",
        "group_members",
        ["participant_id"],
        unique=False,
    )


def _downgrade_group_domain() -> None:
    bind = op.get_bind()
    _require_empty_tables(
        bind,
        ("group_members", "groups"),
        domain="Group domain schema",
    )
    op.drop_table("group_members")
    op.drop_table("groups")


# ---------------------------------------------------------------------------
# Unified direct, Group, A2A, and Trigger chat storage


def _audit_unified_chat_upgrade(bind: sa.Connection) -> None:
    candidate = "(cs.tenant_id IS NULL OR cs.session_type IS NULL)"
    _require_zero(
        bind,
        f"""
        SELECT count(*)
        FROM chat_sessions cs
        LEFT JOIN agents a ON a.id = cs.agent_id
        WHERE {candidate}
          AND cs.tenant_id IS NULL
          AND (cs.agent_id IS NULL OR a.tenant_id IS NULL)
        """,
        "Cannot backfill chat_sessions.tenant_id because the source Agent tenant is missing",
    )
    _require_zero(
        bind,
        f"""
        SELECT count(*)
        FROM chat_sessions cs
        LEFT JOIN users u ON u.id = cs.user_id
        WHERE {candidate}
          AND ({_DERIVED_SESSION_TYPE}) = 'direct'
          AND (cs.user_id IS NULL OR u.tenant_id IS NULL)
        """,
        "Cannot migrate direct chat session because the User tenant is missing",
    )
    _require_zero(
        bind,
        f"""
        SELECT count(*)
        FROM chat_sessions cs
        JOIN agents a ON a.id = cs.agent_id
        JOIN users u ON u.id = cs.user_id
        WHERE {candidate}
          AND ({_DERIVED_SESSION_TYPE}) = 'direct'
          AND a.tenant_id IS DISTINCT FROM u.tenant_id
        """,
        "Cannot migrate direct chat session because Agent and User tenants disagree",
    )


def _backfill_unified_chat(bind: sa.Connection) -> None:
    statements = (
        """
        UPDATE chat_sessions cs
        SET tenant_id = a.tenant_id
        FROM agents a
        WHERE cs.agent_id = a.id
          AND cs.tenant_id IS NULL
        """,
        f"""
        UPDATE chat_sessions cs
        SET session_type = {_DERIVED_SESSION_TYPE}
        WHERE cs.session_type IS NULL
        """,
        """
        UPDATE chat_sessions cs
        SET created_by_participant_id = p.id
        FROM participants p
        WHERE cs.created_by_participant_id IS NULL
          AND cs.session_type IN ('direct', 'group')
          AND p.type = 'user'
          AND p.ref_id = cs.user_id
        """,
        """
        UPDATE chat_sessions
        SET created_by_participant_id = participant_id
        WHERE created_by_participant_id IS NULL
          AND session_type = 'a2a'
          AND participant_id IS NOT NULL
        """,
        """
        UPDATE chat_sessions cs
        SET created_by_participant_id = p.id
        FROM participants p
        WHERE cs.created_by_participant_id IS NULL
          AND cs.session_type = 'trigger'
          AND p.type = 'agent'
          AND p.ref_id = cs.agent_id
        """,
        "UPDATE chat_sessions SET updated_at = now() WHERE updated_at IS NULL",
        "UPDATE chat_messages SET mentions = '[]'::jsonb WHERE mentions IS NULL",
    )
    for statement in statements:
        bind.execute(sa.text(statement))


def _audit_unified_chat_final(bind: sa.Connection) -> None:
    _require_zero(
        bind,
        """
        SELECT count(*) FROM chat_sessions
        WHERE tenant_id IS NULL OR session_type IS NULL OR updated_at IS NULL
        """,
        "Unified chat backfill left required chat_sessions fields empty",
    )
    _require_zero(
        bind,
        "SELECT count(*) FROM chat_messages WHERE mentions IS NULL",
        "Unified chat backfill left chat_messages.mentions empty",
    )
    _require_zero(
        bind,
        """
        SELECT count(*) FROM (
            SELECT tenant_id, agent_id, user_id
            FROM chat_sessions
            WHERE session_type = 'direct'
              AND is_primary = true
              AND deleted_at IS NULL
            GROUP BY tenant_id, agent_id, user_id
            HAVING count(*) > 1
        ) duplicates
        """,
        "Cannot create direct primary-session uniqueness because duplicate primaries exist",
    )
    _require_zero(
        bind,
        """
        SELECT count(*) FROM (
            SELECT group_id
            FROM chat_sessions
            WHERE session_type = 'group'
              AND group_id IS NOT NULL
              AND is_primary = true
              AND deleted_at IS NULL
            GROUP BY group_id
            HAVING count(*) > 1
        ) duplicates
        """,
        "Cannot create group primary-session uniqueness because duplicate primaries exist",
    )


def _upgrade_unified_chat() -> None:
    op.add_column(
        "chat_sessions",
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "chat_sessions",
        sa.Column("session_type", sa.String(length=20), nullable=True),
    )
    op.add_column(
        "chat_sessions",
        sa.Column("group_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "chat_sessions",
        sa.Column(
            "created_by_participant_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
    )
    op.add_column(
        "chat_sessions",
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "chat_sessions",
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "chat_messages",
        sa.Column(
            "mentions",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )

    bind = op.get_bind()
    bind.execute(
        sa.text("LOCK TABLE chat_sessions, chat_messages IN ACCESS EXCLUSIVE MODE")
    )
    _audit_unified_chat_upgrade(bind)
    _backfill_unified_chat(bind)
    _audit_unified_chat_final(bind)

    op.alter_column(
        "chat_sessions",
        "tenant_id",
        existing_type=postgresql.UUID(as_uuid=True),
        nullable=False,
    )
    op.alter_column(
        "chat_sessions",
        "session_type",
        existing_type=sa.String(length=20),
        nullable=False,
    )
    op.alter_column(
        "chat_sessions",
        "agent_id",
        existing_type=postgresql.UUID(as_uuid=True),
        nullable=True,
    )
    op.alter_column(
        "chat_sessions",
        "user_id",
        existing_type=postgresql.UUID(as_uuid=True),
        nullable=True,
    )
    op.alter_column(
        "chat_sessions",
        "updated_at",
        existing_type=sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.func.now(),
    )
    op.alter_column(
        "chat_messages",
        "agent_id",
        existing_type=postgresql.UUID(as_uuid=True),
        nullable=True,
    )
    op.alter_column(
        "chat_messages",
        "user_id",
        existing_type=postgresql.UUID(as_uuid=True),
        nullable=True,
    )
    op.alter_column(
        "chat_messages",
        "mentions",
        existing_type=postgresql.JSONB(astext_type=sa.Text()),
        nullable=False,
        server_default=sa.text("'[]'::jsonb"),
    )

    op.create_foreign_key(
        "fk_chat_sessions_tenant_id_tenants",
        "chat_sessions",
        "tenants",
        ["tenant_id"],
        ["id"],
    )
    op.create_foreign_key(
        "fk_chat_sessions_group_id_groups",
        "chat_sessions",
        "groups",
        ["group_id"],
        ["id"],
    )
    op.create_foreign_key(
        "fk_chat_sessions_created_by_participant_id_participants",
        "chat_sessions",
        "participants",
        ["created_by_participant_id"],
        ["id"],
    )
    op.create_unique_constraint(
        "uq_chat_sessions_tenant_id_id",
        "chat_sessions",
        ["tenant_id", "id"],
    )
    op.create_check_constraint(
        "ck_chat_sessions_session_type",
        "chat_sessions",
        "session_type IN ('direct', 'group', 'a2a', 'trigger')",
    )
    op.drop_index("uq_chat_sessions_primary_platform", table_name="chat_sessions")
    op.create_index(
        "ix_chat_sessions_tenant_id",
        "chat_sessions",
        ["tenant_id"],
        unique=False,
    )
    op.create_index(
        "ix_chat_sessions_group_id",
        "chat_sessions",
        ["group_id"],
        unique=False,
    )
    op.create_index(
        "uq_chat_sessions_primary_direct",
        "chat_sessions",
        ["tenant_id", "agent_id", "user_id"],
        unique=True,
        postgresql_where=sa.text(
            "session_type = 'direct' AND is_primary = true AND deleted_at IS NULL"
        ),
    )
    op.create_index(
        "uq_chat_sessions_primary_group",
        "chat_sessions",
        ["group_id"],
        unique=True,
        postgresql_where=sa.text(
            "session_type = 'group' AND group_id IS NOT NULL "
            "AND is_primary = true AND deleted_at IS NULL"
        ),
    )


def _audit_unified_chat_downgrade(bind: sa.Connection) -> None:
    checks = (
        (
            """
            SELECT count(*) FROM chat_sessions
            WHERE group_id IS NOT NULL
               OR deleted_at IS NOT NULL
               OR agent_id IS NULL
               OR user_id IS NULL
            """,
            "Refusing to downgrade unified chat schema because sessions use new-only semantics",
        ),
        (
            f"""
            SELECT count(*) FROM chat_sessions cs
            WHERE session_type IS DISTINCT FROM ({_DERIVED_SESSION_TYPE})
            """,
            "Refusing to downgrade unified chat schema because session_type is not legacy-representable",
        ),
        (
            "SELECT count(*) FROM chat_messages WHERE mentions IS DISTINCT FROM '[]'::jsonb",
            "Refusing to downgrade unified chat schema because messages contain mentions",
        ),
        (
            "SELECT count(*) FROM chat_messages WHERE agent_id IS NULL OR user_id IS NULL",
            "Refusing to downgrade unified chat schema because messages use nullable legacy identities",
        ),
    )
    for sql, message in checks:
        _require_zero(bind, sql, message)


def _downgrade_unified_chat() -> None:
    bind = op.get_bind()
    bind.execute(
        sa.text("LOCK TABLE chat_sessions, chat_messages IN ACCESS EXCLUSIVE MODE")
    )
    _audit_unified_chat_downgrade(bind)

    for name in (
        "uq_chat_sessions_primary_group",
        "uq_chat_sessions_primary_direct",
        "ix_chat_sessions_group_id",
        "ix_chat_sessions_tenant_id",
    ):
        op.drop_index(name, table_name="chat_sessions")
    op.drop_constraint(
        "ck_chat_sessions_session_type",
        "chat_sessions",
        type_="check",
    )
    op.drop_constraint(
        "uq_chat_sessions_tenant_id_id",
        "chat_sessions",
        type_="unique",
    )
    for name in (
        "fk_chat_sessions_created_by_participant_id_participants",
        "fk_chat_sessions_group_id_groups",
        "fk_chat_sessions_tenant_id_tenants",
    ):
        op.drop_constraint(name, "chat_sessions", type_="foreignkey")

    for table_name, column_name in (
        ("chat_messages", "agent_id"),
        ("chat_messages", "user_id"),
        ("chat_sessions", "agent_id"),
        ("chat_sessions", "user_id"),
    ):
        op.alter_column(
            table_name,
            column_name,
            existing_type=postgresql.UUID(as_uuid=True),
            nullable=False,
        )

    op.drop_column("chat_messages", "mentions")
    for name in (
        "updated_at",
        "deleted_at",
        "created_by_participant_id",
        "group_id",
        "session_type",
        "tenant_id",
    ):
        op.drop_column("chat_sessions", name)
    op.create_index(
        "uq_chat_sessions_primary_platform",
        "chat_sessions",
        ["agent_id", "user_id"],
        unique=True,
        postgresql_where=sa.text(
            "is_primary = true AND source_channel = 'web' AND is_group = false"
        ),
    )


# ---------------------------------------------------------------------------
# LLM Runtime capability metadata


_LLM_CAPABILITY_COLUMNS = {
    "context_window_tokens": lambda: sa.Column(
        "context_window_tokens", sa.Integer(), nullable=True
    ),
    "context_window_tokens_override": lambda: sa.Column(
        "context_window_tokens_override", sa.Integer(), nullable=True
    ),
    "max_input_tokens": lambda: sa.Column(
        "max_input_tokens", sa.Integer(), nullable=True
    ),
    "max_input_tokens_override": lambda: sa.Column(
        "max_input_tokens_override", sa.Integer(), nullable=True
    ),
    "capability_source": lambda: sa.Column(
        "capability_source", sa.String(length=32), nullable=True
    ),
    "capability_checked_at": lambda: sa.Column(
        "capability_checked_at", sa.DateTime(timezone=True), nullable=True
    ),
    "supports_tool_calling": lambda: sa.Column(
        "supports_tool_calling", sa.Boolean(), nullable=True
    ),
    "tool_calling_capability_source": lambda: sa.Column(
        "tool_calling_capability_source", sa.String(length=32), nullable=True
    ),
    "tool_calling_checked_at": lambda: sa.Column(
        "tool_calling_checked_at", sa.DateTime(timezone=True), nullable=True
    ),
    "tool_calling_error": lambda: sa.Column(
        "tool_calling_error", sa.String(length=500), nullable=True
    ),
}
_LLM_CAPABILITY_CHECKS = {
    "ck_llm_models_context_window_tokens_positive": (
        "context_window_tokens IS NULL OR context_window_tokens > 0"
    ),
    "ck_llm_models_context_window_tokens_override_positive": (
        "context_window_tokens_override IS NULL OR context_window_tokens_override > 0"
    ),
    "ck_llm_models_max_input_tokens_positive": (
        "max_input_tokens IS NULL OR max_input_tokens > 0"
    ),
    "ck_llm_models_max_input_tokens_override_positive": (
        "max_input_tokens_override IS NULL OR max_input_tokens_override > 0"
    ),
    "ck_llm_models_capability_source": (
        "capability_source IS NULL OR capability_source IN "
        "('manual', 'provider_api', 'builtin_registry', 'runtime_config')"
    ),
    "ck_llm_models_tool_calling_capability_source": (
        "tool_calling_capability_source IS NULL OR "
        "tool_calling_capability_source IN ('probe', 'builtin_registry')"
    ),
}

_LEGACY_TOOL_CALLING_PROVIDERS = (
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
)


def _upgrade_llm_capabilities() -> None:
    for factory in _LLM_CAPABILITY_COLUMNS.values():
        op.add_column("llm_models", factory())
    for name, expression in _LLM_CAPABILITY_CHECKS.items():
        op.create_check_constraint(name, "llm_models", expression)
    _backfill_legacy_tool_calling_capabilities()


def _backfill_legacy_tool_calling_capabilities() -> None:
    """Preserve already-running cloud models without inferring local support."""
    providers = ", ".join(f"'{provider}'" for provider in _LEGACY_TOOL_CALLING_PROVIDERS)
    op.execute(
        sa.text(
            "UPDATE llm_models "
            "SET supports_tool_calling = true, "
            "tool_calling_capability_source = 'builtin_registry', "
            "tool_calling_checked_at = now(), "
            "tool_calling_error = NULL "
            "WHERE supports_tool_calling IS NULL "
            f"AND lower(provider) IN ({providers})"
        )
    )


def _downgrade_llm_capabilities() -> None:
    bind = op.get_bind()
    bind.execute(sa.text("LOCK TABLE llm_models IN ACCESS EXCLUSIVE MODE"))
    bind.execute(
        sa.text(
            "UPDATE llm_models "
            "SET supports_tool_calling = NULL, "
            "tool_calling_capability_source = NULL, "
            "tool_calling_checked_at = NULL, "
            "tool_calling_error = NULL "
            "WHERE tool_calling_capability_source = 'builtin_registry'"
        )
    )
    predicate = " OR ".join(
        f"{name} IS NOT NULL" for name in _LLM_CAPABILITY_COLUMNS
    )
    _require_zero(
        bind,
        f"SELECT count(*) FROM llm_models WHERE {predicate}",
        "Refusing to downgrade LLM Runtime capabilities because metadata is populated",
    )
    for name in reversed(_LLM_CAPABILITY_CHECKS):
        op.drop_constraint(name, "llm_models", type_="check")
    for name in reversed(_LLM_CAPABILITY_COLUMNS):
        op.drop_column("llm_models", name)


# ---------------------------------------------------------------------------
# Durable Agent Runtime (final schema, not intermediate projection schema)


_CHECKPOINT_SCHEMA = "langgraph_checkpoint"


def _checks(table_name: str) -> list[sa.CheckConstraint]:
    return [
        sa.CheckConstraint(expression, name=name)
        for name, expression in RUNTIME_CHECKS[table_name].items()
    ]


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
        sa.Column("model_turn_limit", sa.Integer(), nullable=True),
        sa.Column("runtime_type", sa.String(length=24), nullable=False),
        sa.Column("runtime_thread_id", sa.String(length=255), nullable=False),
        sa.Column("graph_name", sa.String(length=100), nullable=False),
        sa.Column("graph_version", sa.String(length=64), nullable=False),
        sa.Column("scheduling_lane_key", sa.String(length=255), nullable=True),
        sa.Column(
            "scheduling_position_created_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "scheduling_position_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
        sa.Column(
            "lane_held",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column("lane_claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "session_context_applied_checkpoint_id",
            sa.String(length=255),
            nullable=True,
        ),
        sa.Column("delivery_status", sa.String(length=24), nullable=False),
        sa.Column(
            "delivery_target",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        *_checks("agent_runs"),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name="fk_agent_runs_tenant_id_tenants",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["agent_id"],
            ["agents.id"],
            name="fk_agent_runs_agent_id_agents",
            ondelete="CASCADE",
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
        sa.UniqueConstraint(
            "tenant_id",
            "id",
            name="uq_agent_runs_tenant_id_id",
        ),
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
        sa.Column(
            "status",
            sa.String(length=24),
            server_default=sa.text("'pending'"),
            nullable=False,
        ),
        sa.Column("claimed_by", sa.String(length=128), nullable=True),
        sa.Column("claim_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "attempt_count",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column("applied_checkpoint_id", sa.String(length=255), nullable=True),
        sa.Column("error_code", sa.String(length=100), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("applied_at", sa.DateTime(timezone=True), nullable=True),
        *_checks("agent_run_commands"),
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
        sa.UniqueConstraint(
            "run_id",
            "idempotency_key",
            name="uq_agent_run_commands_run_idempotency",
        ),
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
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        *_checks("agent_run_events"),
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
        sa.UniqueConstraint(
            "run_id",
            "idempotency_key",
            name="uq_agent_run_events_run_idempotency",
        ),
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
        sa.Column(
            "sanitized_arguments",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column("request_ref", sa.String(length=500), nullable=True),
        sa.Column(
            "effect",
            sa.String(length=24),
            server_default=sa.text("'external_write'"),
            nullable=False,
        ),
        sa.Column(
            "retry_policy",
            sa.String(length=24),
            server_default=sa.text("'never'"),
            nullable=False,
        ),
        sa.Column(
            "attempt_count",
            sa.Integer(),
            server_default=sa.text("1"),
            nullable=False,
        ),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("result_summary", sa.Text(), nullable=True),
        sa.Column("result_ref", sa.String(length=500), nullable=True),
        sa.Column(
            "result_metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("lease_owner", sa.String(length=128), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        *_checks("agent_tool_executions"),
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
        sa.UniqueConstraint(
            "run_id",
            "tool_call_id",
            name="uq_agent_tool_executions_run_tool_call",
        ),
    )


def _create_session_context_states() -> None:
    op.create_table(
        "session_context_states",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("agent_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("session_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "summary",
            sa.Text(),
            server_default=sa.text("''"),
            nullable=False,
        ),
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
        sa.Column(
            "covered_through_message_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
        sa.Column(
            "version",
            sa.Integer(),
            server_default=sa.text("1"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        *_checks("session_context_states"),
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
        sa.UniqueConstraint(
            "session_id",
            name="uq_session_context_states_session_id",
        ),
    )


_RUNTIME_CREATE = {
    "agent_runs": _create_agent_runs,
    "agent_run_commands": _create_agent_run_commands,
    "agent_run_events": _create_agent_run_events,
    "agent_tool_executions": _create_agent_tool_executions,
    "session_context_states": _create_session_context_states,
}


def _create_runtime_indexes() -> None:
    op.create_index(
        "ix_agent_runs_tenant_thread_created_at",
        "agent_runs",
        ["tenant_id", "runtime_thread_id", "created_at", "id"],
        unique=False,
    )
    op.create_index(
        "ix_agent_runs_session_created_at",
        "agent_runs",
        ["session_id", sa.text("created_at DESC")],
        unique=False,
    )
    op.create_index(
        "ix_agent_runs_parent_run_id",
        "agent_runs",
        ["parent_run_id"],
        unique=False,
    )
    op.create_index(
        "ix_agent_runs_root_run_id",
        "agent_runs",
        ["root_run_id"],
        unique=False,
    )
    op.create_index(
        "ix_agent_runs_source",
        "agent_runs",
        ["source_type", "source_id"],
        unique=False,
    )
    op.create_index(
        "uq_agent_runs_source_execution",
        "agent_runs",
        ["source_type", "source_execution_id"],
        unique=True,
        postgresql_where=sa.text("source_execution_id IS NOT NULL"),
    )
    op.create_index(
        "uq_agent_runs_active_lane",
        "agent_runs",
        ["scheduling_lane_key"],
        unique=True,
        postgresql_where=sa.text(
            "scheduling_lane_key IS NOT NULL AND lane_held IS true"
        ),
    )
    op.create_index(
        "ix_agent_runs_lane_candidate_order",
        "agent_runs",
        [
            "scheduling_lane_key",
            "scheduling_position_created_at",
            "scheduling_position_id",
            "created_at",
            "id",
        ],
        unique=False,
        postgresql_where=sa.text("scheduling_lane_key IS NOT NULL"),
    )
    for name, table_name, columns in (
        (
            "ix_agent_run_commands_status_claim_created",
            "agent_run_commands",
            ["status", "claim_expires_at", "created_at"],
        ),
        (
            "ix_agent_run_commands_run_created",
            "agent_run_commands",
            ["run_id", "created_at", "id"],
        ),
        (
            "ix_agent_run_events_run_created",
            "agent_run_events",
            ["run_id", "created_at"],
        ),
        (
            "ix_agent_run_events_tenant_type_created",
            "agent_run_events",
            ["tenant_id", "event_type", "created_at"],
        ),
        (
            "ix_agent_tool_executions_tenant_status_started",
            "agent_tool_executions",
            ["tenant_id", "status", "started_at"],
        ),
        (
            "ix_agent_tool_executions_status_lease",
            "agent_tool_executions",
            ["status", "lease_expires_at"],
        ),
    ):
        op.create_index(name, table_name, columns, unique=False)
    op.create_index(
        "ix_session_context_states_tenant_agent_updated",
        "session_context_states",
        ["tenant_id", "agent_id", sa.text("updated_at DESC")],
        unique=False,
    )


def _upgrade_runtime_schema() -> None:
    op.execute(sa.text(f'CREATE SCHEMA IF NOT EXISTS "{_CHECKPOINT_SCHEMA}"'))
    for table_name in RUNTIME_TABLES:
        _RUNTIME_CREATE[table_name]()
    _create_runtime_indexes()


def _downgrade_runtime_schema() -> None:
    bind = op.get_bind()
    drop_order = (
        "agent_run_commands",
        "agent_run_events",
        "agent_tool_executions",
        "session_context_states",
        "agent_runs",
    )
    _require_empty_tables(bind, drop_order, domain="Agent Runtime schema")
    for table_name in drop_order:
        op.drop_table(table_name)
    # Deliberately omit CASCADE: saver-owned checkpoints must block downgrade.
    op.execute(sa.text(f'DROP SCHEMA IF EXISTS "{_CHECKPOINT_SCHEMA}"'))


# ---------------------------------------------------------------------------
# Group-scoped Workspace history and locks


def _add_workspace_scope(table_name: str) -> None:
    op.add_column(
        table_name,
        sa.Column("scope_type", sa.String(length=20), nullable=True),
    )
    op.add_column(
        table_name,
        sa.Column("scope_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.execute(
        sa.text(
            f"UPDATE {table_name} "
            "SET scope_type = 'agent', scope_id = agent_id "
            "WHERE scope_type IS NULL OR scope_id IS NULL"
        )
    )
    op.alter_column(
        table_name,
        "scope_type",
        existing_type=sa.String(length=20),
        nullable=False,
    )
    op.alter_column(
        table_name,
        "scope_id",
        existing_type=postgresql.UUID(as_uuid=True),
        nullable=False,
    )
    op.alter_column(
        table_name,
        "agent_id",
        existing_type=postgresql.UUID(as_uuid=True),
        nullable=True,
    )


def _add_workspace_scope_checks(table_name: str) -> None:
    op.create_check_constraint(
        f"ck_{table_name}_scope_type",
        table_name,
        "scope_type IN ('agent', 'group')",
    )
    op.create_check_constraint(
        f"ck_{table_name}_scope_identity",
        table_name,
        "(scope_type = 'agent' AND agent_id IS NOT NULL AND scope_id = agent_id) "
        "OR (scope_type = 'group' AND agent_id IS NULL)",
    )


def _upgrade_group_workspace_scope() -> None:
    _add_workspace_scope("workspace_file_revisions")
    _add_workspace_scope("workspace_edit_locks")
    op.drop_constraint(
        "uq_workspace_edit_locks_agent_path",
        "workspace_edit_locks",
        type_="unique",
    )
    op.create_unique_constraint(
        "uq_workspace_edit_locks_scope_path",
        "workspace_edit_locks",
        ["scope_type", "scope_id", "path"],
    )
    _add_workspace_scope_checks("workspace_file_revisions")
    _add_workspace_scope_checks("workspace_edit_locks")
    op.create_index(
        "ix_workspace_file_revisions_scope_path",
        "workspace_file_revisions",
        ["scope_type", "scope_id", "path"],
        unique=False,
    )


def _downgrade_group_workspace_scope() -> None:
    # Main cannot represent group-scoped rows. Remove only those rows before
    # restoring the legacy NOT NULL Agent identity.
    op.execute(sa.text("DELETE FROM workspace_edit_locks WHERE scope_type = 'group'"))
    op.execute(
        sa.text("DELETE FROM workspace_file_revisions WHERE scope_type = 'group'")
    )
    op.drop_index(
        "ix_workspace_file_revisions_scope_path",
        table_name="workspace_file_revisions",
    )
    for table_name in ("workspace_edit_locks", "workspace_file_revisions"):
        op.drop_constraint(
            f"ck_{table_name}_scope_identity",
            table_name,
            type_="check",
        )
        op.drop_constraint(
            f"ck_{table_name}_scope_type",
            table_name,
            type_="check",
        )
    op.drop_constraint(
        "uq_workspace_edit_locks_scope_path",
        "workspace_edit_locks",
        type_="unique",
    )
    op.create_unique_constraint(
        "uq_workspace_edit_locks_agent_path",
        "workspace_edit_locks",
        ["agent_id", "path"],
    )
    for table_name in ("workspace_edit_locks", "workspace_file_revisions"):
        op.alter_column(
            table_name,
            "agent_id",
            existing_type=postgresql.UUID(as_uuid=True),
            nullable=False,
        )
        op.drop_column(table_name, "scope_id")
        op.drop_column(table_name, "scope_type")


# ---------------------------------------------------------------------------
# External channel delivery outbox


def _upgrade_channel_delivery_outbox() -> None:
    op.create_table(
        "channel_deliveries",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("agent_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("session_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("message_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("channel", sa.String(length=32), nullable=False),
        sa.Column(
            "target",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("idempotency_key", sa.String(length=255), nullable=False),
        sa.Column(
            "status",
            sa.String(length=24),
            server_default=sa.text("'pending'"),
            nullable=False,
        ),
        sa.Column(
            "attempt_count",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column(
            "next_attempt_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("claimed_by", sa.String(length=128), nullable=True),
        sa.Column("claim_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("provider_message_id", sa.String(length=500), nullable=True),
        sa.Column("last_error_code", sa.String(length=100), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "channel IN ('feishu', 'dingtalk', 'wecom', 'wechat', 'whatsapp', "
            "'slack', 'discord', 'microsoft_teams')",
            name="ck_channel_deliveries_channel",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'claimed', 'delivered', 'failed')",
            name="ck_channel_deliveries_status",
        ),
        sa.CheckConstraint(
            "attempt_count >= 0",
            name="ck_channel_deliveries_attempt_count",
        ),
        sa.ForeignKeyConstraint(
            ["agent_id"],
            ["agents.id"],
            name="fk_channel_deliveries_agent_id_agents",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["message_id"],
            ["chat_messages.id"],
            name="fk_channel_deliveries_message_id_chat_messages",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["chat_sessions.id"],
            name="fk_channel_deliveries_session_id_chat_sessions",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name="fk_channel_deliveries_tenant_id_tenants",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "run_id"],
            ["agent_runs.tenant_id", "agent_runs.id"],
            name="fk_channel_deliveries_tenant_run_agent_runs",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_channel_deliveries"),
        sa.UniqueConstraint(
            "message_id",
            name="uq_channel_deliveries_message_id",
        ),
        sa.UniqueConstraint(
            "run_id",
            "idempotency_key",
            name="uq_channel_deliveries_run_idempotency",
        ),
    )
    op.create_index(
        "ix_channel_deliveries_pending_due",
        "channel_deliveries",
        ["status", "next_attempt_at", "claim_expires_at", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_channel_deliveries_run_created",
        "channel_deliveries",
        ["run_id", "created_at", "id"],
        unique=False,
    )


def _downgrade_channel_delivery_outbox() -> None:
    op.drop_table("channel_deliveries")


# ---------------------------------------------------------------------------
# Chat cursor and removal of obsolete template onboarding prompt


def _upgrade_chat_message_cursor() -> None:
    op.create_index(
        "ix_chat_messages_conversation_created_id",
        "chat_messages",
        ["conversation_id", "created_at", "id"],
        unique=False,
    )


def _downgrade_chat_message_cursor() -> None:
    op.drop_index(
        "ix_chat_messages_conversation_created_id",
        table_name="chat_messages",
    )


def _upgrade_remove_template_bootstrap() -> None:
    op.drop_column("agent_templates", "bootstrap_content")


def _downgrade_remove_template_bootstrap() -> None:
    op.add_column(
        "agent_templates",
        sa.Column("bootstrap_content", sa.Text(), nullable=True),
    )
