"""Pins the contract that rotating an agent's API key evicts any currently-
attached bridge.

The `/ws/bridge` endpoint authenticates only at the WebSocket upgrade — once
a bridge is connected, the read loop never re-checks the token. So rotating
`agents.api_key_hash` alone lets an old bridge keep running sessions on a
revoked credential until it disconnects on its own.

Codex P1 review flagged this. Fix:
  1. `SessionDispatcher.detach_bridge` grew `close_ws` + `reason` params.
  2. The rotate endpoint calls `detach_bridge(close_ws=True, reason="api_key_rotated")`
     after committing the new hash.

These tests pin both pieces:
  - Unit: dispatcher closes the WS with code 4001 + reason, drops the
    registry entry, and fails in-flight sessions.
  - Integration: rotate endpoint invokes detach_bridge with the right args.
"""
from __future__ import annotations

import asyncio
import hashlib
import uuid

import pytest

from app.api import agents as agents_api
from app.services.local_agent.session_dispatcher import (
    BridgeDisconnected,
    SessionDispatcher,
    _Bridge,
    _Session,
)

# Reuse the RecordingDB / make_user / make_agent helpers — same shape as in
# test_install_rotate_decoupling.py. Copied rather than shared to keep this
# file self-contained for future grep-archaeologists.
from tests.test_install_rotate_decoupling import make_agent, make_user, RecordingDB


# ── Unit: dispatcher.detach_bridge(close_ws=True) ──────────────────────


class _FakeWS:
    """Minimal WebSocket stand-in that records close() calls."""

    def __init__(self) -> None:
        self.close_calls: list[dict] = []

    async def close(self, code: int = 1000, reason: str = "") -> None:
        self.close_calls.append({"code": code, "reason": reason})


@pytest.mark.asyncio
async def test_detach_bridge_closes_ws_when_requested(monkeypatch):
    # log_activity hits the real DB in prod; stub it out so the dispatcher's
    # post-detach telemetry doesn't blow up the test.
    from app.services.local_agent import session_dispatcher as sd

    async def _noop_log(**_kwargs):
        return None

    monkeypatch.setattr(sd, "log_activity", _noop_log)

    dispatcher = SessionDispatcher()
    agent_id = str(uuid.uuid4())
    fake_ws = _FakeWS()

    # Register a bridge with one in-flight session so we can also verify
    # that abandoned sessions get failed (not silently dropped).
    loop = asyncio.get_event_loop()
    future: asyncio.Future = loop.create_future()
    events: asyncio.Queue = asyncio.Queue()
    from datetime import datetime, timezone
    session = _Session(
        session_id="s-1",
        agent_id=agent_id,
        adapter="openclaw",
        started_at=datetime.now(timezone.utc),
        future=future,
        events=events,
    )
    bridge = _Bridge(
        agent_id=agent_id,
        ws=fake_ws,  # type: ignore[arg-type]
        bridge_version="0.1.0",
        adapters=["openclaw"],
        capabilities={},
        connected_at=datetime.now(timezone.utc),
        sessions={"s-1": session},
    )
    dispatcher._bridges[agent_id] = bridge

    await dispatcher.detach_bridge(
        agent_id, close_ws=True, reason="api_key_rotated",
    )

    # Bridge is gone from the registry.
    assert agent_id not in dispatcher._bridges

    # WS was closed with the auth-failed code and a reason derived from the
    # caller's argument. 4001 matches what `/ws/bridge` uses at upgrade for
    # auth failure, so the bridge treats this as "go reauth" not "retry".
    assert len(fake_ws.close_calls) == 1
    assert fake_ws.close_calls[0]["code"] == 4001
    assert "api_key_rotated" in fake_ws.close_calls[0]["reason"]

    # In-flight session got failed (not silently abandoned).
    assert future.done()
    with pytest.raises(BridgeDisconnected):
        future.result()


@pytest.mark.asyncio
async def test_detach_bridge_default_does_not_close_ws(monkeypatch):
    # The existing call site (`/ws/bridge` read-loop finally) passes no
    # close_ws kwarg — the socket is already tearing down. Verify we don't
    # double-close, which would raise inside starlette.
    from app.services.local_agent import session_dispatcher as sd

    async def _noop_log(**_kwargs):
        return None

    monkeypatch.setattr(sd, "log_activity", _noop_log)

    dispatcher = SessionDispatcher()
    agent_id = str(uuid.uuid4())
    fake_ws = _FakeWS()

    from datetime import datetime, timezone
    bridge = _Bridge(
        agent_id=agent_id,
        ws=fake_ws,  # type: ignore[arg-type]
        bridge_version="0.1.0",
        adapters=["openclaw"],
        capabilities={},
        connected_at=datetime.now(timezone.utc),
    )
    dispatcher._bridges[agent_id] = bridge

    await dispatcher.detach_bridge(agent_id)

    assert agent_id not in dispatcher._bridges
    assert fake_ws.close_calls == []


@pytest.mark.asyncio
async def test_detach_bridge_noop_when_nothing_attached():
    # Rotation runs detach unconditionally — if the user rotates without a
    # bridge attached, we must not raise.
    dispatcher = SessionDispatcher()
    # Should not raise.
    await dispatcher.detach_bridge(
        str(uuid.uuid4()), close_ws=True, reason="api_key_rotated",
    )


# ── Integration: rotate endpoint → dispatcher.detach_bridge ──────────────


@pytest.mark.asyncio
async def test_rotate_endpoint_evicts_bridge(monkeypatch):
    # The key claim: rotating the API key kicks any attached bridge, not
    # just rewriting the DB. Without this, a bridge that stole the old key
    # keeps running sessions until it disconnects on its own.
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

    # Record detach_bridge calls on the real dispatcher singleton.
    from app.services.local_agent import session_dispatcher as sd

    calls: list[dict] = []

    async def fake_detach(agent_id, close_ws=False, reason=""):
        calls.append(
            {"agent_id": agent_id, "close_ws": close_ws, "reason": reason},
        )

    monkeypatch.setattr(sd.dispatcher, "detach_bridge", fake_detach)

    db = RecordingDB()
    result = await agents_api.generate_or_reset_api_key(
        agent_id=agent.id,
        current_user=user,
        db=db,
    )

    # Normal rotate postconditions still hold.
    assert result["api_key"].startswith("oc-")
    assert agent.api_key_hash != old_hash
    assert db.commits == 1

    # The new invariant: exactly one eviction targeted at this agent, with
    # the flag set so the socket actually gets closed.
    assert len(calls) == 1
    assert calls[0]["agent_id"] == str(agent.id)
    assert calls[0]["close_ws"] is True
    assert calls[0]["reason"] == "api_key_rotated"


@pytest.mark.asyncio
async def test_rotate_endpoint_still_succeeds_if_eviction_raises(monkeypatch):
    # Eviction is best-effort — a failure here must not rollback the rotate
    # or mask it from the operator. Pin the try/except around detach_bridge.
    user = make_user()
    agent = make_agent(
        user.id,
        agent_type="openclaw",
        api_key="oc-old",
        api_key_hash=hashlib.sha256(b"oc-old").hexdigest(),
    )

    async def fake_check(_db, _user, _aid):
        return agent, "manage"

    monkeypatch.setattr(agents_api, "check_agent_access", fake_check)
    monkeypatch.setattr(agents_api, "is_agent_creator", lambda _u, _a: True)

    from app.services.local_agent import session_dispatcher as sd

    async def boom(*_args, **_kwargs):
        raise RuntimeError("dispatcher on fire")

    monkeypatch.setattr(sd.dispatcher, "detach_bridge", boom)

    db = RecordingDB()
    result = await agents_api.generate_or_reset_api_key(
        agent_id=agent.id,
        current_user=user,
        db=db,
    )

    # Rotate completed despite the eviction failure.
    assert result["api_key"].startswith("oc-")
    assert db.commits == 1
