# Multi-User Workspace Isolation System

## 🎯 Summary

Implements comprehensive multi-user workspace isolation for Clawith, solving session pollution when multiple users interact with the same agent. Includes Feishu integration, file sharing tools, and session management.

## ✨ Key Features

### 1. User Workspace Isolation
- Each user gets their own `users/{uuid}/files/` directory
- Platform user ID (UUID) instead of Feishu open_id (ou_xxx)
- Agent-level `user_isolation_enabled` toggle
- Personal Space tab in UI for managing private files

### 2. File Sharing
- `workspace/xxx` - User-isolated files (default)
- `shared/xxx` - Shared across all users
- `enterprise_info/xxx` - Company-wide info
- `move_file()` and `copy_file()` tools

### 3. Feishu Integration
- `/new` or `新建对话` command to create new session
- Session cached in Redis for 24 hours
- Files uploaded to correct user directory

## 🛠️ New Tools

```python
# Move file to shared space
move_file("workspace/report.md", "shared/report.md")

# Copy file for sharing
copy_file("workspace/notes.md", "shared/notes.md")

# List files (shows workspace/ prefix)
list_files("")
# Returns: 📂 workspace/: 0 folder(s), 1 file(s)
#          📄 workspace/file.xlsx (96.8KB)

# Send file via channel
send_channel_file(
    file_path="workspace/file.xlsx",
    member_name="Recipient Name",
    message="Optional message"
)
```

## 🐛 Bug Fixes

- Fix Feishu file upload path (UUID vs open_id)
- Fix `send_channel_file` path resolution in user workspace
- Fix `list_files` to show `workspace/` prefix
- Fix `user_workspaces API` to list `files/` directory
- Fix vision injection in LLM caller

## 📦 Migration

⚠️ **Breaking Change**: User workspace paths changed from `users/{open_id}/` to `users/{uuid}/`

Run migration script:
```bash
python3 migrate_user_workspaces.py
```

## 📁 Files Changed (27 files)

### Backend
- `backend/app/models/agent.py` - Add user_isolation_enabled field
- `backend/app/schemas/schemas.py` - Update schemas
- `backend/app/api/agents.py` - Support updating isolation setting
- `backend/app/api/feishu.py` - File upload, /new command, session caching
- `backend/app/api/user_workspaces.py` - New API for user workspace management
- `backend/app/services/agent_tools.py` - File tools, path resolution
- `backend/app/services/agent_context.py` - User-specific memory loading
- `backend/app/services/channel_user_service.py` - Fix multi-provider issue
- `backend/app/services/llm/caller.py` - Vision injection fix
- `backend/alembic/versions/add_user_isolation.py` - DB migration

### Frontend
- `frontend/src/components/UserWorkspace.tsx` - Personal Space component
- `frontend/src/services/userWorkspaceApi.ts` - API client
- `frontend/src/pages/AgentCreate.tsx` - Isolation toggle
- `frontend/src/pages/AgentDetail.tsx` - Personal Space tab
- `frontend/src/i18n/en.json` & `zh.json` - Translations

### Scripts
- `migrate_user_workspaces.py` - Migration script
- `create_agent.py` - CLI agent creation tool

## 🧪 Testing

1. Upload file via Feishu
2. Verify file saved to `users/{uuid}/files/`
3. Use `list_files('')` - should show `workspace/filename`
4. Use `send_channel_file` - should successfully send
5. Use `/new` command - should create new session
6. Verify subsequent messages use new session

## 📊 Stats

- 27 files changed
- 2,359 insertions(+)
- 52 deletions(-)
