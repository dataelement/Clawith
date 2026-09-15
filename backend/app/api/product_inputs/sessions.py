"""Direct human Session transport over authenticated product services."""

from dataclasses import asdict
from typing import cast
from uuid import UUID

from fastapi import APIRouter, Query, Request, WebSocket
from pydantic import BaseModel, ConfigDict, Field

from app.api.product_inputs.auth import authenticated
from app.api.product_inputs.events import HistoryFrame, serve_product_events
from app.execution_dependencies.product_inputs import ProductInputs
from app.infrastructure.database import DatabaseResources
from app.infrastructure.transactions import transaction
from app.modules.identity_tenant.public import TenantPrincipal
from app.modules.run.public import InputContent, InputReference, RunService
from app.modules.session.public import SessionService
from app.modules.tool.public import PersonalAccountSelection

router = APIRouter(prefix="/api/sessions", tags=["sessions"])


class CreateSession(BaseModel):
    model_config = ConfigDict(extra="forbid")
    agent_id: UUID


class ReferenceInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reference: str = Field(min_length=1, max_length=4096)
    name: str | None = Field(default=None, max_length=512)
    media_type: str | None = Field(default=None, max_length=256)


class AccountSelectionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    target_agent_id: UUID
    connection_ids: list[UUID] = Field(min_length=1, max_length=128)


class SubmitInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_key: str = Field(min_length=1, max_length=512)
    text: str = Field(max_length=65536)
    references: list[ReferenceInput] = Field(default_factory=list, max_length=64)
    reply_to_run_id: UUID | None = None
    waiting_reference: str | None = Field(default=None, max_length=512)
    account_selections: list[AccountSelectionInput] = Field(default_factory=list, max_length=16)


def database(request: Request) -> DatabaseResources:
    return cast(DatabaseResources, request.app.state.database)


@router.post("", status_code=201)
async def create(body: CreateSession, request: Request) -> dict[str, object]:
    access = await authenticated(request)
    async with transaction(database(request).control_sessions) as tx:
        return asdict(await SessionService(tx).create(access.principal, agent_id=body.agent_id))


@router.get("")
async def list_sessions(request: Request, after_id: UUID | None = None, limit: int = Query(100, ge=1, le=100)) -> dict[str, object]:
    access = await authenticated(request)
    async with transaction(database(request).control_sessions) as tx:
        return asdict(await SessionService(tx).list(access.principal, after_id=after_id, limit=limit))


@router.get("/{session_id}/history")
async def history(session_id: UUID, request: Request, after_position: int = Query(0, ge=0),
                  through_position: int | None = Query(None, ge=0), limit: int = Query(100, ge=1, le=100)) -> dict[str, object]:
    access = await authenticated(request)
    async with transaction(database(request).control_sessions) as tx:
        return asdict(await SessionService(tx).read_history(access.principal, session_id=session_id,
            after_position=after_position, through_position=through_position, limit=limit))


@router.get("/{session_id}/work")
async def work(session_id: UUID, request: Request, after_id: UUID | None = None,
               limit: int = Query(100, ge=1, le=100)) -> dict[str, object]:
    access = await authenticated(request)
    async with transaction(database(request).control_sessions) as tx:
        return asdict(await SessionService(tx).list_work(access.principal, session_id=session_id, after_id=after_id, limit=limit))


@router.post("/{session_id}/inputs", status_code=202)
async def submit(session_id: UUID, body: SubmitInput, request: Request) -> dict[str, object]:
    access = await authenticated(request)
    products = cast(ProductInputs, request.app.state.products)
    content = InputContent(body.text, tuple(InputReference(ref.reference, ref.name, ref.media_type) for ref in body.references))
    return asdict(await products.submit_session(access.principal, session_id=session_id, source_key=body.source_key,
        input=content, reply_to_run_id=body.reply_to_run_id, waiting_reference=body.waiting_reference,
        account_selections=tuple(PersonalAccountSelection(item.target_agent_id, tuple(item.connection_ids))
            for item in body.account_selections)))


@router.get("/{session_id}/goal")
async def get_goal(session_id: UUID, request: Request) -> dict[str, object]:
    access = await authenticated(request)
    async with transaction(database(request).control_sessions) as tx:
        goal = await SessionService(tx).get_goal(access.principal, session_id=session_id)
        return {"goal": asdict(goal) if goal is not None else None}


@router.delete("/{session_id}/goal")
async def cancel_goal(session_id: UUID, request: Request) -> dict[str, object]:
    access = await authenticated(request)
    products = cast(ProductInputs, request.app.state.products)
    async with transaction(database(request).control_sessions) as tx:
        result = await SessionService(tx).cancel_goal(access.principal, session_id=session_id)
        changed = (await RunService(tx).terminate(tenant_id=access.principal.tenant_id, run_id=result.active_run_id,
            status="Cancelled", reason="Goal cancelled", consumer=products)) if result.active_run_id is not None else None
    if changed is not None:
        assert products.runtime is not None
        await products.runtime.post_commit(changed)
    return {"goal": asdict(result.goal) if result.goal is not None else None}


@router.websocket("/{session_id}/events")
async def events(socket: WebSocket, session_id: UUID, after_position: int = Query(0, ge=0)) -> None:
    resources = cast(DatabaseResources, socket.app.state.database)
    streams = cast(ProductInputs, socket.app.state.products).streams
    async def authorize(principal: TenantPrincipal) -> None:
        async with transaction(resources.control_sessions) as tx:
            await SessionService(tx).get(principal, session_id=session_id)

    async def read_history(principal: TenantPrincipal, cursor: int) -> HistoryFrame:
        async with transaction(resources.control_sessions) as tx:
            page = await SessionService(tx).read_history(principal, session_id=session_id,
                after_position=cursor, limit=100, max_bytes=1024 * 1024)
        return HistoryFrame({"type": "history", **asdict(page)} if page.entries else {},
            page.next_after_position, page.has_more)

    await serve_product_events(socket, after_position=after_position, authorize=authorize, read_history=read_history,
        closing=streams.closed_event,
        subscribe=lambda principal: streams.subscribe(principal, session_id=session_id),
        unsubscribe=lambda principal, subscription: streams.unsubscribe(principal, session_id=session_id, subscription=subscription))
