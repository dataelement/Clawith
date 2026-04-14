"""aio-sandbox backend."""

import time

import httpx

from app.services.sandbox.base import BaseSandboxBackend, ExecutionResult, SandboxCapabilities
from app.services.sandbox.config import SandboxConfig
from loguru import logger


class AioSandboxBackend(BaseSandboxBackend):
    """aio-sandbox backend.

    Connects to aio-sandbox (https://github.com/agent-infra/sandbox).

    Supports:
    - Shell execution (/v1/shell/exec): bash, node
    - Jupyter execution (/v1/jupyter/execute): python

    Configuration:
    - SANDBOX_API_URL: Base URL of aio-sandbox (e.g., http://localhost:8080)
    - SANDBOX_API_TYPE: Execution type - "shell" or "jupyter" (default: shell)
    - SANDBOX_API_KEY: Optional JWT token for authentication
    """

    name = "aio_sandbox"

    def __init__(self, config: SandboxConfig):
        self.config = config
        self.base_url = config.api_url.rstrip("/") if config.api_url else ""

        if not self.base_url:
            raise ValueError(
                "aio-sandbox URL is required. "
                "Set SANDBOX_API_URL environment variable (e.g., http://localhost:8080)."
            )

    def get_capabilities(self) -> SandboxCapabilities:
        return SandboxCapabilities(
            supported_languages=["python", "bash", "node", "javascript"],
            max_timeout=self.config.max_timeout,
            max_memory_mb=512,
            network_available=True,
            filesystem_available=True,
        )

    async def health_check(self) -> bool:
        """Check if aio-sandbox service is available."""
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    f"{self.base_url}/v1/sandbox",
                    timeout=5.0
                )
                return response.status_code == 200
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
        """Execute code using aio-sandbox."""
        start_time = time.time()

        # Determine endpoint based on language
        # Use jupyter for python, shell for others
        if language == "python":
            endpoint = f"{self.base_url}/v1/jupyter/execute"
            payload = {"code": code}
        else:
            # Shell execution for bash/node
            endpoint = f"{self.base_url}/v1/shell/exec"

            # Build command based on language
            if language == "bash":
                cmd = code
            elif language == "node":
                cmd = f"node -e {repr(code)}"
            elif language == "javascript":
                cmd = f"node -e {repr(code)}"
            else:
                return ExecutionResult(
                    success=False,
                    stdout="",
                    stderr="",
                    exit_code=1,
                    duration_ms=0,
                    error=f"Unsupported language: {language}. Use python, bash, or node."
                )

            payload = {"command": cmd}

        # Build headers
        headers = {"Content-Type": "application/json"}
        if self.config.api_key:
            headers["Authorization"] = f"Bearer {self.config.api_key}"

        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    endpoint,
                    json=payload,
                    headers=headers,
                    timeout=float(timeout + 10)
                )

                duration_ms = int((time.time() - start_time) * 1000)

                if response.status_code != 200:
                    return ExecutionResult(
                        success=False,
                        stdout="",
                        stderr="",
                        exit_code=response.status_code,
                        duration_ms=duration_ms,
                        error=f"aio-sandbox error: HTTP {response.status_code} - {response.text[:200]}"
                    )

                result = response.json()

                # Parse response
                # Shell: {"success": true, "data": {"output": "...", "exit_code": 0}}
                # Jupyter: {"success": true, "data": {"status": "ok", "outputs": [{"text": "..."}]}}

                stdout = ""
                stderr = ""
                success = result.get("success", True)
                error_msg = None
                exit_code = 0

                data = result.get("data", {})

                if language == "python":
                    # Jupyter format - outputs is a list
                    if data and "outputs" in data:
                        outputs = data["outputs"]
                        text_parts = []
                        for out in outputs:
                            if out.get("output_type") == "stream" and out.get("name") == "stdout":
                                text_parts.append(out.get("text", ""))
                            elif out.get("output_type") == "error":
                                stderr += out.get("traceback", [""])[0] if out.get("traceback") else out.get("evalue", "")
                        stdout = "".join(text_parts)

                    if data.get("status") != "ok":
                        success = False
                        exit_code = 1
                else:
                    # Shell format
                    if data:
                        stdout = data.get("output", "")
                        exit_code = data.get("exit_code", 0)

                    if not success:
                        error_msg = data.get("output", "Command failed")

                return ExecutionResult(
                    success=success,
                    stdout=stdout[:10000],
                    stderr=stderr[:5000],
                    exit_code=exit_code,
                    duration_ms=duration_ms,
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
            logger.exception(f"[AioSandbox] Execution error")
            return ExecutionResult(
                success=False,
                stdout="",
                stderr="",
                exit_code=1,
                duration_ms=duration_ms,
                error=f"aio-sandbox error: {str(e)[:200]}"
            )