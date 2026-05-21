"""Initial schema — create all tables for fresh deployments.

env.py already imports all models, so Base.metadata is fully populated
when this migration runs. checkfirst=True makes it a no-op on existing DBs.

Revision ID: initial_schema
Revises: (none)
Create Date: 2026-04-26
"""

from alembic import op

revision: str = "initial_schema"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    from app.database import Base
    bind = op.get_bind()
    Base.metadata.create_all(bind=bind, checkfirst=True)


def downgrade() -> None:
    pass
