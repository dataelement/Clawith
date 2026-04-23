# Clawith Architecture Specification

This document describes the current high-level architecture of Clawith based on the latest codebase. It is intended to help developers quickly identify the system's primary runtime paths, storage model, extension points, and frontend/backend boundaries.

---

## Module 1: System Overview

Clawith is a multi-tenant agent collaboration platform. The product is not just a chat UI: it combines native WebSocket-driven agents, autonomous trigger-based wakeups, external OpenClaw nodes, multi-channel IM ingress, workspace file operations, MCP-based tool import, enterprise directory sync, and a growing OKR subsystem.

### 1.1 Tech Stack

- **Backend**: Python 3.11+, FastAPI, SQLAlchemy 2 async ORM, PostgreSQL, httpx, Loguru.
- **Frontend**: React 19, Vite 6, TypeScript, React Router 7, Zustand, TanStack React Query, i18next, Recharts.
- **Realtime**: WebSocket chat streaming for native agents; additional long-lived background managers for Feishu, DingTalk, WeCom, and Discord.
- **Extension Surface**: Built-in tools, MCP tools, skill packages, AgentBay environments, public published pages, and OpenClaw gateway nodes.

### 1.2 Application Startup and Assembly

The backend entry point is `backend/app/main.py`.

On startup, the app currently does the following:

1. Configures logging and middleware.
2. Ensures database tables exist by importing all models and calling `Base.metadata.create_all()`.
3. Seeds default tenant data, builtin tools, templates, skills, default agents, and the OKR Agent.
4. Starts core background tasks:
   - `trigger_daemon`
   - `feishu_ws_manager`
   - `dingtalk_stream_manager`
   - `wecom_stream_manager`
   - `discord_gateway_manager`
5. Registers a broad route surface covering auth, agents, enterprise admin, tools, skills, notifications, pages, gateway, Aware triggers, chat sessions, AgentBay control, and OKR.

This means `main.py` is both a router composition root and an operational bootstrapper.

For OKR-specific startup patching, the bootstrap path now also self-heals missing builtin OKR tool rows before patching existing OKR Agents. This prevents prompt/tool-list mismatches where an OKR Agent mentions `upsert_member_daily_report` in context but does not actually receive the tool in its callable LLM tool set.

### 1.3 Directory Map

#### Backend (`backend/app/`)

- `api/`: FastAPI route layer.
  - `websocket.py`: native agent runtime entry for streaming chat and tool-calling.
  - `gateway.py`: OpenClaw edge-node poll/report/send channel.
  - `triggers.py` / `webhooks.py`: Aware trigger configuration and public event ingress.
  - `enterprise.py` / `admin.py`: tenant admin, SSO, model pool, org sync, platform settings.
  - `tools.py` / `skills.py`: tool registry and skill registry management.
  - `pages.py`: authenticated page publishing APIs plus public `/p/{short_id}` serving.
  - `agentbay_control.py`: human Take Control session APIs for AgentBay browser/computer environments.
- `models/`: SQLAlchemy ORM definitions.
- `services/`: runtime logic, prompt assembly, agent tooling, trigger daemon, MCP resource discovery, org sync, quota guard, OKR services, AgentBay clients, and workspace collaboration helpers.

#### Frontend (`frontend/src/`)

- `App.tsx`: route composition and auth bootstrap.
- `pages/AgentDetail.tsx`: primary agent work surface; chat, settings, sessions, tools, triggers, files, and realtime rendering all meet here.
- `pages/Dashboard.tsx`, `pages/Plaza.tsx`, `pages/Messages.tsx`, `pages/EnterpriseSettings.tsx`, `pages/OKR.tsx`: major product views.
- `services/api.ts`: HTTP client layer.
- `stores/`: Zustand auth and UI state.
- `index.css`: global theme, shared layout primitives, and key animations.

---

## Module 2: Core Data Model

The database model is intentionally broad because Clawith spans SaaS tenancy, agents, collaboration, extensibility, publishing, and enterprise admin.

### 2.1 Tenant, Identity, and Organization

Primary models:

- `Tenant`: company boundary, activation state, SSO-related flags, tenant-level defaults.
- `User` and `Identity`: human account and identity record pairing.
- `IdentityProvider` and `SSOScanSession`: tenant-bound or global authentication/SSO providers and temporary QR/scan login sessions.
- `OrgDepartment` and `OrgMember`: synced enterprise directory/cache for people and department lookup.
- `TenantSetting` and `SystemSetting`: tenant-level or platform-level configuration storage.
- `InvitationCode`: invite-based user onboarding and admin bootstrap.

This layer supports web auth, SSO login, enterprise directory sync, tenant-specific configuration, and invitation-driven company setup.

Important invariant:

- Any tenant-scoped human `User` who becomes a member of a company through registration, company self-creation, or invitation-based joining should also have a corresponding `OrgMember` record in that tenant. Channel-synced members may supply that record from an external provider; otherwise the platform creates a local provider-less `OrgMember` as the canonical relationship/search entry for agent relationship management and OKR tracking.

### 2.2 Agent Runtime Entities

Primary models:

- `Agent`: the main digital employee entity.
  - Important fields include `agent_type`, `primary_model_id`, `fallback_model_id`, `status`, heartbeat settings, autonomy policy, tenant ownership, and system-agent flags.
- `Participant`: universal sender/receiver identity used to normalize humans and agents in messaging.
- `ChatSession`: conversation container for web chat, channel conversations, trigger reflection sessions, A2A sessions, and group sessions.
  - Platform sessions now distinguish a long-lived primary thread (`is_primary=true`) from temporary side-topic threads.
  - Platform-user unread state is tracked per session via `last_read_at_by_user`.
- `ChatMessage` (stored in `audit.py`): the durable event log for user messages, assistant replies, tool calls, and runtime outputs.
- `AgentCredential`: encrypted per-agent session-cookie storage used by integrations such as AgentBay Take Control cookie export and browser-state reinjection, without persisting third-party usernames or passwords.

The messaging layer is deliberately more general than ordinary user/assistant chat, because the same persistence path supports web UI, IM channels, A2A, and trigger-driven reflection sessions.

### 2.3 Extensibility, Workspace, and Publishing

Primary models:

- `Tool` and `AgentTool`: global/tenant tool registry plus per-agent assignment and config overrides.
- `Skill` and `SkillFile`: skill package registry and multi-file skill content.
- `WorkspaceFileRevision` and `WorkspaceEditLock`: file revision history and short-lived human editing locks for agent workspaces.
- `PublishedPage`: public HTML publishing metadata for workspace files served via short IDs.
- `Notification`: notification inbox records for users and agents.

This layer is what turns Clawith from a single agent chat surface into a configurable workspace platform with reusable capabilities and publication workflows.

### 2.4 Autonomy and Async Delivery

Primary models:

- `AgentTrigger`: Aware trigger definitions for cron, once, interval, poll, on-message, and webhook wake conditions.
- `GatewayMessage`: delivery queue for OpenClaw nodes that run outside the main backend process.

These models are the foundation for asynchronous execution and agent wake-up behavior without direct human initiation.

---

## Module 3: Native Agent Runtime

The native runtime is centered on `backend/app/api/websocket.py`.

### 3.1 WebSocket Session Bootstrap

When the frontend opens an agent chat:

1. The browser connects to `/ws/chat/{agent_id}`.
2. The backend validates the user, agent access, and usable model selection.
3. It loads or creates the relevant `ChatSession`.
4. It reconstructs recent history, including prior `tool_call` records, into the model-facing message format.
5. It starts a realtime streaming loop back to the client.

This path is used for ordinary web chat, but the same underlying `call_llm()` machinery is also reused by triggers and some background execution paths.

For first-party platform chat, the bootstrap now prefers the user's primary session for that agent. This keeps agent-initiated reminders and ongoing context in one durable thread, while user-created ad-hoc sessions remain temporary.

### 3.2 Prompt Assembly and Runtime Context

Prompt context is built primarily by `backend/app/services/agent_context.py`.

The context builder pulls together:

- `soul.md`
- long-term memory (`memory/memory.md` or legacy fallback)
- a skill index derived from the workspace `skills/` directory
- relationship notes
- runtime system instructions
- special-case injections such as OKR Agent rules or channel-specific capability guidance

The important architectural point is that an agent's behavior is not defined only by database fields. It is also materially shaped by files in its persistent workspace.

### 3.3 Tool-Calling Loop

The core `call_llm()` flow is a bounded iterative loop:

1. Select a primary model, with runtime fallback to the configured fallback model when needed.
2. Stream assistant output.
3. Detect requested tool calls.
4. Execute tools through the agent tool layer.
5. Append tool results back into the conversation context.
6. Continue until there is no further tool call or limits are reached.

Key protections already present in the runtime:

- tool-round limits
- warning injection before limit exhaustion
- hard validation for malformed high-risk tool arguments
- quota checks
- token accounting and estimation fallback when providers do not return usage
- optional vision/media handling via helper services such as `vision_inject.py`

### 3.4 Session Variants Supported by the Same Runtime

The same native engine supports more than one conversation shape:

- direct user-agent web chat
- channel-backed chat sessions
- A2A sessions
- trigger-created reflection sessions
- session resume/history browsing via `chat_sessions.py`

Two first-party session rules are now important:

- agent-initiated platform messages reuse the primary session instead of opening a fresh thread each time
- unread badges are derived from assistant/system/tool messages created after `ChatSession.last_read_at_by_user`
- when the owning platform user is actively viewing that exact session, newly delivered assistant/tool/trigger messages immediately advance `last_read_at_by_user` so the active thread does not show itself as unread
- trigger results are routed to one explicit destination session: a user's primary platform session for user-originated context, the matching A2A session for agent-to-agent context, or only the trigger reflection session for pure system/reflection work
- if a trigger already sends the user-facing platform message via `send_platform_message`, the daemon suppresses the extra trigger recap in that primary session and leaves the full execution trace only in the reflection session

This is why session and participant handling are more complex than a typical one-user/one-bot design.

---

## Module 4: Aware Engine

The Aware engine is implemented primarily through:

- `backend/app/models/trigger.py`
- `backend/app/api/triggers.py`
- `backend/app/services/trigger_daemon.py`
- `backend/app/services/heartbeat.py`

### 4.1 Trigger Types and Evaluation

Current trigger types include:

- `cron`
- `once`
- `interval`
- `poll`
- `on_message`
- `webhook`

`trigger_daemon.py` runs a periodic tick, evaluates enabled triggers, applies cooldown and expiry rules, and groups fired triggers by `agent_id`.

### 4.2 Invocation Flow

When triggers fire:

1. Trigger state is updated before invocation to avoid duplicate fires during long-running LLM tasks.
2. A structured wake context is assembled from trigger name, reason, matched message, focus reference, and webhook payload when relevant.
3. A reflection-style `ChatSession` is created with `source_channel="trigger"`.
4. The native `call_llm()` loop is invoked.
5. Trigger results may be persisted and also pushed back into active user WebSocket sessions as trigger notifications.

This means Aware is not a separate execution engine. It is a structured wake-up layer on top of the native agent runtime.

### 4.3 Heartbeat and A2A Wake Integration

The trigger daemon also coordinates with heartbeat behavior and A2A wake paths:

- periodic heartbeat checks run on a slower cadence inside the same operational loop
- A2A notifications can be converted into synthetic wake contexts
- dedup windows and chain-depth guards help prevent wake storms

The current implementation is therefore closer to a unified autonomy framework than a simple scheduler.

---

## Module 5: OpenClaw Gateway and External Channel Ingress

Clawith has two major non-web ingress families: OpenClaw nodes and IM/workflow channels.

### 5.1 OpenClaw Gateway

`backend/app/api/gateway.py` provides the external node protocol for `agent_type="openclaw"` agents.

The main path is:

1. External node authenticates with `X-Api-Key`.
2. Node polls for pending `GatewayMessage` work.
3. Node runs its local prompt/tool/model flow.
4. Node reports the result back.
5. Backend writes the result into chat persistence and can notify active WebSocket viewers.

This allows Clawith to treat remote machines as first-class execution agents while still using the central session/history model.

### 5.2 Channel Ingress Normalization

The backend includes channel adapters for:

- Feishu
- Slack
- Discord
- DingTalk
- WeCom
- Teams

The integration depth varies, but the architectural pattern is consistent:

1. Receive an external event.
2. Map sender/channel identity into tenant-aware internal records.
3. Resolve or create the relevant `ChatSession`.
4. Convert the external message into normalized internal context.
5. Reuse the same core LLM execution path.
6. Convert the response back into channel-native delivery format.

Feishu is currently the deepest integration, including image ingestion, contact mapping, card-style streaming updates, and tenant-stable identity handling.

---

## Module 6: Tool, Skill, and Workspace Ecosystem

This is one of the most important parts of the system because it defines what agents can actually do.

### 6.1 Tool Registry and MCP Import

Tools are stored in the database and assigned per agent.

There are two main tool classes:

- builtin tools
- MCP-backed tools

Key files:

- `backend/app/api/tools.py`
- `backend/app/services/agent_tools.py`
- `backend/app/services/resource_discovery.py`
- `backend/app/services/mcp_client.py`

Important behaviors:

- builtin and tenant-scoped tools can be managed from the backend API
- sensitive tool config values are encrypted/decrypted through the API layer
- MCP servers can be discovered from Smithery and ModelScope
- imported MCP servers can expand into multiple concrete tools
- agent-level tool assignments can override default/global configuration

### 6.2 Skill Registry and Skill Packages

Skills are separate from tools.

Tools provide callable actions. Skills provide procedural instructions and optional multi-file assets such as:

- `SKILL.md`
- helper scripts
- references
- examples

Key files:

- `backend/app/api/skills.py`
- `backend/app/services/skill_seeder.py`
- `backend/app/services/agent_context.py`

The runtime only loads a summarized index into the prompt by default, then expects the agent to read the full skill file when it becomes relevant.

### 6.3 Workspace Files, Collaboration, and Publishing

Agent workspaces live on disk under the configured agent data directory, but the database tracks collaboration state.

Key files:

- `backend/app/services/workspace_collaboration.py`
- `backend/app/models/workspace.py`
- `backend/app/api/pages.py`

Current capabilities include:

- path normalization and traversal-safe file resolution
- revision history for meaningful writes
- short-lived human edit locks to prevent agent/user collisions
- prompt-level workspace organization guidance so agents inspect existing folders before writing, prefer relevant subfolders over `workspace/` root, and create a new topical folder when needed
- public HTML publishing through `PublishedPage`
- sandboxed public rendering with CSP on `/p/{short_id}`

### 6.4 AgentBay and Take Control

Clawith also supports shared control of remote browser/computer environments through AgentBay.

Key files:

- `backend/app/services/agentbay_client.py`
- `backend/app/api/agentbay_control.py`

The architectural idea is:

- agents can operate browser/computer sessions through tools
- humans can temporarily take over those sessions
- Take Control places a lock so automatic agent actions pause during manual intervention
- cookies and browser state can be exported back into agent-managed credentials

This is a meaningful collaboration layer, not just a thin remote desktop helper.

---

## Module 7: Enterprise and Platform Control Plane

Beyond agent execution, Clawith contains a substantial admin/control plane.

### 7.1 Enterprise Management

`backend/app/api/enterprise.py` is one of the largest and most operationally important route modules.

It currently handles several responsibilities:

- tenant-scoped LLM model pool management
- model test calls and provider registry access
- enterprise info and audit/approval-related endpoints
- identity provider CRUD
- SSO-related settings
- org department/member listing
- org sync trigger endpoints
- invitation-code related enterprise administration

The corresponding services include `sso_service.py`, `enterprise_sync.py`, `org_sync_service.py`, and provider-specific auth/sync adapters.

### 7.2 Platform Administration

`backend/app/api/admin.py` handles platform-wide control for platform admins, including:

- company listing and creation
- company activation toggles
- platform metrics
- platform-level settings such as self-serve company creation and invitation policies

This layer is conceptually separate from tenant admin. It operates across all tenants.

### 7.3 Notifications and Activity

Operational visibility also includes:

- `notification.py`: user notification inbox and tenant broadcast flow
- `activity.py` and audit log services: historical activity and usage tracking
- quota guard services: message quota, agent creation quota, agent LLM quota, and heartbeat floor enforcement

This means the control plane is not only configuration management. It also includes enforcement and observability.

---

## Module 8: Frontend Architecture

The frontend is not a thin shell. It coordinates routing, auth recovery, realtime chat rendering, enterprise admin surfaces, and workspace-level UX.

### 8.1 Route Topology

`frontend/src/App.tsx` defines the current high-level product routes:

- `/login`, `/forgot-password`, `/reset-password`, `/verify-email`
- `/sso/entry`
- `/setup-company`
- `/dashboard`
- `/plaza`
- `/agents/new`
- `/agents/:id`
- `/messages`
- `/enterprise`
- `/okr`
- `/invitations`
- `/admin/platform-settings`

The app also consumes token handoff in URL parameters for cross-domain tenant switching, while explicitly avoiding collisions with password-reset and email-verification token flows.

### 8.2 AgentDetail as the Main Work Surface

`frontend/src/pages/AgentDetail.tsx` is the most important frontend page.

It is responsible for a broad mix of concerns:

- WebSocket chat streaming
- live tool-call rendering
- session switching
- A2A message display
- trigger/Aware configuration UI
- workspace-related controls
- various agent settings and admin panels

Architecturally, this file functions as the main operating console for a single agent.

### 8.3 State, Theme, and Realtime Rendering

Key frontend patterns:

- Zustand stores hold auth and lightweight global state.
- React Query is available for data-fetching coordination.
- `index.css` centralizes theme primitives, shared animations, and layout tokens.
- The realtime chat UI relies on incremental rendering strategies to avoid repainting the entire message list for every stream chunk.

There are also global UX behaviors such as:

- notification bar rendering from public backend settings
- route guards for auth, tenant setup, and email verification
- auto-reconnect/resend behavior in chat flows

---

## Module 9: OKR System

The OKR subsystem has its own dedicated API surface and service layer and is now a first-class product area rather than a small extension.

Key files:

- `backend/app/api/okr.py`
- `backend/app/models/okr.py`
- `backend/app/services/okr_scheduler.py`
- `backend/app/services/okr_daily_collection.py`
- `backend/app/services/okr_reporting.py`
- `backend/app/services/okr_agent_hook.py`

Current architectural characteristics:

- tenant-level OKR cadence is persisted through OKR settings
- the OKR Agent is seeded and patched at startup
- daily collection and reporting are coordinated through dedicated backend services
- tracked relationships determine who participates in collection/reporting flows
- human and agent replies are normalized through the OKR Agent's runtime context and tools
- frontend OKR views include period-aware browsing, company reports, and member-level daily report inspection

The OKR subsystem therefore combines scheduled workflow, agent instruction shaping, persistence, and reporting UI.

---

Clawith should be understood as a coordinated system of tenant-scoped agents, persistent workspaces, trigger-driven autonomy, channel adapters, and enterprise control surfaces. When adding new features, the main architectural questions are usually:

- Which tenant boundary does this belong to?
- Does it enter through the native runtime, Aware triggers, a channel adapter, or the OpenClaw gateway?
- Does it belong in workspace files, database models, or both?
- Is it a tool, a skill, a trigger, a published artifact, or a control-plane setting?

Answering those four questions correctly is usually enough to place new code in the right part of the system.

---

## Changelog

| Date | Summary |
| --- | --- |
| 2026-04-21 | Clarified human messaging tool selection so platform-labeled relationships should use `send_platform_message`, channel-labeled relationships should use `send_channel_message`, and the runtime now transparently reroutes mistaken channel sends for platform-only users back onto the platform messaging path. |
| 2026-04-20 | Strengthened workspace-writing guidance so agents should inspect existing folder structure before creating documents, prefer relevant subfolders instead of dumping files into `workspace/` root, and create a new topical folder when no suitable location exists. |
| 2026-04-20 | Tightened trigger result routing so trigger replies no longer fan out to every active web session; user-originated results now land in their primary session, A2A results stay in their A2A session, pure reflection work stays in trigger/reflection sessions, and user-facing `send_platform_message` deliveries no longer get duplicated by an extra trigger recap in the same chat. |
| 2026-04-20 | Renamed the first-party proactive messaging tool from `send_web_message` to `send_platform_message`, covering both web and app surfaces, and added startup seeder logic to rename legacy tool rows in place so existing agent assignments keep working. |
| 2026-04-20 | Made OKR Agent startup patching self-heal missing builtin OKR tool rows before assigning tools, preventing `Unknown tool: upsert_member_daily_report` failures on older databases. |
| 2026-04-20 | Added primary first-party chat sessions, per-session unread tracking, and agent sidebar unread counts so proactive agent messages reuse one durable platform thread. |
