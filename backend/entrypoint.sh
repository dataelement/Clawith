#!/bin/bash
# Docker entrypoint: initialize DB tables, then start the app.
# Order matters:
#   1. create_all  - creates all tables using SQLAlchemy models (idempotent)
#   2. alembic stamp head - tells alembic we are at the latest revision (skips migrations)
#      For existing installs that may have missing columns, safe ALTER TABLE patches run first.
#   3. uvicorn - starts the FastAPI app

set -e

# --- Added: Permission fixing and privilege dropping ---
if [ "$(id -u)" = '0' ]; then
    echo "[entrypoint] Detected root user, fixing permissions..."
    # Ensure directories exist and are owned by clawith
    chown -R clawith:clawith ${AGENT_DATA_DIR}
    
    echo "[entrypoint] Dropping privileges to 'clawith' and re-executing..."
    exec gosu clawith /bin/bash "$0" "$@"
fi
# -------------------------------------------------------

echo "[entrypoint] Step 1: Creating/verifying database tables..."
PYTHONPATH=/app python /app/app/scripts/bootstrap_db.py

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

echo "[entrypoint] Step 3: Starting uvicorn..."
exec uvicorn app.main:app --host 0.0.0.0 --port 8000
