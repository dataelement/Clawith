"""CodeSandbox API-based sandbox backend."""

import logging
import time

import httpx

from app.services.sandbox.base import BaseSandboxBackend, ExecutionResult, SandboxCapabilities
from app.services.sandbox.config import SandboxConfig

logger = logging.getLogger(__name__)

# CodeSandbox language mapping
_CODESANDBOX_LANGUAGES = {
    "python": "python",
    "javascript": "javascript",
    "node": "javascript",
    "typescript": "typescript",
}


class CodeSandboxBackend(BaseSandboxBackend):
    """CodeSandbox API-based sandbox backend.

    CodeSandbox (https://codesandbox.io/) provides cloud-based development
    and execution environments via their API.
    """

    name = "codesandbox"

    def __init__(self, config: SandboxConfig):
        self.config = config
        self.api_url = "https://codesandbox.io/api/v1/sandboxes/exec"

        if not config.api_key:
            raise ValueError(
                "CodeSandbox API key is required. "
                "Set SANDBOX_API_KEY environment variable."
            )

    def get_capabilities(self) -> SandboxCapabilities:
        return SandboxCapabilities(
            supported_languages=["python", "javascript", "node", "typescript"],
            max_timeout=self.config.max_timeout,
            max_memory_mb=512,
            network_available=True,
            filesystem_available=True,
        )

    async def health_check(self) -> bool:
        """Check if CodeSandbox API is available."""
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    "https://codesandbox.io/api/v1/sandboxes",
                    headers={"Authorization": f"Bearer {self.config.api_key}"},
                    timeout=5.0
                )
                return response.status_code in (200, 401)  # 401 means auth works but no sandboxes
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
        """Execute code using CodeSandbox API."""
        start_time = time.time()

        # Map language
        sandbox_language = _CODESANDBOX_LANGUAGES.get(language.lower())
        if sandbox_language is None:
            return ExecutionResult(
                success=False,
                stdout="",
                stderr="",
                exit_code=1,
                duration_ms=int((time.time() - start_time) * 1000),
                error=f"Unsupported language: {language}. Supported: {', '.join(_CODESANDBOX_LANGUAGES.keys())}"
            )

        try:
            async with httpx.AsyncClient() as client:
                # Prepare the sandbox specification
                # CodeSandbox uses a simple execution model
                files = {
                    f"index.{'py' if sandbox_language == 'python' else 'js'}": {
                        "content": code
                    }
                }

                response = await client.post(
                    self.api_url,
                    json={
                        "files": files,
                        "language": sandbox_language,
                    },
                    headers={
                        "Authorization": f"Bearer {self.config.api_key}",
                        "Content-Type": "application/json",
                    },
                    timeout=float(timeout + 5)  # Add buffer for API call
                )

                duration_ms = int((time.time() - start_time) * 1000)

                if response.status_code != 200:
                    return ExecutionResult(
                        success=False,
                        stdout="",
                        stderr="",
                        exit_code=1,
                        duration_ms=duration_ms,
                        error=f"CodeSandbox API error: {response.status_code}"
                    )

                result = response.json()

                # Extract output
                stdout = result.get("output", "") or ""
                stderr = result.get("errors", "") or ""
                exit_code = result.get("exitCode", 0)

                # Truncate output
                stdout = stdout[:10000]
                stderr = stderr[:5000]

                return ExecutionResult(
                    success=exit_code == 0,
                    stdout=stdout,
                    stderr=stderr,
                    exit_code=exit_code,
                    duration_ms=duration_ms,
                    error=None if exit_code == 0 else f"Exit code: {exit_code}"
                )

        except httpx.TimeoutException:
            duration_ms = int((time.time() - start_time) * 1000)
            return ExecutionResult(
                success=False,
                stdout="",
                stderr="",
                exit_code=124,
                duration_ms=duration_ms,
                error=f"Code execution timed out after {timeout}s"
            )

        except Exception as e:
            duration_ms = int((time.time() - start_time) * 1000)
            logger.exception(f"[CodeSandbox] Execution error")
            return ExecutionResult(
                success=False,
                stdout="",
                stderr="",
                exit_code=1,
                duration_ms=duration_ms,
                error=f"CodeSandbox execution error: {str(e)[:200]}"
            )