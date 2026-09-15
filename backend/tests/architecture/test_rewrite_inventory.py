from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
import sys
from copy import deepcopy
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).parents[2] / "scripts/rewrite_inventory.py"
_CANONICAL_OWNER_DAG = Path(__file__).parents[2] / "rewrite/owner-dag.json"
sys.path.insert(0, str(_SCRIPT.parent))
_SPEC = importlib.util.spec_from_file_location("rewrite_inventory", _SCRIPT)
assert _SPEC is not None and _SPEC.loader is not None
rewrite_inventory = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = rewrite_inventory
_SPEC.loader.exec_module(rewrite_inventory)


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _fixture_source(tmp_path: Path) -> Path:
    _write(
        tmp_path / "app/config.py",
        'class Settings:\n    API_PREFIX: str = "/api"\n',
    )
    _write(
        tmp_path / "app/api/items.py",
        '''from fastapi import APIRouter

CALLBACK = "/callback"
router = APIRouter(prefix="/items")

@router.get("")
async def list_items(): ...

@router.get("/")
async def list_items_with_slash(): ...

@router.post("/{item_id}")
async def create_item(): ...

@router.get(CALLBACK)
async def callback(): ...

@router.websocket("/{item_id}/events")
async def events(): ...
''',
    )
    _write(
        tmp_path / "app/main.py",
        '''from contextlib import asynccontextmanager
from fastapi import FastAPI
from app.api.items import router as items_router

settings = object()

@asynccontextmanager
async def lifespan(app):
    await seed_builtin_tools()
    task_specs = [("trigger", start_trigger_daemon()), ("feishu", feishu_ws_manager.start_all())]
    try:
        yield
    finally:
        await realtime_router.stop()
        await close_redis()

app = FastAPI(lifespan=lifespan)
app.include_router(items_router, prefix=settings.API_PREFIX)

@app.get("/health")
async def health(): ...
''',
    )
    return tmp_path


def _artifact(path: Path) -> dict[str, str]:
    return {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def _complete_row(row: dict[str, object], artifact: dict[str, str], *, disposition: str) -> None:
    row["disposition"] = disposition
    row["behavior_evidence"] = [artifact]
    row["consumer_evidence"] = [artifact]
    row["planned_gate"] = "tests/acceptance/test_owner.py"
    row["target_owner_id"] = None if disposition == "delete" else "agent"


def _canonical_owner_manifest() -> dict[str, object]:
    path = Path(__file__).parents[2] / "rewrite/owner-contracts.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    for owner in manifest["owners"]:
        owner.pop("amendment_receipts", None)
        owner.update(
            {
                "state": "unreviewed",
                "contract_artifact": None,
                "contract_hash": None,
                "evidence": [],
            }
        )
    return manifest


def test_fresh_owner_fixture_excludes_existing_approval_history() -> None:
    manifest = _canonical_owner_manifest()
    for owner in manifest["owners"]:
        assert owner["state"] == "unreviewed"
        assert owner["contract_artifact"] is None
        assert owner["contract_hash"] is None
        assert owner["evidence"] == []
        assert "amendment_receipts" not in owner


def _write_owner_contract_fixture(rewrite_dir: Path, owner_manifest: dict[str, object]) -> None:
    rewrite_dir.mkdir(parents=True, exist_ok=True)
    (rewrite_dir / "owner-contracts.json").write_text(json.dumps(owner_manifest), encoding="utf-8")
    (rewrite_dir / "owner-dag.json").write_text(
        _CANONICAL_OWNER_DAG.read_text(encoding="utf-8"),
        encoding="utf-8",
    )


def _authority_manifest(
    tmp_path: Path,
    *,
    authority_path: str,
    ignored_pattern: str | None = None,
    track_authority: bool,
    authority_hash: str | None = None,
) -> Path:
    repo_root = tmp_path / "repo"
    source = _fixture_source(repo_root / "backend/source")
    manifest_path = repo_root / "backend/rewrite/coverage.json"
    authority = repo_root / authority_path
    _write(authority, "approved authority\n")
    evidence = repo_root / "backend/rewrite/disposition-evidence/endpoint-lifecycle-dispositions.json"
    evidence.parent.mkdir(parents=True, exist_ok=True)
    evidence.write_text(
        json.dumps(
            {
                "authorities": [
                    {
                        "path": authority_path,
                        "sha256": authority_hash or hashlib.sha256(authority.read_bytes()).hexdigest(),
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    manifest = rewrite_inventory.build_manifest(manifest_path, source)
    _complete_row(manifest["entries"][0], _artifact(evidence), disposition="delete")
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    subprocess.run(["git", "-C", str(repo_root), "init", "--quiet"], check=True)
    if ignored_pattern is not None:
        _write(repo_root / ".gitignore", ignored_pattern)
    if track_authority:
        subprocess.run(["git", "-C", str(repo_root), "add", "--", authority_path], check=True)
    return manifest_path


def _approve_owner(
    owner_manifest: dict[str, object],
    owner_id: str,
    contract_artifact: Path,
    evidence: Path,
) -> str:
    contract_hash = hashlib.sha256(contract_artifact.read_bytes()).hexdigest()
    evidence_record = _artifact(evidence)
    owners = owner_manifest["owners"]
    assert isinstance(owners, list)
    owners_by_id = {row["owner_id"]: row for row in owners}
    dag = json.loads(_CANONICAL_OWNER_DAG.read_text(encoding="utf-8"))
    dependencies_by_owner = {row["owner_id"]: row["depends_on"] for row in dag["owners"]}

    def approve_with_dependencies(current_owner_id: str) -> None:
        for dependency in dependencies_by_owner[current_owner_id]:
            approve_with_dependencies(dependency)
        owner = owners_by_id[current_owner_id]
        if owner["state"] == "contract_approved":
            return
        owner.update(
            {
                "state": "contract_approved",
                "contract_artifact": str(contract_artifact),
                "contract_hash": contract_hash,
                "evidence": [evidence_record],
            }
        )

    approve_with_dependencies(owner_id)
    return contract_hash


def test_discover_finds_only_mounted_routes_and_lifespan_operations(tmp_path: Path) -> None:
    source = _fixture_source(tmp_path)

    entries = rewrite_inventory.discover(source)
    by_id = {entry.id: entry for entry in entries}

    assert "GET:/api/items" in by_id
    assert "GET:/api/items/" in by_id
    assert "POST:/api/items/{item_id}" in by_id
    assert "GET:/api/items/callback" in by_id
    assert by_id["WEBSOCKET:/api/items/{item_id}/events"].kind == "websocket"
    assert "GET:/health" in by_id
    assert "LIFECYCLE:application:lifespan" in by_id
    assert "LIFECYCLE:bootstrap:seed_builtin_tools" in by_id
    assert "LIFECYCLE:trigger:start_trigger_daemon" in by_id
    assert "LIFECYCLE:channel:feishu_ws_manager_start_all" in by_id
    assert "LIFECYCLE:realtime:realtime_router_stop" in by_id
    assert "LIFECYCLE:infrastructure:close_redis" in by_id


def test_discover_rejects_missing_mounted_source(tmp_path: Path) -> None:
    source = _fixture_source(tmp_path)
    (source / "app/api/items.py").unlink()

    with pytest.raises(rewrite_inventory.InventoryError, match="mounted module is missing"):
        rewrite_inventory.discover(source)


def test_discover_rejects_duplicate_stable_ids(tmp_path: Path) -> None:
    source = _fixture_source(tmp_path)
    items = source / "app/api/items.py"
    items.write_text(
        items.read_text(encoding="utf-8")
        + '\n@router.get("")\nasync def duplicate_list(): ...\n',
        encoding="utf-8",
    )

    with pytest.raises(rewrite_inventory.InventoryError, match="duplicate stable IDs"):
        rewrite_inventory.discover(source)


def test_build_is_deterministic_and_preserves_review_fields(tmp_path: Path) -> None:
    source = _fixture_source(tmp_path / "source")
    manifest_path = tmp_path / "rewrite/coverage.json"
    first = rewrite_inventory.build_manifest(manifest_path, source)
    first["entries"][0]["planned_gate"] = "kept"
    manifest_path.write_text(json.dumps(first), encoding="utf-8")

    second = rewrite_inventory.build_manifest(manifest_path, source)
    rendered = manifest_path.read_text(encoding="utf-8")
    third = rewrite_inventory.build_manifest(manifest_path, source)

    assert second == third
    assert rendered == manifest_path.read_text(encoding="utf-8")
    assert second["entries"][0]["planned_gate"] == "kept"
    assert second["target"]["persistence_namespace"] == "clawith_target"


def test_manifest_rejects_target_namespace_divergence_from_settings(
    tmp_path: Path,
) -> None:
    source = _fixture_source(tmp_path / "source")
    manifest_path = tmp_path / "rewrite/coverage.json"
    manifest = rewrite_inventory.build_manifest(manifest_path, source)
    manifest["target"]["persistence_namespace"] = "clawith_target_rewrite"

    with pytest.raises(
        rewrite_inventory.InventoryError,
        match="target persistence namespace must match Settings: clawith_target",
    ):
        rewrite_inventory.validate_manifest(
            manifest,
            manifest_path,
            validate_artifact_hashes=False,
        )


def test_build_refuses_to_prune_a_reviewed_frozen_inventory(tmp_path: Path) -> None:
    source = _fixture_source(tmp_path / "source")
    manifest_path = tmp_path / "rewrite/coverage.json"
    manifest = rewrite_inventory.build_manifest(manifest_path, source)
    removed = next(row for row in manifest["entries"] if row["id"] == "GET:/api/items")
    removed["state"] = "disposition_approved"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    items = source / "app/api/items.py"
    items.write_text(
        items.read_text(encoding="utf-8").replace(
            '@router.get("")\nasync def list_items(): ...\n\n', ""
        ),
        encoding="utf-8",
    )

    with pytest.raises(rewrite_inventory.InventoryError, match="disappeared from discovery"):
        rewrite_inventory.build_manifest(manifest_path, source)


def test_check_reports_and_enforces_manifest_counts(tmp_path: Path) -> None:
    source = _fixture_source(tmp_path / "source")
    manifest_path = tmp_path / "rewrite/coverage.json"
    manifest = rewrite_inventory.build_manifest(manifest_path, source)

    assert rewrite_inventory.check_manifest(
        manifest_path,
        require_zero_unreviewed=False,
        require_zero_disposition_missing=False,
        require_all_terminal=False,
    ) == (len(manifest["entries"]), len(manifest["entries"]), len(manifest["entries"]))
    with pytest.raises(rewrite_inventory.InventoryError, match="unreviewed="):
        rewrite_inventory.check_manifest(
            manifest_path,
            require_zero_unreviewed=True,
            require_zero_disposition_missing=False,
            require_all_terminal=False,
        )


def test_evidence_hash_change_invalidates_manifest(tmp_path: Path) -> None:
    source = _fixture_source(tmp_path / "source")
    manifest_path = tmp_path / "rewrite/coverage.json"
    evidence = tmp_path / "evidence.txt"
    evidence.write_text("approved", encoding="utf-8")
    manifest = rewrite_inventory.build_manifest(manifest_path, source)
    _complete_row(manifest["entries"][0], _artifact(evidence), disposition="delete")
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    evidence.write_text("changed", encoding="utf-8")

    with pytest.raises(rewrite_inventory.InventoryError, match="evidence hash changed"):
        rewrite_inventory.check_manifest(
            manifest_path,
            require_zero_unreviewed=False,
            require_zero_disposition_missing=False,
            require_all_terminal=False,
        )


def test_disposition_authority_requires_tracked_content_with_matching_hash(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest_path = _authority_manifest(
        tmp_path,
        authority_path="backend/rewrite/backend-capability-coverage-matrix.md",
        track_authority=True,
    )

    rewrite_inventory.check_manifest(
        manifest_path,
        require_zero_unreviewed=False,
        require_zero_disposition_missing=False,
        require_all_terminal=False,
    )
    monkeypatch.chdir(manifest_path.parent.parent)
    rewrite_inventory.check_manifest(
        Path("rewrite/coverage.json"),
        require_zero_unreviewed=False,
        require_zero_disposition_missing=False,
        require_all_terminal=False,
    )


@pytest.mark.parametrize(
    ("authority_path", "ignored_pattern", "message"),
    [
        (".omx/plans/backend-capability-coverage-matrix.md", ".omx/\n", "authority path is ignored"),
        ("docs/backend-capability-coverage-matrix.md", None, "authority path is not Git-tracked"),
    ],
)
def test_disposition_authority_rejects_nonportable_paths(
    tmp_path: Path,
    authority_path: str,
    ignored_pattern: str | None,
    message: str,
) -> None:
    manifest_path = _authority_manifest(
        tmp_path,
        authority_path=authority_path,
        ignored_pattern=ignored_pattern,
        track_authority=False,
    )

    with pytest.raises(rewrite_inventory.InventoryError, match=message):
        rewrite_inventory.check_manifest(
            manifest_path,
            require_zero_unreviewed=False,
            require_zero_disposition_missing=False,
            require_all_terminal=False,
        )


def test_disposition_authority_rejects_hash_drift(tmp_path: Path) -> None:
    manifest_path = _authority_manifest(
        tmp_path,
        authority_path="backend/rewrite/backend-capability-coverage-matrix.md",
        track_authority=True,
        authority_hash="0" * 64,
    )

    with pytest.raises(rewrite_inventory.InventoryError, match="authority hash changed"):
        rewrite_inventory.check_manifest(
            manifest_path,
            require_zero_unreviewed=False,
            require_zero_disposition_missing=False,
            require_all_terminal=False,
        )


def test_transition_enforces_predecessor_disposition_and_evidence(tmp_path: Path) -> None:
    source = _fixture_source(tmp_path / "source")
    manifest_path = tmp_path / "rewrite/coverage.json"
    evidence = tmp_path / "evidence.txt"
    evidence.write_text("approved", encoding="utf-8")
    manifest = rewrite_inventory.build_manifest(manifest_path, source)
    row = manifest["entries"][0]
    _complete_row(row, _artifact(evidence), disposition="delete")
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    rewrite_inventory.transition(manifest_path, row["id"], "disposition_approved", evidence)
    rewrite_inventory.transition(manifest_path, row["id"], "deletion_approved", evidence)
    transitioned = json.loads(manifest_path.read_text(encoding="utf-8"))["entries"][0]

    assert transitioned["state"] == "deletion_approved"
    assert transitioned["removal_evidence"] == [
        {"path": "evidence.txt", "sha256": _artifact(evidence)["sha256"]}
    ]
    with pytest.raises(rewrite_inventory.InventoryError, match="illegal transition"):
        rewrite_inventory.transition(manifest_path, row["id"], "contract_approved", evidence)


def test_contract_transition_uses_the_canonical_owner_ledger(tmp_path: Path) -> None:
    source = _fixture_source(tmp_path / "source")
    manifest_path = tmp_path / "rewrite/coverage.json"
    evidence = tmp_path / "evidence.txt"
    contract = tmp_path / "agent-contract.md"
    evidence.write_text("approved", encoding="utf-8")
    contract.write_text("agent contract", encoding="utf-8")
    manifest = rewrite_inventory.build_manifest(manifest_path, source)
    row = manifest["entries"][0]
    _complete_row(row, _artifact(evidence), disposition="rewrite")
    row["owner_contract_id"] = "agent"
    owner_manifest = _canonical_owner_manifest()
    row["owner_contract_hash"] = _approve_owner(owner_manifest, "agent", contract, evidence)
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    _write_owner_contract_fixture(manifest_path.parent, owner_manifest)

    rewrite_inventory.transition(manifest_path, row["id"], "disposition_approved", evidence)
    rewrite_inventory.transition(manifest_path, row["id"], "contract_approved", evidence)

    assert json.loads(manifest_path.read_text(encoding="utf-8"))["entries"][0]["state"] == "contract_approved"


@pytest.mark.parametrize(
    ("defect", "message"),
    [
        ("wrong_top_level", "owner contract owners must be a list"),
        ("missing", "owner contract manifest is missing owners: agent"),
        ("duplicate", "duplicate owner in contract manifest: agent"),
        ("unapproved", "owner contract is not approved: agent"),
        ("owner_hash", "contract artifact hash mismatch for agent"),
        ("coverage_hash", "owner contract hash does not match"),
    ],
)
def test_contract_transition_rejects_invalid_canonical_owner_link(
    tmp_path: Path,
    defect: str,
    message: str,
) -> None:
    source = _fixture_source(tmp_path / "source")
    manifest_path = tmp_path / "rewrite/coverage.json"
    evidence = tmp_path / "evidence.txt"
    contract = tmp_path / "agent-contract.md"
    evidence.write_text("approved", encoding="utf-8")
    contract.write_text("agent contract", encoding="utf-8")
    manifest = rewrite_inventory.build_manifest(manifest_path, source)
    row = manifest["entries"][0]
    _complete_row(row, _artifact(evidence), disposition="rewrite")
    row["owner_contract_id"] = "agent"
    owner_manifest = _canonical_owner_manifest()
    contract_hash = hashlib.sha256(contract.read_bytes()).hexdigest()
    row["owner_contract_hash"] = contract_hash

    owners = owner_manifest["owners"]
    assert isinstance(owners, list)
    agent = next(owner for owner in owners if owner["owner_id"] == "agent")
    if defect == "wrong_top_level":
        owner_manifest = {"version": 1, "entries": owners}
    elif defect == "missing":
        owners.remove(agent)
    elif defect == "duplicate":
        owners.append(deepcopy(agent))
    elif defect == "unapproved":
        pass
    else:
        approved_hash = _approve_owner(owner_manifest, "agent", contract, evidence)
        if defect == "owner_hash":
            agent["contract_hash"] = "0" * 64
        elif defect == "coverage_hash":
            row["owner_contract_hash"] = "0" * 64
        else:
            raise AssertionError(f"unknown defect: {defect}")
        assert approved_hash == contract_hash

    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    _write_owner_contract_fixture(manifest_path.parent, owner_manifest)

    with pytest.raises(rewrite_inventory.InventoryError, match=message):
        rewrite_inventory.transition(manifest_path, row["id"], "disposition_approved", evidence)
        rewrite_inventory.transition(manifest_path, row["id"], "contract_approved", evidence)


def test_disposition_transition_rejects_target_outside_canonical_roster(tmp_path: Path) -> None:
    source = _fixture_source(tmp_path / "source")
    manifest_path = tmp_path / "rewrite/coverage.json"
    evidence = tmp_path / "evidence.txt"
    evidence.write_text("approved", encoding="utf-8")
    manifest = rewrite_inventory.build_manifest(manifest_path, source)
    row = manifest["entries"][0]
    _complete_row(row, _artifact(evidence), disposition="rewrite")
    row["target_owner_id"] = "definitely_not_an_owner"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    _write_owner_contract_fixture(manifest_path.parent, _canonical_owner_manifest())

    with pytest.raises(rewrite_inventory.InventoryError, match="not in the canonical roster"):
        rewrite_inventory.transition(manifest_path, row["id"], "disposition_approved", evidence)


def _init_reference(tmp_path: Path) -> tuple[Path, str, str]:
    worktree = tmp_path / "reference"
    worktree.mkdir()
    subprocess.run(["git", "init"], cwd=worktree, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "tests@example.com"], cwd=worktree, check=True)
    subprocess.run(["git", "config", "user.name", "Tests"], cwd=worktree, check=True)
    _write(worktree / "backend/app/main.py", "app = object()\n")
    subprocess.run(["git", "add", "."], cwd=worktree, check=True)
    subprocess.run(["git", "commit", "-m", "fixture"], cwd=worktree, check=True, capture_output=True)
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=worktree, check=True, capture_output=True, text=True
    ).stdout.strip()
    return worktree, head, rewrite_inventory._tracked_content_hash(worktree)


def test_reference_integrity_detects_head_content_and_dirty_changes(tmp_path: Path) -> None:
    worktree, head, content_hash = _init_reference(tmp_path)
    manifest = {
        "reference": {"expected_head": head, "tracked_content_hash": content_hash},
    }

    rewrite_inventory._verify_reference(manifest, worktree, head, require_clean=True)
    (worktree / "backend/app/main.py").write_text("app = None\n", encoding="utf-8")

    with pytest.raises(rewrite_inventory.InventoryError, match="not clean"):
        rewrite_inventory._verify_reference(manifest, worktree, head, require_clean=True)
    with pytest.raises(rewrite_inventory.InventoryError, match="tracked content changed"):
        rewrite_inventory._verify_reference(manifest, worktree, head, require_clean=False)


def test_portable_reference_override_still_enforces_head_hash_and_cleanliness(
    tmp_path: Path,
) -> None:
    worktree, head, content_hash = _init_reference(tmp_path)
    configured = tmp_path / "configured-reference"
    configured.mkdir()
    manifest = {
        "reference": {
            "expected_head": head,
            "tracked_content_hash": content_hash,
            "worktree": str(configured),
        },
    }

    with pytest.raises(rewrite_inventory.InventoryError, match="does not match"):
        rewrite_inventory._resolve_reference_worktree(manifest, worktree)
    resolved = rewrite_inventory._resolve_reference_worktree(
        manifest,
        worktree,
        allow_portable_override=True,
    )
    assert resolved == worktree.resolve()

    with pytest.raises(rewrite_inventory.InventoryError, match="HEAD mismatch"):
        rewrite_inventory._verify_reference(
            manifest,
            resolved,
            "definitely-not-the-head",
            require_clean=True,
        )
    (worktree / "backend/app/main.py").write_text("app = None\n", encoding="utf-8")
    with pytest.raises(rewrite_inventory.InventoryError, match="not clean"):
        rewrite_inventory._verify_reference(manifest, resolved, head, require_clean=True)
    with pytest.raises(rewrite_inventory.InventoryError, match="tracked content changed"):
        rewrite_inventory._verify_reference(manifest, resolved, head, require_clean=False)


def test_reference_python_override_must_be_an_executable_inside_worktree(
    tmp_path: Path,
) -> None:
    worktree = tmp_path / "reference"
    python = worktree / "backend/.venv/bin/python"
    python.parent.mkdir(parents=True)
    python.symlink_to(Path(sys.executable))

    assert rewrite_inventory._resolve_reference_python(worktree, python) == python

    outside = tmp_path / "outside-python"
    outside.write_text("#!/bin/sh\n", encoding="utf-8")
    outside.chmod(0o755)
    with pytest.raises(rewrite_inventory.InventoryError, match="virtual environment"):
        rewrite_inventory._resolve_reference_python(worktree, outside)
    python.unlink()
    python.mkdir()
    with pytest.raises(rewrite_inventory.InventoryError, match="executable file"):
        rewrite_inventory._resolve_reference_python(worktree, python)


def test_reference_python_override_normalizes_parent_alias_without_resolving_python(
    tmp_path: Path,
) -> None:
    worktree = tmp_path / "reference"
    python = worktree / "backend/.venv/bin/python"
    python.parent.mkdir(parents=True)
    python.symlink_to(Path(sys.executable))
    alias = tmp_path / "reference-alias"
    alias.symlink_to(worktree, target_is_directory=True)

    resolved = rewrite_inventory._resolve_reference_python(
        worktree.resolve(),
        alias / "backend/.venv/bin/python",
    )

    assert resolved == python
    assert resolved.is_symlink()


def test_bind_reference_records_the_clean_checkout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = _fixture_source(tmp_path / "source")
    manifest_path = tmp_path / "rewrite/coverage.json"
    worktree, head, content_hash = _init_reference(tmp_path)
    rewrite_inventory.build_manifest(manifest_path, source)
    monkeypatch.setattr(rewrite_inventory, "_backend_root", lambda: tmp_path / "target/backend")

    rewrite_inventory.bind_reference(manifest_path, worktree, head[:8])

    reference = json.loads(manifest_path.read_text(encoding="utf-8"))["reference"]
    assert reference == {
        "expected_head": head[:8],
        "persistence_namespace": "clawith_legacy_reference",
        "tracked_content_hash": content_hash,
        "worktree": str(worktree),
    }


def test_reference_environment_requires_distinct_persistence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest = {
        "reference": {"persistence_namespace": "legacy"},
        "target": {"persistence_namespace": "clawith_target"},
    }
    black_box = {
        "persistence_namespace": "legacy",
        "environment_from": {"DATABASE_URL": "LEGACY_DATABASE_URL"},
        "target_environment_from": {"DATABASE_URL": "TARGET_DATABASE_URL"},
    }
    monkeypatch.setenv("LEGACY_DATABASE_URL", "postgresql://legacy")
    monkeypatch.setenv("TARGET_DATABASE_URL", "postgresql://target")

    environment = rewrite_inventory._isolated_environment(manifest, black_box)

    assert environment["DATABASE_URL"] == "postgresql://legacy"
    monkeypatch.setenv("TARGET_DATABASE_URL", "postgresql://legacy")
    with pytest.raises(rewrite_inventory.InventoryError, match="share persistence resource"):
        rewrite_inventory._isolated_environment(manifest, black_box)


def test_release_preflight_requires_all_terminal_and_never_removes_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _fixture_source(tmp_path / "source")
    manifest_path = tmp_path / "rewrite/coverage.json"
    worktree, head, content_hash = _init_reference(tmp_path)
    manifest = rewrite_inventory.build_manifest(manifest_path, source)
    manifest["reference"] = {
        "expected_head": head,
        "persistence_namespace": "legacy",
        "tracked_content_hash": content_hash,
        "worktree": str(worktree),
    }
    manifest["target"] = {"persistence_namespace": "clawith_target"}
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(rewrite_inventory.InventoryError, match="nonterminal="):
        rewrite_inventory.release_reference_preflight(manifest_path, worktree)

    for row in manifest["entries"]:
        evidence = tmp_path / f"{hashlib.sha256(row['id'].encode()).hexdigest()}.txt"
        evidence.write_text("approved", encoding="utf-8")
        record = _artifact(evidence)
        _complete_row(row, record, disposition="delete")
        row["state"] = "deletion_approved"
        row["removal_evidence"] = [record]
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    monkeypatch.setattr(
        rewrite_inventory,
        "_git",
        lambda _path, *args: (
            f"worktree {worktree}\nHEAD {head}" if args == ("worktree", "list", "--porcelain") else head
        ),
    )
    monkeypatch.setattr(rewrite_inventory, "_verify_reference", lambda *args, **kwargs: None)

    assert rewrite_inventory.release_reference_preflight(manifest_path, worktree) == worktree
    rewrite_inventory.release_reference(manifest_path, worktree, preflight_only=True)
    assert worktree.exists()
