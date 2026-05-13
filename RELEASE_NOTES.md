# v1.9.2 — Workspace Governance, Tool UX & Token Cache Accounting

## What's New

### Enterprise Info & Workspace Governance
- **Shared `enterprise_info/` workspace area** now appears as tenant-level company context for agents and users.
- **Agent-side enterprise info is read-only**: agents can list and read company context, but cannot create, edit, or delete shared enterprise files.
- **Admin-managed enterprise knowledge base**: platform and org admins can update enterprise info while regular users and agents are protected from accidental modification.
- **Legacy task files no longer appear in new agent workspaces**: new agents no longer receive `todo.json` / `tasks.json`; existing `tasks.json` files remain supported as legacy snapshots.
- **Workspace file handling polish** improves preview/download behavior for shared enterprise files and preserves read-only boundaries.

### Agent Management & Permissions
- **Company admins can manage company-visible agents** even when those agents were created by regular users.
- **Private user-only agents remain private** to their creator.
- Agent permission APIs now return effective management capability, so the UI can distinguish creator ownership from admin management rights.
- Start, stop, and permission update actions now use effective manager permission instead of creator-only checks.

### Tool Management Experience
- **Agent and company tool lists now share a cleaner grouped UI** with category headers, search, status filters, counts, and bulk toggles.
- Tool categories are easier to scan and can be expanded only when needed, reducing very long tool-list pages.
- Per-tool emoji icons were removed from the main list in favor of calmer category icons and compact labels.
- **`Update Objective` is now a global default tool**, so newly created employees have the OKR objective update capability enabled by default.
- Tool loading now avoids exposing disabled or agent-only tools to the LLM fallback path.

### Chat & Agent UX
- **New and existing chat sessions focus the composer automatically**, so users can type immediately after opening a session.
- **Existing sessions open at the latest message** more reliably.
- **Expanded tool chains now keep following the bottom only while appropriate**: if the user scrolls up intentionally, new tool updates no longer force the viewport back down.
- Duplicate assistant avatars after a tool-chain block were removed for a cleaner transcript.
- Tool-chain copy was refined from "Ran X agents" to clearer activity language.
- Agent expiry quick-renew buttons now show selected state.
- The dashboard's secondary "New Digital Employee" button was removed; creation remains available from the sidebar entry point.

### Token Accounting & Cache Visibility
- Token usage tracking now records input, output, estimated, cache-read, and cache-creation token counters.
- Agent stats expose cache hit information for providers that return cache usage.
- Qwen / Alibaba Bailian compatible calls now support provider-specific prompt cache control while preserving stable prompt prefixes.
- Daily and monthly token reset logic now resets cache counters alongside total token counters.

### Prompting, Webpage Generation & Tool Reliability
- Default webpage/rich-document style guidance moved into the system prompt, reducing repeated tool-description text while keeping generated pages visually consistent.
- Agent-facing reply guidelines now discourage emoji-heavy normal replies.
- Web search instructions now refer to currently enabled tools instead of hardcoding unavailable tool names.
- Tool-call execution now blocks disabled tool names and asks the model to retry malformed JSON tool arguments cleanly.
- HTML-to-PDF and HTML-to-PPT conversion descriptions and parameters were expanded for higher-fidelity Chrome-based rendering.
- Restart script now starts backend and frontend as detached daemons, avoiding local dev servers exiting after the restart command completes.

## Upgrade Guide

> **Database migration required.** Run `alembic upgrade heads` before restarting application services.

This release adds or updates schema/data defaults for:
- agent cache token counters
- daily token usage input/output/cache/estimated counters
- default agent TTL changing to permanent (`0`)
- default daily LLM call limit changing to `1000`

### Docker Deployment

```bash
git pull origin main

# Run database migrations
docker exec clawith-backend-1 alembic upgrade heads

# Rebuild and restart services
docker compose down && docker compose up -d --build
```

### Source Deployment

```bash
git pull origin main

# Run database migrations
cd backend && alembic upgrade heads
cd ..

# Rebuild frontend
cd frontend && npm install && npm run build
cd ..

# Restart backend / frontend services
```

### Kubernetes / Helm

```bash
helm upgrade clawith helm/clawith/ -f values.yaml
# Run migration job / command: alembic upgrade heads
```

### Notes
- `enterprise_info/` is now shared tenant context. Review who has platform or org admin roles, because only admins should update those shared files.
- New agents are permanent by default. If your deployment requires expiring agents, set tenant/user TTL defaults explicitly after migration.
- Token cache counters depend on provider usage payloads. Providers that do not return cache fields will continue to show zero cache usage.
- Existing legacy `tasks.json` files are preserved, but new agents will not get `todo.json` or `tasks.json` automatically.
- If you run from source, use the updated `restart.sh` or your own process manager to keep frontend/backend processes detached.

---

# v1.9.1 — Talent Market, Per-User Onboarding & Template Automation

## What's New

### Talent Market & Agent Templates
- **Talent Market** added to the hiring flow, letting teams browse, compare, and hire curated agents directly from the product UI
- **Folder-based template loader** for agent templates, making template packaging and rollout more maintainable
- **19 new curated templates** across business, engineering, content, and trading scenarios, including:
  - backend architect, chief of staff, code reviewer, content creator, devops automator, frontend developer, growth hacker, rapid prototyper, SEO specialist, TikTok strategist, LinkedIn content creator
  - macro watcher, market intel aggregator, technical analyst, pre-market briefer, watchlist monitor, risk manager, trading journal coach, tilt-bias coach, COT report analyst, earnings/filings analyst
- **Trading-focused built-in skills** added for market data and financial calendar workflows
- **Post-hire settings** now supported, so newly hired agents can be configured immediately after creation

### Per-User Onboarding & Default Model Experience
- **Per-(user, agent) onboarding** introduced, so onboarding runs once per user-agent relationship instead of once per agent globally
- **Two-turn onboarding ritual** added for newly hired or newly contacted agents: a focused introduction followed by an immediate deliverable
- **Onboarding backfill logic** prevents historical agent-user pairs from being re-onboarded after upgrade
- **Tenant default LLM model** support added, including backend APIs and frontend selection flows
- **Model switcher UI** added and refined to better reflect tenant and agent defaults during chat

### Template Automation & MCP Provisioning
- **Template-defined default MCP servers** can now auto-install when an agent is created
- **Template default skills merging** improved so agent creation preserves template-defined skills alongside platform defaults
- **Template bootstrap metadata** added, including capability bullets and bootstrap content for richer cards and onboarding prompts

### Chat, Workspace & UX Improvements
- **Workspace switcher** added to agent chat and detail flows for faster context switching
- **Clawith-styled modal and toast system** replaces native browser dialogs in key frontend flows
- **Agent chat and workspace interactions** polished for smoother file and panel operations
- **Agent creation flow** improved with better structure and clearer template-driven setup
- **Company logo settings** added to the admin/company experience
- **Company region picker** added to enterprise settings
- **Agent detail, layout, enterprise settings, and admin company pages** received usability and visual refinements

### Localization & Marketplace Readiness
- **Locale-aware greeting behavior** added for hired agents
- **Chinese translations and template localization** expanded across Talent Market and onboarding experiences
- **Hardcoded English copy** removed from key hire/onboarding paths to improve multilingual consistency

### Platform & Integration Enhancements
- **WeChat channel support** completed in the mainline release path
- **Webpage tools** enhanced for richer browsing and page interaction workflows
- **Smithery/MCP tool discovery and invocation** made more resilient with live schema override behavior and improved request headers

### Optimizations & Fixes
- **Onboarding performance optimization**: the greeting turn now skips the full tool list, significantly reducing prompt size on first contact
- **Onboarding stability fixes**: prevents ritual leakage into later sessions and avoids duplicate/late onboarding triggers
- **Model picker fixes**: better default syncing, improved dropdown positioning, and clipping fixes
- **Channel user identity reuse and outbound routing** fixed for more reliable cross-channel delivery
- **Agent creation fixes**: template skills and auto-installed MCP tools now attach more consistently
- **Migration graph fixes**: release migrations were stabilized and merged to avoid broken multi-head upgrade paths
- **UI polish fixes** across chat panels, dialogs, agent cards, and company branding

---

## v1.9.1 — Upgrade Guide

> **Database migration required.** Run `alembic upgrade heads` before restarting application services.

This release introduces new schema changes in the `v1.9.0..main` range, including:
- `tenants.default_model_id`
- `agent_user_onboardings`
- `agent_templates.capability_bullets`
- `agent_templates.bootstrap_content`
- `agent_templates.default_mcp_servers`
- release-head merge migration cleanup

### Docker Deployment (Recommended)

```bash
git pull origin main

# Run database migrations
docker exec clawith-backend-1 alembic upgrade heads

# Rebuild and restart services
docker compose down && docker compose up -d --build
```

### Source Deployment

```bash
git pull origin main

# Run database migrations
cd backend && alembic upgrade heads
cd ..

# Rebuild frontend
cd frontend && npm install && npm run build
cd ..

# Restart backend / frontend services
```

### Kubernetes (Helm)

```bash
helm upgrade clawith helm/clawith/ -f values.yaml
# Run migration job / command: alembic upgrade heads
```

### Notes
- Existing user-agent pairs are automatically backfilled into `agent_user_onboardings`, so established conversations should not be re-onboarded after upgrade.
- If your deployment provisions agents from templates, review any template metadata that now uses `bootstrap_content`, `capability_bullets`, or `default_mcp_servers`.
- If you rely on tenant-scoped model management, validate the new default model selection in Company / Enterprise settings after migration.
- New template-driven MCP auto-install flows require a valid Smithery/system MCP configuration in environments that use those templates.

# v1.8.3-beta.2 — A2A Async Communication, Image Context & Search Tools

## What's New

### Agent-to-Agent (A2A) Async Communication — Beta
- **Three communication modes** for `send_message_to_agent`:
  - `notify` — fire-and-forget, one-way announcement
  - `task_delegate` — delegate work and get results back asynchronously via `on_message` trigger
  - `consult` — synchronous question-reply (original behaviour)
- **Feature flag**: controlled at the tenant level via Company Settings → Company Info → A2A Async toggle (default: **OFF**)
- When disabled, the `msg_type` parameter is **hidden from the LLM** so agents only see synchronous consult mode
- Security: chain depth protection (max 3 hops), regex filtering of internal terms, SQL injection prevention
- Performance: async wake sessions use the agent's own `max_tool_rounds` setting (default 50)

### Multimodal Image Context
- Base64 image markers are now persisted to the database at write time
- Chat UI correctly strips `[image_data:]` markers and renders thumbnails
- Fixed chat page vertical scrolling (flexbox `min-height: 0` constraint)
- Removed deprecated `/agents/:id/chat` route

### Search Engine Tools
- New `Exa Search` tool — AI-powered semantic search with category filtering
- New standalone search engine tools: DuckDuckGo, Tavily, Google, Bing (each as own tool)

### UI Improvements
- Drag-and-drop file upload across the application
- Chat sidebar polish: segment control, session items styling
- Agent-to-agent sessions now visible in the admin "Other Users" tab

### Bug Fixes
- DingTalk org sync rate limiting to prevent API throttling
- Tool seeder: `parameters_schema` now correctly included in new tool INSERT
- Unified `msg_type` enum references across codebase
- Docker access port corrected to 3008

---

## v1.8.3-beta.2 — Bug Fixes

### A2A Chat History Fixes
- **A2A session now shows both sides of the conversation**: when a target agent is woken via `notify` or `task_delegate`, its reply is now mirrored into the shared A2A chat session so the full conversation is visible in the admin **Other Users** tab
- **Removed hardcoded 2-round tool call limit** for A2A wake invocations: agents were hitting the limit before completing basic tasks; they now use their own configurable `max_tool_rounds` setting (default 50)
- **Fixed message loading order**: sessions with many messages (e.g. long-running A2A threads) were only showing the oldest 500 messages; now correctly loads the most recent 500

## Upgrade Guide

> **Database migration required.** Run `alembic upgrade heads` to add the `a2a_async_enabled` column.

### Docker Deployment (Recommended)

```bash
git pull origin main

# Run database migration
docker exec clawith-backend-1 alembic upgrade heads

# Rebuild and restart
docker compose down && docker compose up -d --build
```

### Source Deployment

```bash
git pull origin main

# Run database migration
alembic upgrade heads

# Rebuild frontend
cd frontend && npm install && npm run build
cd ..

# Restart services
```

### Kubernetes (Helm)

```bash
helm upgrade clawith helm/clawith/ -f values.yaml
# Run migration job for a2a_async_enabled column
```

### Notes
- The A2A Async feature is **disabled by default**. No behaviour changes until explicitly enabled.
- The `a2a_async_enabled` column defaults to `FALSE`, so existing tenants are unaffected.
