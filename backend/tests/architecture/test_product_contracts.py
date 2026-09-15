from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import subprocess
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[2]
CANONICAL_MANIFEST = BACKEND_ROOT / "rewrite" / "product-contracts.json"
SCRIPT_PATH = BACKEND_ROOT / "scripts" / "check_product_contracts.py"
SPEC = importlib.util.spec_from_file_location("check_product_contracts", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
contracts = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(contracts)


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _complete_auth_contract(tmp_path: Path, *, track_artifact: bool = True) -> tuple[Path, dict]:
    manifest = _read(CANONICAL_MANIFEST)
    artifact = tmp_path / "specs" / "backend-products" / "auth.md"
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_text("# Approved Auth contract\n", encoding="utf-8")
    subprocess.run(["git", "init", "--quiet"], cwd=tmp_path, check=True)
    if track_artifact:
        subprocess.run(["git", "add", "--", "specs/backend-products/auth.md"], cwd=tmp_path, check=True)
    evidence = tmp_path / "auth-review.txt"
    evidence.write_text("product and architecture review passed\n", encoding="utf-8")
    auth = manifest["modules"][0]
    auth.update(
        {
            "state": "contract_approved",
            "contract_artifact": "specs/backend-products/auth.md",
            "contract_hash": hashlib.sha256(artifact.read_bytes()).hexdigest(),
            "evidence": [{"path": str(evidence), "sha256": hashlib.sha256(evidence.read_bytes()).hexdigest()}],
            "actors": ["tenant member"],
            "product_workflow": ["sign in and establish the tenant principal"],
            "persistence": ["account and membership records"],
            "api_events": ["POST /auth/login"],
            "authorization": ["public login followed by tenant-scoped access"],
            "failure_behavior": ["invalid credentials fail without a session"],
            "consumers": ["web application"],
            "endpoint_mapping": ["http.auth.login"],
            "acceptance_tests": ["tests/modules/auth/test_login.py"],
            "explicit_deletions": ["none"],
        }
    )
    manifest_path = tmp_path / "product-contracts.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    return manifest_path, manifest


def test_complete_approved_product_contract_passes(tmp_path: Path) -> None:
    manifest_path, _ = _complete_auth_contract(tmp_path)

    contracts.check_product_contract(manifest_path, "auth")


def test_unreviewed_canonical_product_contract_fails_closed() -> None:
    with pytest.raises(contracts.ProductContractError, match="not approved"):
        contracts.check_product_contract(CANONICAL_MANIFEST, "auth")


def test_unreviewed_product_contract_cannot_carry_approval_data(tmp_path: Path) -> None:
    manifest = _read(CANONICAL_MANIFEST)
    manifest["modules"][0]["actors"] = ["tenant member"]

    with pytest.raises(contracts.ProductContractError, match="unreviewed product contract has approval data"):
        contracts.validate_roster(manifest)


def test_roster_integrity_check_does_not_approve_unresolved_product_behavior(tmp_path: Path) -> None:
    manifest_path, manifest = _complete_auth_contract(tmp_path)
    manifest["modules"][0]["actors"] = None
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    contracts.validate_roster(_read(manifest_path), manifest_path)
    with pytest.raises(contracts.ProductContractError, match="unresolved fields.*actors"):
        contracts.check_product_contract(manifest_path, "auth")


def test_product_contract_requires_the_portable_tracked_specs_path(tmp_path: Path) -> None:
    manifest_path, _ = _complete_auth_contract(tmp_path, track_artifact=False)
    with pytest.raises(contracts.ProductContractError, match="artifact is not tracked"):
        contracts.check_product_contract(manifest_path, "auth")

    manifest_path, manifest = _complete_auth_contract(tmp_path)
    tracked_artifact = tmp_path / manifest["modules"][0]["contract_artifact"]
    ignored_artifact = tmp_path / ".omx" / "specs" / "backend-products" / "auth.md"
    ignored_artifact.parent.mkdir(parents=True)
    ignored_artifact.write_text(tracked_artifact.read_text(encoding="utf-8"), encoding="utf-8")
    manifest["modules"][0]["contract_artifact"] = ".omx/specs/backend-products/auth.md"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(contracts.ProductContractError, match="repository-relative path specs/backend-products/auth.md"):
        contracts.check_product_contract(manifest_path, "auth")


@pytest.mark.parametrize("field", contracts.RESOLUTION_FIELDS)
def test_every_product_contract_field_is_required(tmp_path: Path, field: str) -> None:
    manifest_path, manifest = _complete_auth_contract(tmp_path)
    manifest["modules"][0][field] = None
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(contracts.ProductContractError, match=field):
        contracts.check_product_contract(manifest_path, "auth")


def test_product_contract_rejects_hash_drift(tmp_path: Path) -> None:
    manifest_path, manifest = _complete_auth_contract(tmp_path)
    (tmp_path / manifest["modules"][0]["contract_artifact"]).write_text("changed\n", encoding="utf-8")

    with pytest.raises(contracts.ProductContractError, match="artifact hash mismatch"):
        contracts.check_product_contract(manifest_path, "auth")


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda rows: rows.pop(), "missing modules"),
        (lambda rows: rows.append(copy.deepcopy(rows[0])), "duplicate product module"),
        (lambda rows: rows[0].update(owner_id="identity_tenant"), "product owner mismatch"),
        (lambda rows: rows[0].update(module_id="unknown"), "extra product module"),
    ],
)
def test_product_contract_rejects_roster_drift(tmp_path: Path, mutation, message: str) -> None:
    manifest = _read(CANONICAL_MANIFEST)
    mutation(manifest["modules"])
    manifest_path = tmp_path / "product-contracts.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(contracts.ProductContractError, match=message):
        contracts.validate_roster(_read(manifest_path))
