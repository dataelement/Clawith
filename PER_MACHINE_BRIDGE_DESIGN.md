# Per-machine bridge (multi-agent multiplexing)

Status: draft — design only, no implementation in this PR.
Author: zhongyua
Date: 2026-04-22

## 1. Motivation and current constraints

### Symptom

A user running three OpenClaw agents on the same laptop must install three
independent bridges — one per agent — and keep three `.clawith-bridge.toml`
configs in sync. Each bridge is a separate background process (Scheduled
Task / launchd agent / systemd unit), holding a separate WebSocket, baking
in a separate API key, yet all three of them shell out to the same
`claude` CLI on the same filesystem.

The local compute is a single physical resource; the agent-level binding
is a logical one. Collapsing one onto the other forced the user to manage
a fanout that only exists in the data model.

### Code anchors (as of this PR)

- `bridge/clawith_bridge/config.py:42-63` — `BridgeConfig` has a single
  `server` and a single `token`. No shape in the client for multi-agent
  multiplexing.
- `backend/app/api/gateway.py:34-58` — `_get_agent_by_key()` resolves
  `token → single agent` via `agents.api_key` / `agents.api_key_hash`.
  One token authenticates one agent.
- `backend/app/api/bridge_ws.py` — the `/ws/bridge?token=X` handler
  registers the socket against the agent the token resolves to.
- `backend/app/services/local_agent/session_dispatcher.py` — session
  routing is keyed on `agent_id`, looking up the one bridge that agent
  registered. No concept of "which of my bridges should handle this?"

### Why this isn't just a polish

Phase 1 (the install/rotate decouple that accompanies this doc) fixes
the *download* side: the user can redownload an installer without
rotating the key or kicking the bridge offline. But it doesn't fix the
*installation fanout*: the user still installs N bridges for N agents.
Phase 1 is the minimum that makes per-agent bridge tolerable; this
design replaces it with per-machine bridge so the fanout goes away.

## 2. Data model

### New table: `bridges`

| column                 | type        | notes |
|------------------------|-------------|-------|
| `id`                   | uuid, pk    | stable bridge identity; stays the same across reinstalls on the same machine |
| `owner_user_id`        | uuid, fk    | the user who installed this bridge; ACL anchor |
| `tenant_id`            | uuid, fk, nullable | inherits from owner; materialized for query locality |
| `token`                | varchar(128)| plaintext, same lifecycle semantics as `agents.api_key` post-Phase-1 |
| `display_name`         | varchar(100)| user-editable ("Work laptop", "Home desktop") |
| `installed_at`         | timestamptz | first registration |
| `last_seen_at`         | timestamptz | updated on every ping / active session |
| `advertised_adapters`  | jsonb       | list of adapter names the bridge's TOML enables |
| `bridge_version`       | varchar(20) | for support/debugging |

### Agent table change

Add `bridge_id: uuid | null` to `agents`.

- `NULL` → legacy per-agent bridge (uses `agents.api_key`). Preserves
  backwards compatibility; existing rows are not migrated.
- non-null → agent delegates execution to the referenced bridge.
  `agents.api_key` / `agents.api_key_hash` are ignored for routing (but
  retained for one-time legacy bridges a user still has installed).

### Permissions

The MVP: a bridge serves agents where `agents.creator_id = bridges.owner_user_id`.
That's the straightforward 1-user-1-machine case.

Future extension: `agent_bridge_permissions(agent_id, bridge_id)` junction
for teams that want shared infra-bridges (e.g. a team's build machine
advertised as a shared Hermes runtime). Not in the first cut — opens
questions about billing/quota and trust boundaries that need product
input.

## 3. Protocol extension

### Bridge registration

`BridgeRegisterFrame` (today sent implicitly via the `?token=` query
string) becomes an explicit frame:

```json
{
  "type": "bridge.register",
  "bridge_id": "<uuid-or-null>",
  "bridge_version": "0.3.0",
  "advertised_adapters": ["claude_code", "openclaw"]
}
```

- `bridge_id` present → server looks up `bridges` table, treats it as
  a per-machine bridge.
- `bridge_id` absent (null) → legacy path: server resolves the token
  against `agents.api_key` as today.

### Session start routing

`SessionStartFrame.agent_id` is already in the protocol
(`bridge/clawith_bridge/protocol.py:95`). The new server logic:

```
given agent_id:
  agent = load(agent_id)
  if agent.bridge_id is not None:
    bridge = load(agent.bridge_id)
    route session to bridge.ws_connection
  else:
    # legacy 1:1
    route session to the one bridge that registered with agent's token
```

### Capability advertisement

`advertised_adapters` is authoritative for routing decisions. If an
agent has `bridge_adapter='hermes'` but its assigned bridge advertises
only `['claude_code']`, the server rejects the session up front instead
of shelling out to a runtime that isn't enabled. The existing live
mismatch UI (`AgentDetail.tsx:2834-2875`, `OpenClawSettings.tsx:800-845`)
already models this — we just extend it to read from the bridge the
agent is bound to rather than the single bridge the agent has today.

## 4. Auth

The current `gateway._get_agent_by_key` handles plaintext + hash fallback
for `agents.api_key`. We extend it:

```
authenticate(token):
  # new path first — per-machine bridges
  bridge = bridges.find(token=token)
  if bridge:
    return BridgeAuth(bridge_id=bridge.id, owner_user_id=bridge.owner_user_id)

  # legacy path — per-agent token
  agent = agents.find(api_key=token) or agents.find_by_hash(token)
  if agent:
    return AgentAuth(agent_id=agent.id)

  reject
```

A `BridgeAuth` lets the socket serve any agent whose `bridge_id` points
at this bridge (or, in the MVP scope, any agent owned by the bridge's
owner — while migration is in progress). An `AgentAuth` is the
legacy 1:1 binding.

Feature flag: `ENABLE_PER_MACHINE_BRIDGE` (default `false`). While off,
the per-machine auth branch short-circuits to "reject" so a
misconfigured environment can't accidentally surface the new path.

## 5. Session dispatcher

```
dispatch(agent_id, session_payload):
  agent = load(agent_id)
  candidate_bridge_ids = []

  if agent.bridge_id is not None:
    candidate_bridge_ids = [agent.bridge_id]
  else:
    # legacy: agent owns its bridge
    legacy = find_legacy_bridge_for(agent_id)
    if legacy:
      candidate_bridge_ids = [legacy.id]

  # future: fan-out across multiple bridges a single agent permits
  # (team share case) — not MVP

  pick = select(candidate_bridge_ids, strategy=LEAST_LOADED)
  if pick is None:
    return reject("no_bridge_online")
  forward(pick, session_payload)
```

Selection strategies worth discussing:
- `LEAST_LOADED` — track `active_sessions_count` per bridge in memory.
- `ROUND_ROBIN` — simpler, fair.
- `USER_PINNED` — user picks a default bridge per agent.

Default: `LEAST_LOADED`, with a per-agent override knob added if users
complain.

## 6. Installer flow

The UX flips from "agent-centric" to "machine-centric" at the entry
point:

### New: "Add a machine"

Top-level action in settings (parallel to "Add agent"):

1. User clicks "Add a machine" → server creates a `bridges` row with a
   fresh token, returns installer.
2. Installer bakes the bridge token (not an agent token). On first
   startup the bridge sends `bridge.register` with its `bridge_id`.
3. Server marks it `last_seen_at = now()`.

### New: "Use existing bridge" on agent create/edit

Agent detail page gets a bridge picker:
- Dropdown of the user's online bridges (`bridges` filtered by
  `owner_user_id`, `advertised_adapters` compatible with agent's
  `bridge_adapter`).
- Fallback option: "Create a per-agent bridge" (legacy path, preserves
  today's flow for users who want it).

### Legacy download still works

The per-agent "Download installer" button stays wired for now — it
writes `bridge_id = NULL` on the agent and bakes the agent's
`api_key`. Users don't have to migrate. We can quietly deprecate the
button once adoption tips.

## 7. Legacy migration

### Do not migrate existing data

- Existing agents keep `bridge_id = NULL` and their `api_key_hash` /
  `api_key`. They work exactly as they do today.
- Existing bridges keep their per-agent tokens; no hotswap required.

### Opt-in path for new work

- Agent creation defaults to "create a new bridge" if the user has
  none, or "attach to existing bridge" dropdown if they have any
  online.
- A one-click "migrate this agent to my bridge" button on agent detail
  moves it from legacy to per-machine: writes `bridge_id`, nulls out
  `agents.api_key` (since it's no longer the auth path), updates
  local-bridge config if needed.

### Sunset plan

Track `count(agents where bridge_id IS NOT NULL) / count(agents)` as an
adoption metric. Once > 80%, we flip the agent-create UX to hide the
legacy path by default (still accessible via "advanced"). No hard cut
until we're close to 100% with a clear communication window.

## 8. Risk and rollout

### Feature-flag gating

`ENABLE_PER_MACHINE_BRIDGE` controls:
- Whether the `bridges` auth path is live in `gateway._get_agent_by_key`.
- Whether `bridge_id` column is read by the session dispatcher.
- Whether the "Add a machine" UI surfaces at all.

With the flag off, the entire feature is inert: the `bridges` table can
exist and even have rows, but nothing authenticates against it and
nothing routes against it. That makes rollback trivial — just flip the
flag.

### Necessary preconditions to flipping on

- Protocol backward-compat test matrix: old bridge client + new server,
  new bridge client + old server.
- Load test with N agents fan-in to 1 bridge (serial vs parallel
  sessions, concurrency bounds respected).
- Windows / Linux installer parity on the new "Add a machine" flow.
- Monitoring on `bridges.last_seen_at` staleness and auth-rejection
  rates to detect regressions.

### Token leakage blast radius

A leaked per-machine token grants the holder the ability to serve
*all* the owner's agents. That's strictly larger than today's leaked
per-agent token. Mitigations:

- Rotation endpoint: `POST /bridges/{id}/rotate-token` (mirrors
  `/agents/{id}/api-key`). Breaks all agents using that bridge until
  the user reinstalls.
- On suspicious activity (token used from unexpected IP, many new
  registrations in short window), notify the owner and allow one-click
  rotate.
- Bridge token is only in the installer payload, which stays on the
  user's machine. Don't print it in logs.

## 9. Open questions

These are left for the implementer to resolve with product input, not
decisions I'm making in this doc.

1. **Offline bridge behavior.** When a bridge the agent is bound to is
   offline, should sessions (a) fail immediately, (b) queue and drain
   when it reconnects, or (c) fail over to another of the user's
   online bridges that advertises the same adapter? Today we have (a)
   implicitly. (c) is powerful but introduces non-determinism users
   may not want.
2. **Multi-bridge active-active per agent.** If we eventually allow one
   agent → many bridges (cross-machine redundancy), what's the
   selection policy? Least-loaded is a sensible default but a
   sophisticated user might want "prefer this machine, fall back to
   the other."
3. **Shared team bridges.** Is there a use case for a bridge owned by
   user A serving agents owned by user B (within the same tenant)?
   If yes, `agent_bridge_permissions` becomes MVP-scope, not future.
   If no, the simple `owner_user_id` check is enough.
4. **Billing / quota.** Does the "compute" resource become the bridge
   instead of the agent? If a single bridge serves ten agents, whose
   quota deducts on a session? Per-agent stays simplest.
5. **Deprecation cadence for legacy per-agent tokens.** Communication
   plan, forced-migration deadline (if any), behavior when both
   `agents.api_key` and `agents.bridge_id` are set (prefer bridge).
6. **Bridge UI placement.** Is bridges a top-level nav item, buried
   under settings, or a separate "Machines" page? Discoverability of
   the "Add a machine" flow will set adoption speed.
