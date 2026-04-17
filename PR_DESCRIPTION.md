# Multi-User Workspace Isolation and Feishu Integration Fixes

## Summary

This PR implements comprehensive multi-user workspace isolation for Clawith, fixing session pollution when multiple users interact with the same agent. It also adds Feishu session management and file sharing improvements.

## Key Changes

### 🎯 Multi-User Workspace Isolation

- **User-specific directories**: Each user gets their own `users/{user_id}/files/` directory
- **Platform user ID**: Uses platform UUID instead of Feishu open_id (ou_xxx) for consistency
- **Shared workspace**: Add `shared/` path prefix for cross-user file sharing
- **Agent toggle**: `user_isolation_enabled` field to enable/disable per agent

### 📱 Feishu Integration

- **New session command**: `/new` or `新建对话` to create new session (cached in Redis for 24h)
- **File upload fix**: Files saved to correct user directory (`users/{uuid}/files/`)
- **Path consistency**: `list_files` returns `workspace/filename` matching expected format

### 🛠️ New Tools

- `move_file(source, destination)` - Move files between workspace/ and shared/
- `copy_file(source, destination)` - Copy files for sharing
- Path conventions:
  - `workspace/xxx` - User-isolated files (default)
  - `shared/xxx` - Shared across all users
  - `enterprise_info/xxx` - Company-wide info

### 🐛 Bug Fixes

- Fix `send_channel_file` to resolve files in user workspace
- Fix `list_files` to show `workspace/` prefix for user files
- Fix user workspace API to list `files/` directory
- Fix vision injection to use `AGENT_DATA_DIR` correctly

## Breaking Changes

⚠️ **User workspace paths changed**

Before: `users/{open_id}/` (e.g., `users/ou_xxx/`)
After: `users/{uuid}/` (e.g., `users/4059d7e7-.../`)

**Migration required**: Run `python3 migrate_user_workspaces.py` to migrate existing user directories.

## Usage Examples

### Feishu Session Management

```
/new              # Create new session (English)
新建对话           # Create new session (Chinese)
```

### File Sharing

```python
# List files (shows workspace/ prefix)
list_files('')
# Returns: 📂 workspace/: 0 folder(s), 1 file(s)
#          📄 workspace/wms逻辑说明.xlsx (96.8KB)

# Send file to someone
send_channel_file(
    file_path="workspace/wms逻辑说明.xlsx",
    member_name="朱孙博",
    message="这是吴艺晨发给你的文件"
)

# Share file with all users
write_file("shared/guide.md", content)

# Move file from isolated to shared
move_file("workspace/report.md", "shared/report.md")
```

## Files Changed

- `backend/app/api/feishu.py` - Feishu file upload, session management, /new command
- `backend/app/api/user_workspaces.py` - User workspace API fixes
- `backend/app/services/agent_tools.py` - File tools, path resolution, move/copy tools
- `backend/app/services/llm/caller.py` - Vision injection fix
- `migrate_user_workspaces.py` - Migration script (new)

## Testing

1. Upload file via Feishu
2. Check file saved to `users/{uuid}/files/`
3. Use `list_files('')` - should show `workspace/filename`
4. Use `send_channel_file` - should successfully send
5. Use `/new` command - should create new session
6. Subsequent messages should use new session

## Related Issues

Fixes session pollution issue when multiple users chat with the same agent.
