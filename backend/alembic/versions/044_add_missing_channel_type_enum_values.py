"""Ensure channel_type_enum contains all channel values used by the app.

Revision ID: add_channel_enum_values
Revises: merge_workspace_and_okr
Create Date: 2026-04-18
"""
from typing import Sequence, Union

from alembic import op


revision: str = "add_channel_enum_values"
down_revision: Union[str, Sequence[str], None] = "merge_workspace_and_okr"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Keep the PostgreSQL enum in sync with ChannelConfig.channel_type.
    for value in (
        "wecom",
        "dingtalk",
        "slack",
        "discord",
        "atlassian",
        "microsoft_teams",
        "agentbay",
    ):
        op.execute(f"ALTER TYPE channel_type_enum ADD VALUE IF NOT EXISTS '{value}'")


def downgrade() -> None:
    # PostgreSQL cannot safely remove enum values in-place.
    pass
