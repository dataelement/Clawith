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
PRODUCT_INPUT_OWNERS = frozenset({"session", "a2a", "group", "trigger", "heartbeat", "channel"})
SCHEMA_ONLY_OWNERS = frozenset({"run", "context", "session", "a2a", "group", "trigger", "heartbeat", "channel"})
OWNER_IMPLEMENTATION_FILES = {
    "model": {"execution.py", "adapters.py", "continuation.py"},
    "tool": {"contracts.py", "execution.py", "mcp.py"},
    "workspace": {"files.py", "skills.py"},
    "run": {"contracts.py", "snapshot.py", "engine.py", "lifecycle.py"},
    "session": {"attachments.py"},
    "a2a": {"temp_files.py"},
    "group": {"attachments.py"},
    "channel": {"adapters.py", "chunks.py", "context_repository.py", "contracts.py", "reply_context.py",
        "settings.py", "sync_cursor.py", "transport.py", "providers/dingtalk.py", "providers/discord.py",
        "providers/discord_gateway.py", "providers/feishu.py", "providers/registry.py", "providers/teams.py",
        "providers/wechat.py", "providers/wecom.py"},
}


def _implementation_approvals(manifest: dict) -> tuple[frozenset[str], frozenset[str]]:
    runtime, products = set(), set()
    for row in manifest["owners"]:
        if row["state"] != "contract_approved":
            continue
        owner, phase, artifact = row["owner_id"], row["implementation_phase"], row["contract_artifact"]
        if owner in CORE_RUNTIME_OWNERS and phase == 4 and artifact in {
                "specs/backend-core-runtime.md", "specs/backend-product-inputs.md", "specs/backend-product-input-continuations.md"}:
            runtime.add(owner)
        if owner in PRODUCT_INPUT_OWNERS and phase == 5 and artifact in {
                "specs/backend-product-inputs.md", "specs/backend-product-input-continuations.md"}:
            products.add(owner)
    return frozenset(runtime), frozenset(products)


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
    product_implementation_owners: frozenset[str] = frozenset(),
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
        paths = [path for path in package_root.rglob("*") if "__pycache__" not in path.relative_to(package_root).parts]
        if any(path.is_symlink() for path in paths):
            raise SkeletonError(f"{owner_id} package contains a symlink")
        entries = {path.relative_to(package_root).as_posix() for path in paths if path.is_file()}
        allowed = {"__init__.py"}
        if owner_id in approved_owners:
            if (
                implementation_phase == 2
                or (implementation_phase == 3 and owner_id in EXECUTION_DEPENDENCY_OWNERS)
                or (implementation_phase == 4 and owner_id in CORE_RUNTIME_OWNERS & runtime_implementation_owners)
                or (implementation_phase == 5 and owner_id in PRODUCT_INPUT_OWNERS & product_implementation_owners)
            ):
                allowed |= SERVICE_FILES
                allowed |= OWNER_IMPLEMENTATION_FILES.get(owner_id, set())
                if owner_id in CRYPTO_OWNERS:
                    allowed.add("crypto.py")
            elif owner_id in SCHEMA_ONLY_OWNERS and schema_wave in {"S1", "S2"}:
                allowed |= {"models.py", "AGENTS.md"}
        allowed_directories = {parent.as_posix() for filename in allowed for parent in Path(filename).parents
            if parent != Path(".")}
        directories = {path.relative_to(package_root).as_posix() for path in paths if path.is_dir()}
        if "__init__.py" not in entries or not entries <= allowed or not directories <= allowed_directories:
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
    runtime_approved, product_approved = _implementation_approvals(manifest)
    actual_contracts = _validate_owner_package_skeleton(
        MODULES_ROOT, owner_contracts, approved_owners=approved, runtime_implementation_owners=runtime_approved,
        product_implementation_owners=product_approved,
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


@pytest.mark.parametrize("owner", sorted(PRODUCT_INPUT_OWNERS))
def test_g006_implementation_approval_allows_product_services(tmp_path: Path, owner: str) -> None:
    owners = _canonical_owner_contracts()
    _write_skeleton(tmp_path, (name for name, _, _ in owners))
    (tmp_path / owner / "public.py").touch()
    _validate_owner_package_skeleton(tmp_path, owners, approved_owners=frozenset({owner}),
        product_implementation_owners=frozenset({owner}))
    with pytest.raises(SkeletonError, match=owner):
        _validate_owner_package_skeleton(tmp_path, owners, product_implementation_owners=frozenset({owner}))


@pytest.mark.parametrize("extra", ["providers/unknown.py", "providers/nested/feishu.py", "unknown.py", "public.py/hidden.py"])
def test_channel_provider_roster_does_not_open_arbitrary_paths(tmp_path: Path, extra: str) -> None:
    owners = _canonical_owner_contracts()
    _write_skeleton(tmp_path, (name for name, _, _ in owners))
    provider = tmp_path / "channel/providers/feishu.py"
    provider.parent.mkdir()
    provider.touch()
    options = {"approved_owners": frozenset({"channel"}), "product_implementation_owners": frozenset({"channel"})}
    _validate_owner_package_skeleton(tmp_path, owners, **options)
    unwanted = tmp_path / "channel" / extra
    unwanted.parent.mkdir(parents=True, exist_ok=True)
    unwanted.touch()
    with pytest.raises(SkeletonError, match="channel"):
        _validate_owner_package_skeleton(tmp_path, owners, **options)


def test_s3_cannot_use_g006_implementation_approval(tmp_path: Path) -> None:
    owners = _canonical_owner_contracts()
    _write_skeleton(tmp_path, (name for name, _, _ in owners))
    (tmp_path / "okr/public.py").touch()
    with pytest.raises(SkeletonError, match="okr"):
        _validate_owner_package_skeleton(tmp_path, owners, approved_owners=frozenset({"okr"}),
            product_implementation_owners=frozenset({"okr"}), runtime_implementation_owners=frozenset({"okr"}))


def test_allowed_provider_path_cannot_be_a_symlink(tmp_path: Path) -> None:
    owners = _canonical_owner_contracts()
    _write_skeleton(tmp_path, (name for name, _, _ in owners))
    provider = tmp_path / "channel/providers/feishu.py"
    provider.parent.mkdir()
    provider.symlink_to(tmp_path / "channel/__init__.py")
    with pytest.raises(SkeletonError, match="symlink"):
        _validate_owner_package_skeleton(tmp_path, owners, approved_owners=frozenset({"channel"}),
            product_implementation_owners=frozenset({"channel"}))


def test_implementation_approval_requires_owner_phase_state_and_contract() -> None:
    rows = [
        {"owner_id": "run", "implementation_phase": 4, "state": "contract_approved", "contract_artifact": "specs/backend-product-inputs.md"},
        {"owner_id": "context", "implementation_phase": 4, "state": "contract_approved", "contract_artifact": "specs/backend-core-runtime.md"},
        {"owner_id": "session", "implementation_phase": 5, "state": "contract_approved", "contract_artifact": "specs/backend-product-inputs.md"},
    ]
    assert _implementation_approvals({"owners": rows}) == (frozenset({"run", "context"}), frozenset({"session"}))
    for change in ({"state": "pending"}, {"implementation_phase": 6}, {"contract_artifact": "specs/unapproved.md"}):
        assert _implementation_approvals({"owners": [{**row, **change} for row in rows]}) == (frozenset(), frozenset())
