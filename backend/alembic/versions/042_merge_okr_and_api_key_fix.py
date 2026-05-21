"""Merge okr_agent_id migration and increase_api_key_length migration heads.

Revision ID: merge_okr_api_key
Revises: okr_agent_id_sys_uq, increase_api_key_length
Create Date: 2026-04-15
"""
from typing import Sequence, Union

revision: str = "merge_okr_api_key"
down_revision: Union[str, Sequence[str], None] = (
    "okr_agent_id_sys_uq",
    "increase_api_key_length",
)
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
