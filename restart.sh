#!/bin/bash
# Clawith — Restart Script
# Usage: ./restart.sh [--source]
#   --source  Force source (non-Docker) mode even if Docker is available

set -e

# ═══════════════════════════════════════════════════════
# 配置
# ═══════════════════════════════════════════════════════
ROOT="$(cd "$(dirname "$0")" && pwd)"
DATA_DIR="$ROOT/.data"
PID_DIR="$DATA_DIR/pid"
LOG_DIR="$DATA_DIR/log"

BACKEND_DIR="$ROOT/backend"
FRONTEND_DIR="$ROOT/frontend"

BACKEND_PORT=8008
FRONTEND_PORT=3008
FRONTEND_LOG="$LOG_DIR/frontend.log"
BACKEND_LOG="$LOG_DIR/backend.log"
BACKEND_PID="$PID_DIR/backend.pid"
FRONTEND_PID="$PID_DIR/frontend.pid"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[0;33m'; CYAN='\033[0;36m'; NC='\033[0m'

# Parse arguments
FORCE_SOURCE=false
for arg in "$@"; do
    case $arg in
        --source) FORCE_SOURCE=true ;;
    esac
done

# ═══════════════════════════════════════════════════════
# 初始化目录
# ═══════════════════════════════════════════════════════
init_dirs() {
    mkdir -p "$PID_DIR" "$LOG_DIR"
}

# ═══════════════════════════════════════════════════════
# 加载环境变量
# ═══════════════════════════════════════════════════════
load_env() {
    if [ -f "$ROOT/.env" ]; then
        set -a
        source "$ROOT/.env"
        set +a
    fi

    : "${DATABASE_URL:=postgresql+asyncpg://clawith:clawith@localhost:5432/clawith?ssl=disable}"
    export DATABASE_URL

    # Parse host and port from DATABASE_URL regardless of hostname
    # Format: postgresql+asyncpg://user:pass@host:port/dbname?...
    _db_hostpart=$(echo "$DATABASE_URL" | sed 's|.*://[^@]*@||' | sed 's|/.*||' | sed 's|?.*||')
    PG_HOST="${_db_hostpart%%:*}"
    PG_PORT="${_db_hostpart##*:}"
    PG_PORT=${PG_PORT:-5432}
    export PG_HOST PG_PORT

    # Detect external (non-localhost) database
    EXTERNAL_DB=false
    if [ "$PG_HOST" != "localhost" ] && [ "$PG_HOST" != "127.0.0.1" ]; then
        EXTERNAL_DB=true
    fi
    export EXTERNAL_DB
}

# ═══════════════════════════════════════════════════════
# 清理旧进程
# ═══════════════════════════════════════════════════════
cleanup() {
    echo -e "${YELLOW}🔄 Stopping existing services...${NC}"

    for pidfile in "$BACKEND_PID" "$FRONTEND_PID"; do
        if [ -f "$pidfile" ]; then
            kill -9 "$(cat "$pidfile")" 2>/dev/null || true
            rm -f "$pidfile"
        fi
    done

    for port in $BACKEND_PORT $FRONTEND_PORT; do
        if command -v lsof &>/dev/null; then
            lsof -ti:$port | xargs kill -9 2>/dev/null || true
        elif command -v fuser &>/dev/null; then
            fuser -k $port/tcp 2>/dev/null || true
        fi
    done

    sleep 1
}

# ═══════════════════════════════════════════════════════
# 等待端口就绪
# ═══════════════════════════════════════════════════════
wait_for_port() {
    local port=$1 name=$2 max=${3:-10}
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

# ═══════════════════════════════════════════════════════
# 添加 PostgreSQL 到 PATH
# ═══════════════════════════════════════════════════════
add_pg_path() {
    if [ -d "$ROOT/.pg/bin" ]; then
        export PATH="$ROOT/.pg/bin:$PATH"
    fi
    for dir in /www/server/pgsql/bin /usr/local/pgsql/bin; do
        if [ -x "$dir/pg_isready" ] && ! command -v pg_isready &>/dev/null; then
            export PATH="$dir:$PATH"
        fi
    done
}

# ═══════════════════════════════════════════════════════
# 启动 PostgreSQL
# ═══════════════════════════════════════════════════════
start_postgres() {
    # Skip local PostgreSQL management when using an external database
    if [ "$EXTERNAL_DB" = true ]; then
        echo -e "${GREEN}🐘 Using external database at ${PG_HOST}:${PG_PORT} — skipping local PostgreSQL startup${NC}"
        return 0
    fi

    add_pg_path

    if command -v pg_isready &>/dev/null; then
        if ! pg_isready -h localhost -p "$PG_PORT" -q 2>/dev/null; then
            echo -e "${YELLOW}🐘 Starting PostgreSQL (port $PG_PORT)...${NC}"

            STARTED=false
            [ -f "$ROOT/.pgdata/PG_VERSION" ] && command -v pg_ctl &>/dev/null && \
                pg_ctl -D "$ROOT/.pgdata" -l "$ROOT/.pgdata/pg.log" start >/dev/null 2>&1 && STARTED=true

            if [ "$STARTED" = false ] && command -v brew &>/dev/null; then
                brew services start postgresql@15 2>/dev/null || brew services start postgresql 2>/dev/null || true
                STARTED=true
            fi

            if [ "$STARTED" = false ] && command -v systemctl &>/dev/null; then
                sudo systemctl start postgresql 2>/dev/null || true
                STARTED=true
            fi

            for i in $(seq 1 10); do
                if pg_isready -h localhost -p "$PG_PORT" -q 2>/dev/null; then
                    echo -e "  ${GREEN}✅ PostgreSQL ready (${i}s)${NC}"
                    return 0
                fi
                sleep 1
            done
            echo -e "  ${RED}❌ PostgreSQL failed to start on port $PG_PORT${NC}"
            exit 1
        else
            echo -e "${GREEN}🐘 PostgreSQL already running (port $PG_PORT)${NC}"
        fi
    else
        echo -e "${YELLOW}🐘 pg_isready not found — assuming PostgreSQL is running${NC}"
    fi
}

# ═══════════════════════════════════════════════════════
# 启动后端
# ═══════════════════════════════════════════════════════
start_backend() {
    echo -e "${YELLOW}🚀 Starting backend...${NC}"
    cd "$BACKEND_DIR"

    # Auto-run schema migrations via alembic
    echo -e "${YELLOW}🔄 Running schema migrations...${NC}"
    .venv/bin/alembic upgrade head 2>/dev/null || true

    # Auto-run data migrations (idempotent)
    echo -e "${YELLOW}🔄 Running data migrations...${NC}"
    .venv/bin/python -m app.scripts.migrate_schedules_to_triggers || true
    nohup env PYTHONUNBUFFERED=1 \
        PUBLIC_BASE_URL="${PUBLIC_BASE_URL:-}" \
        DATABASE_URL="$DATABASE_URL" \
        .venv/bin/uvicorn app.main:app --host 0.0.0.0 --port $BACKEND_PORT \
        > "$BACKEND_LOG" 2>&1 &
    echo $! > "$BACKEND_PID"
    wait_for_port $BACKEND_PORT "Backend" 10
}

# ═══════════════════════════════════════════════════════
# 启动前端
# ═══════════════════════════════════════════════════════
start_frontend() {
    echo -e "${YELLOW}🚀 Starting frontend...${NC}"
    cd "$FRONTEND_DIR"
    nohup node_modules/.bin/vite --host 0.0.0.0 --port $FRONTEND_PORT \
        > "$FRONTEND_LOG" 2>&1 &
    echo $! > "$FRONTEND_PID"
    wait_for_port $FRONTEND_PORT "Frontend" 8
}

# ═══════════════════════════════════════════════════════
# 验证代理
# ═══════════════════════════════════════════════════════
verify_proxy() {
    echo -e "${YELLOW}🔍 Verifying API proxy...${NC}"
    HEALTH=$(curl -s -m 3 http://localhost:$FRONTEND_PORT/api/health 2>/dev/null || echo "FAIL")
    if echo "$HEALTH" | grep -q "ok"; then
        echo -e "  ${GREEN}✅ Proxy working${NC}"
    else
        echo -e "  ${YELLOW}⚠️  Proxy may need a moment, backend direct check:${NC}"
        curl -s http://localhost:$BACKEND_PORT/api/health && echo ""
    fi
}

# ═══════════════════════════════════════════════════════
# 打印访问信息
# ═══════════════════════════════════════════════════════
print_info() {
    SERVER_IP=$(hostname -I 2>/dev/null | awk '{print $1}')
    [ -z "$SERVER_IP" ] && SERVER_IP=$(ifconfig 2>/dev/null | grep 'inet ' | grep -v '127.0.0.1' | head -1 | awk '{print $2}')
    [ -z "$SERVER_IP" ] && SERVER_IP="<your-server-ip>"

    echo ""
    echo -e "${GREEN}═══════════════════════════════════════${NC}"
    echo -e "${GREEN}  Clawith running!${NC}"
    echo -e "${GREEN}═══════════════════════════════════════${NC}"
    echo ""
    echo -e "  ${CYAN}Local:${NC}   http://localhost:$FRONTEND_PORT"
    echo -e "  ${CYAN}Network:${NC} http://${SERVER_IP}:$FRONTEND_PORT"
    echo -e "  ${CYAN}API:${NC}     http://${SERVER_IP}:$BACKEND_PORT"
    echo ""
    echo -e "  Backend log:  tail -f $BACKEND_LOG"
    echo -e "  Frontend log: tail -f $FRONTEND_LOG"
}

# ═══════════════════════════════════════════════════════
# Docker 模式
# ═══════════════════════════════════════════════════════
run_docker_mode() {
    if [ "$FORCE_SOURCE" = true ]; then
        return 1
    fi
    # Only switch to Docker mode when there are RUNNING Clawith containers
    if command -v docker &>/dev/null && docker ps --filter 'name=clawith' --filter 'status=running' -q 2>/dev/null | grep -q .; then
        echo -e "${YELLOW}Detected running Docker containers. Starting in Docker mode...${NC}"
        echo -e "  ${YELLOW}Tip: use --source to force source (non-Docker) mode.${NC}"
        DIR_NAME=$(basename "$(dirname "$ROOT")")
        [ -z "$DIR_NAME" ] && DIR_NAME="custom"

        PROJECT_NAME="clawith-${DIR_NAME}"
        echo -e "  Using project name: ${GREEN}$PROJECT_NAME${NC}"
        export COMPOSE_PROJECT_NAME="$PROJECT_NAME"

        cd "$ROOT"

        # 查找空闲端口
        FRONTEND_PORT=3008
        while true; do
            if docker ps 2>/dev/null | grep -q ":$FRONTEND_PORT->"; then
                FRONTEND_PORT=$((FRONTEND_PORT+1)); continue
            fi
            if command -v ss &>/dev/null && ss -tln 2>/dev/null | grep -qE ":$FRONTEND_PORT\b"; then
                FRONTEND_PORT=$((FRONTEND_PORT+1)); continue
            fi
            if command -v lsof &>/dev/null && lsof -nP -iTCP:$FRONTEND_PORT -sTCP:LISTEN >/dev/null 2>&1; then
                FRONTEND_PORT=$((FRONTEND_PORT+1)); continue
            fi
            break
        done

        echo -e "  Allocated Frontend Port: ${GREEN}$FRONTEND_PORT${NC}"
        export FRONTEND_PORT

        docker compose down 2>/dev/null || docker-compose down 2>/dev/null || true
        docker compose up -d --build 2>/dev/null || docker-compose up -d --build

        echo -e "${GREEN}✅ Deployed via Docker. Port: $FRONTEND_PORT${NC}"
        echo -e "  ${CYAN}Local:${NC} http://localhost:$FRONTEND_PORT"
        exit 0
    fi
}

# ═══════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════
main() {
    init_dirs
    load_env
    run_docker_mode || true
    # 启动本地模式，如果docker 不存在
    cleanup
    start_postgres
    start_backend
    start_frontend
    verify_proxy
    print_info
}

main "$@"