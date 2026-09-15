#!/bin/bash
set -euo pipefail

repository_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
backend_root="$repository_root/backend"
reference_temp_root="$(mktemp -d)"
reference_worktree="$reference_temp_root/legacy-reference"

cleanup() {
    original_status=$?
    worktree_remove_status=0
    temp_remove_status=0
    prune_status=0
    trap - EXIT INT TERM
    git -C "$repository_root" worktree remove --force "$reference_worktree" >/dev/null 2>&1 || worktree_remove_status=$?
    rm -rf "$reference_temp_root" || temp_remove_status=$?
    if [ "$worktree_remove_status" -ne 0 ]; then
        git -C "$repository_root" worktree prune || prune_status=$?
        if [ "$prune_status" -ne 0 ]; then
            prune_status=0
            git -C "$repository_root" worktree prune || prune_status=$?
        fi
        if [ "$prune_status" -eq 0 ]; then
            worktree_remove_status=0
        fi
    fi
    if [ "$original_status" -ne 0 ]; then
        exit "$original_status"
    fi
    if [ "$worktree_remove_status" -ne 0 ] || [ "$temp_remove_status" -ne 0 ] || [ "$prune_status" -ne 0 ]; then
        exit 1
    fi
    exit 0
}
trap cleanup EXIT INT TERM

git -C "$repository_root" cat-file -e '8ed4ae2f^{commit}'
git -C "$repository_root" worktree add --detach "$reference_worktree" 8ed4ae2f
uv sync --project "$reference_worktree/backend" --extra dev
reference_python="$reference_worktree/backend/.venv/bin/python"

export CLAWITH_LEGACY_REFERENCE_AGENT_DATA_DIR="$reference_temp_root/persistence/legacy/agents"
export CLAWITH_LEGACY_REFERENCE_DATABASE_URL="postgresql+asyncpg://legacy:legacy@127.0.0.1:5432/clawith_legacy_reference"
export CLAWITH_LEGACY_REFERENCE_REDIS_URL="redis://127.0.0.1:6379/14"
export CLAWITH_LEGACY_REFERENCE_S3_PREFIX="clawith-legacy-reference/"
export CLAWITH_LEGACY_REFERENCE_STORAGE_LOCAL_ROOT="$reference_temp_root/persistence/legacy/storage"
export CLAWITH_TARGET_AGENT_DATA_DIR="$reference_temp_root/persistence/target/agents"
export CLAWITH_TARGET_DATABASE_URL="postgresql+asyncpg://target:target@127.0.0.1:5432/clawith_target"
export CLAWITH_TARGET_REDIS_URL="redis://127.0.0.1:6379/15"
export CLAWITH_TARGET_S3_PREFIX="clawith-target/"
export CLAWITH_TARGET_STORAGE_LOCAL_ROOT="$reference_temp_root/persistence/target/storage"

cd "$backend_root"
uv run python scripts/rewrite_inventory.py check-reference --manifest rewrite/coverage.json --expected-head 8ed4ae2f --require-clean --boot-smoke --black-box-manifest rewrite/legacy-black-box.json --worktree "$reference_worktree" --python "$reference_python"
