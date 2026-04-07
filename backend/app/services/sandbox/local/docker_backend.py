"""Local docker-based sandbox backend."""

import asyncio
import os
import time
from pathlib import Path

from app.services.sandbox.base import BaseSandboxBackend, ExecutionResult, SandboxCapabilities
from app.services.sandbox.config import SandboxConfig
from loguru import logger

# Lazy import docker to make it optional
_docker = None

# Global shared dependencies directory (persisted via Docker volume)
_SHARED_DEPS_DIR = os.environ.get("SHARED_DEPS_DIR", "/data/shared-deps")


def _ensure_deps_dirs() -> None:
    """Ensure shared dependency directories exist (no-op if path is not writable)."""
    for subdir in ("pip", "node_modules"):
        d = Path(_SHARED_DEPS_DIR) / subdir
        try:
            d.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass


_ensure_deps_dirs()


def _get_docker():
    """Lazy load docker SDK."""
    global _docker
    if _docker is None:
        try:
            import docker
            _docker = docker
        except ImportError:
            raise ImportError(
                "docker package is required for docker backend. "
                "Install it with: pip install docker"
            )
    return _docker


# Language to docker image mapping
_DOCKER_IMAGES = {
    "python": "python:3.11-slim",
    "bash": "bash:5.2",
    "node": "node:18-slim",
}


class DockerBackend(BaseSandboxBackend):
    """Docker-based sandbox backend.

    Executes code inside Docker containers for better isolation.
    Requires the docker SDK and a running Docker daemon.
    """

    name = "docker"

    def __init__(self, config: SandboxConfig):
        self.config = config
        self._client = None

    @property
    def client(self):
        """Lazy load docker client."""
        if self._client is None:
            docker_lib = _get_docker()
            self._client = docker_lib.from_env()
        return self._client

    def get_capabilities(self) -> SandboxCapabilities:
        return SandboxCapabilities(
            supported_languages=["python", "bash", "node"],
            max_timeout=self.config.max_timeout,
            max_memory_mb=256,
            network_available=self.config.allow_network,
            filesystem_available=True,
        )

    async def health_check(self) -> bool:
        """Check if docker is available and running."""
        try:
            self.client.ping()
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
        """Execute code inside a docker container."""
        start_time = time.time()

        # Validate language
        if language not in _DOCKER_IMAGES:
            return ExecutionResult(
                success=False,
                stdout="",
                stderr="",
                exit_code=1,
                duration_ms=int((time.time() - start_time) * 1000),
                error=f"Unsupported language: {language}. Use: {', '.join(_DOCKER_IMAGES.keys())}"
            )

        image = _DOCKER_IMAGES[language]

        # Resolve CWD: mount agent root, but set CWD to workspace/ subdir
        container_workspace = "/workspace"
        container_cwd = "/workspace/workspace"  # CWD inside container
        agent_root_host = work_dir  # agent root on host

        # Environment with shared dependency paths (container is always Linux)
        env = {
            "HOME": container_cwd,
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONPATH": f"{container_cwd}:{_SHARED_DEPS_DIR}/pip",
            "NODE_PATH": f"{_SHARED_DEPS_DIR}/node_modules",
            "WORKSPACE_DIR": container_cwd,
            "AGENT_ROOT": container_workspace,
        }

        # Build volume mounts: agent root + global shared deps
        volumes_list = []
        if work_dir:
            volumes_list.append(f"{work_dir}:{container_workspace}")
        volumes_list.append(f"{_SHARED_DEPS_DIR}/pip:{_SHARED_DEPS_DIR}/pip")
        volumes_list.append(f"{_SHARED_DEPS_DIR}/node_modules:{_SHARED_DEPS_DIR}/node_modules")

        # Ensure workspace subdir exists on host
        if work_dir:
            Path(work_dir, "workspace").mkdir(parents=True, exist_ok=True)

        # Build command
        if language == "python":
            cmd = ["python3", "-c", code]
        elif language == "bash":
            cmd = ["bash", "-c", code]
        elif language == "node":
            cmd = ["node", "-e", code]
        else:
            return ExecutionResult(
                success=False, stdout="", stderr="", exit_code=1,
                duration_ms=int((time.time() - start_time) * 1000),
                error=f"Unsupported language: {language}"
            )

        # Resource limits
        cpu_limit = self.config.cpu_limit
        memory_limit = self.config.memory_limit

        # Network: "none" disables networking, None uses default (bridge)
        network_mode = "none" if not self.config.allow_network else None

        container = None
        try:
            # Pull image if needed
            try:
                self.client.images.get(image)
            except Exception:
                self.client.images.pull(image)

            # Run container in detached mode so we get a Container object
            container = await asyncio.to_thread(
                self.client.containers.run,
                image,
                cmd,
                detach=True,
                mem_limit=memory_limit,
                cpu_period=100000,
                cpu_quota=int(float(cpu_limit) * 100000),
                network_mode=network_mode,
                environment=env,
                volumes=volumes_list,
                working_dir=container_cwd if work_dir else None,
                stdout=True,
                stderr=True,
            )

            # Wait for completion with timeout (run in thread to avoid blocking)
            result = await asyncio.to_thread(container.wait, timeout=timeout)
            exit_code = result.get("StatusCode", 1)

            # Retrieve stdout / stderr (full — truncation handled by _format_result)
            stdout_bytes = await asyncio.to_thread(
                container.logs, stdout=True, stderr=False
            )
            stderr_bytes = await asyncio.to_thread(
                container.logs, stdout=False, stderr=True
            )

            stdout_str = stdout_bytes.decode("utf-8", errors="replace")
            stderr_str = stderr_bytes.decode("utf-8", errors="replace")

            duration_ms = int((time.time() - start_time) * 1000)

            return ExecutionResult(
                success=exit_code == 0,
                stdout=stdout_str,
                stderr=stderr_str,
                exit_code=exit_code,
                duration_ms=duration_ms,
                error=None if exit_code == 0 else f"Exit code: {exit_code}"
            )

        except Exception as e:
            duration_ms = int((time.time() - start_time) * 1000)
            error_msg = str(e)
            logger.exception(f"[Docker] Execution error")

            if "timeout" in error_msg.lower():
                return ExecutionResult(
                    success=False, stdout="", stderr="", exit_code=124,
                    duration_ms=duration_ms,
                    error=f"Code execution timed out after {timeout}s"
                )

            return ExecutionResult(
                success=False, stdout="", stderr="", exit_code=1,
                duration_ms=duration_ms,
                error=f"Docker execution error: {error_msg[:200]}"
            )

        finally:
            # Always clean up the container
            if container is not None:
                try:
                    container.remove(force=True)
                except Exception:
                    pass
