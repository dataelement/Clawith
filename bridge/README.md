# clawith-bridge

Reverse-WS local agent session bridge for Clawith. The bridge runs on the
operator's workstation, dials into the Clawith server, and drives local CLIs
(Claude Code) or local daemons (Hermes, OpenClaw) on behalf of the server.

## Install

```bash
pip install .
```

Requires Python 3.10+.

## Configure

Copy `clawith-bridge.toml.example` to `~/.clawith-bridge.toml` and edit:

```toml
server = "wss://clawith.example.com"
token  = "oc-your-agent-api-key"

[claude_code]
enabled    = true
executable = "claude"

[hermes]
enabled  = false
base_url = "http://127.0.0.1:7890"

[openclaw]
enabled  = false
base_url = "http://127.0.0.1:9000"
```

Or pass flags on the command line:

```bash
clawith-bridge --server wss://clawith --token oc-xxx
```

Env vars: `CLAWITH_BRIDGE_SERVER`, `CLAWITH_BRIDGE_TOKEN`,
`CLAWITH_BRIDGE_ADAPTER_CLAUDE_CODE=1`.

**API keys for the agents themselves** (`ANTHROPIC_API_KEY`, Hermes tokens,
etc.) are read from the local environment by the spawned CLIs / daemons.
Clawith never sees them.

## Run

```bash
clawith-bridge
```

You'll see log lines like:

```
server hello: v=1 ...
registered: adapters=['claude_code']
```

At this point the agent is online on the Clawith side (visible in
`/api/admin/bridge/status`). Incoming chat messages from Clawith spawn Claude
Code on your machine and stream the session back.

## Windows (NSSM)

The bridge does not implement its own `daemon start|stop|status` subcommand
on Windows — use NSSM to install it as a service:

```powershell
# Download nssm from https://nssm.cc
nssm install ClawithBridge "C:\Path\To\python.exe" "-m" "clawith_bridge"
nssm set ClawithBridge AppDirectory "C:\Path\To\workspace"
nssm set ClawithBridge AppEnvironmentExtra "CLAWITH_BRIDGE_SERVER=wss://clawith" "CLAWITH_BRIDGE_TOKEN=oc-xxx"
```

**Critical**: run the service under your **user account**, not LocalSystem.
NSSM → Log on tab → "This account" + `.\username` + password. Reason:

- `claude` CLI needs your `~/.claude/` credentials, which are not accessible
  from `C:\Windows\System32\config\systemprofile` (LocalSystem's `~`)
- `claude` needs to resolve via your user PATH — LocalSystem's PATH typically
  doesn't contain npm's global bin directory

See `~/.claude/settings.json` and the cc-connect project for more context on
this Windows constraint — it's not Clawith-specific.

## Docker (optional)

```bash
docker build -t clawith-bridge .
docker run --rm \
    -e CLAWITH_BRIDGE_SERVER=wss://clawith \
    -e CLAWITH_BRIDGE_TOKEN=oc-xxx \
    -e ANTHROPIC_API_KEY=sk-ant-xxx \
    -v $HOME/workspace:/home/bridge/workspace \
    clawith-bridge
```

The image ships with Claude Code CLI preinstalled but not Hermes/OpenClaw
(you'd typically run those on the host and set `base_url` to reach them).

## Packaging the Windows `setup.exe` for Clawith's UI downloader

Clawith's OpenClaw agent settings page offers a one-click "Download Windows
installer" button that serves a self-configuring `clawith-bridge-setup.exe`.
That exe is the pristine PyInstaller binary with a per-agent config trailer
(JSON + 8-byte magic `CLWB!END`) appended at EOF. The server rebuilds the
trailer on each download; the pristine binary itself is identical for every
user and every agent.

To build and deploy the pristine exe so the downloader works:

```bash
# 1. Create a Windows-native Python build env (WSL/mingw won't produce a
#    native Windows PE). Run this on a Windows host.
cd bridge/
python -m venv .venv-build
.venv-build\Scripts\activate
pip install -e . pyinstaller

# 2. Build — produces dist/clawith-bridge.exe (~13–14 MB onefile)
pyinstaller clawith-bridge.spec --clean --noconfirm

# 3. Deploy into the backend's static dir so /api/agents/{id}/bridge-installer
#    can serve it.
cp dist/clawith-bridge.exe ../backend/app/static/bridge/
```

The built binary is **not tracked in git** (`.gitignore` excludes
`backend/app/static/bridge/clawith-bridge.exe`) — each operator rebuilds it
from source. When the file is missing, the download endpoint returns HTTP
503 with a message pointing here.

On macOS and Linux the downloader returns a bash script instead; no binary
packaging is needed on those platforms (the script pip-installs
`clawith-bridge` and registers launchd / systemd user services).

## Stub smoke test

`stub_bridge.py` is a standalone script that pretends to be a real bridge.
Use it to verify the server side (bridge_ws + session_dispatcher) is wired
up correctly before installing Claude Code CLI etc.

```bash
pip install websockets pydantic
python stub_bridge.py --server ws://127.0.0.1:8000 --token oc-your-agent-key
```

Then from the Clawith chat UI, send a message to that agent. The stub replies
with a fake streaming conversation ending in `session.done` with a mock
`diff_summary`.

## Adapters

| Adapter       | Shape       | Notes |
|---------------|-------------|-------|
| `claude_code` | Subprocess  | Spawns `claude --output-format=stream-json -p <prompt>`. Captures `assistant_text`, `tool_use`, `tool_result`, `thinking`. |
| `hermes`      | HTTP daemon | POST `/tasks`, SSE `/tasks/{id}/events`, DELETE `/tasks/{id}`. Subclass `HermesAdapter` if your local Hermes API differs. |
| `openclaw`    | HTTP daemon | POST `/v1/chat`, SSE `/v1/jobs/{id}/events`. Also supports inline responses (no job_id). |

All three are instantiated per-session, so adapter classes can keep state
on `self`. The session manager cancels their running task on `session.cancel`
or on disconnect.
