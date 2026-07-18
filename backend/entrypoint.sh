#!/bin/bash
# Docker entrypoint: optionally run DB migrations, then start the app.

set -e

PROCESS_ROLE="${PROCESS_ROLE:-all}"
ALLOW_MIGRATION_FAILURE="${ALLOW_MIGRATION_FAILURE:-false}"
START_COMMAND="${START_COMMAND:-uvicorn app.main:app --host 0.0.0.0 --port 8000}"

role_contains() {
    case ",${PROCESS_ROLE}," in
        *,all,*|*,"$1",*) return 0 ;;
        *) return 1 ;;
    esac
}

# --- Permission fixing and privilege dropping ---
if [ "$(id -u)" = '0' ]; then
    echo "[entrypoint] Detected root user, checking permissions..."
    TARGET_DIR="${AGENT_DATA_DIR:-/data/agents}"
    if [ -d "${TARGET_DIR}" ]; then
        CURRENT_OWNER=$(stat -c '%U:%G' "${TARGET_DIR}" 2>/dev/null || echo "")
        if [ "${CURRENT_OWNER}" != "clawith:clawith" ]; then
            echo "[entrypoint] Directory ${TARGET_DIR} owner is '${CURRENT_OWNER}', fixing permissions..."
            chown -R clawith:clawith "${TARGET_DIR}"
        else
            echo "[entrypoint] Directory ${TARGET_DIR} is already owned by clawith:clawith, skipping chown."
        fi
    fi

    echo "[entrypoint] Dropping privileges to 'clawith' and re-executing..."
    exec gosu clawith /bin/bash "$0" "$@"
fi
# -------------------------------------------------------

if [ -z "${INSTANCE_ID:-}" ]; then
    SAFE_PROCESS_ROLE="${PROCESS_ROLE//,/-}"
    export INSTANCE_ID="${SAFE_PROCESS_ROLE}-$(hostname)"
fi
echo "[entrypoint] INSTANCE_ID=${INSTANCE_ID}"

if role_contains "bootstrap"; then
    echo "[entrypoint] Step 1: Running alembic migrations for PROCESS_ROLE=${PROCESS_ROLE}..."
    set +e
    ALEMBIC_OUTPUT=$(alembic upgrade head 2>&1)
    ALEMBIC_EXIT=$?
    set -e

    if [ $ALEMBIC_EXIT -ne 0 ]; then
        echo ""
        echo "========================================================================"
        echo "[entrypoint] ERROR: Alembic migration FAILED (exit code $ALEMBIC_EXIT)"
        echo "========================================================================"
        echo ""
        echo "$ALEMBIC_OUTPUT"
        echo ""
        if [ "$ALLOW_MIGRATION_FAILURE" = "true" ]; then
            echo "[entrypoint] Continuing because ALLOW_MIGRATION_FAILURE=true"
        else
            exit $ALEMBIC_EXIT
        fi
    else
        echo "[entrypoint] Alembic migrations completed successfully."

        echo "[entrypoint] Step 2: Installing LangGraph checkpoint tables..."
        set +e
        CHECKPOINT_OUTPUT=$(python -m app.scripts.setup_langgraph_checkpoints 2>&1)
        CHECKPOINT_EXIT=$?
        set -e

        if [ $CHECKPOINT_EXIT -ne 0 ]; then
            echo ""
            echo "========================================================================"
            echo "[entrypoint] ERROR: LangGraph checkpoint setup FAILED (exit code $CHECKPOINT_EXIT)"
            echo "========================================================================"
            echo ""
            echo "$CHECKPOINT_OUTPUT"
            echo ""
            if [ "$ALLOW_MIGRATION_FAILURE" = "true" ]; then
                echo "[entrypoint] Continuing because ALLOW_MIGRATION_FAILURE=true"
            else
                exit $CHECKPOINT_EXIT
            fi
        else
            echo "[entrypoint] LangGraph checkpoint tables are ready."
        fi
    fi
else
    echo "[entrypoint] Step 1: Skipping alembic for PROCESS_ROLE=${PROCESS_ROLE}"
    echo "[entrypoint] Step 2: Skipping LangGraph checkpoint setup for PROCESS_ROLE=${PROCESS_ROLE}"
fi

echo "[entrypoint] Step 3: Starting uvicorn..."
exec /bin/bash -lc "$START_COMMAND"
