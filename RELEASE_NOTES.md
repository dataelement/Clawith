# v1.7.2

## Highlights

- **Discord Gateway (WebSocket)** — Connect Discord bots without a public IP. Configure via Channel Settings.
- **Clawith Pages** — Agents can publish static HTML pages with shareable `/p/{short_id}` URLs.
- **Unified Notification System** — Plaza replies, @mentions, broadcasts, and heartbeat-drain notifications with category filtering.
- **Baidu (Qianfan) LLM Provider** — Add Baidu models alongside OpenAI, Anthropic, and others.
- **LLM Temperature Control** — Set per-model temperature from the LLM management page.
- **OpenClaw Settings Page** — Dedicated API key management for OpenClaw integrations.
- **Platform Settings Restructure** — Companies page reorganized into a tabbed Platform Settings layout.
- **Runtime Version Display** — `/api/version` endpoint and sidebar footer showing the running version.

## Bug Fixes

- Fix Alembic migration cycle error during backend startup (resolved `multi_tenant_registration` loop)
- Fix missing relationship type dropdown when adding an Agent Relationship
- Align Agent-to-Agent relationship types with Human-to-Agent ones and complete missing i18n translations
- Fix missing database migration for `max_output_tokens` in `llm_models` table
- Fix default company heartbeat floor not being applied to newly created agents
- Fix heartbeat/scheduler tool calls failing with empty arguments (empty-args guard ported from chat flow)
- Fix agent-to-agent session duplication and LLM tool confusion
- Harden A2A communication security with tenant isolation and relationship checks
- Fix A2A LLM timeout retries with jitter and error surfacing
- Fix system message always placed first for cross-model compatibility
- Fix streaming state not reset when switching sessions
- Fix trigger resurrection on restart
- Fix non-standard API streaming with JSON buffer
- Fix plaza tenant scoping and @mention navigation
- Fix OpenClaw agent replies not appearing in chat UI
- Fix chat message alignment by participant
- Improve broadcast UI and @mention dropdown (scrollable, increased limit)

## Database Migrations

Four new Alembic migrations run automatically on startup:

1. `add_published_pages` — Creates `published_pages` table
2. `add_notification_agent_id` — Adds `agent_id`, `sender_name` columns to `notifications`; makes `user_id` nullable
3. `add_llm_temperature` — Adds `temperature` column to `llm_models`
4. `add_llm_max_output_tokens` — Adds `max_output_tokens` column to `llm_models`

All migrations are idempotent (safe to re-run).

## New Dependency

- `discord.py>=2.3.0` — Required for Discord Gateway mode

## Infrastructure

- All Docker services now have `restart: unless-stopped`
- `.dockerignore` updated to exclude `agent_data/` from build context
- `entrypoint.sh` removed legacy schedule-to-triggers migration (completed in v1.7.0)
- `restart.sh` supports external `DATABASE_URL`

## Upgrade Instructions

> **Important**: Users must upgrade one version at a time (e.g., v1.6.0 → v1.7.0 → v1.7.1 → v1.7.2). Skipping versions is not supported.

### Option A: Docker Deployment

```bash
cd /path/to/Clawith
git pull origin main
docker compose down
docker compose up -d --build
```

Migrations run automatically via `entrypoint.sh`.

> [!IMPORTANT]
> **`--build` is required for this release.** The following features depend on changes baked into the Docker image (Nginx config, Python dependencies) and will NOT work with hot-update (`docker cp`) alone:
> - **Clawith Pages** — requires the new `/p/` Nginx proxy rule
> - **Discord Gateway** — requires the new `discord.py` dependency
>
> If you previously upgraded via `docker cp` without `--build`, run `docker compose down && docker compose up -d --build` to apply these changes.

### Option B: Source Deployment

```bash
cd /path/to/Clawith
git pull origin main

# Install dependencies (run from the backend/ directory)
cd backend
pip install .

# Run migrations (must be run from backend/ directory where alembic.ini is located)
alembic upgrade head

# Restart backend
# (your restart method here)
```

No `.env` changes required.
