"""Fail-closed Alembic environment while the G008 target baseline is absent."""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from app.infrastructure.config import get_settings, reveal_database_url
from app.infrastructure.database import Base

config = context.config
settings = get_settings()
database_url = reveal_database_url(settings.DATABASE_URL)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata
config.set_main_option("sqlalchemy.url", str(database_url).replace("%", "%%"))

G002_ALEMBIC_UNAVAILABLE = (
    "Alembic execution is unavailable until the reviewed G008 target baseline"
)


def _reject_g002_alembic_execution() -> None:
    raise SystemExit(G002_ALEMBIC_UNAVAILABLE)


def run_migrations_offline() -> None:
    """Reject offline execution until G008 supplies the target baseline."""
    _reject_g002_alembic_execution()


def run_migrations_online() -> None:
    """Reject online execution until G008 supplies the target baseline."""
    _reject_g002_alembic_execution()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
