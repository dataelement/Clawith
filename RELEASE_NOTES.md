# v1.8.1 Release Notes

> Released: 2026-04-03

This is a stability and polish release built on top of v1.8.0-beta.3, covering security hardening,
Feishu reliability fixes, a redesigned tool-call visualization, new file-management tools, and
a first-class Kubernetes deployment option.

---

## Highlights

### Redesigned Tool-Call Visualization (AnalysisCard)

The live chat view now shows agent reasoning and tool calls in a unified **AnalysisCard** that
groups interleaved thinking and tool-call messages into one collapsible block. The card shows:
- A pulse LED while the agent is running, turning green on completion
- The currently-active tool name in collapsed state alongside tool-count badge
- Individual `<details>` rows per tool for args and result (collapsed by default)
- Italic thinking-content blocks inline for extended reasoning (deepthink) models

### New File Management Tools

Three new built-in tools are available to all agents:
- **`edit_file`** — targeted line-range edits without rewriting the entire file
- **`search_files`** — substring or regex search across a workspace
- **`find_files`** — glob-pattern file lookup
- **`read_file`** now supports `offset` / `limit` for reading large files in pages

### Kubernetes Deployment (Helm Chart)

A production-ready Helm chart is now included at `helm/clawith/`. Deploy Clawith on any
Kubernetes cluster in one command:
```bash
helm upgrade --install clawith helm/clawith/ -f values.yaml
```

### Security Fixes

- **Cross-tenant data leak** — org member and department search was returning results across
  tenant boundaries. Now strictly scoped to the requesting tenant. (#security)
- **Platform admin token scope** — `platform_admin` role was not pinned to `tenant_id` in the
  JWT, allowing cross-tenant privilege escalation. Fixed.
- **Duplicate OrgMember shell** — channel users could create duplicate OrgMember rows on
  reconnect. A uniqueness guard has been added.

### Feishu Integration Reliability

- **`feishu_doc_append` intermittent failures** — Markdown `---` dividers were converted to
  `block_type: 22` which the Feishu batch-children API rejects. They now render as a text
  separator line (`────────────────────────`), always accepted.
- **`index: -1` removed** from the children API call — Feishu defaults to append-at-end when
  `index` is omitted, avoiding `1770001 invalid param` errors.
- **Stale `websocket_chat` import** — `feishu_doc_create` was trying to import
  `channel_feishu_sender_open_id` from a deleted module, generating a visible warning. Fixed.
- **Feishu streaming card stalls** — ordered patch queue now correctly processes streaming
  updates for Feishu cards without stalling.
- **Tool status stuck on "running"** — Feishu-channel tool status now correctly transitions
  from `running` → `done` after tool completion.
- **Added `wiki:wiki` permission** to the recommended Full permission set in channel config.

### Admin Chat UI

- **Read-only session viewer** — Admins viewing other users' sessions see a clear "Read-only ·
  username" badge at top-left (fixed overlay, never scrolls away).
- **Card border** — the entire chat area is now enclosed in a 12px-radius bordered card for
  visual clarity.
- **Optimistic relationship deletion** — relationship rows fade out immediately on delete (no wait).

### Cross-Domain Tenant Switch

The `?token=` query param is now consumed on app bootstrap, so users switching between tenant
instances via a generated link land directly in the correct tenant without requiring a page reload.

### i18n Improvements

- All emoji removed from `en.json` and `zh.json` translation keys (project policy).
- Hardcoded "Copy", "Upload", and several status strings now properly use `t()`.
- New i18n key `agent.chat.analysing` for the AnalysisCard header.
- Credential-related UI strings in zh.json completed.

---

## Upgrade Guide

### No breaking changes. No database migrations required.

#### Option A — Docker Compose

```bash
cd <clawith-dir>
git pull origin main
docker compose down && docker compose up -d --build
```

Or the rolling update (no downtime):

```bash
git pull origin main

# Frontend
cd frontend && npm install && npm run build
cp public/logo.png dist/ && cp public/logo.svg dist/
cd dist && zip -r ../dist.zip . && cd ../..
docker cp frontend/dist.zip clawith-frontend-1:/usr/share/nginx/html/dist.zip
docker exec clawith-frontend-1 sh -c "cd /usr/share/nginx/html && unzip -o dist.zip"
docker compose restart frontend

# Backend
docker cp backend/app clawith-backend-1:/app/
docker exec clawith-backend-1 find /app -name "__pycache__" -exec rm -rf {} + 2>/dev/null
docker compose restart backend
```

#### Option B — Source Deployment

```bash
git pull origin main
cd frontend && npm install && npm run build
cd ..
# Restart backend process (e.g. supervisorctl restart clawith-backend)
```

#### Option C — Kubernetes (Helm)

```bash
helm upgrade clawith helm/clawith/ -f values.yaml
```

No Alembic migration is required for this release.

---

## Full Changelog

See all commits since v1.8.0-beta.3:
https://github.com/dataelement/Clawith/compare/v1.8.0-beta.3...v1.8.1
