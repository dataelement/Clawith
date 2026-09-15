from __future__ import annotations

import os
import re
import shlex
import shutil
import signal
import stat
import subprocess
import time
from collections.abc import Callable
from contextlib import suppress
from pathlib import Path

import pytest
import yaml

from app.infrastructure.config import BACKEND_ROOT as SETTINGS_BACKEND_ROOT
from app.infrastructure.config import ENV_FILE_PATH

BACKEND_ROOT = Path(__file__).resolve().parents[2]
REPOSITORY_ROOT = BACKEND_ROOT.parent
CI_GATE_SCRIPT = REPOSITORY_ROOT / "scripts/ci-g002-gates.sh"
G001_REFERENCE_SCRIPT = REPOSITORY_ROOT / "scripts/check-g001-reference.sh"
SHARED_CI_COMMAND = "bash scripts/ci-g003-gates.sh"
SETUP = REPOSITORY_ROOT / "setup.sh"
RESTART = REPOSITORY_ROOT / "restart.sh"
BACKEND_ENV_EXAMPLE = BACKEND_ROOT / ".env.example"
ROOT_ENV_EXAMPLE = REPOSITORY_ROOT / ".env.example"
README = REPOSITORY_ROOT / "README.md"
TARGET_DATABASE = "clawith_target"
ACTIVE_DATABASE_CONFIGS = (
    BACKEND_ROOT / ".env.example",
    REPOSITORY_ROOT / "setup.sh",
    REPOSITORY_ROOT / "docker-compose.yml",
    REPOSITORY_ROOT / "docker-compose.ci.yml",
    REPOSITORY_ROOT / "docker-compose.cd.yml",
    REPOSITORY_ROOT / "deploy/.env.example",
    REPOSITORY_ROOT / "deploy/docker-compose.yml",
    REPOSITORY_ROOT / "deploy/docker-compose-multi.yml",
    REPOSITORY_ROOT / "helm/clawith/values.yaml",
)
COMPOSE_CONFIGS = (
    REPOSITORY_ROOT / "docker-compose.yml",
    REPOSITORY_ROOT / "docker-compose.ci.yml",
    REPOSITORY_ROOT / "docker-compose.cd.yml",
    REPOSITORY_ROOT / "deploy/docker-compose.yml",
    REPOSITORY_ROOT / "deploy/docker-compose-multi.yml",
)
LEGACY_CI_SCRIPTS = (
    REPOSITORY_ROOT / ".github/scripts/ci_deploy_test.sh",
    REPOSITORY_ROOT / ".github/scripts/ci_migration_test.sh",
    REPOSITORY_ROOT / ".github/scripts/ci_upgrade_test.sh",
)
OPERATOR_DOCS = (
    REPOSITORY_ROOT / "README.md",
    REPOSITORY_ROOT / "README_zh-CN.md",
    REPOSITORY_ROOT / "README_ar.md",
    REPOSITORY_ROOT / "README_es.md",
    REPOSITORY_ROOT / "README_ja.md",
    REPOSITORY_ROOT / "README_ko.md",
    REPOSITORY_ROOT / "CONTRIBUTING.md",
    REPOSITORY_ROOT / "backend/ALEMBIC_GUIDELINES.md",
    REPOSITORY_ROOT / "helm/clawith/README.md",
    REPOSITORY_ROOT / "helm/QUICKSTART.md",
    REPOSITORY_ROOT / "helm/QUICKSTART_EN.md",
    REPOSITORY_ROOT / "deploy/RELEASE_DEPLOYMENT.md",
)


class StartupContractError(RuntimeError):
    pass


SETUP_ALLOWED_EXECUTABLE_EXPANSIONS = {
    'ROOT="$(cd "$(dirname "$0")" && pwd)"',
    'TEMP_ENV="$(mktemp "$BACKEND_DIR/.env.tmp.XXXXXX")"',
    'existing="$(grep -m 1 "^${key}=" "$BACKEND_ENV" || true)"',
    'existing_database_url_line="$(grep -m 1 \'^DATABASE_URL=\' "$BACKEND_ENV" || true)"',
}

RESTART_ALLOWED_EXECUTABLE_EXPANSIONS = {
    'ROOT="$(cd "$(dirname "$0")" && pwd)"',
    'evidence_pid="$(sed -n \'s/^pid=//p\' "$PROCESS_FILE")"',
    'evidence_start="$(sed -n \'s/^start=//p\' "$PROCESS_FILE")"',
    'evidence_startup_id="$(sed -n \'s/^startup_id=//p\' "$PROCESS_FILE")"',
    '[ "$(process_start_identity "$owned_pid")" = "$owned_start" ] || return 1',
    'command_line="$(process_command "$owned_pid")"',
    'for _ in $(seq 1 "$STOP_ATTEMPTS"); do',
    'command_line="$(process_command "$pending_pid")"',
    'startup_id="$("$PYTHON_BIN" -c \'import secrets; print(secrets.token_hex(16))\')"',
    'backend_start="$(process_start_identity "$backend_pid")"',
    'TEMP_PROCESS_FILE="$(mktemp "$STATE_DIR/backend.process.tmp.XXXXXX")"',
    'for _ in $(seq 1 "$HEALTH_ATTEMPTS"); do',
    'health_response="$(curl --fail --silent --max-time 1 "http://${BACKEND_HOST}:${BACKEND_PORT}/api/health" || true)"',
}


def _executable_expansion_lines(source: str) -> set[str]:
    return {
        line.strip()
        for line in source.replace("\\\n", " ").splitlines()
        if any(marker in line for marker in ("$(", "`", "<(", ">("))
    }


def _expanded_shell_segments(source: str) -> list[list[str]]:
    assignments: dict[str, str] = {}
    expanded_segments: list[list[str]] = []
    for line in source.replace("\\\n", " ").splitlines():
        lexer = shlex.shlex(line, posix=True, punctuation_chars="|&;<>")
        lexer.commenters = "#"
        lexer.whitespace_split = True
        try:
            tokens = list(lexer)
        except ValueError:
            continue
        segments: list[list[str]] = [[]]
        for token in tokens:
            if token in {";", "&&", "||", "|", "&"}:
                if segments[-1]:
                    segments.append([])
                continue
            segments[-1].append(token)
        for segment in segments:
            if not segment:
                continue
            expanded: list[str] = []
            for token in segment:
                value = token
                for name, assigned in assignments.items():
                    value = value.replace(f"${{{name}}}", assigned)
                    value = re.sub(rf"\${re.escape(name)}\b", assigned, value)
                expanded.append(value)
                match = re.fullmatch(
                    r"([A-Za-z_][A-Za-z0-9_]*)=(.*)",
                    value,
                    re.DOTALL,
                )
                if match:
                    assignments[match.group(1)] = match.group(2)
            expanded_segments.append(expanded)
    return expanded_segments


def _shell_execution_facts(source: str) -> set[str]:
    facts: set[str] = set()
    substitutions = re.findall(r"\$\(([^()]*)\)|`([^`]*)`", source)
    for dollar_substitution, backtick_substitution in substitutions:
        nested = dollar_substitution or backtick_substitution
        if nested:
            facts.update(_shell_execution_facts(nested))
    for expanded_tokens in _expanded_shell_segments(source):
        commands = [
            token
            for token in expanded_tokens
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*", token, re.DOTALL)
            and token not in {"!", "env", "exec", "export", "if", "then"}
        ]
        if not commands:
            continue
        command = Path(commands[0]).name
        normalized = " ".join(expanded_tokens)
        if command in {"echo", "printf"} and "$(" not in normalized and "`" not in normalized:
            continue
        if command == "alembic" or re.search(r"(?:^|\s)alembic(?:\s|$)", normalized):
            facts.add("alembic")
        if "app.scripts.setup_langgraph_checkpoints" in normalized:
            facts.add("checkpoint-installer")
        if "docker compose" in normalized or command in {"docker", "docker-compose"}:
            facts.add("docker")
        if any(
            name in normalized
            for name in (
                "ci_deploy_test",
                "ci_migration_test",
                "ci_upgrade_test",
            )
        ):
            facts.add("legacy-ci")
        if "pytest tests/architecture" in normalized:
            facts.add("architecture-gate")
        if "pytest --collect-only" in normalized:
            facts.add("collection-gate")
        if "ruff check app tests" in normalized:
            facts.add("ruff-gate")
        if "pyright app" in normalized:
            facts.add("pyright-gate")
    return facts


def _dynamic_shell_sink_commands(source: str) -> set[tuple[str, ...]]:
    sinks: set[tuple[str, ...]] = set()
    for tokens in _expanded_shell_segments(source):
        commands = [
            token
            for token in tokens
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*", token, re.DOTALL)
            and token not in {"if", "then"}
        ]
        if not commands:
            continue
        if any(
            (Path(token).name or token) in {"bash", "sh", "eval", "source", "."}
            for token in commands
        ):
            sinks.add(tuple(commands))
    return sinks


def _yaml_executable_commands(source: str) -> list[str]:
    parsed = yaml.safe_load(source)
    commands: list[str] = []

    def visit(value: object, *, executable: bool = False) -> None:
        if executable and isinstance(value, str):
            commands.append(value)
            return
        if isinstance(value, list):
            for item in value:
                visit(item, executable=executable)
            return
        if not isinstance(value, dict):
            return
        for raw_key, nested in value.items():
            visit(
                nested,
                executable=str(raw_key).casefold()
                in {"command", "commands", "run", "script"},
            )

    visit(parsed)
    return commands


REQUIRED_CUMULATIVE_GATE_COMMANDS = (
    "uv run python scripts/validate_goal_gates.py --manifest rewrite/goal-gates.json",
    "uv run python scripts/rewrite_inventory.py check --manifest rewrite/coverage.json --require-zero-unreviewed --require-zero-disposition-missing",
    "uv run --extra dev pytest tests/architecture/test_governance.py tests/architecture/test_module_boundaries.py",
    "uv run python scripts/check_owner_contracts.py check --manifest rewrite/owner-contracts.json",
    "uv run python scripts/validate_goal_gates.py --manifest rewrite/goal-gates.json --check-product-roster-and-linkage",
    "uv run python scripts/validate_load_profile.py tests/performance/profiles/backend_50.json",
    "bash ../scripts/check-g001-reference.sh",
    "uv run --extra dev pytest tests/architecture",
    "uv run --extra dev pytest",
    "uv run --extra dev pytest --collect-only",
    "uv run --extra dev ruff check app tests",
    "uv run --extra dev pyright app",
)

CUMULATIVE_CI_SCRIPT_PREAMBLE = (
    "set -euo pipefail",
    'repository_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"',
    'backend_root="$repository_root/backend"',
    'cd "$backend_root"',
    "uv lock --check",
    "uv sync --extra dev --frozen",
)

G001_REFERENCE_SCRIPT_LINES = (
    "set -euo pipefail",
    'repository_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"',
    'backend_root="$repository_root/backend"',
    'reference_temp_root="$(mktemp -d)"',
    'reference_worktree="$reference_temp_root/legacy-reference"',
    "cleanup() {",
    "original_status=$?",
    "worktree_remove_status=0",
    "temp_remove_status=0",
    "prune_status=0",
    "trap - EXIT INT TERM",
    'git -C "$repository_root" worktree remove --force "$reference_worktree" >/dev/null 2>&1 || worktree_remove_status=$?',
    'rm -rf "$reference_temp_root" || temp_remove_status=$?',
    'if [ "$worktree_remove_status" -ne 0 ]; then',
    'git -C "$repository_root" worktree prune || prune_status=$?',
    'if [ "$prune_status" -ne 0 ]; then',
    "prune_status=0",
    'git -C "$repository_root" worktree prune || prune_status=$?',
    "fi",
    'if [ "$prune_status" -eq 0 ]; then',
    "worktree_remove_status=0",
    "fi",
    "fi",
    'if [ "$original_status" -ne 0 ]; then',
    'exit "$original_status"',
    "fi",
    'if [ "$worktree_remove_status" -ne 0 ] || [ "$temp_remove_status" -ne 0 ] || [ "$prune_status" -ne 0 ]; then',
    "exit 1",
    "fi",
    "exit 0",
    "}",
    "trap cleanup EXIT INT TERM",
    "git -C \"$repository_root\" cat-file -e '8ed4ae2f^{commit}'",
    'git -C "$repository_root" worktree add --detach "$reference_worktree" 8ed4ae2f',
    'uv sync --project "$reference_worktree/backend" --extra dev',
    'reference_python="$reference_worktree/backend/.venv/bin/python"',
    'export CLAWITH_LEGACY_REFERENCE_AGENT_DATA_DIR="$reference_temp_root/persistence/legacy/agents"',
    'export CLAWITH_LEGACY_REFERENCE_DATABASE_URL="postgresql+asyncpg://legacy:legacy@127.0.0.1:5432/clawith_legacy_reference"',
    'export CLAWITH_LEGACY_REFERENCE_REDIS_URL="redis://127.0.0.1:6379/14"',
    'export CLAWITH_LEGACY_REFERENCE_S3_PREFIX="clawith-legacy-reference/"',
    'export CLAWITH_LEGACY_REFERENCE_STORAGE_LOCAL_ROOT="$reference_temp_root/persistence/legacy/storage"',
    'export CLAWITH_TARGET_AGENT_DATA_DIR="$reference_temp_root/persistence/target/agents"',
    'export CLAWITH_TARGET_DATABASE_URL="postgresql+asyncpg://target:target@127.0.0.1:5432/clawith_target"',
    'export CLAWITH_TARGET_REDIS_URL="redis://127.0.0.1:6379/15"',
    'export CLAWITH_TARGET_S3_PREFIX="clawith-target/"',
    'export CLAWITH_TARGET_STORAGE_LOCAL_ROOT="$reference_temp_root/persistence/target/storage"',
    'cd "$backend_root"',
    'uv run python scripts/rewrite_inventory.py check-reference --manifest rewrite/coverage.json --expected-head 8ed4ae2f --require-clean --boot-smoke --black-box-manifest rewrite/legacy-black-box.json --worktree "$reference_worktree" --python "$reference_python"',
)


def _command_tokens(line: str) -> tuple[str, ...]:
    lexer = shlex.shlex(line, posix=True, punctuation_chars="|&;<>")
    lexer.commenters = "#"
    lexer.whitespace_split = True
    return tuple(lexer)


def _validate_cumulative_ci_script(source: str) -> None:
    lines = source.splitlines()
    if not lines or lines[0] != "#!/bin/bash" or "set -euo pipefail" not in lines:
        raise StartupContractError("CI gate script lacks fail-closed Bash setup")
    actual = tuple(
        line.strip()
        for line in lines
        if line.strip() and not line.lstrip().startswith("#")
    )
    expected = CUMULATIVE_CI_SCRIPT_PREAMBLE + REQUIRED_CUMULATIVE_GATE_COMMANDS
    if actual != expected:
        raise StartupContractError("CI gate script does not execute the exact cumulative gates in order")


def _validate_g001_reference_script(source: str) -> None:
    lines = source.splitlines()
    if not lines or lines[0] != "#!/bin/bash":
        raise StartupContractError("G001 reference script lacks the Bash entrypoint")
    actual = tuple(
        line.strip()
        for line in lines
        if line.strip() and not line.lstrip().startswith("#")
    )
    if actual != G001_REFERENCE_SCRIPT_LINES:
        raise StartupContractError("G001 reference script is not the exact isolated check")


def _yaml_continue_on_error(value: object) -> bool:
    if isinstance(value, list):
        return any(_yaml_continue_on_error(item) for item in value)
    if not isinstance(value, dict):
        return False
    return any(
        (str(key).casefold() == "continue-on-error" and str(nested).casefold() == "true")
        or _yaml_continue_on_error(nested)
        for key, nested in value.items()
    )


def _validate_setup_source(source: str) -> None:
    required = (
        'BACKEND_ENV="$BACKEND_DIR/.env"',
        'BACKEND_ENV_EXAMPLE="$BACKEND_DIR/.env.example"',
        'TARGET_DATABASE="clawith_target"',
        'TARGET_ROLE="clawith_target"',
        "uv lock --check",
        "uv sync --extra dev --frozen",
        "uv sync --frozen",
    )
    missing = [value for value in required if value not in source]
    unsafe_substitutions = (
        _executable_expansion_lines(source)
        - SETUP_ALLOWED_EXECUTABLE_EXPANSIONS
    )
    dynamic_sinks = _dynamic_shell_sink_commands(source)
    forbidden = sorted(_shell_execution_facts(source))
    forbidden.extend(
        value
        for value in (
            "$ROOT/.env",
            "ALTER ROLE",
            "create_all",
            "seed.py",
            "AGENT_RUNTIME",
        )
        if value in source
    )
    if (
        missing
        or forbidden
        or unsafe_substitutions
        or dynamic_sinks
        or "/clawith?" in source
        or 'TARGET_ROLE="clawith"' in source
    ):
        raise StartupContractError(
            "invalid setup contract "
            f"missing={missing} forbidden={forbidden} substitutions={sorted(unsafe_substitutions)} "
            f"shell_sinks={sorted(dynamic_sinks)}"
        )


def _validate_restart_source(source: str) -> None:
    command = '"$UVICORN_BIN" app.main:app'
    required = (
        'BACKEND_ENV="$BACKEND_DIR/.env"',
        'UVICORN_BIN="$BACKEND_DIR/.venv/bin/uvicorn"',
        "--workers 1",
        "/api/health",
        "Missing backend/.env",
        "process_pid",
        "startup_id",
        'RESTART_LOCK="$STATE_DIR/backend.restart.lock"',
        'mkdir "$RESTART_LOCK"',
        "evidence_matches_pending",
        "backend.unsettled.*.process",
    )
    missing = [value for value in required if value not in source]
    unsafe_substitutions = (
        _executable_expansion_lines(source)
        - RESTART_ALLOWED_EXECUTABLE_EXPANSIONS
    )
    dynamic_sinks = _dynamic_shell_sink_commands(source)
    forbidden = sorted(_shell_execution_facts(source))
    forbidden.extend(
        value
        for value in (
            "$ROOT/.env",
            "create_all",
            "seed.py",
            "frontend",
            "npm",
            "vite",
            "PROCESS_ROLE",
            "AGENT_RUNTIME",
            "lsof",
            "fuser",
            "kill -9",
        )
        if value.casefold() in source.casefold()
    )
    if (
        missing
        or forbidden
        or unsafe_substitutions
        or dynamic_sinks
        or source.count(command) != 1
        or source.index("Missing backend/.env") > source.index(command)
        or source.index(command) > source.index("/api/health")
    ):
        raise StartupContractError(
            "invalid restart contract "
            f"missing={missing} forbidden={forbidden} "
            f"substitutions={sorted(unsafe_substitutions)} "
            f"shell_sinks={sorted(dynamic_sinks)}"
        )


def _write_executable(path: Path, source: str) -> None:
    path.write_text(source, encoding="utf-8")
    path.chmod(0o755)


def _read_process_pid(process_file: Path) -> int:
    fields = dict(
        line.split("=", 1)
        for line in process_file.read_text(encoding="utf-8").splitlines()
    )
    return int(fields["pid"])


def _wait_for_path(path: Path, *, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists():
            return
        time.sleep(0.02)
    raise AssertionError(f"timed out waiting for {path}")


def _process_exists(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def _restart_fixture(
    tmp_path: Path,
    *,
    uv_source: str,
    curl_source: str,
) -> tuple[Path, Path, dict[str, str]]:
    repository = tmp_path / "repo"
    backend = repository / "backend"
    backend_bin = backend / ".venv/bin"
    fake_bin = tmp_path / "bin"
    backend_bin.mkdir(parents=True)
    fake_bin.mkdir()
    restart = repository / "restart.sh"
    restart.write_text(RESTART.read_text(encoding="utf-8"), encoding="utf-8")
    (backend / ".env").write_text("DATABASE_URL=target\n", encoding="utf-8")
    _write_executable(backend_bin / "uvicorn", uv_source)
    _write_executable(
        backend_bin / "python",
        "#!/bin/sh\nprintf '0123456789abcdef0123456789abcdef\n'\n",
    )
    _write_executable(fake_bin / "curl", curl_source)
    environment = os.environ.copy()
    environment["PATH"] = f"{fake_bin}:{environment['PATH']}"
    environment["CLAWITH_STOP_ATTEMPTS"] = "20"
    return repository, fake_bin, environment


def _validate_compose_quarantine(source: str) -> None:
    config = yaml.safe_load(source)
    unguarded = [
        name
        for name, service in config["services"].items()
        if service.get("profiles") != ["deferred-product"]
    ]
    if unguarded:
        raise StartupContractError(f"Compose services are not deferred: {unguarded}")


def _validate_ci_gate_sources(
    drone: str,
    github: str,
    *,
    legacy_script_exists: bool,
    ci_script: str | None = None,
    reference_script: str | None = None,
) -> None:
    try:
        drone_config = yaml.load(drone, Loader=yaml.BaseLoader)
        github_config = yaml.load(github, Loader=yaml.BaseLoader)
        drone_commands = _yaml_executable_commands(drone)
        github_commands = _yaml_executable_commands(github)
    except yaml.YAMLError as exc:
        raise StartupContractError("CI configuration is invalid YAML") from exc
    source = ci_script if ci_script is not None else CI_GATE_SCRIPT.read_text(encoding="utf-8")
    reference_source = (
        reference_script
        if reference_script is not None
        else G001_REFERENCE_SCRIPT.read_text(encoding="utf-8")
    )
    try:
        _validate_cumulative_ci_script(source)
        _validate_g001_reference_script(reference_source)
    except StartupContractError as exc:
        raise StartupContractError("CI does not match the G002 gate-only contract") from exc
    drone_events = set(drone_config.get("trigger", {}).get("event", []))
    github_triggers = github_config.get("on", {})
    github_push_branches = github_triggers.get("push", {}).get("branches", [])
    github_steps = github_config.get("jobs", {}).get("backend-g002", {}).get("steps", [])
    checkout = next(
        (step for step in github_steps if step.get("uses") == "actions/checkout@v4"),
        {},
    )
    invalid = (
        legacy_script_exists
        or drone_commands != [SHARED_CI_COMMAND]
        or github_commands != [SHARED_CI_COMMAND]
        or drone_config.get("clone", {}).get("depth") != "0"
        or checkout.get("with", {}).get("fetch-depth") != "0"
        or _yaml_continue_on_error(drone_config)
        or _yaml_continue_on_error(github_config)
        or drone_events != {"pull_request", "push"}
        or set(github_triggers) != {"pull_request", "push", "workflow_dispatch"}
        or github_push_branches != ["develop"]
    )
    if invalid:
        raise StartupContractError("CI does not match the G002 gate-only contract")


def _validate_helm_template_quarantine(source: str) -> None:
    condition_stack: list[tuple[bool, bool]] = []
    directive = re.compile(r"^\{\{-?\s*(if|range|with)\s+(.+?)\s*\}\}$")

    def exact_deferred_guard(condition: str) -> bool:
        normalized = " ".join(condition.split())
        return bool(
            re.fullmatch(r"not \.Values\.g002Deferred", normalized)
            or re.fullmatch(
                r"and \(not \.Values\.g002Deferred\)(?: \.Values\.[A-Za-z0-9_.]+)+",
                normalized,
            )
        )

    for line_number, line in enumerate(source.splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped == "---" or stripped.startswith("#"):
            continue
        opened = directive.match(stripped)
        if opened:
            condition_stack.append(
                (
                    opened.group(1) == "if" and exact_deferred_guard(opened.group(2)),
                    False,
                )
            )
            continue
        if re.fullmatch(r"\{\{-?\s*else\s*\}\}", stripped):
            if not condition_stack:
                raise StartupContractError("Helm template has an unmatched else")
            is_guard, in_else = condition_stack[-1]
            condition_stack[-1] = (is_guard, not in_else)
            continue
        if re.fullmatch(r"\{\{-?\s*end\s*\}\}", stripped):
            if not condition_stack:
                raise StartupContractError("Helm template has an unmatched end")
            condition_stack.pop()
            continue
        if re.search(r"\{\{-?\s*(?:else|end|if|range|with)\b", stripped):
            raise StartupContractError("Helm control directive must occupy one line")
        if stripped.startswith("{{-") and re.fullmatch(
                r"\{\{-\s*(?:include|template|toYaml)\b.+?-?\}\}",
                stripped,
            ) is None:
            raise StartupContractError("Helm template syntax is not recognized")
        if not any(
            is_guard and not in_else for is_guard, in_else in condition_stack
        ):
            raise StartupContractError(
                f"Helm resource content is not quarantined at line {line_number}"
            )
    if condition_stack:
        raise StartupContractError("Helm template has an unclosed control block")


def _markdown_fenced_commands(source: str) -> str:
    commands: list[str] = []
    current: list[str] | None = None
    fence_character: str | None = None
    fence_length = 0
    for line in source.splitlines():
        opening = re.match(r"^ {0,3}(`{3,}|~{3,})(?:[^`~]*)$", line)
        if current is None and opening:
            marker = opening.group(1)
            fence_character = marker[0]
            fence_length = len(marker)
            current = []
            continue
        if current is not None and fence_character is not None:
            closing = re.fullmatch(
                rf" {{0,3}}{re.escape(fence_character)}{{{fence_length},}}\s*",
                line,
            )
            if closing:
                commands.append("\n".join(current))
                current = None
                fence_character = None
                fence_length = 0
                continue
            current.append(line)
            continue
    if current is not None:
        raise StartupContractError("operator document has an unclosed code fence")
    return "\n".join(commands)


def _validate_operator_document(source: str) -> None:
    if "G002" not in source or "health-only" not in source:
        raise StartupContractError("operator document lacks the G002 health-only boundary")
    legacy_database = re.compile(
        r"postgresql\+asyncpg://[^\s`]+/clawith(?:[?\"'`\s]|$)"
    )
    if legacy_database.search(source):
        raise StartupContractError("operator document references the legacy database")
    fenced = _markdown_fenced_commands(source)
    unsafe_substitutions = _executable_expansion_lines(fenced)
    if unsafe_substitutions:
        raise StartupContractError(
            "operator document contains an unapproved executable shell expansion"
        )
    facts = _shell_execution_facts(fenced)
    allowed_shell_commands = {("bash", "setup.sh"), ("bash", "restart.sh")}
    unsafe_shell_sinks = _dynamic_shell_sink_commands(fenced) - allowed_shell_commands
    for tokens in _expanded_shell_segments(fenced):
        if not tokens:
            continue
        command = Path(tokens[0]).name
        if command == "helm" and len(tokens) > 1 and tokens[1] in {"install", "upgrade"}:
            facts.add("helm-product")
        if command in {"npm", "vite"}:
            facts.add("frontend-product")
        if command == "cp" and tokens[1:] == [".env.example", ".env"]:
            facts.add("root-dotenv")
        if command == "psql" and any(
            tokens[index : index + 2] == ["-d", "clawith"]
            for index in range(len(tokens) - 1)
        ):
            facts.add("legacy-database")
    forbidden = facts & {
        "alembic",
        "checkpoint-installer",
        "docker",
        "frontend-product",
        "helm-product",
        "legacy-database",
        "root-dotenv",
    }
    if forbidden or unsafe_shell_sinks:
        raise StartupContractError(
            "operator document contains executable legacy instructions: "
            f"facts={sorted(forbidden)} shell_sinks={sorted(unsafe_shell_sinks)}"
        )


def _inject_drone_commands(source: str, commands: list[str]) -> str:
    config = yaml.safe_load(source)
    config["steps"][0]["commands"] = [
        *commands,
        *config["steps"][0]["commands"],
    ]
    return yaml.safe_dump(config, sort_keys=False)


def test_target_settings_owns_only_backend_dotenv() -> None:
    assert SETTINGS_BACKEND_ROOT == BACKEND_ROOT
    assert ENV_FILE_PATH == BACKEND_ROOT / ".env"


def test_backend_environment_template_is_target_only() -> None:
    assignments = {
        line.split("=", 1)[0]: line.split("=", 1)[1]
        for line in BACKEND_ENV_EXAMPLE.read_text(encoding="utf-8").splitlines()
        if line and not line.startswith("#")
    }
    assert set(assignments) == {
        "APP_NAME",
        "DEBUG",
        "DATABASE_URL",
        "CONTROL_DATABASE_POOL_SIZE",
        "EXECUTION_DATABASE_POOL_SIZE",
        "DATABASE_POOL_MAX_OVERFLOW",
    }
    assert assignments["DATABASE_URL"].endswith(
        f"/{TARGET_DATABASE}?ssl=disable"
    )
    assert assignments["DATABASE_URL"].startswith(
        "postgresql+asyncpg://clawith_target:clawith_target@"
    )
    assert "DATABASE_URL=" not in ROOT_ENV_EXAMPLE.read_text(encoding="utf-8")


def test_setup_and_restart_match_health_only_contract() -> None:
    _validate_setup_source(SETUP.read_text(encoding="utf-8"))
    _validate_restart_source(RESTART.read_text(encoding="utf-8"))
    assert SETUP.stat().st_mode & stat.S_IXUSR
    assert RESTART.stat().st_mode & stat.S_IXUSR


@pytest.mark.parametrize(
    "forbidden",
    [
        'cp "$ROOT/.env.example" "$ROOT/.env"',
        "uv run alembic upgrade head",
        "python -m app.scripts.setup_langgraph_checkpoints",
        "python backend/seed.py",
        "docker compose up -d",
        "OUT=$(alembic upgrade head)",
        "readonly OUT=$(alembic $(printf upgrade) head)",
        "cat <(alembic upgrade head)",
        "cat >(alembic upgrade head)",
        "OUT=$(alembic $(printf upgrade) head)",
        'OUT="$(alembic $(printf upgrade) head)"',
        "printf 'alembic upgrade head\\n' | bash",
        "eval 'alembic upgrade head'",
        "source /tmp/legacy-setup.sh",
        ". /tmp/legacy-setup.sh",
        "printf payload | command bash",
        "printf payload | /usr/bin/env bash",
        "printf payload | nice bash",
        "printf payload | xargs bash",
        "DATABASE_URL=postgresql+asyncpg://clawith:clawith@localhost:5432/clawith?ssl=disable",
    ],
)
def test_setup_contract_rejects_legacy_behavior(forbidden: str) -> None:
    with pytest.raises(StartupContractError):
        _validate_setup_source(SETUP.read_text(encoding="utf-8") + forbidden)


@pytest.mark.parametrize(
    "forbidden",
    [
        "uv run alembic upgrade head",
        "python -m app.scripts.setup_langgraph_checkpoints",
        "docker compose up -d",
        "npm run dev",
        "AGENT_RUNTIME_V2_ENABLED=true",
        "kill -9 123",
        "OUT=$(alembic $(printf upgrade) head)",
        "readonly OUT=$(alembic $(printf upgrade) head)",
        "printf 'alembic upgrade head\\n' | bash",
        "eval 'alembic upgrade head'",
        "source /tmp/legacy-restart.sh",
        ". /tmp/legacy-restart.sh",
        "printf payload | command bash",
        "printf payload | /usr/bin/env bash",
        "printf payload | nice bash",
        "printf payload | xargs bash",
        "cat <(alembic upgrade head)",
        "cat >(alembic upgrade head)",
    ],
)
def test_restart_contract_rejects_non_health_startup(forbidden: str) -> None:
    with pytest.raises(StartupContractError):
        _validate_restart_source(RESTART.read_text(encoding="utf-8") + forbidden)


@pytest.mark.parametrize(
    "split_command",
    [
        'MIG=alem\nMIG="${MIG}bic"\n"$MIG" upgrade head\n',
        (
            "MODULE=app.scripts.setup_langgraph_\n"
            'MODULE="${MODULE}checkpoints"\n'
            'python -m "$MODULE"\n'
        ),
    ],
)
def test_startup_contract_rejects_split_token_migration_commands(
    split_command: str,
) -> None:
    with pytest.raises(StartupContractError):
        _validate_setup_source(SETUP.read_text(encoding="utf-8") + split_command)
    with pytest.raises(StartupContractError):
        _validate_restart_source(RESTART.read_text(encoding="utf-8") + split_command)


@pytest.mark.parametrize(
    "forbidden_segment",
    [
        "uv run alembic upgrade head",
        "python -m app.scripts.setup_langgraph_checkpoints",
    ],
)
def test_startup_contract_checks_commands_after_inert_echo(
    forbidden_segment: str,
) -> None:
    bypass = f"\necho safe; {forbidden_segment}\n"
    with pytest.raises(StartupContractError):
        _validate_setup_source(SETUP.read_text(encoding="utf-8") + bypass)
    with pytest.raises(StartupContractError):
        _validate_restart_source(RESTART.read_text(encoding="utf-8") + bypass)


@pytest.mark.parametrize("lock_current", [True, False], ids=["current-lock", "stale-lock"])
def test_setup_synchronizes_backend_env_and_prepares_target_database(
    tmp_path: Path,
    lock_current: bool,
) -> None:
    repository = tmp_path / "repo"
    backend = repository / "backend"
    backend_bin = backend / ".venv/bin"
    fake_bin = tmp_path / "bin"
    backend_bin.mkdir(parents=True)
    fake_bin.mkdir()
    (repository / "setup.sh").write_text(SETUP.read_text(encoding="utf-8"), encoding="utf-8")
    (backend / ".env.example").write_text(
        BACKEND_ENV_EXAMPLE.read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    original_backend_env = (
        "DEBUG=true\nLEGACY_RUNTIME=true\n"
        "DATABASE_URL=postgresql+asyncpg://clawith_target:clawith_target@"
        "localhost:5432/clawith_target?ssl=disable\n"
    )
    (backend / ".env").write_text(original_backend_env, encoding="utf-8")
    command_log = tmp_path / "commands.log"
    _write_executable(
        fake_bin / "psql",
        '#!/bin/sh\nprintf "psql %s\\n" "$*" >> "$COMMAND_LOG"\n',
    )
    _write_executable(
        fake_bin / "createdb",
        '#!/bin/sh\nprintf "createdb %s\\n" "$*" >> "$COMMAND_LOG"\n',
    )
    _write_executable(
        fake_bin / "uv",
        """#!/bin/sh
printf 'uv %s\n' "$*" >> "$COMMAND_LOG"
if [ "$*" = "lock --check" ] && [ "${FAIL_LOCK:-0}" = 1 ]; then
  exit 29
fi
exit 0
""",
    )
    environment = os.environ.copy()
    environment.update(
        {
            "COMMAND_LOG": str(command_log),
            "FAIL_LOCK": "0" if lock_current else "1",
            "PATH": f"{fake_bin}:{environment['PATH']}",
            "USER": "test-admin",
        }
    )

    completed = subprocess.run(
        ["bash", str(repository / "setup.sh"), "--dev"],
        cwd=repository,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    backend_env = (backend / ".env").read_text(encoding="utf-8")
    commands = command_log.read_text(encoding="utf-8")
    if not lock_current:
        assert completed.returncode == 29, completed.stderr
        assert backend_env == original_backend_env
        assert commands == "uv lock --check\n"
        assert not (repository / ".env").exists()
        return

    assert completed.returncode == 0, completed.stderr
    assert "DEBUG=true" in backend_env
    assert "LEGACY_RUNTIME" not in backend_env
    assert f"/{TARGET_DATABASE}?ssl=disable" in backend_env
    assert not (repository / ".env").exists()
    assert "CREATE ROLE clawith_target LOGIN PASSWORD 'clawith_target'" in commands
    assert "ALTER ROLE" not in commands
    assert f"createdb --host localhost --port 5432 --username test-admin --owner clawith_target {TARGET_DATABASE}" in commands
    assert "uv lock --check" in commands
    assert "uv sync --extra dev --frozen" in commands


def test_setup_preserves_explicit_target_database_url_without_database_mutation(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repo"
    backend = repository / "backend"
    fake_bin = tmp_path / "bin"
    backend.mkdir(parents=True)
    fake_bin.mkdir()
    (repository / "setup.sh").write_text(
        SETUP.read_text(encoding="utf-8"), encoding="utf-8"
    )
    (backend / ".env.example").write_text(
        BACKEND_ENV_EXAMPLE.read_text(encoding="utf-8"), encoding="utf-8"
    )
    explicit_url = (
        "postgresql+asyncpg://operator:encoded-secret@"
        "db.internal:6432/clawith_target?ssl=require"
    )
    (backend / ".env").write_text(
        f"DEBUG=true\nDATABASE_URL={explicit_url}\n", encoding="utf-8"
    )
    command_log = tmp_path / "commands.log"
    for command in ("psql", "createdb"):
        _write_executable(
            fake_bin / command,
            f'#!/bin/sh\nprintf "{command} %s\\n" "$*" >> "$COMMAND_LOG"\n',
        )
    _write_executable(
        fake_bin / "uv",
        '#!/bin/sh\nprintf "uv %s\\n" "$*" >> "$COMMAND_LOG"\n',
    )
    environment = os.environ.copy()
    environment.update(
        {
            "COMMAND_LOG": str(command_log),
            "PATH": f"{fake_bin}:{environment['PATH']}",
            "USER": "test-admin",
        }
    )

    completed = subprocess.run(
        ["bash", str(repository / "setup.sh")],
        cwd=repository,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert f"DATABASE_URL={explicit_url}" in (backend / ".env").read_text(
        encoding="utf-8"
    )
    commands = command_log.read_text(encoding="utf-8")
    assert commands == "uv lock --check\nuv sync --frozen\n"
    assert "encoded-secret" not in completed.stdout
    assert "encoded-secret" not in completed.stderr


def test_setup_rejects_non_target_database_url_before_mutation(tmp_path: Path) -> None:
    repository = tmp_path / "repo"
    backend = repository / "backend"
    fake_bin = tmp_path / "bin"
    backend.mkdir(parents=True)
    fake_bin.mkdir()
    (repository / "setup.sh").write_text(
        SETUP.read_text(encoding="utf-8"), encoding="utf-8"
    )
    (backend / ".env.example").write_text(
        BACKEND_ENV_EXAMPLE.read_text(encoding="utf-8"), encoding="utf-8"
    )
    original = (
        "DATABASE_URL=postgresql+asyncpg://legacy:secret@localhost:5432/clawith\n"
    )
    (backend / ".env").write_text(original, encoding="utf-8")
    command_log = tmp_path / "commands.log"
    for command in ("uv", "psql", "createdb"):
        _write_executable(
            fake_bin / command,
            f'#!/bin/sh\nprintf "{command} %s\\n" "$*" >> "$COMMAND_LOG"\n',
        )
    environment = os.environ.copy()
    environment.update(
        {
            "COMMAND_LOG": str(command_log),
            "PATH": f"{fake_bin}:{environment['PATH']}",
            "USER": "test-admin",
        }
    )

    completed = subprocess.run(
        ["bash", str(repository / "setup.sh")],
        cwd=repository,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 1
    assert (backend / ".env").read_text(encoding="utf-8") == original
    assert not command_log.exists()
    assert "Set DATABASE_URL to an existing clawith_target connection" in completed.stderr
    assert "secret" not in completed.stderr


def test_setup_never_changes_credentials_for_an_existing_target_role(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repo"
    backend = repository / "backend"
    fake_bin = tmp_path / "bin"
    backend.mkdir(parents=True)
    fake_bin.mkdir()
    (repository / "setup.sh").write_text(
        SETUP.read_text(encoding="utf-8"), encoding="utf-8"
    )
    (backend / ".env.example").write_text(
        BACKEND_ENV_EXAMPLE.read_text(encoding="utf-8"), encoding="utf-8"
    )
    command_log = tmp_path / "commands.log"
    _write_executable(
        fake_bin / "psql",
        "#!/bin/sh\n"
        'printf "psql %s\\n" "$*" >> "$COMMAND_LOG"\n'
        'case "$*" in *"FROM pg_roles"*|*"FROM pg_database"*) '
        'printf "1\\n" ;; esac\n',
    )
    for command in ("createdb", "uv"):
        _write_executable(
            fake_bin / command,
            f'#!/bin/sh\nprintf "{command} %s\\n" "$*" >> "$COMMAND_LOG"\n',
        )
    environment = os.environ.copy()
    environment.update(
        {
            "COMMAND_LOG": str(command_log),
            "PATH": f"{fake_bin}:{environment['PATH']}",
            "USER": "test-admin",
        }
    )

    completed = subprocess.run(
        ["bash", str(repository / "setup.sh")],
        cwd=repository,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    commands = command_log.read_text(encoding="utf-8")
    assert "FROM pg_roles" in commands
    assert "FROM pg_database" in commands
    assert "ALTER ROLE" not in commands
    assert "CREATE ROLE" not in commands
    assert "createdb " not in commands


def test_restart_fails_before_start_when_backend_env_is_missing(tmp_path: Path) -> None:
    restart = tmp_path / "restart.sh"
    restart.write_text(RESTART.read_text(encoding="utf-8"), encoding="utf-8")

    completed = subprocess.run(
        ["bash", str(restart)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 1
    assert "Missing backend/.env" in completed.stderr
    assert not (tmp_path / ".data/backend.process").exists()


def test_restart_starts_one_worker_and_checks_health(tmp_path: Path) -> None:
    repository = tmp_path / "repo"
    backend = repository / "backend"
    backend_bin = backend / ".venv/bin"
    fake_bin = tmp_path / "bin"
    backend_bin.mkdir(parents=True)
    fake_bin.mkdir()
    restart = repository / "restart.sh"
    restart.write_text(RESTART.read_text(encoding="utf-8"), encoding="utf-8")
    (backend / ".env").write_text("DATABASE_URL=target\n", encoding="utf-8")
    command_log = tmp_path / "restart-commands.log"
    _write_executable(
        backend_bin / "uvicorn",
        '#!/bin/sh\nprintf "uvicorn %s\\n" "$*" >> "$COMMAND_LOG"\nsleep 5\n',
    )
    _write_executable(
        backend_bin / "python",
        "#!/bin/sh\nprintf '0123456789abcdef0123456789abcdef\n'\n",
    )
    _write_executable(
        fake_bin / "curl",
        (
            '#!/bin/sh\nprintf "curl %s\\n" "$*" >> "$COMMAND_LOG"\n'
            'pid="$(sed -n \'s/^pid=//p\' ../.data/backend.process)"\n'
            'startup="$(sed -n \'s/^startup_id=//p\' ../.data/backend.process)"\n'
            'printf \'{"status":"ok","process_pid":%s,"startup_id":"%s"}\\n\' '
            '"$pid" "$startup"\n'
        ),
    )
    environment = os.environ.copy()
    environment.update(
        {
            "COMMAND_LOG": str(command_log),
            "PATH": f"{fake_bin}:{environment['PATH']}",
        }
    )

    completed = subprocess.run(
        ["bash", str(restart)],
        cwd=repository,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    process_file = repository / ".data/backend.process"
    pid = _read_process_pid(process_file)
    try:
        assert completed.returncode == 0, completed.stderr
        commands = command_log.read_text(encoding="utf-8")
        assert commands.count("uvicorn app.main:app") == 1
        assert "--workers 1" in commands
        assert "/api/health" in commands
    finally:
        with suppress(ProcessLookupError):
            os.kill(pid, signal.SIGTERM)


def test_restart_refuses_to_signal_reused_stale_pid(tmp_path: Path) -> None:
    repository, _fake_bin, environment = _restart_fixture(
        tmp_path,
        uv_source="#!/bin/sh\nexit 99\n",
        curl_source="#!/bin/sh\nexit 99\n",
    )
    process_file = repository / ".data/backend.process"
    process_file.parent.mkdir()
    process_file.write_text(
        f"pid={os.getpid()}\nstart=stale-start-identity\n",
        encoding="utf-8",
    )

    completed = subprocess.run(
        ["bash", str(repository / "restart.sh")],
        cwd=repository,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 1
    assert "Refusing to signal" in completed.stderr
    assert _process_exists(os.getpid())
    assert process_file.exists()


def test_restart_identity_capture_failure_stops_pending_child(tmp_path: Path) -> None:
    term_log = tmp_path / "term.log"
    repository, fake_bin, environment = _restart_fixture(
        tmp_path,
        uv_source=(
            "#!/bin/sh\n"
            "trap 'printf terminated > \"$TERM_LOG\"; exit 0' TERM\n"
            "while :; do sleep 0.1; done\n"
        ),
        curl_source="#!/bin/sh\nexit 1\n",
    )
    real_ps = shutil.which("ps")
    assert real_ps is not None
    _write_executable(
        fake_bin / "ps",
        (
            "#!/bin/sh\n"
            "case \"$*\" in\n"
            "  *lstart=*) exit 0 ;;\n"
            "  *) exec \"$REAL_PS\" \"$@\" ;;\n"
            "esac\n"
        ),
    )
    environment.update({"REAL_PS": real_ps, "TERM_LOG": str(term_log)})

    completed = subprocess.run(
        ["bash", str(repository / "restart.sh")],
        cwd=repository,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 1
    assert "Could not capture" in completed.stderr
    match = re.search(r"pid=(\d+)", completed.stderr)
    assert match is not None
    assert not _process_exists(int(match.group(1)))
    assert not (repository / ".data/backend.process").exists()


def test_restart_timeout_stops_owned_child_and_removes_evidence(tmp_path: Path) -> None:
    term_log = tmp_path / "term.log"
    repository, _fake_bin, environment = _restart_fixture(
        tmp_path,
        uv_source=(
            "#!/bin/sh\n"
            "trap 'printf terminated > \"$TERM_LOG\"; exit 0' TERM\n"
            "while :; do sleep 0.1; done\n"
        ),
        curl_source="#!/bin/sh\nexit 1\n",
    )
    environment.update(
        {
            "CLAWITH_HEALTH_ATTEMPTS": "1",
            "TERM_LOG": str(term_log),
        }
    )

    completed = subprocess.run(
        ["bash", str(repository / "restart.sh")],
        cwd=repository,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 1
    assert "timed out" in completed.stderr
    assert term_log.read_text(encoding="utf-8") == "terminated"
    assert not (repository / ".data/backend.process").exists()


@pytest.mark.parametrize("spoof", ["wrong-pid", "wrong-startup"])
def test_restart_rejects_health_from_another_process(
    tmp_path: Path,
    spoof: str,
) -> None:
    term_log = tmp_path / "term.log"
    if spoof == "wrong-pid":
        response = (
            "printf '{\"status\":\"ok\",\"process_pid\":999999,"
            "\"startup_id\":\"0123456789abcdef0123456789abcdef\"}\\n'\n"
        )
    else:
        response = (
            "pid=\"$(sed -n 's/^pid=//p' ../.data/backend.process)\"\n"
            "printf '{\"status\":\"ok\",\"process_pid\":%s,"
            "\"startup_id\":\"ffffffffffffffffffffffffffffffff\"}\\n' \"$pid\"\n"
        )
    repository, _fake_bin, environment = _restart_fixture(
        tmp_path,
        uv_source=(
            "#!/bin/sh\n"
            "trap 'printf terminated > \"$TERM_LOG\"; exit 0' TERM\n"
            "while :; do sleep 0.1; done\n"
        ),
        curl_source=f"#!/bin/sh\n{response}",
    )
    environment.update(
        {
            "CLAWITH_HEALTH_ATTEMPTS": "1",
            "TERM_LOG": str(term_log),
        }
    )

    completed = subprocess.run(
        ["bash", str(repository / "restart.sh")],
        cwd=repository,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 1
    assert "timed out" in completed.stderr
    assert term_log.read_text(encoding="utf-8") == "terminated"
    assert not (repository / ".data/backend.process").exists()


def test_restart_child_failure_removes_terminal_evidence(tmp_path: Path) -> None:
    repository, _fake_bin, environment = _restart_fixture(
        tmp_path,
        uv_source="#!/bin/sh\nsleep 0.1\nexit 7\n",
        curl_source="#!/bin/sh\nsleep 0.2\nexit 1\n",
    )

    completed = subprocess.run(
        ["bash", str(repository / "restart.sh")],
        cwd=repository,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 1
    assert "exited before becoming healthy" in completed.stderr
    assert not (repository / ".data/backend.process").exists()


@pytest.mark.parametrize(
    ("sent_signal", "expected_status"),
    [(signal.SIGINT, 130), (signal.SIGTERM, 143)],
)
def test_restart_signal_stops_owned_child(
    tmp_path: Path,
    sent_signal: signal.Signals,
    expected_status: int,
) -> None:
    term_log = tmp_path / "term.log"
    ready_log = tmp_path / "ready.log"
    repository, _fake_bin, environment = _restart_fixture(
        tmp_path,
        uv_source=(
            "#!/bin/sh\n"
            "trap 'printf terminated > \"$TERM_LOG\"; exit 0' TERM\n"
            "printf ready > \"$READY_LOG\"\n"
            "while :; do sleep 0.1; done\n"
        ),
        curl_source="#!/bin/sh\nsleep 0.1\nexit 1\n",
    )
    environment.update(
        {
            "CLAWITH_HEALTH_ATTEMPTS": "1000",
            "READY_LOG": str(ready_log),
            "TERM_LOG": str(term_log),
        }
    )
    process = subprocess.Popen(
        ["bash", str(repository / "restart.sh")],
        cwd=repository,
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    process_file = repository / ".data/backend.process"
    _wait_for_path(process_file)
    _wait_for_path(ready_log)

    process.send_signal(sent_signal)
    _stdout, stderr = process.communicate(timeout=5)

    assert process.returncode == expected_status, stderr
    assert term_log.read_text(encoding="utf-8") == "terminated"
    assert not process_file.exists()


def test_concurrent_restart_fails_without_touching_active_invocation(
    tmp_path: Path,
) -> None:
    term_log = tmp_path / "term.log"
    repository, _fake_bin, environment = _restart_fixture(
        tmp_path,
        uv_source=(
            "#!/bin/sh\n"
            "trap 'printf terminated > \"$TERM_LOG\"; exit 0' TERM\n"
            "while :; do sleep 0.1; done\n"
        ),
        curl_source="#!/bin/sh\nsleep 0.1\nexit 1\n",
    )
    environment.update(
        {
            "CLAWITH_HEALTH_ATTEMPTS": "1000",
            "TERM_LOG": str(term_log),
        }
    )
    first = subprocess.Popen(
        ["bash", str(repository / "restart.sh")],
        cwd=repository,
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    process_file = repository / ".data/backend.process"
    restart_lock = repository / ".data/backend.restart.lock"
    _wait_for_path(process_file)
    _wait_for_path(restart_lock)
    owned_pid = _read_process_pid(process_file)

    second = subprocess.run(
        ["bash", str(repository / "restart.sh")],
        cwd=repository,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert second.returncode == 1
    assert "Another restart is already in progress" in second.stderr
    assert first.poll() is None
    assert _process_exists(owned_pid)
    first.send_signal(signal.SIGTERM)
    _stdout, stderr = first.communicate(timeout=5)
    assert first.returncode == 143, stderr
    assert term_log.read_text(encoding="utf-8") == "terminated"
    assert not process_file.exists()
    assert not restart_lock.exists()


def test_restart_cleanup_preserves_replaced_shared_evidence(tmp_path: Path) -> None:
    term_log = tmp_path / "term.log"
    ready_log = tmp_path / "ready.log"
    repository, _fake_bin, environment = _restart_fixture(
        tmp_path,
        uv_source=(
            "#!/bin/sh\n"
            "trap 'printf terminated > \"$TERM_LOG\"; exit 0' TERM\n"
            "printf ready > \"$READY_LOG\"\n"
            "while :; do sleep 0.1; done\n"
        ),
        curl_source="#!/bin/sh\nsleep 0.1\nexit 1\n",
    )
    environment.update(
        {
            "CLAWITH_HEALTH_ATTEMPTS": "1000",
            "READY_LOG": str(ready_log),
            "TERM_LOG": str(term_log),
        }
    )
    restart = subprocess.Popen(
        ["bash", str(repository / "restart.sh")],
        cwd=repository,
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    process_file = repository / ".data/backend.process"
    _wait_for_path(process_file)
    _wait_for_path(ready_log)
    owned_pid = _read_process_pid(process_file)
    replacement = (
        f"pid={os.getpid()}\n"
        "start=replacement-start\n"
        "startup_id=replacement-startup\n"
    )
    process_file.write_text(replacement, encoding="utf-8")

    restart.send_signal(signal.SIGTERM)
    _stdout, stderr = restart.communicate(timeout=5)

    assert restart.returncode == 143, stderr
    assert not _process_exists(owned_pid)
    assert term_log.read_text(encoding="utf-8") == "terminated"
    assert process_file.read_text(encoding="utf-8") == replacement
    assert not (repository / ".data/backend.restart.lock").exists()


@pytest.mark.parametrize("evidence_state", ["missing", "foreign"])
def test_restart_records_owned_unsettled_child_without_overwriting_foreign_evidence(
    tmp_path: Path,
    evidence_state: str,
) -> None:
    ready_log = tmp_path / "ready.log"
    repository, _fake_bin, environment = _restart_fixture(
        tmp_path,
        uv_source=(
            "#!/bin/sh\n"
            "trap '' TERM\n"
            "printf ready > \"$READY_LOG\"\n"
            "while :; do sleep 0.1; done\n"
        ),
        curl_source="#!/bin/sh\nsleep 0.1\nexit 1\n",
    )
    environment.update(
        {
            "CLAWITH_HEALTH_ATTEMPTS": "1000",
            "CLAWITH_STOP_ATTEMPTS": "1",
            "READY_LOG": str(ready_log),
        }
    )
    restart = subprocess.Popen(
        ["bash", str(repository / "restart.sh")],
        cwd=repository,
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    process_file = repository / ".data/backend.process"
    _wait_for_path(process_file)
    _wait_for_path(ready_log)
    owned = process_file.read_text(encoding="utf-8")
    owned_pid = _read_process_pid(process_file)
    foreign = (
        f"pid={os.getpid()}\n"
        "start=foreign-start\n"
        "startup_id=foreign-startup\n"
    )
    if evidence_state == "missing":
        process_file.unlink()
    else:
        process_file.write_text(foreign, encoding="utf-8")

    restart.send_signal(signal.SIGTERM)
    _stdout, stderr = restart.communicate(timeout=5)

    try:
        assert restart.returncode == 1
        assert _process_exists(owned_pid)
        if evidence_state == "missing":
            assert process_file.read_text(encoding="utf-8") == owned
            assert "ownership evidence retained" in stderr
        else:
            assert process_file.read_text(encoding="utf-8") == foreign
            [unsettled] = list(
                (repository / ".data").glob("backend.unsettled.*.process")
            )
            assert unsettled.read_text(encoding="utf-8") == owned
            assert "foreign evidence preserved" in stderr
    finally:
        with suppress(ProcessLookupError):
            os.kill(owned_pid, signal.SIGKILL)


def test_restart_fails_closed_on_unverifiable_stale_lock(tmp_path: Path) -> None:
    repository, _fake_bin, environment = _restart_fixture(
        tmp_path,
        uv_source="#!/bin/sh\nexit 99\n",
        curl_source="#!/bin/sh\nexit 99\n",
    )
    restart_lock = repository / ".data/backend.restart.lock"
    restart_lock.mkdir(parents=True)

    completed = subprocess.run(
        ["bash", str(repository / "restart.sh")],
        cwd=repository,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 1
    assert "unverifiable stale lock" in completed.stderr
    assert "manually removing that exact lock directory" in completed.stderr
    assert restart_lock.is_dir()
    assert not (repository / ".data/backend.process").exists()


def test_restart_fails_before_signal_or_launch_when_unsettled_evidence_exists(
    tmp_path: Path,
) -> None:
    launch_log = tmp_path / "launch.log"
    repository, _fake_bin, environment = _restart_fixture(
        tmp_path,
        uv_source='#!/bin/sh\nprintf launched > "$LAUNCH_LOG"\nexit 99\n',
        curl_source="#!/bin/sh\nexit 99\n",
    )
    environment["LAUNCH_LOG"] = str(launch_log)
    state_dir = repository / ".data"
    state_dir.mkdir()
    process_file = state_dir / "backend.process"
    process_file.write_text(
        f"pid={os.getpid()}\nstart=foreign\nstartup_id=foreign\n",
        encoding="utf-8",
    )
    unsettled = state_dir / "backend.unsettled.previous.process"
    unsettled.write_text(
        "pid=999999\nstart=previous\nstartup_id=previous\n",
        encoding="utf-8",
    )

    completed = subprocess.run(
        ["bash", str(repository / "restart.sh")],
        cwd=repository,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 1
    assert "manual recovery before restart" in completed.stderr
    assert _process_exists(os.getpid())
    assert not launch_log.exists()
    assert process_file.exists()
    assert unsettled.exists()
    assert not (state_dir / "backend.restart.lock").exists()


def test_restart_retains_evidence_when_owned_child_cannot_stop(tmp_path: Path) -> None:
    repository, _fake_bin, environment = _restart_fixture(
        tmp_path,
        uv_source=(
            "#!/bin/sh\n"
            "trap '' TERM\n"
            "while :; do sleep 0.1; done\n"
        ),
        curl_source="#!/bin/sh\nexit 1\n",
    )
    environment.update(
        {
            "CLAWITH_HEALTH_ATTEMPTS": "1",
            "CLAWITH_STOP_ATTEMPTS": "1",
        }
    )

    completed = subprocess.run(
        ["bash", str(repository / "restart.sh")],
        cwd=repository,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    process_file = repository / ".data/backend.process"
    pid = _read_process_pid(process_file)
    try:
        assert completed.returncode == 1
        assert "evidence retained" in completed.stderr
        assert _process_exists(pid)
    finally:
        with suppress(ProcessLookupError):
            os.kill(pid, signal.SIGKILL)


def test_public_readme_describes_health_only_state() -> None:
    source = README.read_text(encoding="utf-8")
    assert "current `develop` branch is at G002" in source
    assert "health-only Backend" in source
    assert "not a supported G002 product-start path" in source
    assert "Product APIs" in source and "not available" in source


def test_active_database_configs_use_target_namespace() -> None:
    legacy_patterns = (
        re.compile(r"postgresql\+asyncpg://[^\s]+/clawith(?:[?\"'\s]|$)"),
        re.compile(r"^\s*POSTGRES_DB:\s*[\"']?clawith[\"']?\s*$", re.MULTILINE),
        re.compile(r"^\s*database:\s*[\"']?clawith[\"']?\s*$", re.MULTILINE),
        re.compile(r"\bpsql\b[^\n]*\s-d\s+clawith(?:\s|$)"),
    )
    for config_path in ACTIVE_DATABASE_CONFIGS:
        source = config_path.read_text(encoding="utf-8")
        assert TARGET_DATABASE in source, config_path
        assert not any(pattern.search(source) for pattern in legacy_patterns), config_path


def test_helm_templates_resolve_database_from_target_values() -> None:
    values = yaml.safe_load(
        (REPOSITORY_ROOT / "helm/clawith/values.yaml").read_text(encoding="utf-8")
    )
    assert values["postgresql"]["auth"]["database"] == TARGET_DATABASE
    assert values["postgresql"]["external"]["database"] == TARGET_DATABASE
    backend_template = (
        REPOSITORY_ROOT / "helm/clawith/templates/backend.yaml"
    ).read_text(encoding="utf-8")
    postgres_template = (
        REPOSITORY_ROOT / "helm/clawith/templates/postgresql.yaml"
    ).read_text(encoding="utf-8")
    assert 'include "clawith.postgresql.database"' in backend_template
    assert ".Values.postgresql.auth.database" in postgres_template


def test_deferred_compose_and_helm_paths_require_explicit_opt_in() -> None:
    for config_path in COMPOSE_CONFIGS:
        _validate_compose_quarantine(config_path.read_text(encoding="utf-8"))
    values = yaml.safe_load(
        (REPOSITORY_ROOT / "helm/clawith/values.yaml").read_text(encoding="utf-8")
    )
    assert values["g002Deferred"] is True
    templates_root = REPOSITORY_ROOT / "helm/clawith/templates"
    for template_path in sorted(templates_root.glob("*.yaml")):
        template = template_path.read_text(encoding="utf-8")
        _validate_helm_template_quarantine(template)
    namespace_template = (
        REPOSITORY_ROOT / "helm/clawith/templates/namespace.yaml"
    ).read_text(encoding="utf-8")
    assert namespace_template.count("not .Values.g002Deferred") == 2
    assert namespace_template.count("kind:") == 2


def test_legacy_ci_product_workflows_are_replaced_by_g002_gates() -> None:
    drone = (REPOSITORY_ROOT / ".github/drone.yml").read_text(encoding="utf-8")
    github = (REPOSITORY_ROOT / ".github/workflows/release.yml").read_text(
        encoding="utf-8"
    )
    assert isinstance(yaml.safe_load(drone), dict)
    assert isinstance(yaml.safe_load(github), dict)
    _validate_ci_gate_sources(
        drone,
        github,
        legacy_script_exists=any(path.exists() for path in LEGACY_CI_SCRIPTS),
    )


def test_compose_quarantine_rejects_an_unguarded_service() -> None:
    source = "services:\n  backend:\n    image: target\n"
    with pytest.raises(StartupContractError, match="not deferred"):
        _validate_compose_quarantine(source)


@pytest.mark.parametrize("forbidden", ["alembic", "docker compose", "ci_upgrade_test"])
def test_ci_gate_contract_rejects_legacy_work(forbidden: str) -> None:
    drone = (REPOSITORY_ROOT / ".github/drone.yml").read_text(encoding="utf-8")
    github = (REPOSITORY_ROOT / ".github/workflows/release.yml").read_text(
        encoding="utf-8"
    )
    poisoned_drone = _inject_drone_commands(drone, [forbidden])
    with pytest.raises(StartupContractError, match="gate-only"):
        _validate_ci_gate_sources(
            poisoned_drone,
            github,
            legacy_script_exists=False,
        )


def test_ci_gate_contract_rejects_a_restored_legacy_script() -> None:
    drone = (REPOSITORY_ROOT / ".github/drone.yml").read_text(encoding="utf-8")
    github = (REPOSITORY_ROOT / ".github/workflows/release.yml").read_text(
        encoding="utf-8"
    )
    with pytest.raises(StartupContractError, match="gate-only"):
        _validate_ci_gate_sources(
            drone,
            github,
            legacy_script_exists=True,
        )


@pytest.mark.parametrize(
    "split_commands",
    [
        ["MIG=alem", 'MIG="${MIG}bic"', '"$MIG" upgrade head'],
        [
            "MODULE=app.scripts.setup_langgraph_",
            'MODULE="${MODULE}checkpoints"',
            'python -m "$MODULE"',
        ],
    ],
)
def test_ci_gate_contract_rejects_split_token_commands(
    split_commands: list[str],
) -> None:
    drone = (REPOSITORY_ROOT / ".github/drone.yml").read_text(encoding="utf-8")
    github = (REPOSITORY_ROOT / ".github/workflows/release.yml").read_text(
        encoding="utf-8"
    )
    poisoned_drone = _inject_drone_commands(drone, split_commands)
    with pytest.raises(StartupContractError, match="gate-only"):
        _validate_ci_gate_sources(
            poisoned_drone,
            github,
            legacy_script_exists=False,
        )


def test_ci_gate_contract_allows_inert_migration_prose() -> None:
    drone = (REPOSITORY_ROOT / ".github/drone.yml").read_text(encoding="utf-8")
    github = (REPOSITORY_ROOT / ".github/workflows/release.yml").read_text(
        encoding="utf-8"
    )
    documented_drone = f"description: alembic and checkpoint installers are disabled\n{drone}"
    _validate_ci_gate_sources(
        documented_drone,
        github,
        legacy_script_exists=False,
    )


def test_ci_comments_cannot_supply_required_gate_or_trigger() -> None:
    drone = (REPOSITORY_ROOT / ".github/drone.yml").read_text(encoding="utf-8")
    github = (REPOSITORY_ROOT / ".github/workflows/release.yml").read_text(
        encoding="utf-8"
    )
    ci_script = CI_GATE_SCRIPT.read_text(encoding="utf-8")
    missing_gate = ci_script.replace(
        "uv run --extra dev pyright app",
        "echo pyright-disabled",
    )
    missing_gate = f"# uv run --extra dev pyright app\n{missing_gate}"
    with pytest.raises(StartupContractError, match="gate-only"):
        _validate_ci_gate_sources(
            drone,
            github,
            legacy_script_exists=False,
            ci_script=missing_gate,
        )
    missing_push = github.replace(
        "  push:\n    branches:\n      - develop\n",
        "  # push:\n  #   branches: [develop]\n",
    )
    with pytest.raises(StartupContractError, match="gate-only"):
        _validate_ci_gate_sources(
            drone,
            missing_push,
            legacy_script_exists=False,
        )


def test_ci_short_circuit_or_echo_cannot_supply_required_gate() -> None:
    drone = (REPOSITORY_ROOT / ".github/drone.yml").read_text(encoding="utf-8")
    github = (REPOSITORY_ROOT / ".github/workflows/release.yml").read_text(
        encoding="utf-8"
    )
    ci_script = CI_GATE_SCRIPT.read_text(encoding="utf-8")
    spoofed = ci_script.replace(
        "uv run --extra dev pyright app",
        "true || echo 'uv run --extra dev pyright app'",
    )
    with pytest.raises(StartupContractError, match="gate-only"):
        _validate_ci_gate_sources(
            drone,
            github,
            legacy_script_exists=False,
            ci_script=spoofed,
        )


def test_ci_gate_script_rejects_an_arbitrary_non_gate_command() -> None:
    drone = (REPOSITORY_ROOT / ".github/drone.yml").read_text(encoding="utf-8")
    github = (REPOSITORY_ROOT / ".github/workflows/release.yml").read_text(
        encoding="utf-8"
    )
    ci_script = CI_GATE_SCRIPT.read_text(encoding="utf-8")
    poisoned = ci_script.replace(
        REQUIRED_CUMULATIVE_GATE_COMMANDS[1],
        f"{REQUIRED_CUMULATIVE_GATE_COMMANDS[1]}\nbash /tmp/legacy-deploy.sh",
    )

    with pytest.raises(StartupContractError, match="gate-only"):
        _validate_ci_gate_sources(
            drone,
            github,
            legacy_script_exists=False,
            ci_script=poisoned,
        )


def test_ci_gate_script_rejects_skipping_the_lock_freshness_check() -> None:
    source = CI_GATE_SCRIPT.read_text(encoding="utf-8")
    poisoned = source.replace("uv lock --check", "true # lock is probably current")

    with pytest.raises(StartupContractError, match="exact cumulative gates"):
        _validate_cumulative_ci_script(poisoned)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda source: source.replace(
            "uv run python scripts/validate_goal_gates.py",
            "exit 0\nuv run python scripts/validate_goal_gates.py",
            1,
        ),
        lambda source: source.replace(
            "uv run --extra dev pytest tests/architecture\n",
            "if false; then\nuv run --extra dev pytest tests/architecture\nfi\n",
            1,
        ),
        lambda source: source.replace(
            "uv run --extra dev pyright app",
            "uv run --extra dev pyright app || true",
        ),
    ],
    ids=["early-exit", "false-conditional", "ignored-failure"],
)
def test_ci_gate_script_rejects_unreachable_or_ignored_gates(
    mutation: Callable[[str], str],
) -> None:
    source = CI_GATE_SCRIPT.read_text(encoding="utf-8")
    poisoned = mutation(source)

    with pytest.raises(StartupContractError):
        _validate_cumulative_ci_script(poisoned)


def test_ci_workflow_rejects_continue_on_error() -> None:
    drone = (REPOSITORY_ROOT / ".github/drone.yml").read_text(encoding="utf-8")
    github = (REPOSITORY_ROOT / ".github/workflows/release.yml").read_text(
        encoding="utf-8"
    )
    poisoned = github.replace(
        f"run: {SHARED_CI_COMMAND}",
        f"continue-on-error: true\n        run: {SHARED_CI_COMMAND}",
    )

    with pytest.raises(StartupContractError, match="gate-only"):
        _validate_ci_gate_sources(
            drone,
            poisoned,
            legacy_script_exists=False,
        )


@pytest.mark.parametrize(
    ("fail_gate", "fail_remove", "fail_first_prune", "expected_status"),
    [
        (False, False, False, 0),
        (True, False, False, 19),
        (False, True, False, 0),
        (False, True, True, 0),
    ],
    ids=["success", "gate-failure", "remove-recovered", "prune-retried"],
)
def test_g001_reference_script_removes_temporary_worktree_on_exit(
    tmp_path: Path,
    fail_gate: bool,
    fail_remove: bool,
    fail_first_prune: bool,
    expected_status: int,
) -> None:
    repository = tmp_path / "repository"
    scripts = repository / "scripts"
    backend = repository / "backend"
    fake_bin = tmp_path / "bin"
    scripts.mkdir(parents=True)
    backend.mkdir()
    fake_bin.mkdir()
    script = scripts / "check-g001-reference.sh"
    script.write_text(
        G001_REFERENCE_SCRIPT.read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    script.chmod(0o755)
    temp_root = tmp_path / "ci-temp"
    registry = tmp_path / "worktree-registry"

    _write_executable(
        fake_bin / "mktemp",
        '#!/bin/sh\nmkdir -p "$TEST_TEMP_ROOT"\nprintf "%s\\n" "$TEST_TEMP_ROOT"\n',
    )
    _write_executable(
        fake_bin / "git",
        """#!/bin/sh
case "$*" in
  *"cat-file -e"*) exit 0 ;;
  *"worktree add"*)
    mkdir -p "$TEST_REFERENCE/backend/.venv/bin"
    printf '#!/bin/sh\nexit 0\n' > "$TEST_REFERENCE/backend/.venv/bin/python"
    chmod 755 "$TEST_REFERENCE/backend/.venv/bin/python"
    printf registered > "$TEST_REGISTRY"
    ;;
  *"worktree remove"*)
    if [ "${FAIL_REMOVE:-0}" = 1 ]; then
      exit 31
    fi
    /bin/rm -rf "$TEST_REFERENCE" "$TEST_REGISTRY"
    ;;
  *"worktree prune"*)
    if [ "${FAIL_FIRST_PRUNE:-0}" = 1 ] && [ ! -f "$TEST_PRUNE_MARKER" ]; then
      printf attempted > "$TEST_PRUNE_MARKER"
      exit 32
    fi
    /bin/rm -f "$TEST_REGISTRY"
    ;;
esac
""",
    )
    _write_executable(
        fake_bin / "uv",
        """#!/bin/sh
if [ "${1:-}" = run ] && [ "${FAIL_GATE:-0}" = 1 ]; then
  exit 19
fi
exit 0
""",
    )
    environment = os.environ.copy()
    environment.update(
        {
            "FAIL_GATE": "1" if fail_gate else "0",
            "FAIL_FIRST_PRUNE": "1" if fail_first_prune else "0",
            "FAIL_REMOVE": "1" if fail_remove else "0",
            "PATH": f"{fake_bin}:{environment['PATH']}",
            "TEST_PRUNE_MARKER": str(tmp_path / "prune-attempted"),
            "TEST_REFERENCE": str(temp_root / "legacy-reference"),
            "TEST_REGISTRY": str(registry),
            "TEST_TEMP_ROOT": str(temp_root),
        }
    )

    completed = subprocess.run(
        ["bash", str(script)],
        cwd=repository,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == expected_status, completed.stderr
    assert not temp_root.exists()
    assert not registry.exists()


def test_helm_quarantine_rejects_comment_spoof_and_unguarded_resource() -> None:
    source = (
        "# {{- if not .Values.g002Deferred }}\n"
        "apiVersion: v1\n"
        "kind: Secret\n"
    )
    with pytest.raises(StartupContractError, match="not quarantined"):
        _validate_helm_template_quarantine(source)


@pytest.mark.parametrize(
    "source",
    [
        (
            "{{- if not .Values.g002Deferred }}\n"
            "{{- else }}\n"
            "apiVersion: v1\nkind: Secret\n"
            "{{- end }}\n"
        ),
        "{{- if not .Values.g002Deferred }} kind: Secret\n",
        "{{- unknown .Values.g002Deferred }}\nkind: Secret\n",
        "{{- if not .Values.g002Deferred }}\nkind: Secret\n",
        "{{- if not .Values.g002DeferredBypass }}\nkind: Secret\n{{- end }}\n",
        "{{- if or (not .Values.g002Deferred) true }}\nkind: Secret\n{{- end }}\n",
        "{{- if and (not .Values.g002Deferred) true }}\nkind: Secret\n{{- end }}\n",
    ],
)
def test_helm_quarantine_rejects_else_inline_unknown_and_unclosed_templates(
    source: str,
) -> None:
    with pytest.raises(StartupContractError):
        _validate_helm_template_quarantine(source)


def test_helm_quarantine_allows_inert_comments_without_resources() -> None:
    _validate_helm_template_quarantine(
        "# kind: Secret\n# not .Values.g002Deferred\n"
    )


def test_all_operator_docs_are_quarantined_to_g002_health_only() -> None:
    for document in OPERATOR_DOCS:
        _validate_operator_document(document.read_text(encoding="utf-8"))


def test_alembic_ini_uses_target_namespace_and_operator_warning() -> None:
    source = (BACKEND_ROOT / "alembic.ini").read_text(encoding="utf-8")
    assert "until the reviewed G008 target baseline" in source
    assert "localhost:5432/clawith_target" in source
    assert "localhost:5432/clawith\n" not in source


@pytest.mark.parametrize(
    "instructions",
    [
        "```bash\ndocker compose up -d\n```",
        "```bash\nhelm install clawith ./helm/clawith\n```",
        "```bash\nnpm run dev\n```",
        "```bash\ncp .env.example .env\n```",
        "```bash\npsql -d clawith\n```",
        (
            "```bash\n"
            "MIG=alem\n"
            'MIG="${MIG}bic"\n'
            '"$MIG" upgrade head\n'
            "```"
        ),
        "```bash\necho safe; alembic upgrade head\n```",
        "```bash\necho safe; python -m app.scripts.setup_langgraph_checkpoints\n```",
        "   ```bash\nalembic upgrade head\n   ```",
        "~~~bash\nalembic upgrade head\n~~~",
        "```bash\nOUT=$(alembic upgrade head)\n```",
        "```bash\nOUT=$(alembic $(printf upgrade) head)\n```",
        '```bash\nOUT="$(alembic $(printf upgrade) head)"\n```',
        '```bash\necho "$(alembic $(printf upgrade) head)"\n```',
        "```bash\nprintf 'alembic upgrade head\\n' | bash\n```",
        "```bash\neval 'alembic upgrade head'\n```",
        "```bash\nsource /tmp/legacy.sh\n```",
        "```bash\n. /tmp/legacy.sh\n```",
        "```bash\n/bin/bash setup.sh\n```",
        "```bash\nenv bash setup.sh\n```",
        "```bash\nprintf payload | command bash\n```",
        "```bash\nprintf payload | /usr/bin/env bash\n```",
        "```bash\nprintf payload | nice bash\n```",
        "```bash\nprintf payload | xargs bash\n```",
        "```bash\ncat <(alembic upgrade head)\n```",
        "```bash\ncat >(alembic upgrade head)\n```",
        "DATABASE_URL=postgresql+asyncpg://user:secret@localhost:5432/clawith",
    ],
)
def test_operator_doc_guard_rejects_executable_legacy_instructions(
    instructions: str,
) -> None:
    source = f"# G002 health-only\n\n{instructions}\n"
    with pytest.raises(StartupContractError):
        _validate_operator_document(source)


def test_operator_doc_guard_allows_inert_legacy_prose() -> None:
    source = (
        "# G002 health-only\n\n"
        "Do not run Alembic, Docker, Helm, or the legacy checkpoint installer.\n"
        "The isolated database is `clawith_target`.\n"
    )
    _validate_operator_document(source)


def test_operator_doc_guard_allows_inert_tilde_fenced_warning() -> None:
    source = (
        "# G002 health-only\n\n"
        "~~~text\nDo not run Alembic or Docker during G002.\n~~~\n"
    )
    _validate_operator_document(source)


def test_operator_doc_guard_allows_exact_health_only_entry_commands() -> None:
    source = (
        "# G002 health-only\n\n"
        "```bash\nbash setup.sh\nbash restart.sh\n```\n"
    )
    _validate_operator_document(source)
