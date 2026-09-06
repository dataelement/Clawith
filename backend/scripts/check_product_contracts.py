"""Validate one approved S3 product contract against the canonical ledger."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

PRODUCT_OWNER_MAP = {
    "auth": "auth",
    "sso": "sso",
    "organization": "organization",
    "invitation": "invitation",
    "onboarding": "onboarding",
    "okr": "okr",
    "focus": "focus",
    "notification": "notification",
    "page": "published_page",
    "plaza": "plaza",
    "enterprise_settings": "enterprise_settings",
    "platform_administration": "platform_administration",
    "agentbay": "agentbay",
    "directory": "directory",
    "agent_template": "agent_template",
    "observability": "observability",
    "tenant_knowledge": "tenant_knowledge",
}
RESOLUTION_FIELDS = (
    "actors",
    "product_workflow",
    "persistence",
    "api_events",
    "authorization",
    "failure_behavior",
    "consumers",
    "endpoint_mapping",
    "acceptance_tests",
    "explicit_deletions",
)
PRODUCT_FIELDS = {
    "module_id",
    "owner_id",
    "state",
    "contract_artifact",
    "contract_hash",
    "evidence",
    *RESOLUTION_FIELDS,
}


class ProductContractError(ValueError):
    """A deterministic product-contract validation failure."""


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ProductContractError(f"manifest does not exist: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ProductContractError(f"invalid JSON in {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ProductContractError("product contract manifest must be a JSON object")
    return value


def _resolve_artifact(raw_path: str, manifest_path: Path) -> Path:
    candidate = Path(raw_path).expanduser()
    if candidate.is_absolute():
        resolved = candidate.resolve()
        if resolved.is_file():
            return resolved
        raise ProductContractError(f"artifact does not exist: {raw_path}")

    resolved_manifest = manifest_path.resolve()
    repository_root = (
        resolved_manifest.parents[2]
        if resolved_manifest.parent.name == "rewrite" and resolved_manifest.parent.parent.name == "backend"
        else Path(__file__).resolve().parents[2]
    )
    for unresolved in (Path.cwd() / candidate, manifest_path.parent / candidate, repository_root / candidate):
        resolved = unresolved.resolve()
        if resolved.is_file():
            return resolved
    raise ProductContractError(f"artifact does not exist: {raw_path}")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise ProductContractError(f"cannot read artifact {path}: {exc}") from exc
    return digest.hexdigest()


def _require_tracked_contract_artifact(path: Path, module_id: str) -> None:
    try:
        repository_root = Path(
            subprocess.run(
                ["git", "-C", str(path.parent), "rev-parse", "--show-toplevel"],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
        ).resolve()
    except (OSError, subprocess.CalledProcessError) as exc:
        raise ProductContractError(f"product contract artifact is not in a Git worktree: {module_id}") from exc
    expected_path = (repository_root / "specs" / "backend-products" / f"{module_id}.md").resolve()
    if path != expected_path:
        raise ProductContractError(
            f"product contract artifact for {module_id} must be specs/backend-products/{module_id}.md"
        )
    try:
        subprocess.run(
            [
                "git",
                "-C",
                str(repository_root),
                "ls-files",
                "--error-unmatch",
                "--",
                path.relative_to(repository_root).as_posix(),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise ProductContractError(f"product contract artifact is not tracked: {module_id}") from exc


def _is_resolved(value: Any) -> bool:
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, list):
        return bool(value) and all(_is_resolved(item) for item in value)
    if isinstance(value, dict):
        return bool(value) and all(isinstance(key, str) and key and _is_resolved(item) for key, item in value.items())
    return False


def validate_roster(
    manifest: dict[str, Any], manifest_path: Path = Path("product-contracts.json")
) -> dict[str, dict[str, Any]]:
    if manifest.get("version") != 1:
        raise ProductContractError("product contract manifest version must be 1")
    rows = manifest.get("modules")
    if not isinstance(rows, list):
        raise ProductContractError("product contract modules must be a list")
    modules: dict[str, dict[str, Any]] = {}
    expected_modules = set(PRODUCT_OWNER_MAP)
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise ProductContractError(f"product contract row {index} must be an object")
        if set(row) != PRODUCT_FIELDS:
            raise ProductContractError(f"product contract row {index} has unexpected or missing fields")
        module_id = row.get("module_id")
        if not isinstance(module_id, str) or not module_id:
            raise ProductContractError(f"product contract row {index} has an invalid module_id")
        if module_id in modules:
            raise ProductContractError(f"duplicate product module: {module_id}")
        if module_id not in expected_modules:
            raise ProductContractError(f"extra product module: {module_id}")
        expected_owner = PRODUCT_OWNER_MAP[module_id]
        if row.get("owner_id") != expected_owner:
            raise ProductContractError(
                f"product owner mismatch for {module_id}: expected {expected_owner}, got {row.get('owner_id')!r}"
            )
        if row.get("state") not in {"unreviewed", "contract_approved"}:
            raise ProductContractError(f"invalid product contract state for {module_id}: {row.get('state')!r}")
        if row["state"] == "unreviewed":
            approval_values = [
                row.get("contract_artifact"),
                row.get("contract_hash"),
                *[row.get(field) for field in RESOLUTION_FIELDS],
            ]
            if any(value is not None for value in approval_values) or row.get("evidence") != []:
                raise ProductContractError(f"unreviewed product contract has approval data: {module_id}")
        else:
            raw_artifact = row.get("contract_artifact")
            if not isinstance(raw_artifact, str) or not raw_artifact:
                raise ProductContractError(f"approved product contract has no artifact: {module_id}")
            expected_artifact = f"specs/backend-products/{module_id}.md"
            if raw_artifact.replace("\\", "/") != expected_artifact:
                raise ProductContractError(
                    f"product contract artifact for {module_id} must be the repository-relative path "
                    f"{expected_artifact}"
                )
            artifact_path = _resolve_artifact(raw_artifact, manifest_path)
            _require_tracked_contract_artifact(artifact_path, module_id)
            if row.get("contract_hash") != _sha256(artifact_path):
                raise ProductContractError(f"product contract artifact hash mismatch: {module_id}")

            evidence = row.get("evidence")
            if not isinstance(evidence, list) or not evidence:
                raise ProductContractError(f"approved product contract has no evidence: {module_id}")
            seen_evidence: set[str] = set()
            for evidence_index, evidence_row in enumerate(evidence):
                if not isinstance(evidence_row, dict) or set(evidence_row) != {"path", "sha256"}:
                    raise ProductContractError(
                        f"evidence row {evidence_index} for {module_id} must contain path and sha256"
                    )
                evidence_path_value = evidence_row.get("path")
                if not isinstance(evidence_path_value, str) or not evidence_path_value:
                    raise ProductContractError(
                        f"evidence row {evidence_index} for {module_id} has an invalid path"
                    )
                if evidence_path_value in seen_evidence:
                    raise ProductContractError(f"duplicate product evidence for {module_id}: {evidence_path_value}")
                seen_evidence.add(evidence_path_value)
                evidence_path = _resolve_artifact(evidence_path_value, manifest_path)
                if evidence_row.get("sha256") != _sha256(evidence_path):
                    raise ProductContractError(
                        f"product evidence hash mismatch for {module_id}: {evidence_path_value}"
                    )
        modules[module_id] = row
    missing = sorted(expected_modules - set(modules))
    if missing:
        raise ProductContractError(f"product contract manifest is missing modules: {', '.join(missing)}")
    return modules


def check_product_contract(manifest_path: Path, module_id: str) -> dict[str, Any]:
    manifest_path = manifest_path.resolve()
    manifest = _load_json(manifest_path)
    modules = validate_roster(manifest, manifest_path)
    row = modules.get(module_id)
    if row is None:
        raise ProductContractError(f"unknown product module: {module_id}")
    if row["state"] != "contract_approved":
        raise ProductContractError(f"product contract is not approved: {module_id}")

    unresolved = [field for field in RESOLUTION_FIELDS if not _is_resolved(row.get(field))]
    if unresolved:
        raise ProductContractError(f"product contract has unresolved fields for {module_id}: {', '.join(unresolved)}")
    return row


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--module", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        check_product_contract(args.manifest, args.module)
    except ProductContractError as exc:
        print(f"product contract check failed: {exc}", file=sys.stderr)
        return 1
    print(f"product contract passed: {args.module}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
