#!/bin/bash
# Clawith — Restart Script
# Stops existing services and starts backend + frontend.
# Usage: ./restart.sh

set -e
ROOT="$(cd "$(dirname "$0")" && pwd)"
BACKEND_DIR="$ROOT/backend"
FRONTEND_DIR="$ROOT/frontend"
BACKEND_LOG="/tmp/clawith_backend.log"
FRONTEND_LOG="/tmp/clawith_frontend.log"
BACKEND_PID="/tmp/clawith_backend.pid"
FRONTEND_PID="/tmp/clawith_frontend.pid"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[0;33m'; NC='\033[0m'

# ── Load .env ────────────────────────────────────
if [ -f "$ROOT/.env" ]; then
    set -a
    source "$ROOT/.env"
    set +a
fi

# Default DATABASE_URL if not set
: "${DATABASE_URL:=postgresql+asyncpg://clawith:clawith@localhost:5432/clawith?ssl=disable}"
export DATABASE_URL

# Extract PG port from DATABASE_URL (e.g. localhost:5433 → 5433)
PG_PORT=$(echo "$DATABASE_URL" | grep -oP 'localhost:\K[0-9]+' 2>/dev/null || echo "$DATABASE_URL" | sed -n 's/.*localhost:\([0-9]*\).*/\1/p')
PG_PORT=${PG_PORT:-5432}

cleanup() {
    echo -e "${YELLOW}🔄 Stopping existing services...${NC}"
    # Kill by PID file first
    for pidfile in "$BACKEND_PID" "$FRONTEND_PID"; do
        if [ -f "$pidfile" ]; then
            kill -9 "$(cat "$pidfile")" 2>/dev/null || true
            rm -f "$pidfile"
        fi
    done
    # Kill by port as fallback
    if command -v lsof &>/dev/null; then
        lsof -ti:8008 | xargs kill -9 2>/dev/null || true
        lsof -ti:3008 | xargs kill -9 2>/dev/null || true
    else
        fuser -k 8008/tcp 2>/dev/null || true
        fuser -k 3008/tcp 2>/dev/null || true
    fi
    sleep 1
}

wait_for_port() {
    local port=$1 name=$2 max=$3
    for i in $(seq 1 "$max"); do
        if curl -s -o /dev/null -m 1 "http://localhost:$port" 2>/dev/null; then
            echo -e "  ${GREEN}✅ $name ready (${i}s)${NC}"
            return 0
        fi
        sleep 1
    done
    echo -e "  ${RED}❌ $name failed to start in ${max}s${NC}"
    return 1
}

# Add local PG to PATH if setup.sh installed it
if [ -d "$ROOT/.pg/bin" ]; then
    export PATH="$ROOT/.pg/bin:$PATH"
fi
# Also check common non-standard PG locations
for dir in /www/server/pgsql/bin /usr/local/pgsql/bin; do
    if [ -x "$dir/pg_isready" ] && ! command -v pg_isready &>/dev/null; then
        export PATH="$dir:$PATH"
    fi
done

cleanup

# ── Check for Docker Multi-Instance ────────────────
if command -v docker &>/dev/null && docker ps | grep -E 'clawith' >/dev/null; then
    echo -e "${YELLOW}🐳 Detected existing Docker containers! Starting in Docker mode to avoid conflicts...${NC}"
    DIR_NAME=$(basename "$(dirname "$ROOT")")
    [ -z "$DIR_NAME" ] && DIR_NAME="custom"
    
    PROJECT_NAME="clawith-${DIR_NAME}"
    echo -e "  Using project name: ${GREEN}$PROJECT_NAME${NC}"
    export COMPOSE_PROJECT_NAME="$PROJECT_NAME"
    
    cd "$ROOT"
    
    # Find an open port starting from 3008 for the frontend
    FRONTEND_PORT=3008
    while true; do
        if docker ps 2>/dev/null | grep -q ":$FRONTEND_PORT->"; then
            FRONTEND_PORT=$((FRONTEND_PORT+1))
            continue
        fi
        if command -v ss &>/dev/null && ss -tln 2>/dev/null | grep -qE ":$FRONTEND_PORT\b"; then
            FRONTEND_PORT=$((FRONTEND_PORT+1))
            continue
        fi
        if command -v netstat &>/dev/null && netstat -tln 2>/dev/null | grep -qE ":$FRONTEND_PORT\b"; then
            FRONTEND_PORT=$((FRONTEND_PORT+1))
            continue
        fi
        if command -v lsof &>/dev/null && lsof -nP -iTCP:$FRONTEND_PORT -sTCP:LISTEN >/dev/null 2>&1; then
            FRONTEND_PORT=$((FRONTEND_PORT+1))
            continue
        fi
        break
    done
    
    echo -e "  Allocated Frontend Port: ${GREEN}$FRONTEND_PORT${NC}"
    export FRONTEND_PORT

    # docker-compose with COMPOSE_PROJECT_NAME will automatically name containers like ${PROJECT_NAME}-backend-1
    docker compose down 2>/dev/null || docker-compose down 2>/dev/null || true
    docker compose up -d --build 2>/dev/null || docker-compose up -d --build
    
    echo -e "${GREEN}✅ Multiple instances deployed via Docker. Project suffix: $DIR_NAME${NC}"
    echo -e "  ${CYAN}Local:${NC}   http://localhost:$FRONTEND_PORT"
    exit 0
fi

# ── Ensure PostgreSQL is running ─────────────────
if command -v pg_isready &>/dev/null; then
    if ! pg_isready -h localhost -p "$PG_PORT" -q 2>/dev/null; then

        echo -e "${YELLOW}🐘 Starting PostgreSQL (port $PG_PORT)...${NC}"

        STARTED=false
        # Try 1: local instance managed by setup.sh
        if [ -f "$ROOT/.pgdata/PG_VERSION" ] && command -v pg_ctl &>/dev/null; then
            pg_ctl -D "$ROOT/.pgdata" -l "$ROOT/.pgdata/pg.log" start >/dev/null 2>&1 && STARTED=true
        fi
        # Try 2: brew (macOS)
        if [ "$STARTED" = false ] && command -v brew &>/dev/null; then
            brew services start postgresql@15 2>/dev/null || brew services start postgresql 2>/dev/null || true
            STARTED=true
        fi
        # Try 3: systemctl (Linux)
        if [ "$STARTED" = false ] && command -v systemctl &>/dev/null; then
            sudo systemctl start postgresql 2>/dev/null || true
            STARTED=true
        fi

        # Wait for PG to be ready
        for i in $(seq 1 10); do
            if pg_isready -h localhost -p "$PG_PORT" -q 2>/dev/null; then
                echo -e "  ${GREEN}✅ PostgreSQL ready (${i}s)${NC}"
                break
            fi
            sleep 1
            if [ "$i" -eq 10 ]; then
                echo -e "  ${RED}❌ PostgreSQL failed to start on port $PG_PORT${NC}"
                echo "  Check your PostgreSQL installation or run setup.sh first."
                exit 1
            fi
        done
    else
        echo -e "${GREEN}🐘 PostgreSQL already running (port $PG_PORT)${NC}"
    fi
else
    echo -e "${YELLOW}🐘 pg_isready not found — assuming PostgreSQL is running on port $PG_PORT${NC}"
fi

# ── Start backend ────────────────────────────────
echo -e "${YELLOW}🚀 Starting backend...${NC}"
cd "$BACKEND_DIR"
nohup env PYTHONUNBUFFERED=1 \
    PUBLIC_BASE_URL="${PUBLIC_BASE_URL:-}" \
    DATABASE_URL="$DATABASE_URL" \
    .venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8008 \
    > "$BACKEND_LOG" 2>&1 &
echo $! > "$BACKEND_PID"
wait_for_port 8008 "Backend" 10

# ── Start frontend ───────────────────────────────
echo -e "${YELLOW}🚀 Starting frontend...${NC}"
cd "$FRONTEND_DIR"
nohup node_modules/.bin/vite --host 0.0.0.0 --port 3008 \
    > "$FRONTEND_LOG" 2>&1 &
echo $! > "$FRONTEND_PID"
wait_for_port 3008 "Frontend" 8

# ── Verify proxy ─────────────────────────────────
echo -e "${YELLOW}🔍 Verifying API proxy...${NC}"
HEALTH=$(curl -s -m 3 http://localhost:3008/api/health 2>/dev/null || echo "FAIL")
if echo "$HEALTH" | grep -q "ok"; then
    echo -e "  ${GREEN}✅ Proxy working${NC}"
else
    echo -e "  ${YELLOW}⚠️  Proxy may need a moment, backend direct check:${NC}"
    curl -s http://localhost:8008/api/health && echo ""
fi

# Detect server IP
SERVER_IP=$(hostname -I 2>/dev/null | awk '{print $1}')
[ -z "$SERVER_IP" ] && SERVER_IP=$(ifconfig 2>/dev/null | grep 'inet ' | grep -v '127.0.0.1' | head -1 | awk '{print $2}')
[ -z "$SERVER_IP" ] && SERVER_IP="<your-server-ip>"

echo ""
echo -e "${GREEN}═══════════════════════════════════════${NC}"
echo -e "${GREEN}  Clawith running!${NC}"
echo -e "${GREEN}═══════════════════════════════════════${NC}"
echo ""
echo -e "  ${CYAN}Local:${NC}   http://localhost:3008"
echo -e "  ${CYAN}Network:${NC} http://${SERVER_IP}:3008"
echo -e "  ${CYAN}API:${NC}     http://${SERVER_IP}:8008"
echo ""
echo -e "  Backend log:  tail -f $BACKEND_LOG"
echo -e "  Frontend log: tail -f $FRONTEND_LOG"

