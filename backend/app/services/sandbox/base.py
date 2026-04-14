"""Sandbox backend interface definitions."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass
class ExecutionResult:
    """Result of code execution in a sandbox."""

    success: bool
    stdout: str
    stderr: str
    exit_code: int
    duration_ms: int
    error: str | None = None


@dataclass
class SandboxCapabilities:
    """Capabilities of a sandbox backend."""

    supported_languages: list[str]
    max_timeout: int
    max_memory_mb: int
    network_available: bool
    filesystem_available: bool


@runtime_checkable
class SandboxBackend(Protocol):
    """Protocol defining the interface for sandbox backends."""

    @property
    def name(self) -> str:
        """Backend name for identification."""
        ...

    async def execute(
        self,
        code: str,
        language: str,
        timeout: int = 30,
        work_dir: str | None = None,
        **kwargs
    ) -> ExecutionResult:
        """
        Execute code in the sandbox.

        Args:
            code: The code to execute
            language: Programming language (python, bash, node, etc.)
            timeout: Execution timeout in seconds
            work_dir: Working directory for execution (optional)
            **kwargs: Additional backend-specific options

        Returns:
            ExecutionResult with execution details
        """
        ...

    async def health_check(self) -> bool:
        """
        Check if the sandbox backend is healthy and available.

        Returns:
            True if the backend is healthy, False otherwise
        """
        ...

    def get_capabilities(self) -> SandboxCapabilities:
        """
        Get the capabilities of this sandbox backend.

        Returns:
            SandboxCapabilities describing what this backend supports
        """
        ...


class BaseSandboxBackend(ABC):
    """Base class providing common functionality for sandbox backends."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Backend name for identification."""
        pass

    @abstractmethod
    async def execute(
        self,
        code: str,
        language: str,
        timeout: int = 30,
        work_dir: str | None = None,
        **kwargs
    ) -> ExecutionResult:
        """Execute code in the sandbox."""
        pass

    @abstractmethod
    async def health_check(self) -> bool:
        """Check if the sandbox backend is healthy."""
        pass

    @abstractmethod
    def get_capabilities(self) -> SandboxCapabilities:
        """Get the capabilities of this sandbox backend."""
        pass

    def _format_result(self, result: ExecutionResult) -> str:
        """Format execution result for user display."""
        result_parts = []

        if result.stdout.strip():
            result_parts.append(f"📤 Output:\n{result.stdout}")
        if result.stderr.strip():
            result_parts.append(f"⚠️ Stderr:\n{result.stderr}")
        if result.error:
            result_parts.append(f"❌ Error: {result.error}")
        if result.exit_code != 0 and not result.error:
            result_parts.append(f"Exit code: {result.exit_code}")

        if not result_parts:
            return "✅ Code executed successfully (no output)"

        return "\n\n".join(result_parts)