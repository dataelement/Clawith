"""Clear invalid org member user links.

Revision ID: clear_invalid_org_member_user_links
Revises: add_user_tenant_onboarding
Create Date: 2026-05-15
"""

from typing import Sequence, Union

from alembic import op


revision: str = "clear_invalid_org_member_user_links"
down_revision: Union[str, Sequence[str], None] = "add_user_tenant_onboarding"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE org_members AS om
        SET user_id = NULL
        WHERE om.user_id IS NOT NULL
          AND NOT EXISTS (
              SELECT 1
              FROM users AS u
              WHERE u.id = om.user_id
                AND u.tenant_id IS NOT DISTINCT FROM om.tenant_id
                AND u.is_active = TRUE
          )
        """
    )


def downgrade() -> None:
    pass
