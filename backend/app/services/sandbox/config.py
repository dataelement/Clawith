"""Sandbox configuration models."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from enum import Enum
from typing import Literal

from loguru import logger
from pydantic import BaseModel, Field

CODE_EXECUTION_DEFAULT_TIMEOUT_SECONDS = 180
CODE_EXECUTION_MAX_TIMEOUT_SECONDS = 300


class SandboxConfigurationError(ValueError):
    """Configured Sandbox values are invalid at their owning boundary."""


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
    allow_network: bool = True
    allow_unsafe_fallback_when_bwrap_missing: bool = False
    workspace_mode: Literal["merge", "isolated_output"] = "merge"
    publication_owner: Literal["gateway", "workspace_cas"] = "workspace_cas"

    # API sandbox options
    api_key: str = ""
    api_url: str = ""

    # Common options
    default_timeout: int = Field(
        default=CODE_EXECUTION_DEFAULT_TIMEOUT_SECONDS,
        ge=1,
        le=3600,
    )
    max_timeout: int = Field(
        default=CODE_EXECUTION_MAX_TIMEOUT_SECONDS,
        ge=1,
        le=3600,
    )

    # Proxy options
    http_proxy: str | None = None
    https_proxy: str | None = None
    no_proxy: str | None = None

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
        cls,
        config: Mapping[str, object],
        fallback_config: SandboxConfig | None = None,
        *,
        secret_decoder: Callable[[str], str] | None = None,
    ) -> SandboxConfig:
        """从 dict 构建 SandboxConfig，支持字段级 fallback。

        Args:
            config: 工具配置 dict
            fallback_config: 回退配置（通常是环境变量配置）
            secret_decoder: 调用方提供的已配置密钥解码函数

        Returns:
            SandboxConfig 实例
        """
        def get_value(
            key: str,
            default: object = None,
            encrypt: bool = False,
        ) -> object:
            """获取配置值，优先从 config 读取，缺失则使用 fallback。"""
            value = config.get(key)
            configured = value is not None and value != ""
            if not configured:
                if fallback_config:
                    value = getattr(fallback_config, key, default)
                else:
                    value = default
            if key == "allow_network":
                logger.info(f"[SandboxConfig] allow_network: raw={config.get(key)!r}, resolved={value!r}")

            # 解密敏感字段
            if encrypt and configured:
                if not isinstance(value, str):
                    raise SandboxConfigurationError(
                        f"Configured {key} must be an encrypted string"
                    )
                if secret_decoder is None:
                    raise SandboxConfigurationError(
                        f"Configured {key} requires an explicit secret decoder"
                    )
                try:
                    value = secret_decoder(value)
                except Exception as exc:
                    raise SandboxConfigurationError(
                        f"Configured {key} could not be decrypted"
                    ) from exc
            return value

        # Map config key names to SandboxConfig attributes
        configured_sandbox_type = config.get("sandbox_type")
        if configured_sandbox_type is None or configured_sandbox_type == "":
            sandbox_type_value = (
                fallback_config.type
                if fallback_config is not None
                else SandboxType.SUBPROCESS.value
            )
        else:
            sandbox_type_value = configured_sandbox_type
        if not isinstance(sandbox_type_value, (str, SandboxType)):
            raise SandboxConfigurationError(
                "Configured sandbox_type must be a supported string"
            )
        try:
            sandbox_type = SandboxType(sandbox_type_value)
        except ValueError as exc:
            raise SandboxConfigurationError(
                f"Unsupported sandbox_type: {sandbox_type_value!r}"
            ) from exc

        resolved: dict[str, object] = {
            "type": sandbox_type,
            "enabled": True,  # Always enabled when explicitly configured
            "api_key": get_value("api_key", "", encrypt=True),
            "api_url": get_value("api_url", ""),
            "cpu_limit": get_value("cpu_limit", "0.5"),
            "memory_limit": get_value("memory_limit", "256m"),
            "allow_network": get_value("allow_network", False),
            "allow_unsafe_fallback_when_bwrap_missing": get_value(
                "allow_unsafe_fallback_when_bwrap_missing",
                False,
            ),
            "workspace_mode": get_value("workspace_mode", "merge"),
            "publication_owner": get_value("publication_owner", "workspace_cas"),
            "default_timeout": get_value(
                "default_timeout",
                CODE_EXECUTION_DEFAULT_TIMEOUT_SECONDS,
            ),
            "max_timeout": get_value(
                "max_timeout",
                CODE_EXECUTION_MAX_TIMEOUT_SECONDS,
            ),
            "http_proxy": get_value("http_proxy", None),
            "https_proxy": get_value("https_proxy", None),
            "no_proxy": get_value("no_proxy", None),
        }
        return cls.model_validate(resolved)
