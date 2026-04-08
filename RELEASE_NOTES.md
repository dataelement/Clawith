# v1.8.2 Release Notes

## What's New

### Security
- **Fix account takeover via username collision** (#300): Prevents an attacker from creating an account with a username matching an existing SSO user's email, which could lead to unauthorized account access.
- **Fix duplicate user creation on repeated SSO logins**: Feishu and DingTalk SSO now correctly reuse existing accounts instead of creating duplicate users.

### AgentBay — Cloud Computer & Browser Automation
- **New: `agentbay_file_transfer` tool**: Transfer files between any two environments — agent workspace, browser sandbox, cloud desktop (computer), or code sandbox — in any direction.
- **Fix: Computer Take Control (TC) white screen**: TC now connects to the correct environment session (computer vs. browser) based on `env_type`. Previously, an existing browser session could hijack the computer TC connection.
- **Fix: OS-aware desktop paths**: The `agentbay_file_transfer` tool description now automatically reflects the correct paths for the agent's configured OS type:
  - Windows: `C:\Users\Administrator\Desktop\`
  - Linux: `/home/wuying/Desktop/`
- **Fix: Desktop file refresh**: After uploading to the Linux desktop directory, GNOME is notified to refresh icon display.
- Multiple Take Control stability fixes: CDP polling replaced with sleep, multi-tab cleanup, 40s navigate timeout, unhashable type errors.

### Feishu (Lark) — CardKit Streaming Cards
- Feishu bot responses now stream as animated typing-effect cards using the CardKit API (#287).
- Fixed SSE stream hang issues and websocket proxy bypass for system proxy conflicts.

### DingTalk & Organization Sync
- Fixed DingTalk org sync permissions guide (`Contact.User.Read` scope).
- Fixed `open_id` vs `employee_id` user type handling in Feishu org sync.

### Other Bug Fixes
- **Fix: SSE stream protection** — `finish_reason` break guard added for OpenAI and Gemini streams to prevent runaway streams.
- **Fix: Duplicate tool `send_feishu_message`** — Removed duplicate DB entry; added dedup guard in tool loading to prevent `Tool names must be unique` LLM errors.
- **Fix: JWT token not consumed** on reset-password and verify-email routes.
- **Fix: NULL username/email** for SSO-created users in `list_users`.
- **Fix: Company name slug generation** — Added `anyascii` + `pypinyin` for universal CJK/Latin transliteration.
- **Fix: `publish_page` URL** — Correctly generates `try.clawith.ai` links on source deployments.
- **Fix: Agent template directory** — Dynamic default for source deployments.
- Various i18n fixes (TakeControlPanel, DingTalk guide).

---

## Upgrade Guide

> **No database migrations required.** No new environment variables.

### Docker Deployment (Recommended)

```bash
git pull origin main
docker compose down && docker compose up -d --build
```

### Source Deployment

```bash
git pull origin main

# Install new Python dependency
pip install anyascii>=0.3.2

# Rebuild frontend
cd frontend && npm install && npm run build
cd ..

# Restart services
```

### nginx Update Required

A new routing rule has been added to `nginx.conf`. If you manage nginx separately (not via Docker), add this block inside your `server {}` before the WebSocket proxy section:

```nginx
location ~ ^/WW_verify_[A-Za-z0-9]+\.txt$ {
    proxy_pass http://backend:8000/api/wecom-verify$request_uri;
    proxy_set_header Host $http_host;
    proxy_set_header X-Real-IP $remote_addr;
}
```

### Kubernetes (Helm)

```bash
helm upgrade clawith helm/clawith/ -f values.yaml
```

No migration job needed.

