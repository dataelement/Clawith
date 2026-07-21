"""Tenant-scoped HTTP boundary for native group chats."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import exists, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import get_current_user
from app.database import get_db
from app.models.agent import Agent
from app.models.agent_run import AgentRun
from app.models.agent_run_event import AgentRunEvent
from app.models.audit import AuditLog, ChatMessage
from app.models.group import GroupMember
from app.models.participant import Participant
from app.models.user import User
from app.services import group_chat_service
from app.services import group_file_service
from app.services import group_message_service
from app.services.agent_runtime.session_context_service import (
    SessionContextError,
    SessionContextService,
)
from app.services.agent_runtime.adapter import RuntimeCommandIntake
from app.services.agent_runtime.contracts import CancelRunCommand
from app.services.agent_runtime.run_state_reader import (
    RunStateReadError,
    open_run_state_reader as _open_run_state_reader,
)
from app.services.group_chat_service import GroupChatServiceError
from app.services.group_file_service import GroupFileServiceError
from app.services.group_message_service import GroupMessageServiceError
from app.services.group_realtime import publish_group_message_created
from app.services.participant_identity import get_or_create_user_participant


router = APIRouter(prefix="/api/groups", tags=["groups"])


class CreateGroupIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str | None = None


class PatchGroupIn(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = None


class GroupOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    tenant_id: uuid.UUID
    name: str
    description: str | None = None
    created_by_participant_id: uuid.UUID
    created_at: datetime
    updated_at: datetime


class InviteGroupMemberIn(BaseModel):
    participant_id: uuid.UUID


class GroupMemberOut(BaseModel):
    id: uuid.UUID
    participant_id: uuid.UUID
    participant_type: str
    participant_ref_id: uuid.UUID
    display_name: str
    avatar_url: str | None = None
    role: str
    role_description: str | None = None
    title: str | None = None
    joined_at: datetime


class GroupMemberCandidateOut(BaseModel):
    participant_id: uuid.UUID
    participant_type: Literal["user", "agent"]
    participant_ref_id: uuid.UUID
    display_name: str
    avatar_url: str | None = None
    role_description: str | None = None
    title: str | None = None


class CreateGroupSessionIn(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)


class PatchGroupSessionIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)


class GroupSessionOut(BaseModel):
    id: uuid.UUID
    group_id: uuid.UUID
    title: str
    is_primary: bool
    unread_count: int = 0
    created_by_participant_id: uuid.UUID | None = None
    created_at: datetime
    updated_at: datetime
    last_message_at: datetime | None = None


class MarkGroupSessionReadIn(BaseModel):
    message_id: uuid.UUID


class GroupReadStateOut(BaseModel):
    session_id: uuid.UUID
    last_read_message_id: uuid.UUID
    advanced: bool


class GroupMentionTokenIn(BaseModel):
    participant_id: uuid.UUID


class CreateGroupMessageIn(BaseModel):
    content: str = Field(min_length=1, max_length=1_000_000)
    mentions: list[GroupMentionTokenIn] = Field(default_factory=list, max_length=100)
    message_id: uuid.UUID | None = None


class GroupMessageOut(BaseModel):
    id: uuid.UUID
    role: str
    content: str
    participant_id: uuid.UUID | None = None
    sender_name: str | None = None
    mentions: list[dict]
    created_at: datetime
    cursor: str


class GroupMessageIntakeOut(BaseModel):
    message: GroupMessageOut
    dispatch_kind: str
    run_ids: list[uuid.UUID]
    created: bool
    error_code: str | None = None


class GroupRunStateOut(BaseModel):
    run_id: uuid.UUID
    status: str
    can_cancel: bool
    agent_id: uuid.UUID | None = None
    system_role: str | None = None


class GroupTextFileIn(BaseModel):
    content: str
    expected_version_token: str | None = None


class GroupWorkspaceFileIn(GroupTextFileIn):
    """Workspace-only write conditions; fixed announcement/memory files stay narrow."""

    require_absent: bool = False


class GroupTextFileOut(BaseModel):
    path: str
    content: str
    exists: bool
    version_token: str | None = None
    modified_at: str | None = None
    revision_id: uuid.UUID | None = None


class GroupWorkspaceEntryOut(BaseModel):
    path: str
    name: str
    is_dir: bool
    size: int
    modified_at: str
    version_token: str | None = None


class GroupSessionSummaryOut(BaseModel):
    version: int
    summary: str
    requirements: list[Any]
    decisions: list[Any]
    open_items: list[Any]
    evidence_refs: list[Any]
    workspace_refs: list[Any]
    covered_through_message_id: uuid.UUID | None = None


_NOT_FOUND_CODES = {
    "group_not_found",
    "group_member_not_found",
    "group_session_not_found",
    "group_message_not_found",
}
_FORBIDDEN_CODES = {
    "group_access_denied",
    "group_human_member_required",
    "group_manager_required",
    "group_creator_invalid",
    "group_memory_write_denied",
}
_CONFLICT_CODES = {
    "group_member_already_active",
    "group_last_manager_required",
}


def _tenant_id(current_user: User) -> uuid.UUID:
    tenant_id = current_user.tenant_id
    if tenant_id is None:
        raise HTTPException(status_code=403, detail="A tenant is required for groups")
    return tenant_id


async def _current_participant(db: AsyncSession, current_user: User) -> Participant:
    if not current_user.is_active:
        raise HTTPException(status_code=403, detail="Current user is not active")
    return await get_or_create_user_participant(
        db,
        current_user.id,
        current_user.display_name,
        current_user.avatar_url,
    )


async def _authorized_group_run(
    db: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    group_id: uuid.UUID,
    session_id: uuid.UUID,
    participant_id: uuid.UUID,
    run_id: uuid.UUID,
) -> AgentRun:
    await group_chat_service.authorize_group_session(
        db,
        tenant_id=tenant_id,
        group_id=group_id,
        session_id=session_id,
        participant_id=participant_id,
        human_only=True,
    )
    result = await db.execute(
        select(AgentRun).where(
            AgentRun.tenant_id == tenant_id,
            AgentRun.id == run_id,
            AgentRun.session_id == session_id,
            AgentRun.source_type == "chat",
            AgentRun.runtime_type == "langgraph",
        )
    )
    run = result.scalar_one_or_none()
    if run is None:
        raise HTTPException(status_code=404, detail="Group run not found")
    return run


def _translate_domain_error(exc: GroupChatServiceError) -> HTTPException:
    if exc.code in _NOT_FOUND_CODES:
        status_code = status.HTTP_404_NOT_FOUND
    elif exc.code in _FORBIDDEN_CODES:
        status_code = status.HTTP_403_FORBIDDEN
    elif exc.code in _CONFLICT_CODES:
        status_code = status.HTTP_409_CONFLICT
    else:
        status_code = status.HTTP_400_BAD_REQUEST
    return HTTPException(
        status_code=status_code,
        detail={"code": exc.code, "message": str(exc)},
    )


def _translate_message_error(exc: GroupMessageServiceError) -> HTTPException:
    if exc.code in {"group_not_found", "group_session_not_found"}:
        status_code = status.HTTP_404_NOT_FOUND
    elif exc.code in {"group_access_denied", "group_sender_invalid"}:
        status_code = status.HTTP_403_FORBIDDEN
    elif exc.code in {
        "group_message_idempotency_mismatch",
        "source_idempotency_mismatch",
        "command_idempotency_mismatch",
    }:
        status_code = status.HTTP_409_CONFLICT
    elif exc.code in {"group_planning_not_available", "runtime_v2_disabled"}:
        status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    else:
        status_code = status.HTTP_400_BAD_REQUEST
    return HTTPException(
        status_code=status_code,
        detail={"code": exc.code, "message": str(exc)},
    )


def _translate_file_error(exc: GroupFileServiceError) -> HTTPException:
    if exc.code in {"group_agent_not_found", "group_file_not_found"}:
        status_code = status.HTTP_404_NOT_FOUND
    elif exc.code in {"group_memory_write_denied"}:
        status_code = status.HTTP_403_FORBIDDEN
    elif exc.code in {"group_file_conflict", "group_workspace_directory_not_empty"}:
        status_code = status.HTTP_409_CONFLICT
    else:
        status_code = status.HTTP_400_BAD_REQUEST
    return HTTPException(
        status_code=status_code,
        detail={"code": exc.code, "message": str(exc)},
    )


def _text_file_out(value: group_file_service.GroupTextFile) -> GroupTextFileOut:
    return GroupTextFileOut(
        path=value.path,
        content=value.content,
        exists=value.exists,
        version_token=value.version_token,
        modified_at=value.modified_at,
        revision_id=value.revision_id,
    )


def _stage_audit(
    db: AsyncSession,
    *,
    current_user: User,
    action: str,
    tenant_id: uuid.UUID,
    group_id: uuid.UUID,
    details: dict | None = None,
) -> None:
    db.add(
        AuditLog(
            user_id=current_user.id,
            action=action,
            details={
                "tenant_id": str(tenant_id),
                "group_id": str(group_id),
                **(details or {}),
            },
        )
    )


def _group_session_out(session, *, unread_count: int = 0) -> GroupSessionOut:
    return GroupSessionOut(
        id=session.id,
        group_id=session.group_id,
        title=session.title,
        is_primary=bool(session.is_primary),
        unread_count=unread_count,
        created_by_participant_id=session.created_by_participant_id,
        created_at=session.created_at,
        updated_at=session.updated_at,
        last_message_at=session.last_message_at,
    )


async def _member_outputs(
    db: AsyncSession,
    memberships: list[GroupMember],
) -> list[GroupMemberOut]:
    participant_ids = [membership.participant_id for membership in memberships]
    if not participant_ids:
        return []
    participant_result = await db.execute(
        select(Participant).where(Participant.id.in_(participant_ids))
    )
    participants = {participant.id: participant for participant in participant_result.scalars().all()}

    agent_ref_ids = {
        participant.ref_id for participant in participants.values() if participant.type == "agent"
    }
    user_ref_ids = {
        participant.ref_id for participant in participants.values() if participant.type == "user"
    }
    agents: dict[uuid.UUID, Agent] = {}
    users: dict[uuid.UUID, User] = {}
    if agent_ref_ids:
        agent_result = await db.execute(select(Agent).where(Agent.id.in_(agent_ref_ids)))
        agents = {agent.id: agent for agent in agent_result.scalars().all()}
    if user_ref_ids:
        user_result = await db.execute(select(User).where(User.id.in_(user_ref_ids)))
        users = {user.id: user for user in user_result.scalars().all()}

    output: list[GroupMemberOut] = []
    for membership in memberships:
        participant = participants.get(membership.participant_id)
        if participant is None:
            continue
        agent = agents.get(participant.ref_id) if participant.type == "agent" else None
        user = users.get(participant.ref_id) if participant.type == "user" else None
        output.append(
            GroupMemberOut(
                id=membership.id,
                participant_id=participant.id,
                participant_type=participant.type,
                participant_ref_id=participant.ref_id,
                display_name=participant.display_name,
                avatar_url=participant.avatar_url,
                role=membership.role,
                role_description=agent.role_description if agent is not None else None,
                title=user.title if user is not None else None,
                joined_at=membership.joined_at,
            )
        )
    return output


def _parse_message_cursor(
    value: str | None,
    *,
    parameter: str = "before",
) -> tuple[datetime, uuid.UUID] | None:
    if value is None:
        return None
    timestamp_text, separator, message_id_text = value.rpartition("|")
    if not separator:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid `{parameter}` cursor. Use '<ISO 8601>|<message UUID>'.",
        )
    try:
        created_at = datetime.fromisoformat(timestamp_text.replace("Z", "+00:00"))
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=UTC)
        message_id = uuid.UUID(message_id_text)
    except (TypeError, ValueError):
        raise HTTPException(
            status_code=400,
            detail=f"Invalid `{parameter}` cursor. Use '<ISO 8601>|<message UUID>'.",
        ) from None
    return created_at, message_id


async def _message_outputs(
    db: AsyncSession,
    messages: list[ChatMessage],
) -> list[GroupMessageOut]:
    participant_ids = {message.participant_id for message in messages if message.participant_id}
    sender_names: dict[uuid.UUID, str] = {}
    if participant_ids:
        result = await db.execute(
            select(Participant).where(Participant.id.in_(participant_ids))
        )
        sender_names = {
            participant.id: participant.display_name for participant in result.scalars().all()
        }
    output = []
    for message in messages:
        if message.created_at is None:
            continue
        output.append(
            GroupMessageOut(
                id=message.id,
                role=message.role,
                content=message.content,
                participant_id=message.participant_id,
                sender_name=(
                    sender_names.get(message.participant_id)
                    if message.participant_id is not None
                    else None
                ),
                mentions=list(message.mentions or []),
                created_at=message.created_at,
                cursor=f"{message.created_at.isoformat()}|{message.id}",
            )
        )
    return output


@router.post("", response_model=GroupOut, status_code=status.HTTP_201_CREATED)
async def create_group(
    body: CreateGroupIn,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    tenant_id = _tenant_id(current_user)
    participant = await _current_participant(db, current_user)
    try:
        group = await group_chat_service.create_group(
            db,
            tenant_id=tenant_id,
            creator_participant_id=participant.id,
            name=body.name,
            description=body.description,
        )
    except GroupChatServiceError as exc:
        raise _translate_domain_error(exc) from exc
    _stage_audit(
        db,
        current_user=current_user,
        action="group:create",
        tenant_id=tenant_id,
        group_id=group.id,
    )
    return group


@router.get("", response_model=list[GroupOut])
async def list_groups(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    tenant_id = _tenant_id(current_user)
    participant = await _current_participant(db, current_user)
    return await group_chat_service.list_groups(
        db,
        tenant_id=tenant_id,
        participant_id=participant.id,
    )


@router.get("/{group_id}", response_model=GroupOut)
async def get_group(
    group_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    tenant_id = _tenant_id(current_user)
    participant = await _current_participant(db, current_user)
    try:
        return await group_chat_service.get_group(
            db,
            tenant_id=tenant_id,
            group_id=group_id,
            participant_id=participant.id,
        )
    except GroupChatServiceError as exc:
        raise _translate_domain_error(exc) from exc


@router.patch("/{group_id}", response_model=GroupOut)
async def patch_group(
    group_id: uuid.UUID,
    body: PatchGroupIn,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if "name" not in body.model_fields_set and "description" not in body.model_fields_set:
        raise HTTPException(status_code=400, detail="At least one field must be supplied")
    tenant_id = _tenant_id(current_user)
    participant = await _current_participant(db, current_user)
    try:
        group = await group_chat_service.update_group(
            db,
            tenant_id=tenant_id,
            group_id=group_id,
            actor_participant_id=participant.id,
            name=body.name if "name" in body.model_fields_set else None,
            description=body.description,
            update_description="description" in body.model_fields_set,
        )
    except GroupChatServiceError as exc:
        raise _translate_domain_error(exc) from exc
    _stage_audit(
        db,
        current_user=current_user,
        action="group:update",
        tenant_id=tenant_id,
        group_id=group_id,
        details={"fields": sorted(body.model_fields_set)},
    )
    return group


@router.delete("/{group_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_group(
    group_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    tenant_id = _tenant_id(current_user)
    participant = await _current_participant(db, current_user)
    try:
        await group_chat_service.soft_delete_group(
            db,
            tenant_id=tenant_id,
            group_id=group_id,
            actor_participant_id=participant.id,
        )
    except GroupChatServiceError as exc:
        raise _translate_domain_error(exc) from exc
    _stage_audit(
        db,
        current_user=current_user,
        action="group:delete",
        tenant_id=tenant_id,
        group_id=group_id,
    )
    return None


@router.get("/{group_id}/members", response_model=list[GroupMemberOut])
async def list_group_members(
    group_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    tenant_id = _tenant_id(current_user)
    participant = await _current_participant(db, current_user)
    try:
        memberships = await group_chat_service.list_group_members(
            db,
            tenant_id=tenant_id,
            group_id=group_id,
            actor_participant_id=participant.id,
        )
    except GroupChatServiceError as exc:
        raise _translate_domain_error(exc) from exc
    return await _member_outputs(db, memberships)


@router.get(
    "/{group_id}/member-candidates",
    response_model=list[GroupMemberCandidateOut],
)
async def list_group_member_candidates(
    group_id: uuid.UUID,
    participant_type: Annotated[Literal["user", "agent"], Query()],
    limit: Annotated[int, Query(ge=1, le=100)] = 100,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    tenant_id = _tenant_id(current_user)
    participant = await _current_participant(db, current_user)
    try:
        candidates = await group_chat_service.list_group_member_candidates(
            db,
            tenant_id=tenant_id,
            group_id=group_id,
            actor_participant_id=participant.id,
            actor_user=current_user,
            participant_type=participant_type,
            limit=limit,
        )
    except GroupChatServiceError as exc:
        raise _translate_domain_error(exc) from exc
    return [
        GroupMemberCandidateOut.model_validate(candidate, from_attributes=True)
        for candidate in candidates
    ]


@router.post(
    "/{group_id}/members",
    response_model=GroupMemberOut,
    status_code=status.HTTP_201_CREATED,
)
async def invite_group_member(
    group_id: uuid.UUID,
    body: InviteGroupMemberIn,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    tenant_id = _tenant_id(current_user)
    participant = await _current_participant(db, current_user)
    try:
        membership = await group_chat_service.invite_group_member(
            db,
            tenant_id=tenant_id,
            group_id=group_id,
            actor_participant_id=participant.id,
            participant_id=body.participant_id,
        )
        outputs = await _member_outputs(db, [membership])
    except GroupChatServiceError as exc:
        raise _translate_domain_error(exc) from exc
    if not outputs:
        raise HTTPException(status_code=409, detail="Participant identity is not available")
    _stage_audit(
        db,
        current_user=current_user,
        action="group:member_invite",
        tenant_id=tenant_id,
        group_id=group_id,
        details={"participant_id": str(body.participant_id)},
    )
    return outputs[0]


@router.delete("/{group_id}/members/{member_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_group_member(
    group_id: uuid.UUID,
    member_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    tenant_id = _tenant_id(current_user)
    participant = await _current_participant(db, current_user)
    try:
        removed = await group_chat_service.remove_group_member(
            db,
            tenant_id=tenant_id,
            group_id=group_id,
            actor_participant_id=participant.id,
            member_id=member_id,
        )
    except GroupChatServiceError as exc:
        raise _translate_domain_error(exc) from exc
    _stage_audit(
        db,
        current_user=current_user,
        action="group:member_remove",
        tenant_id=tenant_id,
        group_id=group_id,
        details={"member_id": str(member_id), "participant_id": str(removed.participant_id)},
    )
    return None


@router.get("/{group_id}/sessions", response_model=list[GroupSessionOut])
async def list_group_sessions(
    group_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    tenant_id = _tenant_id(current_user)
    participant = await _current_participant(db, current_user)
    try:
        sessions = await group_chat_service.list_group_sessions(
            db,
            tenant_id=tenant_id,
            group_id=group_id,
            actor_participant_id=participant.id,
        )
        output = []
        for session in sessions:
            unread_count = await group_chat_service.get_group_session_unread_count(
                db,
                tenant_id=tenant_id,
                group_id=group_id,
                session_id=session.id,
                participant_id=participant.id,
            )
            output.append(_group_session_out(session, unread_count=unread_count))
        return output
    except GroupChatServiceError as exc:
        raise _translate_domain_error(exc) from exc


@router.post(
    "/{group_id}/sessions",
    response_model=GroupSessionOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_group_session(
    group_id: uuid.UUID,
    body: CreateGroupSessionIn,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    tenant_id = _tenant_id(current_user)
    participant = await _current_participant(db, current_user)
    try:
        session = await group_chat_service.create_group_session(
            db,
            tenant_id=tenant_id,
            group_id=group_id,
            actor_participant_id=participant.id,
            title=body.title,
        )
    except GroupChatServiceError as exc:
        raise _translate_domain_error(exc) from exc
    _stage_audit(
        db,
        current_user=current_user,
        action="group:session_create",
        tenant_id=tenant_id,
        group_id=group_id,
        details={"session_id": str(session.id)},
    )
    return _group_session_out(session)


@router.patch("/{group_id}/sessions/{session_id}", response_model=GroupSessionOut)
async def patch_group_session(
    group_id: uuid.UUID,
    session_id: uuid.UUID,
    body: PatchGroupSessionIn,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    tenant_id = _tenant_id(current_user)
    participant = await _current_participant(db, current_user)
    try:
        session = await group_chat_service.update_group_session(
            db,
            tenant_id=tenant_id,
            group_id=group_id,
            session_id=session_id,
            actor_participant_id=participant.id,
            title=body.title,
        )
    except GroupChatServiceError as exc:
        raise _translate_domain_error(exc) from exc
    _stage_audit(
        db,
        current_user=current_user,
        action="group:session_update",
        tenant_id=tenant_id,
        group_id=group_id,
        details={"session_id": str(session_id)},
    )
    return _group_session_out(session)


@router.delete(
    "/{group_id}/sessions/{session_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_group_session(
    group_id: uuid.UUID,
    session_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    tenant_id = _tenant_id(current_user)
    participant = await _current_participant(db, current_user)
    try:
        deletion = await group_chat_service.soft_delete_group_session(
            db,
            tenant_id=tenant_id,
            group_id=group_id,
            session_id=session_id,
            actor_participant_id=participant.id,
        )
    except GroupChatServiceError as exc:
        raise _translate_domain_error(exc) from exc
    _stage_audit(
        db,
        current_user=current_user,
        action="group:session_delete",
        tenant_id=tenant_id,
        group_id=group_id,
        details={
            "session_id": str(session_id),
            "replacement_session_id": (
                str(deletion.replacement.id) if deletion.replacement is not None else None
            ),
            "cancelled_run_count": len(deletion.cancelled_run_ids),
        },
    )
    return None


@router.post(
    "/{group_id}/sessions/{session_id}/read",
    response_model=GroupReadStateOut,
)
async def mark_group_session_read(
    group_id: uuid.UUID,
    session_id: uuid.UUID,
    body: MarkGroupSessionReadIn,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    tenant_id = _tenant_id(current_user)
    participant = await _current_participant(db, current_user)
    try:
        result = await group_chat_service.mark_group_session_read(
            db,
            tenant_id=tenant_id,
            group_id=group_id,
            session_id=session_id,
            participant_id=participant.id,
            message_id=body.message_id,
        )
    except GroupChatServiceError as exc:
        raise _translate_domain_error(exc) from exc
    return GroupReadStateOut(
        session_id=result.session_id,
        last_read_message_id=result.last_read_message_id,
        advanced=result.advanced,
    )


@router.get(
    "/{group_id}/sessions/{session_id}/messages",
    response_model=list[GroupMessageOut],
)
async def list_group_messages(
    group_id: uuid.UUID,
    session_id: uuid.UUID,
    limit: Annotated[int, Query(ge=1, le=500)] = 20,
    before: Annotated[
        str | None,
        Query(description="Cursor '<created_at>|<id>' for the first excluded position"),
    ] = None,
    after: Annotated[
        str | None,
        Query(description="Cursor '<created_at>|<id>' for the last seen position"),
    ] = None,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    tenant_id = _tenant_id(current_user)
    participant = await _current_participant(db, current_user)
    try:
        messages = await group_message_service.list_group_messages(
            db,
            tenant_id=tenant_id,
            group_id=group_id,
            session_id=session_id,
            viewer_participant_id=participant.id,
            limit=limit,
            before=_parse_message_cursor(before, parameter="before"),
            after=_parse_message_cursor(after, parameter="after"),
        )
    except GroupMessageServiceError as exc:
        raise _translate_message_error(exc) from exc
    return await _message_outputs(db, messages)


@router.post(
    "/{group_id}/sessions/{session_id}/messages",
    response_model=GroupMessageIntakeOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_group_message(
    group_id: uuid.UUID,
    session_id: uuid.UUID,
    body: CreateGroupMessageIn,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    tenant_id = _tenant_id(current_user)
    participant = await _current_participant(db, current_user)
    try:
        intake = await group_message_service.enqueue_group_message(
            db,
            tenant_id=tenant_id,
            group_id=group_id,
            session_id=session_id,
            sender_participant_id=participant.id,
            content=body.content,
            mention_participant_ids=[mention.participant_id for mention in body.mentions],
            message_id=body.message_id,
        )
    except GroupMessageServiceError as exc:
        raise _translate_message_error(exc) from exc
    messages = await _message_outputs(db, [intake.message])
    if not messages:
        raise HTTPException(status_code=500, detail="Stored group message has no position")
    realtime_messages = await _message_outputs(db, list(intake.new_public_messages))
    # Realtime is a notification of durable state, never an uncommitted preview.
    # get_db's final commit is then a harmless no-op for this endpoint.
    await db.commit()
    for realtime_message in realtime_messages:
        await publish_group_message_created(
            group_id=group_id,
            session_id=session_id,
            message=realtime_message.model_dump(mode="json"),
        )
    return GroupMessageIntakeOut(
        message=messages[0],
        dispatch_kind=intake.dispatch_kind,
        run_ids=[handle.run_id for handle in intake.run_handles],
        created=intake.created,
        error_code=intake.error_code,
    )


@router.get(
    "/{group_id}/sessions/{session_id}/runs",
    response_model=list[GroupRunStateOut],
)
async def list_active_group_runs(
    group_id: uuid.UUID,
    session_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Return exact non-terminal Runs that should animate this group Session."""
    tenant_id = _tenant_id(current_user)
    participant = await _current_participant(db, current_user)
    try:
        await group_chat_service.authorize_group_session(
            db,
            tenant_id=tenant_id,
            group_id=group_id,
            session_id=session_id,
            participant_id=participant.id,
            human_only=True,
        )
    except GroupChatServiceError as exc:
        raise _translate_domain_error(exc) from exc

    terminal_event = exists(
        select(AgentRunEvent.id).where(
            AgentRunEvent.tenant_id == tenant_id,
            AgentRunEvent.run_id == AgentRun.id,
            AgentRunEvent.event_type.in_(("run_completed", "run_failed", "run_cancelled")),
        )
    )
    result = await db.execute(
        select(AgentRun)
        .where(
            AgentRun.tenant_id == tenant_id,
            AgentRun.session_id == session_id,
            AgentRun.source_type == "chat",
            AgentRun.runtime_type == "langgraph",
            ~terminal_event,
        )
        .order_by(AgentRun.created_at, AgentRun.id)
    )
    candidates = list(result.scalars().all())
    active: list[GroupRunStateOut] = []
    async with _open_run_state_reader(db) as reader:
        for run in candidates:
            try:
                view = await reader.get_run_state(tenant_id, run.id)
            except RunStateReadError:
                continue
            execution_status = view.execution_status or "created"
            if execution_status in {"completed", "failed", "cancelled"}:
                continue
            active.append(
                GroupRunStateOut(
                    run_id=run.id,
                    status=execution_status,
                    can_cancel=True,
                    agent_id=run.agent_id,
                    system_role=run.system_role,
                )
            )
    return active


@router.get(
    "/{group_id}/sessions/{session_id}/runs/{run_id}",
    response_model=GroupRunStateOut,
)
async def get_group_run_state(
    group_id: uuid.UUID,
    session_id: uuid.UUID,
    run_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    tenant_id = _tenant_id(current_user)
    participant = await _current_participant(db, current_user)
    try:
        run = await _authorized_group_run(
            db,
            tenant_id=tenant_id,
            group_id=group_id,
            session_id=session_id,
            participant_id=participant.id,
            run_id=run_id,
        )
    except GroupChatServiceError as exc:
        raise _translate_domain_error(exc) from exc
    try:
        async with _open_run_state_reader(db) as reader:
            view = await reader.get_run_state(tenant_id, run.id)
    except RunStateReadError as exc:
        raise HTTPException(status_code=409, detail=exc.code) from exc
    execution_status = view.execution_status or "created"
    return GroupRunStateOut(
        run_id=run.id,
        status=execution_status,
        can_cancel=execution_status not in {"completed", "failed", "cancelled"},
        agent_id=run.agent_id,
        system_role=run.system_role,
    )


@router.post(
    "/{group_id}/sessions/{session_id}/runs/{run_id}/cancel",
    response_model=GroupRunStateOut,
)
async def cancel_group_run(
    group_id: uuid.UUID,
    session_id: uuid.UUID,
    run_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    tenant_id = _tenant_id(current_user)
    participant = await _current_participant(db, current_user)
    try:
        run = await _authorized_group_run(
            db,
            tenant_id=tenant_id,
            group_id=group_id,
            session_id=session_id,
            participant_id=participant.id,
            run_id=run_id,
        )
    except GroupChatServiceError as exc:
        raise _translate_domain_error(exc) from exc
    try:
        async with _open_run_state_reader(db) as reader:
            view = await reader.get_run_state(tenant_id, run.id)
    except RunStateReadError as exc:
        raise HTTPException(status_code=409, detail=exc.code) from exc
    if view.execution_status in {"completed", "failed", "cancelled"}:
        raise HTTPException(status_code=409, detail="Group run is already terminal")

    await RuntimeCommandIntake(db).cancel_run(
        CancelRunCommand(
            tenant_id=tenant_id,
            run_id=run.id,
            idempotency_key=f"cancel:group:{run.id}:user:{current_user.id}",
            reason="cancelled_by_user",
            actor_user_id=current_user.id,
        )
    )
    return GroupRunStateOut(
        run_id=run.id,
        status="cancelling",
        can_cancel=False,
        agent_id=run.agent_id,
        system_role=run.system_role,
    )


@router.get("/{group_id}/announcement", response_model=GroupTextFileOut)
async def get_group_announcement(
    group_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    tenant_id = _tenant_id(current_user)
    participant = await _current_participant(db, current_user)
    try:
        value = await group_file_service.read_announcement(
            db,
            tenant_id=tenant_id,
            group_id=group_id,
            actor_participant_id=participant.id,
        )
    except GroupChatServiceError as exc:
        raise _translate_domain_error(exc) from exc
    except GroupFileServiceError as exc:
        raise _translate_file_error(exc) from exc
    return _text_file_out(value)


@router.put("/{group_id}/announcement", response_model=GroupTextFileOut)
async def put_group_announcement(
    group_id: uuid.UUID,
    body: GroupTextFileIn,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    tenant_id = _tenant_id(current_user)
    participant = await _current_participant(db, current_user)
    try:
        value = await group_file_service.write_announcement(
            db,
            tenant_id=tenant_id,
            group_id=group_id,
            actor_participant_id=participant.id,
            content=body.content,
            expected_version_token=body.expected_version_token,
        )
    except GroupChatServiceError as exc:
        raise _translate_domain_error(exc) from exc
    except GroupFileServiceError as exc:
        raise _translate_file_error(exc) from exc
    _stage_audit(
        db,
        current_user=current_user,
        action="group:announcement_update",
        tenant_id=tenant_id,
        group_id=group_id,
        details={"revision_id": str(value.revision_id) if value.revision_id else None},
    )
    return _text_file_out(value)


@router.get("/{group_id}/agents/{agent_id}/memory", response_model=GroupTextFileOut)
async def get_group_agent_memory(
    group_id: uuid.UUID,
    agent_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    tenant_id = _tenant_id(current_user)
    participant = await _current_participant(db, current_user)
    try:
        value = await group_file_service.read_agent_memory(
            db,
            tenant_id=tenant_id,
            group_id=group_id,
            actor_participant_id=participant.id,
            agent_id=agent_id,
        )
    except GroupChatServiceError as exc:
        raise _translate_domain_error(exc) from exc
    except GroupFileServiceError as exc:
        raise _translate_file_error(exc) from exc
    return _text_file_out(value)


@router.put("/{group_id}/agents/{agent_id}/memory", response_model=GroupTextFileOut)
async def put_group_agent_memory(
    group_id: uuid.UUID,
    agent_id: uuid.UUID,
    body: GroupTextFileIn,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    tenant_id = _tenant_id(current_user)
    participant = await _current_participant(db, current_user)
    try:
        value = await group_file_service.write_agent_memory(
            db,
            tenant_id=tenant_id,
            group_id=group_id,
            actor_participant_id=participant.id,
            agent_id=agent_id,
            content=body.content,
            expected_version_token=body.expected_version_token,
        )
    except GroupChatServiceError as exc:
        raise _translate_domain_error(exc) from exc
    except GroupFileServiceError as exc:
        raise _translate_file_error(exc) from exc
    _stage_audit(
        db,
        current_user=current_user,
        action="group:memory_update",
        tenant_id=tenant_id,
        group_id=group_id,
        details={
            "agent_id": str(agent_id),
            "revision_id": str(value.revision_id) if value.revision_id else None,
        },
    )
    return _text_file_out(value)


@router.delete(
    "/{group_id}/agents/{agent_id}/memory",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_group_agent_memory(
    group_id: uuid.UUID,
    agent_id: uuid.UUID,
    expected_version_token: Annotated[str | None, Query()] = None,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    tenant_id = _tenant_id(current_user)
    participant = await _current_participant(db, current_user)
    try:
        await group_file_service.delete_agent_memory(
            db,
            tenant_id=tenant_id,
            group_id=group_id,
            actor_participant_id=participant.id,
            agent_id=agent_id,
            expected_version_token=expected_version_token,
        )
    except GroupChatServiceError as exc:
        raise _translate_domain_error(exc) from exc
    except GroupFileServiceError as exc:
        raise _translate_file_error(exc) from exc
    _stage_audit(
        db,
        current_user=current_user,
        action="group:memory_delete",
        tenant_id=tenant_id,
        group_id=group_id,
        details={"agent_id": str(agent_id)},
    )
    return None


@router.get(
    "/{group_id}/sessions/{session_id}/summary",
    response_model=GroupSessionSummaryOut,
)
async def get_group_session_summary(
    group_id: uuid.UUID,
    session_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    tenant_id = _tenant_id(current_user)
    participant = await _current_participant(db, current_user)
    try:
        await group_chat_service.authorize_group_session(
            db,
            tenant_id=tenant_id,
            group_id=group_id,
            session_id=session_id,
            participant_id=participant.id,
            human_only=True,
        )
        snapshot = await SessionContextService().load_snapshot(
            db,
            tenant_id=tenant_id,
            session_id=session_id,
        )
    except GroupChatServiceError as exc:
        raise _translate_domain_error(exc) from exc
    except SessionContextError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": exc.code, "message": str(exc)},
        ) from exc
    return GroupSessionSummaryOut.model_validate(snapshot.to_json())


@router.get("/{group_id}/workspace", response_model=list[GroupWorkspaceEntryOut])
async def list_group_workspace(
    group_id: uuid.UUID,
    path: Annotated[str, Query(max_length=500)] = "",
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    tenant_id = _tenant_id(current_user)
    participant = await _current_participant(db, current_user)
    try:
        entries = await group_file_service.list_workspace(
            db,
            tenant_id=tenant_id,
            group_id=group_id,
            actor_participant_id=participant.id,
            path=path,
        )
    except GroupChatServiceError as exc:
        raise _translate_domain_error(exc) from exc
    except GroupFileServiceError as exc:
        raise _translate_file_error(exc) from exc
    return [GroupWorkspaceEntryOut.model_validate(entry, from_attributes=True) for entry in entries]


@router.get("/{group_id}/workspace/file", response_model=GroupTextFileOut)
async def get_group_workspace_file(
    group_id: uuid.UUID,
    path: Annotated[str, Query(min_length=1, max_length=500)],
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    tenant_id = _tenant_id(current_user)
    participant = await _current_participant(db, current_user)
    try:
        value = await group_file_service.read_workspace_file(
            db,
            tenant_id=tenant_id,
            group_id=group_id,
            actor_participant_id=participant.id,
            path=path,
        )
    except GroupChatServiceError as exc:
        raise _translate_domain_error(exc) from exc
    except GroupFileServiceError as exc:
        raise _translate_file_error(exc) from exc
    return _text_file_out(value)


@router.put("/{group_id}/workspace/file", response_model=GroupTextFileOut)
async def put_group_workspace_file(
    group_id: uuid.UUID,
    body: GroupWorkspaceFileIn,
    path: Annotated[str, Query(min_length=1, max_length=500)],
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    tenant_id = _tenant_id(current_user)
    participant = await _current_participant(db, current_user)
    try:
        value = await group_file_service.write_workspace_file(
            db,
            tenant_id=tenant_id,
            group_id=group_id,
            actor_participant_id=participant.id,
            path=path,
            content=body.content,
            expected_version_token=body.expected_version_token,
            require_absent=body.require_absent,
        )
    except GroupChatServiceError as exc:
        raise _translate_domain_error(exc) from exc
    except GroupFileServiceError as exc:
        raise _translate_file_error(exc) from exc
    _stage_audit(
        db,
        current_user=current_user,
        action="group:workspace_write",
        tenant_id=tenant_id,
        group_id=group_id,
        details={
            "path": value.path,
            "revision_id": str(value.revision_id) if value.revision_id else None,
        },
    )
    return _text_file_out(value)


@router.delete("/{group_id}/workspace/file", status_code=status.HTTP_204_NO_CONTENT)
async def delete_group_workspace_file(
    group_id: uuid.UUID,
    path: Annotated[str, Query(min_length=1, max_length=500)],
    expected_version_token: Annotated[str | None, Query()] = None,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    tenant_id = _tenant_id(current_user)
    participant = await _current_participant(db, current_user)
    try:
        await group_file_service.delete_workspace_file(
            db,
            tenant_id=tenant_id,
            group_id=group_id,
            actor_participant_id=participant.id,
            path=path,
            expected_version_token=expected_version_token,
        )
    except GroupChatServiceError as exc:
        raise _translate_domain_error(exc) from exc
    except GroupFileServiceError as exc:
        raise _translate_file_error(exc) from exc
    _stage_audit(
        db,
        current_user=current_user,
        action="group:workspace_delete",
        tenant_id=tenant_id,
        group_id=group_id,
        details={"path": path},
    )
    return None
