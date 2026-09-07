"""Bounded disposable projection storage in the caller's transaction."""

import json
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import Numeric, Text, case, cast, delete, func, or_, select
from sqlalchemy.dialects.postgresql import insert

from app.infrastructure.transactions import TransactionContext
from app.modules.context.models import ContextProjectionRecord

MAX_PROJECTION_BYTES = 16 * 1024 * 1024


class ContextProjectionRepository:
    def __init__(self, transaction: TransactionContext) -> None:
        self._session = transaction.session

    async def load(self, *, tenant_id: UUID, run_id: UUID) -> str | None:
        row = ContextProjectionRecord
        payload = await self._session.scalar(select(cast(row.payload, Text)).where(
            row.tenant_id == tenant_id, row.run_id == run_id,
            row.payload_kind == "context_view", row.payload_schema_version == 1,
            func.octet_length(cast(row.payload, Text)) <= MAX_PROJECTION_BYTES))
        return payload

    async def save(self, *, tenant_id: UUID, run_id: UUID, payload: bytes,
                   coverage: int, through: int) -> None:
        if len(payload) > MAX_PROJECTION_BYTES:
            raise ValueError("Context projection exceeds its storage bound")
        now = datetime.now(UTC)
        values = {"tenant_id": tenant_id, "run_id": run_id, "payload_kind": "context_view",
            "payload_schema_version": 1, "payload": json.loads(payload), "coverage_sequence": coverage,
            "rebuilt_at": now, "updated_at": now}
        row = ContextProjectionRecord
        statement = insert(row).values(**values)
        stored_position = case(
            (func.jsonb_typeof(row.payload["through_sequence"]) == "number",
             cast(row.payload["through_sequence"].as_string(), Numeric)),
            else_=-1)
        # Context projection is advisory; stale saves must not replace a newer view.
        await self._session.execute(statement.on_conflict_do_update(
            index_elements=[row.run_id],
            set_={key: value for key, value in values.items() if key not in ("run_id", "tenant_id", "rebuilt_at")},
            where=(row.tenant_id == tenant_id) &
                or_(stored_position <= through, row.payload_kind != "context_view",
                    row.payload_schema_version != 1,
                    func.octet_length(cast(row.payload, Text)) > MAX_PROJECTION_BYTES)))

    async def discard_observed(self, *, tenant_id: UUID, run_id: UUID, payload: str) -> None:
        """Discard only the invalid value observed; preserve a concurrently replaced view."""
        row = ContextProjectionRecord
        await self._session.execute(delete(row).where(row.tenant_id == tenant_id, row.run_id == run_id,
            cast(row.payload, Text) == payload))
