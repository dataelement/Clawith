"""Group transport preserves Group ownership instead of routing through Session."""

from dataclasses import asdict
from typing import cast
from uuid import UUID

from fastapi import APIRouter, Query, Request, WebSocket
from pydantic import BaseModel, ConfigDict, Field, StrictBool

from app.api.product_inputs.auth import authenticated
from app.api.product_inputs.events import HistoryFrame, serve_product_events
from app.api.product_inputs.sessions import AccountSelectionInput, ReferenceInput
from app.execution_dependencies.product_inputs import ProductInputs
from app.infrastructure.database import DatabaseResources
from app.infrastructure.transactions import transaction
from app.modules.group.public import GroupService
from app.modules.identity_tenant.public import TenantPrincipal
from app.modules.run.public import InputContent, InputReference
from app.modules.tool.public import PersonalAccountSelection

router = APIRouter(prefix="/api/groups", tags=["groups"])


@router.websocket("/{group_id}/events")
async def events(socket: WebSocket, group_id: UUID, conversation_id: UUID | None = None,
        after_position: int = Query(0, ge=0)) -> None:
    resources = cast(DatabaseResources, socket.app.state.database)
    selected_conversation: UUID | None = None

    async def authorize(principal: TenantPrincipal) -> None:
        nonlocal selected_conversation
        async with transaction(resources.control_sessions) as tx:
            selected_conversation = await GroupService(tx).resolve_conversation(principal,
                group_id=group_id, conversation_id=conversation_id)

    async def read_history(principal: TenantPrincipal, cursor: int) -> HistoryFrame:
        assert selected_conversation is not None
        async with transaction(resources.control_sessions) as tx:
            page = await GroupService(tx).read_event_page(principal, group_id=group_id,
                conversation_id=selected_conversation, after_position=cursor)
        payload: dict[str, object] = ({"type": "history", "group_id": str(group_id),
            "conversation_id": str(selected_conversation), **asdict(page)} if page.entries else {})
        return HistoryFrame(payload, page.next_after_position, page.has_more)

    await serve_product_events(socket, after_position=after_position, authorize=authorize, read_history=read_history,
        closing=cast(ProductInputs, socket.app.state.products).streams.closed_event)


class GroupInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=200)
    announcement: str = Field(default="", max_length=16384)


class GroupUpdate(GroupInput):
    enabled: StrictBool


class GroupMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_key: str = Field(min_length=1, max_length=512)
    text: str = Field(max_length=65536)
    agent_ids: list[UUID] = Field(default_factory=list, max_length=100)
    references: list[ReferenceInput] = Field(default_factory=list, max_length=64)
    account_selections: list[AccountSelectionInput] = Field(default_factory=list, max_length=16)
    reply_to_run_id: UUID | None = None
    waiting_reference: str | None = Field(default=None, max_length=512)
    conversation_id: UUID | None = None
    mentioned_membership_ids: list[UUID] = Field(default_factory=list, max_length=100)


class AgentMembershipInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    agent_id: UUID
    enabled: StrictBool = True


class ConversationInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=200)


class ConversationUpdate(ConversationInput):
    enabled: StrictBool = True


class ReadInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    through_position: int = Field(ge=0)


class MembershipInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    membership_id: UUID
    enabled: StrictBool = True


def database(request: Request) -> DatabaseResources:
    return cast(DatabaseResources, request.app.state.database)


@router.post("", status_code=201)
async def create(body: GroupInput, request: Request) -> dict[str, object]:
    access = await authenticated(request)
    async with transaction(database(request).control_sessions) as tx:
        return asdict(await GroupService(tx).create(access.principal, name=body.name, announcement=body.announcement))


@router.get("")
async def list_groups(request: Request, after_id: UUID | None = None, limit: int = Query(100, ge=1, le=100)) -> dict[str, object]:
    access = await authenticated(request)
    async with transaction(database(request).control_sessions) as tx:
        return {"groups": [asdict(item) for item in await GroupService(tx).list_groups(access.principal, after_id=after_id, limit=limit)]}


@router.put("/{group_id}")
async def update(group_id: UUID, body: GroupUpdate, request: Request) -> dict[str, object]:
    access = await authenticated(request)
    async with transaction(database(request).control_sessions) as tx:
        return asdict(await GroupService(tx).update(access.principal, group_id=group_id, name=body.name,
            announcement=body.announcement, enabled=body.enabled))


@router.post("/{group_id}/members")
async def membership(group_id: UUID, body: MembershipInput, request: Request) -> dict[str, bool]:
    access = await authenticated(request)
    async with transaction(database(request).control_sessions) as tx:
        await GroupService(tx).set_membership(access.principal, group_id=group_id,
            membership_id=body.membership_id, enabled=body.enabled)
    return {"accepted": True}


@router.get("/{group_id}/history")
async def history(group_id: UUID, request: Request, after_position: int = Query(0, ge=0),
                  through_position: int | None = Query(None, ge=0), limit: int = Query(100, ge=1, le=100),
                  conversation_id: UUID | None = None) -> dict[str, object]:
    access = await authenticated(request)
    async with transaction(database(request).control_sessions) as tx:
        return {"events": [asdict(item) for item in await GroupService(tx).list_events(access.principal,
            group_id=group_id, after_position=after_position, through_position=through_position, limit=limit,
            conversation_id=conversation_id)]}


@router.post("/{group_id}/inputs", status_code=202)
async def submit(group_id: UUID, body: GroupMessage, request: Request) -> dict[str, object]:
    access = await authenticated(request)
    products = cast(ProductInputs, request.app.state.products)
    content = InputContent(body.text, tuple(InputReference(ref.reference, ref.name, ref.media_type) for ref in body.references))
    if body.reply_to_run_id is not None:
        if body.waiting_reference is None or body.account_selections:
            from app.infrastructure.errors import InvalidInput
            raise InvalidInput("Waiting reply requires its reference and cannot change accounts")
        async with transaction(database(request).control_sessions) as tx:
            accepted, changed = await GroupService(tx).answer_wait(access.principal, group_id=group_id,
                run_id=body.reply_to_run_id, waiting_reference=body.waiting_reference, source_key=body.source_key, input=content)
            if products.other.attachment_binder is not None:
                await products.other.attachment_binder(tx, access.principal, accepted)
        assert products.runtime is not None
        await products.runtime.post_commit(changed)
        await products.after_group_answer(access.principal, accepted, agent_id=changed.run.agent_id)
        return {"accepted": asdict(accepted), "run": asdict(changed.run)}
    return asdict(await products.other.submit_group(access.principal, group_id=group_id, source_key=body.source_key,
        input=content, agent_ids=tuple(body.agent_ids),
        conversation_id=body.conversation_id, mentioned_membership_ids=tuple(body.mentioned_membership_ids),
        account_selections=tuple(PersonalAccountSelection(item.target_agent_id, tuple(item.connection_ids)) for item in body.account_selections)))


@router.get("/{group_id}")
async def get_group(group_id: UUID, request: Request) -> dict[str, object]:
    access = await authenticated(request)
    async with transaction(database(request).control_sessions) as tx:
        return asdict(await GroupService(tx).get(access.principal, group_id=group_id))


@router.get("/{group_id}/members")
async def members(group_id: UUID, request: Request, kind: str = "human",
        offset: int = Query(0, ge=0), limit: int = Query(100, ge=1, le=100)) -> dict[str, object]:
    access = await authenticated(request)
    async with transaction(database(request).control_sessions) as tx:
        return {"members": [asdict(value) for value in await GroupService(tx).list_members(access.principal,
            group_id=group_id, kind=kind, offset=offset, limit=limit)]}


@router.get("/{group_id}/member-candidates")
async def candidates(group_id: UUID, request: Request, kind: str = "human",
        offset: int = Query(0, ge=0), limit: int = Query(100, ge=1, le=100)) -> dict[str, object]:
    access = await authenticated(request)
    async with transaction(database(request).control_sessions) as tx:
        return {"candidates": [asdict(value) for value in await GroupService(tx).invitation_candidates(access.principal,
            group_id=group_id, kind=kind, offset=offset, limit=limit)]}


@router.post("/{group_id}/agents")
async def agent_membership(group_id: UUID, body: AgentMembershipInput, request: Request) -> dict[str, bool]:
    access = await authenticated(request)
    async with transaction(database(request).control_sessions) as tx:
        await GroupService(tx).set_agent(access.principal, group_id=group_id, agent_id=body.agent_id, enabled=body.enabled)
    return {"accepted": True}


@router.get("/{group_id}/conversations")
async def conversations(group_id: UUID, request: Request, offset: int = Query(0, ge=0),
        limit: int = Query(100, ge=1, le=100)) -> dict[str, object]:
    access = await authenticated(request)
    async with transaction(database(request).control_sessions) as tx:
        return {"conversations": [asdict(value) for value in await GroupService(tx).list_conversations(
            access.principal, group_id=group_id, offset=offset, limit=limit)]}


@router.post("/{group_id}/conversations", status_code=201)
async def create_conversation(group_id: UUID, body: ConversationInput, request: Request) -> dict[str, object]:
    access = await authenticated(request)
    async with transaction(database(request).control_sessions) as tx:
        return asdict(await GroupService(tx).create_conversation(access.principal, group_id=group_id, title=body.title))


@router.put("/{group_id}/conversations/{conversation_id}")
async def update_conversation(group_id: UUID, conversation_id: UUID, body: ConversationUpdate, request: Request) -> dict[str, bool]:
    access = await authenticated(request)
    async with transaction(database(request).control_sessions) as tx:
        await GroupService(tx).update_conversation(access.principal, group_id=group_id,
            conversation_id=conversation_id, title=body.title, enabled=body.enabled)
    if not body.enabled:
        await _cancel_conversation_runs(request, access.principal, group_id, conversation_id)
    return {"accepted": True}


@router.delete("/{group_id}/conversations/{conversation_id}")
async def delete_conversation(group_id: UUID, conversation_id: UUID, request: Request) -> dict[str, bool]:
    access = await authenticated(request)
    async with transaction(database(request).control_sessions) as tx:
        await GroupService(tx).delete_conversation(access.principal, group_id=group_id, conversation_id=conversation_id)
    await _cancel_conversation_runs(request, access.principal, group_id, conversation_id)
    return {"accepted": True}


async def _cancel_conversation_runs(request: Request, principal: TenantPrincipal,
        group_id: UUID, conversation_id: UUID) -> None:
    products = cast(ProductInputs, request.app.state.products)
    assert products.runtime is not None
    cursor = None
    while True:
        async with transaction(database(request).control_sessions) as tx:
            ids = await GroupService(tx).conversation_cancellation_page(principal, group_id=group_id,
                conversation_id=conversation_id, after_id=cursor)
        if not ids:
            return
        for run_id in ids:
            async with transaction(database(request).control_sessions) as tx:
                changed = await GroupService(tx).cancel_removed_conversation_work(principal, group_id=group_id,
                    conversation_id=conversation_id, run_id=run_id)
            await products.runtime.post_commit(changed)
        cursor = ids[-1]


@router.post("/{group_id}/conversations/{conversation_id}/read")
async def mark_read(group_id: UUID, conversation_id: UUID, body: ReadInput, request: Request) -> dict[str, int]:
    access = await authenticated(request)
    async with transaction(database(request).control_sessions) as tx:
        value = await GroupService(tx).mark_read(access.principal, group_id=group_id,
            conversation_id=conversation_id, through_position=body.through_position)
    return {"through_position": value}


@router.get("/{group_id}/work")
async def work(group_id: UUID, request: Request, conversation_id: UUID | None = None,
        after_id: UUID | None = None, limit: int = Query(100, ge=1, le=100)) -> dict[str, object]:
    access = await authenticated(request)
    async with transaction(database(request).control_sessions) as tx:
        return {"work": [asdict(value) for value in await GroupService(tx).list_work(access.principal,
            group_id=group_id, conversation_id=conversation_id, after_id=after_id, limit=limit)]}


@router.post("/{group_id}/work/{run_id}/cancel")
async def cancel_work(group_id: UUID, run_id: UUID, request: Request) -> dict[str, object]:
    access = await authenticated(request)
    async with transaction(database(request).control_sessions) as tx:
        changed = await GroupService(tx).cancel_work(access.principal, group_id=group_id, run_id=run_id)
    products = cast(ProductInputs, request.app.state.products)
    assert products.runtime is not None
    await products.runtime.post_commit(changed)
    return {"run": asdict(changed.run)}
