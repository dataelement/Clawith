# v1.8.0-beta.3

## What's Changed

### New Features

- **Split Code Executor into Local and E2B Cloud tools** — The single "Code Executor" tool has been separated into two independent tools. The local tool shows CPU/memory/network config; the E2B Cloud tool only requires an API key. E2B errors are now surfaced explicitly instead of silently falling back to local execution.
- **MCP Server credential management** — New "Edit Server" UI and `PUT /tools/mcp-server` API endpoint for bulk-updating MCP server URLs and API keys across all tools sharing the same server.
- **Feishu Wiki document creation** — `feishu_doc_create` now supports creating documents directly inside Wiki knowledge bases, with automatic detection of Wiki node tokens.
- **Feishu permission JSON UI redesign** — Two-tier segmented control (Basic / Full) with i18n support for Feishu app permission configuration.
- **Live Preview auto-sizing** — AgentBay Live Preview panel now auto-sizes to 50% of the chat container width.

### Bug Fixes

- **Plaintext SMTP relay support** — STARTTLS is now auto-negotiated based on server ESMTP capabilities instead of being forced on port 25/587. AUTH is skipped for unauthenticated IP-whitelisted internal relays. Password is no longer a required field in email configuration.
- **Unified context window size** — Introduced `DEFAULT_CONTEXT_WINDOW_SIZE = 100` constant and unified all 9 communication channels (WebSocket, Feishu, Discord, WeCom, DingTalk, Teams, Slack) to use consistent fallback values.
- **LLM stream retry** — Added `httpx.RemoteProtocolError` to the stream retry logic to handle upstream connection resets.
- **Tool config double-encryption** — Fixed a bug where already-encrypted sensitive config fields were encrypted again on save.
- **Loguru format collision** — Replaced `logger.error(..., exc_info=True)` with `logger.exception(...)` across all channel handlers to prevent crashes when error messages contain special characters.
- **WeCom message handler** — Fixed `NameError` (`agent` vs `agent_obj`) and migrated user creation to `channel_user_service` to avoid AssociationProxy errors.
- **Duplicate tool definition** — Removed `send_channel_message` from `_ALWAYS_INCLUDE_CORE` to prevent "Tool names must be unique" LLM errors.
- **AgentBay connection test** — Fixed test image name (`linux_latest`) and `api_key` lookup in global tool config fallback.
- **FastAPI route ordering** — Reordered `/tools/mcp-server/bulk` before `/tools/{tool_id}` to prevent 422 validation errors on older FastAPI versions.
- **Other fixes** — LLM model temperature persistence, org_admin access to GitHub/ClawHub tokens, MCP tool import tenant scoping.

### UI / i18n

- **Context Window Size terminology** — Corrected misleading "Max Rounds" / "Context Rounds" labels to industry-standard "Context Window Size" with accurate descriptions.
- **MCP Server group header** — Displays hostname instead of full URL for cleaner display.

## Upgrade Notes

This is a **drop-in upgrade** from v1.8.0-beta.2. No breaking changes.

- **No database migrations required**
- **No new dependencies**
- **No environment variable changes**
- The new `execute_code_e2b` tool will be automatically created by the tool seeder on startup. It is **not** a default tool — agents will not have it unless explicitly added.
- The existing `execute_code` tool's config schema will be auto-synced (the sandbox type dropdown is removed since it's now always "subprocess").

### Docker Deployment
```bash
git pull origin main
docker compose down && docker compose up -d --build
```

### Source Deployment
```bash
git pull origin main
# Backend
pip install -r backend/requirements.txt  # no changes expected, but safe to run
# Frontend (pre-built dist.zip is included)
cd frontend && unzip -o dist.zip -d dist/
# Restart services
```
