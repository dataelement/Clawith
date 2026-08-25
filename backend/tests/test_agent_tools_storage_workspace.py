from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, Mock
import uuid

import pytest

from app.services import agent_tools
from app.services import workspace_collaboration
from app.services.storage_runtime.base import StorageBackend, StorageEntry, StorageVersion, WriteCondition, ConditionalWriteResult


@asynccontextmanager
async def _noop_workspace_locks(*_args, **_kwargs):
    yield


@pytest.fixture(autouse=True)
def _isolate_storage_semantics_from_distributed_locking(monkeypatch):
    """These in-memory storage tests do not exercise the Redis lock backend."""
    monkeypatch.setattr(agent_tools, "workspace_locks", _noop_workspace_locks)
    monkeypatch.setattr(
        workspace_collaboration,
        "workspace_locks",
        _noop_workspace_locks,
    )


class MemoryStorageBackend(StorageBackend):
    def __init__(self, files: dict[str, bytes] | None = None):
        self.files = dict(files or {})
        self.versions = {key: 1 for key in self.files}

    async def exists(self, key: str) -> bool:
        return key in self.files

    async def is_file(self, key: str) -> bool:
        return key in self.files

    async def is_dir(self, key: str) -> bool:
        prefix = key.rstrip("/") + "/"
        return any(existing.startswith(prefix) for existing in self.files)

    async def list_dir(self, key: str) -> list[StorageEntry]:
        prefix = key.rstrip("/") + "/"
        entries: dict[str, StorageEntry] = {}
        for existing, data in self.files.items():
            if not existing.startswith(prefix):
                continue
            rest = existing.removeprefix(prefix)
            name, _, tail = rest.partition("/")
            entries[name] = StorageEntry(
                name=name,
                key=f"{prefix}{name}",
                is_dir=bool(tail),
                size=0 if tail else len(data),
            )
        return sorted(entries.values(), key=lambda entry: (not entry.is_dir, entry.name))

    async def read_bytes(self, key: str) -> bytes:
        return self.files[key]

    async def write_bytes(self, key: str, data: bytes, content_type: str | None = None) -> None:
        self.files[key] = data
        self.versions[key] = self.versions.get(key, 0) + 1

    async def delete(self, key: str) -> None:
        self.files.pop(key, None)
        self.versions.pop(key, None)

    async def delete_tree(self, key: str) -> None:
        prefix = key.rstrip("/") + "/"
        for existing in list(self.files):
            if existing.startswith(prefix):
                self.files.pop(existing)
                self.versions.pop(existing, None)

    async def stat(self, key: str) -> StorageEntry:
        return StorageEntry(name=key.rsplit("/", 1)[-1], key=key, is_dir=False, size=len(self.files[key]))

    async def get_version(self, key: str) -> StorageVersion:
        if key not in self.files:
            return StorageVersion(key=key, exists=False, is_dir=False)
        version = str(self.versions.get(key, 0))
        return StorageVersion(
            key=key,
            exists=True,
            is_dir=False,
            size=len(self.files[key]),
            version_id=version,
            etag=version,
            content_hash=version,
        )

    async def write_bytes_if_match(
        self,
        key: str,
        data: bytes,
        *,
        condition: WriteCondition | None = None,
        content_type: str | None = None,
    ) -> ConditionalWriteResult:
        current = await self.get_version(key)
        if condition:
            if condition.require_absent and current.exists:
                return ConditionalWriteResult(ok=False, conflict=True, current_version=current)
            if condition.version_token is not None and current.token != condition.version_token:
                return ConditionalWriteResult(ok=False, conflict=True, current_version=current)
        await self.write_bytes(key, data, content_type=content_type)
        return ConditionalWriteResult(ok=True, current_version=await self.get_version(key))


@pytest.mark.asyncio
async def test_agent_file_tools_use_storage_paths(monkeypatch):
    agent_id = uuid.uuid4()
    storage = MemoryStorageBackend({
        f"{agent_id}/workspace/notes.md": b"# Notes\nneedle\n",
        f"{agent_id}/memory/memory.md": b"# Memory\n",
    })
    monkeypatch.setattr(agent_tools, "get_storage_backend", lambda: storage)

    listing = await agent_tools._storage_list_dir(agent_id, "workspace")
    read = await agent_tools._storage_read_file(agent_id, "workspace/notes.md")
    search = await agent_tools._storage_search_files(agent_id, "needle", path="workspace", file_pattern="*.md")
    found = await agent_tools._storage_find_files(agent_id, "*.md", path="workspace")

    assert "notes.md" in listing
    assert "needle" in read
    assert "workspace/notes.md:2" in search
    assert "workspace/notes.md" in found


@pytest.mark.asyncio
async def test_read_file_outcome_rejects_binary_spreadsheet(monkeypatch):
    agent_id = uuid.uuid4()
    storage = MemoryStorageBackend({
        f"{agent_id}/workspace/inventory.xlsx": b"PK\x03\x04binary workbook",
    })
    monkeypatch.setattr(agent_tools, "get_storage_backend", lambda: storage)

    outcome = await agent_tools._read_file_outcome(
        agent_id,
        {"path": "workspace/inventory.xlsx"},
        tenant_id=None,
    )

    assert outcome.status == "failed"
    assert outcome.error_code == "workspace_binary_file_unsupported"
    assert outcome.retryable is False
    assert "text files only" in (outcome.result_summary or "")


@pytest.mark.asyncio
async def test_complete_skill_read_records_package_digest(monkeypatch):
    agent_id = uuid.uuid4()
    storage = MemoryStorageBackend({
        f"{agent_id}/skills/budget/SKILL.md": b"---\nname: budget\n---\n",
        f"{agent_id}/skills/budget/scripts/auth.py": b"authenticate()\n",
    })
    monkeypatch.setattr(agent_tools, "get_storage_backend", lambda: storage)

    outcome = await agent_tools._read_file_outcome(
        agent_id,
        {"path": "skills/budget/SKILL.md"},
        tenant_id=None,
    )

    activation = outcome.metadata["skill_activation"]
    assert activation["name"] == "budget"
    assert activation["file_count"] == 2
    assert len(activation["package_digest"]) == 64


@pytest.mark.asyncio
async def test_temp_workspace_materializes_only_requested_paths(monkeypatch):
    agent_id = uuid.uuid4()
    storage = MemoryStorageBackend({
        f"{agent_id}/workspace/input.md": b"# Input\n",
        f"{agent_id}/workspace/other.md": b"# Other\n",
    })
    monkeypatch.setattr(agent_tools, "get_storage_backend", lambda: storage)

    temp_ws = await agent_tools._prepare_temp_workspace(agent_id, paths=["workspace/input.md"])
    try:
        assert (temp_ws.root / "workspace" / "input.md").read_text(encoding="utf-8") == "# Input\n"
        assert not (temp_ws.root / "workspace" / "other.md").exists()
    finally:
        temp_ws.cleanup()


@pytest.mark.asyncio
async def test_default_materialization_reserves_capacity_for_complete_skills(monkeypatch):
    agent_id = uuid.uuid4()
    storage = MemoryStorageBackend({
        f"{agent_id}/workspace/history.bin": b"w" * 8,
        f"{agent_id}/skills/budget/SKILL.md": b"skill",
        f"{agent_id}/skills/budget/scripts/auth.py": b"auth",
    })
    monkeypatch.setattr(agent_tools, "get_storage_backend", lambda: storage)
    monkeypatch.setattr(agent_tools, "TOOL_MATERIALIZE_MAX_TOTAL_BYTES", 10)

    temp_ws = await agent_tools._prepare_temp_workspace(agent_id)
    try:
        assert (temp_ws.root / "skills/budget/SKILL.md").read_bytes() == b"skill"
        assert (temp_ws.root / "skills/budget/scripts/auth.py").read_bytes() == b"auth"
    finally:
        temp_ws.cleanup()


@pytest.mark.asyncio
async def test_temp_workspace_rejects_partial_skill_snapshot(monkeypatch):
    agent_id = uuid.uuid4()
    storage = MemoryStorageBackend({
        f"{agent_id}/skills/budget/SKILL.md": b"instructions",
        f"{agent_id}/skills/budget/scripts/auth.py": b"auth",
    })
    monkeypatch.setattr(agent_tools, "get_storage_backend", lambda: storage)

    with pytest.raises(agent_tools.SkillSnapshotIncompleteError):
        await agent_tools._prepare_temp_workspace(
            agent_id,
            max_file_bytes=5,
        )


def test_temp_workspace_materialization_limits_are_50_and_500_mib():
    assert agent_tools.TOOL_MATERIALIZE_MAX_FILE_BYTES == 50 * 1024 * 1024
    assert agent_tools.TOOL_MATERIALIZE_MAX_TOTAL_BYTES == 500 * 1024 * 1024


@pytest.mark.asyncio
async def test_temp_workspace_materializes_file_above_previous_10_mib_limit(
    monkeypatch,
):
    agent_id = uuid.uuid4()
    content = b"x" * (11 * 1024 * 1024)
    storage = MemoryStorageBackend({
        f"{agent_id}/workspace/presentation.pptx": content,
    })
    monkeypatch.setattr(agent_tools, "get_storage_backend", lambda: storage)

    temp_ws = await agent_tools._prepare_temp_workspace(
        agent_id,
        paths=["workspace/presentation.pptx"],
    )
    try:
        assert (temp_ws.root / "workspace" / "presentation.pptx").read_bytes() == content
    finally:
        temp_ws.cleanup()


@pytest.mark.asyncio
async def test_temp_workspace_logs_file_skipped_by_per_file_limit(monkeypatch, tmp_path):
    agent_id = uuid.uuid4()
    storage_key = f"{agent_id}/workspace/oversized.pptx"
    storage = MemoryStorageBackend({storage_key: b"too large"})
    storage.get_version = AsyncMock(  # type: ignore[method-assign]
        return_value=StorageVersion(
            key=storage_key,
            exists=True,
            is_dir=False,
            size=51 * 1024 * 1024,
        )
    )
    warning = Mock()
    monkeypatch.setattr(agent_tools.logger, "warning", warning)

    await agent_tools._materialize_storage_path_with_budget(
        storage,
        storage_key,
        "workspace/oversized.pptx",
        tmp_path,
        {"total": 0},
        {},
    )

    assert not (tmp_path / "workspace" / "oversized.pptx").exists()
    warning.assert_called_once_with(
        "Tool workspace materialization skipped file: path={} size_bytes={} limit_bytes={} reason={}",
        "workspace/oversized.pptx",
        51 * 1024 * 1024,
        50 * 1024 * 1024,
        "per_file_limit",
    )


@pytest.mark.asyncio
async def test_temp_workspace_logs_file_skipped_by_total_limit(monkeypatch, tmp_path):
    agent_id = uuid.uuid4()
    storage_key = f"{agent_id}/workspace/second.pptx"
    storage = MemoryStorageBackend({storage_key: b"second"})
    warning = Mock()
    monkeypatch.setattr(agent_tools.logger, "warning", warning)

    await agent_tools._materialize_storage_path_with_budget(
        storage,
        storage_key,
        "workspace/second.pptx",
        tmp_path,
        {"total": agent_tools.TOOL_MATERIALIZE_MAX_TOTAL_BYTES},
        {},
    )

    assert not (tmp_path / "workspace" / "second.pptx").exists()
    warning.assert_called_once_with(
        "Tool workspace materialization skipped file: path={} size_bytes={} limit_bytes={} reason={}",
        "workspace/second.pptx",
        len(b"second"),
        500 * 1024 * 1024,
        "total_limit",
    )


@pytest.mark.asyncio
async def test_execute_tool_list_files_does_not_create_persistent_workspace(monkeypatch, tmp_path):
    agent_id = uuid.uuid4()
    storage = MemoryStorageBackend({
        f"{agent_id}/workspace/input.md": b"# Input\n",
    })
    monkeypatch.setattr(agent_tools, "get_storage_backend", lambda: storage)
    monkeypatch.setattr(agent_tools, "WORKSPACE_ROOT", tmp_path)

    async def _tenant(_agent_id):
        return None

    monkeypatch.setattr(agent_tools, "_get_agent_tenant_id", _tenant)

    result = await agent_tools.execute_tool("list_files", {"path": "workspace"}, agent_id, agent_id)

    assert "input.md" in result
    assert not (tmp_path / str(agent_id)).exists()


@pytest.mark.asyncio
async def test_write_workspace_file_does_not_mirror_to_local_for_non_local_storage(monkeypatch, tmp_path):
    agent_id = uuid.uuid4()
    storage = MemoryStorageBackend()
    monkeypatch.setattr(workspace_collaboration, "get_storage_backend", lambda: storage)

    async def _noop_revision(*args, **kwargs):
        return None

    monkeypatch.setattr(workspace_collaboration, "record_revision", _noop_revision)

    result = await workspace_collaboration.write_workspace_file(
        db=None,
        agent_id=agent_id,
        base_dir=tmp_path / str(agent_id),
        path="workspace/test.md",
        content="hello",
        actor_type="agent",
        actor_id=agent_id,
        enforce_human_lock=False,
    )

    assert result.ok is True
    assert storage.files[f"{agent_id}/workspace/test.md"] == b"hello"
    assert not (tmp_path / str(agent_id) / "workspace" / "test.md").exists()


@pytest.mark.asyncio
async def test_write_workspace_file_appends_with_version_guard(monkeypatch, tmp_path):
    agent_id = uuid.uuid4()
    storage = MemoryStorageBackend({
        f"{agent_id}/workspace/page.html": b"<main>",
    })
    monkeypatch.setattr(workspace_collaboration, "get_storage_backend", lambda: storage)
    revisions = []

    async def _record_revision(*args, **kwargs):
        revisions.append(kwargs)
        return None

    monkeypatch.setattr(workspace_collaboration, "record_revision", _record_revision)

    result = await workspace_collaboration.write_workspace_file(
        db=None,
        agent_id=agent_id,
        base_dir=tmp_path / str(agent_id),
        path="workspace/page.html",
        content="content</main>",
        actor_type="agent",
        actor_id=agent_id,
        enforce_human_lock=False,
        append=True,
    )

    assert result.ok is True
    assert result.message == "Appended to workspace/page.html (14 chars; 20 total)"
    assert storage.files[f"{agent_id}/workspace/page.html"] == b"<main>content</main>"
    assert revisions[0]["before_content"] == "<main>"
    assert revisions[0]["after_content"] == "<main>content</main>"


@pytest.mark.asyncio
async def test_write_workspace_file_rejects_append_to_missing_file(monkeypatch, tmp_path):
    agent_id = uuid.uuid4()
    storage = MemoryStorageBackend()
    monkeypatch.setattr(workspace_collaboration, "get_storage_backend", lambda: storage)

    result = await workspace_collaboration.write_workspace_file(
        db=None,
        agent_id=agent_id,
        base_dir=tmp_path / str(agent_id),
        path="workspace/page.html",
        content="content",
        actor_type="agent",
        actor_id=agent_id,
        enforce_human_lock=False,
        append=True,
    )

    assert result.ok is False
    assert result.message == "Cannot append to missing file: workspace/page.html"
    assert storage.files == {}


@pytest.mark.asyncio
async def test_write_workspace_file_append_does_not_overwrite_a_concurrent_change(
    monkeypatch,
    tmp_path,
):
    agent_id = uuid.uuid4()
    key = f"{agent_id}/workspace/page.html"

    class RacingStorageBackend(MemoryStorageBackend):
        async def write_bytes_if_match(self, storage_key, data, **kwargs):
            await self.write_bytes(storage_key, b"concurrent")
            return await super().write_bytes_if_match(storage_key, data, **kwargs)

    storage = RacingStorageBackend({key: b"first"})
    monkeypatch.setattr(workspace_collaboration, "get_storage_backend", lambda: storage)

    result = await workspace_collaboration.write_workspace_file(
        db=None,
        agent_id=agent_id,
        base_dir=tmp_path / str(agent_id),
        path="workspace/page.html",
        content=" second",
        actor_type="agent",
        actor_id=agent_id,
        enforce_human_lock=False,
        append=True,
    )

    assert result.ok is False
    assert result.message == "Conflict detected while writing workspace/page.html"
    assert storage.files[key] == b"concurrent"


@pytest.mark.asyncio
async def test_flush_temp_workspace_only_writes_changed_files(monkeypatch):
    agent_id = uuid.uuid4()
    storage = MemoryStorageBackend({
        f"{agent_id}/workspace/input.md": b"# Input\n",
        f"{agent_id}/workspace/other.md": b"# Other\n",
    })
    monkeypatch.setattr(agent_tools, "get_storage_backend", lambda: storage)

    temp_ws = await agent_tools._prepare_temp_workspace(agent_id, paths=["workspace"])
    try:
        (temp_ws.root / "workspace" / "input.md").write_text("# Updated\n", encoding="utf-8")
        result = await agent_tools.flush_temp_workspace(temp_ws)
    finally:
        temp_ws.cleanup()

    assert result["updated"] == ["workspace/input.md"]
    assert "workspace/other.md" in result["skipped"]
    assert storage.files[f"{agent_id}/workspace/input.md"] == b"# Updated\n"
    assert storage.files[f"{agent_id}/workspace/other.md"] == b"# Other\n"


@pytest.mark.asyncio
async def test_flush_temp_workspace_refreshes_manifest_for_reused_workspace(monkeypatch):
    agent_id = uuid.uuid4()
    storage_key = f"{agent_id}/workspace/input.md"
    storage = MemoryStorageBackend({storage_key: b"first"})
    monkeypatch.setattr(agent_tools, "get_storage_backend", lambda: storage)

    temp_ws = await agent_tools._prepare_temp_workspace(agent_id, paths=["workspace"])
    try:
        local_file = temp_ws.root / "workspace" / "input.md"
        local_file.write_bytes(b"second")
        first = await agent_tools.flush_temp_workspace(temp_ws)
        first_token = temp_ws.manifest["workspace/input.md"].base_version_token

        local_file.write_bytes(b"first")
        second = await agent_tools.flush_temp_workspace(temp_ws)
    finally:
        temp_ws.cleanup()

    assert first["updated"] == ["workspace/input.md"]
    assert second["updated"] == ["workspace/input.md"]
    assert storage.files[storage_key] == b"first"
    assert temp_ws.manifest["workspace/input.md"].base_hash == agent_tools.content_hash_bytes(b"first")
    assert temp_ws.manifest["workspace/input.md"].base_version_token != first_token


@pytest.mark.asyncio
async def test_flush_temp_workspace_fails_on_conflict(monkeypatch):
    agent_id = uuid.uuid4()
    storage = MemoryStorageBackend({
        f"{agent_id}/workspace/input.md": b"# Input\n",
    })
    monkeypatch.setattr(agent_tools, "get_storage_backend", lambda: storage)

    temp_ws = await agent_tools._prepare_temp_workspace(agent_id, paths=["workspace/input.md"])
    try:
        (temp_ws.root / "workspace" / "input.md").write_text("# Local change\n", encoding="utf-8")
        await storage.write_bytes(f"{agent_id}/workspace/input.md", b"# Remote change\n")
        result = await agent_tools.flush_temp_workspace(temp_ws)
    finally:
        temp_ws.cleanup()

    assert result["conflicted"] == ["workspace/input.md"]
    assert storage.files[f"{agent_id}/workspace/input.md"] == b"# Remote change\n"


@pytest.mark.asyncio
@pytest.mark.parametrize("existing_before_materialize", [False, True])
async def test_flush_temp_workspace_accepts_stable_identical_concurrent_write(
    monkeypatch,
    existing_before_materialize,
):
    agent_id = uuid.uuid4()
    storage_key = f"{agent_id}/workspace/output/session-id/result.md"
    initial_files = {storage_key: b"# Initial\n"} if existing_before_materialize else None
    storage = MemoryStorageBackend(initial_files)
    monkeypatch.setattr(agent_tools, "get_storage_backend", lambda: storage)

    temp_ws = await agent_tools._prepare_temp_workspace(
        agent_id,
        paths=["workspace/output/session-id"],
        publish_paths=["workspace/output/session-id"],
    )
    try:
        output_path = temp_ws.root / "workspace/output/session-id/result.md"
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"# Identical result\n")

        # Another publisher wins the CAS with this execution's exact bytes.
        await storage.write_bytes(storage_key, b"# Identical result\n")
        result = await agent_tools.flush_temp_workspace(temp_ws)
    finally:
        temp_ws.cleanup()

    assert result == {
        "updated": [],
        "deleted": [],
        "conflicted": [],
        "skipped": ["workspace/output/session-id/result.md"],
    }
    assert storage.files[storage_key] == b"# Identical result\n"
    manifest = temp_ws.manifest["workspace/output/session-id/result.md"]
    assert manifest.base_version_token == (await storage.get_version(storage_key)).token
    assert manifest.base_hash == agent_tools.content_hash_bytes(b"# Identical result\n")


@pytest.mark.asyncio
async def test_flush_temp_workspace_rejects_identical_bytes_when_version_changes_during_check(
    monkeypatch,
):
    agent_id = uuid.uuid4()
    storage_key = f"{agent_id}/workspace/output/session-id/result.md"

    class RacingReadStorageBackend(MemoryStorageBackend):
        mutate_after_read = False

        async def read_bytes(self, key: str) -> bytes:
            data = await super().read_bytes(key)
            if self.mutate_after_read:
                self.mutate_after_read = False
                await self.write_bytes(key, b"# Changed again\n")
            return data

    storage = RacingReadStorageBackend({storage_key: b"# Initial\n"})
    monkeypatch.setattr(agent_tools, "get_storage_backend", lambda: storage)

    temp_ws = await agent_tools._prepare_temp_workspace(
        agent_id,
        paths=["workspace/output/session-id"],
        publish_paths=["workspace/output/session-id"],
    )
    try:
        output_path = temp_ws.root / "workspace/output/session-id/result.md"
        output_path.write_bytes(b"# Identical result\n")
        await storage.write_bytes(storage_key, b"# Identical result\n")
        storage.mutate_after_read = True

        result = await agent_tools.flush_temp_workspace(temp_ws)
    finally:
        temp_ws.cleanup()

    assert result["conflicted"] == ["workspace/output/session-id/result.md"]
    assert result["skipped"] == []
    assert storage.files[storage_key] == b"# Changed again\n"


@pytest.mark.asyncio
async def test_flush_isolated_output_overwrites_unmanifested_existing_file(monkeypatch):
    agent_id = uuid.uuid4()
    session_path = f"workspace/output/{uuid.uuid4()}"
    storage_key = f"{agent_id}/{session_path}/result.json"
    storage = MemoryStorageBackend()
    monkeypatch.setattr(agent_tools, "get_storage_backend", lambda: storage)

    temp_ws = await agent_tools._prepare_temp_workspace(
        agent_id,
        paths=[],
        publish_paths=[session_path],
    )
    try:
        output_file = temp_ws.root / session_path / "result.json"
        output_file.parent.mkdir(parents=True)
        output_file.write_bytes(b"session-result")
        await storage.write_bytes(storage_key, b"previous-result")
        result = await agent_tools.flush_temp_workspace(
            temp_ws,
            conflict_mode="overwrite",
        )
    finally:
        temp_ws.cleanup()

    assert result["updated"] == [f"{session_path}/result.json"]
    assert result["conflicted"] == []
    assert storage.files[storage_key] == b"session-result"
    assert f"{session_path}/result.json" in temp_ws.manifest


@pytest.mark.asyncio
async def test_flush_isolated_output_deletes_newer_existing_file(monkeypatch):
    agent_id = uuid.uuid4()
    session_path = f"workspace/output/{uuid.uuid4()}"
    storage_key = f"{agent_id}/{session_path}/result.json"
    storage = MemoryStorageBackend({storage_key: b"materialized-result"})
    monkeypatch.setattr(agent_tools, "get_storage_backend", lambda: storage)

    temp_ws = await agent_tools._prepare_temp_workspace(
        agent_id,
        paths=[session_path],
        publish_paths=[session_path],
    )
    try:
        (temp_ws.root / session_path / "result.json").unlink()
        await storage.write_bytes(storage_key, b"newer-result")
        result = await agent_tools.flush_temp_workspace(
            temp_ws,
            conflict_mode="overwrite",
        )
    finally:
        temp_ws.cleanup()

    assert result["deleted"] == [f"{session_path}/result.json"]
    assert result["conflicted"] == []
    assert storage_key not in storage.files
    assert f"{session_path}/result.json" not in temp_ws.manifest


@pytest.mark.asyncio
async def test_flush_temp_workspace_filters_manifest_deletions_to_publish_paths(monkeypatch):
    agent_id = uuid.uuid4()
    session_id = uuid.uuid4()
    session_path = f"workspace/output/{session_id}"
    storage = MemoryStorageBackend({
        f"{agent_id}/workspace/read-only.md": b"keep",
        f"{agent_id}/{session_path}/result.txt": b"delete-me",
    })
    monkeypatch.setattr(agent_tools, "get_storage_backend", lambda: storage)

    temp_ws = await agent_tools._prepare_temp_workspace(
        agent_id,
        tenant_id=str(uuid.uuid4()),
        paths=["workspace"],
        publish_paths=[session_path],
    )
    try:
        (temp_ws.root / session_path / "result.txt").unlink()
        (temp_ws.root / "workspace" / "read-only.md").write_text("changed", encoding="utf-8")
        result = await agent_tools.flush_temp_workspace(temp_ws)
    finally:
        temp_ws.cleanup()

    assert result["deleted"] == [f"{session_path}/result.txt"]
    assert storage.files[f"{agent_id}/workspace/read-only.md"] == b"keep"


@pytest.mark.asyncio
async def test_write_workspace_file_fails_on_expected_version_conflict(monkeypatch, tmp_path):
    agent_id = uuid.uuid4()
    storage = MemoryStorageBackend({
        f"{agent_id}/workspace/test.md": b"old",
    })
    monkeypatch.setattr(workspace_collaboration, "get_storage_backend", lambda: storage)

    async def _noop_revision(*args, **kwargs):
        return None

    monkeypatch.setattr(workspace_collaboration, "record_revision", _noop_revision)

    version = await storage.get_version(f"{agent_id}/workspace/test.md")
    await storage.write_bytes(f"{agent_id}/workspace/test.md", b"remote-new")
    result = await workspace_collaboration.write_workspace_file(
        db=None,
        agent_id=agent_id,
        base_dir=tmp_path / str(agent_id),
        path="workspace/test.md",
        content="local-new",
        actor_type="agent",
        actor_id=agent_id,
        enforce_human_lock=False,
        expected_version_token=version.token,
    )

    assert result.ok is False
    assert "Conflict detected" in result.message
    assert storage.files[f"{agent_id}/workspace/test.md"] == b"remote-new"


@pytest.mark.asyncio
async def test_move_workspace_path_fails_when_source_changes(monkeypatch, tmp_path):
    agent_id = uuid.uuid4()
    storage = MemoryStorageBackend({
        f"{agent_id}/workspace/source.md": b"old",
    })
    monkeypatch.setattr(workspace_collaboration, "get_storage_backend", lambda: storage)

    async def _noop_revision(*args, **kwargs):
        return None

    monkeypatch.setattr(workspace_collaboration, "record_revision", _noop_revision)

    version = await storage.get_version(f"{agent_id}/workspace/source.md")
    await storage.write_bytes(f"{agent_id}/workspace/source.md", b"remote-new")
    result = await workspace_collaboration.move_workspace_path(
        db=None,
        agent_id=agent_id,
        base_dir=tmp_path / str(agent_id),
        source_path="workspace/source.md",
        destination_path="workspace/dest.md",
        actor_type="agent",
        actor_id=agent_id,
        enforce_human_lock=False,
        expected_source_version_token=version.token,
    )

    assert result.ok is False
    assert "Conflict detected" in result.message
    assert f"{agent_id}/workspace/dest.md" not in storage.files


@pytest.mark.asyncio
async def test_move_overwrite_keeps_existing_target_when_candidate_write_conflicts(
    monkeypatch,
    tmp_path,
):
    agent_id = uuid.uuid4()

    class ConflictingTargetStorage(MemoryStorageBackend):
        async def write_bytes_if_match(self, key, data, **kwargs):
            if key.endswith("workspace/dest.md"):
                current = await self.get_version(key)
                return ConditionalWriteResult(
                    ok=False,
                    conflict=True,
                    current_version=current,
                )
            return await super().write_bytes_if_match(key, data, **kwargs)

    storage = ConflictingTargetStorage(
        {
            f"{agent_id}/workspace/source.md": b"candidate",
            f"{agent_id}/workspace/dest.md": b"keep-current",
        }
    )
    monkeypatch.setattr(workspace_collaboration, "get_storage_backend", lambda: storage)

    async def _noop_revision(*args, **kwargs):
        return None

    monkeypatch.setattr(workspace_collaboration, "record_revision", _noop_revision)
    result = await workspace_collaboration.move_workspace_path(
        db=None,
        agent_id=agent_id,
        base_dir=tmp_path / str(agent_id),
        source_path="workspace/source.md",
        destination_path="workspace/dest.md",
        actor_type="agent",
        actor_id=agent_id,
        enforce_human_lock=False,
        overwrite=True,
    )

    assert result.ok is False
    assert storage.files[f"{agent_id}/workspace/dest.md"] == b"keep-current"
    assert storage.files[f"{agent_id}/workspace/source.md"] == b"candidate"


@pytest.mark.asyncio
async def test_delete_workspace_directory_uses_prefix_existence(monkeypatch, tmp_path):
    agent_id = uuid.uuid4()
    storage = MemoryStorageBackend({
        f"{agent_id}/workspace/dir/a.txt": b"a",
        f"{agent_id}/workspace/dir/nested/b.txt": b"b",
    })
    monkeypatch.setattr(workspace_collaboration, "get_storage_backend", lambda: storage)

    async def _noop_revision(*args, **kwargs):
        return None

    monkeypatch.setattr(workspace_collaboration, "record_revision", _noop_revision)

    result = await workspace_collaboration.delete_workspace_file(
        db=None,
        agent_id=agent_id,
        base_dir=tmp_path / str(agent_id),
        path="workspace/dir",
        actor_type="user",
        actor_id=agent_id,
        enforce_human_lock=False,
    )

    assert result.ok is True
    assert f"{agent_id}/workspace/dir/a.txt" not in storage.files
    assert f"{agent_id}/workspace/dir/nested/b.txt" not in storage.files
