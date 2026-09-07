from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import shlex
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[2]
CANONICAL_DAG = BACKEND_ROOT / "rewrite" / "owner-dag.json"
CANONICAL_PRODUCT_MANIFEST = BACKEND_ROOT / "rewrite" / "product-contracts.json"
CANONICAL_GOAL_GATES = BACKEND_ROOT / "rewrite" / "goal-gates.json"
SCRIPT_PATH = BACKEND_ROOT / "scripts" / "check_owner_contracts.py"
SPEC = importlib.util.spec_from_file_location("check_owner_contracts", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
contracts = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(contracts)


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _built_manifest(tmp_path: Path) -> Path:
    manifest_path = tmp_path / "owner-contracts.json"
    dag_path = tmp_path / "owner-dag.json"
    dag_path.write_text(CANONICAL_DAG.read_text(encoding="utf-8"), encoding="utf-8")
    contracts.build_manifest(manifest_path, dag_path)
    return manifest_path


def _canonical_built_manifest(tmp_path: Path) -> Path:
    rewrite_dir = tmp_path / "repo" / "backend" / "rewrite"
    rewrite_dir.mkdir(parents=True)
    for source in (CANONICAL_DAG, CANONICAL_PRODUCT_MANIFEST, CANONICAL_GOAL_GATES):
        (rewrite_dir / source.name).write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
    manifest_path = rewrite_dir / "owner-contracts.json"
    contracts.build_manifest(manifest_path, rewrite_dir / "owner-dag.json")
    return manifest_path


def _approve(
    manifest_path: Path,
    owner_id: str,
    artifact: Path,
    evidence: Path,
) -> str:
    receipt = manifest_path.parent / "receipts" / f"{owner_id}-contract-approval.json"
    return contracts.approve_owner(
        manifest_path,
        owner_id,
        str(artifact),
        [str(evidence)],
        str(receipt),
    )


def _approval_receipts(manifest_path: Path) -> list[str]:
    return [str(path) for path in sorted((manifest_path.parent / "receipts").glob("*.json"))]


def _approve_declared_owner(manifest_path: Path, owner_id: str) -> str:
    gates = _read(manifest_path.with_name("goal-gates.json"))
    mutation = next(
        mutation
        for goal in gates["goals"]
        for mutation in goal["mutations"]
        if f"--owner {owner_id} " in mutation["command"]
    )
    repository_root = manifest_path.parents[2]
    artifact = repository_root / "specs" / "owner-contracts" / f"{owner_id}.md"
    evidence = repository_root / "specs" / "owner-contracts" / f"{owner_id}-review.txt"
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_text(f"# {owner_id} contract\n", encoding="utf-8")
    evidence.write_text(f"{owner_id} review passed\n", encoding="utf-8")
    return contracts.approve_owner(
        manifest_path,
        owner_id,
        str(artifact),
        [str(evidence)],
        mutation["receipt"],
    )


def _amendment_case(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    manifest = _canonical_built_manifest(tmp_path)
    _approve_declared_owner(manifest, "identity_tenant")
    artifact = tmp_path / "new-contract.md"
    evidence = tmp_path / "new-review.md"
    artifact.write_text("new approved contract", encoding="utf-8")
    evidence.write_text("independent review passed", encoding="utf-8")
    return manifest, artifact, evidence, tmp_path / "amendment.json"


def test_amend_preserves_history_replays_and_survives_build(tmp_path: Path) -> None:
    manifest, artifact, evidence, receipt = _amendment_case(tmp_path)
    initial = contracts._resolve_output_path(
        contracts._declared_approval_receipts(manifest, {"identity_tenant"})["identity_tenant"], manifest
    )
    original = initial.read_bytes()
    args = (manifest, "identity_tenant", str(artifact), [str(evidence)], str(receipt))
    assert contracts.amend_owner(*args) == "applied"
    after = manifest.read_bytes()
    assert contracts.amend_owner(*args) == "replayed"
    contracts.check_manifest(manifest, [], [])
    contracts.build_manifest(manifest, manifest.with_name("owner-dag.json"))
    assert manifest.read_bytes() == after
    assert initial.read_bytes() == original
    second = tmp_path / "second-contract.md"
    second.write_text("second reviewed contract", encoding="utf-8")
    second_receipt = tmp_path / "second-amendment.json"
    assert contracts.amend_owner(manifest, "identity_tenant", str(second), [str(evidence)], str(second_receipt)) == "applied"
    contracts.check_manifest(manifest, [], [])
    assert _read(second_receipt)["previous_receipt_hash"] == hashlib.sha256(receipt.read_bytes()).hexdigest()
    with pytest.raises(contracts.ContractError, match="must use amend"):
        contracts.approve_owner(manifest, "identity_tenant", str(second), [str(evidence)], str(tmp_path / "bypass.json"))


def test_amend_recovers_only_exact_interrupted_request(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manifest, artifact, evidence, receipt = _amendment_case(tmp_path)
    write = contracts._write_json

    def fail_receipt(path: Path, value: dict) -> None:
        if path == receipt:
            raise OSError("interrupted receipt publication")
        write(path, value)

    args = (manifest, "identity_tenant", str(artifact), [str(evidence)], str(receipt))
    with monkeypatch.context() as patch:
        patch.setattr(contracts, "_write_json", fail_receipt)
        with pytest.raises(OSError, match="interrupted"):
            contracts.amend_owner(*args)
    with pytest.raises(contracts.ContractError, match="does not exist"):
        contracts.check_manifest(manifest, [], [])
    changed = tmp_path / "different.md"
    changed.write_text("not requested", encoding="utf-8")
    with pytest.raises(contracts.ContractError, match="recovery inputs"):
        contracts.amend_owner(manifest, "identity_tenant", str(changed), [str(evidence)], str(receipt))
    with pytest.raises(contracts.ContractError, match="does not exist"):
        contracts.amend_owner(manifest, "identity_tenant", str(artifact), [str(evidence)], str(tmp_path / "other.json"))
    assert not receipt.exists()
    assert contracts.amend_owner(*args) == "receipt_recovered"
    contracts.check_manifest(manifest, [], [])


@pytest.mark.parametrize("tamper", ["hash", "path", "owner", "state", "missing", "cycle", "duplicate", "old_contract", "old_evidence"])
def test_amendment_chain_rejects_corruption(tmp_path: Path, tamper: str) -> None:
    manifest, artifact, evidence, receipt = _amendment_case(tmp_path)
    old = _read(manifest)["owners"][0]
    contracts.amend_owner(manifest, "identity_tenant", str(artifact), [str(evidence)], str(receipt))
    value = _read(receipt)
    if tamper == "hash":
        value["previous_receipt_hash"] = "0" * 64
    elif tamper == "path":
        value["previous_receipt"] = str(tmp_path / "unrelated.json")
    elif tamper in {"owner", "state"}:
        value["owner_row"]["owner_id" if tamper == "owner" else "state"] = "wrong"
    elif tamper in {"cycle", "duplicate"}:
        ledger = _read(manifest)
        ledger["owners"][0]["amendment_receipts"].append(
            value["previous_receipt"] if tamper == "cycle" else str(receipt)
        )
        manifest.write_text(json.dumps(ledger), encoding="utf-8")
    elif tamper in {"old_contract", "old_evidence"}:
        source = old["contract_artifact"] if tamper == "old_contract" else old["evidence"][0]["path"]
        contracts._resolve_artifact(source, manifest).write_text("tampered", encoding="utf-8")
    receipt.write_text(json.dumps(value), encoding="utf-8")
    if tamper == "missing":
        receipt.unlink()
    with pytest.raises(contracts.ContractError):
        contracts.check_manifest(manifest, [], [])
    with pytest.raises(contracts.ContractError):
        contracts.build_manifest(manifest, manifest.with_name("owner-dag.json"))


@pytest.mark.parametrize("target", ["manifest", "dag", "gates", "lock", "contract", "evidence", "initial", "existing"])
def test_amendment_cannot_overwrite_inputs(tmp_path: Path, target: str) -> None:
    manifest, artifact, evidence, _ = _amendment_case(tmp_path)
    initial = contracts._resolve_output_path(
        contracts._declared_approval_receipts(manifest, {"identity_tenant"})["identity_tenant"], manifest
    )
    existing = tmp_path / "existing.json"
    existing.write_text("{}", encoding="utf-8")
    output = {"manifest": manifest, "dag": manifest.with_name("owner-dag.json"),
              "gates": manifest.with_name("goal-gates.json"),
              "lock": manifest.with_name(f".{manifest.name}.lock"),
              "contract": artifact, "evidence": evidence, "initial": initial, "existing": existing}[target]
    before = output.read_bytes()
    ledger_before = manifest.read_bytes()
    with pytest.raises(contracts.ContractError):
        contracts.amend_owner(manifest, "identity_tenant", str(artifact), [str(evidence)], str(output))
    assert output.read_bytes() == before
    assert manifest.read_bytes() == ledger_before


def test_amendment_requires_approved_owner_distinct_evidence_and_changed_binding(tmp_path: Path) -> None:
    manifest, artifact, evidence, receipt = _amendment_case(tmp_path)
    with pytest.raises(contracts.ContractError, match="already approved"):
        contracts.amend_owner(manifest, "run", str(artifact), [str(evidence)], str(receipt))
    for paths in ([], [str(evidence), str(evidence)]):
        with pytest.raises(contracts.ContractError, match="distinct evidence"):
            contracts.amend_owner(manifest, "identity_tenant", str(artifact), paths, str(receipt))
    old = _read(manifest)["owners"][0]
    with pytest.raises(contracts.ContractError, match="must change"):
        contracts.amend_owner(manifest, "identity_tenant", old["contract_artifact"],
                              [e["path"] for e in old["evidence"]], str(receipt))


def test_concurrent_amendments_serialize_and_tampered_replay_fails(tmp_path: Path) -> None:
    manifest, artifact, evidence, receipt = _amendment_case(tmp_path)
    args = (manifest, "identity_tenant", str(artifact), [str(evidence)], str(receipt))
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: contracts.amend_owner(*args), range(2)))
    assert sorted(results) == ["applied", "replayed"]
    assert len(_read(manifest)["owners"][0]["amendment_receipts"]) == 1
    value = _read(receipt)
    value["owner_row"]["contract_hash"] = "0" * 64
    receipt.write_text(json.dumps(value), encoding="utf-8")
    before = manifest.read_bytes()
    with pytest.raises(contracts.ContractError, match="requested mutation"):
        contracts.amend_owner(*args)
    assert manifest.read_bytes() == before


@pytest.mark.parametrize("metadata", [[], "receipt.json", [1], [""], ["a", "./a"]])
def test_amendment_metadata_is_closed_and_nonempty(tmp_path: Path, metadata: object) -> None:
    manifest, _, _, _ = _amendment_case(tmp_path)
    value = _read(manifest)
    value["owners"][0]["amendment_receipts"] = metadata
    manifest.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(contracts.ContractError):
        contracts.check_manifest(manifest, [], [])


def test_amend_cli_and_other_owner_receipt_validation(tmp_path: Path) -> None:
    manifest, artifact, evidence, receipt = _amendment_case(tmp_path)
    _approve_declared_owner(manifest, "audit")
    args = ["amend", "--manifest", str(manifest), "--owner", "identity_tenant",
            "--contract-artifact", str(artifact), "--evidence", str(evidence), "--receipt", str(receipt)]
    assert contracts.main(args) == 0
    audit_receipt = contracts._resolve_output_path(
        contracts._declared_approval_receipts(manifest, {"audit"})["audit"], manifest
    )
    audit_receipt.unlink()
    before = manifest.read_bytes()
    assert contracts.main(args) == 1
    assert manifest.read_bytes() == before


def test_amend_rejects_s3_without_writing_and_keeps_s1_available(tmp_path: Path) -> None:
    manifest, artifact, evidence, receipt = _amendment_case(tmp_path)
    _approve_declared_owner(manifest, "audit")
    before = manifest.read_bytes()
    with pytest.raises(contracts.ContractError, match="S3 amendments require a joint product/owner contract update"):
        contracts.amend_owner(manifest, "organization", str(artifact), [str(evidence)], str(receipt))
    assert manifest.read_bytes() == before
    assert not receipt.exists()
    assert contracts.amend_owner(manifest, "audit", str(artifact), [str(evidence)], str(receipt)) == "applied"
    contracts.check_manifest(manifest, [], [])


def _approve_declared_s3_owner(manifest_path: Path, module_id: str) -> str:
    repository_root = manifest_path.parents[2]
    artifact = repository_root / "specs" / "backend-products" / f"{module_id}.md"
    evidence = repository_root / "specs" / "backend-products" / f"{module_id}-review.txt"
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_text(f"# {module_id} product contract\n", encoding="utf-8")
    evidence.write_text(f"{module_id} product review passed\n", encoding="utf-8")
    subprocess.run(["git", "init", "--quiet"], cwd=repository_root, check=True)
    subprocess.run(
        ["git", "add", "--", f"specs/backend-products/{module_id}.md"],
        cwd=repository_root,
        check=True,
    )

    product_manifest_path = manifest_path.with_name("product-contracts.json")
    product_manifest = _read(product_manifest_path)
    product_row = next(row for row in product_manifest["modules"] if row["module_id"] == module_id)
    product_row.update(
        {
            "state": "contract_approved",
            "contract_artifact": f"specs/backend-products/{module_id}.md",
            "contract_hash": hashlib.sha256(artifact.read_bytes()).hexdigest(),
            "evidence": [{"path": str(evidence), "sha256": hashlib.sha256(evidence.read_bytes()).hexdigest()}],
            "actors": ["tenant member"],
            "product_workflow": [f"use {module_id}"],
            "persistence": [f"{module_id} records"],
            "api_events": [f"POST /{module_id}"],
            "authorization": ["tenant-scoped access"],
            "failure_behavior": ["invalid requests fail closed"],
            "consumers": ["web application"],
            "endpoint_mapping": [f"http.{module_id}"],
            "acceptance_tests": [f"tests/modules/{module_id}"],
            "explicit_deletions": ["none"],
        }
    )
    product_manifest_path.write_text(json.dumps(product_manifest), encoding="utf-8")

    gates = _read(manifest_path.with_name("goal-gates.json"))
    mutation = next(
        mutation
        for goal in gates["goals"]
        for mutation in goal["mutations"]
        if "--owner <owner> " in mutation["command"]
    )
    receipt = mutation["receipt"].replace("<owner>", module_id)
    return contracts.approve_owner(
        manifest_path,
        module_id,
        str(artifact),
        [str(evidence)],
        receipt,
    )


def _run_declared_check(manifest_path: Path, goal_id: str, validation_id: str) -> int:
    gates = _read(manifest_path.with_name("goal-gates.json"))
    goal = next(goal for goal in gates["goals"] if goal["id"] == goal_id)
    command = next(
        validation["command"] for validation in goal["validations"] if validation["id"] == validation_id
    )
    arguments = shlex.split(command)
    script_index = arguments.index("scripts/check_owner_contracts.py")
    cli_arguments = arguments[script_index + 1 :]
    manifest_index = cli_arguments.index("--manifest") + 1
    cli_arguments[manifest_index] = str(manifest_path)
    return contracts.main(cli_arguments)


def _approve_dependencies(manifest_path: Path, owner_id: str, tmp_path: Path) -> None:
    dag_by_owner = {row["owner_id"]: row for row in _read(tmp_path / "owner-dag.json")["owners"]}
    for dependency in dag_by_owner[owner_id]["depends_on"]:
        _approve_dependencies(manifest_path, dependency, tmp_path)
        dependency_row = next(
            row for row in _read(manifest_path)["owners"] if row["owner_id"] == dependency
        )
        if dependency_row["state"] == "contract_approved":
            continue
        artifact = tmp_path / f"{dependency}-contract.md"
        evidence = tmp_path / f"{dependency}-review.txt"
        artifact.write_text(f"# Approved {dependency} contract\n", encoding="utf-8")
        evidence.write_text(f"{dependency} review passed\n", encoding="utf-8")
        assert _approve(manifest_path, dependency, artifact, evidence) == "applied"


def _approved_product(tmp_path: Path, module_id: str) -> tuple[Path, Path]:
    product_manifest = _read(CANONICAL_PRODUCT_MANIFEST)
    artifact = tmp_path / "specs" / "backend-products" / f"{module_id}.md"
    artifact.parent.mkdir(parents=True)
    artifact.write_text(f"# Approved {module_id} contract\n", encoding="utf-8")
    evidence = tmp_path / f"{module_id}-product-review.txt"
    evidence.write_text("product contract approved\n", encoding="utf-8")
    row = next(row for row in product_manifest["modules"] if row["module_id"] == module_id)
    row.update(
        {
            "state": "contract_approved",
            "contract_artifact": f"specs/backend-products/{module_id}.md",
            "contract_hash": hashlib.sha256(artifact.read_bytes()).hexdigest(),
            "evidence": [{"path": str(evidence), "sha256": hashlib.sha256(evidence.read_bytes()).hexdigest()}],
            "actors": ["tenant member"],
            "product_workflow": ["sign in"],
            "persistence": ["account and membership"],
            "api_events": ["POST /auth/login"],
            "authorization": ["public login then tenant principal"],
            "failure_behavior": ["invalid credentials fail closed"],
            "consumers": ["web application"],
            "endpoint_mapping": ["http.auth.login"],
            "acceptance_tests": ["tests/modules/auth/test_login.py"],
            "explicit_deletions": ["none"],
        }
    )
    subprocess.run(["git", "init", "--quiet"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "add", "--", f"specs/backend-products/{module_id}.md"], cwd=tmp_path, check=True
    )
    product_manifest_path = tmp_path / "product-contracts.json"
    product_manifest_path.write_text(json.dumps(product_manifest), encoding="utf-8")
    return artifact, evidence


def test_build_creates_the_exact_approved_owner_roster(tmp_path: Path) -> None:
    manifest_path = _built_manifest(tmp_path)
    manifest = _read(manifest_path)

    assert [row["owner_id"] for row in manifest["owners"]] == [
        row["owner_id"] for row in _read(CANONICAL_DAG)["owners"]
    ]
    assert len(manifest["owners"]) == 34
    assert {row["schema_wave"] for row in manifest["owners"]} == {"S0", "S1", "S2", "S3"}
    assert all(row["state"] == "unreviewed" for row in manifest["owners"])
    contracts.check_manifest(manifest_path, [], [])


def test_sso_dependency_and_ledger_order_are_canonical(tmp_path: Path) -> None:
    manifest_path = _built_manifest(tmp_path)
    dag_rows = _read(tmp_path / "owner-dag.json")["owners"]
    owner_rows = _read(manifest_path)["owners"]
    sso = next(row for row in dag_rows if row["owner_id"] == "sso")

    assert "credential" in sso["depends_on"]
    assert [row["owner_id"] for row in owner_rows] == [row["owner_id"] for row in dag_rows]
    assert next(index for index, row in enumerate(dag_rows) if row["owner_id"] == "credential") < next(
        index for index, row in enumerate(dag_rows) if row["owner_id"] == "sso"
    )


def test_dag_rejects_missing_or_late_sso_credential_dependency(tmp_path: Path) -> None:
    dag = _read(CANONICAL_DAG)
    sso = next(row for row in dag["owners"] if row["owner_id"] == "sso")
    sso["depends_on"].remove("credential")
    missing_dag = tmp_path / "missing-credential.json"
    missing_dag.write_text(json.dumps(dag), encoding="utf-8")
    with pytest.raises(contracts.ContractError, match="missing required dependencies for sso: credential"):
        contracts.build_manifest(tmp_path / "manifest.json", missing_dag)

    dag = _read(CANONICAL_DAG)
    rows = dag["owners"]
    credential_index = next(index for index, row in enumerate(rows) if row["owner_id"] == "credential")
    sso_index = next(index for index, row in enumerate(rows) if row["owner_id"] == "sso")
    sso_row = rows.pop(sso_index)
    rows.insert(credential_index, sso_row)
    late_dag = tmp_path / "late-credential.json"
    late_dag.write_text(json.dumps(dag), encoding="utf-8")
    with pytest.raises(contracts.ContractError, match=r"dependencies must appear before sso: .*credential"):
        contracts.build_manifest(tmp_path / "manifest.json", late_dag)


def test_approval_requires_every_owner_dag_dependency(tmp_path: Path) -> None:
    manifest_path = _built_manifest(tmp_path)
    artifact = tmp_path / "credential-contract.md"
    evidence = tmp_path / "credential-review.txt"
    artifact.write_text("approved Credential contract\n", encoding="utf-8")
    evidence.write_text("Credential review passed\n", encoding="utf-8")

    with pytest.raises(contracts.ContractError, match="not approved for credential: identity_tenant"):
        _approve(manifest_path, "credential", artifact, evidence)


def test_approve_records_current_hashes_and_replays_the_exact_receipt(tmp_path: Path) -> None:
    manifest_path = _built_manifest(tmp_path)
    artifact = tmp_path / "identity-contract.md"
    evidence = tmp_path / "identity-review.txt"
    artifact.write_text("approved identity contract\n", encoding="utf-8")
    evidence.write_text("review passed\n", encoding="utf-8")

    assert _approve(manifest_path, "identity_tenant", artifact, evidence) == "applied"
    contracts.check_manifest(
        manifest_path, ["identity_tenant"], ["S0"], _approval_receipts(manifest_path)
    )

    row = _read(manifest_path)["owners"][0]
    assert row["contract_hash"] == hashlib.sha256(artifact.read_bytes()).hexdigest()
    assert row["evidence"] == [{"path": str(evidence), "sha256": hashlib.sha256(evidence.read_bytes()).hexdigest()}]
    assert _approve(manifest_path, "identity_tenant", artifact, evidence) == "replayed"

    different_artifact = tmp_path / "different-identity-contract.md"
    different_artifact.write_text("different contract\n", encoding="utf-8")
    with pytest.raises(contracts.ContractError, match="receipt does not match requested mutation"):
        _approve(manifest_path, "identity_tenant", different_artifact, evidence)


def test_approve_recovers_a_missing_receipt_only_for_the_exact_ledger_result(tmp_path: Path) -> None:
    manifest_path = _built_manifest(tmp_path)
    artifact = tmp_path / "identity-contract.md"
    evidence = tmp_path / "identity-review.txt"
    artifact.write_text("approved identity contract\n", encoding="utf-8")
    evidence.write_text("review passed\n", encoding="utf-8")

    assert _approve(manifest_path, "identity_tenant", artifact, evidence) == "applied"
    receipt = manifest_path.parent / "receipts" / "identity_tenant-contract-approval.json"
    receipt.unlink()

    assert _approve(manifest_path, "identity_tenant", artifact, evidence) == "receipt_recovered"
    assert receipt.is_file()


def test_approve_rejects_a_tampered_receipt_before_replay(tmp_path: Path) -> None:
    manifest_path = _built_manifest(tmp_path)
    artifact = tmp_path / "identity-contract.md"
    evidence = tmp_path / "identity-review.txt"
    artifact.write_text("approved identity contract\n", encoding="utf-8")
    evidence.write_text("review passed\n", encoding="utf-8")

    assert _approve(manifest_path, "identity_tenant", artifact, evidence) == "applied"
    receipt = manifest_path.parent / "receipts" / "identity_tenant-contract-approval.json"
    tampered = _read(receipt)
    tampered["owner_id"] = "credential"
    receipt.write_text(json.dumps(tampered), encoding="utf-8")

    with pytest.raises(contracts.ContractError, match="receipt does not match requested mutation"):
        _approve(manifest_path, "identity_tenant", artifact, evidence)


def test_concurrent_exact_approvals_serialize_to_one_mutation_and_one_replay(tmp_path: Path) -> None:
    manifest_path = _built_manifest(tmp_path)
    artifact = tmp_path / "identity-contract.md"
    evidence = tmp_path / "identity-review.txt"
    artifact.write_text("approved identity contract\n", encoding="utf-8")
    evidence.write_text("review passed\n", encoding="utf-8")

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(
            executor.map(
                lambda _: _approve(manifest_path, "identity_tenant", artifact, evidence),
                range(2),
            )
        )

    assert sorted(results) == ["applied", "replayed"]


def test_build_and_approve_share_the_manifest_lock(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manifest_path = _built_manifest(tmp_path)
    dag_path = tmp_path / "owner-dag.json"
    artifact = tmp_path / "identity-contract.md"
    evidence = tmp_path / "identity-review.txt"
    artifact.write_text("approved identity contract\n", encoding="utf-8")
    evidence.write_text("review passed\n", encoding="utf-8")
    build_has_lock = Event()
    release_build = Event()
    write_json = contracts._write_json

    def block_first_unreviewed_build(path: Path, value: dict) -> None:
        owners = value.get("owners")
        if path == manifest_path and owners and all(row["state"] == "unreviewed" for row in owners):
            build_has_lock.set()
            assert release_build.wait(timeout=5)
        write_json(path, value)

    monkeypatch.setattr(contracts, "_write_json", block_first_unreviewed_build)
    with ThreadPoolExecutor(max_workers=2) as executor:
        build = executor.submit(contracts.build_manifest, manifest_path, dag_path)
        assert build_has_lock.wait(timeout=5)
        approval = executor.submit(_approve, manifest_path, "identity_tenant", artifact, evidence)
        assert not approval.done()
        release_build.set()
        build.result(timeout=5)
        assert approval.result(timeout=5) == "applied"

    identity = next(row for row in _read(manifest_path)["owners"] if row["owner_id"] == "identity_tenant")
    assert identity["state"] == "contract_approved"


def test_approve_recovers_after_manifest_write_when_receipt_write_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest_path = _built_manifest(tmp_path)
    artifact = tmp_path / "identity-contract.md"
    evidence = tmp_path / "identity-review.txt"
    artifact.write_text("approved identity contract\n", encoding="utf-8")
    evidence.write_text("review passed\n", encoding="utf-8")
    receipt = manifest_path.parent / "receipts" / "identity_tenant-contract-approval.json"
    write_json = contracts._write_json

    def fail_receipt_write(path: Path, value: dict) -> None:
        if path == receipt:
            raise OSError("injected receipt write failure")
        write_json(path, value)

    monkeypatch.setattr(contracts, "_write_json", fail_receipt_write)
    with pytest.raises(OSError, match="injected receipt write failure"):
        _approve(manifest_path, "identity_tenant", artifact, evidence)
    assert not receipt.exists()
    assert _read(manifest_path)["owners"][0]["state"] == "contract_approved"

    monkeypatch.setattr(contracts, "_write_json", write_json)
    assert _approve(manifest_path, "identity_tenant", artifact, evidence) == "receipt_recovered"


def test_check_rejects_tampered_approval_artifacts(tmp_path: Path) -> None:
    manifest_path = _built_manifest(tmp_path)
    artifact = tmp_path / "identity-contract.md"
    evidence = tmp_path / "identity-review.txt"
    artifact.write_text("approved\n", encoding="utf-8")
    evidence.write_text("review passed\n", encoding="utf-8")
    _approve(manifest_path, "identity_tenant", artifact, evidence)

    artifact.write_text("changed after approval\n", encoding="utf-8")

    with pytest.raises(contracts.ContractError, match="artifact hash mismatch"):
        contracts.check_manifest(manifest_path, [], [])


def test_check_requires_and_verifies_every_approved_owner_receipt(tmp_path: Path) -> None:
    manifest_path = _built_manifest(tmp_path)
    artifact = tmp_path / "identity-contract.md"
    evidence = tmp_path / "identity-review.txt"
    artifact.write_text("approved identity contract\n", encoding="utf-8")
    evidence.write_text("review passed\n", encoding="utf-8")
    _approve(manifest_path, "identity_tenant", artifact, evidence)
    receipts = _approval_receipts(manifest_path)

    contracts.check_manifest(manifest_path, [], [], receipts)
    with pytest.raises(contracts.ContractError, match="goal-gates.json"):
        contracts.check_manifest(manifest_path, [], [], [])

    receipt_path = Path(receipts[0])
    receipt = _read(receipt_path)
    receipt["resulting_owner_row_hash"] = "0" * 64
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    with pytest.raises(contracts.ContractError, match="does not match owner ledger state"):
        contracts.check_manifest(manifest_path, [], [], receipts)


def test_canonical_g001_and_g004_checks_carry_forward_approval_receipts(tmp_path: Path) -> None:
    manifest_path = _canonical_built_manifest(tmp_path)
    gates = _read(manifest_path.with_name("goal-gates.json"))
    g001_command = next(
        validation["command"]
        for validation in gates["goals"][1]["validations"]
        if validation["id"] == "owner-dag-and-wave-roster"
    )
    assert "--approval-receipt" not in g001_command

    assert _approve_declared_owner(manifest_path, "identity_tenant") == "applied"
    assert _run_declared_check(manifest_path, "G001", "owner-dag-and-wave-roster") == 0

    g003_owners = gates["goals"][3]["contract_approval_owners"]
    for owner_id in g003_owners[1:]:
        assert _approve_declared_owner(manifest_path, owner_id) == "applied"
    assert _run_declared_check(manifest_path, "G001", "owner-dag-and-wave-roster") == 0

    for owner_id in gates["goals"][4]["contract_approval_owners"]:
        assert _approve_declared_owner(manifest_path, owner_id) == "applied"
    for goal_id, validation_id in (
        ("G001", "owner-dag-and-wave-roster"),
        ("G003", "foundation-contract-prerequisites"),
        ("G004", "product-input-contract-prerequisites"),
    ):
        assert _run_declared_check(manifest_path, goal_id, validation_id) == 0

    assert _approve_declared_s3_owner(manifest_path, "enterprise_settings") == "applied"
    s3_receipt = (
        manifest_path.parents[2]
        / "backend/artifacts/rewrite/G007/receipts/enterprise_settings-contract-approval.json"
    )
    assert s3_receipt.is_file()
    assert not s3_receipt.with_name("enterprise-settings-contract-approval.json").exists()
    for goal_id, validation_id in (
        ("G001", "owner-dag-and-wave-roster"),
        ("G003", "foundation-contract-prerequisites"),
        ("G004", "product-input-contract-prerequisites"),
    ):
        assert _run_declared_check(manifest_path, goal_id, validation_id) == 0


def test_canonical_g001_check_rejects_a_missing_declared_receipt(tmp_path: Path) -> None:
    manifest_path = _canonical_built_manifest(tmp_path)
    assert _approve_declared_owner(manifest_path, "identity_tenant") == "applied"
    receipt = (
        manifest_path.parents[2]
        / "backend/artifacts/rewrite/G003/receipts/identity-tenant-contract-approval.json"
    )
    receipt.unlink()

    assert _run_declared_check(manifest_path, "G001", "owner-dag-and-wave-roster") == 1


def test_explicit_receipts_must_use_the_exact_declared_path_without_duplicates(tmp_path: Path) -> None:
    manifest_path = _canonical_built_manifest(tmp_path)
    assert _approve_declared_owner(manifest_path, "identity_tenant") == "applied"
    canonical_receipt = (
        manifest_path.parents[2]
        / "backend/artifacts/rewrite/G003/receipts/identity-tenant-contract-approval.json"
    )
    copied_receipt = manifest_path.parents[2] / "copied-receipt.json"
    copied_receipt.write_bytes(canonical_receipt.read_bytes())

    with pytest.raises(contracts.ContractError, match="path is not canonical"):
        contracts.check_manifest(manifest_path, [], [], [str(copied_receipt)])
    with pytest.raises(contracts.ContractError, match="duplicate approval receipt"):
        contracts.check_manifest(
            manifest_path, [], [], [str(canonical_receipt), str(canonical_receipt)]
        )


@pytest.mark.parametrize("receipt_target", ["manifest", "dag", "contract", "evidence"])
def test_approve_receipt_cannot_overwrite_authoritative_inputs(
    tmp_path: Path, receipt_target: str
) -> None:
    manifest_path = _built_manifest(tmp_path)
    dag_path = tmp_path / "owner-dag.json"
    artifact = tmp_path / "identity-contract.md"
    evidence = tmp_path / "identity-review.txt"
    artifact.write_text("approved identity contract\n", encoding="utf-8")
    evidence.write_text("review passed\n", encoding="utf-8")
    targets = {
        "manifest": manifest_path,
        "dag": dag_path,
        "contract": artifact,
        "evidence": evidence,
    }
    before = {name: path.read_bytes() for name, path in targets.items()}

    with pytest.raises(contracts.ContractError, match="receipt must be separate"):
        contracts.approve_owner(
            manifest_path,
            "identity_tenant",
            str(artifact),
            [str(evidence)],
            str(targets[receipt_target]),
        )

    assert {name: path.read_bytes() for name, path in targets.items()} == before


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda rows: rows.pop(), "missing owners"),
        (lambda rows: rows.append(copy.deepcopy(rows[0])), "duplicate owner"),
        (
            lambda rows: rows.append(
                {
                    **copy.deepcopy(rows[0]),
                    "owner_id": "runtime",
                }
            ),
            "extra owner",
        ),
        (lambda rows: rows[0].update(schema_wave="S1"), "wave mismatch"),
        (lambda rows: rows[0].update(implementation_phase=4), "phase mismatch"),
    ],
)
def test_check_rejects_roster_and_wave_drift(tmp_path: Path, mutation, message: str) -> None:
    manifest_path = _built_manifest(tmp_path)
    manifest = _read(manifest_path)
    mutation(manifest["owners"])
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(contracts.ContractError, match=message):
        contracts.check_manifest(manifest_path, [], [])


def test_check_rejects_unapproved_required_owner_and_wave(tmp_path: Path) -> None:
    manifest_path = _built_manifest(tmp_path)

    with pytest.raises(contracts.ContractError, match="required owner is not approved"):
        contracts.check_manifest(manifest_path, ["run"], [])
    with pytest.raises(contracts.ContractError, match="S1 has unapproved owners"):
        contracts.check_manifest(manifest_path, [], ["S1"])


def test_build_rejects_duplicate_or_cyclic_dag(tmp_path: Path) -> None:
    dag = _read(CANONICAL_DAG)
    dag["owners"].append(copy.deepcopy(dag["owners"][0]))
    duplicate_dag = tmp_path / "duplicate-dag.json"
    duplicate_dag.write_text(json.dumps(dag), encoding="utf-8")
    with pytest.raises(contracts.ContractError, match="duplicate owner"):
        contracts.build_manifest(tmp_path / "manifest.json", duplicate_dag)

    dag = _read(CANONICAL_DAG)
    dag["owners"][0]["depends_on"] = ["auth"]
    cyclic_dag = tmp_path / "cyclic-dag.json"
    cyclic_dag.write_text(json.dumps(dag), encoding="utf-8")
    with pytest.raises(contracts.ContractError, match="dependency cycle"):
        contracts.build_manifest(tmp_path / "manifest.json", cyclic_dag)


def test_build_does_not_replace_an_invalid_existing_ledger(tmp_path: Path) -> None:
    manifest_path = _built_manifest(tmp_path)
    manifest = _read(manifest_path)
    manifest["owners"].pop()
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(contracts.ContractError, match="missing owners"):
        contracts.build_manifest(manifest_path, CANONICAL_DAG)
    assert _read(manifest_path) == manifest


def test_foundation_auth_owner_is_distinct_from_the_later_auth_product_contract(tmp_path: Path) -> None:
    manifest_path = _built_manifest(tmp_path)
    _approve_dependencies(manifest_path, "auth", tmp_path)
    artifact = tmp_path / "foundation-auth.md"
    evidence = tmp_path / "foundation-auth-review.txt"
    artifact.write_text("minimal login-session Auth\n", encoding="utf-8")
    evidence.write_text("foundation Auth review passed\n", encoding="utf-8")

    assert _approve(manifest_path, "auth", artifact, evidence) == "applied"
    auth = next(row for row in _read(manifest_path)["owners"] if row["owner_id"] == "auth")
    assert auth["schema_wave"] == "S1"
    assert auth["implementation_phase"] == 2
    with pytest.raises(contracts.ContractError, match="required owner is not approved"):
        contracts.check_manifest(manifest_path, ["sso"], [], _approval_receipts(manifest_path))
    product_manifest_path = tmp_path / "product-contracts.json"
    product_manifest_path.write_text(CANONICAL_PRODUCT_MANIFEST.read_text(encoding="utf-8"), encoding="utf-8")
    product_checker = contracts._load_product_checker()
    with pytest.raises(product_checker.ProductContractError, match="product contract is not approved: auth"):
        product_checker.check_product_contract(product_manifest_path, "auth")


def test_s3_owner_approval_requires_the_matching_approved_product_contract(tmp_path: Path) -> None:
    manifest_path = _built_manifest(tmp_path)
    _approve_dependencies(manifest_path, "organization", tmp_path)
    product_manifest_path = tmp_path / "product-contracts.json"
    product_manifest_path.write_text(CANONICAL_PRODUCT_MANIFEST.read_text(encoding="utf-8"), encoding="utf-8")
    arbitrary_artifact = tmp_path / "arbitrary.md"
    arbitrary_evidence = tmp_path / "arbitrary-review.txt"
    arbitrary_artifact.write_text("not the product contract\n", encoding="utf-8")
    arbitrary_evidence.write_text("not the product approval\n", encoding="utf-8")

    with pytest.raises(contracts.ContractError, match="product contract is not approved"):
        _approve(manifest_path, "organization", arbitrary_artifact, arbitrary_evidence)
    organization = next(row for row in _read(manifest_path)["owners"] if row["owner_id"] == "organization")
    assert organization["state"] == "unreviewed"


def test_s3_owner_approval_rejects_product_artifact_or_evidence_mismatch(tmp_path: Path) -> None:
    manifest_path = _built_manifest(tmp_path)
    _approve_dependencies(manifest_path, "organization", tmp_path)
    product_artifact, product_evidence = _approved_product(tmp_path, "organization")
    arbitrary_artifact = tmp_path / "arbitrary.md"
    arbitrary_evidence = tmp_path / "arbitrary-review.txt"
    arbitrary_artifact.write_text("not the product contract\n", encoding="utf-8")
    arbitrary_evidence.write_text("not the product approval\n", encoding="utf-8")

    with pytest.raises(contracts.ContractError, match="artifact does not match product contract"):
        _approve(manifest_path, "organization", arbitrary_artifact, product_evidence)
    with pytest.raises(contracts.ContractError, match="evidence does not match product contract"):
        _approve(manifest_path, "organization", product_artifact, arbitrary_evidence)


def test_s3_owner_check_remains_linked_to_product_contract_state(tmp_path: Path) -> None:
    manifest_path = _built_manifest(tmp_path)
    _approve_dependencies(manifest_path, "organization", tmp_path)
    product_artifact, product_evidence = _approved_product(tmp_path, "organization")

    _approve(manifest_path, "organization", product_artifact, product_evidence)
    contracts.check_manifest(
        manifest_path, ["organization"], [], _approval_receipts(manifest_path)
    )

    product_manifest_path = tmp_path / "product-contracts.json"
    product_manifest = _read(product_manifest_path)
    canonical_organization = next(
        row for row in _read(CANONICAL_PRODUCT_MANIFEST)["modules"] if row["module_id"] == "organization"
    )
    organization_index = next(
        index for index, row in enumerate(product_manifest["modules"]) if row["module_id"] == "organization"
    )
    product_manifest["modules"][organization_index] = canonical_organization
    product_manifest_path.write_text(json.dumps(product_manifest), encoding="utf-8")

    with pytest.raises(contracts.ContractError, match="product contract is not approved"):
        contracts.check_manifest(
            manifest_path, ["organization"], [], _approval_receipts(manifest_path)
        )
