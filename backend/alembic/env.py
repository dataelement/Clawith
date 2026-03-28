"""Alembic environment configuration for async SQLAlchemy."""

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.ext.asyncio import async_engine_from_config

from app.database import Base
from app.config import get_settings

# Import all models so they are registered with Base.metadata
from app.models.identity import IdentityProvider, SSOScanSession  # noqa: F401
from app.models.user import User  # noqa: F401
from app.models.agent import Agent, AgentPermission, AgentTemplate  # noqa: F401
from app.models.task import Task, TaskLog  # noqa: F401
from app.models.channel_config import ChannelConfig  # noqa: F401
from app.models.llm import LLMModel  # noqa: F401
from app.models.audit import AuditLog, ApprovalRequest, ChatMessage, EnterpriseInfo  # noqa: F401
from app.models.skill import Skill, SkillFile  # noqa: F401
from app.models.chat_session import ChatSession  # noqa: F401
from app.models.participant import Participant  # noqa: F401
from app.models.activity_log import AgentActivityLog  # noqa: F401
from app.models.invitation_code import InvitationCode  # noqa: F401
from app.models.org import OrgDepartment, OrgMember, AgentRelationship, AgentAgentRelationship  # noqa: F401
from app.models.plaza import PlazaPost, PlazaComment, PlazaLike  # noqa: F401
from app.models.schedule import AgentSchedule  # noqa: F401
from app.models.system_settings import SystemSetting  # noqa: F401
from app.models.tenant import Tenant  # noqa: F401
from app.models.tool import Tool  # noqa: F401
from app.models.trigger import AgentTrigger  # noqa: F401

config = context.config
settings = get_settings()

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

config.set_main_option("sqlalchemy.url", settings.DATABASE_URL)


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection):
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """Run migrations in 'online' mode with async engine."""
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode."""
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
