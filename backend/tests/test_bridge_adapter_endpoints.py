"""Endpoint-level tests for the `bridge_adapter` plumbing.

Covers three code paths that existed with no regression coverage:

1. `GET /agents/{id}/bridge-status` — response shape for native agents
   (not applicable), openclaw without a bridge (disconnected), and
   openclaw with a bridge connected. Ensures auth goes through
   `check_agent_access` like the rest of the agent endpoints.

2. `PATCH /agents/{id}` — silently drops `bridge_adapter` when the
   agent is not openclaw, and applies it when it is. The "silent drop"
   behavior is load-bearing for generic bulk-update flows; if we
   errored instead, the UI would have to branch on agent type before
   every PATCH.

3. `POST /agents/` — defaults `bridge_adapter` to `"claude_code"` for
   openclaw agents when the caller omits it, respects an explicit
   value when provided, and leaves it NULL for native agents even if
   the caller mistakenly sends one.
"""

import uuid
from datetime import datetime, timezone

import pytest

from app.api import agents as agents_api
from app.models.agent import Agent
from app.models.user import User
from app.schemas.schemas import AgentCreate, AgentUpdate


# ── Test doubles ────────────────────────────────────────────────────────

class DummyResult:
    def __init__(self, values=None):
        self._values = list(values or [])

    def scalar_one_or_none(self):
        return self._values[0] if self._values else None

    def scalars(self):
        return self

    def all(self):
        return list(self._values)


def _populate_agent_server_defaults(agent: Agent) -> None:
    """Mimic Postgres server-side defaults on un-flushed Agent instances
    so `AgentOut.model_validate(agent)` downstream doesn't choke on None
    values for fields declared as `int` / `dict` / `datetime` in the
    Pydantic schema.
    """
    if agent.id is None:
        agent.id = uuid.uuid4()
    if agent.tokens_used_today is None:
        agent.tokens_used_today = 0
    if agent.tokens_used_month is None:
        agent.tokens_used_month = 0
    if agent.tokens_used_total is None:
        agent.tokens_used_total = 0
    if agent.llm_calls_today is None:
        agent.llm_calls_today = 0
    if agent.max_llm_calls_per_day is None:
        agent.max_llm_calls_per_day = 100
    if agent.max_triggers is None:
        agent.max_triggers = 20
    if agent.min_poll_interval_min is None:
        agent.min_poll_interval_min = 5
    if agent.webhook_rate_limit is None:
        agent.webhook_rate_limit = 5
    if agent.heartbeat_enabled is None:
        agent.heartbeat_enabled = True
    if agent.heartbeat_interval_minutes is None:
        agent.heartbeat_interval_minutes = 240
    if agent.heartbeat_active_hours is None:
        agent.heartbeat_active_hours = "09:00-18:00"
    if agent.max_tool_rounds is None:
        agent.max_tool_rounds = 50
    if agent.context_window_size is None:
        agent.context_window_size = 100
    if agent.is_expired is None:
        agent.is_expired = False
    if agent.autonomy_policy is None:
        agent.autonomy_policy = {}
    if agent.created_at is None:
        agent.created_at = datetime.now(timezone.utc)
    if agent.bridge_mode is None:
        agent.bridge_mode = "disabled"
    if agent.status is None:
        agent.status = "idle"


class RecordingDB:
    def __init__(self, responses=None):
        self.responses = list(responses or [])
        self.added: list[object] = []
        self.committed = False
        self.flush_count = 0

    async def execute(self, statement, params=None):
        if self.responses:
            return self.responses.pop(0)
        return DummyResult()

    def add(self, obj):
        self.added.append(obj)
        if isinstance(obj, Agent):
            _populate_agent_server_defaults(obj)

    async def flush(self):
        self.flush_count += 1

    async def commit(self):
        self.committed = True


def make_user(**overrides):
    # Note: username/email/password_hash on User are association_proxy
    # fields that delegate to Identity — setting them on a fresh User
    # without a bound Identity raises. The tests below don't read those
    # fields, so we just leave them off.
    values = {
        "id": uuid.uuid4(),
        "display_name": "Alice",
        "role": "member",
        "tenant_id": None,
        "is_active": True,
        "quota_agent_ttl_hours": 48,
    }
    values.update(overrides)
    return User(**values)


def make_agent(creator_id: uuid.UUID, **overrides):
    values = {
        "id": uuid.uuid4(),
        "name": "Ops Bot",
        "role_description": "assistant",
        "creator_id": creator_id,
        "status": "idle",
        "agent_type": "native",
    }
    values.update(overrides)
    agent = Agent(**values)
    _populate_agent_server_defaults(agent)
    return agent


# ── GET /agents/{id}/bridge-status ──────────────────────────────────────


@pytest.mark.asyncio
async def test_bridge_status_not_applicable_for_native_agent(monkeypatch):
    user = make_user()
    agent = make_agent(user.id, agent_type="native")

    async def fake_check(_db, _user, _aid):
        return agent, "use"

    monkeypatch.setattr(agents_api, "check_agent_access", fake_check)

    result = await agents_api.get_bridge_status(
        agent_id=agent.id,
        current_user=user,
        db=RecordingDB(),
    )
    assert result == {"connected": False, "applicable": False}


@pytest.mark.asyncio
async def test_bridge_status_disconnected_for_openclaw_without_bridge(monkeypatch):
    user = make_user()
    agent = make_agent(user.id, agent_type="openclaw", bridge_adapter="claude_code")

    async def fake_check(_db, _user, _aid):
        return agent, "use"

    monkeypatch.setattr(agents_api, "check_agent_access", fake_check)

    from app.services.local_agent import session_dispatcher as sd

    monkeypatch.setattr(sd.dispatcher, "get_bridge_info", lambda _aid: None)

    result = await agents_api.get_bridge_status(
        agent_id=agent.id,
        current_user=user,
        db=RecordingDB(),
    )
    assert result == {"connected": False, "applicable": True}


@pytest.mark.asyncio
async def test_bridge_status_connected_returns_full_shape(monkeypatch):
    user = make_user()
    agent = make_agent(user.id, agent_type="openclaw", bridge_adapter="hermes")

    async def fake_check(_db, _user, _aid):
        return agent, "use"

    monkeypatch.setattr(agents_api, "check_agent_access", fake_check)

    info = {
        "bridge_version": "0.2.1",
        "adapters": ["claude_code", "hermes"],
        "connected_at": "2026-04-22T12:00:00Z",
        "active_sessions": ["s1", "s2"],
    }
    from app.services.local_agent import session_dispatcher as sd

    monkeypatch.setattr(sd.dispatcher, "get_bridge_info", lambda _aid: info)

    result = await agents_api.get_bridge_status(
        agent_id=agent.id,
        current_user=user,
        db=RecordingDB(),
    )
    assert result["connected"] is True
    assert result["applicable"] is True
    assert result["bridge_version"] == "0.2.1"
    assert result["adapters"] == ["claude_code", "hermes"]
    assert result["connected_at"] == "2026-04-22T12:00:00Z"
    # Adapter returns the *length* of active_sessions, not the list itself,
    # so the UI can render "3 active sessions" without a second round-trip.
    assert result["active_sessions"] == 2


@pytest.mark.asyncio
async def test_bridge_status_handles_missing_optional_fields(monkeypatch):
    # Bridges that registered without advertising adapters/version should
    # still produce a well-formed response (empty list, None version) so
    # the frontend doesn't need to defensively null-check each field.
    user = make_user()
    agent = make_agent(user.id, agent_type="openclaw")

    async def fake_check(_db, _user, _aid):
        return agent, "use"

    monkeypatch.setattr(agents_api, "check_agent_access", fake_check)

    from app.services.local_agent import session_dispatcher as sd

    monkeypatch.setattr(sd.dispatcher, "get_bridge_info", lambda _aid: {})

    result = await agents_api.get_bridge_status(
        agent_id=agent.id,
        current_user=user,
        db=RecordingDB(),
    )
    assert result["connected"] is True
    assert result["adapters"] == []
    assert result["bridge_version"] is None
    assert result["active_sessions"] == 0


# ── PATCH /agents/{id} — bridge_adapter guard ──────────────────────────


@pytest.mark.asyncio
async def test_update_agent_drops_bridge_adapter_for_native(monkeypatch):
    user = make_user()
    agent = make_agent(user.id, agent_type="native", bridge_adapter=None)

    async def fake_check(_db, _user, _aid):
        return agent, "manage"

    monkeypatch.setattr(agents_api, "check_agent_access", fake_check)
    monkeypatch.setattr(agents_api, "is_agent_creator", lambda _u, _a: True)

    await agents_api.update_agent(
        agent_id=agent.id,
        data=AgentUpdate(bridge_adapter="hermes"),
        current_user=user,
        db=RecordingDB(),
    )
    # Native agent: the field must not have been written. A silent drop
    # is the contract — generic bulk update shouldn't need to know the
    # agent type.
    assert agent.bridge_adapter is None


@pytest.mark.asyncio
async def test_update_agent_applies_bridge_adapter_for_openclaw(monkeypatch):
    user = make_user()
    agent = make_agent(user.id, agent_type="openclaw", bridge_adapter="claude_code")

    async def fake_check(_db, _user, _aid):
        return agent, "manage"

    monkeypatch.setattr(agents_api, "check_agent_access", fake_check)
    monkeypatch.setattr(agents_api, "is_agent_creator", lambda _u, _a: True)

    await agents_api.update_agent(
        agent_id=agent.id,
        data=AgentUpdate(bridge_adapter="hermes"),
        current_user=user,
        db=RecordingDB(),
    )
    assert agent.bridge_adapter == "hermes"


@pytest.mark.asyncio
async def test_update_agent_leaves_bridge_adapter_unchanged_when_absent(monkeypatch):
    # exclude_unset semantics: a PATCH that doesn't mention bridge_adapter
    # must not touch the existing value, regardless of agent type.
    user = make_user()
    agent = make_agent(user.id, agent_type="openclaw", bridge_adapter="hermes")

    async def fake_check(_db, _user, _aid):
        return agent, "manage"

    monkeypatch.setattr(agents_api, "check_agent_access", fake_check)
    monkeypatch.setattr(agents_api, "is_agent_creator", lambda _u, _a: True)

    await agents_api.update_agent(
        agent_id=agent.id,
        data=AgentUpdate(bio="new bio"),
        current_user=user,
        db=RecordingDB(),
    )
    assert agent.bridge_adapter == "hermes"
    assert agent.bio == "new bio"


# ── POST /agents — bridge_adapter defaults ─────────────────────────────


@pytest.fixture
def _stub_quota(monkeypatch):
    from app.services import quota_guard

    async def fake_check(_user_id):
        return None

    monkeypatch.setattr(quota_guard, "check_agent_creation_quota", fake_check)


@pytest.mark.asyncio
async def test_create_openclaw_defaults_bridge_adapter_to_claude_code(_stub_quota):
    user = make_user()
    db = RecordingDB()
    data = AgentCreate(
        name="TestOpenClaw",
        role_description="",
        agent_type="openclaw",
        # bridge_adapter intentionally omitted
    )

    await agents_api.create_agent(data=data, current_user=user, db=db)

    added_agents = [obj for obj in db.added if isinstance(obj, Agent)]
    assert len(added_agents) == 1
    assert added_agents[0].agent_type == "openclaw"
    assert added_agents[0].bridge_adapter == "claude_code"


@pytest.mark.asyncio
async def test_create_openclaw_respects_explicit_bridge_adapter(_stub_quota):
    user = make_user()
    db = RecordingDB()
    data = AgentCreate(
        name="TestHermes",
        role_description="",
        agent_type="openclaw",
        bridge_adapter="hermes",
    )

    await agents_api.create_agent(data=data, current_user=user, db=db)

    added_agents = [obj for obj in db.added if isinstance(obj, Agent)]
    assert added_agents[0].bridge_adapter == "hermes"


@pytest.mark.asyncio
async def test_create_native_leaves_bridge_adapter_null(_stub_quota, monkeypatch):
    # Even if a misbehaving client sends bridge_adapter with a native
    # agent, the backend must refuse to persist it — the field is only
    # meaningful for bridge-style agents.
    user = make_user()
    db = RecordingDB()

    # Native path touches agent_manager; stub it so we don't hit the FS
    # or spawn containers.
    from app.services import agent_manager as am_module

    async def _noop(*_args, **_kwargs):
        return None

    monkeypatch.setattr(am_module.agent_manager, "initialize_agent_files", _noop)
    monkeypatch.setattr(am_module.agent_manager, "start_container", _noop)

    data = AgentCreate(
        name="TestNative",
        role_description="",
        agent_type="native",
        bridge_adapter="hermes",  # client mistake
    )

    await agents_api.create_agent(data=data, current_user=user, db=db)

    added_agents = [obj for obj in db.added if isinstance(obj, Agent)]
    assert added_agents[0].agent_type == "native"
    assert added_agents[0].bridge_adapter is None
