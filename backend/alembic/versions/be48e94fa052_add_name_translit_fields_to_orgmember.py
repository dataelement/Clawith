"""Add name_translit fields to OrgMember

Revision ID: be48e94fa052
Revises: add_daily_token_usage
Create Date: 2026-03-27 15:13:16.315195
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'be48e94fa052'
down_revision: Union[str, None] = 'add_daily_token_usage'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Use raw SQL with IF NOT EXISTS for idempotency
    op.execute("ALTER TABLE org_members ADD COLUMN IF NOT EXISTS name_translit_full VARCHAR(255)")
    op.execute("ALTER TABLE org_members ADD COLUMN IF NOT EXISTS name_translit_initial VARCHAR(50)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_org_members_name_translit_full ON org_members(name_translit_full)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_org_members_name_translit_initial ON org_members(name_translit_initial)")


def downgrade() -> None:
    # Use raw SQL with IF EXISTS for idempotency
    op.execute("DROP INDEX IF EXISTS ix_org_members_name_translit_initial")
    op.execute("DROP INDEX IF EXISTS ix_org_members_name_translit_full")
    op.execute("ALTER TABLE org_members DROP COLUMN IF EXISTS name_translit_initial")
    op.execute("ALTER TABLE org_members DROP COLUMN IF EXISTS name_translit_full")
