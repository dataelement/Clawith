#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
BACKEND_DIR="$ROOT/backend"
BACKEND_ENV="$BACKEND_DIR/.env"
BACKEND_ENV_EXAMPLE="$BACKEND_DIR/.env.example"
TARGET_DATABASE="clawith_target"
TARGET_ROLE="clawith_target"
PG_HOST="${CLAWITH_PG_HOST:-localhost}"
PG_PORT="${CLAWITH_PG_PORT:-5432}"
PG_ADMIN_USER="${CLAWITH_PG_ADMIN_USER:-${USER:-postgres}}"
INSTALL_DEV=false

for argument in "$@"; do
    case "$argument" in
        --dev) INSTALL_DEV=true ;;
        *) echo "Unsupported setup option: $argument" >&2; exit 2 ;;
    esac
done

for command in uv psql createdb; do
    if ! command -v "$command" >/dev/null 2>&1; then
        echo "Required command is unavailable: $command" >&2
        exit 1
    fi
done

if [ ! -f "$BACKEND_ENV_EXAMPLE" ]; then
    echo "Missing target environment template: $BACKEND_ENV_EXAMPLE" >&2
    exit 1
fi

is_complete_target_database_url() {
    local url="$1"
    local authority_and_path credentials endpoint database_and_query database port
    local identity_query_pattern

    [[ "$url" == postgresql+asyncpg://* ]] || return 1
    authority_and_path="${url#postgresql+asyncpg://}"
    [[ "$authority_and_path" == *@*/* ]] || return 1
    credentials="${authority_and_path%%@*}"
    endpoint="${authority_and_path#*@}"
    endpoint="${endpoint%%/*}"
    database_and_query="${authority_and_path#*/}"
    database="${database_and_query%%\?*}"
    port="${endpoint##*:}"

    [[ "$credentials" == ?*:?* ]] || return 1
    [[ "$endpoint" == ?*:?* ]] || return 1
    [[ "$port" =~ ^[0-9]+$ ]] || return 1
    (( 10#$port >= 1 && 10#$port <= 65535 )) || return 1
    [[ "$database" == "$TARGET_DATABASE" ]] || return 1
    identity_query_pattern='[?&]([dD][aA][tT][aA][bB][aA][sS][eE]|[dD][bB][nN][aA][mM][eE]|[dD][sS][nN]|[hH][oO][sS][tT]|[pP][aA][sS][sS][wW][oO][rR][dD]|[pP][oO][rR][tT]|[uU][sS][eE][rR]|[uU][sS][eE][rR][nN][aA][mM][eE])='
    [[ ! "$database_and_query" =~ $identity_query_pattern ]]
}

TARGET_DATABASE_URL="postgresql+asyncpg://${TARGET_ROLE}:${TARGET_ROLE}@${PG_HOST}:${PG_PORT}/${TARGET_DATABASE}?ssl=disable"
DATABASE_URL_TO_WRITE="$TARGET_DATABASE_URL"
MANAGE_TARGET_DATABASE=true
if [ -f "$BACKEND_ENV" ]; then
    existing_database_url_line="$(grep -m 1 '^DATABASE_URL=' "$BACKEND_ENV" || true)"
    if [ -n "$existing_database_url_line" ]; then
        existing_database_url="${existing_database_url_line#*=}"
        if ! is_complete_target_database_url "$existing_database_url"; then
            echo "Existing backend/.env DATABASE_URL is not a complete postgresql+asyncpg connection for ${TARGET_DATABASE}." >&2
            echo "Set DATABASE_URL to an existing ${TARGET_DATABASE} connection, or remove backend/.env to let setup create the isolated local default." >&2
            exit 1
        fi
        DATABASE_URL_TO_WRITE="$existing_database_url"
        if [ "$existing_database_url" != "$TARGET_DATABASE_URL" ]; then
            MANAGE_TARGET_DATABASE=false
        fi
    fi
fi

cd "$BACKEND_DIR"
uv lock --check

TEMP_ENV="$(mktemp "$BACKEND_DIR/.env.tmp.XXXXXX")"
trap 'rm -f "$TEMP_ENV"' EXIT

while IFS= read -r line || [ -n "$line" ]; do
    if [[ "$line" =~ ^([A-Z][A-Z0-9_]*)=(.*)$ ]]; then
        key="${BASH_REMATCH[1]}"
        value="${BASH_REMATCH[2]}"
        if [ "$key" = "DATABASE_URL" ]; then
            value="$DATABASE_URL_TO_WRITE"
        elif [ -f "$BACKEND_ENV" ]; then
            existing="$(grep -m 1 "^${key}=" "$BACKEND_ENV" || true)"
            if [ -n "$existing" ]; then
                value="${existing#*=}"
            fi
        fi
        printf '%s=%s\n' "$key" "$value" >> "$TEMP_ENV"
    else
        printf '%s\n' "$line" >> "$TEMP_ENV"
    fi
done < "$BACKEND_ENV_EXAMPLE"

mv "$TEMP_ENV" "$BACKEND_ENV"
trap - EXIT
chmod 600 "$BACKEND_ENV"
echo "Prepared backend/.env from backend/.env.example"

if [ "$MANAGE_TARGET_DATABASE" = true ]; then
    PSQL_ADMIN=(psql --host "$PG_HOST" --port "$PG_PORT" --username "$PG_ADMIN_USER" --dbname postgres --set ON_ERROR_STOP=1)
    if ! "${PSQL_ADMIN[@]}" --tuples-only --no-align --command "SELECT 1 FROM pg_roles WHERE rolname='${TARGET_ROLE}'" | grep -q '^1$'; then
        "${PSQL_ADMIN[@]}" --command "CREATE ROLE ${TARGET_ROLE} LOGIN PASSWORD '${TARGET_ROLE}'"
    fi

    if ! "${PSQL_ADMIN[@]}" --tuples-only --no-align --command "SELECT 1 FROM pg_database WHERE datname='${TARGET_DATABASE}'" | grep -q '^1$'; then
        createdb --host "$PG_HOST" --port "$PG_PORT" --username "$PG_ADMIN_USER" --owner "$TARGET_ROLE" "$TARGET_DATABASE"
    fi
    echo "Prepared PostgreSQL database: $TARGET_DATABASE"
else
    echo "Preserved operator-managed DATABASE_URL; skipped PostgreSQL role and database changes."
fi

if [ "$INSTALL_DEV" = true ]; then
    uv sync --extra dev --frozen
else
    uv sync --frozen
fi

echo "G002 setup complete. No schema migration or product bootstrap was run."
echo "Start the health-only target with: bash restart.sh"
