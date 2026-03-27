"""E2B API-based sandbox backend."""

import logging
import time
from typing import Any

from app.services.sandbox.base import BaseSandboxBackend, ExecutionResult, SandboxCapabilities
from app.services.sandbox.config import SandboxConfig

logger = logging.getLogger(__name__)

# Lazy import e2b to make it optional
_e2b = None


def _get_e2b():
    """Lazy load e2b SDK."""
    global _e2b
    if _e2b is None:
        try:
            import e2b
            _e2b = e2b
        except ImportError:
            raise ImportError(
                "e2b package is required for E2B backend. "
                "Install it with: pip install e2b"
            )
    return _e2b


# Language mapping for E2B
_LANGUAGE_MAP = {
    "python": "python",
    "bash": "bash",
    "node": "node",
    "javascript": "javascript",
}


class E2bBackend(BaseSandboxBackend):
    """E2B cloud-based sandbox backend.

    E2B (https://e2b.dev/) provides secure, cloud-based code execution
    with built-in isolation and networking.
    """

    name = "e2b"

    def __init__(self, config: SandboxConfig):
        self.config = config
        self._client = None

        if not config.api_key:
            raise ValueError("E2B API key is required. Set SANDBOX_API_KEY environment variable.")

    @property
    def client(self):
        """Get or create E2B client."""
        e2b_lib = _get_e2b()
        if self._client is None:
            self._client = e2b_lib.AsyncSandbox
        return self._client

    def get_capabilities(self) -> SandboxCapabilities:
        return SandboxCapabilities(
            supported_languages=["python", "bash", "node", "javascript"],
            max_timeout=self.config.max_timeout,
            max_memory_mb=512,
            network_available=True,
            filesystem_available=True,
        )

    async def health_check(self) -> bool:
        """Check if E2B service is available."""
        try:
            e2b_lib = _get_e2b()
            # Try to list sandboxes to verify API is accessible
            await e2b_lib.AsyncSandbox.list(api_key=self.config.api_key)
            return True
        except Exception:
            return False

    async def execute(
        self,
        code: str,
        language: str,
        timeout: int = 30,
        work_dir: str | None = None,
        **kwargs
    ) -> ExecutionResult:
        """Execute code using E2B cloud sandbox."""
        start_time = time.time()

        # Map language to E2B format
        e2b_language = _LANGUAGE_MAP.get(language, language)
        if language not in _LANGUAGE_MAP:
            return ExecutionResult(
                success=False,
                stdout="",
                stderr="",
                exit_code=1,
                duration_ms=int((time.time() - start_time) * 1000),
                error=f"Unsupported language: {language}"
            )

        e2b_lib = _get_e2b()

        try:
            # Create sandbox and run code
            async with await e2b_lib.AsyncSandbox.create(
                api_key=self.config.api_key,
                timeout=timeout,
            ) as sandbox:
                # Build the command based on language
                if e2b_language == "python":
                    cmd = "python3"
                    args = ["-c", code]
                elif e2b_language == "bash":
                    cmd = "bash"
                    args = ["-c", code]
                elif e2b_language in ("node", "javascript"):
                    cmd = "node"
                    args = ["-e", code]
                else:
                    return ExecutionResult(
                        success=False,
                        stdout="",
                        stderr="",
                        exit_code=1,
                        duration_ms=int((time.time() - start_time) * 1000),
                        error=f"Unsupported language: {language}"
                    )

                # Run the command - use string format for e2b
                cmd_str = f"{cmd} {args[0]} '{args[1]}'"
                result = await sandbox.commands.run(cmd_str)

            duration_ms = int((time.time() - start_time) * 1000)

            return ExecutionResult(
                success=result.exit_code == 0,
                stdout=result.stdout or "",
                stderr=result.stderr or "",
                exit_code=result.exit_code or 0,
                duration_ms=duration_ms,
                error=None
            )

        except Exception as e:
            duration_ms = int((time.time() - start_time) * 1000)
            error_msg = str(e)
            logger.exception(f"[E2B] Execution error")

            # Handle timeout
            if "timeout" in error_msg.lower():
                return ExecutionResult(
                    success=False,
                    stdout="",
                    stderr="",
                    exit_code=124,
                    duration_ms=duration_ms,
                    error=f"Code execution timed out after {timeout}s"
                )

            return ExecutionResult(
                success=False,
                stdout="",
                stderr="",
                exit_code=1,
                duration_ms=duration_ms,
                error=f"E2B execution error: {error_msg[:200]}"
            )