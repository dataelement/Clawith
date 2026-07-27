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

run_schema_command() {
  IMAGE="$1"
  COMMAND="$2"
  docker run --rm \
    --network "$NETWORK" \
    --entrypoint /bin/bash \
    -e DATABASE_URL=postgresql+asyncpg://clawith:clawith@postgres:5432/clawith \
    -e REDIS_URL=redis://redis:6379/0 \
    -e SECRET_KEY=ci-test-secret \
    -e JWT_SECRET_KEY=ci-test-jwt-secret \
    "$IMAGE" -lc "$COMMAND"
}

run_schema_python() {
  IMAGE="$1"
  PYTHON_CODE="$2"
  docker run --rm \
    --network "$NETWORK" \
    --entrypoint python \
    -e DATABASE_URL=postgresql+asyncpg://clawith:clawith@postgres:5432/clawith \
    -e REDIS_URL=redis://redis:6379/0 \
    -e SECRET_KEY=ci-test-secret \
    -e JWT_SECRET_KEY=ci-test-jwt-secret \
    "$IMAGE" -c "$PYTHON_CODE"
}

schema_revision_digest() {
  IMAGE="$1"
  REVISION="$2"
  run_schema_python "$IMAGE" \
    "import hashlib; from alembic.config import Config; from alembic.script import ScriptDirectory; script = ScriptDirectory.from_config(Config(\"alembic.ini\")); revision = script.get_revision(\"$REVISION\"); print(hashlib.sha256(open(revision.path, \"rb\").read()).hexdigest())"
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

SOURCE_SCHEMA_HEADS=$(run_schema_python "$OLD_IMAGE" \
  'from alembic.config import Config; from alembic.script import ScriptDirectory; script = ScriptDirectory.from_config(Config("alembic.ini")); print("\n".join(script.get_heads()))')
SOURCE_SCHEMA_PARENT_REVISIONS=$(run_schema_python "$OLD_IMAGE" \
  'from alembic.config import Config; from alembic.script import ScriptDirectory; script = ScriptDirectory.from_config(Config("alembic.ini")); normalize = lambda value: () if value is None else (value,) if isinstance(value, str) else tuple(value); print("\n".join(sorted({parent for head in script.get_heads() for parent in normalize(script.get_revision(head).down_revision)})))')
SOURCE_SCHEMA_ROOT_HEADS=$(run_schema_python "$OLD_IMAGE" \
  'from alembic.config import Config; from alembic.script import ScriptDirectory; script = ScriptDirectory.from_config(Config("alembic.ini")); normalize = lambda value: () if value is None else (value,) if isinstance(value, str) else tuple(value); print("\n".join(sorted(head for head in script.get_heads() if not normalize(script.get_revision(head).down_revision))))')

if [ -z "$SOURCE_SCHEMA_HEADS" ]; then
  echo "无法识别升级源 Alembic revision graph"
  exit 1
fi

echo "使用升级源镜像建立并提交 source head 的父 revision"
# 父 revision 必须完全由升级源镜像建立；任何中间 migration 失败都会直接终止。
for SOURCE_SCHEMA_PARENT in $SOURCE_SCHEMA_PARENT_REVISIONS; do
  case "$SOURCE_SCHEMA_PARENT" in
    *[!A-Za-z0-9_.-]*)
      echo "升级源 Alembic parent revision 格式无效: $SOURCE_SCHEMA_PARENT"
      exit 1
      ;;
  esac
  run_schema_command "$OLD_IMAGE" "alembic upgrade $SOURCE_SCHEMA_PARENT"
done

echo "使用升级源镜像逐个提交 source head"
# source head 使用独立事务，失败时不会回滚已经提交的历史 schema。
for SOURCE_SCHEMA_HEAD in $SOURCE_SCHEMA_HEADS; do
  case "$SOURCE_SCHEMA_HEAD" in
    *[!A-Za-z0-9_.-]*)
      echo "升级源 Alembic head 格式无效: $SOURCE_SCHEMA_HEAD"
      exit 1
      ;;
  esac

  if run_schema_command "$OLD_IMAGE" "alembic upgrade $SOURCE_SCHEMA_HEAD"; then
    continue
  fi

  SOURCE_HEAD_IS_ROOT=false
  for SOURCE_SCHEMA_ROOT_HEAD in $SOURCE_SCHEMA_ROOT_HEADS; do
    if [ "$SOURCE_SCHEMA_ROOT_HEAD" = "$SOURCE_SCHEMA_HEAD" ]; then
      SOURCE_HEAD_IS_ROOT=true
      break
    fi
  done

  if [ "$SOURCE_HEAD_IS_ROOT" = "true" ]; then
    echo "升级源 root head 失败，禁止由目标镜像从空库构造旧 schema: $SOURCE_SCHEMA_HEAD"
    exit 1
  fi

  SOURCE_HEAD_DIGEST=$(schema_revision_digest "$OLD_IMAGE" "$SOURCE_SCHEMA_HEAD")
  TARGET_HEAD_DIGEST=$(schema_revision_digest "$NEW_IMAGE" "$SOURCE_SCHEMA_HEAD")
  if [ "$SOURCE_HEAD_DIGEST" = "$TARGET_HEAD_DIGEST" ]; then
    echo "目标镜像没有该 source head 的修复版本，拒绝掩盖 migration 错误: $SOURCE_SCHEMA_HEAD"
    exit 1
  fi

  echo "升级源 head migration 失败，使用目标镜像仅修复相同 head=$SOURCE_SCHEMA_HEAD"
  run_schema_command "$NEW_IMAGE" "alembic upgrade $SOURCE_SCHEMA_HEAD"
done

EXPECTED_SOURCE_SCHEMA_HEADS=$(printf '%s\n' "$SOURCE_SCHEMA_HEADS" | sort)
ACTUAL_SOURCE_SCHEMA_HEADS=$(compose exec -T postgres \
  psql -U clawith -d clawith -Atc \
  "SELECT version_num FROM alembic_version ORDER BY version_num;" | tr -d '\r' | sort)
if [ "$ACTUAL_SOURCE_SCHEMA_HEADS" != "$EXPECTED_SOURCE_SCHEMA_HEADS" ]; then
  echo "升级源 schema head 不匹配"
  echo "expected=$EXPECTED_SOURCE_SCHEMA_HEADS"
  echo "actual=$ACTUAL_SOURCE_SCHEMA_HEADS"
  exit 1
fi

echo "使用升级源镜像建立 LangGraph checkpoint schema"
run_schema_command "$OLD_IMAGE" "python -m app.scripts.setup_langgraph_checkpoints"

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
  -e PROCESS_ROLE=api,worker \
  -e INSTANCE_ID="$PROJECT-backend-old" \
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
