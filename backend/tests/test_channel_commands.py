"""Unit tests for app.services.channel_commands.

Covers:
1. `handle_channel_command()` scopes its archive lookup by source_channel
   (no cross-channel collision on shared external_conv_id).
2. It archives the matching old session by renaming its external_conv_id.
3. It defers new-session creation to the next user message so the session
   title auto-names from the first message — rather than being locked to
   a hard-coded 'New Session' placeholder.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any

import pytest

from app.services import channel_commands


class _ExecutedQuery:
    """Captures WHERE-clause state for an executed SQLAlchemy select()."""

    def __init__(self, statement: Any) -> None:
        self.statement = statement
        # Extract column names referenced by equality comparisons in the WHERE
        # clause. This lets tests assert that source_channel is part of the
        # filter without depending on clause order.
        self.filter_columns: set[str] = set()
        self.filter_values: dict[str, Any] = {}
        whereclause = getattr(statement, "whereclause", None)
        self._collect(whereclause)

    def _collect(self, clause: Any) -> None:
        if clause is None:
            return
        # BooleanClauseList (AND/OR) has .clauses
        sub_clauses = getattr(clause, "clauses", None)
        if sub_clauses:
            for c in sub_clauses:
                self._collect(c)
            return
        left = getattr(clause, "left", None)
        right = getattr(clause, "right", None)
        if left is not None:
            name = getattr(left, "key", None) or getattr(left, "name", None)
            if name:
                self.filter_columns.add(name)
                if right is not None:
                    # BindParameter exposes .value
                    val = getattr(right, "value", None)
                    if val is not None:
                        self.filter_values[name] = val


class _FakeResult:
    def __init__(self, value: Any) -> None:
        self._value = value

    def scalar_one_or_none(self) -> Any:
        return self._value


class FakeDB:
    """Minimal AsyncSession stub that records executes / adds / flush / commit."""

    def __init__(self, lookup_result: Any = None) -> None:
        self._lookup_result = lookup_result
        self.executed: list[_ExecutedQuery] = []
        self.added: list[Any] = []
        self.flushes = 0

    async def execute(self, statement, _params=None):  # noqa: D401
        self.executed.append(_ExecutedQuery(statement))
        return _FakeResult(self._lookup_result)

    def add(self, obj) -> None:
        # Assign an id so handle_channel_command can stringify it.
        if getattr(obj, "id", None) is None:
            try:
                obj.id = uuid.uuid4()
            except Exception:
                pass
        self.added.append(obj)

    async def flush(self) -> None:
        self.flushes += 1


@pytest.mark.asyncio
async def test_handle_channel_command_scopes_lookup_by_source_channel():
    """Regression test for review concern #2.

    The session-archive lookup must include `source_channel` in its WHERE
    clause so a /new command on one channel never archives a same-external-id
    session on another channel.
    """
    agent_id = uuid.uuid4()
    user_id = uuid.uuid4()

    db = FakeDB(lookup_result=None)  # no pre-existing session

    result = await channel_commands.handle_channel_command(
        db=db,
        command="/new",
        agent_id=agent_id,
        user_id=user_id,
        external_conv_id="feishu_p2p_ou_xxx",
        source_channel="feishu",
    )

    assert result["action"] == "new_session"
    # Exactly one SELECT for the old-session lookup.
    assert len(db.executed) == 1
    q = db.executed[0]
    # The WHERE clause must filter on all three columns.
    assert "agent_id" in q.filter_columns
    assert "external_conv_id" in q.filter_columns
    assert "source_channel" in q.filter_columns, (
        "handle_channel_command() must scope the archive lookup by source_channel "
        "so it never archives a cross-channel session with a colliding external_conv_id"
    )
    assert q.filter_values.get("source_channel") == "feishu"


@pytest.mark.asyncio
async def test_handle_channel_command_does_not_preempt_session_creation():
    """New sessions must not be pre-created by /new — they're built by the
    next user message via find_or_create_channel_session, so the first real
    message content becomes the session title instead of "New Session".
    """
    agent_id = uuid.uuid4()
    user_id = uuid.uuid4()

    # Simulate no pre-existing session (lookup miss).
    db = FakeDB(lookup_result=None)

    result = await channel_commands.handle_channel_command(
        db=db,
        command="/new",
        agent_id=agent_id,
        user_id=user_id,
        external_conv_id="shared_conv_id_xxx",
        source_channel="feishu",
    )

    assert result["action"] == "new_session"
    # Nothing should be added to the DB — creation is deferred.
    assert db.added == []
    # And the response must not leak a session_id (there is no session yet).
    assert "session_id" not in result


@pytest.mark.asyncio
async def test_handle_channel_command_archives_old_session():
    """When a session for the same (agent_id, external_conv_id, source_channel)
    exists, /reset must archive it by renaming its external_conv_id, so the
    next user message creates a fresh one.
    """
    agent_id = uuid.uuid4()
    user_id = uuid.uuid4()

    # Existing session to be archived.
    old_session = SimpleNamespace(external_conv_id="feishu_p2p_ou_zzz")
    db = FakeDB(lookup_result=old_session)

    result = await channel_commands.handle_channel_command(
        db=db,
        command="/reset",
        agent_id=agent_id,
        user_id=user_id,
        external_conv_id="feishu_p2p_ou_zzz",
        source_channel="feishu",
    )

    assert result["action"] == "new_session"
    # Old session got its external_conv_id renamed to the archived form.
    assert old_session.external_conv_id.startswith("feishu_p2p_ou_zzz__archived_")
    # No new session pre-created (deferred to next user message).
    assert db.added == []


@pytest.mark.asyncio
async def test_is_channel_command_recognises_slash_commands():
    assert channel_commands.is_channel_command("/new") is True
    assert channel_commands.is_channel_command("/reset") is True
    assert channel_commands.is_channel_command("  /NEW  ") is True
    assert channel_commands.is_channel_command("/RESET") is True
    # Non-commands
    assert channel_commands.is_channel_command("hello") is False
    assert channel_commands.is_channel_command("/newish") is False
    assert channel_commands.is_channel_command("") is False
