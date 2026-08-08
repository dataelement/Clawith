"""Static contract tests for the channel delivery outbox migration."""

from importlib import util
from pathlib import Path

from sqlalchemy import CheckConstraint

from app.models.agent_run_event import AgentRunEvent
from app.models.channel_delivery import ChannelDelivery


MIGRATION_PATH = (
    Path(__file__).resolve().parents[1]
    / "alembic"
    / "versions"
    / "202607161200_unify_runtime_group_schema.py"
)


def _load_migration():
    spec = util.spec_from_file_location("unify_runtime_group_schema", MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    module = util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_channel_delivery_migration_follows_the_runtime_schema_head() -> None:
    migration = _load_migration()

    assert migration.revision == "unify_runtime_group_schema"
    assert migration.down_revision == "add_title_to_agent_focus_items"


def test_channel_delivery_model_is_an_outbox_not_runtime_state() -> None:
    columns = set(ChannelDelivery.__table__.columns.keys())

    assert {
        "run_id",
        "message_id",
        "channel",
        "target",
        "status",
        "attempt_count",
        "next_attempt_at",
        "claim_expires_at",
    } <= columns
    assert not {
        "runtime_thread_id",
        "checkpoint_id",
        "graph_name",
        "graph_state",
        "next_node",
    } & columns


def test_channel_delivery_model_has_retry_and_idempotency_constraints() -> None:
    table = ChannelDelivery.__table__
    names = {constraint.name for constraint in table.constraints}
    indexes = {index.name for index in table.indexes}

    assert "uq_channel_deliveries_run_idempotency" in names
    assert "uq_channel_deliveries_message_id" in names
    assert "ck_channel_deliveries_attempt_count" in names
    assert "ix_channel_deliveries_pending_due" in indexes


def test_event_model_allows_channel_delivery_outcomes() -> None:
    constraint = next(
        item
        for item in AgentRunEvent.__table__.constraints
        if isinstance(item, CheckConstraint)
        and item.name == "ck_agent_run_events_event_type"
    )
    model_sql = str(constraint.sqltext)

    assert "channel_delivery_delivered" in model_sql
    assert "channel_delivery_failed" in model_sql
