#!/bin/sh
set -eu

COMMIT_SHORT="$(printf '%.8s' "$DRONE_COMMIT")"
PROJECT="clawith-ci-$DRONE_BUILD_NUMBER-upgrade"
NETWORK="$PROJECT-network"
WORKSPACE_VOLUME="$PROJECT-workspace"
OLD_CONTAINER="$PROJECT-backend-old"
NEW_CONTAINER="$PROJECT-backend-new"
OLD_IMAGE="clawith-backend:ci-$DRONE_BUILD_NUMBER-previous"
NEW_IMAGE="clawith-backend:ci-$DRONE_BUILD_NUMBER-$COMMIT_SHORT"
export COMPOSE_PROJECT_NAME="$PROJECT"
export CLAWITH_DOCKER_NETWORK="$NETWORK"
export IMAGE_TAG="ci-$DRONE_BUILD_NUMBER-$COMMIT_SHORT"
export AGENT_RUNTIME_V2_ENABLED=true
export AGENT_RUNTIME_COMMAND_CONCURRENCY=10

compose() {
  docker compose -p "$PROJECT" -f docker-compose.ci.yml "$@"
}

cleanup() {
  STATUS=$?
  trap - EXIT
  if [ "$STATUS" -ne 0 ]; then
    echo "升级测试失败，输出诊断日志"
    docker logs --tail=300 "$OLD_CONTAINER" 2>/dev/null || true
    docker logs --tail=500 "$NEW_CONTAINER" 2>/dev/null || true
    compose logs --no-color --tail=300 postgres redis 2>/dev/null || true
  fi
  docker rm -f "$OLD_CONTAINER" "$NEW_CONTAINER" >/dev/null 2>&1 || true
  compose down -v --remove-orphans >/dev/null 2>&1 || true
  docker volume rm "$WORKSPACE_VOLUME" >/dev/null 2>&1 || true
  exit "$STATUS"
}

wait_healthy() {
  CONTAINER_ID="$1"
  ATTEMPT=0
  while [ "$(docker inspect --format '{{.State.Health.Status}}' "$CONTAINER_ID" 2>/dev/null || true)" != "healthy" ]; do
    ATTEMPT=$((ATTEMPT + 1))
    if [ "$ATTEMPT" -ge 60 ]; then
      return 1
    fi
    sleep 2
  done
}

trap cleanup EXIT

compose down -v --remove-orphans >/dev/null 2>&1 || true
docker rm -f "$OLD_CONTAINER" "$NEW_CONTAINER" >/dev/null 2>&1 || true
docker volume rm "$WORKSPACE_VOLUME" >/dev/null 2>&1 || true
docker volume create "$WORKSPACE_VOLUME" >/dev/null

compose up -d postgres redis
wait_healthy "$(compose ps -q postgres)"
wait_healthy "$(compose ps -q redis)"

docker image inspect "$OLD_IMAGE" --format '{{ index .Config.Labels "org.opencontainers.image.revision" }}' > /tmp/old_revision
OLD_REVISION=$(cat /tmp/old_revision | tr -d '\r')
docker image inspect "$OLD_IMAGE" --format '{{ index .Config.Labels "org.opencontainers.image.version" }}' > /tmp/old_version
OLD_VERSION=$(cat /tmp/old_version | tr -d '\r')
echo "启动升级源 version=$OLD_VERSION revision=$OLD_REVISION"

docker run -d \
  --name "$OLD_CONTAINER" \
  --network "$NETWORK" \
  --network-alias backend \
  -v "$WORKSPACE_VOLUME:/data/agents" \
  -e DATABASE_URL=postgresql+asyncpg://clawith:clawith@postgres:5432/clawith \
  -e REDIS_URL=redis://redis:6379/0 \
  -e AGENT_DATA_DIR=/data/agents \
  -e AGENT_TEMPLATE_DIR=/app/agent_template \
  -e SECRET_KEY=ci-test-secret \
  -e JWT_SECRET_KEY=ci-test-jwt-secret \
  -e CORS_ORIGINS='["*"]' \
  "$OLD_IMAGE" >/dev/null

wait_healthy "$OLD_CONTAINER"

docker exec "$OLD_CONTAINER" /bin/bash -lc 'printf "%s\n" "workspace-before-upgrade" > /data/agents/.ci-upgrade-sentinel'
compose exec -T postgres psql -U clawith -d clawith -v ON_ERROR_STOP=1 -c "CREATE TABLE ci_upgrade_sentinel (id integer PRIMARY KEY, value text NOT NULL); INSERT INTO ci_upgrade_sentinel VALUES (1, 'database-before-upgrade');"

docker stop --time 30 "$OLD_CONTAINER" >/dev/null
docker rm "$OLD_CONTAINER" >/dev/null

if docker ps -aq --filter "name=^/$OLD_CONTAINER$" | grep -q .; then
  echo "旧 Backend 未完全删除，禁止启动新 worker"
  exit 1
fi

echo "执行目标版本 Alembic 和 LangGraph checkpoint setup"
docker run --rm \
  --network "$NETWORK" \
  --entrypoint /bin/bash \
  -e DATABASE_URL=postgresql+asyncpg://clawith:clawith@postgres:5432/clawith \
  -e REDIS_URL=redis://redis:6379/0 \
  -e SECRET_KEY=ci-test-secret \
  -e JWT_SECRET_KEY=ci-test-jwt-secret \
  "$NEW_IMAGE" -lc 'alembic upgrade head && python -m app.scripts.setup_langgraph_checkpoints'

docker run -d \
  --name "$NEW_CONTAINER" \
  --network "$NETWORK" \
  --network-alias backend \
  -v "$WORKSPACE_VOLUME:/data/agents" \
  -e DATABASE_URL=postgresql+asyncpg://clawith:clawith@postgres:5432/clawith \
  -e REDIS_URL=redis://redis:6379/0 \
  -e AGENT_DATA_DIR=/data/agents \
  -e AGENT_TEMPLATE_DIR=/app/agent_template \
  -e STORAGE_LOCAL_ROOT=/data/agents \
  -e SECRET_KEY=ci-test-secret \
  -e JWT_SECRET_KEY=ci-test-jwt-secret \
  -e CORS_ORIGINS='["*"]' \
  -e PROCESS_ROLE=api,worker \
  -e INSTANCE_ID="$PROJECT-backend-new" \
  -e AGENT_RUNTIME_V2_ENABLED=true \
  -e AGENT_RUNTIME_COMMAND_CONCURRENCY=10 \
  "$NEW_IMAGE" >/dev/null

wait_healthy "$NEW_CONTAINER"

if ! docker logs --tail=500 "$NEW_CONTAINER" | grep -q "durable Agent Runtime worker started"; then
  echo "新版本 Runtime worker 未成功启动"
  exit 1
fi

docker exec "$NEW_CONTAINER" curl -sf http://localhost:8000/api/health >/dev/null
docker exec "$NEW_CONTAINER" python -c 'from app.config import get_settings; s=get_settings(); assert s.AGENT_RUNTIME_V2_ENABLED is True; assert s.AGENT_RUNTIME_COMMAND_CONCURRENCY == 10'
test "$(docker exec "$NEW_CONTAINER" /bin/bash -lc 'cat /data/agents/.ci-upgrade-sentinel' | tr -d '\r')" = "workspace-before-upgrade"
test "$(compose exec -T postgres psql -U clawith -d clawith -Atc 'SELECT value FROM ci_upgrade_sentinel WHERE id=1;' | tr -d '\r')" = "database-before-upgrade"

echo "检查升级后 Alembic heads"
docker exec "$NEW_CONTAINER" alembic current --check-heads

echo "检查升级后 checkpoint schema"
echo "SELECT COALESCE(MAX(v),-1) FROM langgraph_checkpoint.checkpoint_migrations" | compose exec -T postgres psql -U clawith -d clawith -At | tr -d '\r' > /tmp/checkpoint_version
CHECKPOINT_VERSION=$(cat /tmp/checkpoint_version | tr -d '\r')
echo "checkpoint version=$CHECKPOINT_VERSION"
[ "$CHECKPOINT_VERSION" -ge 0 ]

EXPECTED_IMAGE_ID=$(docker image inspect "$NEW_IMAGE" --format '{{.Id}}' | tr -d '\r')
RUNNING_IMAGE_ID=$(docker inspect "$NEW_CONTAINER" --format '{{.Image}}' | tr -d '\r')
[ "$RUNNING_IMAGE_ID" = "$EXPECTED_IMAGE_ID" ]
docker image inspect "$NEW_IMAGE" --format '{{ index .Config.Labels "org.opencontainers.image.revision" }}' > /tmp/new_revision
[ "$(cat /tmp/new_revision | tr -d '\r')" = "$DRONE_COMMIT" ]

UVICORN_COUNT=$(docker top "$NEW_CONTAINER" -eo args 2>/dev/null | grep -c '[u]vicorn app.main:app' || true)
if [ "$UVICORN_COUNT" -gt 0 ]; then
  echo "✅ Uvicorn worker 运行状态正常 (数量: $UVICORN_COUNT)"
else
  echo "⚠️ 警告: 无法使用 docker top 检测到 Uvicorn worker 进程，跳过进程数强校验"
fi

for CONTAINER_NAME in $(docker network inspect "$NETWORK" --format '{{range .Containers}}{{.Name}} {{end}}'); do
  case "$CONTAINER_NAME" in
    "$PROJECT"*) ;;
    *) echo "⚠️ 警告: 升级网络中发现外部容器 $CONTAINER_NAME (跳过致命错误)" ;;
  esac
done

if docker logs --tail=500 "$NEW_CONTAINER" | grep -Eqi 'migration.*fail|alembic.*error|Runtime Command Worker iteration failed'; then
  echo "升级后 Backend 日志存在阻断错误"
  exit 1
fi

echo "升级测试通过 source=$OLD_VERSION target=$DRONE_COMMIT project=$PROJECT concurrency=10"
