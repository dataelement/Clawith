"""Group membership, immutable conversation events and per-Agent outcomes."""

import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from hashlib import sha256
from typing import Protocol
from uuid import UUID, uuid4

from sqlalchemy import Text, cast, func, select, update

from app.infrastructure.errors import AccessDenied, Conflict, InvalidInput, NotFound
from app.infrastructure.transactions import TransactionContext
from app.modules.agent.public import AgentMetadataView, AgentService
from app.modules.group.attachments import (
    GroupAttachmentBlob,
    GroupAttachmentDelegation,
    GroupAttachmentObject,
    GroupAttachmentService,
    GroupAttachmentStorage,
    GroupAttachmentView,
    GroupRunAttachmentAuthorizer,
)
from app.modules.group.models import (
    GroupAgentRecord,
    GroupConversationRecord,
    GroupEventRecord,
    GroupMembershipRecord,
    GroupReadRecord,
    GroupRecord,
    GroupRunLinkRecord,
)
from app.modules.identity_tenant.public import IdentityService, InvitationCandidate, TenantPrincipal
from app.modules.run.public import (
    InputContent,
    InputReference,
    RunService,
    RunView,
    SourceIdentity,
    TerminalOutcomePayload,
    TransitionResult,
    WaitingPayload,
)
from app.modules.tool.public import (
    EnabledSources,
    PersonalAccountSelection,
    ToolService,
    decode_personal_selections,
    encode_personal_selections,
)

__all__ = [
    "AcceptedGroupInput",
    "GroupAttachmentBlob",
    "GroupAttachmentDelegation",
    "GroupAttachmentObject",
    "GroupAttachmentService",
    "GroupAttachmentStorage",
    "GroupAttachmentView",
    "GroupConversationView",
    "GroupDeliveryPage",
    "GroupDeliveryScope",
    "GroupEventView",
    "GroupExternalMessageAuthorizer",
    "GroupMemberView",
    "GroupRunAttachmentAuthorizer",
    "GroupRunLinkView",
    "GroupService",
    "GroupView",
]


@dataclass(frozen=True, slots=True)
class GroupView:
    id: UUID
    tenant_id: UUID
    name: str
    announcement: str
    enabled: bool


@dataclass(frozen=True, slots=True)
class GroupEventView:
    id: UUID
    group_id: UUID
    position: int
    kind: str
    input: InputContent
    membership_id: UUID | None
    agent_id: UUID | None
    origin_event_id: UUID | None
    source_run_id: UUID | None
    waiting_reference: str | None
    related_run_id: UUID | None
    step_id: str | None
    call_id: str | None
    conversation_id: UUID
    mentioned_membership_ids: tuple[UUID, ...]


@dataclass(frozen=True, slots=True)
class GroupRunLinkView:
    id: UUID
    tenant_id: UUID
    group_id: UUID
    event_id: UUID
    agent_id: UUID
    run_id: UUID | None
    admission: str
    admission_error: str | None
    result: dict[str, object] | None
    conversation_id: UUID


@dataclass(frozen=True, slots=True)
class GroupConversationView:
    id: UUID
    group_id: UUID
    title: str
    is_default: bool
    enabled: bool
    head_position: int
    read_position: int
    unread_count: int


@dataclass(frozen=True, slots=True)
class GroupMemberView:
    kind: str
    id: UUID
    enabled: bool


@dataclass(frozen=True, slots=True)
class AcceptedGroupInput:
    event: GroupEventView
    links: tuple[GroupRunLinkView, ...]
    created: bool


@dataclass(frozen=True, slots=True)
class GroupDeliveryPage:
    entries: tuple[GroupEventView, ...]
    next_after_position: int
    has_more: bool


@dataclass(frozen=True, slots=True)
class GroupDeliveryScope:
    tenant_id: UUID
    group_id: UUID


def _input(value: object) -> InputContent:
    if not isinstance(value, dict) or set(value) != {"text", "references"}:
        raise InvalidInput("Group input has an unsupported shape")
    text, references = value["text"], value["references"]
    if not isinstance(text, str) or not isinstance(references, (list, tuple)) or len(references) > 100:
        raise InvalidInput("Group input is invalid")
    parsed = []
    for reference in references:
        if not isinstance(reference, dict) or set(reference) != {"reference", "name", "media_type"}:
            raise InvalidInput("Group reference is invalid")
        if not isinstance(reference["reference"], str) or not reference["reference"]:
            raise InvalidInput("Group reference requires an identity")
        if any(v is not None and not isinstance(v, str) for v in reference.values()):
            raise InvalidInput("Group reference fields are invalid")
        parsed.append(InputReference(**reference))
    if len(json.dumps(value, ensure_ascii=False).encode()) > 256 * 1024:
        raise InvalidInput("Group input exceeds its byte bound")
    return InputContent(text, tuple(parsed))


def _event(row: GroupEventRecord) -> GroupEventView:
    if row.conversation_id is None:
        raise InvalidInput("Group event requires a conversation")
    if (row.payload_version != 1 or row.kind not in ("input", "reply") or not isinstance(row.payload, dict)
            or set(row.payload) != {"input", "waiting_reference", "related_run_id", "step_id", "call_id", "account_selections", "mentioned_membership_ids"}):
        raise InvalidInput("Group event has an unsupported persisted format")
    decode_personal_selections(row.payload["account_selections"])
    waiting = row.payload["waiting_reference"]
    if waiting is not None and (not isinstance(waiting, str) or not waiting or len(waiting) > 512):
        raise InvalidInput("Group wait reference is invalid")
    related = row.payload["related_run_id"]
    try:
        related_id = UUID(related) if isinstance(related, str) else None
    except ValueError:
        raise InvalidInput("Group reply execution identity is invalid") from None
    if related is not None and related_id is None:
        raise InvalidInput("Group reply execution identity is invalid")
    for field in ("step_id", "call_id"):
        value = row.payload[field]
        if value is not None and (not isinstance(value, str) or not value or len(value) > 256):
            raise InvalidInput("Group message source correlation is invalid")
    mentions = row.payload["mentioned_membership_ids"]
    if not isinstance(mentions, list) or len(mentions) > 100:
        raise InvalidInput("Group mentions are invalid")
    try:
        parsed_mentions = tuple(UUID(value) for value in mentions if isinstance(value, str))
    except ValueError:
        raise InvalidInput("Group mentions are invalid") from None
    if len(parsed_mentions) != len(mentions) or len(set(parsed_mentions)) != len(mentions):
        raise InvalidInput("Group mentions are invalid")
    return GroupEventView(row.id, row.group_id, row.position, row.kind, _input(row.payload["input"]),
        row.membership_id, row.agent_id, row.origin_event_id, row.source_run_id, waiting, related_id,
        row.payload["step_id"], row.payload["call_id"], row.conversation_id, parsed_mentions)


def _link(row: GroupRunLinkRecord) -> GroupRunLinkView:
    if row.conversation_id is None:
        raise InvalidInput("Group execution requires a conversation")
    if row.result_version != 1 or row.admission not in ("pending", "started", "failed"):
        raise InvalidInput("Group execution link has an unsupported persisted format")
    if row.result is not None and (not isinstance(row.result, dict) or set(row.result) != {"status", "reason", "run_id"}
            or row.result["status"] not in ("Completed", "Failed", "Cancelled", "Interrupted")
            or not isinstance(row.result["run_id"], str)
            or (row.result["reason"] is not None and not isinstance(row.result["reason"], str))):
        raise InvalidInput("Group outcome has an unsupported shape")
    return GroupRunLinkView(row.id, row.tenant_id, row.group_id, row.event_id, row.agent_id, row.run_id,
        row.admission, row.admission_error, json.loads(json.dumps(row.result)) if row.result is not None else None,
        row.conversation_id)


class GroupExternalMessageAuthorizer(Protocol):
    async def __call__(self, transaction: TransactionContext, *, run: RunView,
        target_id: UUID, conversation_id: UUID | None, input: InputContent) -> None: ...


class GroupService:
    def __init__(self, transaction: TransactionContext, *, enabled_sources: EnabledSources | None = None) -> None:
        self.tx, self.session = transaction, transaction.session
        self._enabled_sources = enabled_sources

    async def set_agent(self, principal: TenantPrincipal, *, group_id: UUID, agent_id: UUID, enabled: bool) -> None:
        if type(enabled) is not bool:
            raise InvalidInput("Agent membership state is invalid")
        group = await self._authorized(principal, group_id, lock=True)
        await AgentService(self.tx).require_execution_ids(principal, agent_ids=(agent_id,))
        if not enabled and not (principal.can_manage_all_agents or group.created_by_membership_id == principal.membership_id):
            raise AccessDenied("Only the Group creator or administrator may remove an Agent")
        row = await self.session.scalar(select(GroupAgentRecord).where(GroupAgentRecord.tenant_id == principal.tenant_id,
            GroupAgentRecord.group_id == group_id, GroupAgentRecord.agent_id == agent_id))
        now = datetime.now(UTC)
        if row is None:
            row = GroupAgentRecord(id=uuid4(), tenant_id=principal.tenant_id, group_id=group_id, agent_id=agent_id,
                enabled=enabled, created_at=now, updated_at=now)
            self.session.add(row)
        else:
            row.enabled, row.updated_at = enabled, now
        await self.session.flush()

    async def authorize_destination(self, principal: TenantPrincipal, *, group_id: UUID,
            agent_id: UUID, conversation_id: UUID | None = None) -> UUID:
        """Resolve a human-selected delivery destination without granting another Group."""
        selected = await self.resolve_conversation(principal, group_id=group_id, conversation_id=conversation_id)
        await AgentService(self.tx).require_execution_ids(principal, agent_ids=(agent_id,))
        await self._delivery_membership(principal.tenant_id, group_id, agent_id)
        return selected

    async def _delivery_membership(self, tenant_id: UUID, group_id: UUID, agent_id: UUID) -> None:
        member = await self.session.scalar(select(GroupAgentRecord.id).where(GroupAgentRecord.tenant_id == tenant_id,
            GroupAgentRecord.group_id == group_id, GroupAgentRecord.agent_id == agent_id, GroupAgentRecord.enabled.is_(True)))
        if member is None:
            raise AccessDenied("The delivering Agent is not a member of this Group")

    async def execution_destination(self, run: RunView) -> tuple[UUID, UUID]:
        """Native configuration can reuse only its actual Group conversation."""
        group_id, conversation_id = await self.execution_conversation(run)
        await self._delivery_membership(run.tenant_id, group_id, run.agent_id)
        conversation = await self.session.scalar(select(GroupConversationRecord.id).join(GroupRecord,
            (GroupRecord.tenant_id == GroupConversationRecord.tenant_id) & (GroupRecord.id == GroupConversationRecord.group_id)).where(
            GroupConversationRecord.tenant_id == run.tenant_id, GroupConversationRecord.group_id == group_id,
            GroupConversationRecord.id == conversation_id, GroupConversationRecord.enabled.is_(True), GroupRecord.enabled.is_(True)))
        if conversation is None:
            raise AccessDenied("Group delivery destination is unavailable")
        return group_id, conversation_id

    async def invitation_candidates(self, principal: TenantPrincipal, *, group_id: UUID,
            kind: str, offset: int = 0, limit: int = 100) -> tuple[InvitationCandidate, ...] | tuple[AgentMetadataView, ...]:
        await self._authorized(principal, group_id)
        self._page(offset, limit)
        if kind == "human":
            return await IdentityService(self.tx).invitation_candidates(principal, offset=offset, limit=limit)
        if kind == "agent":
            return await AgentService(self.tx).list_visible_metadata(principal, offset=offset, limit=limit)
        raise InvalidInput("Unknown Group member kind")

    async def authorized_group_ids(self, principal: TenantPrincipal, *, group_ids: tuple[UUID, ...]) -> frozenset[UUID]:
        """Filter a bounded product-result page without exposing other Groups' contents."""
        if len(group_ids) > 100:
            raise InvalidInput("Group visibility batch exceeds its bound")
        query = select(GroupRecord.id).join(GroupMembershipRecord,
            (GroupMembershipRecord.tenant_id == GroupRecord.tenant_id) & (GroupMembershipRecord.group_id == GroupRecord.id)).where(
            GroupRecord.tenant_id == principal.tenant_id, GroupRecord.id.in_(group_ids), GroupRecord.enabled.is_(True),
            GroupMembershipRecord.membership_id == principal.membership_id, GroupMembershipRecord.enabled.is_(True))
        return frozenset((await self.session.scalars(query)).all())

    async def event_conversation(self, *, tenant_id: UUID, group_id: UUID, event_id: UUID) -> UUID:
        """Return the actual event's topic for already-authorized input composition."""
        value = await self.session.scalar(select(GroupEventRecord.conversation_id).where(
            GroupEventRecord.tenant_id == tenant_id, GroupEventRecord.group_id == group_id, GroupEventRecord.id == event_id))
        if value is None:
            raise NotFound("Group event conversation is unavailable")
        return value

    async def list_members(self, principal: TenantPrincipal, *, group_id: UUID,
            kind: str = "human", offset: int = 0, limit: int = 100) -> tuple[GroupMemberView, ...]:
        await self._authorized(principal, group_id)
        self._page(offset, limit)
        if kind == "human":
            query = select(GroupMembershipRecord.membership_id).where(GroupMembershipRecord.tenant_id == principal.tenant_id,
                GroupMembershipRecord.group_id == group_id, GroupMembershipRecord.enabled.is_(True))
            query = query.order_by(GroupMembershipRecord.membership_id)
        elif kind == "agent":
            query = select(GroupAgentRecord.agent_id).where(GroupAgentRecord.tenant_id == principal.tenant_id,
                GroupAgentRecord.group_id == group_id, GroupAgentRecord.enabled.is_(True))
            if not principal.can_manage_all_agents:
                query = query.where(GroupAgentRecord.agent_id.in_(principal.allowed_agent_ids))
            query = query.order_by(GroupAgentRecord.agent_id)
        else:
            raise InvalidInput("Unknown Group member kind")
        return tuple(GroupMemberView(kind, value, True) for value in (await self.session.scalars(query.offset(offset).limit(limit))).all())

    async def create_conversation(self, principal: TenantPrincipal, *, group_id: UUID, title: str) -> GroupConversationView:
        await self._authorized(principal, group_id, lock=True)
        self._title(title)
        now = datetime.now(UTC)
        row = GroupConversationRecord(id=uuid4(), tenant_id=principal.tenant_id, group_id=group_id, title=title,
            is_default=False, enabled=True, created_by_membership_id=principal.membership_id, created_at=now, updated_at=now)
        self.session.add(row)
        await self.session.flush()
        return GroupConversationView(row.id, group_id, title, False, True, 0, 0, 0)

    async def update_conversation(self, principal: TenantPrincipal, *, group_id: UUID, conversation_id: UUID,
            title: str, enabled: bool = True) -> None:
        group = await self._authorized(principal, group_id, lock=True)
        self._title(title)
        if type(enabled) is not bool:
            raise InvalidInput("Conversation availability is invalid")
        row = await self._conversation(principal.tenant_id, group_id, conversation_id, allow_disabled=True)
        if enabled != row.enabled:
            if enabled:
                raise Conflict("Removed conversations cannot be reopened")
            self._manager(principal, group)
            await self._close_conversation(principal, row)
        row.title, row.updated_at = title, datetime.now(UTC)
        await self.session.flush()

    async def delete_conversation(self, principal: TenantPrincipal, *, group_id: UUID, conversation_id: UUID) -> None:
        """Commit this admission barrier before paging and cancelling linked Runs."""
        group = await self._authorized(principal, group_id, lock=True)
        self._manager(principal, group)
        row = await self._conversation(principal.tenant_id, group_id, conversation_id, allow_disabled=True)
        if row.enabled:
            await self._close_conversation(principal, row)

    async def _close_conversation(self, principal: TenantPrincipal, row: GroupConversationRecord) -> None:
        was_default = row.is_default
        row.enabled, row.is_default, row.updated_at = False, False, datetime.now(UTC)
        await self.session.flush()
        if was_default:
            replacement = await self.session.scalar(select(GroupConversationRecord).where(
                GroupConversationRecord.tenant_id == principal.tenant_id, GroupConversationRecord.group_id == row.group_id,
                GroupConversationRecord.enabled.is_(True)).order_by(GroupConversationRecord.created_at, GroupConversationRecord.id).limit(1))
            if replacement is None:
                now = datetime.now(UTC)
                replacement = GroupConversationRecord(id=uuid4(), tenant_id=principal.tenant_id, group_id=row.group_id,
                    title="General", enabled=True, is_default=True, created_by_membership_id=principal.membership_id,
                    created_at=now, updated_at=now)
                self.session.add(replacement)
            else:
                replacement.is_default = True
        await self.session.execute(update(GroupRunLinkRecord).where(GroupRunLinkRecord.tenant_id == principal.tenant_id,
            GroupRunLinkRecord.group_id == row.group_id, GroupRunLinkRecord.conversation_id == row.id,
            GroupRunLinkRecord.admission == "pending").values(admission="failed", admission_error="conversation_removed",
                updated_at=datetime.now(UTC)))
        await self.session.flush()

    async def conversation_cancellation_page(self, principal: TenantPrincipal, *, group_id: UUID,
            conversation_id: UUID, after_id: UUID | None = None, limit: int = 100) -> tuple[UUID, ...]:
        group = await self._authorized(principal, group_id)
        self._manager(principal, group)
        self._page(0, limit)
        row = await self._conversation(principal.tenant_id, group_id, conversation_id, allow_disabled=True)
        if row.enabled:
            raise Conflict("Conversation admission must close before cancellation")
        query = select(GroupRunLinkRecord.run_id).where(GroupRunLinkRecord.tenant_id == principal.tenant_id,
            GroupRunLinkRecord.group_id == group_id, GroupRunLinkRecord.conversation_id == conversation_id,
            GroupRunLinkRecord.run_id.is_not(None))
        if after_id is not None:
            query = query.where(GroupRunLinkRecord.run_id > after_id)
        return tuple(value for value in (await self.session.scalars(query.order_by(GroupRunLinkRecord.run_id).limit(limit))).all()
            if value is not None)

    async def cancel_removed_conversation_work(self, principal: TenantPrincipal, *, group_id: UUID,
            conversation_id: UUID, run_id: UUID) -> TransitionResult:
        run = await RunService(self.tx).lock_main(tenant_id=principal.tenant_id, run_id=run_id)
        link = await self._for_run(run)
        group = await self._authorized(principal, group_id)
        self._manager(principal, group)
        if link.group_id != group_id or link.conversation_id != conversation_id:
            raise AccessDenied("Execution belongs to another conversation")
        row = await self._conversation(principal.tenant_id, group_id, conversation_id, allow_disabled=True)
        if row.enabled:
            raise Conflict("Conversation is not removed")
        return await RunService(self.tx).terminate(tenant_id=principal.tenant_id, run_id=run_id,
            status="Cancelled", reason="group_conversation_removed", consumer=self)

    @staticmethod
    def _manager(principal: TenantPrincipal, group: GroupRecord) -> None:
        if not (principal.can_manage_all_agents or group.created_by_membership_id == principal.membership_id):
            raise AccessDenied("Only the Group creator or administrator may remove a conversation")

    async def list_conversations(self, principal: TenantPrincipal, *, group_id: UUID,
            offset: int = 0, limit: int = 100) -> tuple[GroupConversationView, ...]:
        await self._authorized(principal, group_id)
        self._page(offset, limit)
        rows = (await self.session.scalars(select(GroupConversationRecord).where(
            GroupConversationRecord.tenant_id == principal.tenant_id, GroupConversationRecord.group_id == group_id,
            GroupConversationRecord.enabled.is_(True)).order_by(GroupConversationRecord.id).offset(offset).limit(limit))).all()
        ids = [row.id for row in rows]
        reads = {key: value for key, value in (await self.session.execute(select(GroupReadRecord.conversation_id, GroupReadRecord.through_position).where(
            GroupReadRecord.tenant_id == principal.tenant_id, GroupReadRecord.membership_id == principal.membership_id,
            GroupReadRecord.conversation_id.in_(ids)))).all()}
        heads = {key: value for key, value in (await self.session.execute(select(GroupEventRecord.conversation_id, func.max(GroupEventRecord.position)).where(
            GroupEventRecord.tenant_id == principal.tenant_id, GroupEventRecord.conversation_id.in_(ids))
            .group_by(GroupEventRecord.conversation_id))).all()}
        from sqlalchemy import or_
        unread = {key: value for key, value in (await self.session.execute(select(GroupEventRecord.conversation_id, func.count()).outerjoin(
            GroupReadRecord, (GroupReadRecord.tenant_id == GroupEventRecord.tenant_id)
            & (GroupReadRecord.conversation_id == GroupEventRecord.conversation_id)
            & (GroupReadRecord.membership_id == principal.membership_id)).where(
                GroupEventRecord.tenant_id == principal.tenant_id, GroupEventRecord.conversation_id.in_(ids),
                GroupEventRecord.position > func.coalesce(GroupReadRecord.through_position, 0),
                or_(GroupEventRecord.membership_id.is_(None), GroupEventRecord.membership_id != principal.membership_id))
            .group_by(GroupEventRecord.conversation_id))).all()}
        return tuple(GroupConversationView(row.id, group_id, row.title, row.is_default, row.enabled,
            heads.get(row.id, 0), reads.get(row.id, 0), unread.get(row.id, 0)) for row in rows)

    async def mark_read(self, principal: TenantPrincipal, *, group_id: UUID, conversation_id: UUID,
            through_position: int) -> int:
        await self._authorized(principal, group_id, lock=True)
        await self._conversation(principal.tenant_id, group_id, conversation_id)
        if type(through_position) is not int or through_position < 0:
            raise InvalidInput("Read position is invalid")
        if through_position:
            position = await self.session.scalar(select(GroupEventRecord.position).where(
                GroupEventRecord.tenant_id == principal.tenant_id, GroupEventRecord.group_id == group_id,
                GroupEventRecord.conversation_id == conversation_id, GroupEventRecord.position == through_position))
            if position is None:
                raise InvalidInput("Read position must identify an event in this conversation")
        row = await self.session.scalar(select(GroupReadRecord).where(GroupReadRecord.tenant_id == principal.tenant_id,
            GroupReadRecord.conversation_id == conversation_id, GroupReadRecord.membership_id == principal.membership_id))
        now = datetime.now(UTC)
        if row is None:
            row = GroupReadRecord(id=uuid4(), tenant_id=principal.tenant_id, group_id=group_id,
                conversation_id=conversation_id, membership_id=principal.membership_id,
                through_position=through_position, created_at=now, updated_at=now)
            self.session.add(row)
        else:
            row.through_position, row.updated_at = max(row.through_position, through_position), now
        await self.session.flush()
        return row.through_position

    async def list_work(self, principal: TenantPrincipal, *, group_id: UUID, conversation_id: UUID | None = None,
            after_id: UUID | None = None, limit: int = 100) -> tuple[GroupRunLinkView, ...]:
        await self._authorized(principal, group_id)
        self._page(0, limit)
        conversation = await self._conversation(principal.tenant_id, group_id, conversation_id)
        query = select(GroupRunLinkRecord).where(GroupRunLinkRecord.tenant_id == principal.tenant_id,
            GroupRunLinkRecord.group_id == group_id, GroupRunLinkRecord.conversation_id == conversation.id)
        if not principal.can_manage_all_agents:
            query = query.where(GroupRunLinkRecord.agent_id.in_(principal.allowed_agent_ids))
        if after_id is not None:
            query = query.where(GroupRunLinkRecord.id > after_id)
        return tuple(_link(row) for row in (await self.session.scalars(query.order_by(GroupRunLinkRecord.id).limit(limit))).all())

    async def cancel_work(self, principal: TenantPrincipal, *, group_id: UUID, run_id: UUID) -> TransitionResult:
        run = await RunService(self.tx).lock_main(tenant_id=principal.tenant_id, run_id=run_id)
        link = await self._for_run(run)
        if link.group_id != group_id:
            raise AccessDenied("Execution belongs to another Group")
        await self._authorized(principal, group_id)
        await AgentService(self.tx).require_execution_ids(principal, agent_ids=(run.agent_id,))
        return await RunService(self.tx).terminate(tenant_id=principal.tenant_id, run_id=run_id,
            status="Cancelled", reason="group_member_cancelled", consumer=self)

    async def _conversation(self, tenant_id: UUID, group_id: UUID, conversation_id: UUID | None,
            *, allow_disabled: bool = False) -> GroupConversationRecord:
        query = select(GroupConversationRecord).where(GroupConversationRecord.tenant_id == tenant_id,
            GroupConversationRecord.group_id == group_id)
        query = query.where(GroupConversationRecord.is_default.is_(True)) if conversation_id is None else query.where(GroupConversationRecord.id == conversation_id)
        row = await self.session.scalar(query)
        if row is None or (not row.enabled and not allow_disabled):
            raise NotFound("Group conversation is unavailable")
        return row

    @staticmethod
    def _page(offset: int, limit: int) -> None:
        if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 100:
            raise InvalidInput("Group page is invalid")

    @staticmethod
    def _title(title: str) -> None:
        if not title.strip() or len(title) > 200:
            raise InvalidInput("Conversation title is invalid")

    async def create(self, principal: TenantPrincipal, *, name: str, announcement: str = "") -> GroupView:
        if not name.strip() or len(name) > 200 or len(announcement) > 16384:
            raise InvalidInput("Group name or announcement exceeds its bound")
        now = datetime.now(UTC)
        row = GroupRecord(id=uuid4(), tenant_id=principal.tenant_id, name=name, announcement=announcement,
            next_position=1, enabled=True, created_at=now, updated_at=now, created_by_membership_id=principal.membership_id)
        await IdentityService(self.tx).require_membership(tenant_id=principal.tenant_id, membership_id=principal.membership_id)
        self.session.add(row)
        await self.session.flush()
        self.session.add(GroupMembershipRecord(id=uuid4(), tenant_id=principal.tenant_id, group_id=row.id,
            membership_id=principal.membership_id, enabled=True, created_at=now, updated_at=now))
        self.session.add(GroupConversationRecord(id=uuid4(), tenant_id=principal.tenant_id, group_id=row.id,
            title="General", is_default=True, enabled=True, created_by_membership_id=principal.membership_id,
            created_at=now, updated_at=now))
        await self.session.flush()
        return GroupView(row.id, row.tenant_id, row.name, row.announcement, row.enabled)

    async def get(self, principal: TenantPrincipal, *, group_id: UUID) -> GroupView:
        row = await self._authorized(principal, group_id)
        return GroupView(row.id, row.tenant_id, row.name, row.announcement, row.enabled)

    async def list_groups(self, principal: TenantPrincipal, *, after_id: UUID | None = None,
            limit: int = 100) -> tuple[GroupView, ...]:
        if type(limit) is not int or not 1 <= limit <= 100:
            raise InvalidInput("Group page is invalid")
        query = select(GroupRecord).join(GroupMembershipRecord,
            (GroupMembershipRecord.tenant_id == GroupRecord.tenant_id) & (GroupMembershipRecord.group_id == GroupRecord.id)).where(
                GroupRecord.tenant_id == principal.tenant_id, GroupRecord.enabled.is_(True),
                GroupMembershipRecord.membership_id == principal.membership_id, GroupMembershipRecord.enabled.is_(True))
        if after_id is not None:
            query = query.where(GroupRecord.id > after_id)
        return tuple(GroupView(row.id, row.tenant_id, row.name, row.announcement, row.enabled)
            for row in (await self.session.scalars(query.order_by(GroupRecord.id).limit(limit))).all())

    async def set_membership(self, principal: TenantPrincipal, *, group_id: UUID,
            membership_id: UUID, enabled: bool) -> None:
        if type(enabled) is not bool:
            raise InvalidInput("Group membership state is invalid")
        group = await self._authorized(principal, group_id, lock=True)
        if not enabled and not (
                principal.can_manage_all_agents or group.created_by_membership_id == principal.membership_id):
            raise AccessDenied("Only the Group creator or administrator may remove another member")
        if not enabled and membership_id == group.created_by_membership_id:
            raise Conflict("The Group creator must remain a member")
        member = await IdentityService(self.tx).require_membership(tenant_id=principal.tenant_id, membership_id=membership_id)
        if enabled and not member.enabled:
            raise AccessDenied("Disabled Membership cannot join a Group")
        row = await self.session.scalar(select(GroupMembershipRecord).where(
            GroupMembershipRecord.tenant_id == principal.tenant_id, GroupMembershipRecord.group_id == group_id,
            GroupMembershipRecord.membership_id == membership_id))
        now = datetime.now(UTC)
        if row is None:
            row = GroupMembershipRecord(id=uuid4(), tenant_id=principal.tenant_id, group_id=group_id,
                membership_id=membership_id, enabled=enabled, created_at=now, updated_at=now)
            self.session.add(row)
        else:
            row.enabled, row.updated_at = enabled, now
        await self.session.flush()

    async def update(self, principal: TenantPrincipal, *, group_id: UUID, name: str,
            announcement: str, enabled: bool) -> GroupView:
        if not name.strip() or len(name) > 200 or len(announcement) > 16384 or type(enabled) is not bool:
            raise InvalidInput("Group configuration is invalid")
        row = await self._authorized(principal, group_id, lock=True, allow_disabled=True)
        if enabled != row.enabled and not (principal.can_manage_all_agents or row.created_by_membership_id == principal.membership_id):
            raise AccessDenied("Only the Group creator or administrator may change Group availability")
        row.name, row.announcement, row.enabled, row.updated_at = name, announcement, enabled, datetime.now(UTC)
        await self.session.flush()
        return GroupView(row.id, row.tenant_id, row.name, row.announcement, row.enabled)

    async def accept_input(self, principal: TenantPrincipal, *, group_id: UUID, source_key: str,
            input: InputContent, agent_ids: tuple[UUID, ...],
            account_selections: tuple[PersonalAccountSelection, ...] = (), conversation_id: UUID | None = None,
            mentioned_membership_ids: tuple[UUID, ...] = ()) -> AcceptedGroupInput:
        if not source_key or len(source_key) > 512 or len(agent_ids) > 100 or len(set(agent_ids)) != len(agent_ids):
            raise InvalidInput("Group source or target list is invalid")
        payload = asdict(input)
        _input(payload)
        group = await self._authorized(principal, group_id, lock=True)
        existing = await self.session.scalar(select(GroupEventRecord).where(
            GroupEventRecord.tenant_id == principal.tenant_id, GroupEventRecord.group_id == group_id,
            GroupEventRecord.source_key == source_key))
        if existing is not None:
            if existing.membership_id != principal.membership_id:
                raise AccessDenied("Group source identity belongs to another member")
            return AcceptedGroupInput(_event(existing), await self._links(principal.tenant_id, existing.id), False)
        conversation = await self._conversation(principal.tenant_id, group_id, conversation_id)
        await AgentService(self.tx).require_execution_ids(principal, agent_ids=agent_ids)
        roster = frozenset((await self.session.scalars(select(GroupAgentRecord.agent_id).where(
            GroupAgentRecord.tenant_id == principal.tenant_id, GroupAgentRecord.group_id == group_id,
            GroupAgentRecord.enabled.is_(True), GroupAgentRecord.agent_id.in_(agent_ids)))).all())
        if roster != frozenset(agent_ids):
            raise AccessDenied("Target Agent must be an active Group member")
        if len(mentioned_membership_ids) > 100 or len(set(mentioned_membership_ids)) != len(mentioned_membership_ids):
            raise InvalidInput("Group mentions are invalid")
        humans = frozenset((await self.session.scalars(select(GroupMembershipRecord.membership_id).where(
            GroupMembershipRecord.tenant_id == principal.tenant_id, GroupMembershipRecord.group_id == group_id,
            GroupMembershipRecord.enabled.is_(True), GroupMembershipRecord.membership_id.in_(mentioned_membership_ids)))).all())
        if humans != frozenset(mentioned_membership_ids):
            raise AccessDenied("Mentioned person must be an active Group member")
        if account_selections:
            await ToolService(self.tx, enabled_sources=self._enabled_sources).validate_personal_selections(principal, selections=account_selections)
        now = datetime.now(UTC)
        row = GroupEventRecord(id=uuid4(), tenant_id=principal.tenant_id, group_id=group_id, conversation_id=conversation.id, position=group.next_position,
            kind="input", source_key=source_key, message_key=None, source_run_id=None,
            membership_id=principal.membership_id, agent_id=None, origin_event_id=None,
            payload_version=1, payload={"input": payload, "waiting_reference": None, "related_run_id": None,
                "step_id": None, "call_id": None, "account_selections": encode_personal_selections(account_selections),
                "mentioned_membership_ids": [str(value) for value in mentioned_membership_ids]}, created_at=now, updated_at=now)
        if len(json.dumps(row.payload, ensure_ascii=False).encode()) > 256 * 1024:
            raise InvalidInput("Group input and account selection exceed their byte bound")
        group.next_position += 1
        self.session.add(row)
        await self.session.flush()
        for agent_id in agent_ids:
            self.session.add(GroupRunLinkRecord(id=uuid4(), tenant_id=principal.tenant_id, group_id=group_id,
                event_id=row.id, conversation_id=conversation.id, agent_id=agent_id, run_id=None, admission="pending", admission_error=None,
                result_version=1, result=None, created_at=now, updated_at=now))
        await self.session.flush()
        return AcceptedGroupInput(_event(row), await self._links(principal.tenant_id, row.id), True)

    async def list_events(self, principal: TenantPrincipal, *, group_id: UUID,
            after_position: int = 0, through_position: int | None = None, limit: int = 100,
            conversation_id: UUID | None = None) -> tuple[GroupEventView, ...]:
        group = await self._authorized(principal, group_id)
        conversation = await self._conversation(principal.tenant_id, group_id, conversation_id)
        if type(limit) is not int or not 1 <= limit <= 100 or type(after_position) is not int or after_position < 0:
            raise InvalidInput("Group history page is invalid")
        cutoff = group.next_position - 1 if through_position is None else through_position
        if type(cutoff) is not int or cutoff < 0 or cutoff >= group.next_position:
            raise InvalidInput("Group history cutoff is invalid")
        query = select(GroupEventRecord.id, func.octet_length(cast(GroupEventRecord.payload, Text))).where(GroupEventRecord.tenant_id == principal.tenant_id,
            GroupEventRecord.group_id == group_id, GroupEventRecord.position > after_position,
            GroupEventRecord.conversation_id == conversation.id,
            GroupEventRecord.position <= cutoff).order_by(GroupEventRecord.position).limit(limit)
        ids, total = [], 0
        for event_id, size in (await self.session.execute(query)).all():
            if size > 256 * 1024 + 2048:
                raise InvalidInput("Stored Group event exceeds its payload bound")
            if total + size > 1024 * 1024:
                break
            ids.append(event_id)
            total += size
        if not ids:
            return ()
        rows = await self.session.scalars(select(GroupEventRecord).where(GroupEventRecord.tenant_id == principal.tenant_id,
            GroupEventRecord.id.in_(ids)).order_by(GroupEventRecord.position))
        return tuple(_event(row) for row in rows.all())

    async def resolve_conversation(self, principal: TenantPrincipal, *, group_id: UUID,
            conversation_id: UUID | None = None) -> UUID:
        """Pin the selected active topic under Group membership authorization."""
        await self._authorized(principal, group_id)
        return (await self._conversation(principal.tenant_id, group_id, conversation_id)).id

    async def read_event_page(self, principal: TenantPrincipal, *, group_id: UUID, conversation_id: UUID,
            after_position: int = 0, limit: int = 100) -> GroupDeliveryPage:
        """Committed topic events; the head excludes positions belonging to other topics."""
        await self.resolve_conversation(principal, group_id=group_id, conversation_id=conversation_id)
        head = await self.session.scalar(select(func.max(GroupEventRecord.position)).where(
            GroupEventRecord.tenant_id == principal.tenant_id, GroupEventRecord.group_id == group_id,
            GroupEventRecord.conversation_id == conversation_id)) or 0
        entries = await self.list_events(principal, group_id=group_id, conversation_id=conversation_id,
            after_position=after_position, through_position=head, limit=limit)
        position = entries[-1].position if entries else after_position
        return GroupDeliveryPage(entries, position, position < head)

    async def read_context_history(self, principal: TenantPrincipal, *, group_id: UUID, conversation_id: UUID,
            through_position: int, limit: int = 20, max_bytes: int = 16384) -> str:
        """Bounded recent input context; oversized entries remain explicit event references."""
        group = await self._authorized(principal, group_id)
        await self._conversation(principal.tenant_id, group_id, conversation_id)
        self._page(0, limit)
        if (type(max_bytes) is not int or not 1024 <= max_bytes <= 65536 or type(through_position) is not int
                or not 0 <= through_position < group.next_position):
            raise InvalidInput("Group context window is invalid")
        rows = (await self.session.execute(select(GroupEventRecord.id, GroupEventRecord.position,
            func.octet_length(cast(GroupEventRecord.payload, Text))).where(GroupEventRecord.tenant_id == principal.tenant_id,
                GroupEventRecord.group_id == group_id, GroupEventRecord.conversation_id == conversation_id,
                GroupEventRecord.position <= through_position).order_by(GroupEventRecord.position.desc()).limit(limit + 1))).all()
        selected = rows[:limit]
        small_ids = [identity for identity, _, size in selected if size <= max_bytes]
        records = {row.id: row for row in (await self.session.scalars(select(GroupEventRecord).where(
            GroupEventRecord.tenant_id == principal.tenant_id, GroupEventRecord.id.in_(small_ids)))).all()}
        entries: list[dict[str, object]] = []
        total = 256
        has_more = len(rows) > limit
        for identity, position, _ in selected:
            entry: dict[str, object] = {"event_id": str(identity), "position": position, "reference_only": True}
            if identity in records:
                event = _event(records[identity])
                complete: dict[str, object] = {**entry, "reference_only": False, "kind": event.kind,
                    "membership_id": str(event.membership_id) if event.membership_id else None,
                    "agent_id": str(event.agent_id) if event.agent_id else None, "input": asdict(event.input),
                    "mentioned_membership_ids": [str(value) for value in event.mentioned_membership_ids]}
                if total + len(json.dumps(complete, ensure_ascii=False).encode()) + 2 <= max_bytes:
                    entry = complete
            size = len(json.dumps(entry, ensure_ascii=False).encode()) + 2
            if total + size > max_bytes:
                has_more = True
                break
            entries.append(entry)
            total += size
        return json.dumps({"conversation_id": str(conversation_id), "through_position": through_position,
            "has_more": has_more, "entries": list(reversed(entries))}, ensure_ascii=False)

    async def answer_wait(self, principal: TenantPrincipal, *, group_id: UUID, run_id: UUID,
            waiting_reference: str, source_key: str, input: InputContent) -> tuple[AcceptedGroupInput, TransitionResult]:
        """Caller commits the human event and the resumed Run together, then schedules."""
        run = await RunService(self.tx).lock_main(tenant_id=principal.tenant_id, run_id=run_id)
        link = await self._for_run(run)
        if link.group_id != group_id:
            raise AccessDenied("Waiting execution belongs to another Group")
        await AgentService(self.tx).get_for_execution(principal, agent_id=run.agent_id)
        accepted = await self.accept_input(principal, group_id=group_id, source_key=source_key, input=input, agent_ids=(), conversation_id=link.conversation_id)
        if accepted.created:
            event = await self.session.get(GroupEventRecord, accepted.event.id)
            if event is None:
                raise NotFound("Group reply input does not exist")
            event.payload = {**event.payload, "waiting_reference": waiting_reference, "related_run_id": str(run_id)}
            await self.session.flush()
            accepted = AcceptedGroupInput(_event(event), (), True)
        elif (accepted.event.related_run_id, accepted.event.waiting_reference) != (run_id, waiting_reference):
            raise Conflict("Group input was accepted with another reply relation")
        changed = await RunService(self.tx).append_related(tenant_id=principal.tenant_id, run_id=run_id, input=accepted.event.input,
            source=SourceIdentity("group_answer", accepted.event.id, waiting_reference), waiting_reference=waiting_reference)
        return accepted, changed

    async def accept_message(self, *, tenant_id: UUID, run_id: UUID, step_id: str, call_id: str, input: InputContent) -> GroupEventView:
        run = await RunService(self.tx).lock_main(tenant_id=tenant_id, run_id=run_id)
        link = await self._for_run(run)
        group = await self._group(tenant_id, link.group_id, lock=True)
        if not all(isinstance(value, str) and 0 < len(value) <= 256 for value in (step_id, call_id)):
            raise InvalidInput("Group message source is invalid")
        message_key = sha256(f"{run_id}\0{step_id}\0{call_id}".encode()).hexdigest()
        existing = await self.session.scalar(select(GroupEventRecord).where(GroupEventRecord.tenant_id == tenant_id,
            GroupEventRecord.group_id == group.id, GroupEventRecord.message_key == message_key))
        if existing is not None:
            return _event(existing)
        await RunService(self.tx).verify_main_tool_origin(tenant_id=tenant_id, run_id=run_id,
            step_id=step_id, call_id=call_id, tool_name="send_message")
        return await self._message(group, link, run, message_key, input, step_id=step_id, call_id=call_id)

    async def get_message_for_delivery(self, *, tenant_id: UUID, agent_id: UUID,
            message_id: UUID) -> GroupEventView:
        """Trusted Channel lookup; not a human authorization or arbitrary Group read port."""
        row = await self.session.scalar(select(GroupEventRecord).where(GroupEventRecord.tenant_id == tenant_id,
            GroupEventRecord.agent_id == agent_id, GroupEventRecord.id == message_id, GroupEventRecord.kind == "reply"))
        if row is None or row.source_run_id is None:
            raise NotFound("Group message does not belong to this Agent")
        if row.origin_event_id is None:
            run = await RunService(self.tx).get(tenant_id=tenant_id, run_id=row.source_run_id)
            if run.agent_id != agent_id or run.parent_run_id is not None or run.source.kind not in ("trigger", "heartbeat"):
                raise InvalidInput("External Group message source is inconsistent")
            return _event(row)
        link = await self.session.scalar(select(GroupRunLinkRecord).where(GroupRunLinkRecord.tenant_id == tenant_id,
            GroupRunLinkRecord.agent_id == agent_id, GroupRunLinkRecord.run_id == row.source_run_id,
            GroupRunLinkRecord.group_id == row.group_id, GroupRunLinkRecord.event_id == row.origin_event_id))
        if link is None:
            raise InvalidInput("Group message has no matching execution association")
        return _event(row)

    async def accept_external_message(self, *, run: RunView, group_id: UUID, conversation_id: UUID,
            step_id: str, call_id: str, input: InputContent, authorize: GroupExternalMessageAuthorizer) -> GroupEventView:
        if conversation_id is None:
            raise InvalidInput("External Group delivery requires its explicit conversation")
        actual = await RunService(self.tx).lock_main(tenant_id=run.tenant_id, run_id=run.id)
        if actual.source.kind not in ("trigger", "heartbeat"):
            raise AccessDenied("External Group messages require an unattended Main")
        if not all(isinstance(value, str) and 0 < len(value) <= 256 for value in (step_id, call_id)):
            raise InvalidInput("External message correlation is invalid")
        await authorize(self.tx, run=actual, target_id=group_id, conversation_id=conversation_id, input=input)
        group = await self._group(actual.tenant_id, group_id, lock=True)
        conversation = await self._conversation(actual.tenant_id, group_id, conversation_id)
        key = sha256(f"{actual.id}\0{step_id}\0{call_id}".encode()).hexdigest()
        existing = await self.session.scalar(select(GroupEventRecord).where(GroupEventRecord.tenant_id == actual.tenant_id,
            GroupEventRecord.group_id == group_id, GroupEventRecord.message_key == key))
        if existing is not None:
            if existing.source_run_id != actual.id or existing.origin_event_id is not None or existing.conversation_id != conversation_id:
                raise Conflict("External Group message correlation is inconsistent")
            return _event(existing)
        if not group.enabled or not await self.session.scalar(select(GroupAgentRecord.id).where(
                GroupAgentRecord.tenant_id == actual.tenant_id, GroupAgentRecord.group_id == group_id,
                GroupAgentRecord.agent_id == actual.agent_id, GroupAgentRecord.enabled.is_(True))):
            raise AccessDenied("External message destination or Agent membership is unavailable")
        await RunService(self.tx).verify_main_tool_origin(tenant_id=actual.tenant_id, run_id=actual.id,
            step_id=step_id, call_id=call_id, tool_name="send_message")
        content = asdict(input)
        _input(content)
        payload = {"input":content,"waiting_reference":None,"related_run_id":None,"step_id":step_id,
            "call_id":call_id,"account_selections":encode_personal_selections(()),"mentioned_membership_ids":[]}
        if len(json.dumps(payload, ensure_ascii=False).encode()) > 256 * 1024:
            raise InvalidInput("External Group message exceeds its byte bound")
        now = datetime.now(UTC)
        row = GroupEventRecord(id=uuid4(), tenant_id=actual.tenant_id, group_id=group_id, conversation_id=conversation.id,
            position=group.next_position, kind="reply", source_key=None, message_key=key, source_run_id=actual.id,
            membership_id=None, agent_id=actual.agent_id, origin_event_id=None, payload_version=1,
            payload=payload, created_at=now, updated_at=now)
        group.next_position += 1
        self.session.add(row)
        await self.session.flush()
        return _event(row)

    async def find_accepted_message(self, *, run: RunView, step_id: str, call_id: str) -> GroupEventView | None:
        if run.parent_run_id is not None or run.source.kind != "group":
            raise AccessDenied("This execution has no Group message destination")
        key = sha256(f"{run.id}\0{step_id}\0{call_id}".encode()).hexdigest()
        row = await self.session.scalar(select(GroupEventRecord).where(GroupEventRecord.tenant_id == run.tenant_id,
            GroupEventRecord.source_run_id == run.id, GroupEventRecord.message_key == key))
        if row is None:
            return None
        return await self.get_message_for_delivery(tenant_id=run.tenant_id, agent_id=run.agent_id, message_id=row.id)

    async def find_external_message(self, *, run: RunView, group_id: UUID, conversation_id: UUID,
            step_id: str, call_id: str, authorize: GroupExternalMessageAuthorizer) -> GroupEventView | None:
        if conversation_id is None:
            raise InvalidInput("External Group delivery requires its explicit conversation")
        actual = await RunService(self.tx).get(tenant_id=run.tenant_id, run_id=run.id)
        if actual.parent_run_id is not None or actual.source.kind not in ("trigger", "heartbeat"):
            raise AccessDenied("External message lookup requires an unattended Main")
        await authorize(self.tx, run=actual, target_id=group_id, conversation_id=conversation_id, input=InputContent(""))
        key = sha256(f"{actual.id}\0{step_id}\0{call_id}".encode()).hexdigest()
        row = await self.session.scalar(select(GroupEventRecord).where(GroupEventRecord.tenant_id == actual.tenant_id,
            GroupEventRecord.group_id == group_id, GroupEventRecord.message_key == key))
        if row is None:
            return None
        if row.source_run_id != actual.id or row.origin_event_id is not None or row.conversation_id != conversation_id:
            raise Conflict("External message lookup correlation differs")
        return await self.get_message_for_delivery(tenant_id=actual.tenant_id, agent_id=actual.agent_id, message_id=row.id)

    async def _message(self, group: GroupRecord, link: GroupRunLinkRecord, run: RunView,
            key: str, input: InputContent, waiting_reference: str | None = None,
            *, step_id: str | None = None, call_id: str | None = None) -> GroupEventView:
        if not key or len(key) > 512:
            raise InvalidInput("Group message identity is invalid")
        payload = asdict(input)
        _input(payload)
        row = await self.session.scalar(select(GroupEventRecord).where(GroupEventRecord.tenant_id == run.tenant_id,
            GroupEventRecord.group_id == group.id, GroupEventRecord.source_run_id == run.id,
            GroupEventRecord.message_key == key))
        if row is not None:
            return _event(row)
        if run.status not in ("Running", "Waiting"):
            raise Conflict("Terminal execution cannot send another message")
        now = datetime.now(UTC)
        row = GroupEventRecord(id=uuid4(), tenant_id=run.tenant_id, group_id=group.id, conversation_id=link.conversation_id, position=group.next_position,
            kind="reply", source_key=None, message_key=key, source_run_id=run.id, membership_id=None,
            agent_id=run.agent_id, origin_event_id=link.event_id, payload_version=1,
            payload={"input": payload, "waiting_reference": waiting_reference, "related_run_id": None,
                "step_id": step_id, "call_id": call_id, "account_selections": encode_personal_selections(()),
                "mentioned_membership_ids": []}, created_at=now, updated_at=now)
        group.next_position += 1
        self.session.add(row)
        await self.session.flush()
        return _event(row)

    async def record_started(self, transaction: TransactionContext, *, run: RunView) -> None:
        service = GroupService(transaction)
        link = await service._for_run(run, starting=True)
        await service._group(run.tenant_id, link.group_id, lock=True)
        await service._conversation(run.tenant_id, link.group_id, link.conversation_id)
        if link.run_id not in (None, run.id):
            raise Conflict("Group target already started another execution")
        link.run_id, link.admission, link.admission_error, link.updated_at = run.id, "started", None, datetime.now(UTC)
        await transaction.session.flush()

    async def record_waiting(self, transaction: TransactionContext, *, run: RunView, waiting: WaitingPayload) -> None:
        service = GroupService(transaction)
        link = await service._for_run(run)
        group = await service._group(run.tenant_id, link.group_id, lock=True)
        key = sha256(f"{run.id}\0waiting\0{waiting.reference}".encode()).hexdigest()
        await service._message(group, link, run, key, InputContent(waiting.question), waiting.reference, step_id=waiting.step_id)

    async def record_outcome(self, transaction: TransactionContext, *, run: RunView,
            outcome: TerminalOutcomePayload) -> None:
        link = await GroupService(transaction)._for_run(run)
        reason = outcome.reason
        if reason is not None and len(reason.encode()) > 2048:
            reason = reason.encode()[:2048].decode(errors="ignore") + " [Reason truncated; see Run outcome.]"
        link.result = {"status": outcome.status, "reason": reason, "run_id": str(run.id)}
        link.updated_at = datetime.now(UTC)
        await transaction.session.flush()

    async def mark_admission_failed(self, *, tenant_id: UUID, event_id: UUID, agent_id: UUID, reason: str) -> None:
        if not reason or len(reason) > 512:
            raise InvalidInput("Group admission reason is invalid")
        link = await self.session.scalar(select(GroupRunLinkRecord).where(GroupRunLinkRecord.tenant_id == tenant_id,
            GroupRunLinkRecord.event_id == event_id, GroupRunLinkRecord.agent_id == agent_id).with_for_update())
        if link is None:
            raise NotFound("Group target does not exist")
        if link.admission == "pending":
            link.admission, link.admission_error, link.updated_at = "failed", reason, datetime.now(UTC)
            await self.session.flush()

    async def links(self, principal: TenantPrincipal, *, group_id: UUID, event_id: UUID) -> tuple[GroupRunLinkView, ...]:
        await self._authorized(principal, group_id)
        event = await self.session.scalar(select(GroupEventRecord).where(GroupEventRecord.tenant_id == principal.tenant_id,
            GroupEventRecord.group_id == group_id, GroupEventRecord.id == event_id, GroupEventRecord.kind == "input"))
        if event is None:
            raise NotFound("Group input does not exist")
        return await self._links(principal.tenant_id, event_id)

    async def delivery_heads(self, scopes: tuple[GroupDeliveryScope, ...]) -> dict[UUID, int]:
        if not isinstance(scopes, tuple) or len(scopes) > 100:
            raise InvalidInput("Group delivery scope batch is invalid")
        if not scopes:
            return {}
        from sqlalchemy import tuple_
        keys = {(scope.tenant_id, scope.group_id) for scope in scopes}
        rows = (await self.session.execute(select(GroupRecord.id, GroupRecord.next_position).where(
            tuple_(GroupRecord.tenant_id, GroupRecord.id).in_(keys)))).all()
        if len(rows) != len(keys):
            raise AccessDenied("Channel Group scopes do not match their owners")
        return {id: position - 1 for id, position in rows}

    async def read_delivery_page(self, *, tenant_id: UUID, group_id: UUID, after_position: int,
            limit: int = 100) -> GroupDeliveryPage:
        """Trusted Channel group mapping; scan all positions even when another Agent authored them."""
        if type(limit) is not int or not 1 <= limit <= 100 or type(after_position) is not int or after_position < 0:
            raise InvalidInput("Group delivery page is invalid")
        group = await self._group(tenant_id, group_id)
        through = group.next_position - 1
        if after_position > through:
            raise InvalidInput("Group delivery cursor is beyond committed history")
        query = select(GroupEventRecord).where(GroupEventRecord.tenant_id == tenant_id, GroupEventRecord.group_id == group_id,
            GroupEventRecord.position > after_position, GroupEventRecord.position <= through)
        sizes = (await self.session.execute(query.with_only_columns(GroupEventRecord.id, GroupEventRecord.position,
            func.octet_length(cast(GroupEventRecord.payload, Text))).order_by(GroupEventRecord.position).limit(limit))).all()
        selected, used = [], 1024
        for id, position, size in sizes:
            if position != after_position + len(selected) + 1 or size > 262144:
                raise InvalidInput("Group delivery history is incomplete or oversized")
            if used + size + 2048 > 1024 * 1024:
                break
            selected.append(id)
            used += size + 2048
        rows = (await self.session.scalars(query.where(GroupEventRecord.id.in_(selected),
            func.octet_length(cast(GroupEventRecord.payload, Text)) <= 262144).order_by(GroupEventRecord.position))).all() if selected else []
        if len(rows) != len(selected) or (not rows and after_position < through):
            raise InvalidInput("Group delivery history changed while reading")
        after = rows[-1].position if rows else after_position
        return GroupDeliveryPage(tuple(_event(row) for row in rows), after, after < through)

    async def default_conversation_id(self, *, tenant_id: UUID, group_id: UUID) -> UUID:
        """Trusted Channel mappings address the Group's default conversation only."""
        return (await self._conversation(tenant_id, group_id, None)).id

    async def input_accounts(self, principal: TenantPrincipal, *, group_id: UUID, event_id: UUID,
            target_agent_id: UUID) -> tuple[UUID, ...]:
        await self._authorized(principal, group_id)
        return await self._event_accounts(principal.tenant_id, group_id, event_id, target_agent_id)

    async def execution_accounts(self, run: RunView, *, target_agent_id: UUID) -> tuple[UUID, ...]:
        """Resolve only the original human Group event, not later messages or another Agent's context."""
        actual = await RunService(self.tx).get(tenant_id=run.tenant_id, run_id=run.id)
        if actual.parent_run_id is not None or actual.source.kind != "group":
            raise AccessDenied("Only a Group Main has Group input account choices")
        link = await self.session.scalar(select(GroupRunLinkRecord).where(GroupRunLinkRecord.tenant_id == run.tenant_id,
            GroupRunLinkRecord.event_id == actual.source.owner_id, GroupRunLinkRecord.agent_id == actual.agent_id,
            GroupRunLinkRecord.run_id == actual.id))
        if link is None:
            raise AccessDenied("Group execution has no input association")
        return await self._event_accounts(run.tenant_id, link.group_id, link.event_id, target_agent_id)

    async def execution_conversation(self, run: RunView) -> tuple[UUID, UUID]:
        """Trusted operations compare the actual Main's Group and topic association."""
        actual = await RunService(self.tx).get(tenant_id=run.tenant_id, run_id=run.id)
        if actual.parent_run_id is not None or actual.source.kind != "group":
            raise AccessDenied("Only a Group Main has a Group conversation")
        link = await self.session.scalar(select(GroupRunLinkRecord).where(
            GroupRunLinkRecord.tenant_id == actual.tenant_id, GroupRunLinkRecord.event_id == actual.source.owner_id,
            GroupRunLinkRecord.agent_id == actual.agent_id, GroupRunLinkRecord.run_id == actual.id))
        if link is None or link.conversation_id is None:
            raise AccessDenied("Group execution has no conversation association")
        return link.group_id, link.conversation_id

    async def _event_accounts(self, tenant_id: UUID, group_id: UUID, event_id: UUID, target_agent_id: UUID) -> tuple[UUID, ...]:
        row = await self.session.scalar(select(GroupEventRecord).where(GroupEventRecord.tenant_id == tenant_id,
            GroupEventRecord.group_id == group_id, GroupEventRecord.id == event_id, GroupEventRecord.kind == "input"))
        if row is None:
            raise NotFound("Group account selection input is unavailable")
        _event(row)
        return next((item.connection_ids for item in decode_personal_selections(row.payload["account_selections"])
            if item.target_agent_id == target_agent_id), ())

    async def _links(self, tenant: UUID, event: UUID) -> tuple[GroupRunLinkView, ...]:
        rows = (await self.session.scalars(select(GroupRunLinkRecord).where(GroupRunLinkRecord.tenant_id == tenant,
            GroupRunLinkRecord.event_id == event).order_by(GroupRunLinkRecord.agent_id).limit(101))).all()
        if len(rows) > 100:
            raise InvalidInput("Group target count exceeds its bound")
        return tuple(_link(row) for row in rows)

    async def _for_run(self, run: RunView, *, starting: bool = False) -> GroupRunLinkRecord:
        if run.parent_run_id is not None or run.source.kind != "group":
            raise AccessDenied("Group callback requires its own Main execution")
        link = await self.session.scalar(select(GroupRunLinkRecord).where(GroupRunLinkRecord.tenant_id == run.tenant_id,
            GroupRunLinkRecord.event_id == run.source.owner_id, GroupRunLinkRecord.agent_id == run.agent_id))
        if link is None or (not starting and link.run_id != run.id):
            raise AccessDenied("Execution does not belong to the Group target")
        await self._group(run.tenant_id, link.group_id, lock=True)
        link = await self.session.scalar(select(GroupRunLinkRecord).where(
            GroupRunLinkRecord.tenant_id == run.tenant_id, GroupRunLinkRecord.id == link.id)
            .with_for_update().execution_options(populate_existing=True))
        assert link is not None
        _link(link)
        return link

    async def _authorized(self, principal: TenantPrincipal, group: UUID, *, lock: bool = False,
            allow_disabled: bool = False) -> GroupRecord:
        row = await self._group(principal.tenant_id, group, lock=lock)
        membership = await self.session.scalar(select(GroupMembershipRecord).where(
            GroupMembershipRecord.tenant_id == principal.tenant_id, GroupMembershipRecord.group_id == group,
            GroupMembershipRecord.membership_id == principal.membership_id, GroupMembershipRecord.enabled.is_(True)))
        if (not row.enabled and not allow_disabled) or membership is None:
            raise AccessDenied("Active Group membership is required")
        return row

    async def _group(self, tenant: UUID, group: UUID, *, lock: bool = False) -> GroupRecord:
        query = select(GroupRecord).where(GroupRecord.tenant_id == tenant, GroupRecord.id == group)
        if lock:
            query = query.with_for_update()
        row = await self.session.scalar(query.execution_options(populate_existing=True))
        if row is None:
            raise NotFound("Group does not exist")
        return row
