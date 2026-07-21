"""Unit tests for transaction-scoped Participant identity helpers."""

import uuid

import pytest
from sqlalchemy.exc import IntegrityError

from app.models.participant import Participant
from app.services.participant_identity import (
    get_or_create_agent_participant,
    get_or_create_participant,
    get_or_create_user_participant,
)


class _ScalarResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class _NestedTransaction:
    def __init__(self, db: "_RecordingSession"):
        self.db = db

    async def __aenter__(self):
        self.db.nested_entries += 1
        return self

    async def __aexit__(self, exc_type, exc, tb):
        self.db.nested_exit_exceptions.append(exc_type)
        return False


class _RecordingSession:
    def __init__(self, *, results=(), flush_errors=()):
        self.results = list(results)
        self.flush_errors = list(flush_errors)
        self.statements = []
        self.added = []
        self.flush_count = 0
        self.commit_count = 0
        self.rollback_count = 0
        self.nested_entries = 0
        self.nested_exit_exceptions = []

    async def execute(self, statement):
        self.statements.append(statement)
        return _ScalarResult(self.results.pop(0))

    def add(self, value):
        self.added.append(value)

    async def flush(self):
        self.flush_count += 1
        if self.flush_errors:
            error = self.flush_errors.pop(0)
            if error is not None:
                raise error

    def begin_nested(self):
        return _NestedTransaction(self)

    async def commit(self):
        self.commit_count += 1

    async def rollback(self):
        self.rollback_count += 1


@pytest.mark.asyncio
async def test_existing_participant_is_reused_and_non_empty_fields_are_synced():
    ref_id = uuid.uuid4()
    existing = Participant(
        type="user",
        ref_id=ref_id,
        display_name="Old name",
        avatar_url="old.png",
    )
    db = _RecordingSession(results=[existing])

    participant = await get_or_create_participant(
        db,
        "user",
        ref_id,
        "New name",
        "new.png",
    )

    assert participant is existing
    assert participant.display_name == "New name"
    assert participant.avatar_url == "new.png"
    assert db.added == []
    assert db.flush_count == 1
    assert db.nested_entries == 0
    sql = str(db.statements[0])
    assert "participants.type" in sql
    assert "participants.ref_id" in sql


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("helper", "expected_type"),
    [
        (get_or_create_user_participant, "user"),
        (get_or_create_agent_participant, "agent"),
    ],
)
async def test_wrappers_create_identity_in_the_supplied_session(helper, expected_type):
    ref_id = uuid.uuid4()
    db = _RecordingSession(results=[None])

    participant = await helper(db, ref_id, "Identity", "avatar.png")

    assert participant is db.added[0]
    assert participant.type == expected_type
    assert participant.ref_id == ref_id
    assert participant.display_name == "Identity"
    assert participant.avatar_url == "avatar.png"
    assert db.flush_count == 1
    assert db.nested_entries == 1
    assert db.commit_count == 0
    assert db.rollback_count == 0


@pytest.mark.asyncio
async def test_invalid_participant_type_is_rejected_before_using_the_session():
    db = _RecordingSession()

    with pytest.raises(ValueError, match="user.*agent"):
        await get_or_create_participant(
            db,
            "service-account",
            uuid.uuid4(),
            "Invalid",
        )

    assert db.statements == []
    assert db.added == []
    assert db.flush_count == 0
    assert db.commit_count == 0
    assert db.rollback_count == 0


@pytest.mark.asyncio
async def test_unique_conflict_rolls_back_only_savepoint_then_reuses_winner():
    ref_id = uuid.uuid4()
    winner = Participant(
        type="agent",
        ref_id=ref_id,
        display_name="Concurrent name",
        avatar_url=None,
    )
    conflict = IntegrityError(
        statement="INSERT INTO participants",
        params={},
        orig=Exception("uq_participants_type_ref"),
    )
    db = _RecordingSession(
        results=[None, winner],
        flush_errors=[conflict, None],
    )

    participant = await get_or_create_agent_participant(
        db,
        ref_id,
        "Requested name",
        "requested.png",
    )

    assert participant is winner
    assert participant.display_name == "Requested name"
    assert participant.avatar_url == "requested.png"
    assert db.nested_entries == 1
    assert db.nested_exit_exceptions == [IntegrityError]
    assert len(db.statements) == 2
    assert db.flush_count == 2
    assert db.commit_count == 0
    assert db.rollback_count == 0


@pytest.mark.asyncio
async def test_unrelated_integrity_error_is_not_swallowed():
    conflict = IntegrityError(
        statement="INSERT INTO participants",
        params={},
        orig=Exception("some_other_constraint"),
    )
    db = _RecordingSession(
        results=[None, None],
        flush_errors=[conflict],
    )

    with pytest.raises(IntegrityError) as raised:
        await get_or_create_user_participant(
            db,
            uuid.uuid4(),
            "Identity",
        )

    assert raised.value is conflict
    assert db.nested_exit_exceptions == [IntegrityError]
    assert db.rollback_count == 0
    assert db.commit_count == 0
