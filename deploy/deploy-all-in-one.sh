#!/bin/bash
# All-in-One deployment script for Clawith
# Target: 192.168.106.163:/root/yaojin/clawith-yaojin

set -e

REMOTE_HOST="root@192.168.106.163"
REMOTE_PASS="dataelem"
REMOTE_DIR="/root/yaojin/clawith-yaojin"
LOCAL_DIR="$(cd "$(dirname "$0")/.." && pwd)"

echo "=== Clawith All-in-One Deployment ==="
echo "Local: $LOCAL_DIR"
echo "Remote: $REMOTE_HOST:$REMOTE_DIR"
echo ""

# Step 1: Clean remote directory
echo "[1/4] Cleaning remote directory..."
sshpass -p "$REMOTE_PASS" ssh "$REMOTE_HOST" "rm -rf $REMOTE_DIR/*"

# Step 2: Sync code (excluding unnecessary files)
echo "[2/4] Syncing code..."
sshpass -p "$REMOTE_PASS" rsync -avz --progress \
  --exclude='node_modules' \
  --exclude='.git' \
  --exclude='frontend/dist' \
  --exclude='frontend/build' \
  --exclude='__pycache__' \
  --exclude='*.pyc' \
  --exclude='.env' \
  "$LOCAL_DIR/" "$REMOTE_HOST:$REMOTE_DIR/"

# Step 3: Copy deploy configs to root
echo "[3/4] Setting up deploy configs..."
sshpass -p "$REMOTE_PASS" ssh "$REMOTE_HOST" "cd $REMOTE_DIR && \
  cp deploy/docker-compose.yml . && \
  cp deploy/.env.example .env 2>/dev/null || true && \
  mkdir -p nginx && \
  cp deploy/nginx/nginx.conf nginx/ && \
  cp deploy/nginx/all-in-one.conf nginx/"

# Step 4: Build and start services
echo "[4/4] Building and starting services..."
sshpass -p "$REMOTE_PASS" ssh "$REMOTE_HOST" "cd $REMOTE_DIR && \
  rm -rf frontend/dist frontend/build && \
  docker compose build backend --no-cache && \
  docker compose build frontend --no-cache && \
  docker compose up -d"

echo ""
echo "=== Deployment Complete ==="
echo "Frontend: http://192.168.106.163:3008"
echo "Backend: http://192.168.106.163:8000"
