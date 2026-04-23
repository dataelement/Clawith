"""Tests for the install/rotate decoupling (agents.api_key dual storage).

Pre-change behavior: every installer download minted a fresh API key and
overwrote `agents.api_key_hash`, which dropped any currently-connected
bridge using the old key. That made "re-download for a different runtime"
a destructive operation — the user had to race to the machine to replace
the installer before losing access.

Post-change behavior: the agent's plaintext key is stored in
`agents.api_key`, and download reuses it. Explicit rotation is a separate
endpoint (POST /agents/{id}/api-key).

These tests pin the contract:

1. Download reuses the stored plaintext, does NOT rotate the hash.
2. Legacy agents (NULL api_key) get a plaintext minted + dual-written
   on their first download — a one-time opportunistic upgrade.
3. The explicit rotate endpoint dual-writes both columns.
4. Creating an openclaw agent dual-writes both columns up front.
"""

import hashlib
import uuid
from datetime import datetime, timezone

import pytest

from app.api import agents as agents_api
from app.models.agent import Agent
from app.models.user import User


# ── Test doubles (mirrored from test_bridge_adapter_endpoints.py) ───────


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
        self.commits = 0
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
        self.commits += 1

    @property
    def committed(self) -> bool:
        return self.commits > 0


def make_user(**overrides):
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
        "agent_type": "openclaw",
    }
    values.update(overrides)
    agent = Agent(**values)
    _populate_agent_server_defaults(agent)
    return agent


# ── download_bridge_installer — reuse path ──────────────────────────────


@pytest.fixture
def _download_env(monkeypatch):
    """Stub PUBLIC_BASE_URL, render_installer, and log_activity so the
    download endpoint runs end-to-end without touching the filesystem,
    network, or database activity log.
    """
    # PUBLIC_BASE_URL must be set or the endpoint 500s before doing anything.
    from app import config as app_config

    def fake_settings():
        class S:
            PUBLIC_BASE_URL = "https://clawith.example.com"
        return S()

    monkeypatch.setattr(app_config, "get_settings", fake_settings)

    # Capture what api_key render_installer sees — this is the whole point
    # of the test: the *baked* key should equal the stored plaintext,
    # not a freshly-minted one.
    captured: dict = {}

    def fake_render(*, platform, server_url, api_key, agent_name, adapter):
        captured["api_key"] = api_key
        captured["adapter"] = adapter
        return (b"payload", "installer.sh", "application/x-sh")

    from app.services.local_agent import installer_templates

    monkeypatch.setattr(installer_templates, "render_installer", fake_render)

    # log_activity imports are done inside the endpoint; patch via module.
    async def noop_log(**_kwargs):
        return None

    from app.services import activity_logger

    monkeypatch.setattr(activity_logger, "log_activity", noop_log)

    return captured


@pytest.mark.asyncio
async def test_download_reuses_stored_plaintext_without_rotating(
    monkeypatch, _download_env
):
    user = make_user()
    stored_plaintext = "oc-existing-plaintext-abc123"
    stored_hash = hashlib.sha256(stored_plaintext.encode()).hexdigest()
    agent = make_agent(
        user.id,
        agent_type="openclaw",
        api_key=stored_plaintext,
        api_key_hash=stored_hash,
        bridge_mode="enabled",
    )

    async def fake_check(_db, _user, _aid):
        return agent, "manage"

    monkeypatch.setattr(agents_api, "check_agent_access", fake_check)
    monkeypatch.setattr(agents_api, "is_agent_creator", lambda _u, _a: True)

    db = RecordingDB()
    await agents_api.download_bridge_installer(
        agent_id=agent.id,
        platform="linux",
        current_user=user,
        db=db,
    )

    # The installer must bake the *stored* key, not a freshly-minted one.
    assert _download_env["api_key"] == stored_plaintext
    # Neither column should have been touched.
    assert agent.api_key == stored_plaintext
    assert agent.api_key_hash == stored_hash
    # And no commit, since nothing changed.
    assert db.commits == 0


@pytest.mark.asyncio
async def test_download_fills_plaintext_for_legacy_agent(
    monkeypatch, _download_env
):
    # Legacy agent: predates the api_key column, only has api_key_hash.
    # First download opportunistically upgrades it by minting + dual-writing.
    user = make_user()
    legacy_hash = hashlib.sha256(b"legacy-unknown").hexdigest()
    agent = make_agent(
        user.id,
        agent_type="openclaw",
        api_key=None,
        api_key_hash=legacy_hash,
        bridge_mode="enabled",
    )

    async def fake_check(_db, _user, _aid):
        return agent, "manage"

    monkeypatch.setattr(agents_api, "check_agent_access", fake_check)
    monkeypatch.setattr(agents_api, "is_agent_creator", lambda _u, _a: True)

    db = RecordingDB()
    await agents_api.download_bridge_installer(
        agent_id=agent.id,
        platform="linux",
        current_user=user,
        db=db,
    )

    minted = _download_env["api_key"]
    assert minted.startswith("oc-")
    # Both columns must have been written together.
    assert agent.api_key == minted
    assert agent.api_key_hash == hashlib.sha256(minted.encode()).hexdigest()
    # Legacy hash is gone — this is the one-time upgrade; subsequent
    # downloads take the reuse path.
    assert agent.api_key_hash != legacy_hash
    assert db.commits == 1


@pytest.mark.asyncio
async def test_download_does_not_persist_when_render_fails(monkeypatch):
    # If render_installer raises (e.g. bundled exe missing), the existing
    # plaintext must not be touched and no commit may happen. This pins
    # the rollback-safety invariant from the prior bug fix.
    user = make_user()
    stored_plaintext = "oc-existing-plaintext"
    stored_hash = hashlib.sha256(stored_plaintext.encode()).hexdigest()
    agent = make_agent(
        user.id,
        agent_type="openclaw",
        api_key=stored_plaintext,
        api_key_hash=stored_hash,
    )

    async def fake_check(_db, _user, _aid):
        return agent, "manage"

    monkeypatch.setattr(agents_api, "check_agent_access", fake_check)
    monkeypatch.setattr(agents_api, "is_agent_creator", lambda _u, _a: True)

    from app import config as app_config

    def fake_settings():
        class S:
            PUBLIC_BASE_URL = "https://clawith.example.com"
        return S()

    monkeypatch.setattr(app_config, "get_settings", fake_settings)

    def boom(**_kwargs):
        raise FileNotFoundError("windows bundled exe missing")

    from app.services.local_agent import installer_templates

    monkeypatch.setattr(installer_templates, "render_installer", boom)

    from fastapi import HTTPException

    db = RecordingDB()
    with pytest.raises(HTTPException) as excinfo:
        await agents_api.download_bridge_installer(
            agent_id=agent.id,
            platform="windows",
            current_user=user,
            db=db,
        )
    assert excinfo.value.status_code == 503
    assert agent.api_key == stored_plaintext
    assert agent.api_key_hash == stored_hash
    assert db.commits == 0


# ── rotate — explicit dual-write ────────────────────────────────────────


@pytest.mark.asyncio
async def test_rotate_endpoint_writes_both_fields(monkeypatch):
    # The /agents/{id}/api-key endpoint is the *explicit* rotation path.
    # It must overwrite both columns together so the dual-path auth
    # (gateway._get_agent_by_key, plaintext first then hash fallback) stays
    # consistent across rotations.
    user = make_user()
    old_plaintext = "oc-old-abc"
    old_hash = hashlib.sha256(old_plaintext.encode()).hexdigest()
    agent = make_agent(
        user.id,
        agent_type="openclaw",
        api_key=old_plaintext,
        api_key_hash=old_hash,
    )

    async def fake_check(_db, _user, _aid):
        return agent, "manage"

    monkeypatch.setattr(agents_api, "check_agent_access", fake_check)
    monkeypatch.setattr(agents_api, "is_agent_creator", lambda _u, _a: True)

    db = RecordingDB()
    result = await agents_api.generate_or_reset_api_key(
        agent_id=agent.id,
        current_user=user,
        db=db,
    )

    new_plaintext = result["api_key"]
    assert new_plaintext.startswith("oc-")
    assert new_plaintext != old_plaintext
    assert agent.api_key == new_plaintext
    assert agent.api_key_hash == hashlib.sha256(new_plaintext.encode()).hexdigest()
    assert agent.api_key_hash != old_hash
    assert db.commits == 1


# ── create_agent — openclaw dual-write on creation ──────────────────────


@pytest.fixture
def _stub_quota(monkeypatch):
    from app.services import quota_guard

    async def fake_check(_user_id):
        return None

    monkeypatch.setattr(quota_guard, "check_agent_creation_quota", fake_check)


@pytest.mark.asyncio
async def test_create_openclaw_agent_stores_plaintext_key(_stub_quota):
    # Creating an openclaw agent must write both columns so that the
    # FIRST installer download can take the fast reuse path — without
    # this, every freshly-created agent would immediately hit the legacy
    # upgrade branch on first download.
    from app.schemas.schemas import AgentCreate

    user = make_user()
    db = RecordingDB()
    data = AgentCreate(
        name="FreshOpenClaw",
        role_description="",
        agent_type="openclaw",
    )

    result = await agents_api.create_agent(data=data, current_user=user, db=db)

    added_agents = [obj for obj in db.added if isinstance(obj, Agent)]
    assert len(added_agents) == 1
    created = added_agents[0]
    assert created.agent_type == "openclaw"
    # Both columns populated at creation time.
    assert created.api_key is not None
    assert created.api_key.startswith("oc-")
    assert created.api_key_hash == hashlib.sha256(
        created.api_key.encode()
    ).hexdigest()
    # The one-time plaintext returned to the caller matches what's stored.
    assert result["api_key"] == created.api_key
