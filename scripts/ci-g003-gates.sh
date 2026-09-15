#!/bin/bash
set -euo pipefail

repository_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
backend_root="$repository_root/backend"

bash "$repository_root/scripts/ci-g002-gates.sh"

cd "$backend_root"
uv run python scripts/check_owner_contracts.py check --manifest rewrite/owner-contracts.json --require-approved-owner run --require-approved-owner context --require-approved-wave S0 --require-approved-wave S1 --approval-receipt backend/artifacts/rewrite/G003/receipts/identity-tenant-contract-approval.json --approval-receipt backend/artifacts/rewrite/G003/receipts/credential-contract-approval.json --approval-receipt backend/artifacts/rewrite/G003/receipts/model-contract-approval.json --approval-receipt backend/artifacts/rewrite/G003/receipts/agent-contract-approval.json --approval-receipt backend/artifacts/rewrite/G003/receipts/permission-contract-approval.json --approval-receipt backend/artifacts/rewrite/G003/receipts/auth-contract-approval.json --approval-receipt backend/artifacts/rewrite/G003/receipts/audit-contract-approval.json --approval-receipt backend/artifacts/rewrite/G003/receipts/workspace-contract-approval.json --approval-receipt backend/artifacts/rewrite/G003/receipts/tool-contract-approval.json --approval-receipt backend/artifacts/rewrite/G003/receipts/capability-market-contract-approval.json --approval-receipt backend/artifacts/rewrite/G003/receipts/context-contract-approval.json --approval-receipt backend/artifacts/rewrite/G003/receipts/run-contract-approval.json
uv run --extra dev pytest tests/database tests/modules/identity_tenant tests/modules/credential tests/modules/model tests/modules/agent tests/modules/permission tests/modules/auth tests/modules/audit
