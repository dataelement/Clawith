"""CI must provide PostgreSQL externally and execute cumulative G003 gates."""

from __future__ import annotations

import copy
from pathlib import Path

import pytest
import yaml

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
DRONE_PATH = REPOSITORY_ROOT / ".github/drone.yml"
GITHUB_PATH = REPOSITORY_ROOT / ".github/workflows/release.yml"
G003_SCRIPT = REPOSITORY_ROOT / "scripts/ci-g003-gates.sh"
GOAL_GATES_PATH = REPOSITORY_ROOT / "backend/rewrite/goal-gates.json"
TARGET_DATABASE = "clawith_target"
POSTGRES_IMAGE = "postgres:15"
DRONE_DATABASE_URL = (
    "postgresql+asyncpg://clawith_test:isolated-test-only@postgres:5432/clawith_target"
)
GITHUB_DATABASE_URL = (
    "postgresql+asyncpg://clawith_test:isolated-test-only@127.0.0.1:5432/clawith_target"
)
G003_COMMAND = "bash scripts/ci-g003-gates.sh"
_GOAL_GATES = yaml.safe_load(GOAL_GATES_PATH.read_text(encoding="utf-8"))
FOUNDATION_CHECK = _GOAL_GATES["goals"][3]["validations"][0]["command"]
FOUNDATION_TESTS = (
    "uv run --extra dev pytest tests/database tests/modules/identity_tenant "
    "tests/modules/credential tests/modules/model tests/modules/agent "
    "tests/modules/permission tests/modules/auth tests/modules/audit"
)


class CiContractError(AssertionError):
    pass


def _load(path: Path) -> dict:
    value = yaml.load(path.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
    assert isinstance(value, dict)
    return value


def _validate_ci(drone: dict, github: dict) -> None:
    drone_steps = drone.get("steps")
    drone_services = drone.get("services")
    if not isinstance(drone_steps, list) or len(drone_steps) != 1:
        raise CiContractError("Drone must keep one Backend gate step")
    if not isinstance(drone_services, list) or len(drone_services) != 1:
        raise CiContractError("Drone must provide one PostgreSQL service")
    drone_step = drone_steps[0]
    drone_postgres = drone_services[0]
    if drone_step.get("commands") != [G003_COMMAND]:
        raise CiContractError("Drone must invoke the G003 wrapper exactly")
    if drone_step.get("environment", {}).get("CLAWITH_TEST_POSTGRES_URL") != DRONE_DATABASE_URL:
        raise CiContractError("Drone must use its PostgreSQL service hostname")
    if drone_postgres.get("name") != "postgres" or drone_postgres.get("image") != POSTGRES_IMAGE:
        raise CiContractError("Drone PostgreSQL service must be PostgreSQL 15")
    if drone_postgres.get("environment") != {
        "POSTGRES_USER": "clawith_test",
        "POSTGRES_PASSWORD": "isolated-test-only",
        "POSTGRES_DB": TARGET_DATABASE,
    }:
        raise CiContractError("Drone PostgreSQL service must own the target test database")

    job = github.get("jobs", {}).get("backend-g002", {})
    github_postgres = job.get("services", {}).get("postgres", {})
    if job.get("env", {}).get("CLAWITH_TEST_POSTGRES_URL") != GITHUB_DATABASE_URL:
        raise CiContractError("GitHub must use its loopback PostgreSQL service port")
    if github_postgres.get("image") != POSTGRES_IMAGE:
        raise CiContractError("GitHub PostgreSQL service must be PostgreSQL 15")
    if github_postgres.get("env") != {
        "POSTGRES_USER": "clawith_test",
        "POSTGRES_PASSWORD": "isolated-test-only",
        "POSTGRES_DB": TARGET_DATABASE,
    }:
        raise CiContractError("GitHub PostgreSQL service must own the target test database")
    if github_postgres.get("ports") != ["5432:5432"]:
        raise CiContractError("GitHub PostgreSQL service must bind only its test port")
    health_options = " ".join(str(github_postgres.get("options", "")).split())
    if health_options != (
        '--health-cmd "pg_isready -U clawith_test -d clawith_target" '
        "--health-interval 2s --health-timeout 5s --health-retries 30"
    ):
        raise CiContractError("GitHub PostgreSQL service must wait for target database readiness")
    run_steps = [step.get("run") for step in job.get("steps", []) if isinstance(step, dict) and "run" in step]
    if run_steps != [G003_COMMAND]:
        raise CiContractError("GitHub must invoke the G003 wrapper exactly")


def test_ci_provides_postgres_15_and_invokes_the_cumulative_g003_wrapper() -> None:
    _validate_ci(_load(DRONE_PATH), _load(GITHUB_PATH))

    lines = [
        line.strip()
        for line in G003_SCRIPT.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    assert lines == [
        "set -euo pipefail",
        'repository_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"',
        'backend_root="$repository_root/backend"',
        'bash "$repository_root/scripts/ci-g002-gates.sh"',
        'cd "$backend_root"',
        FOUNDATION_CHECK,
        FOUNDATION_TESTS,
    ]
    assert "--approval-receipt" in FOUNDATION_CHECK


@pytest.mark.parametrize(
    "mutation",
    [
        lambda drone, github: drone.pop("services"),
        lambda drone, github: github["jobs"]["backend-g002"].pop("services"),
        lambda drone, github: drone["services"][0].update(image="postgres:16"),
        lambda drone, github: drone["steps"][0]["commands"].__setitem__(
            0, "bash scripts/ci-g002-gates.sh"
        ),
        lambda drone, github: drone["steps"][0]["environment"].update(
            CLAWITH_TEST_POSTGRES_URL=GITHUB_DATABASE_URL
        ),
        lambda drone, github: github["jobs"]["backend-g002"]["services"]["postgres"][
            "env"
        ].update(POSTGRES_DB="postgres"),
        lambda drone, github: github["jobs"]["backend-g002"]["services"]["postgres"].pop(
            "options"
        ),
    ],
)
def test_ci_rejects_missing_or_miswired_g003_postgres_contract(mutation) -> None:
    drone = copy.deepcopy(_load(DRONE_PATH))
    github = copy.deepcopy(_load(GITHUB_PATH))
    mutation(drone, github)

    with pytest.raises(CiContractError):
        _validate_ci(drone, github)
