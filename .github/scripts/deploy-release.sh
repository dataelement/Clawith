#!/usr/bin/env bash

set -Eeuo pipefail

release_tag=${1:?release tag is required}
deploy_dir=${2:?deployment directory is required}

if ! [[ "$release_tag" =~ ^v[0-9]+\.[0-9]+\.[0-9]+([-.][0-9A-Za-z.]+)?$ ]]; then
    echo "Invalid release tag: $release_tag" >&2
    exit 1
fi

cd "$deploy_dir"
git rev-parse --is-inside-work-tree >/dev/null
test -f .env
test -f ss-nodes.json

if [ -n "$(git status --porcelain --untracked-files=no)" ]; then
    echo "Tracked files contain local changes; refusing to overwrite production." >&2
    git status --short --untracked-files=no >&2
    exit 1
fi

if docker compose version >/dev/null 2>&1; then
    compose=(docker compose)
elif sudo -n docker compose version >/dev/null 2>&1; then
    compose=(sudo -n docker compose)
else
    echo "Docker Compose is unavailable to the deployment user." >&2
    exit 1
fi

previous_commit=$(git rev-parse HEAD)
rollback_required=false

rollback() {
    status=$?
    trap - EXIT
    if [ "$status" -ne 0 ] && [ "$rollback_required" = true ]; then
        echo "Deployment failed; restoring $previous_commit." >&2
        set +e
        git checkout --detach "$previous_commit"
        "${compose[@]}" up -d --build
    fi
    exit "$status"
}
trap rollback EXIT

git fetch origin "refs/heads/main:refs/remotes/origin/main"
git fetch origin "refs/tags/$release_tag:refs/tags/$release_tag"

release_commit=$(git rev-parse "$release_tag^{commit}")
if ! git merge-base --is-ancestor "$release_commit" origin/main; then
    echo "Release $release_tag is not contained in origin/main." >&2
    exit 1
fi

git checkout --detach "$release_tag"
rollback_required=true

"${compose[@]}" config --quiet
"${compose[@]}" up -d --build

published_address=$("${compose[@]}" port frontend 3000 | tail -n 1)
frontend_port=${published_address##*:}
if ! [[ "$frontend_port" =~ ^[0-9]+$ ]]; then
    echo "Unable to resolve the published frontend port." >&2
    exit 1
fi

for attempt in $(seq 1 24); do
    if curl -fsS --max-time 5 "http://127.0.0.1:$frontend_port/api/health" >/dev/null; then
        echo "Successfully deployed $release_tag ($release_commit)."
        "${compose[@]}" ps
        rollback_required=false
        exit 0
    fi
    sleep 5
done

echo "Production health check failed after 120 seconds." >&2
"${compose[@]}" ps >&2
"${compose[@]}" logs --tail 200 backend frontend >&2
exit 1
