# Clawith v1.7.1 Release Notes

## What's New

### ClawHub Skills Marketplace
- Browse and install skills directly from ClawHub (the OpenClaw skill registry)
- Import skills from any GitHub URL
- ClawHub API key configuration for authenticated access to the skill registry
- Tenant-scoped GitHub token configuration for higher API rate limits
- Skill tenant isolation — imported skills are properly scoped to the importing company

### Feishu User Identity Architecture Fix
Replaced `open_id` (per-app, unstable) with `user_id` (cross-app, stable) as the primary identifier for Feishu users. This fixes:
- Duplicate user records when switching Feishu Apps or using multiple bots
- Cross-app errors when the org sync App differs from the Agent's bot App
- Session fragmentation — chat history now stays unified across App changes

All changes include `open_id` fallback for environments that haven't enabled `user_id` permissions yet.

### Logging System Overhaul
- Unified logging with loguru and trace ID support for request tracing
- LLM Request ID tracking for debugging model interactions
- Improved error messages throughout the platform

### Bug Fixes
- Fixed notification badge cramping for multi-digit counts
- Fixed IME composition conflict with Enter to send
- Fixed emoji-first-character handling in agent avatars
- Fixed agent creation validation error messages
- Fixed sidebar agent sorting (now by created_at descending)
- Fixed ClawHub 429 rate limit handling
- Centered agent avatars in collapsed sidebar
- Prevented ClawHub key save from clearing GitHub token

---

## Upgrade Guide

> **Important**: Users must upgrade one version at a time (e.g., v1.6.0 -> v1.7.0 -> v1.7.1). Skipping versions is not supported.

### Option A: Docker Deployment (Recommended)

1. **Pull the latest code**:
   ```bash
   git pull origin main
   ```

2. **Check environment variables** (Optional):
   ```bash
   diff .env .env.example
   ```

3. **Rebuild and restart services**:
   ```bash
   docker compose down
   docker compose up -d --build
   ```
   > During startup, `entrypoint.sh` automatically runs `alembic upgrade head` and data migration scripts. No manual intervention required.

4. **Feishu user_id migration** (Optional but recommended):
   
   If you use Feishu org sync, run this one-time migration to backfill `user_id` and clean up duplicate users:
   ```bash
   docker exec clawith-backend-1 python3 -m app.scripts.cleanup_duplicate_feishu_users
   ```
   
   > **Prerequisite**: Your Feishu org sync App must have the `contact:user.employee_id:readonly` permission. Add it in the [Feishu Open Platform](https://open.feishu.cn/) if missing, then re-sync from Company Settings > Org Structure > Sync Now before running the script.

5. **Verify**: Visit the frontend and confirm the version shows `1.7.1` in the sidebar footer.

---

### Option B: Source Deployment

1. **Pull the latest code**:
   ```bash
   git pull origin main
   ```

2. **Run database migrations**:
   ```bash
   cd backend
   alembic upgrade head
   ```

3. **Update backend dependencies** (new dependency: `loguru`):
   ```bash
   pip install -r requirements.txt
   ```

4. **Restart**:
   ```bash
   bash restart.sh
   ```

5. **Feishu migration** (same as Docker step 4 above):
   ```bash
   cd backend
   python3 -m app.scripts.cleanup_duplicate_feishu_users
   ```

---

### New Dependencies
| Component | Dependency | Required |
|-----------|-----------|----------|
| Backend | `loguru>=0.7.0` | Yes |
| Platform | GitHub Token (Company Settings > Skills) | Optional (raises GitHub API rate limit) |
| Platform | ClawHub API Key (Company Settings > Skills) | Optional (for authenticated ClawHub access) |

### New Database Changes (auto-applied by Alembic)
- New table: `tenant_settings` (per-tenant key-value configuration)
- New migration: `df3da9cf3b27` (adds missing columns from Docker entrypoint to Alembic)
