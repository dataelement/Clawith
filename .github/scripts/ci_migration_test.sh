#!/bin/sh
set -eu

COMMIT_SHORT="$(printf '%.8s' "$DRONE_COMMIT")"
PROJECT="clawith-ci-$DRONE_BUILD_NUMBER-migration"
NETWORK="$PROJECT-network"
export COMPOSE_PROJECT_NAME="$PROJECT"
export CLAWITH_DOCKER_NETWORK="$NETWORK"
export IMAGE_TAG="ci-$DRONE_BUILD_NUMBER-$COMMIT_SHORT"

compose() {
  docker compose -p "$PROJECT" -f docker-compose.ci.yml "$@"
}

cleanup() {
  STATUS=$?
  trap - EXIT
  if [ "$STATUS" -ne 0 ]; then
    echo "空数据库迁移测试失败，保留诊断输出"
    compose logs --no-color --tail=300 postgres 2>/dev/null || true
  fi
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
compose up -d postgres
wait_healthy "$(compose ps -q postgres)"

echo "从空 PostgreSQL 数据库执行 alembic upgrade head"
compose run --rm --no-deps --entrypoint /bin/bash backend \
  -lc 'alembic upgrade head && alembic current --check-heads'

DELETED_AT_COLUMN_COUNT="$(
  compose exec -T postgres psql -U clawith -d clawith -Atc "
    SELECT COUNT(*)
    FROM information_schema.columns
    WHERE table_schema = 'public'
      AND table_name IN ('agents', 'llm_models')
      AND column_name = 'deleted_at';
  " | tr -d '\r'
)"
[ "$DELETED_AT_COLUMN_COUNT" = "2" ]

ACTIVE_INDEX_COUNT="$(
  compose exec -T postgres psql -U clawith -d clawith -Atc "
    SELECT COUNT(*)
    FROM pg_indexes
    WHERE schemaname = 'public'
      AND indexname IN (
        'ix_agents_active_tenant_created_at',
        'ix_llm_models_active_tenant_created_at'
      );
  " | tr -d '\r'
)"
[ "$ACTIVE_INDEX_COUNT" = "2" ]

echo "空数据库迁移测试通过 columns=$DELETED_AT_COLUMN_COUNT indexes=$ACTIVE_INDEX_COUNT"
