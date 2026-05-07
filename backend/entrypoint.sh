#!/bin/bash
# Docker entrypoint: run DB migrations, then start the app.
# Order matters:
#   1. alembic upgrade head — apply all migrations (creates tables + schema changes)
#   2. uvicorn — starts the FastAPI app

set -e

# --- Permission fixing and privilege dropping ---
if [ "$(id -u)" = '0' ]; then
    echo "[entrypoint] Detected root user, fixing permissions..."
    chown -R clawith:clawith ${AGENT_DATA_DIR}
    # Project shared workspace root (bind-mounted; may be owned by root on first boot)
    PROJECT_WS_DIR="${PROJECT_WORKSPACE_DIR:-/data/project_workspaces}"
    mkdir -p "${PROJECT_WS_DIR}"
    chown -R clawith:clawith "${PROJECT_WS_DIR}"
    # Backup target — also bind-mounted; same permission pattern
    BACKUP_DIR_LOCAL="/data/backups"
    mkdir -p "${BACKUP_DIR_LOCAL}"
    chown -R clawith:clawith "${BACKUP_DIR_LOCAL}"

    echo "[entrypoint] Dropping privileges to 'clawith' and re-executing..."
    exec gosu clawith /bin/bash "$0" "$@"
fi
# -------------------------------------------------------

echo "[entrypoint] Step 1: Creating/verifying database tables..."

python << 'PYEOF'
import asyncio, sys

async def main():
    # Import all models to populate Base.metadata before create_all
    from app.database import Base, engine
    import app.models.user           # noqa
    import app.models.agent          # noqa
    import app.models.task           # noqa
    import app.models.llm            # noqa
    import app.models.tool           # noqa
    import app.models.audit          # noqa
    import app.models.skill          # noqa
    import app.models.channel_config # noqa
    import app.models.schedule       # noqa
    import app.models.plaza          # noqa
    import app.models.activity_log   # noqa
    import app.models.org            # noqa
    import app.models.system_settings # noqa
    import app.models.invitation_code # noqa
    import app.models.tenant         # noqa
    import app.models.participant     # noqa
    import app.models.chat_session   # noqa
    import app.models.trigger        # noqa
    import app.models.notification   # noqa
    import app.models.gateway_message # noqa
    import app.models.project        # noqa  (Project feature — required for create_all to resolve chat_sessions.project_id FK)
    import app.models.project_task   # noqa  (Project Tasks — Phase 3 deliverables)

    # Create all tables that don't exist yet (safe to run on every startup)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    print("[entrypoint] Tables created/verified")

    # Apply safe column patches for existing installs that may be missing columns.
    # All statements use IF NOT EXISTS so they are fully idempotent.
    patches = [
        # Quota fields added in v0.2
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS quota_message_limit INTEGER DEFAULT 50",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS quota_message_period VARCHAR(20) DEFAULT 'permanent'",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS quota_messages_used INTEGER DEFAULT 0",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS quota_period_start TIMESTAMPTZ",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS quota_max_agents INTEGER DEFAULT 2",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS quota_agent_ttl_hours INTEGER DEFAULT 48",
        "ALTER TABLE agents ADD COLUMN IF NOT EXISTS expires_at TIMESTAMPTZ",
        "ALTER TABLE agents ADD COLUMN IF NOT EXISTS is_expired BOOLEAN DEFAULT FALSE",
        "ALTER TABLE agents ADD COLUMN IF NOT EXISTS llm_calls_today INTEGER DEFAULT 0",
        "ALTER TABLE agents ADD COLUMN IF NOT EXISTS max_llm_calls_per_day INTEGER DEFAULT 100",
        "ALTER TABLE agents ADD COLUMN IF NOT EXISTS llm_calls_reset_at TIMESTAMPTZ",
        # agent_tools source tracking added later
        "ALTER TABLE agent_tools ADD COLUMN IF NOT EXISTS source VARCHAR(20) NOT NULL DEFAULT 'system'",
        "ALTER TABLE agent_tools ADD COLUMN IF NOT EXISTS installed_by_agent_id UUID",
        # chat_sessions channel tracking
        "ALTER TABLE chat_sessions ADD COLUMN IF NOT EXISTS source_channel VARCHAR(20) NOT NULL DEFAULT 'web'",
        # Token reset tracking
        "ALTER TABLE agents ADD COLUMN IF NOT EXISTS last_daily_reset TIMESTAMPTZ",
        "ALTER TABLE agents ADD COLUMN IF NOT EXISTS last_monthly_reset TIMESTAMPTZ",
        "ALTER TABLE agents ADD COLUMN IF NOT EXISTS tokens_used_total INTEGER DEFAULT 0",
        # OpenClaw Agent support
        "ALTER TABLE agents ADD COLUMN IF NOT EXISTS agent_type VARCHAR(20) NOT NULL DEFAULT 'native'",
        "ALTER TABLE agents ADD COLUMN IF NOT EXISTS api_key_hash VARCHAR(128)",
        "ALTER TABLE agents ADD COLUMN IF NOT EXISTS openclaw_last_seen TIMESTAMPTZ",
        # SSO fields
        "ALTER TABLE tenants ADD COLUMN IF NOT EXISTS sso_enabled BOOLEAN DEFAULT FALSE",
        "ALTER TABLE tenants ADD COLUMN IF NOT EXISTS sso_domain VARCHAR(255)",
        "CREATE UNIQUE INDEX IF NOT EXISTS ux_tenants_sso_domain ON tenants(sso_domain) WHERE sso_domain IS NOT NULL",
    ]

    from sqlalchemy import text
    async with engine.begin() as conn:
        for sql in patches:
            try:
                await conn.execute(text(sql))
            except Exception as e:
                print(f"[entrypoint] Patch skipped ({e})")

    await engine.dispose()
    print("[entrypoint] Column patches applied")

asyncio.run(main())
PYEOF

echo "[entrypoint] Step 2: Running alembic migrations..."
# Run all migrations to ensure database schema is up to date.
# Capture exit code explicitly — do NOT let a migration failure go unnoticed.
set +e
ALEMBIC_OUTPUT=$(alembic upgrade head 2>&1)
ALEMBIC_EXIT=$?
set -e

if [ $ALEMBIC_EXIT -ne 0 ]; then
    echo ""
    echo "========================================================================"
    echo "[entrypoint] WARNING: Alembic migration FAILED (exit code $ALEMBIC_EXIT)"
    echo "========================================================================"
    echo ""
    echo "$ALEMBIC_OUTPUT"
    echo ""
    echo "------------------------------------------------------------------------"
    echo "  The database schema may be INCOMPLETE. Some features will NOT work."
    echo "  Common causes:"
    echo "    - Migration cycle detected (pull latest code to fix)"
    echo "    - Database connection issue"
    echo "    - Incompatible migration state"
    echo ""
    echo "  To fix: pull the latest code and restart the backend."
    echo "    Docker:  git pull && docker compose restart backend"
    echo "    Source:  git pull && alembic upgrade head"
    echo "------------------------------------------------------------------------"
    echo ""
    echo "[entrypoint] Continuing startup despite migration failure..."
else
    echo "[entrypoint] Alembic migrations completed successfully."
fi

echo "[entrypoint] Step 2: Starting uvicorn..."
exec uvicorn app.main:app --host 0.0.0.0 --port 8000
