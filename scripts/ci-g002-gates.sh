#!/bin/bash
set -euo pipefail

repository_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
backend_root="$repository_root/backend"

cd "$backend_root"
uv lock --check
uv sync --extra dev --frozen
uv run python scripts/validate_goal_gates.py --manifest rewrite/goal-gates.json
uv run python scripts/rewrite_inventory.py check --manifest rewrite/coverage.json --require-zero-unreviewed --require-zero-disposition-missing
uv run --extra dev pytest tests/architecture/test_governance.py tests/architecture/test_module_boundaries.py
uv run python scripts/check_owner_contracts.py check --manifest rewrite/owner-contracts.json
uv run python scripts/validate_goal_gates.py --manifest rewrite/goal-gates.json --check-product-roster-and-linkage
uv run python scripts/validate_load_profile.py tests/performance/profiles/backend_50.json
bash ../scripts/check-g001-reference.sh
uv run --extra dev pytest tests/architecture
uv run --extra dev pytest
uv run --extra dev pytest --collect-only
uv run --extra dev ruff check app tests
uv run --extra dev pyright app
