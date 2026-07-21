"""Group file boundary, permission, and revision tests."""

from __future__ import annotations

import uuid

import pytest

from app.models.participant import Participant
from app.models.workspace import WorkspaceFileRevision
from app.services import group_file_service
from app.services.storage_runtime.base import (
    ConditionalWriteResult,
    StorageEntry,
    StorageVersion,
)
from app.services.storage_runtime.local import LocalStorageBackend


class _RecordingDB:
    def __init__(self) -> None:
        self.added = []
        self.flush_count = 0

    def add(self, value) -> None:
        self.added.append(value)

    async def flush(self) -> None:
        self.flush_count += 1

    async def execute(self, _statement):
        raise AssertionError("authorization lookup should be stubbed in this test")


def _participant(kind: str, ref_id: uuid.UUID | None = None) -> Participant:
    return Participant(
        id=uuid.uuid4(),
        type=kind,
        ref_id=ref_id or uuid.uuid4(),
        display_name=f"{kind} member",
    )


def _stub_storage_and_authorization(monkeypatch, tmp_path, actor: Participant):
    storage = LocalStorageBackend(str(tmp_path))

    async def authorize(_db, **kwargs):
        if kwargs.get("human_only") and actor.type != "user":
            raise AssertionError("test actor is not human")
        return None, None, actor

    monkeypatch.setattr(group_file_service, "get_storage_backend", lambda: storage)
    monkeypatch.setattr(
        group_file_service.group_chat_service,
        "authorize_group_member",
        authorize,
    )
    return storage


@pytest.mark.asyncio
async def test_group_workspace_uses_fixed_storage_prefix_and_group_revision(
    monkeypatch,
    tmp_path,
) -> None:
    tenant_id = uuid.uuid4()
    group_id = uuid.uuid4()
    actor = _participant("user")
    db = _RecordingDB()
    storage = _stub_storage_and_authorization(monkeypatch, tmp_path, actor)

    written = await group_file_service.write_workspace_file(
        db,
        tenant_id=tenant_id,
        group_id=group_id,
        actor_participant_id=actor.id,
        path="reports/final.md",
        content="# Final",
    )

    assert written.path == "reports/final.md"
    assert written.version_token
    assert await storage.read_text(
        f"groups/{group_id}/workspace/reports/final.md"
    ) == "# Final"
    revision = next(value for value in db.added if isinstance(value, WorkspaceFileRevision))
    assert revision.scope_type == "group"
    assert revision.scope_id == group_id
    assert revision.agent_id is None
    assert revision.path == "workspace/reports/final.md"
    assert revision.actor_type == "user"
    assert revision.actor_id == actor.ref_id

    read_back = await group_file_service.read_workspace_file(
        db,
        tenant_id=tenant_id,
        group_id=group_id,
        actor_participant_id=actor.id,
        path="reports/final.md",
    )
    entries = await group_file_service.list_workspace(
        db,
        tenant_id=tenant_id,
        group_id=group_id,
        actor_participant_id=actor.id,
        path="reports",
    )

    assert read_back.content == "# Final"
    assert [(entry.path, entry.is_dir) for entry in entries] == [
        ("reports/final.md", False)
    ]

    await group_file_service.delete_workspace_file(
        db,
        tenant_id=tenant_id,
        group_id=group_id,
        actor_participant_id=actor.id,
        path=entries[0].path,
        expected_version_token=entries[0].version_token,
    )
    assert await storage.exists(
        f"groups/{group_id}/workspace/reports/final.md"
    ) is False


@pytest.mark.asyncio
async def test_group_workspace_directory_size_includes_all_descendant_files(
    monkeypatch,
    tmp_path,
) -> None:
    tenant_id = uuid.uuid4()
    group_id = uuid.uuid4()
    actor = _participant("user")
    db = _RecordingDB()
    _stub_storage_and_authorization(monkeypatch, tmp_path, actor)

    for path, content in (
        ("reports/summary.md", "abc"),
        ("reports/archive/details.md", "12345"),
        ("outside.md", "not part of reports"),
    ):
        await group_file_service.write_workspace_file(
            db,
            tenant_id=tenant_id,
            group_id=group_id,
            actor_participant_id=actor.id,
            path=path,
            content=content,
        )

    entries = await group_file_service.list_workspace(
        db,
        tenant_id=tenant_id,
        group_id=group_id,
        actor_participant_id=actor.id,
    )

    reports = next(entry for entry in entries if entry.path == "reports")
    assert reports.is_dir is True
    assert reports.size == 8


@pytest.mark.asyncio
async def test_group_workspace_rejects_traversal_and_stale_writes(
    monkeypatch,
    tmp_path,
) -> None:
    tenant_id = uuid.uuid4()
    group_id = uuid.uuid4()
    actor = _participant("user")
    db = _RecordingDB()
    _stub_storage_and_authorization(monkeypatch, tmp_path, actor)

    with pytest.raises(group_file_service.GroupFileServiceError) as path_error:
        await group_file_service.write_workspace_file(
            db,
            tenant_id=tenant_id,
            group_id=group_id,
            actor_participant_id=actor.id,
            path="../system/announcement.md",
            content="escape",
        )
    assert path_error.value.code == "group_workspace_path_invalid"

    current = await group_file_service.write_workspace_file(
        db,
        tenant_id=tenant_id,
        group_id=group_id,
        actor_participant_id=actor.id,
        path="notes.md",
        content="v1",
    )
    assert current.version_token
    revision_count = len(db.added)

    with pytest.raises(group_file_service.GroupFileServiceError) as conflict:
        await group_file_service.write_workspace_file(
            db,
            tenant_id=tenant_id,
            group_id=group_id,
            actor_participant_id=actor.id,
            path="notes.md",
            content="stale",
            expected_version_token="stale-version",
        )
    assert conflict.value.code == "group_file_conflict"
    assert len(db.added) == revision_count


@pytest.mark.asyncio
async def test_group_workspace_create_can_require_the_path_to_be_absent(
    monkeypatch,
    tmp_path,
) -> None:
    tenant_id = uuid.uuid4()
    group_id = uuid.uuid4()
    actor = _participant("user")
    db = _RecordingDB()
    storage = _stub_storage_and_authorization(monkeypatch, tmp_path, actor)

    await group_file_service.write_workspace_file(
        db,
        tenant_id=tenant_id,
        group_id=group_id,
        actor_participant_id=actor.id,
        path="notes.md",
        content="existing",
    )

    with pytest.raises(group_file_service.GroupFileServiceError) as conflict:
        await group_file_service.write_workspace_file(
            db,
            tenant_id=tenant_id,
            group_id=group_id,
            actor_participant_id=actor.id,
            path="notes.md",
            content="upload",
            require_absent=True,
        )

    assert conflict.value.code == "group_file_conflict"
    assert await storage.read_text(f"groups/{group_id}/workspace/notes.md") == "existing"


@pytest.mark.asyncio
async def test_group_workspace_deletes_empty_directory_but_rejects_non_empty_directory(
    monkeypatch,
    tmp_path,
) -> None:
    tenant_id = uuid.uuid4()
    group_id = uuid.uuid4()
    actor = _participant("user")
    db = _RecordingDB()
    storage = _stub_storage_and_authorization(monkeypatch, tmp_path, actor)

    await group_file_service.write_workspace_file(
        db,
        tenant_id=tenant_id,
        group_id=group_id,
        actor_participant_id=actor.id,
        path="empty/.gitkeep",
        content="",
    )
    empty_entry = (await group_file_service.list_workspace(
        db,
        tenant_id=tenant_id,
        group_id=group_id,
        actor_participant_id=actor.id,
    ))[0]
    assert empty_entry.is_dir is True
    assert empty_entry.version_token

    await group_file_service.delete_workspace_file(
        db,
        tenant_id=tenant_id,
        group_id=group_id,
        actor_participant_id=actor.id,
        path=empty_entry.path,
        expected_version_token=empty_entry.version_token,
    )
    assert await storage.exists(f"groups/{group_id}/workspace/empty") is False

    await group_file_service.write_workspace_file(
        db,
        tenant_id=tenant_id,
        group_id=group_id,
        actor_participant_id=actor.id,
        path="full/file.md",
        content="keep me",
    )
    full_entry = (await group_file_service.list_workspace(
        db,
        tenant_id=tenant_id,
        group_id=group_id,
        actor_participant_id=actor.id,
    ))[0]

    with pytest.raises(group_file_service.GroupFileServiceError) as not_empty:
        await group_file_service.delete_workspace_file(
            db,
            tenant_id=tenant_id,
            group_id=group_id,
            actor_participant_id=actor.id,
            path=full_entry.path,
            expected_version_token=full_entry.version_token,
        )

    assert not_empty.value.code == "group_workspace_directory_not_empty"
    assert await storage.read_text(f"groups/{group_id}/workspace/full/file.md") == "keep me"


@pytest.mark.asyncio
async def test_group_workspace_virtual_directory_delete_never_recurses_over_new_object(
    monkeypatch,
) -> None:
    tenant_id = uuid.uuid4()
    group_id = uuid.uuid4()
    actor = _participant("user")
    db = _RecordingDB()
    directory_key = f"groups/{group_id}/workspace/empty"
    marker_key = f"{directory_key}/.gitkeep"
    arrived_key = f"{directory_key}/arrived.md"

    class _VirtualObjectStore:
        def __init__(self) -> None:
            self.objects = {marker_key: "marker-v1"}
            self.injected = False

        async def is_dir(self, key: str) -> bool:
            prefix = key.rstrip("/") + "/"
            return any(object_key.startswith(prefix) for object_key in self.objects)

        async def list_dir(self, key: str):
            assert key == directory_key
            entries = [
                StorageEntry(
                    name=".gitkeep",
                    key=marker_key,
                    is_dir=False,
                    etag="marker-v1",
                )
            ]
            if not self.injected:
                self.injected = True
                self.objects[arrived_key] = "arrived-v1"
            return entries

        async def get_version(self, key: str) -> StorageVersion:
            token = self.objects.get(key)
            return StorageVersion(
                key=key,
                exists=token is not None,
                is_dir=False,
                etag=token or "",
            )

        async def delete_if_match(self, key: str, *, condition):
            current = await self.get_version(key)
            if condition.version_token != current.token:
                return ConditionalWriteResult(
                    ok=False,
                    conflict=True,
                    current_version=current,
                )
            self.objects.pop(key, None)
            return ConditionalWriteResult(
                ok=True,
                current_version=await self.get_version(key),
            )

    storage = _VirtualObjectStore()

    async def authorize(_db, **_kwargs):
        return None, None, actor

    monkeypatch.setattr(group_file_service, "get_storage_backend", lambda: storage)
    monkeypatch.setattr(
        group_file_service.group_chat_service,
        "authorize_group_member",
        authorize,
    )

    with pytest.raises(group_file_service.GroupFileServiceError) as conflict:
        await group_file_service.delete_workspace_file(
            db,
            tenant_id=tenant_id,
            group_id=group_id,
            actor_participant_id=actor.id,
            path="empty",
            expected_version_token="marker-v1",
        )

    assert conflict.value.code == "group_file_conflict"
    assert arrived_key in storage.objects
    assert marker_key not in storage.objects


@pytest.mark.asyncio
async def test_agent_can_read_peer_memory_but_only_write_its_own(
    monkeypatch,
    tmp_path,
) -> None:
    tenant_id = uuid.uuid4()
    group_id = uuid.uuid4()
    actor_agent_id = uuid.uuid4()
    peer_agent_id = uuid.uuid4()
    actor = _participant("agent", actor_agent_id)
    peer = _participant("agent", peer_agent_id)
    db = _RecordingDB()
    _stub_storage_and_authorization(monkeypatch, tmp_path, actor)

    async def active_agent(_db, **kwargs):
        return actor if kwargs["agent_id"] == actor_agent_id else peer

    monkeypatch.setattr(group_file_service, "_active_agent_participant", active_agent)

    peer_memory = await group_file_service.read_agent_memory(
        db,
        tenant_id=tenant_id,
        group_id=group_id,
        actor_participant_id=actor.id,
        agent_id=peer_agent_id,
    )
    assert peer_memory.exists is False
    assert peer_memory.content == ""

    with pytest.raises(group_file_service.GroupFileServiceError) as denied:
        await group_file_service.write_agent_memory(
            db,
            tenant_id=tenant_id,
            group_id=group_id,
            actor_participant_id=actor.id,
            agent_id=peer_agent_id,
            content="not mine",
        )
    assert denied.value.code == "group_memory_write_denied"

    own_memory = await group_file_service.write_agent_memory(
        db,
        tenant_id=tenant_id,
        group_id=group_id,
        actor_participant_id=actor.id,
        agent_id=actor_agent_id,
        content="remember this",
        session_id=uuid.uuid4(),
    )
    assert own_memory.exists is True
    revision = db.added[-1]
    assert revision.path == f"agents/{actor_agent_id}/memory/memory.md"
    assert revision.actor_type == "agent"


@pytest.mark.asyncio
async def test_announcement_write_requires_human_authorization(
    monkeypatch,
    tmp_path,
) -> None:
    tenant_id = uuid.uuid4()
    group_id = uuid.uuid4()
    actor = _participant("user")
    db = _RecordingDB()
    storage = LocalStorageBackend(str(tmp_path))
    calls = []

    async def authorize(_db, **kwargs):
        calls.append(kwargs)
        return None, None, actor

    monkeypatch.setattr(group_file_service, "get_storage_backend", lambda: storage)
    monkeypatch.setattr(
        group_file_service.group_chat_service,
        "authorize_group_member",
        authorize,
    )

    result = await group_file_service.write_announcement(
        db,
        tenant_id=tenant_id,
        group_id=group_id,
        actor_participant_id=actor.id,
        content="Keep decisions explicit.",
    )

    assert calls == [
        {
            "tenant_id": tenant_id,
            "group_id": group_id,
            "participant_id": actor.id,
            "human_only": True,
        }
    ]
    assert result.content == "Keep decisions explicit."
    assert await storage.read_text(
        f"groups/{group_id}/system/announcement.md"
    ) == result.content
