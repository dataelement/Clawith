"""Add OKR system tables.

Creates six tables for the OKR feature:
  okr_objectives, okr_key_results, okr_alignments,
  okr_progress_logs, work_reports, okr_settings

Revision ID: add_okr_tables
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "add_okr_tables"
down_revision = "add_agentbay_enum_value"  # latest migration in the chain
branch_labels = None
depends_on = None


def upgrade():
    conn = op.get_bind()

    # ── okr_objectives ──────────────────────────────────────────────────────
    if not conn.dialect.has_table(conn, "okr_objectives"):
        op.create_table(
            "okr_objectives",
            sa.Column(
                "id",
                UUID(as_uuid=True),
                primary_key=True,
                server_default=sa.text("gen_random_uuid()"),
            ),
            sa.Column(
                "tenant_id",
                UUID(as_uuid=True),
                sa.ForeignKey("tenants.id", ondelete="CASCADE"),
                nullable=False,
                index=True,
            ),
            sa.Column("title", sa.String(500), nullable=False),
            sa.Column("description", sa.Text),
            # owner_type: "company" | "user" | "agent"
            sa.Column("owner_type", sa.String(20), nullable=False),
            # NULL for company-level objectives
            sa.Column("owner_id", UUID(as_uuid=True)),
            sa.Column("period_start", sa.Date, nullable=False),
            sa.Column("period_end", sa.Date, nullable=False),
            # status: draft | active | completed | archived
            sa.Column(
                "status",
                sa.String(20),
                nullable=False,
                server_default="active",
            ),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
            ),
        )

    # ── okr_key_results ─────────────────────────────────────────────────────
    if not conn.dialect.has_table(conn, "okr_key_results"):
        op.create_table(
            "okr_key_results",
            sa.Column(
                "id",
                UUID(as_uuid=True),
                primary_key=True,
                server_default=sa.text("gen_random_uuid()"),
            ),
            sa.Column(
                "objective_id",
                UUID(as_uuid=True),
                sa.ForeignKey("okr_objectives.id", ondelete="CASCADE"),
                nullable=False,
                index=True,
            ),
            sa.Column("title", sa.String(500), nullable=False),
            sa.Column(
                "target_value",
                sa.Float,
                nullable=False,
                server_default=sa.text("100"),
            ),
            sa.Column(
                "current_value",
                sa.Float,
                nullable=False,
                server_default=sa.text("0"),
            ),
            # Display unit, e.g. "%", "followers", "万元"
            sa.Column("unit", sa.String(50)),
            # Links to an Agent's focus/<focus_ref>.md file
            sa.Column("focus_ref", sa.String(200)),
            # status: on_track | at_risk | behind | completed
            sa.Column(
                "status",
                sa.String(20),
                nullable=False,
                server_default="on_track",
            ),
            sa.Column(
                "last_updated_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
            ),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
            ),
        )

    # ── okr_alignments ──────────────────────────────────────────────────────
    if not conn.dialect.has_table(conn, "okr_alignments"):
        op.create_table(
            "okr_alignments",
            sa.Column(
                "id",
                UUID(as_uuid=True),
                primary_key=True,
                server_default=sa.text("gen_random_uuid()"),
            ),
            # source: the lower/peer entity that aligns toward target
            sa.Column(
                "source_type", sa.String(20), nullable=False
            ),  # "objective" | "key_result"
            sa.Column("source_id", UUID(as_uuid=True), nullable=False),
            # target: the higher/peer entity being aligned to
            sa.Column(
                "target_type", sa.String(20), nullable=False
            ),  # "objective" | "key_result"
            sa.Column("target_id", UUID(as_uuid=True), nullable=False),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
            ),
            sa.UniqueConstraint(
                "source_type",
                "source_id",
                "target_type",
                "target_id",
                name="uq_okr_alignment",
            ),
        )

    # ── okr_progress_logs ───────────────────────────────────────────────────
    if not conn.dialect.has_table(conn, "okr_progress_logs"):
        op.create_table(
            "okr_progress_logs",
            sa.Column(
                "id",
                UUID(as_uuid=True),
                primary_key=True,
                server_default=sa.text("gen_random_uuid()"),
            ),
            sa.Column(
                "kr_id",
                UUID(as_uuid=True),
                sa.ForeignKey("okr_key_results.id", ondelete="CASCADE"),
                nullable=False,
                index=True,
            ),
            sa.Column("previous_value", sa.Float, nullable=False),
            sa.Column("new_value", sa.Float, nullable=False),
            # source: "okr_agent" | "manual" | "self_report"
            sa.Column("source", sa.String(30), nullable=False),
            # Optional note extracted by OKR Agent from conversation
            sa.Column("note", sa.Text),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
            ),
        )

    # ── work_reports ────────────────────────────────────────────────────────
    if not conn.dialect.has_table(conn, "work_reports"):
        op.create_table(
            "work_reports",
            sa.Column(
                "id",
                UUID(as_uuid=True),
                primary_key=True,
                server_default=sa.text("gen_random_uuid()"),
            ),
            sa.Column(
                "tenant_id",
                UUID(as_uuid=True),
                sa.ForeignKey("tenants.id", ondelete="CASCADE"),
                nullable=False,
                index=True,
            ),
            # author_type: "user" | "agent"
            sa.Column("author_type", sa.String(20), nullable=False),
            sa.Column("author_id", UUID(as_uuid=True), nullable=False),
            # report_type: "daily" | "weekly"
            sa.Column("report_type", sa.String(10), nullable=False),
            # For daily: the day; for weekly: the Monday of that week
            sa.Column("period_date", sa.Date, nullable=False),
            sa.Column(
                "content",
                sa.Text,
                nullable=False,
                server_default="",
            ),
            # source: "okr_agent_collected" | "manual"
            sa.Column(
                "source",
                sa.String(30),
                nullable=False,
                server_default="okr_agent_collected",
            ),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
            ),
        )

    # ── okr_settings ────────────────────────────────────────────────────────
    if not conn.dialect.has_table(conn, "okr_settings"):
        op.create_table(
            "okr_settings",
            # Primary key is tenant_id — one row per tenant
            sa.Column(
                "tenant_id",
                UUID(as_uuid=True),
                sa.ForeignKey("tenants.id", ondelete="CASCADE"),
                primary_key=True,
            ),
            sa.Column(
                "enabled",
                sa.Boolean,
                nullable=False,
                server_default=sa.text("false"),
            ),
            sa.Column(
                "daily_report_enabled",
                sa.Boolean,
                nullable=False,
                server_default=sa.text("false"),
            ),
            # HH:MM 24-hour time, interpreted in OKR Agent's configured timezone
            sa.Column(
                "daily_report_time",
                sa.String(5),
                nullable=False,
                server_default="18:00",
            ),
            sa.Column(
                "weekly_report_enabled",
                sa.Boolean,
                nullable=False,
                server_default=sa.text("false"),
            ),
            # 0=Monday ... 6=Sunday
            sa.Column(
                "weekly_report_day",
                sa.Integer,
                nullable=False,
                server_default=sa.text("4"),
            ),
            # period_frequency: "quarterly" | "monthly" | "custom"
            sa.Column(
                "period_frequency",
                sa.String(20),
                nullable=False,
                server_default="quarterly",
            ),
            # Only used when period_frequency == "custom"
            sa.Column("period_length_days", sa.Integer),
        )


def downgrade():
    conn = op.get_bind()
    # Drop in reverse dependency order
    for table in [
        "okr_settings",
        "work_reports",
        "okr_progress_logs",
        "okr_alignments",
        "okr_key_results",
        "okr_objectives",
    ]:
        if conn.dialect.has_table(conn, table):
            op.drop_table(table)
