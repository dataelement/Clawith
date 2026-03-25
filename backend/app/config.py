"""Application configuration."""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings


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

    # Redis
    REDIS_URL: str = "redis://localhost:6379/0"

    # JWT
    JWT_SECRET_KEY: str = "change-me-jwt-secret"
    JWT_ALGORITHM: str = "HS256"
    JWT_ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24  # 24 hours

    # File Storage
    AGENT_DATA_DIR: str = _default_agent_data_dir()
    WORKSPACE_STATIC_DIR: str = "/srv/workspace"
    WORKSPACE_CONF_DIR: str = "/etc/nginx/workspace.d"
    WORKSPACE_GATEWAY_CONTAINER: str = "workspace_gateway"
    AGENT_TEMPLATE_DIR: str = "/app/agent_template"
    AGENCY_AGENTS_DIR: str = "/data/shared/agency-agents"

    # Docker (for Agent containers)
    DOCKER_NETWORK: str = "clawith_network"
    OPENCLAW_IMAGE: str = "openclaw:local"
    OPENCLAW_GATEWAY_PORT: int = 18789

    # Feishu OAuth
    FEISHU_APP_ID: str = ""
    FEISHU_APP_SECRET: str = ""
    FEISHU_REDIRECT_URI: str = ""

    # CORS
    CORS_ORIGINS: list[str] = ["http://localhost:3000", "http://localhost:5173"]

    # Jina AI (Reader + Search APIs)
    JINA_API_KEY: str = ""

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
