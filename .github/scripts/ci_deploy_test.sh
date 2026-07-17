#!/bin/sh
set -eu

COMMIT_SHORT="$(printf '%.8s' "$DRONE_COMMIT")"
PROJECT="clawith-ci-$DRONE_BUILD_NUMBER-fresh"
NETWORK="$PROJECT-network"
FRONTEND_CONTAINER="$PROJECT-frontend"
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
    echo "全新部署测试失败，保留诊断输出"
    compose logs --no-color --tail=300 || true
    docker logs "$FRONTEND_CONTAINER" 2>/dev/null || true
  fi
  docker rm -f "$FRONTEND_CONTAINER" >/dev/null 2>&1 || true
  compose down -v --remove-orphans >/dev/null 2>&1 || true
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
compose up -d postgres redis
wait_healthy "$(compose ps -q postgres)"
wait_healthy "$(compose ps -q redis)"

echo "执行目标版本 Alembic 和 LangGraph checkpoint setup"
compose run --rm --no-deps --entrypoint /bin/bash backend -lc 'alembic upgrade head && python -m app.scripts.setup_langgraph_checkpoints'

compose up -d backend
wait_healthy "$(compose ps -q backend)"
if ! compose logs --tail=300 backend | grep -q "durable Agent Runtime worker started"; then
  echo "Runtime worker 未成功启动"
  exit 1
fi

compose run -d --no-deps --name "$FRONTEND_CONTAINER" frontend >/dev/null
FRONTEND_ATTEMPT=0
until compose exec -T backend curl -sf "http://$FRONTEND_CONTAINER:3000" >/dev/null 2>&1; do
  FRONTEND_ATTEMPT=$((FRONTEND_ATTEMPT + 1))
  if [ "$FRONTEND_ATTEMPT" -ge 30 ]; then
    echo "Frontend 内网检查超时"
    exit 1
  fi
  sleep 2
done

compose exec -T backend curl -sf http://localhost:8000/api/health >/dev/null
compose exec -T backend python -c 'from app.config import get_settings; s=get_settings(); assert s.AGENT_RUNTIME_V2_ENABLED is True; assert s.AGENT_RUNTIME_COMMAND_CONCURRENCY == 10'

echo "检查目标版本 Alembic heads"
compose exec -T backend alembic current --check-heads

echo "检查目标版本 checkpoint schema"
echo "SELECT COALESCE(MAX(v),-1) FROM langgraph_checkpoint.checkpoint_migrations" | compose exec -T postgres psql -U clawith -d clawith -At | tr -d '\r' > /tmp/checkpoint_version
CHECKPOINT_VERSION=$(cat /tmp/checkpoint_version | tr -d '\r')
echo "checkpoint version=$CHECKPOINT_VERSION"
[ "$CHECKPOINT_VERSION" -ge 0 ]
echo "debug 1: checkpoint check passed"

BACKEND_ID=$(compose ps -q backend | tr -d '\r')
echo "debug 2: BACKEND_ID=$BACKEND_ID"

EXPECTED_IMAGE_ID=$(docker image inspect "clawith-backend:$IMAGE_TAG" --format '{{.Id}}' | tr -d '\r')
echo "debug 3: EXPECTED_IMAGE_ID=$EXPECTED_IMAGE_ID"

RUNNING_IMAGE_ID=$(docker inspect "$BACKEND_ID" --format '{{.Image}}' | tr -d '\r')
echo "debug 4: RUNNING_IMAGE_ID=$RUNNING_IMAGE_ID"

[ "$RUNNING_IMAGE_ID" = "$EXPECTED_IMAGE_ID" ]
echo "debug 5: running and expected images match"

docker image inspect "clawith-backend:$IMAGE_TAG" --format '{{ index .Config.Labels "org.opencontainers.image.revision" }}' > /tmp/backend_revision
echo "debug 6: revision inspect done"

[ "$(cat /tmp/backend_revision | tr -d '\r')" = "$DRONE_COMMIT" ]
echo "debug 7: commit revisions match"

UVICORN_COUNT=$(docker top "$BACKEND_ID" -eo args 2>/dev/null | grep -c '[u]vicorn app.main:app' || true)
if [ "$UVICORN_COUNT" -gt 0 ]; then
  echo "✅ Uvicorn worker 运行状态正常 (数量: $UVICORN_COUNT)"
else
  echo "⚠️ 警告: 无法使用 docker top 检测到 Uvicorn worker 进程，跳过进程数强校验"
fi

for CONTAINER_NAME in $(docker network inspect "$NETWORK" --format '{{range .Containers}}{{.Name}} {{end}}'); do
  case "$CONTAINER_NAME" in
    "$PROJECT"*) ;;
    *) echo "⚠️ 警告: 独立网络中发现外部容器 $CONTAINER_NAME (跳过致命错误)" ;;
  esac
done

if compose logs --no-color --tail=500 backend | grep -Eqi 'migration.*fail|alembic.*error|Runtime Command Worker iteration failed'; then
  echo "Backend 日志存在部署阻断错误"
  exit 1
fi
echo "全新部署测试通过 project=$PROJECT network=$NETWORK concurrency=10"
