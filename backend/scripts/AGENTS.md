# Backend Data Maintenance & Migration Scripts Guidelines

> Auto-loads when editing anything under `backend/scripts/`.
> Read this **before** creating or running manual data maintenance or migration scripts.
> Complements [`backend/AGENTS.md`](file:///Users/alex/Documents/Code/dataelem/Clawith/backend/AGENTS.md) and [`backend/alembic/AGENTS.md`](file:///Users/alex/Documents/Code/dataelem/Clawith/backend/alembic/AGENTS.md).

---

## 1. Overview & Purpose

While `backend/alembic/` is reserved strictly for DDL schema migrations, `backend/scripts/` is the dedicated home for:
- Manual data backfill / data clean-up jobs.
- One-off maintenance scripts.
- Cross-tenant data reconciliation out-of-band operations.

---

## 2. Mandatory Script Rules

### 2.1 Dry-Run First (默认为安全预演模式)
- Every data modification script MUST default to **Dry-Run mode** (logging planned changes without mutating database rows).
- Require an explicit `--apply` CLI flag to write changes to PostgreSQL.

```bash
# Default preview run (no DB writes)
uv run python scripts/backfill_agent_credentials.py

# Actual execution
uv run python scripts/backfill_agent_credentials.py --apply
```

### 2.2 Batching & Idempotency (分批提交与幂等防护)
- **Batch Processing**: NEVER update large datasets in a single massive transaction. Process in batches (e.g., `--batch-size 500`) and commit per batch to avoid locking tables.
- **Idempotency**: Re-running the script must be safe and produce the same end state without duplicate records or errors.

### 2.3 Working Directory & Python Path
- All scripts MUST be executed from the `backend/` directory root.
- Python scripts must handle sys.path or environment variables to resolve `from app.xxx import ...`.

### 2.4 Tenant Filter Bypass
- Out-of-band maintenance scripts run outside FastAPI request lifecycles.
- Explicitly bypass or cycle through `tenant_id` scopes when processing cross-tenant tables.

---

## 3. Standard Script Template

```python
"""
Data Backfill Script: <Description>

Usage:
    uv run python scripts/my_script.py [--batch-size 500] [--apply]
"""
import argparse
import asyncio
import os
import sys

_BACKEND_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _BACKEND_ROOT not in sys.path:
    sys.path.insert(0, _BACKEND_ROOT)

from app.core.logger import logger

async def process_data(batch_size: int, apply: bool) -> int:
    logger.info(f"Starting data migration. Mode: {'APPLY' if apply else 'DRY-RUN'}")
    # Implementation logic...
    return 0

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch-size", type=int, default=500, help="Batch size for DB operations")
    parser.add_argument("--apply", action="store_true", help="Execute DB mutations (default is dry-run)")
    args = parser.parse_args()
    return asyncio.run(process_data(args.batch_size, args.apply))

if __name__ == "__main__":
    sys.exit(main())
```
