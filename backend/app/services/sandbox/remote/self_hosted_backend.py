"""Self-hosted sandbox backend."""

import logging
import time

import httpx

from app.services.sandbox.base import BaseSandboxBackend, ExecutionResult, SandboxCapabilities
from app.services.sandbox.config import SandboxConfig

logger = logging.getLogger(__name__)


class SelfHostedBackend(BaseSandboxBackend):
    """Self-hosted sandbox backend.

    Connects to user-deployed sandbox services like aio-sandbox.

    Usage:
    - Set SANDBOX_API_URL to full endpoint URL
    - For aio-sandbox shell: http://localhost:8080/v1/shell/exec
    - For aio-sandbox jupyter: http://localhost:8080/v1/jupyter/execute

    Response format expected: {"success": bool, "output": str, "error": str?}
    """

    name = "self_hosted"

    def __init__(self, config: SandboxConfig):
        self.config = config

        if not config.api_url:
            raise ValueError(
                "Self-hosted sandbox URL is required. "
                "Set SANDBOX_API_URL environment variable."
            )

        # Normalize URL (remove trailing slash)
        self.api_url = config.api_url.rstrip("/")

    def get_capabilities(self) -> SandboxCapabilities:
        # Capabilities depend on the self-hosted service
        # We'll report conservative defaults
        return SandboxCapabilities(
            supported_languages=["python", "bash", "node", "javascript"],
            max_timeout=self.config.max_timeout,
            max_memory_mb=256,
            network_available=True,
            filesystem_available=True,
        )

    async def health_check(self) -> bool:
        """Check if the self-hosted service is available."""
        try:
            async with httpx.AsyncClient() as client:
                # Try /v1/sandbox first (aio-sandbox), then fall back to /health
                for endpoint in ["/v1/sandbox", "/health"]:
                    check_url = self.api_url.split("/v1/")[0] + endpoint if "/v1/" in self.api_url else f"{self.api_url.rsplit('/', 1)[0]}/health"
                    try:
                        response = await client.get(check_url, timeout=5.0)
                        if response.status_code == 200:
                            return True
                    except Exception:
                        continue
                return False
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
        """Execute code using the self-hosted sandbox service."""
        start_time = time.time()

        # Build request
        headers = {
            "Content-Type": "application/json",
        }

        # Add API key if configured
        if self.config.api_key:
            headers["Authorization"] = f"Bearer {self.config.api_key}"

        # Build payload based on the API endpoint
        # For shell exec: {"cmd": "..."}
        # For jupyter: {"code": "..."}
        payload = {}

        # Detect endpoint type from URL and build appropriate payload
        url_lower = self.api_url.lower()
        if "shell" in url_lower:
            # aio-sandbox shell: wrap code as command
            if language == "python":
                cmd = f"python3 -c {repr(code)}"
            elif language == "bash":
                cmd = code
            elif language == "node":
                cmd = f"node -e {repr(code)}"
            else:
                cmd = code
            payload = {"cmd": cmd}
        elif "jupyter" in url_lower:
            # aio-sandbox jupyter
            payload = {"code": code}
        else:
            # Generic format
            payload = {
                "code": code,
                "language": language,
                "timeout": timeout,
            }

        # Add any additional kwargs
        payload.update(kwargs)

        try:
            async with httpx.AsyncClient() as client:
                # Use URL directly without appending /execute
                response = await client.post(
                    self.api_url,
                    json=payload,
                    headers=headers,
                    timeout=float(timeout + 10)  # Add buffer for network
                )

                duration_ms = int((time.time() - start_time) * 1000)

                if response.status_code != 200:
                    return ExecutionResult(
                        success=False,
                        stdout="",
                        stderr="",
                        exit_code=response.status_code,
                        duration_ms=duration_ms,
                        error=f"Sandbox service error: HTTP {response.status_code} - {response.text[:200]}"
                    )

                result = response.json()

                # Parse response - support multiple formats:
                # Generic: {"success": true, "stdout": "...", "stderr": "...", "exit_code": 0}
                # aio-sandbox shell: {"success": true, "data": {"output": "..."}}
                # aio-sandbox jupyter: {"output": "...", "status": "ok"}

                # Try to extract output
                output = ""
                stderr = ""
                success = True
                error_msg = None
                exit_code = 0

                # aio-sandbox shell format
                if "data" in result and isinstance(result.get("data"), dict):
                    output = result["data"].get("output", "")
                # aio-sandbox jupyter format
                elif "output" in result and "status" in result:
                    output = result.get("output", "")
                    if result.get("status") != "ok":
                        success = False
                        error_msg = result.get("error", result.get("output", ""))
                # Generic format
                else:
                    output = result.get("stdout") or result.get("output") or ""
                    stderr = result.get("stderr") or ""
                    success = result.get("success", True)
                    exit_code = result.get("exit_code", 0 if success else 1)
                    error_msg = result.get("error")

                return ExecutionResult(
                    success=success,
                    stdout=output[:10000],
                    stderr=stderr[:5000],
                    exit_code=exit_code,
                    duration_ms=result.get("duration_ms", duration_ms),
                    error=error_msg
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
            logger.exception(f"[SelfHosted] Execution error")
            return ExecutionResult(
                success=False,
                stdout="",
                stderr="",
                exit_code=1,
                duration_ms=duration_ms,
                error=f"Self-hosted sandbox error: {str(e)[:200]}"
            )