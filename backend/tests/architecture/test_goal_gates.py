from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[2]
MANIFEST_PATH = BACKEND_ROOT / "rewrite" / "goal-gates.json"
SCRIPT_PATH = BACKEND_ROOT / "scripts" / "validate_goal_gates.py"
SPEC = importlib.util.spec_from_file_location("validate_goal_gates", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
goal_gates = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(goal_gates)

EXPECTED_GOALS = [f"G{number:03d}" for number in range(10)]
G003_SCHEMA_OWNERS = [
    "identity_tenant",
    "credential",
    "model",
    "agent",
    "permission",
    "auth",
    "audit",
    "run",
    "context",
]
G004_SCHEMA_OWNERS = [
    "workspace",
    "tool",
    "capability_market",
    "session",
    "a2a",
    "group",
    "trigger",
    "heartbeat",
    "channel",
]
G003_APPROVAL_OWNERS = [
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
    "run",
    "context",
]
G004_APPROVAL_OWNERS = ["session", "a2a", "group", "trigger", "heartbeat", "channel"]


def _manifest() -> dict:
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


def _write_manifest(tmp_path: Path, manifest: dict) -> Path:
    path = tmp_path / "goal-gates.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    return path


def test_canonical_manifest_passes_validation() -> None:
    goal_gates.validate_manifest(MANIFEST_PATH)


def test_phase_zero_product_roster_and_linkage_pass_current_ledgers() -> None:
    goal_gates.check_product_roster_and_linkage(MANIFEST_PATH)


def test_g001_checks_product_integrity_while_g007_requires_auth_semantic_approval() -> None:
    manifest = _manifest()
    g001 = {validation["id"]: validation["command"] for validation in manifest["goals"][1]["validations"]}
    g007 = {validation["id"]: validation["command"] for validation in manifest["goals"][7]["validations"]}

    assert g001["product-roster-and-linkage"].endswith("--check-product-roster-and-linkage")
    assert g007["auth-product-contract"] == (
        "uv run python scripts/check_product_contracts.py "
        "--manifest rewrite/product-contracts.json --module auth"
    )
    assert manifest["goals"][7]["implementation_owners"] == ["auth", "S3-approved-owner"]
    assert manifest["goals"][7]["contract_approval_owners"] == []


def test_validator_rejects_missing_or_replaced_g007_auth_product_prerequisite(tmp_path: Path) -> None:
    manifest = _manifest()
    manifest["goals"][7]["validations"].pop(0)
    with pytest.raises(goal_gates.GateContractError, match="validation roster mismatch for G007"):
        goal_gates.validate_manifest(_write_manifest(tmp_path, manifest))

    manifest = _manifest()
    manifest["goals"][7]["validations"][0]["command"] = (
        "uv run python scripts/check_product_contracts.py "
        "--manifest rewrite/product-contracts.json --module sso"
    )
    with pytest.raises(
        goal_gates.GateContractError,
        match="validation command mismatch for G007: auth-product-contract",
    ):
        goal_gates.validate_manifest(_write_manifest(tmp_path, manifest))


def test_validator_rejects_goal_roster_or_order_drift(tmp_path: Path) -> None:
    manifest = _manifest()
    manifest["goals"][3], manifest["goals"][4] = manifest["goals"][4], manifest["goals"][3]

    with pytest.raises(goal_gates.GateContractError, match="exactly G000 through G009 in order"):
        goal_gates.validate_manifest(_write_manifest(tmp_path, manifest))


def test_validator_rejects_missing_cumulative_carry_forward(tmp_path: Path) -> None:
    manifest = _manifest()
    manifest["goals"][6]["carries_forward"].remove("G003")

    with pytest.raises(goal_gates.GateContractError, match="cumulative carry-forward mismatch for G006"):
        goal_gates.validate_manifest(_write_manifest(tmp_path, manifest))


def test_validator_rejects_unknown_validation_entry(tmp_path: Path) -> None:
    manifest = _manifest()
    manifest["goals"][3]["validations"].append(
        {
            "id": "approve-run-contract",
            "command": "uv run python scripts/check_owner_contracts.py approve --owner run",
            "artifacts": ["backend/rewrite/owner-contracts.json"],
        }
    )

    with pytest.raises(goal_gates.GateContractError, match="validation roster mismatch for G003"):
        goal_gates.validate_manifest(_write_manifest(tmp_path, manifest))


def test_validator_rejects_mutation_without_receipt_guard(tmp_path: Path) -> None:
    manifest = _manifest()
    manifest["goals"][3]["mutations"][0]["receipt"] = None

    with pytest.raises(goal_gates.GateContractError, match="mutation receipt is required for G003"):
        goal_gates.validate_manifest(_write_manifest(tmp_path, manifest))


def test_validator_rejects_e2e_regression(tmp_path: Path) -> None:
    manifest = _manifest()
    manifest["goals"][7]["e2e_level"] = "core_runtime"

    with pytest.raises(goal_gates.GateContractError, match="E2E level regresses at G007"):
        goal_gates.validate_manifest(_write_manifest(tmp_path, manifest))


def test_validator_rejects_missing_required_fixture_path(tmp_path: Path) -> None:
    manifest = _manifest()
    manifest["goals"][5]["required_paths"].remove("backend/tests/performance/profiles/backend_50.json")

    with pytest.raises(goal_gates.GateContractError, match="required paths mismatch for G005"):
        goal_gates.validate_manifest(_write_manifest(tmp_path, manifest))


def test_validator_rejects_ignored_omx_as_canonical_evidence(tmp_path: Path) -> None:
    manifest = _manifest()
    manifest["goals"][0]["required_paths"][0] = ".omx/plans/prd-clean-break-backend-rewrite.md"

    with pytest.raises(goal_gates.GateContractError, match="ignored .omx path cannot be canonical evidence"):
        goal_gates.validate_manifest(_write_manifest(tmp_path, manifest))


def test_validator_rejects_required_artifact_path_drift(tmp_path: Path) -> None:
    manifest = _manifest()
    manifest["goals"][8]["validations"][0]["artifacts"] = ["backend/artifacts/rewrite/G008/unspecified.txt"]

    with pytest.raises(goal_gates.GateContractError, match="validation artifact paths mismatch for G008"):
        goal_gates.validate_manifest(_write_manifest(tmp_path, manifest))


def test_validator_rejects_goal_phase_crosswalk_drift(tmp_path: Path) -> None:
    manifest = _manifest()
    manifest["goals"][8]["phase_crosswalk"] = [7]

    with pytest.raises(goal_gates.GateContractError, match="phase crosswalk mismatch for G008"):
        goal_gates.validate_manifest(_write_manifest(tmp_path, manifest))


def test_validator_requires_contract_only_approvals_before_g003_and_g004(tmp_path: Path) -> None:
    manifest = _manifest()
    broken_g003 = copy.deepcopy(manifest)
    broken_g003["goals"][3]["contract_approval_owners"].remove("context")
    with pytest.raises(goal_gates.GateContractError, match="contract approvals mismatch for G003"):
        goal_gates.validate_manifest(_write_manifest(tmp_path, broken_g003))

    manifest["goals"][4]["contract_approval_owners"].remove("heartbeat")
    with pytest.raises(goal_gates.GateContractError, match="contract approvals mismatch for G004"):
        goal_gates.validate_manifest(_write_manifest(tmp_path, manifest))


def test_validator_requires_complete_schema_wave_rosters(tmp_path: Path) -> None:
    manifest = _manifest()
    assert manifest["goals"][3]["schema_owners"] == G003_SCHEMA_OWNERS
    assert manifest["goals"][4]["schema_owners"] == G004_SCHEMA_OWNERS

    manifest["goals"][3]["schema_owners"].remove("run")
    with pytest.raises(goal_gates.GateContractError, match="schema owners mismatch for G003"):
        goal_gates.validate_manifest(_write_manifest(tmp_path, manifest))


def test_validator_requires_dependency_ordered_contract_approvals(tmp_path: Path) -> None:
    manifest = _manifest()
    assert manifest["goals"][3]["contract_approval_owners"] == G003_APPROVAL_OWNERS
    assert manifest["goals"][4]["contract_approval_owners"] == G004_APPROVAL_OWNERS

    manifest["goals"][3]["contract_approval_owners"].remove("workspace")
    with pytest.raises(goal_gates.GateContractError, match="contract approvals mismatch for G003"):
        goal_gates.validate_manifest(_write_manifest(tmp_path, manifest))

    manifest = _manifest()
    manifest["goals"][4]["contract_approval_owners"].remove("session")
    with pytest.raises(goal_gates.GateContractError, match="contract approvals mismatch for G004"):
        goal_gates.validate_manifest(_write_manifest(tmp_path, manifest))


def test_validator_requires_one_receipted_approval_per_schema_owner(tmp_path: Path) -> None:
    manifest = _manifest()
    for goal_index, expected_owners in ((3, G003_APPROVAL_OWNERS), (4, G004_APPROVAL_OWNERS)):
        mutations = manifest["goals"][goal_index]["mutations"]
        owners = [mutation["command"].split("--owner ", 1)[1].split(" ", 1)[0] for mutation in mutations]
        assert owners == expected_owners
        assert all(mutation["receipt"] for mutation in mutations)
        assert all(mutation["replay_policy"] == "verify_receipt_before_execute" for mutation in mutations)

    manifest["goals"][3]["mutations"].pop()
    with pytest.raises(goal_gates.GateContractError, match="mutations mismatch for G003"):
        goal_gates.validate_manifest(_write_manifest(tmp_path, manifest))


def test_owner_approval_commands_receive_their_declared_receipt() -> None:
    manifest = _manifest()
    for goal_index in (3, 4, 7):
        for mutation in manifest["goals"][goal_index]["mutations"]:
            if "check_owner_contracts.py approve" not in mutation["command"]:
                continue
            assert mutation["command"].endswith(f"--receipt {mutation['receipt']}")


def test_cumulative_owner_checks_receive_every_prior_approval_receipt() -> None:
    manifest = _manifest()
    g003_receipts = [mutation["receipt"] for mutation in manifest["goals"][3]["mutations"]]
    g004_receipts = [mutation["receipt"] for mutation in manifest["goals"][4]["mutations"]]
    g003_check = manifest["goals"][3]["validations"][0]["command"]
    g004_check = manifest["goals"][4]["validations"][0]["command"]

    assert [path for path in g003_receipts if f"--approval-receipt {path}" not in g003_check] == []
    assert [path for path in [*g003_receipts, *g004_receipts] if f"--approval-receipt {path}" not in g004_check] == []


def test_validator_rejects_a_cumulative_check_missing_a_receipt(tmp_path: Path) -> None:
    manifest = _manifest()
    command = manifest["goals"][4]["validations"][0]["command"]
    receipt = manifest["goals"][3]["mutations"][0]["receipt"]
    manifest["goals"][4]["validations"][0]["command"] = command.replace(
        f" --approval-receipt {receipt}", ""
    )

    with pytest.raises(goal_gates.GateContractError, match="validation command mismatch for G004"):
        goal_gates.validate_manifest(_write_manifest(tmp_path, manifest))


def test_validator_rejects_an_approval_command_without_receipt_input(tmp_path: Path) -> None:
    manifest = _manifest()
    command = manifest["goals"][3]["mutations"][0]["command"]
    manifest["goals"][3]["mutations"][0]["command"] = command.split(" --receipt ", 1)[0]

    with pytest.raises(goal_gates.GateContractError, match="mutation command mismatch for G003"):
        goal_gates.validate_manifest(_write_manifest(tmp_path, manifest))


def test_validator_keeps_early_contract_approvals_out_of_implementation_rosters(tmp_path: Path) -> None:
    manifest = _manifest()
    manifest["goals"][3]["implementation_owners"].append("run")

    with pytest.raises(goal_gates.GateContractError, match="implementation owners mismatch for G003"):
        goal_gates.validate_manifest(_write_manifest(tmp_path, manifest))


def test_validator_keeps_g001_validation_only(tmp_path: Path) -> None:
    manifest = _manifest()
    manifest["goals"][1]["mutations"].append(copy.deepcopy(manifest["goals"][3]["mutations"][0]))

    with pytest.raises(goal_gates.GateContractError, match="mutations mismatch for G001"):
        goal_gates.validate_manifest(_write_manifest(tmp_path, manifest))


def test_g001_requires_each_phase_zero_fact_check() -> None:
    validation_ids = [validation["id"] for validation in _manifest()["goals"][1]["validations"]]

    assert validation_ids == [
        "coverage-disposition-state",
        "governance",
        "owner-dag-and-wave-roster",
        "product-roster-and-linkage",
        "strict-load-profile",
        "immutable-reference",
    ]


@pytest.mark.parametrize(
    "command",
    [
        "uv run python scripts/rewrite_inventory.py check --manifest rewrite/coverage.json && touch /tmp/gate",
        "uv run python scripts/rewrite_inventory.py check --manifest rewrite/coverage.json; true",
        "uv run python scripts/unknown.py",
        "uv run alembic upgrade head",
        "uv run python scripts/rewrite_inventory.py bind-reference --manifest rewrite/coverage.json",
        "uv run python scripts/rewrite_inventory.py build --manifest rewrite/coverage.json",
        "uv run python scripts/check_owner_contracts.py approve --manifest rewrite/owner-contracts.json",
        "uv run python scripts/rewrite_inventory.py transition --manifest rewrite/coverage.json",
        "uv run python scripts/rewrite_inventory.py release-reference --manifest rewrite/coverage.json",
        "uv  run python scripts/rewrite_inventory.py check --manifest rewrite/coverage.json",
        "touch backend/artifacts/rewrite/G001/coverage-disposition.json",
    ],
)
def test_validator_accepts_only_the_exact_closed_validation_commands(tmp_path: Path, command: str) -> None:
    manifest = _manifest()
    manifest["goals"][1]["validations"][0]["command"] = command

    with pytest.raises(goal_gates.GateContractError, match="validation command mismatch for G001"):
        goal_gates.validate_manifest(_write_manifest(tmp_path, manifest))


def test_canonical_validation_commands_have_no_shell_operators_or_placeholders() -> None:
    commands = [
        validation["command"]
        for goal in _manifest()["goals"]
        for validation in goal["validations"]
    ]

    assert all(not any(operator in command for operator in ("&&", "||", ";", "\n", ">", "<", "|")) for command in commands)


def test_g008_release_requires_exact_receipt_and_before_after_artifacts(tmp_path: Path) -> None:
    manifest = _manifest()
    release = manifest["goals"][8]["mutations"][0]
    assert release == {
        "id": "release-legacy-reference",
        "command": "uv run python scripts/rewrite_inventory.py release-reference --manifest rewrite/coverage.json --require-all-terminal --worktree <legacy-path>",
        "receipt": "backend/artifacts/rewrite/G008/receipts/legacy-reference-removal.json",
        "replay_policy": "verify_receipt_before_execute",
        "requires_artifact": "backend/artifacts/rewrite/G008/fresh-e2e-before-reference-removal.txt",
        "followed_by_artifact": "backend/artifacts/rewrite/G008/fresh-e2e-after-reference-removal.txt",
    }

    release.pop("requires_artifact")
    with pytest.raises(goal_gates.GateContractError, match="release mutation mismatch for G008"):
        goal_gates.validate_manifest(_write_manifest(tmp_path, manifest))


@pytest.mark.parametrize(
    ("field", "artifact"),
    [
        ("requires_artifact", "backend/artifacts/rewrite/G008/fresh-e2e-after-reference-removal.txt"),
        ("followed_by_artifact", "backend/artifacts/rewrite/G008/fresh-e2e-before-reference-removal.txt"),
        ("requires_artifact", "backend/artifacts/rewrite/G008/missing.txt"),
    ],
)
def test_validator_rejects_reference_removal_order_drift(tmp_path: Path, field: str, artifact: str) -> None:
    manifest = _manifest()
    manifest["goals"][8]["mutations"][0][field] = artifact

    with pytest.raises(goal_gates.GateContractError, match="release mutation mismatch for G008"):
        goal_gates.validate_manifest(_write_manifest(tmp_path, manifest))


def test_validator_rejects_after_removal_validation_before_precondition(tmp_path: Path) -> None:
    manifest = _manifest()
    validations = manifest["goals"][8]["validations"]
    validations[0], validations[2] = validations[2], validations[0]

    with pytest.raises(goal_gates.GateContractError, match="validation roster mismatch for G008"):
        goal_gates.validate_manifest(_write_manifest(tmp_path, manifest))


def test_validator_requires_the_g005_hostile_scheduler_fixture(tmp_path: Path) -> None:
    manifest = _manifest()
    manifest["goals"][5]["hostile_fairness_test"]["initial_tenant_a_runs"] = 49

    with pytest.raises(goal_gates.GateContractError, match="hostile fairness contract mismatch for G005"):
        goal_gates.validate_manifest(_write_manifest(tmp_path, manifest))
