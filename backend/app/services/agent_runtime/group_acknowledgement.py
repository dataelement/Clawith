"""Durable public acknowledgement for accepted group mention Runs."""

from __future__ import annotations

from collections.abc import Mapping
import uuid

from loguru import logger
from sqlalchemy import select

from app.models.agent_run import AgentRun
from app.services.agent_runtime.command_worker import (
    CheckpointObservation,
    RuntimeCommandRecord,
    RuntimeRunRecord,
    RuntimeSessionFactory,
)
from app.services.agent_runtime.delivery import (
    DeliveryReceipt,
    DeliveryRequest,
    deliver_runtime_message,
)
from app.services.group_realtime import publish_stored_group_message


_ACK_CONTENT = "收到，我开始处理。"


class RuntimeGroupStartAcknowledgementHandler:
    """Write one ordinary Agent group message only after intake is committed."""

    def __init__(self, *, session_factory: RuntimeSessionFactory) -> None:
        self._session_factory = session_factory

    async def handle(
        self,
        *,
        run: RuntimeRunRecord,
        command: RuntimeCommandRecord,
        checkpoint: CheckpointObservation | None,
    ) -> None:
        del checkpoint
        if command.command_type != "start" or run.run_kind == "orchestration":
            return

        receipt: DeliveryReceipt | None = None
        async with self._session_factory() as db:
            async with db.begin():
                result = await db.execute(
                    select(AgentRun.delivery_target).where(
                        AgentRun.tenant_id == run.tenant_id,
                        AgentRun.id == run.run_id,
                    )
                )
                target = result.scalar_one_or_none()
                if not isinstance(target, Mapping) or target.get("kind") != "group":
                    return
                receipt = await deliver_runtime_message(
                    db,
                    DeliveryRequest(
                        tenant_id=run.tenant_id,
                        run_id=run.run_id,
                        kind="ack",
                        content=_ACK_CONTENT,
                    ),
                )
        if (
            receipt is not None
            and receipt.status == "delivered"
            and isinstance(receipt.actual_session_id, uuid.UUID)
            and isinstance(receipt.message_id, uuid.UUID)
        ):
            try:
                await publish_stored_group_message(
                    self._session_factory,
                    tenant_id=run.tenant_id,
                    session_id=receipt.actual_session_id,
                    message_id=receipt.message_id,
                )
            except Exception as exc:
                logger.warning(f"[GroupRealtime] ACK publish lookup failed: {exc}")


__all__ = ["RuntimeGroupStartAcknowledgementHandler"]
