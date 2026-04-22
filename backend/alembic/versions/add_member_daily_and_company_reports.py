"""Add consolidated OKR reporting and scheduling schema updates.

Revision ID: add_member_daily_company_reports
Revises: add_channel_enum_values
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "add_member_daily_company_reports"
down_revision: Union[str, Sequence[str], None] = "add_channel_enum_values"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    table_names = set(inspector.get_table_names())

    if "tenants" in table_names:
        tenant_columns = {col["name"] for col in inspector.get_columns("tenants")}
        if "country_region" not in tenant_columns:
            op.add_column(
                "tenants",
                sa.Column("country_region", sa.String(length=10), nullable=False, server_default="001"),
            )

    if "okr_settings" in table_names:
        okr_settings_columns = {col["name"] for col in inspector.get_columns("okr_settings")}
        if "daily_report_skip_non_workdays" not in okr_settings_columns:
            op.add_column(
                "okr_settings",
                sa.Column("daily_report_skip_non_workdays", sa.Boolean(), nullable=False, server_default=sa.text("true")),
            )
        if "first_enabled_at" not in okr_settings_columns:
            op.add_column(
                "okr_settings",
                sa.Column("first_enabled_at", sa.DateTime(timezone=True), nullable=True),
            )
        op.execute(
            """
            UPDATE okr_settings s
            SET first_enabled_at = COALESCE(
                (
                    SELECT MIN(o.period_start)::timestamptz
                    FROM okr_objectives o
                    WHERE o.tenant_id = s.tenant_id
                ),
                NOW()
            )
            WHERE s.enabled = TRUE
              AND s.first_enabled_at IS NULL
            """
        )

    if "member_daily_reports" not in table_names:
        op.create_table(
            "member_daily_reports",
            sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("member_type", sa.String(length=20), nullable=False),
            sa.Column("member_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("report_date", sa.Date(), nullable=False),
            sa.Column("content", sa.Text(), nullable=False, server_default=""),
            sa.Column("status", sa.String(length=20), nullable=False, server_default="submitted"),
            sa.Column("source", sa.String(length=30), nullable=False, server_default="okr_agent_assisted"),
            sa.Column("submitted_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "tenant_id", "member_type", "member_id", "report_date",
                name="uq_member_daily_report",
            ),
        )
        table_names.add("member_daily_reports")
    if "member_daily_reports" in table_names:
        member_daily_indexes = {idx["name"] for idx in inspector.get_indexes("member_daily_reports")}
        if "ix_member_daily_reports_tenant_id" not in member_daily_indexes:
            op.create_index("ix_member_daily_reports_tenant_id", "member_daily_reports", ["tenant_id"])
        if "ix_member_daily_reports_member_id" not in member_daily_indexes:
            op.create_index("ix_member_daily_reports_member_id", "member_daily_reports", ["member_id"])
        if "ix_member_daily_reports_report_date" not in member_daily_indexes:
            op.create_index("ix_member_daily_reports_report_date", "member_daily_reports", ["report_date"])

    if "company_reports" not in table_names:
        op.create_table(
            "company_reports",
            sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("report_type", sa.String(length=10), nullable=False),
            sa.Column("period_start", sa.Date(), nullable=False),
            sa.Column("period_end", sa.Date(), nullable=False),
            sa.Column("period_label", sa.String(length=100), nullable=False, server_default=""),
            sa.Column("content", sa.Text(), nullable=False, server_default=""),
            sa.Column("submitted_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("missing_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("needs_refresh", sa.Boolean(), nullable=False, server_default=sa.text("false")),
            sa.Column("generated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "tenant_id", "report_type", "period_start", "period_end",
                name="uq_company_report_period",
            ),
        )
        table_names.add("company_reports")
    if "company_reports" in table_names:
        company_report_indexes = {idx["name"] for idx in inspector.get_indexes("company_reports")}
        if "ix_company_reports_tenant_id" not in company_report_indexes:
            op.create_index("ix_company_reports_tenant_id", "company_reports", ["tenant_id"])
        if "ix_company_reports_period_start" not in company_report_indexes:
            op.create_index("ix_company_reports_period_start", "company_reports", ["period_start"])


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    table_names = set(inspector.get_table_names())

    if "company_reports" in table_names:
        company_report_indexes = {idx["name"] for idx in inspector.get_indexes("company_reports")}
        if "ix_company_reports_period_start" in company_report_indexes:
            op.drop_index("ix_company_reports_period_start", table_name="company_reports")
        if "ix_company_reports_tenant_id" in company_report_indexes:
            op.drop_index("ix_company_reports_tenant_id", table_name="company_reports")
        op.drop_table("company_reports")

    if "member_daily_reports" in table_names:
        member_daily_indexes = {idx["name"] for idx in inspector.get_indexes("member_daily_reports")}
        if "ix_member_daily_reports_report_date" in member_daily_indexes:
            op.drop_index("ix_member_daily_reports_report_date", table_name="member_daily_reports")
        if "ix_member_daily_reports_member_id" in member_daily_indexes:
            op.drop_index("ix_member_daily_reports_member_id", table_name="member_daily_reports")
        if "ix_member_daily_reports_tenant_id" in member_daily_indexes:
            op.drop_index("ix_member_daily_reports_tenant_id", table_name="member_daily_reports")
        op.drop_table("member_daily_reports")

    if "okr_settings" in table_names:
        okr_settings_columns = {col["name"] for col in inspector.get_columns("okr_settings")}
        if "first_enabled_at" in okr_settings_columns:
            op.drop_column("okr_settings", "first_enabled_at")
        if "daily_report_skip_non_workdays" in okr_settings_columns:
            op.drop_column("okr_settings", "daily_report_skip_non_workdays")

    if "tenants" in table_names:
        tenant_columns = {col["name"] for col in inspector.get_columns("tenants")}
        if "country_region" in tenant_columns:
            op.drop_column("tenants", "country_region")
