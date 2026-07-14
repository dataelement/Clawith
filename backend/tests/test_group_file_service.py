"""Group file boundary, permission, and revision tests."""

from __future__ import annotations

import asyncio
import hashlib
import uuid

import pytest

from app.database import transaction
from app.models.participant import Participant
from app.models.workspace import WorkspaceFileRevision
from app.services import group_file_service, workspace_locking
from app.services.group_chat_service import GroupChatServiceError
from app.services.storage_runtime.base import WriteCondition
from app.services.storage_runtime.local import LocalStorageBackend


class _FakeWorkspaceLockRedis:
    def __init__(self, *, fail_release: bool = False) -> None:
        self.values: dict[str, str] = {}
        self.set_calls: list[tuple[str, str, int | None, bool]] = []
        self.release_calls: list[tuple[str, str]] = []
        self.fail_release = fail_release

    async def set(
        self,
        key: str,
        value: str,
        *,
        ex: int | None = None,
        nx: bool = False,
    ) -> bool:
        self.set_calls.append((key, value, ex, nx))
        if nx and key in self.values:
            return False
        self.values[key] = value
        return True

    async def eval(
        self,
        _script: str,
        _key_count: int,
        key: str,
        owner_token: str,
    ) -> int:
        self.release_calls.append((key, owner_token))
        if self.fail_release:
            raise RuntimeError("redis release unavailable")
        if self.values.get(key) != owner_token:
            return 0
        self.values.pop(key)
        return 1


class _RecordingDB:
    def __init__(self, *, commit_error: Exception | None = None) -> None:
        self.added = []
        self.flush_count = 0
        self.info = {}
        self.commit_count = 0
        self.rollback_count = 0
        self.commit_error = commit_error

    def add(self, value) -> None:
        self.added.append(value)

    async def flush(self) -> None:
        self.flush_count += 1

    async def execute(self, _statement):
        raise AssertionError("authorization lookup should be stubbed in this test")

    async def commit(self) -> None:
        self.commit_count += 1
        if self.commit_error is not None:
            raise self.commit_error

    async def rollback(self) -> None:
        self.rollback_count += 1


def _participant(kind: str, ref_id: uuid.UUID | None = None) -> Participant:
    return Participant(
        id=uuid.uuid4(),
        type=kind,
        ref_id=ref_id or uuid.uuid4(),
        display_name=f"{kind} member",
    )


def _stub_storage_and_authorization(
    monkeypatch,
    tmp_path,
    actor: Participant,
    *,
    lock_redis: _FakeWorkspaceLockRedis | None = None,
):
    storage = LocalStorageBackend(str(tmp_path))
    lock_redis = lock_redis or _FakeWorkspaceLockRedis()

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

    async def get_lock_redis():
        return lock_redis

    monkeypatch.setattr(workspace_locking, "get_redis", get_lock_redis)
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
async def test_binary_upload_preserves_bytes_and_records_hash_only_revision(
    monkeypatch,
    tmp_path,
) -> None:
    tenant_id = uuid.uuid4()
    group_id = uuid.uuid4()
    actor = _participant("user")
    db = _RecordingDB()
    storage = _stub_storage_and_authorization(monkeypatch, tmp_path, actor)
    payload = b"\x00\xff\x89PNG\r\n\x1a\n\x80binary\x00payload"
    storage_key = f"groups/{group_id}/workspace/evidence/screenshot.png"

    uploaded = await group_file_service.upload_workspace_file(
        db,
        tenant_id=tenant_id,
        group_id=group_id,
        actor_participant_id=actor.id,
        directory="evidence",
        filename="screenshot.png",
        content=payload,
        content_type="image/png",
    )

    assert uploaded.path == "evidence/screenshot.png"
    assert uploaded.storage_key == storage_key
    assert uploaded.size == len(payload)
    assert uploaded.content_type == "image/png"
    assert await storage.read_bytes(storage_key) == payload
    revision = next(value for value in db.added if isinstance(value, WorkspaceFileRevision))
    assert revision.scope_type == "group"
    assert revision.scope_id == group_id
    assert revision.path == "workspace/evidence/screenshot.png"
    assert revision.operation == "upload"
    assert revision.before_content is None
    assert revision.after_content is None
    assert revision.content_hash == hashlib.sha256(payload).hexdigest()


@pytest.mark.asyncio
@pytest.mark.parametrize("previous_content", [None, b"old binary content"])
async def test_binary_upload_revision_failure_restores_previous_storage_state(
    monkeypatch,
    tmp_path,
    previous_content: bytes | None,
) -> None:
    tenant_id = uuid.uuid4()
    group_id = uuid.uuid4()
    actor = _participant("user")
    db = _RecordingDB()
    lock_redis = _FakeWorkspaceLockRedis()
    storage = _stub_storage_and_authorization(
        monkeypatch,
        tmp_path,
        actor,
        lock_redis=lock_redis,
    )
    storage_key = f"groups/{group_id}/workspace/evidence.bin"
    if previous_content is not None:
        await storage.write_bytes(storage_key, previous_content)

    async def fail_revision(*_args, **_kwargs):
        raise RuntimeError("revision insert failed")

    monkeypatch.setattr(group_file_service, "record_group_revision", fail_revision)

    with pytest.raises(RuntimeError, match="revision insert failed"):
        async with transaction(db):  # type: ignore[arg-type]
            await group_file_service.upload_workspace_file(
                db,
                tenant_id=tenant_id,
                group_id=group_id,
                actor_participant_id=actor.id,
                directory="",
                filename="evidence.bin",
                content=b"uncommitted upload",
            )

    restored = await storage.get_version(storage_key)
    assert restored.exists is (previous_content is not None)
    if previous_content is not None:
        assert await storage.read_bytes(storage_key) == previous_content
    assert db.commit_count == 0
    assert db.rollback_count == 1
    assert db.info == {}
    assert lock_redis.values == {}
    assert len(lock_redis.release_calls) == 1


@pytest.mark.asyncio
async def test_binary_upload_commit_failure_restores_overwritten_file(
    monkeypatch,
    tmp_path,
) -> None:
    tenant_id = uuid.uuid4()
    group_id = uuid.uuid4()
    actor = _participant("user")
    commit_error = RuntimeError("database commit failed")
    db = _RecordingDB(commit_error=commit_error)
    storage = _stub_storage_and_authorization(monkeypatch, tmp_path, actor)
    storage_key = f"groups/{group_id}/workspace/report.dat"
    await storage.write_bytes(storage_key, b"committed old content")

    with pytest.raises(RuntimeError, match="database commit failed") as exc_info:
        async with transaction(db):  # type: ignore[arg-type]
            await group_file_service.upload_workspace_file(
                db,
                tenant_id=tenant_id,
                group_id=group_id,
                actor_participant_id=actor.id,
                directory="",
                filename="report.dat",
                content=b"uncommitted new content",
            )

    assert exc_info.value is commit_error
    assert await storage.read_bytes(storage_key) == b"committed old content"
    assert db.commit_count == 1
    assert db.rollback_count == 1
    assert db.info == {}


@pytest.mark.asyncio
async def test_binary_upload_rollback_does_not_overwrite_newer_storage_version(
    monkeypatch,
    tmp_path,
) -> None:
    tenant_id = uuid.uuid4()
    group_id = uuid.uuid4()
    actor = _participant("user")
    db = _RecordingDB()
    lock_redis = _FakeWorkspaceLockRedis()
    storage = _stub_storage_and_authorization(
        monkeypatch,
        tmp_path,
        actor,
        lock_redis=lock_redis,
    )
    storage_key = f"groups/{group_id}/workspace/report.dat"
    await storage.write_bytes(storage_key, b"old content")

    with pytest.raises(RuntimeError, match="abort transaction"):
        async with transaction(db):  # type: ignore[arg-type]
            uploaded = await group_file_service.upload_workspace_file(
                db,
                tenant_id=tenant_id,
                group_id=group_id,
                actor_participant_id=actor.id,
                directory="",
                filename="report.dat",
                content=b"uncommitted upload",
            )
            newer = await storage.write_bytes_if_match(
                storage_key,
                b"newer committed content",
                condition=WriteCondition(version_token=uploaded.version_token),
            )
            assert newer.ok
            raise RuntimeError("abort transaction")

    assert await storage.read_bytes(storage_key) == b"newer committed content"
    assert db.rollback_count == 1
    assert db.info == {}


@pytest.mark.asyncio
async def test_binary_upload_success_discards_rollback_compensation(
    monkeypatch,
    tmp_path,
) -> None:
    tenant_id = uuid.uuid4()
    group_id = uuid.uuid4()
    actor = _participant("user")
    db = _RecordingDB()
    lock_redis = _FakeWorkspaceLockRedis()
    storage = _stub_storage_and_authorization(
        monkeypatch,
        tmp_path,
        actor,
        lock_redis=lock_redis,
    )
    storage_key = f"groups/{group_id}/workspace/report.dat"
    await storage.write_bytes(storage_key, b"old content")

    async with transaction(db):  # type: ignore[arg-type]
        await group_file_service.upload_workspace_file(
            db,
            tenant_id=tenant_id,
            group_id=group_id,
            actor_participant_id=actor.id,
            directory="",
            filename="report.dat",
            content=b"committed new content",
        )

    assert await storage.read_bytes(storage_key) == b"committed new content"
    assert db.commit_count == 1
    assert db.rollback_count == 0
    assert db.info == {}
    assert lock_redis.values == {}
    assert len(lock_redis.release_calls) == 1


@pytest.mark.asyncio
async def test_upload_compensation_holds_path_lock_until_restore_finishes(
    monkeypatch,
    tmp_path,
) -> None:
    tenant_id = uuid.uuid4()
    group_id = uuid.uuid4()
    actor = _participant("user")
    storage_key = f"groups/{group_id}/workspace/report.dat"
    lock_redis = _FakeWorkspaceLockRedis()
    storage = _stub_storage_and_authorization(
        monkeypatch,
        tmp_path,
        actor,
        lock_redis=lock_redis,
    )
    await storage.write_bytes(storage_key, b"committed old content")

    compensation_started = asyncio.Event()
    finish_compensation = asyncio.Event()
    original_write_if_match = storage.write_bytes_if_match

    async def gated_write_if_match(key, data, *, condition=None, content_type=None):
        if key == storage_key and data == b"committed old content":
            compensation_started.set()
            await finish_compensation.wait()
        return await original_write_if_match(
            key,
            data,
            condition=condition,
            content_type=content_type,
        )

    monkeypatch.setattr(storage, "write_bytes_if_match", gated_write_if_match)
    failed_db = _RecordingDB()

    async def abort_upload() -> None:
        async with transaction(failed_db):  # type: ignore[arg-type]
            await group_file_service.upload_workspace_file(
                failed_db,
                tenant_id=tenant_id,
                group_id=group_id,
                actor_participant_id=actor.id,
                directory="",
                filename="report.dat",
                content=b"uncommitted upload",
            )
            raise RuntimeError("abort upload")

    rollback_task = asyncio.create_task(abort_upload())
    await asyncio.wait_for(compensation_started.wait(), timeout=1)

    competing_db = _RecordingDB()
    with pytest.raises(group_file_service.GroupFileServiceError) as busy:
        await group_file_service.write_workspace_file(
            competing_db,
            tenant_id=tenant_id,
            group_id=group_id,
            actor_participant_id=actor.id,
            path="report.dat",
            content="must not interleave",
        )
    assert busy.value.code == "group_file_conflict"
    assert await storage.read_bytes(storage_key) == b"uncommitted upload"

    finish_compensation.set()
    with pytest.raises(RuntimeError, match="abort upload"):
        await rollback_task
    assert await storage.read_bytes(storage_key) == b"committed old content"
    assert lock_redis.values == {}

    succeeding_db = _RecordingDB()
    async with transaction(succeeding_db):  # type: ignore[arg-type]
        written = await group_file_service.write_workspace_file(
            succeeding_db,
            tenant_id=tenant_id,
            group_id=group_id,
            actor_participant_id=actor.id,
            path="report.dat",
            content="write after release",
        )
    assert written.content == "write after release"
    assert await storage.read_text(storage_key) == "write after release"
    assert lock_redis.values == {}
    assert len(lock_redis.set_calls) == 3
    assert len(lock_redis.release_calls) == 2


@pytest.mark.asyncio
async def test_group_workspace_lock_release_failure_falls_back_to_ttl(
    monkeypatch,
    tmp_path,
) -> None:
    tenant_id = uuid.uuid4()
    group_id = uuid.uuid4()
    actor = _participant("user")
    db = _RecordingDB()
    lock_redis = _FakeWorkspaceLockRedis(fail_release=True)
    _stub_storage_and_authorization(
        monkeypatch,
        tmp_path,
        actor,
        lock_redis=lock_redis,
    )

    async with transaction(db):  # type: ignore[arg-type]
        await group_file_service.write_workspace_file(
            db,
            tenant_id=tenant_id,
            group_id=group_id,
            actor_participant_id=actor.id,
            path="release-failure.txt",
            content="committed",
        )

    assert db.commit_count == 1
    assert db.info == {}
    assert len(lock_redis.values) == 1
    assert len(lock_redis.release_calls) == 1


@pytest.mark.asyncio
async def test_binary_workspace_paths_reject_traversal(monkeypatch, tmp_path) -> None:
    tenant_id = uuid.uuid4()
    group_id = uuid.uuid4()
    actor = _participant("user")
    db = _RecordingDB()
    _stub_storage_and_authorization(monkeypatch, tmp_path, actor)

    with pytest.raises(group_file_service.GroupFileServiceError) as upload_error:
        await group_file_service.upload_workspace_file(
            db,
            tenant_id=tenant_id,
            group_id=group_id,
            actor_participant_id=actor.id,
            directory="../system",
            filename="announcement.md",
            content=b"escape",
        )
    assert upload_error.value.code == "group_workspace_path_invalid"

    with pytest.raises(group_file_service.GroupFileServiceError) as download_error:
        await group_file_service.prepare_workspace_download(
            db,
            tenant_id=tenant_id,
            group_id=group_id,
            actor_participant_id=actor.id,
            path="../../system/announcement.md",
        )
    assert download_error.value.code == "group_workspace_path_invalid"


@pytest.mark.asyncio
async def test_binary_download_requires_group_membership_authorization(
    monkeypatch,
) -> None:
    tenant_id = uuid.uuid4()
    group_id = uuid.uuid4()
    actor = _participant("user")
    db = _RecordingDB()
    calls = []

    async def deny(_db, **kwargs):
        calls.append((_db, kwargs))
        raise GroupChatServiceError("group_access_denied", "Membership is required")

    def unexpected_storage():
        raise AssertionError("storage must not be read before authorization")

    monkeypatch.setattr(
        group_file_service.group_chat_service,
        "authorize_group_member",
        deny,
    )
    monkeypatch.setattr(group_file_service, "get_storage_backend", unexpected_storage)

    with pytest.raises(GroupChatServiceError) as exc_info:
        await group_file_service.prepare_workspace_download(
            db,
            tenant_id=tenant_id,
            group_id=group_id,
            actor_participant_id=actor.id,
            path="evidence/screenshot.png",
        )

    assert exc_info.value.code == "group_access_denied"
    assert calls == [
        (
            db,
            {
                "tenant_id": tenant_id,
                "group_id": group_id,
                "participant_id": actor.id,
                "human_only": False,
            },
        )
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
