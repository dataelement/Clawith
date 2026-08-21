from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from dataclasses import replace

import pytest

from app.services import agent_tools
from app.services.storage_runtime.base import WriteCondition
from app.services.storage_runtime.local import LocalStorageBackend
from app.services.workspace_reconciliation import (
    CandidateChange,
    ReconciliationScope,
    WorkspaceReconciliationService,
    expand_move,
)


@asynccontextmanager
async def _unlocked(*_args, **_kwargs):
    yield


def _scope() -> ReconciliationScope:
    return ReconciliationScope(
        tenant_id=str(uuid.uuid4()),
        agent_id=uuid.uuid4(),
        run_id=str(uuid.uuid4()),
        execution_id=str(uuid.uuid4()),
    )


def _service(storage: LocalStorageBackend) -> WorkspaceReconciliationService:
    return WorkspaceReconciliationService(storage, lock_factory=_unlocked)


@pytest.mark.asyncio
async def test_persist_and_verify_candidate_truth_table(tmp_path) -> None:
    scope = _scope()
    storage = LocalStorageBackend(str(tmp_path))
    service = _service(storage)
    applied_key = f"{scope.agent_id}/workspace/applied.txt"
    base_key = f"{scope.agent_id}/workspace/base.txt"
    conflict_key = f"{scope.agent_id}/workspace/conflict.txt"
    unloaded_key = f"{scope.agent_id}/workspace/unloaded.txt"
    await storage.write_bytes(applied_key, b"candidate")
    await storage.write_bytes(base_key, b"base")
    await storage.write_bytes(conflict_key, b"third")
    await storage.write_bytes(unloaded_key, b"current")

    manifest = await service.persist_candidate(
        scope,
        [
            CandidateChange.replace("workspace/applied.txt", b"candidate", base_hash=service.hash_bytes(b"base")),
            CandidateChange.replace("workspace/base.txt", b"candidate", base_hash=service.hash_bytes(b"base")),
            CandidateChange.replace("workspace/conflict.txt", b"candidate", base_hash=service.hash_bytes(b"base")),
            CandidateChange(
                path="workspace/unloaded.txt",
                operation="replace",
                base_state="unloaded",
                data=b"candidate",
            ),
        ],
    )

    result = await service.verify_current(scope, manifest.candidate_ref)

    assert result.status == "needs_resolution"
    assert result.counts == {
        "applied": 1,
        "not_saved": 1,
        "conflict": 1,
        "unverified": 1,
    }
    assert {item.path: item.status for item in result.changes} == {
        "workspace/applied.txt": "applied",
        "workspace/base.txt": "not_saved",
        "workspace/conflict.txt": "conflict",
        "workspace/unloaded.txt": "unverified",
    }


@pytest.mark.asyncio
async def test_verify_read_failure_is_unverified(tmp_path) -> None:
    scope = _scope()

    class ReadFailingStorage(LocalStorageBackend):
        async def get_version(self, key: str):
            if key.endswith("workspace/fail.txt"):
                raise PermissionError("denied")
            return await super().get_version(key)

    storage = ReadFailingStorage(str(tmp_path))
    service = _service(storage)
    manifest = await service.persist_candidate(
        scope,
        [CandidateChange.create("workspace/fail.txt", b"candidate")],
    )

    result = await service.verify_current(scope, manifest.candidate_ref)

    assert result.status == "unverified"
    assert result.changes[0].status == "unverified"
    assert result.changes[0].detail == "PermissionError"


@pytest.mark.asyncio
async def test_multi_file_manifest_and_move_expansion_preserve_private_bytes(tmp_path) -> None:
    scope = _scope()
    storage = LocalStorageBackend(str(tmp_path))
    service = _service(storage)
    changes = expand_move(
        source_path="workspace/old.bin",
        destination_path="workspace/new.bin",
        data=b"binary\x00payload",
        source_base_version="source-v1",
        source_base_hash=service.hash_bytes(b"binary\x00payload"),
        destination_base_state="absent",
    )

    manifest = await service.persist_candidate(scope, changes)
    duplicate = await service.persist_candidate(scope, changes)

    assert duplicate == manifest
    assert [item.operation for item in manifest.changes] == ["create", "delete"]
    assert manifest.changes[0].candidate_ref is not None
    assert manifest.changes[0].candidate_ref.startswith(manifest.candidate_ref.removesuffix("manifest.json"))
    assert await storage.read_bytes(manifest.changes[0].candidate_ref) == b"binary\x00payload"
    assert manifest.changes[1].candidate_ref is None
    assert manifest.changes[1].candidate_hash is None


@pytest.mark.asyncio
async def test_apply_is_locked_version_protected_write_first_and_idempotent(tmp_path) -> None:
    scope = _scope()

    class RecordingStorage(LocalStorageBackend):
        def __init__(self, root: str) -> None:
            super().__init__(root)
            self.mutations: list[tuple[str, str]] = []

        async def write_bytes_if_match(self, key, data, **kwargs):
            self.mutations.append(("write", key))
            return await super().write_bytes_if_match(key, data, **kwargs)

        async def delete_if_match(self, key, **kwargs):
            self.mutations.append(("delete", key))
            return await super().delete_if_match(key, **kwargs)

    storage = RecordingStorage(str(tmp_path))
    service = _service(storage)
    replace_key = f"{scope.agent_id}/workspace/replace.txt"
    delete_key = f"{scope.agent_id}/workspace/delete.txt"
    await storage.write_bytes(replace_key, b"base")
    await storage.write_bytes(delete_key, b"remove")
    replace_version = await storage.get_version(replace_key)
    delete_version = await storage.get_version(delete_key)
    manifest = await service.persist_candidate(
        scope,
        [
            CandidateChange.replace(
                "workspace/replace.txt",
                b"candidate",
                base_version=replace_version.token,
                base_hash=service.hash_bytes(b"base"),
            ),
            CandidateChange.delete(
                "workspace/delete.txt",
                base_version=delete_version.token,
                base_hash=service.hash_bytes(b"remove"),
            ),
        ],
    )
    storage.mutations.clear()
    await storage.write_bytes(replace_key, b"third-party")

    first = await service.apply_candidate(scope, manifest.candidate_ref, authorized=True)
    first_mutations = list(storage.mutations)
    second = await service.apply_candidate(scope, manifest.candidate_ref, authorized=True)

    assert first.status == "applied"
    assert [operation for operation, _key in first_mutations] == ["write", "delete"]
    assert await storage.read_bytes(replace_key) == b"candidate"
    assert not await storage.exists(delete_key)
    assert second.status == "already_applied"
    assert storage.mutations == first_mutations


@pytest.mark.asyncio
async def test_apply_requires_authorization_and_rechecks_version_inside_lock(tmp_path) -> None:
    scope = _scope()

    class RacingStorage(LocalStorageBackend):
        def __init__(self, root: str) -> None:
            super().__init__(root)
            self.race = True

        async def write_bytes_if_match(self, key, data, *, condition: WriteCondition | None = None, **kwargs):
            if self.race and key.endswith("workspace/file.txt"):
                self.race = False
                await self.write_bytes(key, b"raced")
            return await super().write_bytes_if_match(key, data, condition=condition, **kwargs)

    storage = RacingStorage(str(tmp_path))
    service = _service(storage)
    key = f"{scope.agent_id}/workspace/file.txt"
    await storage.write_bytes(key, b"base")
    manifest = await service.persist_candidate(
        scope,
        [CandidateChange.replace("workspace/file.txt", b"candidate", base_hash=service.hash_bytes(b"base"))],
    )

    with pytest.raises(PermissionError, match="explicit authorization"):
        await service.apply_candidate(scope, manifest.candidate_ref, authorized=False)
    result = await service.apply_candidate(scope, manifest.candidate_ref, authorized=True)

    assert result.status == "conflict"
    assert await storage.read_bytes(key) == b"raced"


@pytest.mark.asyncio
async def test_preserve_conflicts_applies_safe_writes_but_skips_deletes(tmp_path) -> None:
    scope = _scope()
    storage = LocalStorageBackend(str(tmp_path))
    service = _service(storage)
    conflict_key = f"{scope.agent_id}/workspace/conflict.txt"
    safe_key = f"{scope.agent_id}/workspace/safe.txt"
    delete_key = f"{scope.agent_id}/workspace/source.txt"
    await storage.write_bytes(conflict_key, b"base")
    await storage.write_bytes(safe_key, b"base")
    await storage.write_bytes(delete_key, b"source")
    manifest = await service.persist_candidate(
        scope,
        [
            CandidateChange.replace(
                "workspace/conflict.txt",
                b"agent",
                base_hash=service.hash_bytes(b"base"),
            ),
            CandidateChange.replace(
                "workspace/safe.txt",
                b"agent-safe",
                base_hash=service.hash_bytes(b"base"),
            ),
            CandidateChange.delete(
                "workspace/source.txt",
                base_hash=service.hash_bytes(b"source"),
            ),
        ],
    )
    await storage.write_bytes(conflict_key, b"human")

    result = await service.preserve_conflicts_and_apply_safe_changes(
        scope,
        manifest.candidate_ref,
    )

    assert result.status == "needs_resolution"
    assert await storage.read_bytes(conflict_key) == b"human"
    assert await storage.read_bytes(safe_key) == b"agent-safe"
    assert await storage.read_bytes(delete_key) == b"source"


@pytest.mark.asyncio
async def test_preserve_conflicts_uses_cas_for_safe_writes(tmp_path) -> None:
    scope = _scope()

    class RacingStorage(LocalStorageBackend):
        async def write_bytes_if_match(self, key, data, *, condition=None, **kwargs):
            if key.endswith("workspace/safe.txt"):
                await self.write_bytes(key, b"newer-human")
            return await super().write_bytes_if_match(
                key,
                data,
                condition=condition,
                **kwargs,
            )

    storage = RacingStorage(str(tmp_path))
    service = _service(storage)
    key = f"{scope.agent_id}/workspace/safe.txt"
    await storage.write_bytes(key, b"base")
    manifest = await service.persist_candidate(
        scope,
        [
            CandidateChange.replace(
                "workspace/safe.txt",
                b"agent",
                base_hash=service.hash_bytes(b"base"),
            )
        ],
    )

    result = await service.preserve_conflicts_and_apply_safe_changes(
        scope,
        manifest.candidate_ref,
    )

    assert result.status == "needs_resolution"
    assert await storage.read_bytes(key) == b"newer-human"


@pytest.mark.asyncio
async def test_scope_ref_and_path_validation_reject_cross_scope_access(tmp_path) -> None:
    scope = _scope()
    storage = LocalStorageBackend(str(tmp_path))
    service = _service(storage)
    manifest = await service.persist_candidate(scope, [CandidateChange.create("workspace/a.txt", b"a")])

    other_scope = replace(scope, tenant_id=str(uuid.uuid4()))
    with pytest.raises(ValueError, match="candidate_ref does not belong"):
        await service.verify_current(other_scope, manifest.candidate_ref)
    with pytest.raises(ValueError, match="candidate_ref does not belong"):
        await service.discard_candidate(other_scope, manifest.candidate_ref)
    with pytest.raises(ValueError, match="traversal"):
        await service.persist_candidate(scope, [CandidateChange.create("workspace/../../escape.txt", b"x")])
    with pytest.raises(ValueError, match="scope component"):
        ReconciliationScope(
            tenant_id="tenant/escape",
            agent_id=scope.agent_id,
            run_id=scope.run_id,
            execution_id=scope.execution_id,
        )
    with pytest.raises(ValueError, match="scope component"):
        replace(scope, run_id="..")

    await service.discard_candidate(scope, manifest.candidate_ref)
    await service.discard_candidate(scope, manifest.candidate_ref)
    assert not await storage.exists(manifest.candidate_ref)


@pytest.mark.asyncio
async def test_sandbox_candidate_marks_budget_omission_as_unloaded(
    tmp_path,
    monkeypatch,
) -> None:
    agent_id = uuid.uuid4()
    tenant_id = str(uuid.uuid4())
    storage = LocalStorageBackend(str(tmp_path / "storage"))
    monkeypatch.setattr(agent_tools, "get_storage_backend", lambda: storage)
    existing_key = f"{agent_id}/workspace/report.txt"
    await storage.write_bytes(existing_key, b"durable version omitted by materialization")

    temp_root = tmp_path / "sandbox"
    (temp_root / "workspace").mkdir(parents=True)
    (temp_root / "workspace/report.txt").write_bytes(b"agent candidate")
    temp_workspace = agent_tools.TempWorkspace(
        temp_dir=type("TempDir", (), {"cleanup": lambda self: None})(),
        root=temp_root,
        agent_id=agent_id,
        tenant_id=tenant_id,
        materialized_paths=["workspace"],
        publish_paths=["workspace"],
        manifest={},
    )

    changes = await agent_tools._workspace_candidate_changes(temp_workspace)

    assert len(changes) == 1
    assert changes[0].path == "workspace/report.txt"
    assert changes[0].base_state == "unloaded"
    assert changes[0].data == b"agent candidate"


@pytest.mark.asyncio
async def test_directory_move_candidate_covers_every_source_file(
    tmp_path,
    monkeypatch,
) -> None:
    agent_id = uuid.uuid4()
    storage = LocalStorageBackend(str(tmp_path))
    monkeypatch.setattr(agent_tools, "get_storage_backend", lambda: storage)
    await storage.write_bytes(f"{agent_id}/workspace/source/a.txt", b"a")
    await storage.write_bytes(f"{agent_id}/workspace/source/nested/b.txt", b"b")

    changes = await agent_tools._move_candidate_changes(
        agent_id,
        "workspace/source",
        "workspace/archive/source",
    )

    assert {(change.operation, change.path) for change in changes} == {
        ("create", "workspace/archive/source/a.txt"),
        ("delete", "workspace/source/a.txt"),
        ("create", "workspace/archive/source/nested/b.txt"),
        ("delete", "workspace/source/nested/b.txt"),
    }


@pytest.mark.asyncio
async def test_terminal_run_cleanup_removes_all_execution_candidates(tmp_path) -> None:
    scope = _scope()
    storage = LocalStorageBackend(str(tmp_path))
    service = _service(storage)
    first = await service.persist_candidate(
        scope,
        [CandidateChange.create("workspace/a.txt", b"a")],
    )
    second_scope = replace(scope, execution_id=str(uuid.uuid4()))
    second = await service.persist_candidate(
        second_scope,
        [CandidateChange.create("workspace/b.txt", b"b")],
    )

    await service.cleanup_run_candidates(
        tenant_id=scope.tenant_id,
        agent_id=scope.agent_id,
        run_id=scope.run_id,
    )

    assert not await storage.exists(first.candidate_ref)
    assert not await storage.exists(second.candidate_ref)
