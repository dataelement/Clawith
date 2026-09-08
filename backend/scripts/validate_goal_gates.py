"""Validate the cumulative clean-rewrite Goal checkpoint contract.

Run from ``backend/``::

    uv run python scripts/validate_goal_gates.py \
        --manifest rewrite/goal-gates.json

The manifest declares gates and future evidence paths. It does not execute a gate,
record runtime state, or replay mutating commands.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
from types import ModuleType
from typing import Any

EXPECTED_GOALS = tuple(f"G{number:03d}" for number in range(10))
EXPECTED_PHASES = {
    "G000": [0],
    "G001": [0],
    "G002": [1],
    "G003": [2],
    "G004": [3],
    "G005": [4],
    "G006": [5],
    "G007": [6],
    "G008": [7, 8],
    "G009": [8],
}
EXPECTED_E2E_LEVELS = {
    "G000": "unavailable",
    "G001": "unavailable",
    "G002": "unavailable",
    "G003": "unavailable",
    "G004": "unavailable",
    "G005": "core_runtime",
    "G006": "product_input",
    "G007": "module_cumulative",
    "G008": "complete_backend",
    "G009": "complete_backend",
}
EXPECTED_SCHEMA_OWNERS = {
    "G003": ["identity_tenant", "credential", "model", "agent", "permission", "auth", "audit", "run", "context"],
    "G004": [
        "workspace",
        "tool",
        "capability_market",
        "session",
        "a2a",
        "group",
        "trigger",
        "heartbeat",
        "channel",
    ],
}
EXPECTED_APPROVALS = {
    "G003": [
        "identity_tenant",
        "credential",
        "model",
        "agent",
        "permission",
        "auth",
        "audit",
        "workspace",
        "tool",
        "capability_market",
        "context",
        "run",
    ],
    "G004": ["session", "a2a", "group", "trigger", "heartbeat", "channel"],
}
EXPECTED_IMPLEMENTATION_OWNERS = {
    "G000": [],
    "G001": [],
    "G002": [],
    "G003": ["identity_tenant", "credential", "model", "agent", "permission", "auth", "audit"],
    "G004": ["workspace", "tool", "capability_market", "model"],
    "G005": ["run", "context"],
    "G006": ["session", "a2a", "group", "trigger", "heartbeat", "channel"],
    "G007": ["auth", "S3-approved-owner"],
    "G008": [],
    "G009": [],
}
EXPECTED_MUTATIONS = {
    "G000": [],
    "G001": [],
    "G002": [],
    "G003": [
        "approve-identity-tenant-contract-only",
        "approve-credential-contract-only",
        "approve-model-contract-only",
        "approve-agent-contract-only",
        "approve-permission-contract-only",
        "approve-auth-contract-only",
        "approve-audit-contract-only",
        "approve-workspace-contract-only",
        "approve-tool-contract-only",
        "approve-capability-market-contract-only",
        "approve-context-contract-only",
        "approve-run-contract-only",
    ],
    "G004": [
        "approve-session-contract-only",
        "approve-a2a-contract-only",
        "approve-group-contract-only",
        "approve-trigger-contract-only",
        "approve-heartbeat-contract-only",
        "approve-channel-contract-only",
    ],
    "G005": [],
    "G006": [],
    "G007": ["approve-s3-owner-contract", "transition-coverage-row"],
    "G008": ["release-legacy-reference"],
    "G009": [],
}
EXPECTED_VALIDATION_ARTIFACTS = {
    "G000": ["backend/rewrite/goal-gates.json"],
    "G001": [
        "backend/artifacts/rewrite/G001/coverage-disposition.json",
        "backend/artifacts/rewrite/G001/governance.txt",
        "backend/artifacts/rewrite/G001/owner-dag-wave-roster.json",
        "backend/artifacts/rewrite/G001/product-roster-linkage.json",
        "backend/artifacts/rewrite/G001/strict-load-profile.txt",
        "backend/artifacts/rewrite/G001/legacy-reference.json",
    ],
    "G002": [
        "backend/artifacts/rewrite/G002/architecture.txt",
        "backend/artifacts/rewrite/G002/ruff.txt",
        "backend/artifacts/rewrite/G002/pyright.txt",
        "backend/artifacts/rewrite/G002/pytest-collection.txt",
    ],
    "G003": [
        "backend/artifacts/rewrite/G003/owner-contract-check.txt",
        "backend/artifacts/rewrite/G003/foundation-tests.txt",
    ],
    "G004": [
        "backend/artifacts/rewrite/G004/owner-contract-check.txt",
        "backend/artifacts/rewrite/G004/execution-dependency-tests.txt",
    ],
    "G005": [
        "backend/artifacts/rewrite/G005/core-runtime-e2e.txt",
        "backend/artifacts/performance/core.json",
    ],
    "G006": [
        "backend/artifacts/rewrite/G006/product-input-e2e.txt",
        "backend/artifacts/performance/mixed.json",
    ],
    "G007": [
        "backend/artifacts/rewrite/G007/auth-product-contract.txt",
        "backend/artifacts/rewrite/G007/cumulative-module-e2e.txt",
        "backend/artifacts/rewrite/G007/coverage-progress.json",
    ],
    "G008": [
        "backend/artifacts/rewrite/G008/fresh-e2e-before-reference-removal.txt",
        "backend/artifacts/rewrite/G008/terminal-coverage.json",
        "backend/artifacts/rewrite/G008/fresh-e2e-after-reference-removal.txt",
    ],
    "G009": [
        "backend/artifacts/rewrite/G009/complete-backend-pytest.txt",
        "backend/artifacts/rewrite/G009/complete-backend-ruff.txt",
        "backend/artifacts/rewrite/G009/complete-backend-pyright.txt",
        "backend/artifacts/rewrite/G009/deployment-recovery.txt",
        "backend/artifacts/performance/final.json",
    ],
}


def _approval_receipt_argument(goal_id: str, owner_id: str) -> str:
    owner_slug = owner_id.replace("_", "-")
    return f" --approval-receipt backend/artifacts/rewrite/{goal_id}/receipts/{owner_slug}-contract-approval.json"


G003_APPROVAL_RECEIPT_ARGUMENTS = "".join(
    _approval_receipt_argument("G003", owner_id) for owner_id in EXPECTED_APPROVALS["G003"]
)
G004_APPROVAL_RECEIPT_ARGUMENTS = "".join(
    _approval_receipt_argument("G004", owner_id) for owner_id in EXPECTED_APPROVALS["G004"]
)


EXPECTED_VALIDATION_COMMANDS = {
    "G000": {
        "goal-gate-contract": "uv run python scripts/validate_goal_gates.py --manifest rewrite/goal-gates.json",
    },
    "G001": {
        "coverage-disposition-state": "uv run python scripts/rewrite_inventory.py check --manifest rewrite/coverage.json --require-zero-unreviewed --require-zero-disposition-missing",
        "governance": "uv run --extra dev pytest tests/architecture/test_governance.py tests/architecture/test_module_boundaries.py",
        "owner-dag-and-wave-roster": "uv run python scripts/check_owner_contracts.py check --manifest rewrite/owner-contracts.json",
        "product-roster-and-linkage": "uv run python scripts/validate_goal_gates.py --manifest rewrite/goal-gates.json --check-product-roster-and-linkage",
        "strict-load-profile": "uv run python scripts/validate_load_profile.py tests/performance/profiles/backend_50.json",
        "immutable-reference": "bash ../scripts/check-g001-reference.sh",
    },
    "G002": {
        "architecture": "uv run --extra dev pytest tests/architecture",
        "ruff": "uv run --extra dev ruff check app tests",
        "pyright": "uv run --extra dev pyright app",
        "full-test-collection-disposition": "uv run --extra dev pytest --collect-only",
    },
    "G003": {
        "foundation-contract-prerequisites": "uv run python scripts/check_owner_contracts.py check --manifest rewrite/owner-contracts.json --require-approved-owner run --require-approved-owner context --require-approved-wave S0 --require-approved-wave S1"
        + G003_APPROVAL_RECEIPT_ARGUMENTS,
        "foundation-schema-and-integration": "uv run --extra dev pytest tests/database tests/modules/identity_tenant tests/modules/credential tests/modules/model tests/modules/agent tests/modules/permission tests/modules/auth tests/modules/audit",
    },
    "G004": {
        "product-input-contract-prerequisites": "uv run python scripts/check_owner_contracts.py check --manifest rewrite/owner-contracts.json --require-approved-owner session --require-approved-owner a2a --require-approved-owner group --require-approved-owner trigger --require-approved-owner heartbeat --require-approved-owner channel --require-approved-wave S2"
        + G003_APPROVAL_RECEIPT_ARGUMENTS
        + G004_APPROVAL_RECEIPT_ARGUMENTS,
        "execution-dependency-integration": "uv run --extra dev pytest tests/database/test_schema_wave_S2.py tests/modules/workspace tests/modules/tool tests/modules/capability_market tests/modules/model/test_execution.py tests/modules/model/test_continuation.py",
    },
    "G005": {
        "core-runtime-real-entry": "uv run --extra dev pytest tests/runtime tests/e2e/test_runtime_product_owner_fixture.py tests/performance/test_execution_scheduler_fairness.py",
        "core-runtime-load": "uv run python tests/performance/run_backend_load.py --profile tests/performance/profiles/backend_50.json --scenario core --out artifacts/performance/core.json",
    },
    "G006": {
        "product-input-real-entry": "uv run --extra dev pytest tests/modules/session tests/modules/a2a tests/modules/group tests/modules/trigger tests/modules/heartbeat tests/modules/channel tests/e2e/test_direct_session.py tests/e2e/test_product_inputs.py",
        "mixed-product-input-load": "uv run python tests/performance/run_backend_load.py --profile tests/performance/profiles/backend_50.json --scenario mixed --out artifacts/performance/mixed.json",
    },
    "G007": {
        "auth-product-contract": "uv run python scripts/check_product_contracts.py --manifest rewrite/product-contracts.json --module auth",
        "all-implemented-module-e2e": "uv run --extra dev pytest tests/e2e",
        "coverage-terminal-progress": "uv run python scripts/rewrite_inventory.py check --manifest rewrite/coverage.json",
    },
    "G008": {
        "fresh-environment-e2e-before-reference-removal": "uv run --extra dev pytest tests/database/test_fresh_baseline.py tests/e2e",
        "terminal-coverage-before-reference-removal": "uv run python scripts/rewrite_inventory.py check --manifest rewrite/coverage.json --require-all-terminal",
        "fresh-environment-e2e-after-reference-removal": "uv run --extra dev pytest tests/database/test_fresh_baseline.py tests/e2e",
    },
    "G009": {
        "complete-backend-pytest": "uv run --extra dev pytest",
        "complete-backend-ruff": "uv run --extra dev ruff check .",
        "complete-backend-pyright": "uv run --extra dev pyright app",
        "deployment-and-recovery": "uv run --extra dev pytest tests/deployment/test_single_runner_topology.py tests/deployment/test_readiness.py tests/recovery/test_target_snapshot_restore.py tests/recovery/test_fix_forward.py",
        "final-load": "uv run python tests/performance/run_backend_load.py --profile tests/performance/profiles/backend_50.json --scenario final --out artifacts/performance/final.json",
    },
}
EXPECTED_REQUIRED_PATHS = {
    "G000": [
        ".agents/notes/proposed/architecture/2026-08-28-target-agent-execution-architecture.md",
        ".agents/notes/proposed/architecture/2026-08-27-agent-runner-lifecycle-and-history.md",
        ".agents/notes/proposed/architecture/2026-08-28-capacity-performance-and-responsiveness.md",
        ".agents/notes/proposed/architecture/2026-08-28-product-input-main-run-and-output-boundaries.md",
        ".agents/notes/proposed/testing/2026-09-02-cumulative-goal-checkpoints.md",
        "backend/rewrite/goal-gates.json",
    ],
    "G001": [
        "backend/rewrite/coverage.json",
        "backend/rewrite/owner-contracts.json",
        "backend/rewrite/product-contracts.json",
        "backend/rewrite/owner-dag.json",
        "backend/rewrite/legacy-black-box.json",
        "scripts/check-g001-reference.sh",
        "backend/tests/performance/profiles/backend_50.json",
    ],
    "G002": [
        "backend/app/application.py",
        "backend/app/infrastructure/database.py",
        "backend/tests/architecture/test_application_composition.py",
        "backend/tests/architecture/test_import_boundaries.py",
        "backend/tests/architecture/test_module_boundaries.py",
    ],
    "G003": [
        "backend/rewrite/owner-contracts.json",
        "backend/tests/database/test_schema_wave_S0.py",
        "backend/tests/database/test_schema_wave_S1.py",
        "backend/tests/compose.postgres.yml",
    ],
    "G004": [
        "backend/rewrite/owner-contracts.json",
        "backend/tests/database/test_schema_wave_S2.py",
        "backend/tests/modules/workspace",
        "backend/tests/modules/tool",
        "backend/tests/modules/capability_market",
    ],
    "G005": [
        "backend/tests/e2e/test_runtime_product_owner_fixture.py",
        "backend/tests/performance/profiles/backend_50.json",
        "backend/tests/performance/test_execution_scheduler_fairness.py",
    ],
    "G006": [
        "backend/tests/e2e/test_direct_session.py",
        "backend/tests/e2e/test_product_inputs.py",
        "backend/tests/performance/profiles/backend_50.json",
    ],
    "G007": [
        "backend/rewrite/coverage.json",
        "backend/rewrite/owner-contracts.json",
        "backend/rewrite/product-contracts.json",
        "backend/tests/e2e",
    ],
    "G008": [
        "backend/tests/e2e",
        "backend/tests/database/test_fresh_baseline.py",
        "backend/rewrite/coverage.json",
        "backend/rewrite/legacy-black-box.json",
    ],
    "G009": [
        "backend/tests/e2e",
        "backend/tests/deployment/test_single_runner_topology.py",
        "backend/tests/deployment/test_readiness.py",
        "backend/tests/recovery/test_target_snapshot_restore.py",
        "backend/tests/recovery/test_fix_forward.py",
        "backend/tests/performance/profiles/backend_50.json",
    ],
}
EXPECTED_HOSTILE_FAIRNESS = {
    "scheduler": "in_memory_tenant_then_agent_execution_scheduler",
    "boundary": "after_each_bounded_model_step_or_tool_batch",
    "initial_tenant_a_runs": 50,
    "tenant_a_runs": "continuously_runnable_nonterminating",
    "tenant_b_expectation": "next_model_step_within_scheduler_bound",
    "max_consecutive_eligible_tenant_skips": 1,
    "fifo_scope": "per_agent",
    "terminal_cleanup": ["cancellation_removes_run", "failure_removes_run", "execution_slot_released"],
    "excluded_authorities": [
        "initial_admission_queue",
        "persisted_queue",
        "checkpoint",
        "durable_scheduler_state",
        "whole_run_limit",
    ],
}
REPLAY_POLICY = "verify_receipt_before_execute"
APPROVAL_COMMAND = (
    "uv run python scripts/check_owner_contracts.py approve --manifest rewrite/owner-contracts.json "
    "--owner {owner} --contract-artifact <path> --evidence <path> --receipt {receipt}"
)
EXPECTED_MUTATION_COMMANDS = {
    "G003": {
        f"approve-{owner.replace('_', '-')}-contract-only": APPROVAL_COMMAND.format(
            owner=owner,
            receipt=f"backend/artifacts/rewrite/G003/receipts/{owner.replace('_', '-')}-contract-approval.json",
        )
        for owner in EXPECTED_APPROVALS["G003"]
    },
    "G004": {
        f"approve-{owner.replace('_', '-')}-contract-only": APPROVAL_COMMAND.format(
            owner=owner,
            receipt=f"backend/artifacts/rewrite/G004/receipts/{owner.replace('_', '-')}-contract-approval.json",
        )
        for owner in EXPECTED_APPROVALS["G004"]
    },
    "G007": {
        "approve-s3-owner-contract": APPROVAL_COMMAND.format(
            owner="<owner>",
            receipt="backend/artifacts/rewrite/G007/receipts/<owner>-contract-approval.json",
        ),
        "transition-coverage-row": "uv run python scripts/rewrite_inventory.py transition --manifest rewrite/coverage.json --id <id> --to <state> --evidence <path>",
    },
    "G008": {
        "release-legacy-reference": "uv run python scripts/rewrite_inventory.py release-reference --manifest rewrite/coverage.json --require-all-terminal --worktree <legacy-path>",
    },
}
EXPECTED_RELEASE_MUTATION = {
    "id": "release-legacy-reference",
    "command": EXPECTED_MUTATION_COMMANDS["G008"]["release-legacy-reference"],
    "receipt": "backend/artifacts/rewrite/G008/receipts/legacy-reference-removal.json",
    "replay_policy": REPLAY_POLICY,
    "requires_artifact": "backend/artifacts/rewrite/G008/fresh-e2e-before-reference-removal.txt",
    "followed_by_artifact": "backend/artifacts/rewrite/G008/fresh-e2e-after-reference-removal.txt",
}


class GateContractError(ValueError):
    """A deterministic Goal-gate contract validation failure."""


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise GateContractError(f"manifest does not exist: {path}") from exc
    except json.JSONDecodeError as exc:
        raise GateContractError(f"invalid JSON in {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise GateContractError("goal-gate manifest must be an object")
    return value


def _list(value: Any, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise GateContractError(f"{label} must be a list")
    return value


def _load_sibling_script(name: str) -> ModuleType:
    path = Path(__file__).with_name(name)
    spec = importlib.util.spec_from_file_location(f"_goal_gate_{path.stem}", path)
    if spec is None or spec.loader is None:
        raise GateContractError(f"cannot load phase-0 checker: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def check_product_roster_and_linkage(manifest_path: Path) -> None:
    owner_checker = _load_sibling_script("check_owner_contracts.py")
    product_checker = _load_sibling_script("check_product_contracts.py")
    owner_path = manifest_path.with_name("owner-contracts.json")
    product_path = manifest_path.with_name("product-contracts.json")
    try:
        product_checker.validate_roster(json.loads(product_path.read_text(encoding="utf-8")), product_path)
        owner_checker.validate_manifest(json.loads(owner_path.read_text(encoding="utf-8")), owner_path)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        raise GateContractError(f"product roster or linkage is invalid: {exc}") from exc


def _validate_policy(manifest: dict[str, Any]) -> list[str]:
    policy = manifest.get("policy")
    if not isinstance(policy, dict):
        raise GateContractError("policy must be an object")
    expected = {
        "cumulative": True,
        "validation_commands_are_repeatable": True,
        "mutations_require_receipts": True,
        "mutation_replay_policy": REPLAY_POLICY,
        "e2e_levels": [
            "unavailable",
            "core_runtime",
            "product_input",
            "module_cumulative",
            "complete_backend",
        ],
    }
    if policy != expected:
        raise GateContractError("goal-gate policy differs from the canonical cumulative contract")
    return expected["e2e_levels"]


def _validate_goal(goal: dict[str, Any], index: int, levels: list[str]) -> None:
    goal_id = EXPECTED_GOALS[index]
    if goal.get("phase_crosswalk") != EXPECTED_PHASES[goal_id]:
        raise GateContractError(f"phase crosswalk mismatch for {goal_id}")
    if goal.get("carries_forward") != list(EXPECTED_GOALS[:index]):
        raise GateContractError(f"cumulative carry-forward mismatch for {goal_id}")
    if goal.get("e2e_level") != EXPECTED_E2E_LEVELS[goal_id]:
        current_level = goal.get("e2e_level")
        expected_level = EXPECTED_E2E_LEVELS[goal_id]
        if current_level in levels and levels.index(current_level) < levels.index(expected_level):
            raise GateContractError(f"E2E level regresses at {goal_id}")
        raise GateContractError(f"E2E level mismatch for {goal_id}")
    required_paths = goal.get("required_paths")
    if isinstance(required_paths, list) and any(
        isinstance(path, str) and path.startswith(".omx/") for path in required_paths
    ):
        raise GateContractError(f"ignored .omx path cannot be canonical evidence for {goal_id}")
    if goal.get("required_paths") != EXPECTED_REQUIRED_PATHS[goal_id]:
        raise GateContractError(f"required paths mismatch for {goal_id}")
    expected_approvals = EXPECTED_APPROVALS.get(goal_id, [])
    if goal.get("contract_approval_owners") != expected_approvals:
        raise GateContractError(f"contract approvals mismatch for {goal_id}")
    if goal_id in EXPECTED_SCHEMA_OWNERS:
        if goal.get("schema_owners") != EXPECTED_SCHEMA_OWNERS[goal_id]:
            raise GateContractError(f"schema owners mismatch for {goal_id}")
    elif "schema_owners" in goal:
        raise GateContractError(f"schema owners are not allowed for {goal_id}")
    if goal.get("implementation_owners") != EXPECTED_IMPLEMENTATION_OWNERS[goal_id]:
        raise GateContractError(f"implementation owners mismatch for {goal_id}")
    if goal_id == "G005" and goal.get("hostile_fairness_test") != EXPECTED_HOSTILE_FAIRNESS:
        raise GateContractError("hostile fairness contract mismatch for G005")

    validations = _list(goal.get("validations"), f"validations for {goal_id}")
    if not validations:
        raise GateContractError(f"at least one validation is required for {goal_id}")
    validation_id_roster = [
        validation.get("id") if isinstance(validation, dict) else None for validation in validations
    ]
    if validation_id_roster != list(EXPECTED_VALIDATION_COMMANDS[goal_id]):
        raise GateContractError(f"validation roster mismatch for {goal_id}")
    validation_ids: set[str] = set()
    for validation in validations:
        if not isinstance(validation, dict):
            raise GateContractError(f"validation entry must be an object for {goal_id}")
        validation_id = validation.get("id")
        command = validation.get("command")
        artifacts = validation.get("artifacts")
        if not isinstance(validation_id, str) or not validation_id or validation_id in validation_ids:
            raise GateContractError(f"validation ids must be unique non-empty strings for {goal_id}")
        validation_ids.add(validation_id)
        if not isinstance(command, str) or not command:
            raise GateContractError(f"validation command is required for {goal_id}")
        if command != EXPECTED_VALIDATION_COMMANDS[goal_id][validation_id]:
            raise GateContractError(f"validation command mismatch for {goal_id}: {validation_id}")
        if not isinstance(artifacts, list) or not artifacts or not all(isinstance(path, str) and path for path in artifacts):
            raise GateContractError(f"validation artifacts are required for {goal_id}: {validation_id}")
    artifact_paths = [path for validation in validations for path in validation["artifacts"]]
    if artifact_paths != EXPECTED_VALIDATION_ARTIFACTS[goal_id]:
        raise GateContractError(f"validation artifact paths mismatch for {goal_id}")

    mutations = _list(goal.get("mutations"), f"mutations for {goal_id}")
    mutation_id_roster = [mutation.get("id") if isinstance(mutation, dict) else None for mutation in mutations]
    if mutation_id_roster != EXPECTED_MUTATIONS[goal_id]:
        raise GateContractError(f"mutations mismatch for {goal_id}")
    mutation_ids: set[str] = set()
    if goal_id == "G008" and mutations != [EXPECTED_RELEASE_MUTATION]:
        raise GateContractError("release mutation mismatch for G008")
    for mutation in mutations:
        if not isinstance(mutation, dict):
            raise GateContractError(f"mutation entry must be an object for {goal_id}")
        mutation_id = mutation.get("id")
        command = mutation.get("command")
        if not isinstance(mutation_id, str) or not mutation_id or mutation_id in mutation_ids:
            raise GateContractError(f"mutation ids must be unique non-empty strings for {goal_id}")
        mutation_ids.add(mutation_id)
        expected_commands = EXPECTED_MUTATION_COMMANDS.get(goal_id, {})
        if not isinstance(command, str) or command != expected_commands.get(mutation_id):
            raise GateContractError(f"mutation command mismatch for {goal_id}: {mutation_id}")
        if not isinstance(mutation.get("receipt"), str) or not mutation["receipt"]:
            raise GateContractError(f"mutation receipt is required for {goal_id}: {mutation_id}")
        if mutation.get("replay_policy") != REPLAY_POLICY:
            raise GateContractError(f"mutation receipt guard mismatch for {goal_id}: {mutation_id}")
    if goal_id == "G008":
        artifacts_by_validation = {
            validation["id"]: validation["artifacts"][0] for validation in validations
        }
        if EXPECTED_RELEASE_MUTATION["requires_artifact"] != artifacts_by_validation[
            "fresh-environment-e2e-before-reference-removal"
        ]:
            raise GateContractError("release precondition artifact is not produced by the before-removal validation")
        if EXPECTED_RELEASE_MUTATION["followed_by_artifact"] != artifacts_by_validation[
            "fresh-environment-e2e-after-reference-removal"
        ]:
            raise GateContractError("release follow-up artifact is not produced by the after-removal validation")


def validate_manifest(path: Path) -> None:
    manifest = _load_json(path)
    if manifest.get("version") != 1:
        raise GateContractError("goal-gate manifest version must be 1")
    levels = _validate_policy(manifest)
    goals = _list(manifest.get("goals"), "goals")
    goal_ids = [goal.get("id") if isinstance(goal, dict) else None for goal in goals]
    if goal_ids != list(EXPECTED_GOALS):
        raise GateContractError("goals must be exactly G000 through G009 in order")
    for index, goal in enumerate(goals):
        assert isinstance(goal, dict)
        _validate_goal(goal, index, levels)
    level_indexes = [levels.index(EXPECTED_E2E_LEVELS[goal_id]) for goal_id in EXPECTED_GOALS]
    if level_indexes != sorted(level_indexes):
        raise GateContractError("E2E levels must be monotonic")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=Path("rewrite/goal-gates.json"))
    parser.add_argument("--check-product-roster-and-linkage", action="store_true")
    return parser


def main() -> int:
    args = _parser().parse_args()
    try:
        validate_manifest(args.manifest)
        if args.check_product_roster_and_linkage:
            check_product_roster_and_linkage(args.manifest)
    except GateContractError as exc:
        print(f"goal-gate validation failed: {exc}")
        return 1
    suffix = " and product roster/linkage are valid" if args.check_product_roster_and_linkage else " is valid"
    print(f"goal-gate validation passed: G000-G009 cumulative contract{suffix}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
