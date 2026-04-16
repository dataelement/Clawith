#!/bin/bash
# clawith — Stop Script
# Usage: ./stop.sh [--force] [--logs-only]
#
# Options:
#   --force      Skip graceful shutdown, kill immediately
#   --logs-only  Only clean up logs, don't stop services

# ─── 配置 ───
ROOT="$(cd "$(dirname "$0")" && pwd)"
DATA_DIR="$ROOT/.data"
PID_DIR="$DATA_DIR/pid"
LOG_DIR="$DATA_DIR/log"

BACKEND_PORT=8008
FRONTEND_PORT=3008
BACKEND_PID="$PID_DIR/backend.pid"
FRONTEND_PID="$PID_DIR/frontend.pid"
PGDATA="$ROOT/.pgdata"

RED='\033[0;31m'; YELLOW='\033[0;33m'; GREEN='\033[0;32m'; CYAN='\033[0;36m'; NC='\033[0m'

FORCE=false
LOGS_ONLY=false
ERRORS=()
MAX_WAIT=10

# ─── 解析参数 ───
while [[ $# -gt 0 ]]; do
    case $1 in
        --force) FORCE=true; shift ;;
        --logs-only) LOGS_ONLY=true; shift ;;
        -h|--help) echo "Usage: $0 [--force] [--logs-only]"; exit 0 ;;
        *) shift ;;
    esac
done

# ─── 颜色定义 ───
log_info()  { echo -e "${CYAN}[INFO]${NC} $1"; }
log_warn()  { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1"; }
log_ok()    { echo -e "${GREEN}[OK]${NC} $1"; }

# ─── 记录错误 ───
record_error() {
    ERRORS+=("$1")
}

# ─── 获取本机 IP ───
get_local_ip() {
    local ip=""
    local os=$(uname -s)
    
    if [ "$os" = "Linux" ]; then
        ip=$(hostname -I 2>/dev/null | awk '{print $1}')
    elif [ "$os" = "Darwin" ]; then
        ip=$(ifconfig 2>/dev/null | grep 'inet ' | grep -v '127.0.0.1' | head -1 | awk '{print $2}')
    fi
    
    echo "${ip:-127.0.0.1}"
}

# ─── 从 DATABASE_URL 提取 host 和 port ───
parse_database_url() {
    local url="$1"
    # 移除 scheme://user:pass@ -> @host:port/db
    local hostport="${url##*@}"
    # host = @ 后第一个 : 之前的部分
    local host="${hostport%%:*}"
    # port = 第一个 : 后的数字部分（到 / 或字符串结尾）
    local port="${hostport#*:}"
    port="${port%%/*}"
    
    echo "${host:-localhost}|${port:-5432}"
}

# ─── 加载环境变量 ───
load_env() {
    if [ -f "$ROOT/.env" ]; then
        set -a
        source "$ROOT/.env"
        set +a
    fi
    
    : "${DATABASE_URL:=postgresql+asyncpg://clawith:clawith@localhost:5432/clawith?ssl=disable}"
    export DATABASE_URL
    
    # 解析数据库 URL
    local parsed=$(parse_database_url "$DATABASE_URL")
    DB_HOST="${parsed%%|*}"
    DB_PORT="${parsed##*|}"
    
    # 获取本机 IP
    LOCAL_IP=$(get_local_ip)
    
    # 判断是否是外部数据库
    if [ "$DB_HOST" != "localhost" ] && [ "$DB_HOST" != "127.0.0.1" ] && [ "$DB_HOST" != "$LOCAL_IP" ]; then
        EXTERNAL_DB=true
    else
        EXTERNAL_DB=false
    fi
    
    export EXTERNAL_DB DB_HOST DB_PORT LOCAL_IP
}

# ─── 权限检查 ───
check_permissions() {
    if [ -d "$PID_DIR" ] && [ ! -w "$PID_DIR" ]; then
        log_error "PID directory $PID_DIR is not writable"
        return 1
    fi
    return 0
}

# ─── 查找 PostgreSQL 及其数据目录 ───
# 返回格式: pg_ctl_path|data_dir
find_pg_ctl_and_data() {
    local pg_ctl=""
    local pg_data=""
    
    # 方法1: Homebrew Linuxbrew (多版本)
    for ver in 18 17 16 15 14; do
        local brew_pg_ctl="$HOME/.linuxbrew/opt/postgresql@${ver}/bin/pg_ctl"
        if [ -x "$brew_pg_ctl" ]; then
            pg_ctl="$brew_pg_ctl"
            pg_data="$HOME/.linuxbrew/var/postgresql@${ver}/data"
            break
        fi
    done
    
    # 方法2: 项目本地 pg_ctl
    if [ -z "$pg_ctl" ] && [ -x "$ROOT/.pg/bin/pg_ctl" ]; then
        pg_ctl="$ROOT/.pg/bin/pg_ctl"
        # 数据目录可能在 .pgdata 或从环境变量
        if [ -d "$PGDATA" ]; then
            pg_data="$PGDATA"
        elif [ -d "$ROOT/.pgdata" ]; then
            pg_data="$ROOT/.pgdata"
        fi
    fi
    
    # 方法3: 系统路径搜索
    if [ -z "$pg_ctl" ]; then
        for dir in /usr/lib/postgresql/*/bin /opt/homebrew/opt/postgresql@*/bin; do
            if [ -x "$dir/pg_ctl" ]; then
                pg_ctl="$dir/pg_ctl"
                # 从 bin 目录推导 data 目录（相对于 prefix）
                local prefix=$(dirname "$(dirname "$dir")")
                pg_data="$prefix/data"
                break
            fi
        done
    fi
    
    # 方法4: PATH 中的 pg_ctl
    if [ -z "$pg_ctl" ] && command -v pg_ctl &>/dev/null; then
        pg_ctl="pg_ctl"
    fi
    
    # 验证 data 目录有效
    if [ -n "$pg_data" ] && [ -d "$pg_data" ] && [ -f "$pg_data/PG_VERSION" ]; then
        echo "$pg_ctl|$pg_data"
    elif [ -n "$pg_ctl" ]; then
        # 尝试从环境变量或常见位置找 data
        local alt_data=""
        for alt in "$PGDATA" "$ROOT/.pgdata" "$HOME/var/postgresql/data" "$HOME/.local/share/postgresql/data"; do
            if [ -d "$alt" ] && [ -f "$alt/PG_VERSION" ]; then
                alt_data="$alt"
                break
            fi
        done
        if [ -n "$alt_data" ]; then
            echo "$pg_ctl|$alt_data"
        else
            echo "$pg_ctl|"
        fi
    else
        echo "|"
    fi
}

# ─── 查找 PostgreSQL 可执行文件 ───
find_pg_ctl() {
    local result=$(find_pg_ctl_and_data)
    echo "${result%%|*}"
}

# ─── 查找 PostgreSQL 数据目录 ───
find_pg_data() {
    local result=$(find_pg_ctl_and_data)
    echo "${result##*|}"
}

# ─── 检测本地 PostgreSQL 端口 ───
get_local_pg_port() {
    local pg_isready="$(dirname "$1")/pg_isready"
    local pg_data="$2"
    local default_port="5432"
    
    # 优先从 postgresql.conf 读取 port
    if [ -n "$pg_data" ] && [ -f "$pg_data/postgresql.conf" ]; then
        local conf_port=$(grep -E "^\s*port\s*=" "$pg_data/postgresql.conf" 2>/dev/null | head -1 | sed 's/.*=\s*//' | tr -d ' ')
        if [ -n "$conf_port" ]; then
            echo "$conf_port"
            return
        fi
    fi
    
    # 从环境变量 PGPORT 读取
    if [ -n "$PGPORT" ]; then
        echo "$PGPORT"
        return
    fi
    
    # 尝试 pg_isready 探测常见端口
    for port in 5432 5433 5434; do
        if "$pg_isready" -h localhost -p "$port" -q 2>/dev/null; then
            echo "$port"
            return
        fi
    done
    
    echo "$default_port"
}

# ─── 添加 PostgreSQL 到 PATH ───
add_pg_path() {
    local pg_ctl=$(find_pg_ctl)
    
    if [ -n "$pg_ctl" ] && [ -d "$(dirname "$pg_ctl")" ]; then
        export PATH="$(dirname "$pg_ctl"):$PATH"
    fi
}

# ─── 检查进程是否属于本应用 ───
is_our_process() {
    local pid=$1
    local os=$(uname -s)
    
    if [ "$os" = "Linux" ]; then
        if [ -f "/proc/$pid/cmdline" ]; then
            local cmd=$(tr '\0' ' ' < "/proc/$pid/cmdline" 2>/dev/null)
            echo "$cmd" | grep -qE "(uvicorn|vite|node|npm|python.*uvicorn)" && return 0
        fi
    elif [ "$os" = "Darwin" ]; then
        local cmd=$(ps -p "$pid" -o command= 2>/dev/null)
        echo "$cmd" | grep -qE "(uvicorn|vite|node|npm)" && return 0
    fi
    
    return 1
}

# ─── 停止单个进程 ───
stop_process() {
    local pidfile=$1 name=$2
    local max_wait=${3:-$MAX_WAIT}
    
    if [ ! -f "$pidfile" ]; then
        log_warn "PID file $pidfile not found, skipping $name"
        return 0
    fi
    
    local pid=$(cat "$pidfile" 2>/dev/null)
    
    # 验证 PID 有效性
    if [ -z "$pid" ] || ! [[ "$pid" =~ ^[0-9]+$ ]]; then
        log_warn "Invalid PID in $pidfile, removing stale file"
        rm -f "$pidfile"
        return 0
    fi
    
    # 检查进程是否存在
    if ! kill -0 "$pid" 2>/dev/null; then
        log_info "Process $pid ($name) not running, removing stale PID file"
        rm -f "$pidfile"
        return 0
    fi
    
    log_info "Stopping $name (PID: $pid)..."
    
    if [ "$FORCE" = true ]; then
        kill -9 "$pid" 2>/dev/null || record_error "Failed to force kill $name (PID: $pid)"
        log_ok "Force killed $name (PID: $pid)"
    else
        kill -15 "$pid" 2>/dev/null || true
        
        local waited=0
        while kill -0 "$pid" 2>/dev/null && [ $waited -lt $max_wait ]; do
            sleep 1
            waited=$((waited + 1))
        done
        
        if kill -0 "$pid" 2>/dev/null; then
            log_warn "$name did not stop gracefully, sending SIGKILL"
            kill -9 "$pid" 2>/dev/null || record_error "Failed to SIGKILL $name (PID: $pid)"
        fi
        
        # 验证停止
        sleep 0.5
        if kill -0 "$pid" 2>/dev/null; then
            record_error "$name (PID: $pid) still running after SIGKILL"
            return 1
        fi
        
        log_ok "Stopped $name (PID: $pid)"
    fi
    
    rm -f "$pidfile"
    return 0
}

# ─── 按端口停止进程 ───
stop_by_port() {
    local port=$1 name=$2
    local os=$(uname -s)
    local pids=""
    
    # 获取端口上的进程 PID
    if command -v lsof &>/dev/null; then
        # 测试 lsof 是否有权限
        if ! lsof -ti:$port &>/dev/null 2>&1; then
            if [ "$os" = "Darwin" ]; then
                log_warn "lsof permission denied on port $port, trying sudo"
                pids=$(sudo lsof -ti:$port 2>/dev/null || true)
            else
                log_warn "lsof failed on port $port"
            fi
        else
            pids=$(lsof -ti:$port 2>/dev/null || true)
        fi
    elif command -v ss &>/dev/null; then
        pids=$(ss -tlnp 2>/dev/null | grep ":$port " | grep -oP 'pid=\K[0-9]+' || true)
    elif command -v fuser &>/dev/null; then
        fuser -k $port/tcp 2>/dev/null || true
        return 0
    fi
    
    # 处理多 PID（用空白分割）
    local IFS=$' \t\n'
    for pid in $pids; do
        [ -z "$pid" ] && continue
        [[ "$pid" =~ ^[0-9]+$ ]] || continue
        
        if is_our_process "$pid"; then
            log_info "Killing process $pid on port $port ($name)"
            kill -9 "$pid" 2>/dev/null || record_error "Failed to kill process $pid on port $port"
        else
            log_warn "Skipping unrelated process $pid on port $port"
        fi
    done
}

# ─── 检测 Docker Compose 命令 ───
detect_compose_command() {
    if ! command -v docker &>/dev/null; then
        return 1
    fi
    
    if docker compose version &>/dev/null 2>&1; then
        echo "docker compose"
        return 0
    fi
    
    if command -v docker-compose &>/dev/null; then
        echo "docker-compose"
        return 0
    fi
    
    return 1
}

# ─── 停止 Docker 容器 ───
stop_docker() {
    local compose_cmd=$(detect_compose_command)
    
    if [ -z "$compose_cmd" ]; then
        return 0
    fi
    
    local running=$(docker ps --filter 'name=clawith' --filter 'status=running' -q 2>/dev/null || true)
    
    if [ -z "$running" ]; then
        log_info "No Docker containers found for Clawith"
        return 0
    fi
    
    if [ ! -f "$ROOT/docker-compose.yml" ] && [ ! -f "$ROOT/compose.yml" ]; then
        record_error "Docker containers running but no compose file in $ROOT"
        return 1
    fi
    
    local dir_name=$(basename "$ROOT")
    [ -z "$dir_name" ] && dir_name="clawith"
    local project_name="clawith-${dir_name}"
    
    log_info "Stopping Docker containers (project: $project_name)..."
    export COMPOSE_PROJECT_NAME="$project_name"
    
    if ! "$compose_cmd" down 2>&1; then
        record_error "Failed to stop Docker containers"
        return 1
    fi
    
    log_ok "Docker containers stopped"
    return 0
}

# ─── PostgreSQL 是否在运行 ───
pg_is_running() {
    local pg_ctl=$1
    local pg_data=$2
    local pg_port=$3
    
    # 检查 socket 文件
    if [ -S "$pg_data/.s.PGSQL.${pg_port}" ]; then
        return 0
    fi
    
    # pg_ctl status
    local status=$("$pg_ctl" -D "$pg_data" status 2>&1)
    if echo "$status" | grep -qE "(server is running|PID|ready)"; then
        return 0
    fi
    
    # pg_isready 连接测试
    local pg_isready="$(dirname "$pg_ctl")/pg_isready"
    if [ -x "$pg_isready" ]; then
        if "$pg_isready" -h localhost -p "$pg_port" -q 2>/dev/null; then
            return 0
        fi
    fi
    
    return 1
}

# ─── 停止 PostgreSQL ───
stop_postgres() {
    if [ "$EXTERNAL_DB" = "true" ]; then
        log_info "External database ($DB_HOST:$DB_PORT), skipping local PostgreSQL"
        return 0
    fi
    
    add_pg_path
    local pg_ctl=$(find_pg_ctl)
    local pg_data=$(find_pg_data)
    
    if [ -z "$pg_ctl" ]; then
        log_warn "pg_ctl not found, cannot stop local PostgreSQL"
        return 0
    fi
    
    if [ -z "$pg_data" ] || [ ! -d "$pg_data" ]; then
        log_info "No local PostgreSQL data directory found, skipping"
        return 0
    fi
    
    # 检测本地 PostgreSQL 端口
    local local_pg_port=$(get_local_pg_port "$pg_ctl" "$pg_data")
    
    log_info "Stopping PostgreSQL (data: $pg_data, port: $local_pg_port)..."
    
    # 检查是否在运行
    if pg_is_running "$pg_ctl" "$pg_data" "$local_pg_port"; then
        if [ "$FORCE" = true ]; then
            "$pg_ctl" -D "$pg_data" -m fast stop 2>/dev/null || true
            sleep 1
            "$pg_ctl" -D "$pg_data" -m immediate stop 2>/dev/null || true
        else
            "$pg_ctl" -D "$pg_data" -m fast stop 2>/dev/null
        fi
        
        # 验证
        sleep 1
        if pg_is_running "$pg_ctl" "$pg_data" "$local_pg_port"; then
            record_error "PostgreSQL still running after stop"
            return 1
        fi
        
        log_ok "PostgreSQL stopped"
    else
        log_info "PostgreSQL not running"
    fi
    
    return 0
}

# ─── 服务状态预检 ───
pre_check() {
    log_info "=== Pre-check: Services Status ==="
    
    # Docker
    if command -v docker &>/dev/null; then
        local docker_count=$(docker ps --filter 'name=clawith' --filter 'status=running' -q 2>/dev/null | wc -l)
        if [ "$docker_count" -gt 0 ]; then
            log_info "  Docker: $docker_count container(s) running"
        fi
    fi
    
    # PID 文件
    for pidfile in "$BACKEND_PID" "$FRONTEND_PID"; do
        if [ -f "$pidfile" ]; then
            local pid=$(cat "$pidfile" 2>/dev/null)
            if kill -0 "$pid" 2>/dev/null; then
                log_info "  $(basename "$pidfile" .pid): PID $pid running"
            else
                log_info "  $(basename "$pidfile" .pid): stale PID file"
            fi
        fi
    done
    
    # 端口
    for port in $BACKEND_PORT $FRONTEND_PORT; do
        if command -v lsof &>/dev/null; then
            local pids=$(lsof -ti:$port 2>/dev/null || true)
            if [ -n "$pids" ]; then
                log_info "  Port $port: in use by PIDs $pids"
            fi
        fi
    done
    
    echo ""
}

# ─── 清理日志 ───
cleanup_logs() {
    if [ ! -d "$LOG_DIR" ]; then
        return 0
    fi
    
    local log_count=$(find "$LOG_DIR" -maxdepth 1 -type f -name "*.log" 2>/dev/null | wc -l | tr -d ' ')
    
    if [ -z "$log_count" ] || [ "$log_count" -eq 0 ]; then
        log_info "No log files to clean"
        return 0
    fi
    
    local size=$(du -sh "$LOG_DIR" 2>/dev/null | cut -f1 || echo "unknown")
    log_info "Logs: $LOG_DIR (${size}, ${log_count} files)"
    
    # 只清理 7 天前的旧日志
    local old_logs=$(find "$LOG_DIR" -maxdepth 1 -type f -name "*.log" -mtime +7 2>/dev/null)
    
    if [ -z "$(echo "$old_logs" | tr -d ' ')" ]; then
        log_info "No logs older than 7 days"
        return 0
    fi
    
    # 创建备份目录
    local backup_dir="$LOG_DIR/old/$(date +%Y%m%d_%H%M%S)"
    mkdir -p "$backup_dir"
    
    # 移动旧日志（不需要 sync，mv 会正确处理）
    local IFS=$'\n'
    for log in $old_logs; do
        [ -z "$log" ] && continue
        if [ -f "$log" ]; then
            if mv "$log" "$backup_dir/"; then
                log_info "  Moved: $(basename "$log")"
            else
                record_error "Failed to move log: $(basename "$log")"
            fi
        fi
    done
    
    # 清理旧目录
    if [ -d "$LOG_DIR/old" ]; then
        rm -rf "$LOG_DIR/old" 2>/dev/null || log_warn "Failed to remove old logs directory"
    fi
    
    log_ok "Log cleanup completed"
    return 0
}

# ─── 主流程 ───
main() {
    echo -e "${YELLOW}🛑 Stopping clawith services...${NC}"
    echo ""
    
    load_env
    check_permissions || record_error "Permission check failed"
    
    if [ "$LOGS_ONLY" = true ]; then
        cleanup_logs
        echo ""
        if [ ${#ERRORS[@]} -gt 0 ]; then
            echo -e "${RED}⚠️  Errors during cleanup:${NC}"
            for err in "${ERRORS[@]}"; do
                echo -e "  ${RED}•${NC} $err"
            done
            exit 1
        else
            log_ok "Log cleanup completed"
            exit 0
        fi
    fi
    
    pre_check
    
    log_info "=== Docker Containers ==="
    stop_docker
    echo ""
    
    log_info "=== Local Services (PID files) ==="
    stop_process "$BACKEND_PID" "Backend" 5
    stop_process "$FRONTEND_PID" "Frontend" 3
    echo ""
    
    log_info "=== Port Cleanup ==="
    stop_by_port $BACKEND_PORT "backend"
    stop_by_port $FRONTEND_PORT "frontend"
    echo ""
    
    log_info "=== PostgreSQL ==="
    stop_postgres
    echo ""
    
    log_info "=== Log Cleanup ==="
    cleanup_logs
    echo ""
    
    # 最终状态检查
    local remaining=""
    for pidfile in "$BACKEND_PID" "$FRONTEND_PID"; do
        [ -f "$pidfile" ] && remaining="$remaining $(basename "$pidfile")"
    done
    
    if [ ${#ERRORS[@]} -gt 0 ] || [ -n "$remaining" ]; then
        [ -n "$remaining" ] && record_error "Failed to stop:$remaining"
        
        echo -e "${RED}⚠️  Errors encountered:${NC}"
        for err in "${ERRORS[@]}"; do
            echo -e "  ${RED}•${NC} $err"
        done
        echo ""
        exit 1
    else
        echo -e "${GREEN}✅ All services stopped successfully${NC}"
        exit 0
    fi
}

main "$@"
