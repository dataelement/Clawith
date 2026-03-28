"""Sandbox configuration models."""

from loguru import logger
from enum import Enum
from typing import Optional
from pydantic import BaseModel, Field


class SandboxType(str, Enum):
    """Supported sandbox backend types."""

    SUBPROCESS = "subprocess"
    DOCKER = "docker"
    E2B = "e2b"
    JUDGE0 = "judge0"
    CODEDANDBOX = "codesandbox"
    SELF_HOSTED = "self_hosted"
    AIO_SANDBOX = "aio_sandbox"


class SandboxConfig(BaseModel):
    """Configuration for sandbox backend."""

    type: SandboxType = SandboxType.SUBPROCESS
    enabled: bool = True

    # Local sandbox options
    cpu_limit: str = "0.5"
    memory_limit: str = "256m"
    allow_network: bool = False

    # API sandbox options
    api_key: str = ""
    api_url: str = ""

    # Common options
    default_timeout: int = Field(default=30, ge=1, le=300)
    max_timeout: int = Field(default=60, ge=1, le=300)

    # Language mapping for API sandboxes
    # Maps our internal language names to API-specific language IDs
    language_mapping: dict[str, str] = Field(default_factory=lambda: {
        "python": "python",
        "bash": "bash",
        "node": "javascript",
        "javascript": "javascript",
    })

    class Config:
        use_enum_values = True

    @classmethod
    def from_dict(
        cls, config: dict, fallback_config: Optional["SandboxConfig"] = None
    ) -> "SandboxConfig":
        """从 dict 构建 SandboxConfig，支持字段级 fallback。

        Args:
            config: 工具配置 dict
            fallback_config: 回退配置（通常是环境变量配置）

        Returns:
            SandboxConfig 实例
        """
        def get_value(key: str, default=None, encrypt: bool = False):
            """获取配置值，优先从 config 读取，缺失则使用 fallback。"""
            value = config.get(key)
            if value is None or value == "":
                if fallback_config:
                    value = getattr(fallback_config, key, default)
                else:
                    value = default

            # 解密敏感字段
            if encrypt and value:
                try:
                    from app.core.security import decrypt_data
                    from app.config import get_settings

                    settings = get_settings()
                    decrypted = decrypt_data(value, settings.SECRET_KEY)
                    value = decrypted
                except Exception as e:
                    logger.warning(f"[SandboxConfig] Failed to decrypt {key}: {e}")
                    # 解密失败，使用 fallback
                    if fallback_config:
                        value = getattr(fallback_config, key, default)
                    else:
                        value = default
            return value

        # Map config key names to SandboxConfig attributes
        sandbox_type_str = get_value("sandbox_type", "subprocess")
        try:
            sandbox_type = SandboxType(sandbox_type_str)
        except ValueError:
            sandbox_type = SandboxType.SUBPROCESS

        result = cls(
            type=sandbox_type,
            enabled=True,  # Always enabled when explicitly configured
            api_key=get_value("api_key", "", encrypt=True),
            api_url=get_value("api_url", ""),
            cpu_limit=get_value("cpu_limit", "0.5"),
            memory_limit=get_value("memory_limit", "256m"),
            allow_network=get_value("allow_network", False),
            default_timeout=get_value("default_timeout", 30),
            max_timeout=get_value("max_timeout", 60),
        )
        return result