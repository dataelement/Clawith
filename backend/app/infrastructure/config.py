"""Target application configuration."""

from functools import lru_cache
from pathlib import Path
from secrets import token_hex

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import ArgumentError

BACKEND_ROOT = Path(__file__).resolve().parents[2]
VERSION_PATH = BACKEND_ROOT / "VERSION"
ENV_FILE_PATH = BACKEND_ROOT / ".env"
TARGET_DATABASE_NAME = "clawith_target"
DATABASE_IDENTITY_QUERY_KEYS = frozenset(
    {"database", "dbname", "dsn", "host", "password", "port", "user", "username"}
)


def reveal_database_url(value: SecretStr) -> URL:
    """Reveal a database secret only into SQLAlchemy's password-masking URL type."""
    return make_url(value.get_secret_value())


def _read_version() -> str:
    version = VERSION_PATH.read_text(encoding="utf-8").strip()
    if not version:
        raise ValueError(f"version file is empty: {VERSION_PATH}")
    return version


class Settings(BaseSettings):
    """Configuration owned by the target composition and database infrastructure."""

    APP_NAME: str = Field(default="Clawith", min_length=1)
    APP_VERSION: str = Field(default_factory=_read_version, min_length=1)
    DEBUG: bool = False
    STARTUP_INSTANCE_ID: str = Field(
        default_factory=lambda: token_hex(16),
        pattern=r"^[0-9a-f]{32}$",
    )
    DATABASE_URL: SecretStr = Field(
        default=SecretStr(
            "postgresql+asyncpg://clawith_target:clawith_target@localhost:5432/clawith_target"
        ),
    )
    CONTROL_DATABASE_POOL_SIZE: int = Field(default=20, gt=0)
    EXECUTION_DATABASE_POOL_SIZE: int = Field(default=20, gt=0)
    DATABASE_POOL_MAX_OVERFLOW: int = Field(default=0, ge=0)

    @field_validator("DATABASE_URL")
    @classmethod
    def _complete_async_postgres_url(cls, value: SecretStr) -> SecretStr:
        try:
            url = reveal_database_url(value)
        except ArgumentError:
            raise ValueError("DATABASE_URL must be a complete SQLAlchemy URL") from None

        try:
            required_parts = {
                "username": url.username,
                "password": url.password,
                "host": url.host,
                "port": url.port,
                "database": url.database,
            }
        except ValueError:
            raise ValueError("DATABASE_URL contains an invalid port") from None
        missing = [name for name, part in required_parts.items() if part in (None, "")]
        invalid_port = url.port is not None and not 1 <= url.port <= 65535
        if url.drivername != "postgresql+asyncpg" or missing or invalid_port:
            detail = f"; missing {', '.join(missing)}" if missing else ""
            if invalid_port:
                detail = "; port must be between 1 and 65535"
            raise ValueError(
                "DATABASE_URL must use postgresql+asyncpg and include username, password, "
                f"host, port, and database{detail}"
            )
        if url.database != TARGET_DATABASE_NAME:
            raise ValueError(
                f"DATABASE_URL database must be exactly {TARGET_DATABASE_NAME}"
            )
        identity_overrides = sorted(
            key
            for key in url.query
            if key.casefold() in DATABASE_IDENTITY_QUERY_KEYS
        )
        if identity_overrides:
            raise ValueError(
                "DATABASE_URL query may not override connection identity fields: "
                + ", ".join(identity_overrides)
            )
        return value

    model_config = SettingsConfigDict(
        env_file=ENV_FILE_PATH,
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="forbid",
        hide_input_in_errors=True,
        validate_default=True,
    )


@lru_cache
def get_settings() -> Settings:
    """Return the process configuration snapshot."""
    return Settings()
