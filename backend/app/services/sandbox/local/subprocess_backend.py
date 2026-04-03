"""Local subprocess-based sandbox backend."""

import asyncio
from loguru import logger
import os
import sys
import time
import re
import platform
from pathlib import Path
from typing import Optional

from app.services.sandbox.base import BaseSandboxBackend, ExecutionResult, SandboxCapabilities
from app.services.sandbox.config import SandboxConfig


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

# Default pip black list - packages that can escape sandbox
_DEFAULT_PIP_BLACK_LIST = [
    "subprocess", "ptyprocess", "pexpect", "pwntools",
    "capstone", "keystone-engine", "unicorn",
]


def _extract_pip_packages(code: str) -> list[str]:
    """Extract package names from pip install command.

    Examples:
        pip install numpy pandas -> ["numpy", "pandas"]
        pip install numpy==1.20.0 -> ["numpy"]
        pip install -U numpy -> ["numpy"]
        pip install -r requirements.txt -> []
    """
    packages = []

    # Find all pip install commands
    # Match the entire pip install line and extract everything after "install"
    pip_patterns = [
        r"pip\s+install\s+(.+?)(?:;|&&|\|\||$)",
        r"pip3\s+install\s+(.+?)(?:;|&&|\|\||$)",
        r"python\s+-m\s+pip\s+install\s+(.+?)(?:;|&&|\|\||$)",
    ]

    # Flags that take no argument
    no_arg_flags = {"-U", "--upgrade", "-q", "--quiet", "--no-cache-dir",
                    "--force-reinstall", "-I", "--ignore-installed", "--user"}

    # Flags that take an argument (skip next token)
    arg_flags = {"-r", "--requirement", "-i", "--index-url", "--extra-index-url",
                 "-f", "--find-links", "--trusted-host", "--proxy", "--constraint",
                 "-c", "--config-settings"}

    for pattern in pip_patterns:
        for match in re.finditer(pattern, code, re.IGNORECASE):
            install_args = match.group(1).strip()

            # Split by spaces and process each argument
            args = install_args.split()
            skip_next = False

            for arg in args:
                if skip_next:
                    skip_next = False
                    continue

                # Skip flags that take an argument (and mark to skip next)
                if arg in arg_flags:
                    skip_next = True
                    continue

                # Skip flags that take no argument
                if arg in no_arg_flags:
                    continue

                # Skip if it starts with -- (other flags)
                if arg.startswith("--"):
                    continue

                # Skip if it starts with - and has no value (short flags like -v, -h)
                if arg.startswith("-") and len(arg) <= 2:
                    continue

                # Extract package name (remove version specifier)
                pkg = arg.split("==")[0].split(">=")[0].split("<=")[0].split("~=")[0].split("[")[0].strip()

                if pkg and pkg not in packages:
                    packages.append(pkg)

    return packages


def _check_pip_packages(packages: list[str], black_list: list[str]) -> Optional[str]:
    """Check if pip packages are in black list."""
    for pkg in packages:
        pkg_lower = pkg.lower()
        for blocked in black_list:
            if blocked.lower() in pkg_lower or pkg_lower in blocked.lower():
                return f"Blocked: package '{pkg}' is in black list (potential sandbox escape)"
    return None


def _check_code_safety(
    language: str,
    code: str,
    allow_network: bool = False,
    allow_pip_install: bool = False,
    pip_black_list: list[str] | None = None,
) -> str | None:
    """Check code for dangerous patterns. Returns error message if unsafe, None if ok."""
    code_lower = code.lower()
    pip_black_list = pip_black_list or _DEFAULT_PIP_BLACK_LIST

    if language == "bash":
        # Check pip install packages if present
        if "pip install" in code_lower or "pip3 install" in code_lower:
            if not allow_pip_install:
                return "Blocked: pip install is not allowed"
            packages = _extract_pip_packages(code)
            pkg_error = _check_pip_packages(packages, pip_black_list)
            if pkg_error:
                return pkg_error

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


# Global shared venv path cache
_shared_venv_cache: dict[str, Path] = {}

# Pre-installed packages for office/data processing
_PREINSTALLED_PACKAGES = [
    # Excel/CSV processing
    "pandas>=2.0.0",
    "openpyxl>=3.1.0",
    "xlsxwriter>=3.1.0",
    "xlrd>=2.0.0",
    # PDF processing
    "PyPDF2>=3.0.0",
    "pdfplumber>=0.10.0",
    "reportlab>=4.0.0",
    # PPT/DOCX processing
    "python-pptx>=0.6.21",
    "python-docx>=1.1.0",
    # Data analysis
    "numpy>=1.24.0",
    "scipy>=1.11.0",
    # Charting/Visualization
    "matplotlib>=3.7.0",
    "seaborn>=0.13.0",
    "plotly>=5.18.0",
    # HTTP/Network
    "requests>=2.31.0",
    "aiohttp>=3.9.0",
    "httpx>=0.25.0",
    # Archive/Compression
    "rarfile>=4.0",
    "py7zr>=0.20.0",
    # Image processing
    "Pillow>=10.0.0",
    # JSON/YAML/CSV extras
    "pyyaml>=6.0",
    # Date/time
    "python-dateutil>=2.8.0",
]


async def _ensure_shared_venv(base_dir: Path, pip_index_url: str = "https://pypi.tuna.tsinghua.edu.cn/simple") -> Path:
    """Ensure shared virtual environment exists with pre-installed packages.

    Args:
        base_dir: Base directory for agents (AGENT_DATA_DIR)
        pip_index_url: Pip index URL for package installation

    Returns:
        Path to the shared virtual environment
    """
    cache_key = str(base_dir)

    if cache_key in _shared_venv_cache:
        venv_path = _shared_venv_cache[cache_key]
        if venv_path.exists():
            return venv_path

    # Create shared venv directory
    venv_path = base_dir / "_shared_venv"
    packages_marker = venv_path / ".packages_installed"

    if not venv_path.exists():
        logger.info(f"[SharedVenv] Creating shared virtual environment at {venv_path}")
        proc = await asyncio.create_subprocess_exec(
            sys.executable, "-m", "venv", str(venv_path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            logger.error(f"[SharedVenv] Failed to create venv: {stderr.decode()}")
            raise RuntimeError(f"Failed to create shared venv: {stderr.decode()}")
        logger.info(f"[SharedVenv] Shared venv created successfully")

    # Install pre-installed packages if not already done
    if not packages_marker.exists():
        venv_python = _get_venv_python(venv_path)
        if venv_python.exists():
            logger.info(f"[SharedVenv] Installing pre-installed packages ({len(_PREINSTALLED_PACKAGES)} packages)...")

            # Parse trusted host from index URL
            from urllib.parse import urlparse
            parsed = urlparse(pip_index_url)
            trusted_host = parsed.netloc

            # Install packages in batches to avoid command line length limits
            batch_size = 10
            for i in range(0, len(_PREINSTALLED_PACKAGES), batch_size):
                batch = _PREINSTALLED_PACKAGES[i:i + batch_size]
                pkg_str = " ".join(batch)

                install_cmd = f'"{str(venv_python)}" -m pip install --quiet -i {pip_index_url} --trusted-host {trusted_host} {pkg_str}'
                logger.info(f"[SharedVenv] Installing batch {i//batch_size + 1}: {[p.split(">=")[0].split("[")[0] for p in batch]}")

                proc = await asyncio.create_subprocess_shell(
                    install_cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                stdout, stderr = await proc.communicate()
                if proc.returncode != 0:
                    logger.warning(f"[SharedVenv] Some packages may have failed to install: {stderr.decode()[:200]}")

            # Mark packages as installed
            packages_marker.write_text("\n".join(_PREINSTALLED_PACKAGES), encoding="utf-8")
            logger.info(f"[SharedVenv] Pre-installed packages installed successfully")

    _shared_venv_cache[cache_key] = venv_path
    return venv_path


def _get_venv_python(venv_path: Path) -> Path:
    """Get Python executable path from venv."""
    if platform.system() == "Windows":
        return venv_path / "Scripts" / "python.exe"
    else:
        return venv_path / "bin" / "python"


def _inject_pip_index_url(code: str, index_url: str) -> str:
    """Inject pip index URL into pip install commands.

    Args:
        code: Original code
        index_url: Pip index URL (mirror)

    Returns:
        Modified code with index URL injected
    """
    if not index_url or "pip install" not in code.lower():
        return code

    # Extract trusted host from index URL
    from urllib.parse import urlparse
    parsed = urlparse(index_url)
    trusted_host = parsed.netloc

    # Pattern to find pip install commands that don't already have -i or --index-url
    # We need to inject -i and --trusted-host after "pip install"
    def add_index(match):
        full_match = match.group(0)
        # Check if already has index url specified
        if " -i " in full_match or " --index-url " in full_match:
            return full_match

        # Find the position right after "install"
        install_pos = full_match.lower().find("install") + len("install")
        prefix = full_match[:install_pos]
        suffix = full_match[install_pos:]

        # Inject index URL and trusted host
        injection = f" -i {index_url} --trusted-host {trusted_host}"
        return prefix + injection + suffix

    # Match pip install commands (handle various forms)
    patterns = [
        r"pip\s+install\s+[^\s].*?(?=;|&&|\|\||$|\n)",
        r"pip3\s+install\s+[^\s].*?(?=;|&&|\|\||$|\n)",
        r"python\s+-m\s+pip\s+install\s+[^\s].*?(?=;|&&|\|\||$|\n)",
    ]

    modified_code = code
    for pattern in patterns:
        modified_code = re.sub(pattern, add_index, modified_code, flags=re.IGNORECASE)

    return modified_code


class SubprocessBackend(BaseSandboxBackend):
    """Local subprocess-based sandbox backend.

    This backend executes code in a subprocess within the agent's workspace.
    It provides basic security checks but no process isolation.

    Features:
    - Shared virtual environment for all agents (persistent pip packages)
    - Extended timeout support for long-running tasks
    - Configurable pip install with black list
    """

    name = "subprocess"

    def __init__(self, config: SandboxConfig, agent_data_dir: str | None = None):
        self.config = config
        self.agent_data_dir = agent_data_dir

    def get_capabilities(self) -> SandboxCapabilities:
        return SandboxCapabilities(
            supported_languages=["python", "bash", "node"],
            max_timeout=self.config.long_task_timeout,
            max_memory_mb=512,
            network_available=self.config.allow_network,
            filesystem_available=True,
        )

    async def health_check(self) -> bool:
        """Check if basic system commands are available."""
        try:
            proc = await asyncio.create_subprocess_exec(
                sys.executable, "--version",
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
        use_venv: bool = True,
        mode: str = "normal",
        **kwargs
    ) -> ExecutionResult:
        """Execute code in a subprocess.

        Args:
            code: Code to execute
            language: Language (python, bash, node)
            timeout: Execution timeout in seconds
            work_dir: Working directory
            use_venv: Whether to use shared virtual environment for Python
            mode: Execution mode (normal, extended, long_task)
        """
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

        # Determine max timeout based on mode
        max_timeouts = {
            "normal": self.config.max_timeout,
            "extended": self.config.extended_timeout,
            "long_task": self.config.long_task_timeout,
        }
        max_timeout = max_timeouts.get(mode, self.config.max_timeout)
        timeout = min(timeout, max_timeout)

        # Security check with pip install support
        safety_error = _check_code_safety(
            language,
            code,
            allow_network=self.config.allow_network,
            allow_pip_install=self.config.allow_pip_install,
            pip_black_list=self.config.pip_black_list,
        )
        if safety_error:
            return ExecutionResult(
                success=False,
                stdout="",
                stderr="",
                exit_code=1,
                duration_ms=int((time.time() - start_time) * 1000),
                error=f"❌ {safety_error}"
            )

        # Inject pip index URL (mirror) for faster downloads in China
        if self.config.pip_index_url and language == "bash":
            code = _inject_pip_index_url(code, self.config.pip_index_url)

        # Determine work directory
        if work_dir:
            work_path = Path(work_dir)
        else:
            work_path = Path.cwd() / "workspace"
        work_path.mkdir(parents=True, exist_ok=True)

        # Determine command and file extension
        cmd_prefix = []

        if language == "python":
            ext = ".py"
            # Use shared venv for Python if enabled
            if use_venv and self.config.allow_pip_install and self.agent_data_dir:
                try:
                    base_dir = Path(self.agent_data_dir)
                    venv_path = await _ensure_shared_venv(base_dir, self.config.pip_index_url)
                    venv_python = _get_venv_python(venv_path)
                    if venv_python.exists():
                        cmd_prefix = [str(venv_python)]
                        logger.debug(f"[Subprocess] Using shared venv Python: {venv_python}")
                except Exception as e:
                    logger.warning(f"[Subprocess] Failed to use shared venv, falling back: {e}")

            if not cmd_prefix:
                cmd_prefix = [sys.executable]

        elif language == "bash":
            ext = ".sh"
            # On Windows, find Git Bash or WSL bash
            if platform.system() == "Windows":
                git_bash = Path("C:/Program Files/Git/usr/bin/bash.exe")
                if git_bash.exists():
                    cmd_prefix = [str(git_bash)]
                else:
                    cmd_prefix = ["bash"]
            else:
                cmd_prefix = ["bash"]

        elif language == "node":
            ext = ".js"
            cmd_prefix = ["node"]

        # Write code to temp file
        script_path = work_path / f"_exec_tmp{ext}"

        try:
            script_path.write_text(code, encoding="utf-8")

            # Set up safe environment
            safe_env = dict(os.environ)
            safe_env["HOME"] = str(work_path)
            safe_env["PYTHONDONTWRITEBYTECODE"] = "1"

            # Add shared venv to PATH if using Python
            if language == "python" and use_venv and self.agent_data_dir:
                base_dir = Path(self.agent_data_dir)
                venv_path = base_dir / "_shared_venv"
                if platform.system() == "Windows":
                    venv_bin = venv_path / "Scripts"
                else:
                    venv_bin = venv_path / "bin"
                if venv_bin.exists():
                    safe_env["PATH"] = str(venv_bin) + os.pathsep + safe_env.get("PATH", "")
                    safe_env["VIRTUAL_ENV"] = str(venv_path)

            # Execute
            proc = await asyncio.create_subprocess_exec(
                *cmd_prefix, str(script_path),
                cwd=str(work_path),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=safe_env,
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