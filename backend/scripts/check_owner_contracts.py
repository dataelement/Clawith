"""Build, approve, and validate the clean-rewrite owner contract ledger.

Run from ``backend/``::

    uv run python scripts/check_owner_contracts.py build \
        --manifest rewrite/owner-contracts.json --dag rewrite/owner-dag.json
    uv run python scripts/check_owner_contracts.py approve \
        --manifest rewrite/owner-contracts.json --owner run \
        --contract-artifact ../.agents/notes/proposed/architecture/run.md \
        --evidence ../.omx/evidence/run-contract-review.md \
        --receipt artifacts/rewrite/G003/receipts/run-contract-approval.json
    uv run python scripts/check_owner_contracts.py check \
        --manifest rewrite/owner-contracts.json --require-approved-wave S1
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import importlib.util
import json
import os
import shlex
import sys
import tempfile
from collections import Counter
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from types import ModuleType
from typing import Any

OWNER_FIELDS = {
    "owner_id",
    "schema_wave",
    "implementation_phase",
    "state",
    "contract_artifact",
    "contract_hash",
    "evidence",
}
EVIDENCE_FIELDS = {"path", "sha256"}
RECEIPT_FIELDS = {
    "version",
    "operation",
    "owner_id",
    "manifest_path",
    "contract_artifact",
    "contract_hash",
    "evidence",
    "resulting_state",
    "resulting_owner_row_hash",
}
APPROVED_STATE = "contract_approved"
UNREVIEWED_STATE = "unreviewed"


def _owners(*owner_ids: str, wave: str, phase: int) -> list[tuple[str, str, int]]:
    return [(owner_id, wave, phase) for owner_id in owner_ids]


EXPECTED_OWNERS = tuple(
    _owners("identity_tenant", wave="S0", phase=2)
    + _owners("agent", "credential", "model", "auth", "audit", "run", "permission", "context", wave="S1", phase=4)
    + _owners(
        "workspace",
        "tool",
        "capability_market",
        "session",
        "a2a",
        "group",
        "trigger",
        "heartbeat",
        "channel",
        wave="S2",
        phase=5,
    )
    + _owners(
        "sso",
        "organization",
        "invitation",
        "onboarding",
        "okr",
        "focus",
        "notification",
        "published_page",
        "plaza",
        "enterprise_settings",
        "platform_administration",
        "agentbay",
        "directory",
        "agent_template",
        "observability",
        "tenant_knowledge",
        wave="S3",
        phase=6,
    )
)

# Some owners register schema before their service implementation phase. This map is
# the approved implementation slicing, not a restatement of the schema waves.
IMPLEMENTATION_PHASE_OVERRIDES = {
    "agent": 2,
    "credential": 2,
    "model": 2,
    "audit": 2,
    "permission": 2,
    "auth": 2,
    "workspace": 3,
    "tool": 3,
    "capability_market": 3,
}
EXPECTED_OWNER_MAP = {
    owner_id: (wave, IMPLEMENTATION_PHASE_OVERRIDES.get(owner_id, phase)) for owner_id, wave, phase in EXPECTED_OWNERS
}
REQUIRED_DAG_EDGES = {"sso": {"credential"}}


class ContractError(ValueError):
    """A deterministic contract-ledger validation failure."""


def _load_product_checker() -> ModuleType:
    script_path = Path(__file__).with_name("check_product_contracts.py")
    spec = importlib.util.spec_from_file_location("_clawith_product_contracts", script_path)
    if spec is None or spec.loader is None:
        raise ContractError(f"cannot load product contract checker: {script_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ContractError(f"file does not exist: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ContractError(f"invalid JSON in {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ContractError(f"expected a JSON object in {path}")
    return value


def _render_json(value: dict[str, Any]) -> str:
    return json.dumps(value, indent=2, ensure_ascii=False) + "\n"


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rendered = _render_json(value)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        handle.write(rendered)
        temporary_path = Path(handle.name)
    os.replace(temporary_path, path)


def _json_hash(value: dict[str, Any]) -> str:
    canonical = json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@contextmanager
def _manifest_lock(manifest_path: Path) -> Iterator[None]:
    lock_path = manifest_path.with_name(f".{manifest_path.name}.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+", encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def _require_list(value: Any, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise ContractError(f"{label} must be a list")
    return value


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise ContractError(f"cannot read artifact {path}: {exc}") from exc
    return digest.hexdigest()


def _resolve_artifact(raw_path: str, manifest_path: Path) -> Path:
    candidate = Path(raw_path).expanduser()
    if candidate.is_absolute():
        resolved = candidate.resolve()
        if resolved.is_file():
            return resolved
        raise ContractError(f"artifact does not exist: {raw_path}")

    repository_root = _repository_root_for_manifest(manifest_path)
    candidates = (Path.cwd() / candidate, manifest_path.parent / candidate, repository_root / candidate)
    for unresolved in candidates:
        resolved = unresolved.resolve()
        if resolved.is_file():
            return resolved
    raise ContractError(f"artifact does not exist: {raw_path}")


def _stored_path(path: Path) -> str:
    repository_root = Path(__file__).resolve().parents[2]
    try:
        return path.relative_to(repository_root).as_posix()
    except ValueError:
        return str(path)


def _repository_root_for_manifest(manifest_path: Path) -> Path:
    resolved = manifest_path.resolve()
    if resolved.parent.name == "rewrite" and resolved.parent.parent.name == "backend":
        return resolved.parents[2]
    return Path(__file__).resolve().parents[2]


def _resolve_output_path(raw_path: str, manifest_path: Path | None = None) -> Path:
    candidate = Path(raw_path).expanduser()
    if candidate.is_absolute():
        return candidate.resolve()
    repository_root = (
        _repository_root_for_manifest(manifest_path)
        if manifest_path is not None
        else Path(__file__).resolve().parents[2]
    )
    if candidate.parts and candidate.parts[0] == "backend":
        return (repository_root / candidate).resolve()
    return (Path.cwd() / candidate).resolve()


def _validate_dag(dag: dict[str, Any]) -> list[dict[str, Any]]:
    if dag.get("version") != 1:
        raise ContractError("owner DAG version must be 1")
    rows = _require_list(dag.get("owners"), "owner DAG owners")
    seen: set[str] = set()
    normalized: list[dict[str, Any]] = []
    expected_ids = set(EXPECTED_OWNER_MAP)

    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise ContractError(f"owner DAG row {index} must be an object")
        owner_id = row.get("owner_id")
        if not isinstance(owner_id, str) or not owner_id:
            raise ContractError(f"owner DAG row {index} has an invalid owner_id")
        if owner_id in seen:
            raise ContractError(f"duplicate owner in owner DAG: {owner_id}")
        seen.add(owner_id)
        if owner_id not in expected_ids:
            raise ContractError(f"extra owner in owner DAG: {owner_id}")
        expected_wave, expected_phase = EXPECTED_OWNER_MAP[owner_id]
        if row.get("schema_wave") != expected_wave:
            raise ContractError(
                f"owner DAG wave mismatch for {owner_id}: expected {expected_wave}, got {row.get('schema_wave')!r}"
            )
        if row.get("implementation_phase") != expected_phase:
            raise ContractError(
                f"owner DAG phase mismatch for {owner_id}: expected {expected_phase}, "
                f"got {row.get('implementation_phase')!r}"
            )
        dependencies = _require_list(row.get("depends_on"), f"owner DAG depends_on for {owner_id}")
        if any(not isinstance(dependency, str) or not dependency for dependency in dependencies):
            raise ContractError(f"owner DAG dependencies for {owner_id} must be non-empty strings")
        if len(dependencies) != len(set(dependencies)):
            raise ContractError(f"duplicate dependency in owner DAG for {owner_id}")
        normalized.append(row)

    missing = sorted(expected_ids - seen)
    if missing:
        raise ContractError(f"owner DAG is missing owners: {', '.join(missing)}")

    dependencies_by_owner = {row["owner_id"]: row["depends_on"] for row in normalized}
    position_by_owner = {row["owner_id"]: index for index, row in enumerate(normalized)}
    for owner_id, dependencies in dependencies_by_owner.items():
        unknown = sorted(set(dependencies) - expected_ids)
        if unknown:
            raise ContractError(f"owner DAG has unknown dependencies for {owner_id}: {', '.join(unknown)}")
        if owner_id in dependencies:
            raise ContractError(f"owner DAG owner depends on itself: {owner_id}")
        missing_required = sorted(REQUIRED_DAG_EDGES.get(owner_id, set()) - set(dependencies))
        if missing_required:
            raise ContractError(
                f"owner DAG is missing required dependencies for {owner_id}: {', '.join(missing_required)}"
            )

    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(owner_id: str) -> None:
        if owner_id in visiting:
            raise ContractError(f"owner DAG contains a dependency cycle at {owner_id}")
        if owner_id in visited:
            return
        visiting.add(owner_id)
        for dependency in dependencies_by_owner[owner_id]:
            visit(dependency)
        visiting.remove(owner_id)
        visited.add(owner_id)

    for owner_id in dependencies_by_owner:
        visit(owner_id)
    for owner_id, dependencies in dependencies_by_owner.items():
        late_dependencies = sorted(
            dependency for dependency in dependencies if position_by_owner[dependency] >= position_by_owner[owner_id]
        )
        if late_dependencies:
            raise ContractError(f"owner DAG dependencies must appear before {owner_id}: {', '.join(late_dependencies)}")
    return normalized


def _dag_rows_for_manifest(manifest_path: Path) -> list[dict[str, Any]]:
    return _validate_dag(_load_json(manifest_path.with_name("owner-dag.json")))


def _validate_required_approval_dependencies(
    owner_id: str,
    owners: dict[str, dict[str, Any]],
    dag_by_owner: dict[str, dict[str, Any]],
) -> None:
    required_dependencies = set(dag_by_owner[owner_id]["depends_on"])
    unapproved = sorted(
        dependency for dependency in required_dependencies if owners[dependency]["state"] != APPROVED_STATE
    )
    if unapproved:
        raise ContractError(f"owner contract dependencies are not approved for {owner_id}: {', '.join(unapproved)}")


def _validate_evidence(evidence: Any, owner_id: str, manifest_path: Path) -> None:
    evidence_rows = _require_list(evidence, f"evidence for {owner_id}")
    if not evidence_rows:
        raise ContractError(f"approved owner has no evidence: {owner_id}")
    seen_paths: set[str] = set()
    for index, row in enumerate(evidence_rows):
        if not isinstance(row, dict) or set(row) != EVIDENCE_FIELDS:
            raise ContractError(f"evidence row {index} for {owner_id} must contain path and sha256")
        raw_path = row.get("path")
        expected_hash = row.get("sha256")
        if not isinstance(raw_path, str) or not raw_path:
            raise ContractError(f"evidence row {index} for {owner_id} has an invalid path")
        if raw_path in seen_paths:
            raise ContractError(f"duplicate evidence path for {owner_id}: {raw_path}")
        seen_paths.add(raw_path)
        evidence_path = _resolve_artifact(raw_path, manifest_path)
        actual_hash = _sha256(evidence_path)
        if expected_hash != actual_hash:
            raise ContractError(f"evidence hash mismatch for {owner_id}: {raw_path}")


def _product_evidence_multiset(evidence: Any, manifest_path: Path, label: str) -> Counter[tuple[Path, str]]:
    rows = _require_list(evidence, label)
    result: Counter[tuple[Path, str]] = Counter()
    for row in rows:
        if not isinstance(row, dict):
            raise ContractError(f"{label} must contain evidence objects")
        raw_path = row.get("path")
        sha256 = row.get("sha256")
        if not isinstance(raw_path, str) or not isinstance(sha256, str):
            raise ContractError(f"{label} must contain path and sha256 strings")
        result[(_resolve_artifact(raw_path, manifest_path), sha256)] += 1
    return result


def _validate_s3_product_link(row: dict[str, Any], manifest_path: Path) -> None:
    product_checker = _load_product_checker()
    owner_id = row["owner_id"]
    module_by_owner = {owner: module for module, owner in product_checker.PRODUCT_OWNER_MAP.items()}
    module_id = module_by_owner.get(owner_id)
    if module_id is None:
        raise ContractError(f"S3 owner has no product contract module: {owner_id}")
    product_manifest_path = manifest_path.with_name("product-contracts.json")
    try:
        product_row = product_checker.check_product_contract(product_manifest_path, module_id)
    except product_checker.ProductContractError as exc:
        raise ContractError(f"S3 product contract is not valid for {owner_id}: {exc}") from exc

    owner_artifact = _resolve_artifact(row["contract_artifact"], manifest_path)
    product_artifact = _resolve_artifact(product_row["contract_artifact"], product_manifest_path)
    if owner_artifact != product_artifact or row["contract_hash"] != product_row["contract_hash"]:
        raise ContractError(f"S3 owner contract artifact does not match product contract: {owner_id}")
    owner_evidence = _product_evidence_multiset(row["evidence"], manifest_path, f"owner evidence for {owner_id}")
    product_evidence = _product_evidence_multiset(
        product_row["evidence"], product_manifest_path, f"product evidence for {module_id}"
    )
    if owner_evidence != product_evidence:
        raise ContractError(f"S3 owner contract evidence does not match product contract: {owner_id}")


def validate_manifest(manifest: dict[str, Any], manifest_path: Path) -> dict[str, dict[str, Any]]:
    if manifest.get("version") != 1:
        raise ContractError("owner contract manifest version must be 1")
    rows = _require_list(manifest.get("owners"), "owner contract owners")
    dag_rows = _dag_rows_for_manifest(manifest_path)
    dag_by_owner = {row["owner_id"]: row for row in dag_rows}
    seen: dict[str, dict[str, Any]] = {}
    expected_ids = set(EXPECTED_OWNER_MAP)
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise ContractError(f"owner contract row {index} must be an object")
        if set(row) != OWNER_FIELDS:
            raise ContractError(f"owner contract row {index} has unexpected or missing fields")
        owner_id = row.get("owner_id")
        if not isinstance(owner_id, str) or not owner_id:
            raise ContractError(f"owner contract row {index} has an invalid owner_id")
        if owner_id in seen:
            raise ContractError(f"duplicate owner in contract manifest: {owner_id}")
        if owner_id not in expected_ids:
            raise ContractError(f"extra owner in contract manifest: {owner_id}")
        expected_wave, expected_phase = EXPECTED_OWNER_MAP[owner_id]
        if row.get("schema_wave") != expected_wave:
            raise ContractError(
                f"owner contract wave mismatch for {owner_id}: expected {expected_wave}, got {row.get('schema_wave')!r}"
            )
        if row.get("implementation_phase") != expected_phase:
            raise ContractError(
                f"owner contract phase mismatch for {owner_id}: expected {expected_phase}, "
                f"got {row.get('implementation_phase')!r}"
            )
        state = row.get("state")
        if state not in {UNREVIEWED_STATE, APPROVED_STATE}:
            raise ContractError(f"invalid owner contract state for {owner_id}: {state!r}")
        if state == UNREVIEWED_STATE:
            if (
                row.get("contract_artifact") is not None
                or row.get("contract_hash") is not None
                or row.get("evidence") != []
            ):
                raise ContractError(f"unreviewed owner has approval data: {owner_id}")
        else:
            raw_artifact = row.get("contract_artifact")
            expected_hash = row.get("contract_hash")
            if not isinstance(raw_artifact, str) or not raw_artifact:
                raise ContractError(f"approved owner has no contract artifact: {owner_id}")
            artifact_path = _resolve_artifact(raw_artifact, manifest_path)
            if expected_hash != _sha256(artifact_path):
                raise ContractError(f"contract artifact hash mismatch for {owner_id}")
            _validate_evidence(row.get("evidence"), owner_id, manifest_path)
            if expected_wave == "S3":
                _validate_s3_product_link(row, manifest_path)
        seen[owner_id] = row

    missing = sorted(expected_ids - set(seen))
    if missing:
        raise ContractError(f"owner contract manifest is missing owners: {', '.join(missing)}")
    manifest_order = [row["owner_id"] for row in rows]
    dag_order = [row["owner_id"] for row in dag_rows]
    if manifest_order != dag_order:
        raise ContractError("owner contract manifest order does not match owner DAG")
    for owner_id, row in seen.items():
        if row["state"] == APPROVED_STATE:
            _validate_required_approval_dependencies(owner_id, seen, dag_by_owner)
    return seen


def build_manifest(manifest_path: Path, dag_path: Path) -> None:
    manifest_path = manifest_path.resolve()
    dag_path = dag_path.resolve()
    with _manifest_lock(manifest_path):
        dag_rows = _validate_dag(_load_json(dag_path))
        preserved: dict[str, dict[str, Any]] = {}
        if manifest_path.exists():
            existing = _load_json(manifest_path)
            preserved = validate_manifest(existing, manifest_path)

        owners: list[dict[str, Any]] = []
        for dag_row in dag_rows:
            owner_id = dag_row["owner_id"]
            existing_row = preserved.get(owner_id)
            if existing_row is not None and existing_row["state"] == APPROVED_STATE:
                owners.append(existing_row)
                continue
            owners.append(
                {
                    "owner_id": owner_id,
                    "schema_wave": dag_row["schema_wave"],
                    "implementation_phase": dag_row["implementation_phase"],
                    "state": UNREVIEWED_STATE,
                    "contract_artifact": None,
                    "contract_hash": None,
                    "evidence": [],
                }
            )
        _write_json(manifest_path, {"version": 1, "owners": owners})


def _expected_receipt(manifest_path: Path, row: dict[str, Any]) -> dict[str, Any]:
    return {
        "version": 1,
        "operation": "approve_owner_contract",
        "owner_id": row["owner_id"],
        "manifest_path": _stored_path(manifest_path),
        "contract_artifact": row["contract_artifact"],
        "contract_hash": row["contract_hash"],
        "evidence": row["evidence"],
        "resulting_state": APPROVED_STATE,
        "resulting_owner_row_hash": _json_hash(row),
    }


def _declared_approval_receipts(
    manifest_path: Path, approved_owners: set[str]
) -> dict[str, str]:
    if not approved_owners:
        return {}
    gate_path = manifest_path.with_name("goal-gates.json")
    gate_manifest = _load_json(gate_path)
    if gate_manifest.get("version") != 1:
        raise ContractError("goal-gate manifest version must be 1")
    goals = _require_list(gate_manifest.get("goals"), "goal-gate goals")
    receipts_by_owner: dict[str, str] = {}
    generic_receipt: str | None = None
    for goal in goals:
        if not isinstance(goal, dict):
            raise ContractError("goal-gate goals must contain objects")
        mutations = _require_list(goal.get("mutations"), f"mutations for {goal.get('id', '<unknown>')}")
        for mutation in mutations:
            if not isinstance(mutation, dict):
                raise ContractError("goal-gate mutations must contain objects")
            command = mutation.get("command")
            receipt = mutation.get("receipt")
            if not isinstance(command, str) or "scripts/check_owner_contracts.py approve" not in command:
                continue
            if not isinstance(receipt, str) or not receipt:
                raise ContractError("owner approval mutation has no declared receipt")
            arguments = shlex.split(command)
            try:
                owner_id = arguments[arguments.index("--owner") + 1]
                command_receipt = arguments[arguments.index("--receipt") + 1]
            except (ValueError, IndexError) as exc:
                raise ContractError("owner approval mutation has incomplete owner or receipt arguments") from exc
            if command_receipt != receipt:
                raise ContractError("owner approval mutation command and receipt declaration differ")
            if owner_id == "<owner>":
                if generic_receipt is not None and generic_receipt != receipt:
                    raise ContractError("multiple generic owner approval receipt declarations")
                generic_receipt = receipt
                continue
            if owner_id in receipts_by_owner:
                raise ContractError(f"duplicate owner approval receipt declaration: {owner_id}")
            receipts_by_owner[owner_id] = receipt

    declared: dict[str, str] = {}
    for owner_id in sorted(approved_owners):
        receipt = receipts_by_owner.get(owner_id)
        if receipt is None and generic_receipt is not None:
            receipt = generic_receipt.replace("<owner>", owner_id)
        if receipt is None:
            raise ContractError(f"approved owner has no canonical receipt declaration: {owner_id}")
        declared[owner_id] = receipt
    return declared


def _validate_approval_receipts(
    manifest_path: Path,
    owners: dict[str, dict[str, Any]],
    receipt_paths: Sequence[str],
) -> None:
    approved_owners = {owner_id for owner_id, row in owners.items() if row["state"] == APPROVED_STATE}
    gate_path = manifest_path.with_name("goal-gates.json")
    declared_receipts = (
        _declared_approval_receipts(manifest_path, approved_owners)
        if gate_path.is_file() or not receipt_paths
        else {}
    )
    receipts_by_owner: dict[str, Path] = {}

    def validate_receipt(raw_path: str, *, require_declared_path: bool) -> None:
        receipt_path = _resolve_output_path(raw_path, manifest_path)
        receipt = _load_json(receipt_path)
        if set(receipt) != RECEIPT_FIELDS:
            raise ContractError(f"approval receipt has unexpected or missing fields: {receipt_path}")
        owner_id = receipt.get("owner_id")
        if not isinstance(owner_id, str) or owner_id not in owners:
            raise ContractError(f"approval receipt has an unknown owner: {receipt_path}")
        if owner_id in receipts_by_owner:
            raise ContractError(f"duplicate approval receipt for owner: {owner_id}")
        declared_path = declared_receipts.get(owner_id)
        if require_declared_path and (
            declared_path is None
            or receipt_path != _resolve_output_path(declared_path, manifest_path)
        ):
            raise ContractError(f"approval receipt path is not canonical for owner: {owner_id}")
        row = owners[owner_id]
        if row["state"] != APPROVED_STATE or receipt != _expected_receipt(manifest_path, row):
            raise ContractError(f"approval receipt does not match owner ledger state: {owner_id}")
        receipts_by_owner[owner_id] = receipt_path

    for raw_path in receipt_paths:
        validate_receipt(raw_path, require_declared_path=bool(declared_receipts))
    for owner_id, raw_path in declared_receipts.items():
        if owner_id not in receipts_by_owner:
            validate_receipt(raw_path, require_declared_path=False)

    missing = sorted(approved_owners - set(receipts_by_owner))
    if missing:
        raise ContractError(f"approved owners are missing approval receipts: {', '.join(missing)}")
    extra = sorted(set(receipts_by_owner) - approved_owners)
    if extra:
        raise ContractError(f"approval receipts exist for unapproved owners: {', '.join(extra)}")


def approve_owner(
    manifest_path: Path,
    owner_id: str,
    contract_artifact: str,
    evidence: Sequence[str],
    receipt: str,
) -> str:
    if not evidence:
        raise ContractError("at least one evidence artifact is required")
    manifest_path = manifest_path.resolve()
    receipt_path = _resolve_output_path(receipt, manifest_path)

    with _manifest_lock(manifest_path):
        manifest = _load_json(manifest_path)
        owners = validate_manifest(manifest, manifest_path)
        if owner_id not in owners:
            raise ContractError(f"unknown owner: {owner_id}")
        row = owners[owner_id]

        artifact_path = _resolve_artifact(contract_artifact, manifest_path)
        evidence_rows: list[dict[str, str]] = []
        seen_paths: set[Path] = set()
        for raw_evidence_path in evidence:
            evidence_path = _resolve_artifact(raw_evidence_path, manifest_path)
            if evidence_path in seen_paths:
                raise ContractError(f"duplicate evidence artifact: {raw_evidence_path}")
            seen_paths.add(evidence_path)
            evidence_rows.append({"path": _stored_path(evidence_path), "sha256": _sha256(evidence_path)})
        dag_path = manifest_path.with_name("owner-dag.json").resolve()
        if receipt_path in {manifest_path, dag_path, artifact_path, *seen_paths}:
            raise ContractError(
                "approval receipt must be separate from the manifest, owner DAG, contract, and evidence artifacts"
            )

        candidate_row = {
            **row,
            "state": APPROVED_STATE,
            "contract_artifact": _stored_path(artifact_path),
            "contract_hash": _sha256(artifact_path),
            "evidence": evidence_rows,
        }
        if row["schema_wave"] == "S3":
            _validate_s3_product_link(candidate_row, manifest_path)

        expected_receipt = _expected_receipt(manifest_path, candidate_row)

        if receipt_path.exists():
            existing_receipt = _load_json(receipt_path)
            if set(existing_receipt) != RECEIPT_FIELDS or existing_receipt != expected_receipt:
                raise ContractError(f"approval receipt does not match requested mutation: {receipt_path}")
            if row != candidate_row:
                raise ContractError(f"approval receipt does not match owner ledger state: {owner_id}")
            return "replayed"

        if row["state"] == APPROVED_STATE:
            if row != candidate_row:
                raise ContractError(f"owner contract is already approved with different inputs: {owner_id}")
            _write_json(receipt_path, expected_receipt)
            return "receipt_recovered"

        dag_by_owner = {dag_row["owner_id"]: dag_row for dag_row in _dag_rows_for_manifest(manifest_path)}
        _validate_required_approval_dependencies(owner_id, owners, dag_by_owner)
        row.update(candidate_row)
        _write_json(manifest_path, manifest)
        _write_json(receipt_path, expected_receipt)
        return "applied"


def check_manifest(
    manifest_path: Path,
    required_owners: Sequence[str],
    required_waves: Sequence[str],
    approval_receipts: Sequence[str] = (),
) -> None:
    manifest_path = manifest_path.resolve()
    owners = validate_manifest(_load_json(manifest_path), manifest_path)
    _validate_approval_receipts(manifest_path, owners, approval_receipts)
    for owner_id in required_owners:
        row = owners.get(owner_id)
        if row is None:
            raise ContractError(f"required owner is absent: {owner_id}")
        if row["state"] != APPROVED_STATE:
            raise ContractError(f"required owner is not approved: {owner_id}")
    for wave in required_waves:
        if wave not in {"S0", "S1", "S2", "S3"}:
            raise ContractError(f"unknown schema wave: {wave}")
        wave_rows = [row for row in owners.values() if row["schema_wave"] == wave]
        if not wave_rows:
            raise ContractError(f"schema wave has no owners: {wave}")
        unapproved = sorted(row["owner_id"] for row in wave_rows if row["state"] != APPROVED_STATE)
        if unapproved:
            raise ContractError(f"schema wave {wave} has unapproved owners: {', '.join(unapproved)}")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    build = subparsers.add_parser("build", help="Build the exact owner roster from the approved DAG")
    build.add_argument("--manifest", type=Path, required=True)
    build.add_argument("--dag", type=Path, required=True)

    approve = subparsers.add_parser("approve", help="Approve one owner contract and record immutable evidence")
    approve.add_argument("--manifest", type=Path, required=True)
    approve.add_argument("--owner", required=True)
    approve.add_argument("--contract-artifact", required=True)
    approve.add_argument("--evidence", action="append", required=True)
    approve.add_argument("--receipt", required=True)

    check = subparsers.add_parser("check", help="Validate the ledger and requested approval gates")
    check.add_argument("--manifest", type=Path, required=True)
    check.add_argument("--require-approved-owner", action="append", default=[])
    check.add_argument("--require-approved-wave", action="append", default=[])
    check.add_argument("--approval-receipt", action="append", default=[])
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "build":
            build_manifest(args.manifest, args.dag)
            print(f"built owner contract manifest: {args.manifest}")
        elif args.command == "approve":
            result = approve_owner(args.manifest, args.owner, args.contract_artifact, args.evidence, args.receipt)
            print(f"owner contract approval {result}: {args.owner}")
        else:
            check_manifest(
                args.manifest,
                args.require_approved_owner,
                args.require_approved_wave,
                args.approval_receipt,
            )
            print(f"owner contract manifest passed: {args.manifest}")
    except ContractError as exc:
        print(f"owner contract check failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
