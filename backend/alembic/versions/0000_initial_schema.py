"""Initial schema — create all tables for fresh deployments.

Uses SQLAlchemy create_all(checkfirst=True) so existing databases
(upgraded from the bootstrap_db.py era) are completely unaffected.

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
    import app.models.activity_log     # noqa: F401
    import app.models.agent            # noqa: F401
    import app.models.agent_credential # noqa: F401
    import app.models.audit            # noqa: F401
    import app.models.channel_config   # noqa: F401
    import app.models.chat_session     # noqa: F401
    import app.models.gateway_message  # noqa: F401
    import app.models.identity         # noqa: F401
    import app.models.invitation_code  # noqa: F401
    import app.models.llm              # noqa: F401
    import app.models.notification     # noqa: F401
    import app.models.okr              # noqa: F401
    import app.models.org              # noqa: F401
    import app.models.participant      # noqa: F401
    import app.models.plaza            # noqa: F401
    import app.models.published_page   # noqa: F401
    import app.models.schedule         # noqa: F401
    import app.models.skill            # noqa: F401
    import app.models.system_settings  # noqa: F401
    import app.models.task             # noqa: F401
    import app.models.tenant           # noqa: F401
    import app.models.tenant_setting   # noqa: F401
    import app.models.tool             # noqa: F401
    import app.models.trigger          # noqa: F401
    import app.models.user             # noqa: F401
    import app.models.workspace        # noqa: F401

    bind = op.get_bind()
    Base.metadata.create_all(bind=bind, checkfirst=True)


def downgrade() -> None:
    pass  # Initial schema — not reversible
