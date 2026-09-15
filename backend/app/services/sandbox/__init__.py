"""Sandbox service module.

This module provides a pluggable architecture for code execution sandbox backends.

Usage:
    from app.services.sandbox.registry import get_sandbox_backend
    from app.services.sandbox.config import SandboxConfig, SandboxType

    # Create config from settings
    config = SandboxConfig(
        type=SandboxType.SUBPROCESS,
        enabled=True,
    )

    # Get backend instance
    backend = get_sandbox_backend(config)

    # Execute code
    result = await backend.execute(code, language, timeout)
"""

from app.services.sandbox.base import (
    BaseSandboxBackend,
    ExecutionResult,
    SandboxBackend,
    SandboxCapabilities,
)
from app.services.sandbox.config import SandboxConfig, SandboxType
from app.services.sandbox.registry import (
    get_registered_backends,
    get_sandbox_backend,
    register_sandbox_backend,
)

__all__ = [
    "BaseSandboxBackend",
    "ExecutionResult",
    "SandboxBackend",
    "SandboxCapabilities",
    "SandboxConfig",
    "SandboxType",
    "get_registered_backends",
    "get_sandbox_backend",
    "register_sandbox_backend",
]
