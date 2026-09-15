"""Authenticated schedule configuration and signed webhook intake."""

import asyncio
from typing import cast
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field, StrictBool

from app.api.product_inputs.auth import authenticated
from app.execution_dependencies.schedule_tools import heartbeat_config, trigger_config
from app.execution_dependencies.scheduled_inputs import ScheduledInputs
from app.infrastructure.transactions import transaction
from app.modules.agent.public import AgentService
from app.modules.heartbeat.public import HeartbeatService
from app.modules.run.public import InputContent
from app.modules.trigger.public import TriggerService

router = APIRouter(prefix="/api", tags=["schedules"])


class TriggerInput(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    agent_id: UUID
    config: dict[str, object]
    enabled: StrictBool = True
    delegated_connection_ids: list[UUID] = Field(default_factory=list, max_length=128)


class ScheduleUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    config: dict[str, object]
    enabled: StrictBool = True
    delegated_connection_ids: list[UUID] = Field(default_factory=list, max_length=128)


class ManualInput(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    event_id: str = Field(min_length=1, max_length=480)
    text: str | None = Field(default=None, max_length=65536)


def schedules(request: Request) -> ScheduledInputs:
    return cast(ScheduledInputs, request.app.state.scheduled)


@router.post("/triggers")
async def create_trigger(body: TriggerInput, request: Request):
    principal = (await authenticated(request)).principal
    service = schedules(request)
    async with transaction(service.database.control_sessions) as tx:
        agent = await AgentService(tx).get_for_execution(principal, agent_id=body.agent_id)
        return await TriggerService(tx, enabled_sources=service.execution.market.enabled_source_ids).create(principal,
            agent_id=agent.id, config=trigger_config(body.config, timezone=agent.timezone), enabled=body.enabled,
            delegated_connection_ids=tuple(body.delegated_connection_ids))


@router.get("/agents/{agent_id}/triggers")
async def list_triggers(agent_id: UUID, request: Request, after_id: UUID | None = None, limit: int = Query(100, ge=1, le=100)):
    principal = (await authenticated(request)).principal
    async with transaction(schedules(request).database.control_sessions) as tx:
        return await TriggerService(tx).list(principal, agent_id=agent_id, after_id=after_id, limit=limit)


@router.get("/triggers/{trigger_id}")
async def get_trigger(trigger_id: UUID, request: Request):
    principal = (await authenticated(request)).principal
    async with transaction(schedules(request).database.control_sessions) as tx:
        return await TriggerService(tx).get(principal, trigger_id=trigger_id)


@router.put("/triggers/{trigger_id}")
async def update_trigger(trigger_id: UUID, body: ScheduleUpdate, request: Request):
    principal = (await authenticated(request)).principal
    service = schedules(request)
    async with transaction(service.database.control_sessions) as tx:
        owner = TriggerService(tx, enabled_sources=service.execution.market.enabled_source_ids)
        current = await owner.get(principal, trigger_id=trigger_id)
        agent = await AgentService(tx).get_for_execution(principal, agent_id=current.agent_id)
        return await owner.update(principal, trigger_id=trigger_id, config=trigger_config(body.config, timezone=agent.timezone),
            enabled=body.enabled, delegated_connection_ids=tuple(body.delegated_connection_ids))


@router.delete("/triggers/{trigger_id}")
async def remove_trigger(trigger_id: UUID, request: Request):
    principal = (await authenticated(request)).principal
    async with transaction(schedules(request).database.control_sessions) as tx:
        return await TriggerService(tx).remove(principal, trigger_id=trigger_id)


@router.get("/triggers/{trigger_id}/history")
async def trigger_history(trigger_id: UUID, request: Request, after_id: UUID | None = None, limit: int = Query(100, ge=1, le=100)):
    principal = (await authenticated(request)).principal
    async with transaction(schedules(request).database.control_sessions) as tx:
        return await TriggerService(tx).history(principal, trigger_id=trigger_id, after_id=after_id, limit=limit)


@router.post("/triggers/{trigger_id}/fire")
async def fire_trigger(trigger_id: UUID, body: ManualInput, request: Request):
    principal = (await authenticated(request)).principal
    return await schedules(request).fire_manual(principal, trigger_id=trigger_id, event_id=body.event_id,
        input=InputContent(body.text) if body.text is not None else None)


@router.get("/triggers/{trigger_id}/history/{occurrence_id}/result")
async def trigger_result(trigger_id: UUID, occurrence_id: UUID, request: Request,
        content_offset: int = Query(0, ge=0, le=16777216)):
    principal = (await authenticated(request)).principal
    async with transaction(schedules(request).database.control_sessions) as tx:
        return await TriggerService(tx).read_result(principal, trigger_id=trigger_id,
            occurrence_id=occurrence_id, content_offset=content_offset)


@router.get("/agents/{agent_id}/heartbeat")
async def get_heartbeat(agent_id: UUID, request: Request):
    principal = (await authenticated(request)).principal
    async with transaction(schedules(request).database.control_sessions) as tx:
        return await HeartbeatService(tx).get(principal, agent_id=agent_id)


@router.put("/agents/{agent_id}/heartbeat")
async def configure_heartbeat(agent_id: UUID, body: ScheduleUpdate, request: Request):
    principal = (await authenticated(request)).principal
    service = schedules(request)
    async with transaction(service.database.control_sessions) as tx:
        agent = await AgentService(tx).get_for_execution(principal, agent_id=agent_id)
        return await HeartbeatService(tx, enabled_sources=service.execution.market.enabled_source_ids).configure(principal,
            agent_id=agent_id, config=heartbeat_config(body.config, timezone=agent.timezone), enabled=body.enabled,
            delegated_connection_ids=tuple(body.delegated_connection_ids))


@router.get("/agents/{agent_id}/heartbeat/history")
async def heartbeat_history(agent_id: UUID, request: Request, after_id: UUID | None = None, limit: int = Query(100, ge=1, le=100)):
    principal = (await authenticated(request)).principal
    async with transaction(schedules(request).database.control_sessions) as tx:
        return await HeartbeatService(tx).history(principal, agent_id=agent_id, after_id=after_id, limit=limit)


@router.post("/webhooks/{tenant_id}/{trigger_id}")
async def receive_webhook(tenant_id: UUID, trigger_id: UUID, request: Request):
    event_id, signature = request.headers.get("x-event-id"), request.headers.get("x-signature")
    if not event_id or not signature:
        raise HTTPException(401, "Webhook authentication is required")
    chunks, size = [], 0
    try:
        async with asyncio.timeout(10):
            async for chunk in request.stream():
                size += len(chunk)
                if size > 64 * 1024:
                    raise HTTPException(413, "Webhook body exceeds its bound")
                chunks.append(chunk)
    except TimeoutError:
        raise HTTPException(408, "Webhook body timed out") from None
    occurrence = await schedules(request).webhook(tenant_id=tenant_id, trigger_id=trigger_id,
        event_id=event_id, signature=signature, body=b"".join(chunks))
    return {"accepted": True, "occurrence_id": str(occurrence.id), "admission": occurrence.admission,
        "run_id": str(occurrence.run_id) if occurrence.run_id else None}


@router.get("/agents/{agent_id}/heartbeat/history/{occurrence_id}/result")
async def heartbeat_result(agent_id: UUID, occurrence_id: UUID, request: Request,
        content_offset: int = Query(0, ge=0, le=16777216)):
    principal = (await authenticated(request)).principal
    async with transaction(schedules(request).database.control_sessions) as tx:
        return await HeartbeatService(tx).read_result(principal, agent_id=agent_id,
            occurrence_id=occurrence_id, content_offset=content_offset)
