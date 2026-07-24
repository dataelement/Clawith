#!/usr/bin/env bash

set -Eeuo pipefail

release_tag=${1:?release tag is required}
deploy_dir=${2:?deployment directory is required}
source_dir=${3:?source directory is required}

case "$deploy_dir" in
    /*) ;;
    *) deploy_dir="$PWD/$deploy_dir" ;;
esac
case "$source_dir" in
    /*) ;;
    *) source_dir="$PWD/$source_dir" ;;
esac

if ! [[ "$release_tag" =~ ^v[0-9]+\.[0-9]+\.[0-9]+([-.][0-9A-Za-z.]+)?$ ]]; then
    echo "Invalid release tag: $release_tag" >&2
    exit 1
fi

cd "$source_dir"
git rev-parse --is-inside-work-tree >/dev/null

if [ -n "$(git status --porcelain --untracked-files=no)" ]; then
    echo "Source contains tracked local changes; refusing to overwrite production." >&2
    git status --short --untracked-files=no >&2
    exit 1
fi

if docker version >/dev/null 2>&1; then
    docker_cmd=(docker)
elif sudo -n docker version >/dev/null 2>&1; then
    docker_cmd=(sudo -n docker)
else
    echo "Docker is unavailable to the deployment user." >&2
    exit 1
fi
compose=("${docker_cmd[@]}" compose)
app_services=(backend-api backend-worker frontend)

cd "$deploy_dir"
test -f .env
"${compose[@]}" config --quiet

backend_container=$("${compose[@]}" ps -q backend-api)
worker_container=$("${compose[@]}" ps -q backend-worker)
frontend_container=$("${compose[@]}" ps -q frontend)
test -n "$backend_container"
test -n "$worker_container"
test -n "$frontend_container"

old_backend_image=$("${docker_cmd[@]}" inspect -f '{{.Image}}' "$backend_container")
old_worker_image=$("${docker_cmd[@]}" inspect -f '{{.Image}}' "$worker_container")
old_frontend_image=$("${docker_cmd[@]}" inspect -f '{{.Image}}' "$frontend_container")
if [ "$old_backend_image" != "$old_worker_image" ]; then
    echo "Backend API and worker do not use the same image; refusing deployment." >&2
    exit 1
fi

cd "$source_dir"
previous_commit=$(git rev-parse HEAD)
activation_started=false

rollback() {
    status=$?
    trap - EXIT
    if [ "$status" -ne 0 ]; then
        echo "Deployment failed; restoring source $previous_commit." >&2
        set +e
        cd "$source_dir"
        git checkout --detach "$previous_commit"
        if [ "$activation_started" = true ]; then
            "${docker_cmd[@]}" tag "$old_backend_image" clawith-backend:latest
            "${docker_cmd[@]}" tag "$old_frontend_image" clawith-frontend:latest
            cd "$deploy_dir"
            "${compose[@]}" up -d --no-deps --force-recreate "${app_services[@]}"
        fi
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

backend_release_image="clawith-backend:$release_tag"
frontend_release_image="clawith-frontend:$release_tag"

"${docker_cmd[@]}" build -t "$backend_release_image" backend
"${docker_cmd[@]}" build -t "$frontend_release_image" frontend

activation_started=true
"${docker_cmd[@]}" tag "$backend_release_image" clawith-backend:latest
"${docker_cmd[@]}" tag "$frontend_release_image" clawith-frontend:latest

cd "$deploy_dir"
"${compose[@]}" up -d --no-deps --force-recreate "${app_services[@]}"

expected_backend_id=$("${docker_cmd[@]}" image inspect -f '{{.Id}}' "$backend_release_image")
expected_frontend_id=$("${docker_cmd[@]}" image inspect -f '{{.Id}}' "$frontend_release_image")
running_api_id=$("${docker_cmd[@]}" inspect -f '{{.Image}}' "$("${compose[@]}" ps -q backend-api)")
running_worker_id=$("${docker_cmd[@]}" inspect -f '{{.Image}}' "$("${compose[@]}" ps -q backend-worker)")
running_frontend_id=$("${docker_cmd[@]}" inspect -f '{{.Image}}' "$("${compose[@]}" ps -q frontend)")

if [ "$running_api_id" != "$expected_backend_id" ] ||
   [ "$running_worker_id" != "$expected_backend_id" ] ||
   [ "$running_frontend_id" != "$expected_frontend_id" ]; then
    echo "Running containers do not use the newly built release images." >&2
    exit 1
fi

published_address=$("${compose[@]}" port frontend 3000 | tail -n 1)
frontend_port=${published_address##*:}
if ! [[ "$frontend_port" =~ ^[0-9]+$ ]]; then
    echo "Unable to resolve the published frontend port." >&2
    exit 1
fi

expected_version=${release_tag#v}
health_response=
for attempt in $(seq 1 24); do
    if health_response=$(curl -fsS --max-time 5 "http://127.0.0.1:$frontend_port/api/health"); then
        health_status=$(printf '%s' "$health_response" | sed -n 's/.*"status"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p')
        running_version=$(printf '%s' "$health_response" | sed -n 's/.*"version"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p')
        if [ "$health_status" = ok ] && [ "$running_version" = "$expected_version" ]; then
            echo "Successfully deployed $release_tag ($release_commit)."
            "${compose[@]}" ps
            activation_started=false
            exit 0
        fi
    fi
    sleep 5
done

echo "Production health check failed after 120 seconds." >&2
echo "Last health response: ${health_response:-<none>}" >&2
"${compose[@]}" ps >&2
"${compose[@]}" logs --tail 200 backend frontend >&2
exit 1
