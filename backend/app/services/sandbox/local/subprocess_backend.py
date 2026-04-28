"""Local subprocess-based sandbox backend."""

import asyncio
import os
import shutil
import time
from pathlib import Path

from loguru import logger

from app.services.sandbox.base import BaseSandboxBackend, ExecutionResult, SandboxCapabilities
from app.services.sandbox.config import SandboxConfig
from app.services.workspace_paths import WorkspacePathError, resolve_path_within_root


# Security patterns - reused from agent_tools.py
_DANGEROUS_BASH_ALWAYS = [
    "rm -rf /", "rm -rf ~", "sudo ", "mkfs", "dd if=",
    ":(){ :", "chmod 777 /", "chown ", "shutdown", "reboot",
    "python3 -c", "python -c",
]

_DANGEROUS_BASH_NETWORK = [
    "curl ", "wget ", "nc ", "ncat ", "ssh ", "scp ",
]

_DANGEROUS_PYTHON_IMPORTS_ALWAYS = [
    "subprocess", "shutil.rmtree", "os.system", "os.popen",
    "os.exec", "os.spawn",
]

_DANGEROUS_PYTHON_IMPORTS_NETWORK = [
    "socket", "http.client", "urllib.request", "requests",
    "ftplib", "smtplib", "telnetlib", "ctypes",
    "__import__", "importlib",
]

_DANGEROUS_NODE_ALWAYS = [
    "child_process", "fs.rmSync", "fs.rmdirSync", "process.exit",
]

_DANGEROUS_NODE_NETWORK = [
    "require('http')", "require('https')", "require('net')"
]


def _check_code_safety(language: str, code: str, allow_network: bool = False) -> str | None:
    """Check code for dangerous patterns. Returns error message if unsafe, None if ok."""
    code_lower = code.lower()

    if language == "bash":
        # Always check dangerous patterns
        for pattern in _DANGEROUS_BASH_ALWAYS:
            if pattern.lower() in code_lower:
                logger.warning(f"Blocked: dangerous command detected ({pattern.strip()})")
                return f"Blocked: dangerous command detected ({pattern.strip()})"
        # Network commands only when network is not allowed
        if not allow_network:
            for pattern in _DANGEROUS_BASH_NETWORK:
                if pattern.lower() in code_lower:
                    logger.warning(f"Blocked: network command not allowed ({pattern.strip()})")        
                    return f"Blocked: network command not allowed ({pattern.strip()})"
        if "../../" in code:
            return "Blocked: directory traversal not allowed"

    elif language == "python":
        # Always check dangerous patterns
        for pattern in _DANGEROUS_PYTHON_IMPORTS_ALWAYS:
            if pattern.lower() in code_lower:
                logger.warning(f"Blocked: unsafe operation detected ({pattern.strip()})")
                return f"Blocked: unsafe operation detected ({pattern.strip()})"
        # Network imports only when network is not allowed
        if not allow_network:
            for pattern in _DANGEROUS_PYTHON_IMPORTS_NETWORK:
                if pattern.lower() in code_lower:
                    logger.warning(f"Blocked: network operation not allowed ({pattern.strip()})")
                    return f"Blocked: network operation not allowed ({pattern.strip()})"

    elif language == "node":
        # Always check dangerous patterns
        for pattern in _DANGEROUS_NODE_ALWAYS:
            if pattern.lower() in code_lower:
                return f"Blocked: unsafe operation detected ({pattern})"
        # Network requires only when network is not allowed
        if not allow_network:
            for pattern in _DANGEROUS_NODE_NETWORK:
                if pattern.lower() in code_lower:
                    logger.warning(f"Blocked: network operation not allowed ({pattern.strip()})")
                    return f"Blocked: network operation not allowed ({pattern.strip()})"

    return None


class SubprocessBackend(BaseSandboxBackend):
    """Local subprocess-based sandbox backend.

    This backend executes code in a subprocess within the agent's workspace.
    It requires bubblewrap-based filesystem isolation for execute_code.
    When bubblewrap is unavailable, code execution fails closed.
    """

    name = "subprocess"
    _bwrap_missing_warned = False

    def __init__(self, config: SandboxConfig):
        self.config = config

    def _build_command(self, language: str, script_path: str) -> list[str]:
        if language == "python":
            return ["python3", "-I", "-B", str(script_path)]
        if language == "bash":
            return ["bash", "--noprofile", "--norc", str(script_path)]
        return ["node", str(script_path)]

    def _build_safe_env(self, work_path: Path) -> dict[str, str]:
        env = {
            "HOME": str(work_path),
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONNOUSERSITE": "1",
            "TMPDIR": str(work_path / ".tmp"),
            "NODE_PATH": "",
            "BASH_ENV": "",
            "ENV": "",
        }
        return env

    def _build_preexec_fn(self, work_path: Path, timeout: int):
        def _preexec():
            os.chdir(work_path)
            os.setsid()
            os.umask(0o077)

            try:
                import resource

                memory_bytes = int(self.config.memory_limit.rstrip("mM")) * 1024 * 1024
                cpu_limit = max(1, min(timeout, self.config.max_timeout, 60))
                resource.setrlimit(resource.RLIMIT_CPU, (cpu_limit, cpu_limit))
                resource.setrlimit(resource.RLIMIT_AS, (memory_bytes, memory_bytes))
                resource.setrlimit(resource.RLIMIT_FSIZE, (10 * 1024 * 1024, 10 * 1024 * 1024))
                resource.setrlimit(resource.RLIMIT_NOFILE, (64, 64))
                resource.setrlimit(resource.RLIMIT_NPROC, (32, 32))
                if hasattr(resource, "RLIMIT_CORE"):
                    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
            except Exception as exc:
                logger.warning(f"[Subprocess] Failed to apply resource limits: {exc}")

            if hasattr(os, "setgid"):
                try:
                    os.setgid(os.getgid())
                except Exception:
                    pass
            if hasattr(os, "setuid"):
                try:
                    os.setuid(os.getuid())
                except Exception:
                    pass

            if hasattr(os, "chroot") and os.geteuid() == 0:
                try:
                    os.chroot(work_path)
                    os.chdir("/")
                except Exception as exc:
                    logger.warning(f"[Subprocess] Failed to chroot into workspace: {exc}")

        return _preexec

    def _build_bwrap_command(self, command: list[str], work_path: Path) -> list[str] | None:
        bwrap = shutil.which("bwrap")
        if not bwrap:
            if not SubprocessBackend._bwrap_missing_warned:
                logger.warning(
                    "[Subprocess] bubblewrap (bwrap) is not available. "
                    "execute_code will be rejected until bubblewrap is installed."
                )
                SubprocessBackend._bwrap_missing_warned = True
            return None

        cmd = [
            bwrap,
            "--die-with-parent",
            "--new-session",
            "--unshare-user",
            "--unshare-ipc",
            "--unshare-pid",
            "--unshare-uts",
            "--unshare-cgroup",
            "--ro-bind", "/usr", "/usr",
            "--ro-bind", "/bin", "/bin",
            "--ro-bind", "/lib", "/lib",
            "--ro-bind", "/lib64", "/lib64",
            "--ro-bind", "/etc", "/etc",
            "--bind", str(work_path), "/workspace",
            "--dev", "/dev",
            "--proc", "/proc",
            "--dir", "/tmp",
            "--setenv", "HOME", "/workspace",
            "--setenv", "TMPDIR", "/workspace/.tmp",
            "--setenv", "PYTHONDONTWRITEBYTECODE", "1",
            "--setenv", "PYTHONNOUSERSITE", "1",
            "--setenv", "NODE_PATH", "",
            "--setenv", "BASH_ENV", "",
            "--setenv", "ENV", "",
            "--chdir", "/workspace",
        ]
        if not self.config.allow_network:
            cmd.append("--unshare-net")
        cmd.extend(command)
        return cmd

    def get_capabilities(self) -> SandboxCapabilities:
        return SandboxCapabilities(
            supported_languages=["python", "bash", "node"],
            max_timeout=self.config.max_timeout,
            max_memory_mb=256,
            network_available=self.config.allow_network,
            filesystem_available=True,
        )

    async def health_check(self) -> bool:
        """Check if basic system commands are available."""
        try:
            proc = await asyncio.create_subprocess_exec(
                "python3", "--version",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await proc.communicate()
            return proc.returncode == 0
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
        """Execute code in a subprocess."""
        start_time = time.time()

        # Validate language
        if language not in ("python", "bash", "node"):
            return ExecutionResult(
                success=False,
                stdout="",
                stderr="",
                exit_code=1,
                duration_ms=int((time.time() - start_time) * 1000),
                error=f"Unsupported language: {language}. Use: python, bash, or node"
            )

        # Security check - pass allow_network config
        safety_error = _check_code_safety(language, code, self.config.allow_network)
        if safety_error:
            return ExecutionResult(
                success=False,
                stdout="",
                stderr="",
                exit_code=1,
                duration_ms=int((time.time() - start_time) * 1000),
                error=f"❌ {safety_error}"
            )

        # Determine work directory and ensure it cannot escape its own root.
        if work_dir:
            work_path = Path(work_dir).resolve()
        else:
            work_path = (Path.cwd() / "workspace").resolve()
        try:
            work_path = resolve_path_within_root(work_path, "", label="work_dir")
        except WorkspacePathError as exc:
            return ExecutionResult(
                success=False,
                stdout="",
                stderr="",
                exit_code=1,
                duration_ms=int((time.time() - start_time) * 1000),
                error=str(exc),
            )
        work_path.mkdir(parents=True, exist_ok=True)
        (work_path / ".tmp").mkdir(parents=True, exist_ok=True)

        # Determine command and file extension
        if language == "python":
            ext = ".py"
        elif language == "bash":
            ext = ".sh"
        elif language == "node":
            ext = ".js"
        
        # Write code to temp file
        script_path = work_path / f"_exec_tmp{ext}"

        try:
            script_path.write_text(code, encoding="utf-8")

            sandbox_command = self._build_command(language, f"/workspace/{script_path.name}")
            bwrap_command = self._build_bwrap_command(sandbox_command, work_path)
            if not bwrap_command:
                duration_ms = int((time.time() - start_time) * 1000)
                return ExecutionResult(
                    success=False,
                    stdout="",
                    stderr="",
                    exit_code=1,
                    duration_ms=duration_ms,
                    error=(
                        "bubblewrap (bwrap) is required for execute_code but is not available. "
                        "Install bwrap in the runtime environment and restart the backend."
                    ),
                )

            safe_env = self._build_safe_env(work_path)

            kwargs = {
                "stdout": asyncio.subprocess.PIPE,
                "stderr": asyncio.subprocess.PIPE,
                "env": safe_env,
            }

            proc = await asyncio.create_subprocess_exec(
                *bwrap_command,
                cwd=str(work_path),
                **kwargs,
            )

            try:
                stdout, stderr = await asyncio.wait_for(
                    proc.communicate(),
                    timeout=timeout
                )
            except asyncio.TimeoutError:
                proc.kill()
                await proc.communicate()
                return ExecutionResult(
                    success=False,
                    stdout="",
                    stderr="",
                    exit_code=124,
                    duration_ms=int((time.time() - start_time) * 1000),
                    error=f"Code execution timed out after {timeout}s"
                )

            stdout_str = stdout.decode("utf-8", errors="replace")[:10000]
            stderr_str = stderr.decode("utf-8", errors="replace")[:5000]

            duration_ms = int((time.time() - start_time) * 1000)

            return ExecutionResult(
                success=proc.returncode == 0,
                stdout=stdout_str,
                stderr=stderr_str,
                exit_code=proc.returncode,
                duration_ms=duration_ms,
                error=None if proc.returncode == 0 else f"Exit code: {proc.returncode}"
            )

        except Exception as e:
            duration_ms = int((time.time() - start_time) * 1000)
            logger.exception(f"[Subprocess] Execution error")
            return ExecutionResult(
                success=False,
                stdout="",
                stderr="",
                exit_code=1,
                duration_ms=duration_ms,
                error=f"Execution error: {str(e)[:200]}"
            )

        finally:
            # Clean up temp script
            try:
                script_path.unlink(missing_ok=True)
            except Exception:
                pass
