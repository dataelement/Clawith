"""Sandbox backend interface definitions."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, runtime_checkable

# Inline truncation limits (protect LLM context window)
_MAX_STDOUT_INLINE = 10_000   # ~10KB
_MAX_STDERR_INLINE = 5_000    # ~5KB


@dataclass
class ExecutionResult:
    """Result of code execution in a sandbox."""

    success: bool
    stdout: str
    stderr: str
    exit_code: int
    duration_ms: int
    error: str | None = None
    output_file: str | None = field(default=None, repr=False)
    """If stdout/stderr exceeded inline limits, full output was saved here."""


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

    def _format_result(
        self, result: ExecutionResult, work_dir: str | None = None
    ) -> str:
        """Format execution result for user display.

        If stdout/stderr exceed inline limits, the full output is saved
        to a file inside *work_dir* and a path is returned to the LLM
        so it can use ``read_file`` to inspect the full content.
        """
        result_parts: list[str] = []
        truncated = False

        # --- stdout ---
        if len(result.stdout) > _MAX_STDOUT_INLINE:
            truncated = True
            file_path = self._save_overflow(
                result.stdout, work_dir, prefix="stdout", suffix=".log"
            )
            if file_path:
                result.output_file = file_path
                result_parts.append(
                    f"📤 Output (truncated, {len(result.stdout):,} chars total):\n"
                    f"{result.stdout[:_MAX_STDOUT_INLINE]}\n"
                    f"... [truncated] Full output saved to: {file_path}"
                )
            else:
                # Cannot save file — fall back to truncation only
                result_parts.append(
                    f"📤 Output (truncated to {_MAX_STDOUT_INLINE} chars):\n"
                    f"{result.stdout[:_MAX_STDOUT_INLINE]}"
                )
        elif result.stdout.strip():
            result_parts.append(f"📤 Output:\n{result.stdout}")

        # --- stderr ---
        if len(result.stderr) > _MAX_STDERR_INLINE:
            truncated = True
            file_path = self._save_overflow(
                result.stderr, work_dir, prefix="stderr", suffix=".log"
            )
            if file_path:
                result_parts.append(
                    f"⚠️ Stderr (truncated, {len(result.stderr):,} chars total):\n"
                    f"{result.stderr[:_MAX_STDERR_INLINE]}\n"
                    f"... [truncated] Full output saved to: {file_path}"
                )
            else:
                result_parts.append(
                    f"⚠️ Stderr (truncated to {_MAX_STDERR_INLINE} chars):\n"
                    f"{result.stderr[:_MAX_STDERR_INLINE]}"
                )
        elif result.stderr.strip():
            result_parts.append(f"⚠️ Stderr:\n{result.stderr}")

        # --- error / exit code ---
        if result.error:
            result_parts.append(f"❌ Error: {result.error}")
        if result.exit_code != 0 and not result.error:
            result_parts.append(f"Exit code: {result.exit_code}")

        if not result_parts:
            return "✅ Code executed successfully (no output)"

        return "\n\n".join(result_parts)

    @staticmethod
    def _save_overflow(
        content: str, work_dir: str | None, prefix: str = "output", suffix: str = ".log"
    ) -> str | None:
        """Save overflow content to a file. Returns relative path or None."""
        import time as _time

        if not work_dir:
            return None
        try:
            out_dir = Path(work_dir) / "workspace"
            out_dir.mkdir(parents=True, exist_ok=True)
            ts = int(_time.time() * 1000)
            file_path = out_dir / f"_exec_{prefix}_{ts}{suffix}"
            file_path.write_text(content, encoding="utf-8")
            # Return relative path from work_dir root for LLM readability
            # Always use forward slashes for consistency across platforms
            try:
                return file_path.relative_to(Path(work_dir)).as_posix()
            except ValueError:
                return file_path.as_posix()
        except Exception:
            return None