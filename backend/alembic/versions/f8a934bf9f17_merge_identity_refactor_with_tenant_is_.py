"""merge identity refactor with tenant_is_default

Revision ID: f8a934bf9f17
Revises: add_tenant_is_default, d9cbd43b62e5
Create Date: 2026-04-02
"""
from typing import Sequence, Union

# revision identifiers, used by Alembic.
revision: str = 'f8a934bf9f17'
down_revision: Union[str, Sequence[str]] = ('add_tenant_is_default', 'd9cbd43b62e5')
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
