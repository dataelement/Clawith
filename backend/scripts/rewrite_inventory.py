"""Build and enforce the clean-break Backend coverage inventory.

Run from ``backend/``. Route discovery is static: importing the legacy
application would execute configuration and other module-level behavior.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from check_owner_contracts import ContractError as OwnerContractError
from check_owner_contracts import validate_manifest as validate_owner_contract_manifest

from app.infrastructure.config import TARGET_DATABASE_NAME

SCHEMA_VERSION = 1
HTTP_METHODS = {"delete", "get", "head", "options", "patch", "post", "put"}
KINDS = {"bootstrap", "connector", "http", "lifecycle", "websocket"}
STATES = {
    "unreviewed",
    "disposition_approved",
    "contract_approved",
    "replacement_passed",
    "deletion_approved",
}
DISPOSITIONS = {"delete", "defer_rewrite", "reuse_rewrite", "rewrite"}
TERMINAL_STATES = {"replacement_passed", "deletion_approved"}
REWRITE_DISPOSITIONS = {"defer_rewrite", "reuse_rewrite", "rewrite"}
TRANSITIONS = {
    ("unreviewed", "disposition_approved"),
    ("disposition_approved", "contract_approved"),
    ("contract_approved", "replacement_passed"),
    ("disposition_approved", "deletion_approved"),
}
DEFAULT_REFERENCE_HEAD = "8ed4ae2f"


class InventoryError(RuntimeError):
    """Raised when an inventory command cannot preserve its contract."""


@dataclass(frozen=True)
class DiscoveredEntry:
    id: str
    source: str
    kind: str


def _backend_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _module_path(source_root: Path, module: str) -> Path:
    return source_root / (module.replace(".", "/") + ".py")


def _parse(path: Path) -> ast.Module:
    try:
        return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, SyntaxError) as exc:
        raise InventoryError(f"cannot parse mounted source {path}: {exc}") from exc


def _dotted_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = _dotted_name(node.value)
        if parent:
            return f"{parent}.{node.attr}"
    return None


class StaticStrings:
    """Resolve route strings without importing application modules."""

    def __init__(self, source_root: Path) -> None:
        self.source_root = source_root
        self._trees: dict[str, ast.Module] = {}
        self._values: dict[tuple[str, str], str | None] = {}

    def tree(self, module: str) -> ast.Module:
        if module not in self._trees:
            path = _module_path(self.source_root, module)
            if not path.is_file():
                raise InventoryError(f"mounted module is missing: {module} ({path})")
            self._trees[module] = _parse(path)
        return self._trees[module]

    def value(self, module: str, node: ast.AST) -> str | None:
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            left = self.value(module, node.left)
            right = self.value(module, node.right)
            return left + right if left is not None and right is not None else None
        if isinstance(node, ast.JoinedStr):
            parts: list[str] = []
            for value in node.values:
                if not isinstance(value, ast.Constant) or not isinstance(value.value, str):
                    return None
                parts.append(value.value)
            return "".join(parts)
        if isinstance(node, ast.Name):
            return self.named_value(module, node.id)
        if (
            isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id == "settings"
            and node.attr == "API_PREFIX"
        ):
            return self.named_value("app.config", "API_PREFIX")
        return None

    def named_value(self, module: str, name: str) -> str | None:
        key = (module, name)
        if key in self._values:
            return self._values[key]
        self._values[key] = None
        tree = self.tree(module)
        for node in tree.body:
            if isinstance(node, (ast.Assign, ast.AnnAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                value = node.value
                if value is None:
                    continue
                for target in targets:
                    if isinstance(target, ast.Name) and target.id == name:
                        resolved = self.value(module, value)
                        self._values[key] = resolved
                        return resolved
            if isinstance(node, ast.ClassDef):
                for child in node.body:
                    if not isinstance(child, ast.AnnAssign):
                        continue
                    if isinstance(child.target, ast.Name) and child.target.id == name and child.value:
                        resolved = self.value(module, child.value)
                        self._values[key] = resolved
                        return resolved
            if isinstance(node, ast.ImportFrom) and node.module:
                for alias in node.names:
                    if (alias.asname or alias.name) == name:
                        resolved = self.named_value(node.module, alias.name)
                        self._values[key] = resolved
                        return resolved
        return None


def _imports(tree: ast.Module) -> dict[str, tuple[str, str]]:
    result: dict[str, tuple[str, str]] = {}
    for node in tree.body:
        if not isinstance(node, ast.ImportFrom) or not node.module:
            continue
        for alias in node.names:
            result[alias.asname or alias.name] = (node.module, alias.name)
    return result


def _router_prefix(strings: StaticStrings, module: str, router_name: str) -> str:
    if router_name == "app":
        return ""
    for node in strings.tree(module).body:
        if not isinstance(node, ast.Assign):
            continue
        if not any(isinstance(target, ast.Name) and target.id == router_name for target in node.targets):
            continue
        if not isinstance(node.value, ast.Call) or _dotted_name(node.value.func) != "APIRouter":
            continue
        for keyword in node.value.keywords:
            if keyword.arg == "prefix":
                value = strings.value(module, keyword.value)
                if value is None:
                    raise InventoryError(f"unresolved router prefix: {module}.{router_name}")
                return value
        return ""
    raise InventoryError(f"mounted router definition is missing: {module}.{router_name}")


def _join_route(*parts: str) -> str:
    trailing_slash = bool(parts and parts[-1] and parts[-1].endswith("/"))
    path = "/" + "/".join(part.strip("/") for part in parts if part and part != "/")
    if trailing_slash and path != "/":
        return f"{path}/"
    return path


def _source_label(source_root: Path, path: Path, symbol: str) -> str:
    return f"{path.relative_to(source_root).as_posix()}:{symbol}"


def _discover_router_entries(
    strings: StaticStrings,
    module: str,
    router_name: str,
    mounted_prefix: str,
) -> list[DiscoveredEntry]:
    path = _module_path(strings.source_root, module)
    router_prefix = _router_prefix(strings, module, router_name)
    entries: list[DiscoveredEntry] = []
    for node in strings.tree(module).body:
        if not isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)):
            continue
        for decorator in node.decorator_list:
            if not isinstance(decorator, ast.Call) or not isinstance(decorator.func, ast.Attribute):
                continue
            if not isinstance(decorator.func.value, ast.Name) or decorator.func.value.id != router_name:
                continue
            operation = decorator.func.attr.lower()
            if operation not in HTTP_METHODS | {"websocket"}:
                continue
            if not decorator.args:
                raise InventoryError(f"route path is missing: {module}.{node.name}")
            route_path = strings.value(module, decorator.args[0])
            if route_path is None:
                raise InventoryError(f"unresolved route path: {module}.{node.name}")
            full_path = _join_route(mounted_prefix, router_prefix, route_path)
            method = "WEBSOCKET" if operation == "websocket" else operation.upper()
            entries.append(
                DiscoveredEntry(
                    id=f"{method}:{full_path}",
                    source=_source_label(strings.source_root, path, node.name),
                    kind="websocket" if operation == "websocket" else "http",
                )
            )
    return entries


def _lifespan_function(tree: ast.Module) -> str:
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or _dotted_name(node.func) != "FastAPI":
            continue
        for keyword in node.keywords:
            if keyword.arg == "lifespan" and isinstance(keyword.value, ast.Name):
                return keyword.value.id
    raise InventoryError("FastAPI application has no statically named lifespan function")


def _lifecycle_owner(name: str) -> tuple[str, str]:
    lowered = name.lower()
    if any(token in lowered for token in ("feishu", "dingtalk", "wecom", "wechat", "discord")):
        return "channel", "connector"
    if "trigger" in lowered or "scheduler" in lowered:
        return "trigger", "lifecycle"
    if "runtime" in lowered or "worker" in lowered:
        return "run", "lifecycle"
    if "realtime" in lowered:
        return "realtime", "lifecycle"
    if "audit" in lowered:
        return "audit", "bootstrap"
    if any(
        token in lowered
        for token in ("seed", "patch", "push_default", "clean_orphaned", "create_all", "copytree", "default_tenant")
    ):
        return "bootstrap", "bootstrap"
    if "redis" in lowered:
        return "infrastructure", "lifecycle"
    if "ss_local" in lowered or "ss-local" in lowered:
        return "discord_infrastructure", "connector"
    return "application", "lifecycle"


def _is_lifecycle_call(name: str) -> bool:
    leaf = name.rsplit(".", 1)[-1].lower().lstrip("_")
    return (
        leaf in {"aclose", "close", "close_redis", "copytree", "start", "start_all", "stop", "stop_all"}
        or leaf.startswith(("clean_orphaned", "patch_", "push_", "seed_", "start_", "stop_"))
        or "running_runtime_worker_context" in leaf
        or leaf == "create_all"
        or leaf == "write_audit_log"
    )


def _discover_lifecycles(source_root: Path, tree: ast.Module) -> list[DiscoveredEntry]:
    lifespan_name = _lifespan_function(tree)
    function = next(
        (
            node
            for node in tree.body
            if isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)) and node.name == lifespan_name
        ),
        None,
    )
    if function is None:
        raise InventoryError(f"lifespan function is missing: {lifespan_name}")
    entries = [
        DiscoveredEntry(
            id=f"LIFECYCLE:application:{lifespan_name}",
            source=_source_label(source_root, source_root / "app/main.py", lifespan_name),
            kind="lifecycle",
        )
    ]
    seen_names: set[str] = set()
    for node in ast.walk(function):
        if not isinstance(node, ast.Call):
            continue
        name = _dotted_name(node.func)
        candidates = [name] if name and _is_lifecycle_call(name) else []
        if name and name.endswith(".run_sync") and node.args:
            callback = _dotted_name(node.args[0])
            if callback and _is_lifecycle_call(callback):
                candidates.append(callback)
        if name and name.endswith(".add") and node.args:
            added_type = _dotted_name(node.args[0].func) if isinstance(node.args[0], ast.Call) else None
            if added_type in {"Tenant", "_T"}:
                candidates.append("default_tenant_creation")
        for candidate in candidates:
            stable_name = candidate.replace(".", "_").lstrip("_")
            if stable_name in seen_names:
                continue
            seen_names.add(stable_name)
            owner, kind = _lifecycle_owner(stable_name)
            entries.append(
                DiscoveredEntry(
                    id=f"LIFECYCLE:{owner}:{stable_name}",
                    source=_source_label(source_root, source_root / "app/main.py", lifespan_name),
                    kind=kind,
                )
            )
    return entries


def discover(source_root: Path) -> list[DiscoveredEntry]:
    source_root = source_root.resolve()
    main_path = source_root / "app/main.py"
    if not main_path.is_file():
        raise InventoryError(f"application composition is missing: {main_path}")
    strings = StaticStrings(source_root)
    main_tree = strings.tree("app.main")
    imported = _imports(main_tree)
    entries: list[DiscoveredEntry] = []
    for node in main_tree.body:
        if not isinstance(node, ast.Expr) or not isinstance(node.value, ast.Call):
            continue
        call = node.value
        if _dotted_name(call.func) != "app.include_router" or not call.args:
            continue
        if not isinstance(call.args[0], ast.Name):
            raise InventoryError("mounted router must use a statically imported name")
        alias = call.args[0].id
        if alias not in imported:
            raise InventoryError(f"mounted router import is missing: {alias}")
        module, router_name = imported[alias]
        mounted_prefix = ""
        for keyword in call.keywords:
            if keyword.arg == "prefix":
                resolved = strings.value("app.main", keyword.value)
                if resolved is None:
                    raise InventoryError(f"unresolved mounted prefix for {alias}")
                mounted_prefix = resolved
        entries.extend(_discover_router_entries(strings, module, router_name, mounted_prefix))

    entries.extend(_discover_router_entries(strings, "app.main", "app", ""))
    entries.extend(_discover_lifecycles(source_root, main_tree))
    entries.sort(key=lambda entry: entry.id)
    duplicates = _duplicates(entry.id for entry in entries)
    if duplicates:
        raise InventoryError(f"duplicate stable IDs: {', '.join(duplicates)}")
    return entries


def _duplicates(values: Any) -> list[str]:
    seen: set[str] = set()
    duplicate: set[str] = set()
    for value in values:
        if value in seen:
            duplicate.add(value)
        seen.add(value)
    return sorted(duplicate)


def _default_row(entry: DiscoveredEntry) -> dict[str, Any]:
    return {
        "id": entry.id,
        "source": entry.source,
        "kind": entry.kind,
        "state": "unreviewed",
        "disposition": None,
        "target_owner_id": None,
        "owner_contract_id": None,
        "owner_contract_hash": None,
        "behavior_evidence": [],
        "consumer_evidence": [],
        "planned_gate": None,
        "test_artifacts": [],
        "removal_evidence": [],
        "transition_evidence": [],
    }


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise InventoryError(f"cannot read JSON {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise InventoryError(f"JSON root must be an object: {path}")
    return value


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rendered = json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(rendered)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _source_digest(entries: list[DiscoveredEntry]) -> str:
    payload = "\n".join(f"{entry.id}\0{entry.source}\0{entry.kind}" for entry in entries)
    return hashlib.sha256(payload.encode()).hexdigest()


def build_manifest(manifest_path: Path, source_root: Path) -> dict[str, Any]:
    discovered = discover(source_root)
    existing: dict[str, Any] = {}
    previous: dict[str, dict[str, Any]] = {}
    if manifest_path.exists():
        existing = _load_json(manifest_path)
        for row in existing.get("entries", []):
            if isinstance(row, dict) and isinstance(row.get("id"), str):
                previous[row["id"]] = row
    discovered_ids = {entry.id for entry in discovered}
    stale_ids = sorted(
        row_id
        for row_id in set(previous) - discovered_ids
        if previous[row_id].get("state") != "unreviewed"
    )
    if stale_ids:
        raise InventoryError(
            "existing coverage rows disappeared from discovery; preserve the immutable inventory: "
            + ", ".join(stale_ids)
        )
    rows: list[dict[str, Any]] = []
    for entry in discovered:
        row = _default_row(entry)
        row.update(previous.get(entry.id, {}))
        row.update({"id": entry.id, "source": entry.source, "kind": entry.kind})
        rows.append(row)
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "source_digest": _source_digest(discovered),
        "reference": existing.get(
            "reference",
            {
                "expected_head": DEFAULT_REFERENCE_HEAD,
                "persistence_namespace": "clawith_legacy_reference",
                "tracked_content_hash": None,
                "worktree": None,
            },
        ),
        "target": existing.get("target", {"persistence_namespace": TARGET_DATABASE_NAME}),
        "entries": rows,
    }
    validate_manifest(manifest, manifest_path, validate_artifact_hashes=False)
    _write_json(manifest_path, manifest)
    return manifest


def _artifact_path(manifest_path: Path, artifact: dict[str, Any]) -> Path:
    raw = artifact.get("path")
    if not isinstance(raw, str) or not raw:
        raise InventoryError("evidence artifact requires a non-empty path")
    path = Path(raw)
    if not path.is_absolute():
        path = manifest_path.parent.parent / path
    return path


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            while chunk := handle.read(1024 * 1024):
                digest.update(chunk)
    except OSError as exc:
        raise InventoryError(f"cannot hash evidence {path}: {exc}") from exc
    return digest.hexdigest()


def _validate_authority_document(manifest_path: Path, evidence_path: Path) -> None:
    if evidence_path.name != "endpoint-lifecycle-dispositions.json":
        return
    try:
        document = json.loads(evidence_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise InventoryError(f"cannot read disposition authority evidence {evidence_path}: {exc}") from exc
    authorities = document.get("authorities") if isinstance(document, dict) else None
    if not isinstance(authorities, list) or not authorities:
        raise InventoryError("disposition evidence authorities must be a non-empty list")
    repo_root = manifest_path.resolve().parent.parent.parent
    for authority in authorities:
        if not isinstance(authority, dict):
            raise InventoryError("disposition evidence authority entries must be objects")
        raw_path = authority.get("path")
        expected = authority.get("sha256")
        if not isinstance(raw_path, str) or not raw_path:
            raise InventoryError("disposition evidence authority requires a non-empty path")
        relative = Path(raw_path)
        if relative.is_absolute() or ".." in relative.parts:
            raise InventoryError(f"authority path must stay inside the repository: {raw_path}")
        resolved = repo_root / relative
        if not resolved.is_file():
            raise InventoryError(f"authority path is not a file: {raw_path}")
        ignored = subprocess.run(
            ["git", "-C", str(repo_root), "check-ignore", "--quiet", "--no-index", "--", raw_path],
            check=False,
        )
        if ignored.returncode == 0:
            raise InventoryError(f"authority path is ignored: {raw_path}")
        if ignored.returncode != 1:
            raise InventoryError(f"cannot determine whether authority path is ignored: {raw_path}")
        tracked = subprocess.run(
            ["git", "-C", str(repo_root), "ls-files", "--error-unmatch", "--", raw_path],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        if tracked.returncode != 0:
            raise InventoryError(f"authority path is not Git-tracked: {raw_path}")
        if not isinstance(expected, str) or len(expected) != 64:
            raise InventoryError(f"authority hash is invalid: {raw_path}")
        if _sha256(resolved) != expected:
            raise InventoryError(f"authority hash changed: {raw_path}")


def _validate_artifacts(
    manifest_path: Path,
    row: dict[str, Any],
    validated_artifacts: set[Path],
) -> None:
    for field in (
        "behavior_evidence",
        "consumer_evidence",
        "test_artifacts",
        "removal_evidence",
        "transition_evidence",
    ):
        artifacts = row.get(field)
        if not isinstance(artifacts, list):
            raise InventoryError(f"{row.get('id')}: {field} must be a list")
        for artifact in artifacts:
            if not isinstance(artifact, dict):
                raise InventoryError(f"{row.get('id')}: {field} entries must be objects")
            expected = artifact.get("sha256")
            if not isinstance(expected, str) or len(expected) != 64:
                raise InventoryError(f"{row.get('id')}: {field} artifact hash is invalid")
            artifact_path = _artifact_path(manifest_path, artifact)
            actual = _sha256(artifact_path)
            if actual != expected:
                raise InventoryError(f"{row.get('id')}: evidence hash changed for {artifact['path']}")
            resolved = artifact_path.resolve()
            if resolved not in validated_artifacts:
                _validate_authority_document(manifest_path, resolved)
                validated_artifacts.add(resolved)


def _disposition_missing(row: dict[str, Any]) -> list[str]:
    missing: list[str] = []
    disposition = row.get("disposition")
    if disposition not in DISPOSITIONS:
        missing.append("disposition")
    if not row.get("behavior_evidence"):
        missing.append("behavior_evidence")
    if not row.get("consumer_evidence"):
        missing.append("consumer_evidence")
    if not isinstance(row.get("planned_gate"), str) or not row["planned_gate"].strip():
        missing.append("planned_gate")
    if disposition == "delete":
        if row.get("target_owner_id"):
            missing.append("deletion_target_owner_must_be_empty")
    elif not isinstance(row.get("target_owner_id"), str) or not row["target_owner_id"].strip():
        missing.append("target_owner_id")
    return missing


def validate_manifest(
    manifest: dict[str, Any],
    manifest_path: Path,
    *,
    validate_artifact_hashes: bool = True,
) -> tuple[int, int, int]:
    if manifest.get("schema_version") != SCHEMA_VERSION:
        raise InventoryError(f"unsupported coverage schema: {manifest.get('schema_version')}")
    if manifest.get("target", {}).get("persistence_namespace") != TARGET_DATABASE_NAME:
        raise InventoryError(
            f"target persistence namespace must match Settings: {TARGET_DATABASE_NAME}"
        )
    rows = manifest.get("entries")
    if not isinstance(rows, list):
        raise InventoryError("coverage entries must be a list")
    ids = [row.get("id") for row in rows if isinstance(row, dict)]
    if len(ids) != len(rows) or any(not isinstance(row_id, str) for row_id in ids):
        raise InventoryError("every coverage row requires a string ID")
    duplicates = _duplicates(ids)
    if duplicates:
        raise InventoryError(f"duplicate stable IDs: {', '.join(duplicates)}")
    disposition_missing = 0
    unreviewed = 0
    nonterminal = 0
    canonical_owner_ids: set[str] | None = None
    validated_artifacts: set[Path] = set()
    for row in rows:
        state = row.get("state")
        if state not in STATES:
            raise InventoryError(f"{row['id']}: invalid state {state!r}")
        if row.get("kind") not in KINDS:
            raise InventoryError(f"{row['id']}: invalid kind {row.get('kind')!r}")
        if row["kind"] == "http" and row["id"].split(":", 1)[0] not in {
            method.upper() for method in HTTP_METHODS
        }:
            raise InventoryError(f"{row['id']}: HTTP stable ID is invalid")
        if row["kind"] == "websocket" and not row["id"].startswith("WEBSOCKET:/"):
            raise InventoryError(f"{row['id']}: WebSocket stable ID is invalid")
        if row["kind"] in {"bootstrap", "connector", "lifecycle"} and not row["id"].startswith(
            "LIFECYCLE:"
        ):
            raise InventoryError(f"{row['id']}: lifecycle stable ID is invalid")
        if not isinstance(row.get("source"), str) or not row["source"]:
            raise InventoryError(f"{row['id']}: source is required")
        if state == "unreviewed":
            unreviewed += 1
        if state not in TERMINAL_STATES:
            nonterminal += 1
        missing = _disposition_missing(row)
        if missing:
            disposition_missing += 1
            if state != "unreviewed":
                raise InventoryError(f"{row['id']}: approved row is missing {', '.join(missing)}")
        disposition = row.get("disposition")
        if disposition in REWRITE_DISPOSITIONS and row.get("target_owner_id"):
            if canonical_owner_ids is None:
                canonical_owner_ids = set(_validated_owner_contracts(manifest_path))
            if row["target_owner_id"] not in canonical_owner_ids:
                raise InventoryError(
                    f"{row['id']}: target owner is not in the canonical roster: {row['target_owner_id']}"
                )
        if state in {"contract_approved", "replacement_passed"} and disposition not in REWRITE_DISPOSITIONS:
            raise InventoryError(f"{row['id']}: {state} requires a rewrite disposition")
        if state == "deletion_approved" and disposition != "delete":
            raise InventoryError(f"{row['id']}: deletion_approved requires delete disposition")
        if validate_artifact_hashes:
            _validate_artifacts(manifest_path, row, validated_artifacts)
    return unreviewed, disposition_missing, nonterminal


def _load_validated(manifest_path: Path) -> tuple[dict[str, Any], tuple[int, int, int]]:
    manifest = _load_json(manifest_path)
    return manifest, validate_manifest(manifest, manifest_path)


def check_manifest(
    manifest_path: Path,
    *,
    require_zero_unreviewed: bool,
    require_zero_disposition_missing: bool,
    require_all_terminal: bool,
) -> tuple[int, int, int]:
    _, counts = _load_validated(manifest_path)
    unreviewed, disposition_missing, nonterminal = counts
    if require_zero_unreviewed and unreviewed:
        raise InventoryError(f"coverage gate failed: unreviewed={unreviewed}")
    if require_zero_disposition_missing and disposition_missing:
        raise InventoryError(f"coverage gate failed: disposition_missing={disposition_missing}")
    if require_all_terminal and nonterminal:
        raise InventoryError(f"coverage gate failed: nonterminal={nonterminal}")
    return counts


def _evidence_record(manifest_path: Path, evidence: Path) -> dict[str, str]:
    resolved = evidence.resolve()
    if not resolved.is_file():
        raise InventoryError(f"transition evidence is not a file: {resolved}")
    try:
        display = resolved.relative_to(manifest_path.parent.parent.resolve()).as_posix()
    except ValueError:
        display = str(resolved)
    return {"path": display, "sha256": _sha256(resolved)}


def _validated_owner_contracts(manifest_path: Path) -> dict[str, dict[str, Any]]:
    owner_path = manifest_path.with_name("owner-contracts.json")
    owner_manifest = _load_json(owner_path)
    try:
        return validate_owner_contract_manifest(owner_manifest, owner_path)
    except OwnerContractError as exc:
        raise InventoryError(f"owner contract manifest is invalid: {exc}") from exc


def _approved_owner_contract(manifest_path: Path, row: dict[str, Any]) -> None:
    contract_id = row.get("owner_contract_id")
    contract_hash = row.get("owner_contract_hash")
    if not isinstance(contract_id, str) or not contract_id:
        raise InventoryError(f"{row['id']}: owner_contract_id is required")
    owners = _validated_owner_contracts(manifest_path)
    owner = owners.get(contract_id)
    if owner is None:
        raise InventoryError(f"{row['id']}: owner contract is missing: {contract_id}")
    if owner.get("state") != "contract_approved":
        raise InventoryError(f"{row['id']}: owner contract is not approved: {contract_id}")
    if row.get("target_owner_id") != contract_id:
        raise InventoryError(f"{row['id']}: target owner and owner contract differ")
    if not isinstance(contract_hash, str) or contract_hash != owner.get("contract_hash"):
        raise InventoryError(f"{row['id']}: owner contract hash does not match")


def transition(manifest_path: Path, row_id: str, target_state: str, evidence: Path) -> None:
    manifest, _ = _load_validated(manifest_path)
    matches = [row for row in manifest["entries"] if row["id"] == row_id]
    if len(matches) != 1:
        raise InventoryError(f"coverage ID must resolve exactly once: {row_id}")
    row = matches[0]
    current = row["state"]
    if (current, target_state) not in TRANSITIONS:
        raise InventoryError(f"illegal transition: {current} -> {target_state}")
    if target_state == "disposition_approved":
        missing = _disposition_missing(row)
        if missing:
            raise InventoryError(f"{row_id}: disposition is missing {', '.join(missing)}")
    elif target_state in {"contract_approved", "replacement_passed"}:
        if row.get("disposition") not in REWRITE_DISPOSITIONS:
            raise InventoryError(f"{row_id}: rewrite transition conflicts with disposition")
        if target_state == "contract_approved":
            _approved_owner_contract(manifest_path, row)
    elif target_state == "deletion_approved" and row.get("disposition") != "delete":
        raise InventoryError(f"{row_id}: deletion transition conflicts with disposition")
    record = _evidence_record(manifest_path, evidence)
    row["state"] = target_state
    row["transition_evidence"].append({"from": current, "to": target_state, **record})
    if target_state == "replacement_passed":
        row["test_artifacts"].append(record)
    elif target_state == "deletion_approved":
        row["removal_evidence"].append(record)
    validate_manifest(manifest, manifest_path)
    _write_json(manifest_path, manifest)


def _git(worktree: Path, *args: str) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", str(worktree), *args],
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise InventoryError(f"git reference check failed in {worktree}: {exc}") from exc
    return result.stdout.strip()


def _tracked_content_hash(worktree: Path) -> str:
    names = _git(worktree, "ls-files", "-z")
    digest = hashlib.sha256()
    for name in names.split("\0"):
        if not name:
            continue
        path = worktree / name
        digest.update(name.encode())
        digest.update(b"\0")
        digest.update(bytes.fromhex(_sha256(path)))
    return digest.hexdigest()


def _resolve_reference_worktree(
    manifest: dict[str, Any],
    override: Path | None,
    *,
    allow_portable_override: bool = False,
) -> Path:
    configured = manifest.get("reference", {}).get("worktree")
    if (
        override is not None
        and configured
        and override.resolve() != Path(configured).resolve()
        and not allow_portable_override
    ):
        raise InventoryError("reference worktree override does not match the manifest")
    raw = override or configured
    if not raw:
        raise InventoryError("reference worktree is not configured")
    worktree = Path(raw).resolve()
    if not worktree.is_dir():
        raise InventoryError(f"reference worktree does not exist: {worktree}")
    return worktree


def _resolve_reference_python(worktree: Path, override: Path | None) -> Path:
    if override is None:
        return Path(sys.executable)
    reference_python = override.parent.resolve() / override.name
    expected_python = worktree / "backend/.venv/bin/python"
    if reference_python != expected_python:
        raise InventoryError(
            "reference Python override must be the reference backend virtual environment"
        )
    if not reference_python.is_file() or not os.access(reference_python, os.X_OK):
        raise InventoryError("reference Python override is not an executable file")
    return reference_python


def _verify_reference(
    manifest: dict[str, Any],
    worktree: Path,
    expected_head: str,
    *,
    require_clean: bool,
) -> None:
    actual_head = _git(worktree, "rev-parse", "HEAD")
    if not actual_head.startswith(expected_head):
        raise InventoryError(f"reference HEAD mismatch: expected {expected_head}, got {actual_head}")
    configured_head = manifest.get("reference", {}).get("expected_head")
    if configured_head and not actual_head.startswith(configured_head):
        raise InventoryError(f"reference manifest HEAD mismatch: expected {configured_head}, got {actual_head}")
    if require_clean and _git(worktree, "status", "--porcelain", "--untracked-files=all"):
        raise InventoryError("reference worktree is not clean")
    configured_hash = manifest.get("reference", {}).get("tracked_content_hash")
    if not isinstance(configured_hash, str) or len(configured_hash) != 64:
        raise InventoryError("reference tracked_content_hash is not configured")
    actual_hash = _tracked_content_hash(worktree)
    if actual_hash != configured_hash:
        raise InventoryError("reference tracked content changed")


def bind_reference(manifest_path: Path, worktree: Path, expected_head: str) -> None:
    manifest, _ = _load_validated(manifest_path)
    resolved = worktree.resolve()
    if not resolved.is_dir():
        raise InventoryError(f"reference worktree does not exist: {resolved}")
    if resolved == _backend_root().parent.resolve():
        raise InventoryError("the active target worktree cannot be bound as the immutable reference")
    actual_head = _git(resolved, "rev-parse", "HEAD")
    if not actual_head.startswith(expected_head):
        raise InventoryError(f"reference HEAD mismatch: expected {expected_head}, got {actual_head}")
    if _git(resolved, "status", "--porcelain", "--untracked-files=all"):
        raise InventoryError("reference worktree is not clean")
    reference = manifest.get("reference")
    if not isinstance(reference, dict):
        raise InventoryError("reference configuration is missing")
    reference.update(
        {
            "expected_head": expected_head,
            "tracked_content_hash": _tracked_content_hash(resolved),
            "worktree": str(resolved),
        }
    )
    _write_json(manifest_path, manifest)


def _isolated_namespace(manifest: dict[str, Any], black_box: dict[str, Any]) -> str:
    reference_namespace = manifest.get("reference", {}).get("persistence_namespace")
    target_namespace = manifest.get("target", {}).get("persistence_namespace")
    fixture_namespace = black_box.get("persistence_namespace")
    if not all(isinstance(value, str) and value for value in (reference_namespace, target_namespace, fixture_namespace)):
        raise InventoryError("reference, target, and black-box persistence namespaces are required")
    if target_namespace != TARGET_DATABASE_NAME:
        raise InventoryError(
            f"target persistence namespace must match Settings: {TARGET_DATABASE_NAME}"
        )
    if reference_namespace != fixture_namespace or reference_namespace == target_namespace:
        raise InventoryError("reference black-box persistence namespace is not isolated")
    return reference_namespace


def _isolated_environment(manifest: dict[str, Any], black_box: dict[str, Any]) -> dict[str, str]:
    namespace = _isolated_namespace(manifest, black_box)
    reference_sources = black_box.get("environment_from")
    target_sources = black_box.get("target_environment_from")
    if not isinstance(reference_sources, dict) or not isinstance(target_sources, dict):
        raise InventoryError("black-box environment mappings are required")
    if set(reference_sources) != set(target_sources) or not reference_sources:
        raise InventoryError("reference and target environment mappings must cover the same resources")
    environment = {**os.environ, "CLAWITH_PERSISTENCE_NAMESPACE": namespace}
    for application_name, reference_name in reference_sources.items():
        target_name = target_sources.get(application_name)
        if (
            not isinstance(application_name, str)
            or not application_name
            or not isinstance(reference_name, str)
            or not reference_name
            or not isinstance(target_name, str)
            or not target_name
        ):
            raise InventoryError("black-box environment mappings require non-empty string names")
        reference_value = os.environ.get(reference_name)
        target_value = os.environ.get(target_name)
        if not reference_value or not target_value:
            raise InventoryError(
                f"isolated persistence environment is missing for {application_name}: "
                f"{reference_name}, {target_name}"
            )
        if reference_value == target_value:
            raise InventoryError(f"reference and target share persistence resource {application_name}")
        environment[application_name] = reference_value
    return environment


def check_reference(
    manifest_path: Path,
    expected_head: str,
    *,
    require_clean: bool,
    boot_smoke: bool,
    black_box_manifest_path: Path,
    worktree_override: Path | None = None,
    python_override: Path | None = None,
) -> None:
    manifest, _ = _load_validated(manifest_path)
    worktree = _resolve_reference_worktree(
        manifest,
        worktree_override,
        allow_portable_override=True,
    )
    _verify_reference(manifest, worktree, expected_head, require_clean=require_clean)
    reference_python = _resolve_reference_python(worktree, python_override)
    black_box = _load_json(black_box_manifest_path)
    if black_box.get("schema_version") != SCHEMA_VERSION:
        raise InventoryError(f"unsupported black-box schema: {black_box.get('schema_version')}")
    environment = _isolated_environment(manifest, black_box)
    backend = worktree / "backend"
    if boot_smoke:
        _run_fixture(
            backend,
            [str(reference_python), "-c", "from app.main import app; assert app is not None"],
            environment,
            "boot-smoke",
        )
    fixtures = black_box.get("fixtures")
    if not isinstance(fixtures, list) or not fixtures:
        raise InventoryError("black-box manifest requires at least one fixture")
    for fixture in fixtures:
        if not isinstance(fixture, dict) or not fixture.get("required", True):
            continue
        fixture_id = fixture.get("id")
        argv = fixture.get("argv")
        if not isinstance(fixture_id, str) or not isinstance(argv, list) or not all(
            isinstance(part, str) for part in argv
        ):
            raise InventoryError("black-box fixture requires string id and argv")
        command = [str(reference_python) if part == "{python}" else part for part in argv]
        cwd = worktree / fixture.get("cwd", "backend")
        _run_fixture(cwd, command, environment, fixture_id)


def _run_fixture(cwd: Path, argv: list[str], environment: dict[str, str], fixture_id: str) -> None:
    try:
        subprocess.run(argv, cwd=cwd, env=environment, check=True, timeout=120)
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        raise InventoryError(f"reference fixture failed: {fixture_id}: {exc}") from exc


def release_reference_preflight(manifest_path: Path, worktree: Path) -> Path:
    manifest, (_, _, nonterminal) = _load_validated(manifest_path)
    if nonterminal:
        raise InventoryError(f"reference release blocked: nonterminal={nonterminal}")
    if not manifest.get("reference", {}).get("worktree"):
        raise InventoryError("reference worktree must be recorded in the manifest before release")
    resolved = _resolve_reference_worktree(manifest, worktree)
    current = _backend_root().parent.resolve()
    if resolved == current:
        raise InventoryError("refusing to remove the active target worktree")
    expected = manifest.get("reference", {}).get("expected_head")
    if not isinstance(expected, str) or not expected:
        raise InventoryError("reference expected_head is not configured")
    _verify_reference(manifest, resolved, expected, require_clean=True)
    registered = {
        Path(line.removeprefix("worktree ")).resolve()
        for line in _git(current, "worktree", "list", "--porcelain").splitlines()
        if line.startswith("worktree ")
    }
    if resolved not in registered:
        raise InventoryError(f"reference path is not a registered git worktree: {resolved}")
    return resolved


def release_reference(manifest_path: Path, worktree: Path, *, preflight_only: bool) -> None:
    resolved = release_reference_preflight(manifest_path, worktree)
    if preflight_only:
        return
    try:
        subprocess.run(
            ["git", "-C", str(_backend_root().parent), "worktree", "remove", str(resolved)],
            check=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise InventoryError(f"reference worktree removal failed: {exc}") from exc
    if resolved.exists():
        raise InventoryError("reference worktree removal did not remove the complete path")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    build = subparsers.add_parser("build")
    build.add_argument("--manifest", type=Path, required=True)
    build.add_argument("--source-root", type=Path, default=_backend_root())

    check = subparsers.add_parser("check")
    check.add_argument("--manifest", type=Path, required=True)
    check.add_argument("--require-zero-unreviewed", action="store_true")
    check.add_argument("--require-zero-disposition-missing", action="store_true")
    check.add_argument("--require-all-terminal", action="store_true")

    move = subparsers.add_parser("transition")
    move.add_argument("--manifest", type=Path, required=True)
    move.add_argument("--id", required=True)
    move.add_argument("--to", choices=sorted(STATES - {"unreviewed"}), required=True)
    move.add_argument("--evidence", type=Path, required=True)

    reference = subparsers.add_parser("check-reference")
    reference.add_argument("--manifest", type=Path, required=True)
    reference.add_argument("--expected-head", required=True)
    reference.add_argument("--require-clean", action="store_true")
    reference.add_argument("--boot-smoke", action="store_true")
    reference.add_argument("--black-box-manifest", type=Path, required=True)
    reference.add_argument("--worktree", type=Path)
    reference.add_argument("--python", type=Path)

    bind = subparsers.add_parser("bind-reference")
    bind.add_argument("--manifest", type=Path, required=True)
    bind.add_argument("--worktree", type=Path, required=True)
    bind.add_argument("--expected-head", required=True)

    release = subparsers.add_parser("release-reference")
    release.add_argument("--manifest", type=Path, required=True)
    release.add_argument("--require-all-terminal", action="store_true", required=True)
    release.add_argument("--worktree", type=Path, required=True)
    release.add_argument("--preflight-only", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "build":
            manifest = build_manifest(args.manifest, args.source_root)
            print(f"entries={len(manifest['entries'])} source_digest={manifest['source_digest']}")
        elif args.command == "check":
            unreviewed, disposition_missing, nonterminal = check_manifest(
                args.manifest,
                require_zero_unreviewed=args.require_zero_unreviewed,
                require_zero_disposition_missing=args.require_zero_disposition_missing,
                require_all_terminal=args.require_all_terminal,
            )
            print(
                f"unreviewed={unreviewed} disposition_missing={disposition_missing} "
                f"nonterminal={nonterminal}"
            )
        elif args.command == "transition":
            transition(args.manifest, args.id, args.to, args.evidence)
            print(f"transitioned={args.id} state={args.to}")
        elif args.command == "check-reference":
            check_reference(
                args.manifest,
                args.expected_head,
                require_clean=args.require_clean,
                boot_smoke=args.boot_smoke,
                black_box_manifest_path=args.black_box_manifest,
                worktree_override=args.worktree,
                python_override=args.python,
            )
            print("reference=valid")
        elif args.command == "bind-reference":
            bind_reference(args.manifest, args.worktree, args.expected_head)
            print("reference=bound")
        elif args.command == "release-reference":
            release_reference(args.manifest, args.worktree, preflight_only=args.preflight_only)
            print("reference=release-ready" if args.preflight_only else "reference=released")
    except InventoryError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
