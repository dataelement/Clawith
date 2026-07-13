"""Application configuration."""

from functools import lru_cache
import os
from pathlib import Path
import socket
from typing import Self
import uuid

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings

from app.services.sandbox.config import SandboxConfig, SandboxType


def _running_in_container() -> bool:
    """Best-effort container runtime detection."""
    if Path("/.dockerenv").exists() or Path("/run/.containerenv").exists():
        return True

    cgroup = Path("/proc/1/cgroup")
    if not cgroup.exists():
        return False

    try:
        content = cgroup.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return False

    return any(token in content for token in ("docker", "containerd", "kubepods", "podman"))


def _default_agent_data_dir() -> str:
    """Use Docker path in containers, user-writable path on local hosts."""
    if _running_in_container():
        return "/data/agents"
    return str(Path.home() / ".clawith" / "data" / "agents")


def _default_instance_id() -> str:
    """Generate a stable-enough per-process instance identifier."""
    host = socket.gethostname() or "unknown"
    pid = os.getpid()
    suffix = uuid.uuid4().hex[:8]
    return f"{host}-{pid}-{suffix}"


def _default_agent_template_dir() -> str:
    """Locate the agent template directory for both Docker and source deployments.

    In a Docker container the backend source is copied to /app, so the template
    lives at /app/agent_template.  In a source deployment it sits next to the
    backend/ package root, i.e. <repo>/backend/agent_template.
    """
    if _running_in_container():
        return "/app/agent_template"
    # Source layout: backend/app/config.py -> ../.. = backend/ -> agent_template
    source_path = Path(__file__).resolve().parent.parent / "agent_template"
    return str(source_path)


def _default_allow_unsafe_bwrap_fallback() -> bool:
    """Allow local source runs to work without bubblewrap by default."""
    return not _running_in_container()


def _read_version() -> str:
    """Read version from local VERSION file, fallback to root."""
    for candidate in [Path(__file__).resolve().parent.parent / "VERSION",
                      Path(__file__).resolve().parent.parent.parent / "VERSION",
                      Path("/app/VERSION"), Path("/VERSION")]:
        try:
            return candidate.read_text(encoding="utf-8").strip()
        except OSError:
            continue
    return "0.0.0"


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    # App
    APP_NAME: str = "Clawith"
    APP_VERSION: str = _read_version()
    DEBUG: bool = False
    SECRET_KEY: str = "change-me-in-production"
    API_PREFIX: str = "/api"

    # Database
    DATABASE_URL: str = "postgresql+asyncpg://clawith:clawith@localhost:5432/clawith"
    DATABASE_AUTO_CREATE_TABLES: bool = False

    # Redis
    REDIS_URL: str = "redis://localhost:6379/0"
    INSTANCE_ID: str = _default_instance_id()

    # JWT
    JWT_SECRET_KEY: str = "change-me-jwt-secret"
    JWT_ALGORITHM: str = "HS256"
    JWT_ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24  # 24 hours
    PASSWORD_RESET_TOKEN_EXPIRE_MINUTES: int = 60
    EMAIL_VERIFICATION_TOKEN_EXPIRE_MINUTES: int = 60  # 1 hour
    EMAIL_VERIFICATION_REQUIRED: bool = False  # Require email verification for login

    # File Storage
    STORAGE_BACKEND: str = "local"
    AGENT_DATA_DIR: str = _default_agent_data_dir()
    AGENT_TEMPLATE_DIR: str = _default_agent_template_dir()
    STORAGE_LOCAL_ROOT: str = _default_agent_data_dir()
    STORAGE_LOCAL_FALLBACK_ENABLED: bool = True
    S3_BUCKET: str = ""
    S3_REGION: str = ""
    S3_ENDPOINT_URL: str = ""
    S3_ACCESS_KEY_ID: str = ""
    S3_SECRET_ACCESS_KEY: str = ""
    S3_PREFIX: str = "agents"
    S3_PRESIGN_TTL_SECONDS: int = 3600
    S3_MAX_POOL_CONNECTIONS: int = 50
    S3_WRITE_WORKERS: int = 32

    # Process role
    PROCESS_ROLE: str = "all"

    # Agent Runtime
    AGENT_RUNTIME_V2_ENABLED: bool = False
    AGENT_RUNTIME_V2_AGENT_IDS: str = ""
    AGENT_RUNTIME_V2_SOURCE_TYPES: str = "task"
    AGENT_RUNTIME_GRAPH_NAME: str = "clawith_agent_runtime"
    AGENT_RUNTIME_GRAPH_VERSION: str = "v1"
    LANGGRAPH_CHECKPOINT_DATABASE_URL: str | None = None
    LANGGRAPH_AES_KEY: str | None = None
    AGENT_RUNTIME_COMMAND_CLAIM_TTL_SECONDS: int = Field(default=60, gt=0)
    AGENT_RUNTIME_COMMAND_CLAIM_RENEW_SECONDS: int = Field(default=20, gt=0)
    AGENT_RUNTIME_COMMAND_MAX_ATTEMPTS: int = Field(default=5, gt=0)
    AGENT_RUNTIME_SUMMARY_THRESHOLD_RATIO: float = Field(default=0.85, gt=0, le=1)
    AGENT_RUNTIME_SESSION_RECENT_MESSAGES: int = Field(default=20, gt=0)
    AGENT_RUNTIME_SESSION_COMPACT_MESSAGE_THRESHOLD: int | None = Field(default=None, gt=0)
    AGENT_RUNTIME_SESSION_COMPACT_SCAN_SECONDS: float = Field(default=5.0, gt=0)
    AGENT_RUNTIME_SESSION_COMPACT_SCAN_BATCH_SIZE: int = Field(default=50, gt=0, le=500)
    AGENT_RUNTIME_RUN_COMPACT_MESSAGE_THRESHOLD: int | None = Field(default=None, gt=0)
    AGENT_RUNTIME_RUN_COMPACT_TOOL_RESULT_BYTES: int | None = Field(default=None, gt=0)
    AGENT_RUNTIME_VERIFY_REPAIR_COMPACT_ROUNDS: int | None = Field(default=None, gt=0)
    AGENT_RUNTIME_MODEL_CAPABILITY_REFRESH_SECONDS: int = Field(default=86400, gt=0)
    MULTI_AGENT_COMPACT_MODEL_ID: uuid.UUID | None = None
    MULTI_AGENT_PLANNING_MODEL_ID: uuid.UUID | None = None
    GROUP_CONTEXT_ANNOUNCEMENT_MAX_CHARS: int = Field(default=12000, gt=0)
    GROUP_CONTEXT_MEMORY_MAX_CHARS: int = Field(default=12000, gt=0)
    GROUP_CONTEXT_WORKSPACE_MAX_ENTRIES: int = Field(default=100, gt=0)
    AGENT_RUNTIME_CHECKPOINT_RETENTION_DAYS: int = Field(default=30, gt=0)
    AGENT_RUNTIME_EVENT_PAYLOAD_MAX_BYTES: int = Field(default=16384, gt=0)
    AGENT_RUNTIME_TOOL_RESULT_INLINE_MAX_BYTES: int = Field(default=8192, gt=0)
    MAX_AGENT_CYCLE_COUNT: int = Field(default=5, gt=0)

    # Docker (for Agent containers)
    DOCKER_NETWORK: str = "clawith_network"
    OPENCLAW_IMAGE: str = "openclaw:local"
    OPENCLAW_GATEWAY_PORT: int = 18789

    # Feishu OAuth
    FEISHU_APP_ID: str = ""
    FEISHU_APP_SECRET: str = ""
    FEISHU_REDIRECT_URI: str = ""
    PUBLIC_BASE_URL: str = ""
    HTTP_PROXY: str = ""

    # CORS
    CORS_ORIGINS: list[str] = ["http://localhost:3000", "http://localhost:5173"]

    # Jina AI (Reader + Search APIs)
    JINA_API_KEY: str = ""

    # Exa AI (Search API)
    EXA_API_KEY: str = ""


    # Sandbox configuration
    SANDBOX_TYPE: SandboxType = SandboxType.SUBPROCESS
    SANDBOX_API_KEY: str = ""
    SANDBOX_API_URL: str = ""
    SANDBOX_CPU_LIMIT: str = "0.5"
    SANDBOX_MEMORY_LIMIT: str = "256m"
    SANDBOX_ALLOW_NETWORK: bool = False
    SANDBOX_ALLOW_UNSAFE_FALLBACK_WHEN_BWRAP_MISSING: bool = _default_allow_unsafe_bwrap_fallback()
    SANDBOX_DEFAULT_TIMEOUT: int = 30
    SANDBOX_MAX_TIMEOUT: int = 60

    @field_validator(
        "LANGGRAPH_CHECKPOINT_DATABASE_URL",
        "LANGGRAPH_AES_KEY",
        "MULTI_AGENT_COMPACT_MODEL_ID",
        "MULTI_AGENT_PLANNING_MODEL_ID",
        "AGENT_RUNTIME_SESSION_COMPACT_MESSAGE_THRESHOLD",
        "AGENT_RUNTIME_RUN_COMPACT_MESSAGE_THRESHOLD",
        "AGENT_RUNTIME_RUN_COMPACT_TOOL_RESULT_BYTES",
        "AGENT_RUNTIME_VERIFY_REPAIR_COMPACT_ROUNDS",
        mode="before",
    )
    @classmethod
    def _blank_optional_runtime_values(cls, value: object) -> object | None:
        """Treat blank optional environment variables as unset."""
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("AGENT_RUNTIME_GRAPH_NAME", "AGENT_RUNTIME_GRAPH_VERSION")
    @classmethod
    def _nonempty_runtime_identifiers(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Runtime graph name and version must not be blank")
        return normalized

    @model_validator(mode="after")
    def _claim_renewal_precedes_expiry(self) -> Self:
        if self.AGENT_RUNTIME_COMMAND_CLAIM_RENEW_SECONDS >= self.AGENT_RUNTIME_COMMAND_CLAIM_TTL_SECONDS:
            raise ValueError(
                "AGENT_RUNTIME_COMMAND_CLAIM_RENEW_SECONDS must be less than "
                "AGENT_RUNTIME_COMMAND_CLAIM_TTL_SECONDS"
            )
        return self

    model_config = {
        "env_file": [".env", "../.env"],
        "env_file_encoding": "utf-8",
        "case_sensitive": True,
        "extra": "ignore",
    }


@lru_cache
def get_settings() -> Settings:
    """Get cached application settings."""
    return Settings()


def get_sandbox_config() -> SandboxConfig:
    """Create SandboxConfig from application settings."""
    settings = get_settings()
    return SandboxConfig(
        type=settings.SANDBOX_TYPE,
        enabled=True,
        api_key=settings.SANDBOX_API_KEY,
        api_url=settings.SANDBOX_API_URL,
        cpu_limit=settings.SANDBOX_CPU_LIMIT,
        memory_limit=settings.SANDBOX_MEMORY_LIMIT,
        allow_network=settings.SANDBOX_ALLOW_NETWORK,
        allow_unsafe_fallback_when_bwrap_missing=settings.SANDBOX_ALLOW_UNSAFE_FALLBACK_WHEN_BWRAP_MISSING,
        default_timeout=settings.SANDBOX_DEFAULT_TIMEOUT,
        max_timeout=settings.SANDBOX_MAX_TIMEOUT,
    )
