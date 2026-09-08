from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[2]
MODULES_ROOT = BACKEND_ROOT / "app" / "modules"
OWNER_CONTRACTS = BACKEND_ROOT / "rewrite" / "owner-contracts.json"
EXPECTED_OWNER_COUNT = 34

OwnerContract = tuple[str, str, int]
SERVICE_FILES = {"__init__.py", "models.py", "public.py", "repository.py", "AGENTS.md"}
CRYPTO_OWNERS = frozenset({"credential", "auth"})
EXECUTION_DEPENDENCY_OWNERS = frozenset({"workspace", "tool", "capability_market"})
CORE_RUNTIME_OWNERS = frozenset({"run", "context"})
SCHEMA_ONLY_OWNERS = frozenset({"run", "context", "session", "a2a", "group", "trigger", "heartbeat", "channel"})
OWNER_IMPLEMENTATION_FILES = {
    "model": {"execution.py", "adapters.py", "continuation.py"},
    "tool": {"contracts.py", "execution.py", "mcp.py"},
    "workspace": {"files.py", "skills.py"},
    "run": {"contracts.py", "snapshot.py", "engine.py", "lifecycle.py"},
}


class SkeletonError(AssertionError):
    pass


def _canonical_owner_contracts() -> list[OwnerContract]:
    manifest = json.loads(OWNER_CONTRACTS.read_text(encoding="utf-8"))
    return [
        (row["owner_id"], row["schema_wave"], row["implementation_phase"])
        for row in manifest["owners"]
    ]


def _owner_contract_map(owner_contracts: Iterable[OwnerContract]) -> dict[str, tuple[str, int]]:
    rows = list(owner_contracts)
    owner_ids = [owner_id for owner_id, _, _ in rows]
    duplicate_owner_ids = sorted(
        owner_id for owner_id in set(owner_ids) if owner_ids.count(owner_id) > 1
    )
    if duplicate_owner_ids:
        raise SkeletonError(f"duplicate owners: {duplicate_owner_ids}")
    if len(rows) != EXPECTED_OWNER_COUNT:
        raise SkeletonError(f"expected {EXPECTED_OWNER_COUNT} owners, found {len(rows)}")
    return {
        owner_id: (schema_wave, implementation_phase)
        for owner_id, schema_wave, implementation_phase in rows
    }


def _validate_owner_package_skeleton(
    modules_root: Path,
    owner_contracts: Iterable[OwnerContract],
    *,
    approved_owners: frozenset[str] = frozenset(),
    runtime_implementation_owners: frozenset[str] = frozenset(),
) -> dict[str, tuple[str, int]]:
    expected_contracts = _owner_contract_map(owner_contracts)
    expected_owner_ids = set(expected_contracts)
    actual_owner_ids = {path.name for path in modules_root.iterdir() if path.is_dir() and path.name != "__pycache__"}

    missing_owner_ids = sorted(expected_owner_ids - actual_owner_ids)
    if missing_owner_ids:
        raise SkeletonError(f"missing owner packages: {missing_owner_ids}")
    extra_owner_ids = sorted(actual_owner_ids - expected_owner_ids)
    if extra_owner_ids:
        raise SkeletonError(f"extra owner packages: {extra_owner_ids}")

    root_files = sorted(path.name for path in modules_root.iterdir() if path.is_file())
    if root_files != ["__init__.py"]:
        raise SkeletonError(f"unexpected modules root files: {root_files}")
    if (modules_root / "__init__.py").read_bytes():
        raise SkeletonError("modules package marker must be empty")

    for owner_id, (schema_wave, implementation_phase) in expected_contracts.items():
        package_root = modules_root / owner_id
        entries = {path.name for path in package_root.iterdir() if path.name != "__pycache__"}
        allowed = {"__init__.py"}
        if owner_id in approved_owners:
            if (
                implementation_phase == 2
                or (implementation_phase == 3 and owner_id in EXECUTION_DEPENDENCY_OWNERS)
                or (implementation_phase == 4 and owner_id in CORE_RUNTIME_OWNERS & runtime_implementation_owners)
            ):
                allowed |= SERVICE_FILES
                allowed |= OWNER_IMPLEMENTATION_FILES.get(owner_id, set())
                if owner_id in CRYPTO_OWNERS:
                    allowed.add("crypto.py")
            elif owner_id in SCHEMA_ONLY_OWNERS and schema_wave in {"S1", "S2"}:
                allowed |= {"models.py", "AGENTS.md"}
        if "__init__.py" not in entries or not entries <= allowed:
            raise SkeletonError(
                f"{owner_id} ({schema_wave}/phase-{implementation_phase}) has unexpected implementation: {entries}"
            )
        if (package_root / "__init__.py").read_bytes():
            raise SkeletonError(f"{owner_id} package marker must be empty")

    return expected_contracts


def _write_skeleton(root: Path, owner_ids: Iterable[str]) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "__init__.py").touch()
    for owner_id in owner_ids:
        owner_root = root / owner_id
        owner_root.mkdir()
        (owner_root / "__init__.py").touch()


def test_owner_package_skeleton_matches_the_canonical_contract_ledger() -> None:
    owner_contracts = _canonical_owner_contracts()
    manifest = json.loads(OWNER_CONTRACTS.read_text(encoding="utf-8"))
    approved = frozenset(row["owner_id"] for row in manifest["owners"] if row["state"] == "contract_approved")
    runtime_approved = frozenset(
        row["owner_id"] for row in manifest["owners"]
        if row["state"] == "contract_approved" and row["contract_artifact"] == "specs/backend-core-runtime.md"
    )
    actual_contracts = _validate_owner_package_skeleton(
        MODULES_ROOT, owner_contracts, approved_owners=approved, runtime_implementation_owners=runtime_approved,
    )

    assert len(owner_contracts) == EXPECTED_OWNER_COUNT
    assert len(actual_contracts) == EXPECTED_OWNER_COUNT
    assert actual_contracts == {
        owner_id: (schema_wave, implementation_phase)
        for owner_id, schema_wave, implementation_phase in owner_contracts
    }


def test_owner_package_skeleton_rejects_a_missing_owner(tmp_path: Path) -> None:
    owner_contracts = _canonical_owner_contracts()
    _write_skeleton(tmp_path, (owner_id for owner_id, _, _ in owner_contracts[1:]))

    with pytest.raises(SkeletonError, match="missing owner packages"):
        _validate_owner_package_skeleton(tmp_path, owner_contracts)


def test_owner_package_skeleton_rejects_an_extra_owner(tmp_path: Path) -> None:
    owner_contracts = _canonical_owner_contracts()
    _write_skeleton(tmp_path, (owner_id for owner_id, _, _ in owner_contracts))
    extra_owner = tmp_path / "unexpected_owner"
    extra_owner.mkdir()
    (extra_owner / "__init__.py").touch()

    with pytest.raises(SkeletonError, match="extra owner packages"):
        _validate_owner_package_skeleton(tmp_path, owner_contracts)


def test_owner_package_skeleton_rejects_duplicate_ledger_owners(tmp_path: Path) -> None:
    owner_contracts = _canonical_owner_contracts()
    _write_skeleton(tmp_path, (owner_id for owner_id, _, _ in owner_contracts))

    with pytest.raises(SkeletonError, match="duplicate owners"):
        _validate_owner_package_skeleton(tmp_path, [*owner_contracts, owner_contracts[0]])


def test_approved_foundation_and_execution_dependencies_can_implement(tmp_path: Path) -> None:
    owners = _canonical_owner_contracts()
    _write_skeleton(tmp_path, (owner_id for owner_id, _, _ in owners))
    (tmp_path / "identity_tenant/public.py").write_text("class IdentityService: pass\n", encoding="utf-8")
    (tmp_path / "run/models.py").write_text("# schema only\n", encoding="utf-8")
    (tmp_path / "workspace/public.py").write_text("class Workspace: pass\n", encoding="utf-8")
    approved = frozenset({"identity_tenant", "run", "workspace"})
    _validate_owner_package_skeleton(tmp_path, owners, approved_owners=approved)
    with pytest.raises(SkeletonError, match="identity_tenant"):
        _validate_owner_package_skeleton(tmp_path, owners)
    (tmp_path / "session/public.py").write_text("class Session: pass\n", encoding="utf-8")
    with pytest.raises(SkeletonError, match="session"):
        _validate_owner_package_skeleton(tmp_path, owners, approved_owners=approved)


@pytest.mark.parametrize("owner", ["session", "a2a", "group", "trigger", "heartbeat", "channel"])
def test_s2_product_approval_permits_schema_but_not_services(tmp_path: Path, owner: str) -> None:
    owners = _canonical_owner_contracts()
    _write_skeleton(tmp_path, (owner_id for owner_id, _, _ in owners))
    (tmp_path / owner / "models.py").write_text("# schema only\n", encoding="utf-8")
    approved = frozenset({owner})
    _validate_owner_package_skeleton(tmp_path, owners, approved_owners=approved)
    (tmp_path / owner / "public.py").write_text("class ProductService: pass\n", encoding="utf-8")
    with pytest.raises(SkeletonError, match=owner):
        _validate_owner_package_skeleton(tmp_path, owners, approved_owners=approved)


@pytest.mark.parametrize("filename", ["public.py", "snapshot.py", "lifecycle.py", "engine.py"])
def test_run_schema_approval_does_not_allow_runtime_service(tmp_path: Path, filename: str) -> None:
    owners = _canonical_owner_contracts()
    _write_skeleton(tmp_path, (owner_id for owner_id, _, _ in owners))
    (tmp_path / "run" / filename).write_text("class Runner: pass\n", encoding="utf-8")
    with pytest.raises(SkeletonError, match="run"):
        _validate_owner_package_skeleton(tmp_path, owners, approved_owners=frozenset({"run"}))


@pytest.mark.parametrize("owner,filename", [("run", "public.py"), ("context", "public.py"),
    ("run", "snapshot.py"), ("run", "lifecycle.py"), ("run", "engine.py")])
def test_g005_contract_approval_allows_only_runtime_owner_implementation(tmp_path: Path, owner: str, filename: str) -> None:
    owners = _canonical_owner_contracts()
    _write_skeleton(tmp_path, (owner_id for owner_id, _, _ in owners))
    (tmp_path / owner / filename).write_text("class Service: pass\n", encoding="utf-8")
    approved = frozenset({owner, "session"})
    _validate_owner_package_skeleton(tmp_path, owners, approved_owners=approved,
                                    runtime_implementation_owners=frozenset({owner}))
    (tmp_path / "session/public.py").write_text("class Session: pass\n", encoding="utf-8")
    with pytest.raises(SkeletonError, match="session"):
        _validate_owner_package_skeleton(tmp_path, owners, approved_owners=approved,
                                        runtime_implementation_owners=frozenset({owner}))


@pytest.mark.parametrize("owner,filename", [("model", "adapters.py"), ("tool", "mcp.py"), ("workspace", "skills.py")])
def test_execution_files_stay_with_their_approved_owner(tmp_path: Path, owner: str, filename: str) -> None:
    owners = _canonical_owner_contracts()
    _write_skeleton(tmp_path, (owner_id for owner_id, _, _ in owners))
    (tmp_path / owner / filename).write_text("# owner implementation\n", encoding="utf-8")
    approved = frozenset({owner, "agent"})
    _validate_owner_package_skeleton(tmp_path, owners, approved_owners=approved)
    (tmp_path / "agent" / filename).write_text("# misplaced implementation\n", encoding="utf-8")
    with pytest.raises(SkeletonError, match="agent"):
        _validate_owner_package_skeleton(tmp_path, owners, approved_owners=approved)


def test_only_credential_and_auth_may_add_g003_crypto_modules(tmp_path: Path) -> None:
    owners = _canonical_owner_contracts()
    _write_skeleton(tmp_path, (owner_id for owner_id, _, _ in owners))
    (tmp_path / "credential/crypto.py").write_text("# credential envelope\n", encoding="utf-8")
    (tmp_path / "auth/crypto.py").write_text("# password and token hashes\n", encoding="utf-8")
    approved = frozenset({"credential", "auth", "agent"})
    _validate_owner_package_skeleton(tmp_path, owners, approved_owners=approved)

    (tmp_path / "agent/crypto.py").write_text("# misplaced crypto\n", encoding="utf-8")
    with pytest.raises(SkeletonError, match="agent"):
        _validate_owner_package_skeleton(tmp_path, owners, approved_owners=approved)


def test_g003_owner_packages_cannot_add_transport_api_modules(tmp_path: Path) -> None:
    owners = _canonical_owner_contracts()
    _write_skeleton(tmp_path, (owner_id for owner_id, _, _ in owners))
    (tmp_path / "auth/api.py").write_text("# premature HTTP adapter\n", encoding="utf-8")

    with pytest.raises(SkeletonError, match="auth"):
        _validate_owner_package_skeleton(tmp_path, owners, approved_owners=frozenset({"auth"}))
