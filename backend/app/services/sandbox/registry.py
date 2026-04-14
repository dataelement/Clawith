"""Sandbox backend registry and factory."""

from typing import Type
from loguru import logger

from app.services.sandbox.base import SandboxBackend
from app.services.sandbox.config import SandboxConfig, SandboxType


def get_sandbox_backend(config: SandboxConfig) -> SandboxBackend:
    """
    Factory function: create sandbox backend instance based on config.

    This function uses dependency injection to allow easy testing and
    customization. For production use, pass the config from settings.

    Args:
        config: SandboxConfig describing which backend to create

    Returns:
        SandboxBackend instance

    Raises:
        ValueError: If the sandbox type is unknown or not supported
    """
    if not config.enabled:
        raise ValueError("Sandbox is disabled")

    backend_class = _BACKEND_REGISTRY.get(config.type)
    if not backend_class:
        raise ValueError(f"Unknown sandbox type: {config.type}")

    return backend_class(config)


# Registry mapping - populated at module load time
# Using module-level dict to avoid test pollution (unlike class variables)
_BACKEND_REGISTRY: dict[SandboxType, Type[SandboxBackend]] = {}


def register_sandbox_backend(
    sandbox_type: SandboxType,
    backend_class: Type[SandboxBackend]
) -> None:
    """
    Register a sandbox backend implementation.

    This function can be used to register custom backends at runtime.

    Args:
        sandbox_type: The SandboxType to register
        backend_class: The backend class to use for this type
    """
    _BACKEND_REGISTRY[sandbox_type] = backend_class


def get_registered_backends() -> dict[SandboxType, Type[SandboxBackend]]:
    """Get all registered sandbox backends."""
    return _BACKEND_REGISTRY.copy()


# Import and register built-in backends
# These are imported lazily to avoid circular imports
def _register_builtin_backends() -> None:
    """Register all built-in sandbox backends."""
    from app.services.sandbox.local.subprocess_backend import SubprocessBackend
    from app.services.sandbox.local.docker_backend import DockerBackend
    from app.services.sandbox.api.e2b_backend import E2bBackend
    from app.services.sandbox.api.judge0_backend import Judge0Backend
    from app.services.sandbox.api.codesandbox_backend import CodeSandboxBackend
    from app.services.sandbox.remote.self_hosted_backend import SelfHostedBackend
    from app.services.sandbox.remote.aio_sandbox_backend import AioSandboxBackend

    _BACKEND_REGISTRY[SandboxType.SUBPROCESS] = SubprocessBackend
    _BACKEND_REGISTRY[SandboxType.DOCKER] = DockerBackend
    _BACKEND_REGISTRY[SandboxType.E2B] = E2bBackend
    _BACKEND_REGISTRY[SandboxType.JUDGE0] = Judge0Backend
    _BACKEND_REGISTRY[SandboxType.CODEDANDBOX] = CodeSandboxBackend
    _BACKEND_REGISTRY[SandboxType.SELF_HOSTED] = SelfHostedBackend
    _BACKEND_REGISTRY[SandboxType.AIO_SANDBOX] = AioSandboxBackend


# Register built-in backends on module import
_register_builtin_backends()