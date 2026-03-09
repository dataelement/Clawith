#!/usr/bin/env bash
# ────────────────────────────────────────────────
# Clawith — First-time Setup Script
# Sets up backend, frontend, database, and seed data.
# ────────────────────────────────────────────────
set -e

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[0;33m'; CYAN='\033[0;36m'; NC='\033[0m'
ROOT="$(cd "$(dirname "$0")" && pwd)"

# Parse arguments
INSTALL_DEV=false
for arg in "$@"; do
    case $arg in
        --dev) INSTALL_DEV=true ;;
    esac
done

# --- Helper: detect server IP ---
get_server_ip() {
    # Try hostname -I (Linux), then ifconfig (macOS), then fallback
    local ip
    ip=$(hostname -I 2>/dev/null | awk '{print $1}')
    [ -z "$ip" ] && ip=$(ifconfig 2>/dev/null | grep 'inet ' | grep -v '127.0.0.1' | head -1 | awk '{print $2}')
    [ -z "$ip" ] && ip="<your-server-ip>"
    echo "$ip"
}

# --- Optional package mirror overrides ---
PIP_INSTALL_ARGS=()
if [ -n "${CLAWITH_PIP_INDEX_URL:-}" ]; then
    PIP_INSTALL_ARGS+=(--index-url "$CLAWITH_PIP_INDEX_URL")
fi
if [ -n "${CLAWITH_PIP_TRUSTED_HOST:-}" ]; then
    PIP_INSTALL_ARGS+=(--trusted-host "$CLAWITH_PIP_TRUSTED_HOST")
fi
NPM_MIRROR="--registry https://registry.npmmirror.com"

echo ""
echo -e "${CYAN}═══════════════════════════════════════${NC}"
echo -e "${CYAN}  🦞 Clawith — First-time Setup${NC}"
echo -e "${CYAN}═══════════════════════════════════════${NC}"
echo ""

# ── 1. Environment file ──────────────────────────
echo -e "${YELLOW}[1/6]${NC} Checking environment file..."
if [ ! -f "$ROOT/.env" ]; then
    cp "$ROOT/.env.example" "$ROOT/.env"
    echo -e "  ${GREEN}✓${NC} Created .env from .env.example"
    echo -e "  ${YELLOW}⚠${NC}  Please edit .env to set SECRET_KEY and JWT_SECRET_KEY before production use."
else
    echo -e "  ${GREEN}✓${NC} .env already exists"
fi

# ── 2. PostgreSQL setup ──────────────────────────
echo ""
echo -e "${YELLOW}[2/6]${NC} Setting up PostgreSQL..."

# --- Helper: find psql binary ---
find_psql() {
    # Check PATH first
    if command -v psql &>/dev/null; then
        command -v psql
        return 0
    fi
    # Search common non-standard locations
    local search_paths=(
        "/www/server/pgsql/bin"
        "/usr/local/pgsql/bin"
        "/usr/lib/postgresql/15/bin"
        "/usr/lib/postgresql/14/bin"
        "/usr/lib/postgresql/16/bin"
        "/opt/homebrew/opt/postgresql@15/bin"
        "/opt/homebrew/opt/postgresql/bin"
    )
    for dir in "${search_paths[@]}"; do
        if [ -x "$dir/psql" ]; then
            echo "$dir"
            return 0
        fi
    done
    return 1
}

# --- Helper: find a free port starting from $1 ---
find_free_port() {
    local port=$1
    while ss -tlnp 2>/dev/null | grep -q ":${port} " || \
          lsof -iTCP:${port} -sTCP:LISTEN 2>/dev/null | grep -q LISTEN; do
        echo -e "  ${YELLOW}⚠${NC}  Port $port is in use, trying $((port+1))..."
        port=$((port+1))
    done
    echo "$port"
}

PG_PORT=5432
PG_MANAGED_BY_US=false

if PG_BIN_DIR=$(find_psql 2>/dev/null); then
    # If find_psql returned a directory (not a full path), add to PATH
    if [ -d "$PG_BIN_DIR" ]; then
        export PATH="$PG_BIN_DIR:$PATH"
    fi
    echo -e "  ${GREEN}✓${NC} Found psql: $(which psql)"

    # Check if PG is running and we can connect
    if pg_isready -h localhost -p 5432 -q 2>/dev/null; then
        echo -e "  ${GREEN}✓${NC} PostgreSQL is running on port 5432"
        PG_PORT=5432

        # Try to create role and database
        ROLE_EXISTS=false
        if psql -h localhost -p $PG_PORT -U "$USER" -d postgres -tAc "SELECT 1 FROM pg_roles WHERE rolname='clawith'" 2>/dev/null | grep -q 1; then
            ROLE_EXISTS=true
            echo -e "  ${GREEN}✓${NC} Role 'clawith' already exists"
        elif sudo -u postgres psql -tAc "SELECT 1 FROM pg_roles WHERE rolname='clawith'" 2>/dev/null | grep -q 1; then
            ROLE_EXISTS=true
            echo -e "  ${GREEN}✓${NC} Role 'clawith' already exists"
        fi

        if [ "$ROLE_EXISTS" = false ]; then
            # Try 1: as current user
            if createuser -h localhost -p $PG_PORT clawith 2>/dev/null; then
                psql -h localhost -p $PG_PORT -U "$USER" -d postgres -c "ALTER ROLE clawith WITH LOGIN PASSWORD 'clawith';" &>/dev/null
                echo -e "  ${GREEN}✓${NC} Created PostgreSQL role: clawith"
            # Try 2: via sudo -u postgres (standard Linux setup)
            elif sudo -u postgres createuser clawith 2>/dev/null && \
                 sudo -u postgres psql -c "ALTER ROLE clawith WITH LOGIN PASSWORD 'clawith';" &>/dev/null; then
                echo -e "  ${GREEN}✓${NC} Created PostgreSQL role: clawith (via sudo)"
            else
                echo -e "  ${YELLOW}⚠${NC}  Could not create role in existing PG — will init a local instance"
                PG_BIN_DIR=""  # Force local PG setup below
            fi
        fi

        if [ -n "$PG_BIN_DIR" ] || command -v psql &>/dev/null; then
            DB_EXISTS=false
            if psql -h localhost -p $PG_PORT -U "$USER" -lqt 2>/dev/null | cut -d\| -f1 | grep -qw clawith; then
                DB_EXISTS=true
            elif sudo -u postgres psql -lqt 2>/dev/null | cut -d\| -f1 | grep -qw clawith; then
                DB_EXISTS=true
            fi

            if [ "$DB_EXISTS" = true ]; then
                echo -e "  ${GREEN}✓${NC} Database 'clawith' already exists"
            else
                if createdb -h localhost -p $PG_PORT -O clawith clawith 2>/dev/null || \
                   sudo -u postgres createdb -O clawith clawith 2>/dev/null; then
                    echo -e "  ${GREEN}✓${NC} Created database: clawith"
                fi
            fi
        fi
    else
        echo -e "  ${YELLOW}⚠${NC}  PostgreSQL binaries found but service is not running on port 5432"
        echo "  Will set up a local instance..."
        PG_BIN_DIR=""  # Force local PG setup below
    fi
fi

# --- Local PG instance: install + initdb if needed ---
if [ -z "$PG_BIN_DIR" ] && ! (PGPASSWORD=clawith psql -h localhost -p 5432 -U clawith -d clawith -c "SELECT 1" &>/dev/null); then
    echo -e "  ${CYAN}↓${NC} No usable PostgreSQL found — setting up a local instance..."
    PG_MANAGED_BY_US=true
    PGDATA="$ROOT/.pgdata"
    PG_LOCAL="$ROOT/.pg"

    # Strategy 1: Install via system package manager (most reliable)
    if [ ! -x "$PG_LOCAL/bin/psql" ]; then
        INSTALLED_VIA_PKG=false
        OS=$(uname -s | tr '[:upper:]' '[:lower:]')

        if [ "$OS" = "darwin" ]; then
            if command -v brew &>/dev/null; then
                echo "  Installing PostgreSQL via Homebrew..."
                brew install postgresql@15 2>/dev/null && brew services start postgresql@15 2>/dev/null && INSTALLED_VIA_PKG=true
            else
                echo -e "  ${YELLOW}⚠${NC}  On macOS, please install Homebrew first, then:"
                echo "    brew install postgresql@15 && brew services start postgresql@15"
                echo "    Then re-run: bash setup.sh"
                exit 1
            fi
        elif [ "$OS" = "linux" ]; then
            # Check if sudo is available
            CAN_SUDO=false
            if command -v sudo &>/dev/null; then
                if sudo -n true 2>/dev/null || (echo "" | sudo -S true 2>/dev/null); then
                    CAN_SUDO=true
                fi
            fi

            if [ "$CAN_SUDO" = true ]; then
                if command -v apt-get &>/dev/null; then
                    echo "  Installing PostgreSQL via apt..."
                    sudo apt-get update -qq 2>/dev/null
                    sudo apt-get install -y -qq postgresql postgresql-client 2>/dev/null && INSTALLED_VIA_PKG=true
                elif command -v yum &>/dev/null; then
                    echo "  Installing PostgreSQL via yum..."
                    sudo yum install -y -q postgresql-server postgresql 2>/dev/null && \
                    sudo postgresql-setup --initdb 2>/dev/null; \
                    sudo systemctl start postgresql 2>/dev/null && INSTALLED_VIA_PKG=true
                elif command -v dnf &>/dev/null; then
                    echo "  Installing PostgreSQL via dnf..."
                    sudo dnf install -y -q postgresql-server postgresql 2>/dev/null && \
                    sudo postgresql-setup --initdb 2>/dev/null; \
                    sudo systemctl start postgresql 2>/dev/null && INSTALLED_VIA_PKG=true
                fi
            fi
        fi

        if [ "$INSTALLED_VIA_PKG" = true ]; then
            echo -e "  ${GREEN}✓${NC} PostgreSQL installed via package manager"
            # Re-find psql after install
            if PG_BIN_DIR=$(find_psql 2>/dev/null); then
                if [ -d "$PG_BIN_DIR" ]; then
                    export PATH="$PG_BIN_DIR:$PATH"
                fi
            fi
            # Wait for PG to be ready
            for i in $(seq 1 10); do
                if pg_isready -h localhost -p 5432 -q 2>/dev/null; then
                    PG_PORT=5432
                    break
                fi
                sleep 1
            done
            # Create role and database
            if command -v psql &>/dev/null; then
                if ! psql -h localhost -p $PG_PORT -U postgres -tAc "SELECT 1 FROM pg_roles WHERE rolname='clawith'" 2>/dev/null | grep -q 1; then
                    sudo -u postgres createuser clawith 2>/dev/null || createuser -h localhost -p $PG_PORT clawith 2>/dev/null || true
                    sudo -u postgres psql -c "ALTER ROLE clawith WITH LOGIN PASSWORD 'clawith';" 2>/dev/null || \
                        psql -h localhost -p $PG_PORT -U postgres -c "ALTER ROLE clawith WITH LOGIN PASSWORD 'clawith';" 2>/dev/null || true
                    echo -e "  ${GREEN}✓${NC} Created role: clawith"
                fi
                if ! psql -h localhost -p $PG_PORT -U postgres -lqt 2>/dev/null | cut -d\| -f1 | grep -qw clawith; then
                    sudo -u postgres createdb -O clawith clawith 2>/dev/null || createdb -h localhost -p $PG_PORT -O clawith clawith 2>/dev/null || true
                    echo -e "  ${GREEN}✓${NC} Created database: clawith"
                fi
                PG_MANAGED_BY_US=false  # System manages PG now
            fi
        fi
    fi

    # Strategy 2: Use system PG binaries from non-standard paths for user-space initdb
    if [ "$INSTALLED_VIA_PKG" != true ] || [ "$PG_MANAGED_BY_US" = true ]; then
        if [ ! -x "$PG_LOCAL/bin/initdb" ]; then
            # Try to link from common system paths
            for dir in /www/server/pgsql /usr/local/pgsql /usr/lib/postgresql/15 /usr/lib/postgresql/14 /usr/lib/postgresql/16; do
                if [ -x "$dir/bin/initdb" ]; then
                    mkdir -p "$PG_LOCAL"
                    ln -sf "$dir/bin" "$PG_LOCAL/bin" 2>/dev/null || cp -r "$dir/bin" "$PG_LOCAL/bin" 2>/dev/null
                    if [ -d "$dir/lib" ]; then
                        ln -sf "$dir/lib" "$PG_LOCAL/lib" 2>/dev/null || cp -r "$dir/lib" "$PG_LOCAL/lib" 2>/dev/null
                    fi
                    if [ -d "$dir/share" ]; then
                        ln -sf "$dir/share" "$PG_LOCAL/share" 2>/dev/null || cp -r "$dir/share" "$PG_LOCAL/share" 2>/dev/null
                    fi
                    echo -e "  ${GREEN}✓${NC} Found system PG binaries at $dir"
                    break
                fi
            done
        fi

        if [ -x "$PG_LOCAL/bin/initdb" ]; then
            export PATH="$PG_LOCAL/bin:$PATH"
            export LD_LIBRARY_PATH="$PG_LOCAL/lib:${LD_LIBRARY_PATH:-}"

            # Find a free port
            PG_PORT=$(find_free_port 5432)

            # Initialize data directory
            if [ ! -f "$PGDATA/PG_VERSION" ]; then
                echo "  Initializing database cluster..."
                initdb -D "$PGDATA" -U postgres --auth=trust -E UTF8 --locale=C >/dev/null 2>&1
                # Configure port (handle both GNU and BSD sed)
                sed -i "s/#port = 5432/port = $PG_PORT/" "$PGDATA/postgresql.conf" 2>/dev/null || \
                sed -i '' "s/#port = 5432/port = $PG_PORT/" "$PGDATA/postgresql.conf" 2>/dev/null
                sed -i "s/#listen_addresses = 'localhost'/listen_addresses = 'localhost'/" "$PGDATA/postgresql.conf" 2>/dev/null || \
                sed -i '' "s/#listen_addresses = 'localhost'/listen_addresses = 'localhost'/" "$PGDATA/postgresql.conf" 2>/dev/null
                echo -e "  ${GREEN}✓${NC} Database cluster initialized (port $PG_PORT)"
            else
                # Read configured port from existing cluster
                PG_PORT=$(grep "^port = " "$PGDATA/postgresql.conf" 2>/dev/null | awk '{print $3}')
                PG_PORT=${PG_PORT:-5432}
                echo -e "  ${GREEN}✓${NC} Existing data directory found (port $PG_PORT)"
            fi

            # Start PostgreSQL
            if ! pg_isready -h localhost -p "$PG_PORT" -q 2>/dev/null; then
                pg_ctl -D "$PGDATA" -l "$PGDATA/pg.log" start >/dev/null 2>&1
                sleep 2
                if pg_isready -h localhost -p "$PG_PORT" -q 2>/dev/null; then
                    echo -e "  ${GREEN}✓${NC} PostgreSQL started on port $PG_PORT"
                else
                    echo -e "  ${RED}✗${NC} Failed to start PostgreSQL. Check $PGDATA/pg.log"
                    exit 1
                fi
            else
                echo -e "  ${GREEN}✓${NC} PostgreSQL already running on port $PG_PORT"
            fi

            # Create role and database
            if ! psql -h localhost -p "$PG_PORT" -U postgres -tAc "SELECT 1 FROM pg_roles WHERE rolname='clawith'" 2>/dev/null | grep -q 1; then
                createuser -h localhost -p "$PG_PORT" -U postgres clawith 2>/dev/null || true
                psql -h localhost -p "$PG_PORT" -U postgres -c "ALTER ROLE clawith WITH LOGIN PASSWORD 'clawith';" &>/dev/null
                echo -e "  ${GREEN}✓${NC} Created role: clawith"
            fi
            if ! psql -h localhost -p "$PG_PORT" -U postgres -lqt 2>/dev/null | cut -d\| -f1 | grep -qw clawith; then
                createdb -h localhost -p "$PG_PORT" -U postgres -O clawith clawith 2>/dev/null
                echo -e "  ${GREEN}✓${NC} Created database: clawith"
            fi
        else
            echo -e "  ${RED}✗${NC} Could not set up PostgreSQL automatically."
            echo ""
            echo "  Please install PostgreSQL manually:"
            echo ""
            echo "    Ubuntu/Debian:  sudo apt install postgresql"
            echo "    CentOS/RHEL:    sudo yum install postgresql-server"
            echo "    macOS:          brew install postgresql@15"
            echo ""
            echo "  Then re-run: bash setup.sh"
            exit 1
        fi
    fi
fi

# Ensure DATABASE_URL is correct in .env
DB_URL="postgresql+asyncpg://clawith:clawith@localhost:${PG_PORT}/clawith?ssl=disable"
if grep -q "^DATABASE_URL=" "$ROOT/.env" 2>/dev/null; then
    # Update existing DATABASE_URL
    sed -i "s|^DATABASE_URL=.*|DATABASE_URL=${DB_URL}|" "$ROOT/.env" 2>/dev/null || \
    sed -i '' "s|^DATABASE_URL=.*|DATABASE_URL=${DB_URL}|" "$ROOT/.env" 2>/dev/null
elif grep -q "^# DATABASE_URL=" "$ROOT/.env" 2>/dev/null; then
    # Uncomment and set
    sed -i "s|^# DATABASE_URL=.*|DATABASE_URL=${DB_URL}|" "$ROOT/.env" 2>/dev/null || \
    sed -i '' "s|^# DATABASE_URL=.*|DATABASE_URL=${DB_URL}|" "$ROOT/.env" 2>/dev/null
else
    echo "DATABASE_URL=${DB_URL}" >> "$ROOT/.env"
fi
echo -e "  ${GREEN}✓${NC} DATABASE_URL set (port $PG_PORT)"

# ── 3. Backend setup ─────────────────────────────
echo ""
echo -e "${YELLOW}[3/6]${NC} Setting up backend..."
cd "$ROOT/backend"

if [ ! -d ".venv" ]; then
    echo "  Creating Python virtual environment..."
    python3 -m venv .venv
    echo -e "  ${GREEN}✓${NC} Virtual environment created"
fi

if [ "$INSTALL_DEV" = true ]; then
    PIP_TARGET=".[dev]"
    echo "  Installing dependencies with dev extras (this may take 2-5 minutes)..."
else
    PIP_TARGET="."
    echo "  Installing dependencies (this may take 1-2 minutes)..."
fi
if .venv/bin/pip install -e "$PIP_TARGET" "${PIP_INSTALL_ARGS[@]}" 2>&1; then
    echo -e "  ${GREEN}✓${NC} Backend dependencies installed"
else
    echo -e "  ${RED}✗${NC} Failed to install backend dependencies."
    echo "  Try manually: cd backend && .venv/bin/pip install -e '$PIP_TARGET'"
    exit 1
fi

# ── 4. Frontend setup ────────────────────────────
echo ""
echo -e "${YELLOW}[4/6]${NC} Setting up frontend..."
cd "$ROOT/frontend"

if [ ! -d "node_modules" ]; then
    echo "  Installing npm packages..."
    npm install --silent $NPM_MIRROR 2>&1 | tail -1
fi
echo -e "  ${GREEN}✓${NC} Frontend dependencies installed"

# ── 5. Database setup ────────────────────────────
echo ""
echo -e "${YELLOW}[5/6]${NC} Setting up database..."
cd "$ROOT/backend"

# Source .env for DATABASE_URL
if [ -f "$ROOT/.env" ]; then
    set -a
    source "$ROOT/.env"
    set +a
fi

# ── 6. Seed data ─────────────────────────────────
echo ""
echo -e "${YELLOW}[6/6]${NC} Running database seed..."

if .venv/bin/python seed.py 2>&1 | while IFS= read -r line; do echo "  $line"; done; then
    echo ""
else
    echo ""
    echo -e "  ${RED}✗ Seed failed.${NC}"
    echo "  Common fixes:"
    echo "    1. Make sure PostgreSQL is running"
    echo "    2. Set DATABASE_URL in .env, e.g.:"
    echo "       DATABASE_URL=postgresql+asyncpg://clawith:clawith@localhost:5432/clawith?ssl=disable"
    echo "    3. Create the database first:"
    echo "       createdb clawith"
    echo ""
    echo "  After fixing, re-run: bash setup.sh"
    exit 1
fi

# ── Summary ──────────────────────────────────────
SERVER_IP=$(get_server_ip)

echo ""
echo -e "${GREEN}═══════════════════════════════════════${NC}"
echo -e "${GREEN}  🎉 Setup complete!${NC}"
echo -e "${GREEN}═══════════════════════════════════════${NC}"
echo ""
echo "  To start the application:"
echo ""
echo -e "  ${CYAN}Option A: One-command start${NC}"
echo "    bash restart.sh"
echo ""
echo -e "  ${CYAN}Option B: Manual start${NC}"
echo "    # Terminal 1 — Backend"
echo "    cd backend && .venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8008"
echo ""
echo "    # Terminal 2 — Frontend"
echo "    cd frontend && npx vite --host 0.0.0.0 --port 3008"
echo ""
echo -e "  ${CYAN}Option C: Docker${NC}"
echo "    docker compose up -d"
echo ""
echo -e "  ${CYAN}Access URLs:${NC}"
echo "    Local:   http://localhost:3008"
echo "    Network: http://${SERVER_IP}:3008"
echo ""
echo "  The first user to register becomes the platform admin."
echo ""
