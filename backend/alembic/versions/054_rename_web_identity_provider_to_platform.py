"""Rename web identity provider display name to Platform.

Revision ID: web_provider_platform
Revises: add_relationship_access_metadata
Create Date: 2026-05-06
"""

from typing import Sequence, Union

from alembic import op


revision: str = "web_provider_platform"
down_revision: Union[str, Sequence[str], None] = "add_relationship_access_metadata"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("UPDATE identity_providers SET name = 'Platform' WHERE provider_type = 'web' AND name = 'Web'")
    op.execute("UPDATE org_members SET title = 'Platform User' WHERE title = 'Web User'")


def downgrade() -> None:
    op.execute("UPDATE identity_providers SET name = 'Web' WHERE provider_type = 'web' AND name = 'Platform'")
    op.execute("UPDATE org_members SET title = 'Web User' WHERE title = 'Platform User'")
