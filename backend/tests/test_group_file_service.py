"""Group file boundary, permission, and revision tests."""

from __future__ import annotations

import uuid

import pytest

from app.models.participant import Participant
from app.models.workspace import WorkspaceFileRevision
from app.services import group_file_service
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
