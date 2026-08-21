"""Backfill ChatMessage.tenant_id from its authoritative ChatSession.

Usage from ``backend/``::

    uv run python scripts/backfill_chat_message_tenant_id.py
    uv run python scripts/backfill_chat_message_tenant_id.py --apply
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys

from sqlalchemy import text

_BACKEND_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _BACKEND_ROOT not in sys.path:
    sys.path.insert(0, _BACKEND_ROOT)

from app.database import async_session  # noqa: E402


async def _counts() -> tuple[int, int]:
    async with async_session() as db:
        result = await db.execute(
            text(
                """
                SELECT
                    count(*) FILTER (WHERE s.tenant_id IS NOT NULL) AS resolvable,
                    count(*) FILTER (WHERE s.tenant_id IS NULL) AS unresolved
                FROM chat_messages AS m
                LEFT JOIN chat_sessions AS s ON s.id::text = m.conversation_id
                WHERE m.tenant_id IS NULL
                """
            )
        )
        row = result.one()
        return int(row.resolvable), int(row.unresolved)


async def process_data(batch_size: int, apply: bool) -> int:
    resolvable, unresolved = await _counts()
    mode = "APPLY" if apply else "DRY-RUN"
    print(f"mode={mode} resolvable={resolvable} unresolved={unresolved}")
    if unresolved:
        print("Refusing to continue: some tenant-less messages have no authoritative session tenant.")
        return 1
    if not apply:
        return 0

    updated = 0
    while True:
        async with async_session() as db:
            result = await db.execute(
                text(
                    """
                    WITH batch AS (
                        SELECT m.id, s.tenant_id
                        FROM chat_messages AS m
                        JOIN chat_sessions AS s ON s.id::text = m.conversation_id
                        WHERE m.tenant_id IS NULL
                          AND s.tenant_id IS NOT NULL
                        ORDER BY m.id
                        LIMIT :batch_size
                    )
                    UPDATE chat_messages AS m
                    SET tenant_id = batch.tenant_id
                    FROM batch
                    WHERE m.id = batch.id
                    RETURNING m.id
                    """
                ),
                {"batch_size": batch_size},
            )
            batch_count = len(result.all())
            await db.commit()
        updated += batch_count
        print(f"updated={updated}")
        if batch_count < batch_size:
            break

    remaining, unresolved = await _counts()
    print(f"complete updated={updated} remaining_resolvable={remaining} unresolved={unresolved}")
    return 0 if remaining == 0 and unresolved == 0 else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch-size", type=int, default=500)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if args.batch_size <= 0:
        parser.error("--batch-size must be positive")
    return asyncio.run(process_data(args.batch_size, args.apply))


if __name__ == "__main__":
    raise SystemExit(main())
