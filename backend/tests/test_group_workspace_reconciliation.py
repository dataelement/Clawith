"""Durable Group Workspace mutation and reconciliation contracts."""

from __future__ import annotations

from collections import deque
import uuid

import pytest
from sqlalchemy.exc import IntegrityError

from app.models.participant import Participant
from app.models.workspace import WorkspaceFileRevision
from app.services import group_file_service, workspace_collaboration
from app.services.storage_runtime.local import LocalStorageBackend
from app.services.storage_runtime.s3 import S3StorageBackend


class _ScalarResult:
    def __init__(self, value=None, values=()) -> None:
        self._value = value
        self._values = list(values)

    def scalar_one_or_none(self):
        return self._value

    def scalars(self):
        return self

    def all(self):
        return list(self._values)


class _RevisionDB:
    def __init__(self, *results: _ScalarResult) -> None:
        self.results = deque(results)
        self.added: list[object] = []
        self.statements: list[object] = []
        self.flush_count = 0

    async def execute(self, statement):
        self.statements.append(statement)
        return self.results.popleft()

    def add(self, value) -> None:
        self.added.append(value)

    async def flush(self) -> None:
        self.flush_count += 1

    def begin_nested(self):
        return _NestedTransaction()


class _NestedTransaction:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False


class _ConcurrentRevisionDB(_RevisionDB):
    async def flush(self) -> None:
        self.flush_count += 1
        raise IntegrityError("insert revision", {}, RuntimeError("duplicate pk"))


def _actor() -> Participant:
    return Participant(
        id=uuid.uuid4(),
        type="agent",
        ref_id=uuid.uuid4(),
        display_name="Writer",
    )


def _prepared_revision(
    *,
    group_id: uuid.UUID,
    operation_id: uuid.UUID,
    operation: str,
    path: str,
    before: str | None,
    after: str | None,
) -> WorkspaceFileRevision:
    return WorkspaceFileRevision(
        id=operation_id,
        agent_id=None,
        scope_type="group",
        scope_id=group_id,
        path=f"workspace/{path}",
        operation=f"prepared_{operation}",
        actor_type="agent",
        actor_id=uuid.uuid4(),
        session_id=str(uuid.uuid4()),
        before_content=before,
        after_content=after,
        content_hash=workspace_collaboration.content_hash(after),
        group_key=workspace_collaboration.group_runtime_operation_key(operation_id),
    )


@pytest.mark.asyncio
async def test_group_runtime_revision_uses_operation_id_and_prepared_is_hidden_from_history(
) -> None:
    group_id = uuid.uuid4()
    operation_id = uuid.uuid4()
    actor = _actor()
    db = _RevisionDB(_ScalarResult())

    revision = await workspace_collaboration.prepare_group_runtime_revision(
        db,
        group_id=group_id,
        operation_id=operation_id,
        path="workspace/report.md",
        operation="write",
        actor_type=actor.type,
        actor_id=actor.ref_id,
        before_content="old",
        after_content="new",
        session_id=str(uuid.uuid4()),
    )

    assert revision in db.added
    assert revision.id == operation_id
    assert revision.operation == "prepared_write"
    assert revision.group_key == f"runtime-operation:{operation_id}"
    assert revision.content_hash == workspace_collaboration.content_hash("new")

    db.results.append(_ScalarResult(revision))
    finalized = await workspace_collaboration.finalize_group_runtime_revision(
        db,
        group_id=group_id,
        operation_id=operation_id,
        operation="write",
    )
    assert finalized.id == revision.id
    assert finalized.operation == "write"

    hidden = _prepared_revision(
        group_id=group_id,
        operation_id=uuid.uuid4(),
        operation="delete",
        path="draft.md",
        before="draft",
        after=None,
    )
    db.results.append(_ScalarResult(values=[finalized]))
    history = await workspace_collaboration.list_group_revisions(
        db,
        group_id=group_id,
        path="workspace/report.md",
    )
    assert history == [finalized]
    history_sql = str(db.statements[-1])
    assert "workspace_file_revisions.operation NOT IN" in history_sql
    assert hidden.operation == "prepared_delete"

    duplicate_db = _RevisionDB(_ScalarResult(revision))
    duplicate = await workspace_collaboration.prepare_group_runtime_revision(
        duplicate_db,
        group_id=group_id,
        operation_id=operation_id,
        path="workspace/report.md",
        operation="write",
        actor_type=actor.type,
        actor_id=actor.ref_id,
        before_content="old",
        after_content="new",
        session_id=revision.session_id,
    )
    assert duplicate is revision
    assert duplicate_db.added == []


@pytest.mark.asyncio
async def test_concurrent_prepare_reuses_the_revision_primary_key_winner() -> None:
    group_id = uuid.uuid4()
    operation_id = uuid.uuid4()
    actor = _actor()
    session_id = str(uuid.uuid4())
    winner = WorkspaceFileRevision(
        id=operation_id,
        agent_id=None,
        scope_type="group",
        scope_id=group_id,
        path="workspace/report.md",
        operation="prepared_write",
        actor_type=actor.type,
        actor_id=actor.ref_id,
        session_id=session_id,
        before_content="old",
        after_content="new",
        content_hash=workspace_collaboration.content_hash("new"),
        group_key=workspace_collaboration.group_runtime_operation_key(
            operation_id
        ),
    )
    db = _ConcurrentRevisionDB(_ScalarResult(), _ScalarResult(winner))

    revision = await workspace_collaboration.prepare_group_runtime_revision(
        db,
        group_id=group_id,
        operation_id=operation_id,
        path="workspace/report.md",
        operation="write",
        actor_type=actor.type,
        actor_id=actor.ref_id,
        before_content="old",
        after_content="new",
        session_id=session_id,
    )

    assert revision is winner
    assert revision.id == operation_id
    assert db.flush_count == 1


class _CountingStorage(LocalStorageBackend):
    def __init__(self, root: str) -> None:
        super().__init__(root)
        self.conditional_writes = 0
        self.conditional_deletes = 0

    async def write_bytes_if_match(self, *args, **kwargs):
        self.conditional_writes += 1
        return await super().write_bytes_if_match(*args, **kwargs)

    async def delete_if_match(self, *args, **kwargs):
        self.conditional_deletes += 1
        return await super().delete_if_match(*args, **kwargs)


@pytest.mark.asyncio
async def test_prepared_write_with_after_hash_forward_finalizes_without_rewriting(
    monkeypatch,
    tmp_path,
) -> None:
    group_id = uuid.uuid4()
    operation_id = uuid.uuid4()
    revision = _prepared_revision(
        group_id=group_id,
        operation_id=operation_id,
        operation="write",
        path="report.md",
        before="old",
        after="final",
    )
    storage = _CountingStorage(str(tmp_path))
    key = f"groups/{group_id}/workspace/report.md"
    await storage.write_text(key, "final")
    finalized: list[WorkspaceFileRevision] = []

    async def get_revision(_db, **kwargs):
        assert kwargs == {
            "group_id": group_id,
            "operation_id": operation_id,
            "lock": True,
        }
        return revision

    async def finalize(_db, **kwargs):
        assert kwargs["operation"] == "write"
        revision.operation = "write"
        finalized.append(revision)
        return revision

    monkeypatch.setattr(group_file_service, "get_storage_backend", lambda: storage)
    monkeypatch.setattr(
        group_file_service,
        "get_group_runtime_revision",
        get_revision,
    )
    monkeypatch.setattr(
        group_file_service,
        "finalize_group_runtime_revision",
        finalize,
    )

    receipt = await group_file_service.reconcile_runtime_workspace_operation(
        object(),
        group_id=group_id,
        operation_id=operation_id,
    )

    assert finalized == [revision]
    assert receipt.operation_id == operation_id
    assert receipt.revision_id == revision.id
    assert receipt.operation == "write"
    assert receipt.path == "report.md"
    assert receipt.content_hash == workspace_collaboration.content_hash("final")
    assert storage.conditional_writes == 0
    assert storage.conditional_deletes == 0


@pytest.mark.asyncio
async def test_prepared_delete_with_missing_file_forward_finalizes_without_redeleting(
    monkeypatch,
    tmp_path,
) -> None:
    group_id = uuid.uuid4()
    operation_id = uuid.uuid4()
    revision = _prepared_revision(
        group_id=group_id,
        operation_id=operation_id,
        operation="delete",
        path="obsolete.md",
        before="remove me",
        after=None,
    )
    storage = _CountingStorage(str(tmp_path))

    async def get_revision(_db, **_kwargs):
        return revision

    async def finalize(_db, **_kwargs):
        revision.operation = "delete"
        return revision

    monkeypatch.setattr(group_file_service, "get_storage_backend", lambda: storage)
    monkeypatch.setattr(
        group_file_service,
        "get_group_runtime_revision",
        get_revision,
    )
    monkeypatch.setattr(
        group_file_service,
        "finalize_group_runtime_revision",
        finalize,
    )

    receipt = await group_file_service.reconcile_runtime_workspace_operation(
        object(),
        group_id=group_id,
        operation_id=operation_id,
    )

    assert receipt.operation == "delete"
    assert receipt.deleted is True
    assert storage.conditional_writes == 0
    assert storage.conditional_deletes == 0


@pytest.mark.asyncio
async def test_prepared_delete_storage_read_failure_never_finalizes(
    monkeypatch,
) -> None:
    group_id = uuid.uuid4()
    operation_id = uuid.uuid4()
    revision = _prepared_revision(
        group_id=group_id,
        operation_id=operation_id,
        operation="delete",
        path="obsolete.md",
        before="remove me",
        after=None,
    )
    storage = S3StorageBackend(bucket="bucket")

    class FailingHeadClient:
        def head_object(self, **_kwargs):
            raise PermissionError("storage read denied")

    storage._client = FailingHeadClient()

    async def get_revision(_db, **_kwargs):
        return revision

    async def never_finalize(*_args, **_kwargs):
        raise AssertionError("an unverified delete must not finalize")

    monkeypatch.setattr(group_file_service, "get_storage_backend", lambda: storage)
    monkeypatch.setattr(
        group_file_service,
        "get_group_runtime_revision",
        get_revision,
    )
    monkeypatch.setattr(
        group_file_service,
        "finalize_group_runtime_revision",
        never_finalize,
    )

    with pytest.raises(PermissionError, match="storage read denied"):
        await group_file_service.reconcile_runtime_workspace_operation(
            object(),
            group_id=group_id,
            operation_id=operation_id,
        )


@pytest.mark.asyncio
async def test_committed_revision_rebuilds_stable_receipt_without_storage_access(
    monkeypatch,
) -> None:
    group_id = uuid.uuid4()
    operation_id = uuid.uuid4()
    revision = _prepared_revision(
        group_id=group_id,
        operation_id=operation_id,
        operation="write",
        path="final.md",
        before="draft",
        after="final",
    )
    revision.operation = "write"

    async def get_revision(_db, **_kwargs):
        return revision

    monkeypatch.setattr(
        group_file_service,
        "get_group_runtime_revision",
        get_revision,
    )
    monkeypatch.setattr(
        group_file_service,
        "get_storage_backend",
        lambda: (_ for _ in ()).throw(
            AssertionError("committed replay must not inspect or mutate storage")
        ),
    )

    receipt = await group_file_service.reconcile_runtime_workspace_operation(
        object(),
        group_id=group_id,
        operation_id=operation_id,
    )

    assert receipt.operation_id == operation_id
    assert receipt.revision_id == revision.id
    assert receipt.content_hash == revision.content_hash


@pytest.mark.asyncio
async def test_prepared_write_third_storage_state_is_unknown_conflict_and_never_rewritten(
    monkeypatch,
    tmp_path,
) -> None:
    group_id = uuid.uuid4()
    operation_id = uuid.uuid4()
    revision = _prepared_revision(
        group_id=group_id,
        operation_id=operation_id,
        operation="write",
        path="report.md",
        before="old",
        after="expected",
    )
    storage = _CountingStorage(str(tmp_path))
    await storage.write_text(f"groups/{group_id}/workspace/report.md", "other writer")

    async def get_revision(_db, **_kwargs):
        return revision

    async def never_finalize(*_args, **_kwargs):
        raise AssertionError("conflicting storage must not finalize")

    monkeypatch.setattr(group_file_service, "get_storage_backend", lambda: storage)
    monkeypatch.setattr(
        group_file_service,
        "get_group_runtime_revision",
        get_revision,
    )
    monkeypatch.setattr(
        group_file_service,
        "finalize_group_runtime_revision",
        never_finalize,
    )

    with pytest.raises(group_file_service.GroupFileServiceError) as error:
        await group_file_service.reconcile_runtime_workspace_operation(
            object(),
            group_id=group_id,
            operation_id=operation_id,
        )

    assert error.value.code == "group_workspace_reconciliation_conflict"
    assert await storage.read_text(
        f"groups/{group_id}/workspace/report.md"
    ) == "other writer"
    assert storage.conditional_writes == 0
    assert storage.conditional_deletes == 0


@pytest.mark.asyncio
async def test_runtime_write_cas_uses_captured_version_even_without_model_token(
    monkeypatch,
    tmp_path,
) -> None:
    tenant_id = uuid.uuid4()
    group_id = uuid.uuid4()
    operation_id = uuid.uuid4()
    actor = _actor()
    storage = _CountingStorage(str(tmp_path))
    key = f"groups/{group_id}/workspace/report.md"
    await storage.write_text(key, "v1")
    captured_revision = _prepared_revision(
        group_id=group_id,
        operation_id=operation_id,
        operation="write",
        path="report.md",
        before="v1",
        after="v2",
    )

    async def authorize(*_args, **_kwargs):
        return None, None, actor

    async def prepare_revision(*_args, **_kwargs):
        return captured_revision

    monkeypatch.setattr(group_file_service, "get_storage_backend", lambda: storage)
    monkeypatch.setattr(
        group_file_service.group_chat_service,
        "authorize_group_member",
        authorize,
    )
    monkeypatch.setattr(
        group_file_service,
        "prepare_group_runtime_revision",
        prepare_revision,
    )

    prepared = await group_file_service.prepare_runtime_workspace_write(
        object(),
        tenant_id=tenant_id,
        group_id=group_id,
        actor_participant_id=actor.id,
        operation_id=operation_id,
        path="report.md",
        content="v2",
        expected_version_token=None,
        session_id=uuid.uuid4(),
    )
    await storage.write_text(key, "concurrent")

    with pytest.raises(group_file_service.GroupFileServiceError) as error:
        await group_file_service.apply_runtime_workspace_operation(prepared)

    assert error.value.code == "group_file_conflict"
    assert await storage.read_text(key) == "concurrent"
    assert storage.conditional_writes == 1
