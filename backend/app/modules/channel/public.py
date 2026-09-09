"""Channel configuration, identity associations and source-backed delivery attempts."""

import asyncio
import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal, cast
from uuid import UUID, uuid4

import httpx
from sqlalchemy import and_, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.infrastructure.errors import Conflict, DomainError, InvalidInput, NotFound
from app.infrastructure.transactions import TransactionContext, transaction
from app.modules.agent.public import AgentService
from app.modules.channel.adapters import SlackAdapter
from app.modules.channel.context_repository import ReplyContextRepository
from app.modules.channel.contracts import (
    AttachmentReference,
    ChannelAdapter,
    ChannelAdapters,
    ChannelListener,
    ChannelView,
    DeliveryContent,
    DeliveryFileLoader,
    DeliveryStatus,
    DeliveryView,
    InboundResult,
    IncomingMessage,
    ListenerDisconnected,
    MessageKind,
    MessageLoader,
    Provider,
    ResolvedInbound,
    SendOutcome,
    WebhookReply,
)
from app.modules.channel.models import (
    AgentChannelConfigurationRecord,
    ChannelActorLinkRecord,
    ChannelConversationRecord,
    ChannelDeliveryRecord,
    ChannelGroupLinkRecord,
    ChannelInputRouteRecord,
    ChannelReplyContextRecord,
)
from app.modules.channel.providers.dingtalk import DingTalkAdapter
from app.modules.channel.providers.discord import DiscordAdapter
from app.modules.channel.providers.feishu import FeishuAdapter
from app.modules.channel.providers.registry import create_channel_adapters
from app.modules.channel.providers.teams import TeamsAdapter
from app.modules.channel.providers.wechat import PollBatch, QRChallenge, QRStatus, WeChatAdapter, WeChatSessionExpired
from app.modules.channel.providers.wecom import CustomerServiceNotice, CustomerServicePage, WeComAdapter
from app.modules.channel.reply_context import ChannelContextCodec, ReplyContext
from app.modules.channel.repository import ChannelRepository
from app.modules.channel.settings import validate_settings
from app.modules.channel.sync_cursor import ChannelSyncCursors, SyncCursorView
from app.modules.credential.public import CredentialService, Secret
from app.modules.identity_tenant.public import IdentityService, TenantPrincipal, require_admin

__all__ = [
    "AttachmentReference",
    "ChannelAdapter",
    "ChannelAdapters",
    "ChannelContextCodec",
    "ChannelDeliverySource",
    "ChannelInputRoute",
    "ChannelListener",
    "ChannelService",
    "ChannelSyncCursors",
    "ChannelView",
    "CustomerServiceNotice",
    "CustomerServicePage",
    "DeliveryContent",
    "DeliveryService",
    "DeliveryView",
    "DingTalkAdapter",
    "DiscordAdapter",
    "FeishuAdapter",
    "InboundResult",
    "InboundService",
    "IncomingMessage",
    "ListenerDisconnected",
    "MessageLoader",
    "PollBatch",
    "QRChallenge",
    "QRStatus",
    "ResolvedInbound",
    "SendOutcome",
    "SlackAdapter",
    "SyncCursorView",
    "TeamsAdapter",
    "WeChatAdapter",
    "WeChatSessionExpired",
    "WeComAdapter",
    "WebhookReply",
    "create_channel_adapters",
]


def _bounded(value: str, limit: int) -> str:
    try:
        invalid = not value or len(value) > limit or len(value.encode()) > limit
    except UnicodeError:
        raise InvalidInput("Channel identity is invalid") from None
    if invalid:
        raise InvalidInput("Channel identity is invalid")
    return value


@dataclass(frozen=True, slots=True)
class ChannelInputRoute:
    id: UUID
    tenant_id: UUID
    agent_id: UUID
    channel_id: UUID
    event_id: str
    kind: MessageKind
    input_id: UUID
    destination: str
    reply_context_id: UUID | None


@dataclass(frozen=True, slots=True)
class ChannelDeliverySource:
    id: UUID
    tenant_id: UUID
    channel_id: UUID
    agent_id: UUID
    kind: MessageKind
    owner_id: UUID
    membership_id: UUID | None
    cursor: int
    destination: str


def _route(row: ChannelInputRouteRecord) -> ChannelInputRoute:
    input_id = row.session_input_id or row.group_input_id
    if input_id is None:
        raise InvalidInput("Channel route has no product input")
    return ChannelInputRoute(row.id, row.tenant_id, row.agent_id, row.channel_configuration_id, row.external_event_id,
        "session" if row.session_input_id else "group", input_id, row.destination, row.reply_context_id)


class ChannelService:
    def __init__(self, tx: TransactionContext) -> None:
        self._tx, self._repository = tx, ChannelRepository(tx.session)

    async def get_for_intake(self, *, tenant_id: UUID, channel_id: UUID, lock: bool = False) -> ChannelView:
        channel = _channel(await self._require(channel_id, tenant_id, lock=lock))
        if not channel.enabled:
            raise NotFound("Channel is disabled")
        return channel

    async def enabled_channels(self, *, after_id: UUID | None = None, limit: int = 100) -> tuple[ChannelView, ...]:
        if type(limit) is not int or not 1 <= limit <= 100:
            raise InvalidInput("Channel configuration page is invalid")
        query = select(AgentChannelConfigurationRecord).where(AgentChannelConfigurationRecord.enabled.is_(True))
        if after_id is not None:
            query = query.where(AgentChannelConfigurationRecord.id > after_id)
        return tuple(_channel(row) for row in (await self._tx.session.scalars(query.order_by(AgentChannelConfigurationRecord.id).limit(limit))).all())

    async def conversation_session(self, *, tenant_id: UUID, channel_id: UUID, conversation_id: str,
            membership_id: UUID) -> UUID | None:
        return await self._tx.session.scalar(select(ChannelConversationRecord.session_id).where(
            ChannelConversationRecord.tenant_id == tenant_id, ChannelConversationRecord.channel_configuration_id == channel_id,
            ChannelConversationRecord.external_conversation_id == _bounded(conversation_id, 512),
            ChannelConversationRecord.membership_id == membership_id))

    async def bind_conversation(self, *, tenant_id: UUID, channel_id: UUID, conversation_id: str,
            membership_id: UUID, session_id: UUID) -> UUID:
        """Caller creates Session in this transaction after taking the Channel configuration lock."""
        channel = await self.get_for_intake(tenant_id=tenant_id, channel_id=channel_id, lock=True)
        existing = await self.conversation_session(tenant_id=tenant_id, channel_id=channel_id,
            conversation_id=conversation_id, membership_id=membership_id)
        if existing is not None:
            if existing != session_id:
                raise Conflict("Channel conversation already has a Session")
            return existing
        now = datetime.now(UTC)
        self._tx.session.add(ChannelConversationRecord(id=uuid4(), tenant_id=tenant_id, agent_id=channel.agent_id,
            channel_configuration_id=channel_id, external_conversation_id=_bounded(conversation_id, 512),
            membership_id=membership_id, session_id=session_id, message_cursor=0, created_at=now, updated_at=now))
        await self._flush()
        return session_id

    async def delivery_sources(self, *, kind: MessageKind, after_id: UUID | None = None, limit: int = 100) -> tuple[ChannelDeliverySource, ...]:
        if kind not in ("session", "group") or type(limit) is not int or not 1 <= limit <= 100:
            raise InvalidInput("Channel message source scan is invalid")
        if kind == "session":
            query = select(ChannelConversationRecord).join(AgentChannelConfigurationRecord,
                ChannelConversationRecord.channel_configuration_id == AgentChannelConfigurationRecord.id).where(AgentChannelConfigurationRecord.enabled.is_(True))
            if after_id is not None:
                query = query.where(ChannelConversationRecord.id > after_id)
            rows = (await self._tx.session.scalars(query.order_by(ChannelConversationRecord.id).limit(limit))).all()
            return tuple(ChannelDeliverySource(row.id, row.tenant_id, row.channel_configuration_id, row.agent_id, "session",
                row.session_id, row.membership_id, row.message_cursor, row.external_conversation_id) for row in rows)
        query = select(ChannelGroupLinkRecord, AgentChannelConfigurationRecord.agent_id, AgentChannelConfigurationRecord.provider).join(AgentChannelConfigurationRecord,
            ChannelGroupLinkRecord.channel_configuration_id == AgentChannelConfigurationRecord.id).where(
                ChannelGroupLinkRecord.enabled.is_(True), AgentChannelConfigurationRecord.enabled.is_(True))
        if after_id is not None:
            query = query.where(ChannelGroupLinkRecord.id > after_id)
        rows = (await self._tx.session.execute(query.order_by(ChannelGroupLinkRecord.id).limit(limit))).all()
        return tuple(ChannelDeliverySource(row.id, row.tenant_id, row.channel_configuration_id, agent_id, "group",
            row.group_id, None, row.message_cursor,
            "group:" + row.external_group_id if provider == "dingtalk" else row.external_group_id) for row, agent_id, provider in rows)

    async def advance_message_cursor(self, source: ChannelDeliverySource, *, through_position: int) -> None:
        if type(through_position) is not int or through_position < source.cursor:
            raise InvalidInput("Channel message cursor cannot move backwards")
        if source.kind == "session":
            row = await self._tx.session.scalar(select(ChannelConversationRecord).where(ChannelConversationRecord.id == source.id,
                ChannelConversationRecord.tenant_id == source.tenant_id, ChannelConversationRecord.channel_configuration_id == source.channel_id)
                .with_for_update().execution_options(populate_existing=True))
        else:
            row = await self._tx.session.scalar(select(ChannelGroupLinkRecord).where(ChannelGroupLinkRecord.id == source.id,
                ChannelGroupLinkRecord.tenant_id == source.tenant_id, ChannelGroupLinkRecord.channel_configuration_id == source.channel_id)
                .with_for_update().execution_options(populate_existing=True))
        if row is None or row.message_cursor != source.cursor:
            raise Conflict("Channel message cursor changed")
        row.message_cursor, row.updated_at = through_position, datetime.now(UTC)
        await self._flush()

    async def record_input_route(self, *, tenant_id: UUID, channel_id: UUID, event_id: str, kind: MessageKind,
            input_id: UUID, destination: str, reply_context_id: UUID | None = None) -> ChannelInputRoute:
        channel = await self.get_for_intake(tenant_id=tenant_id, channel_id=channel_id)
        existing = await self._tx.session.scalar(select(ChannelInputRouteRecord).where(ChannelInputRouteRecord.tenant_id == tenant_id,
            ChannelInputRouteRecord.channel_configuration_id == channel_id, ChannelInputRouteRecord.external_event_id == _bounded(event_id, 512)))
        if existing is not None:
            route = _route(existing)
            if (route.kind, route.input_id, route.destination, route.reply_context_id) != (kind, input_id, destination, reply_context_id):
                raise Conflict("Channel event already has another product route")
            return route
        if kind not in ("session", "group"):
            raise InvalidInput("Channel input source is invalid")
        now = datetime.now(UTC)
        row = ChannelInputRouteRecord(id=uuid4(), tenant_id=tenant_id, agent_id=channel.agent_id, channel_configuration_id=channel_id,
            external_event_id=event_id, session_input_id=input_id if kind == "session" else None,
            group_input_id=input_id if kind == "group" else None, destination=_bounded(destination, 512),
            reply_context_id=reply_context_id, created_at=now, updated_at=now)
        self._tx.session.add(row)
        await self._flush()
        return _route(row)

    async def route_for_event(self, *, tenant_id: UUID, channel_id: UUID, event_id: str) -> ChannelInputRoute | None:
        row = await self._tx.session.scalar(select(ChannelInputRouteRecord).where(ChannelInputRouteRecord.tenant_id == tenant_id,
            ChannelInputRouteRecord.channel_configuration_id == channel_id, ChannelInputRouteRecord.external_event_id == _bounded(event_id, 512)))
        return _route(row) if row is not None else None

    async def input_routes(self, *, tenant_id: UUID, agent_id: UUID, kind: MessageKind, input_id: UUID) -> tuple[ChannelInputRoute, ...]:
        if kind not in ("session", "group"):
            raise InvalidInput("Channel input source is invalid")
        column = ChannelInputRouteRecord.session_input_id if kind == "session" else ChannelInputRouteRecord.group_input_id
        rows = (await self._tx.session.scalars(select(ChannelInputRouteRecord).where(ChannelInputRouteRecord.tenant_id == tenant_id,
            ChannelInputRouteRecord.agent_id == agent_id, column == input_id).order_by(ChannelInputRouteRecord.id).limit(101))).all()
        if len(rows) > 100:
            raise InvalidInput("Channel input has too many delivery routes")
        return tuple(_route(row) for row in rows)

    async def pending_deliveries(self, *, after_id: UUID | None = None, limit: int = 100) -> tuple[DeliveryView, ...]:
        if type(limit) is not int or not 1 <= limit <= 100:
            raise InvalidInput("Channel delivery page is invalid")
        query = select(ChannelDeliveryRecord).where(ChannelDeliveryRecord.delivery_status == "pending")
        if after_id is not None:
            position = await self._tx.session.scalar(select(ChannelDeliveryRecord.created_at).where(ChannelDeliveryRecord.id == after_id))
            if position is None:
                raise InvalidInput("Channel delivery cursor is unavailable")
            query = query.where(or_(ChannelDeliveryRecord.created_at > position,
                and_(ChannelDeliveryRecord.created_at == position, ChannelDeliveryRecord.id > after_id)))
        return tuple(_delivery(row) for row in (await self._tx.session.scalars(query.order_by(ChannelDeliveryRecord.created_at, ChannelDeliveryRecord.id).limit(limit))).all())

    async def get_delivery(self, principal: TenantPrincipal, *, delivery_id: UUID) -> DeliveryView:
        require_admin(principal)
        row = await self._repository.delivery(principal.tenant_id, delivery_id)
        if row is None:
            raise NotFound("Channel delivery is unavailable")
        return _delivery(row)

    async def record_delivery_error(self, *, tenant_id: UUID, delivery_id: UUID, code: str) -> None:
        row = await self._repository.delivery(tenant_id, delivery_id, lock=True)
        if row is None:
            raise NotFound("Channel delivery is unavailable")
        if row.delivery_status in ("pending", "failed"):
            row.delivery_status, row.last_error, row.updated_at = "failed", _bounded(code, 512), datetime.now(UTC)
            await self._flush()

    async def delivered_reply(self, *, tenant_id: UUID, channel_id: UUID, destination: str, acknowledgement: str) -> DeliveryView | None:
        rows = (await self._tx.session.scalars(select(ChannelDeliveryRecord).where(ChannelDeliveryRecord.tenant_id == tenant_id,
            ChannelDeliveryRecord.channel_configuration_id == channel_id, ChannelDeliveryRecord.destination == destination,
            ChannelDeliveryRecord.provider_reply_ids.contains([_bounded(acknowledgement, 512)]),
            ChannelDeliveryRecord.delivery_status.in_(("delivered", "uncertain"))).limit(2))).all()
        if len(rows) > 1:
            raise Conflict("Provider reply identity is ambiguous")
        return _delivery(rows[0]) if rows else None

    async def expiry_tenants(self, *, now: datetime, limit: int = 100) -> tuple[UUID, ...]:
        if type(limit) is not int or not 1 <= limit <= 100 or now.tzinfo is None:
            raise InvalidInput("Channel context expiry scan is invalid")
        return tuple((await self._tx.session.scalars(select(ChannelReplyContextRecord.tenant_id).where(
            ChannelReplyContextRecord.expires_at <= now, ChannelReplyContextRecord.ciphertext != b"")
            .distinct().order_by(ChannelReplyContextRecord.tenant_id).limit(limit))).all())

    async def configure(self, principal: TenantPrincipal, *, agent_id: UUID, provider: Provider,
            external_identity: str, credential_id: UUID, settings_json: str = "{}") -> ChannelView:
        require_admin(principal)
        if provider not in {"feishu", "dingtalk", "discord", "slack", "teams", "wechat", "wecom"}:
            raise InvalidInput("Channel provider is unsupported")
        if provider == "slack" and not re.fullmatch(r"[A-Za-z0-9]+:[A-Za-z0-9]+", external_identity):
            raise InvalidInput("Slack external identity must identify its workspace and application")
        await AgentService(self._tx).get(principal, agent_id=agent_id)
        credentials = CredentialService(self._tx)
        credential = await credentials.get_metadata(principal, credential_id=credential_id)
        if not ((credential.owner_kind == "tenant" and credential.owner_id == principal.tenant_id)
                or (credential.owner_kind == "agent" and credential.owner_id == agent_id)):
            raise InvalidInput("Channel Credential owner is incompatible")
        await credentials.require_owner_metadata(tenant_id=principal.tenant_id, credential_id=credential_id,
            owner_kind=credential.owner_kind, owner_id=credential.owner_id)
        if credential.provider != provider or credential.kind != "channel":
            raise InvalidInput("Channel Credential provider or kind is incompatible")
        now = datetime.now(UTC)
        row = AgentChannelConfigurationRecord(id=uuid4(), tenant_id=principal.tenant_id, agent_id=agent_id,
            provider=provider, external_identity=_bounded(external_identity, 512), configuration_version=1,
            non_secret_config=json.loads(validate_settings(provider, settings_json)), enabled=True, credential_id=credential_id, credential_owner_kind=credential.owner_kind,
            credential_owner_id=credential.owner_id, created_at=now, updated_at=now)
        self._tx.session.add(row)
        await self._flush()
        return _channel(row)

    async def get(self, principal: TenantPrincipal, *, channel_id: UUID) -> ChannelView:
        require_admin(principal)
        return _channel(await self._require(channel_id, principal.tenant_id))

    async def list(self, principal: TenantPrincipal, *, agent_id: UUID, limit: int = 100, offset: int = 0) -> tuple[ChannelView, ...]:
        require_admin(principal)
        if type(limit) is not int or not 1 <= limit <= 100 or type(offset) is not int or offset < 0:
            raise InvalidInput("Channel page is invalid")
        await AgentService(self._tx).get(principal, agent_id=agent_id)
        return tuple(_channel(row) for row in await self._repository.list_channels(principal.tenant_id,
            agent_id, limit=limit, offset=offset))

    async def set_enabled(self, principal: TenantPrincipal, *, channel_id: UUID, enabled: bool) -> ChannelView:
        require_admin(principal)
        row = await self._require(channel_id, principal.tenant_id, lock=True)
        row.enabled, row.updated_at = enabled, datetime.now(UTC)
        await self._flush()
        return _channel(row)

    async def update_settings(self, principal: TenantPrincipal, *, channel_id: UUID, settings_json: str) -> ChannelView:
        require_admin(principal)
        row = await self._require(channel_id, principal.tenant_id, lock=True)
        channel = _channel(row)
        row.non_secret_config = json.loads(validate_settings(channel.provider, settings_json))
        row.updated_at = datetime.now(UTC)
        await self._flush()
        return _channel(row)

    async def bind_actor(self, principal: TenantPrincipal, *, channel_id: UUID, external_actor_id: str,
            membership_id: UUID) -> None:
        require_admin(principal)
        await self._require(channel_id, principal.tenant_id)
        await IdentityService(self._tx).require_membership(tenant_id=principal.tenant_id, membership_id=membership_id)
        now = datetime.now(UTC)
        self._tx.session.add(ChannelActorLinkRecord(id=uuid4(), tenant_id=principal.tenant_id,
            channel_configuration_id=channel_id, external_actor_id=_bounded(external_actor_id, 512),
            membership_id=membership_id, enabled=True, created_at=now, updated_at=now))
        await self._flush()

    async def bind_group(self, principal: TenantPrincipal, *, channel_id: UUID, external_group_id: str,
            group_id: UUID) -> None:
        require_admin(principal)
        await self._require(channel_id, principal.tenant_id)
        now = datetime.now(UTC)
        self._tx.session.add(ChannelGroupLinkRecord(id=uuid4(), tenant_id=principal.tenant_id,
            channel_configuration_id=channel_id, external_group_id=_bounded(external_group_id, 512),
            group_id=group_id, enabled=True, message_cursor=0, created_at=now, updated_at=now))
        await self._flush()

    async def mapped_subjects(self, *, tenant_id: UUID, channel_id: UUID, message: IncomingMessage) -> tuple[UUID, UUID | None]:
        channel = await self._require(channel_id, tenant_id)
        if not channel.enabled:
            raise NotFound("Channel is disabled")
        actor = await self._tx.session.scalar(select(ChannelActorLinkRecord).where(
            ChannelActorLinkRecord.tenant_id == tenant_id, ChannelActorLinkRecord.channel_configuration_id == channel_id,
            ChannelActorLinkRecord.external_actor_id == message.actor_id, ChannelActorLinkRecord.enabled.is_(True)))
        if actor is None:
            raise NotFound("Channel actor is not mapped to this Tenant")
        group_id = None
        if message.group_id is not None:
            group_id = await self._tx.session.scalar(select(ChannelGroupLinkRecord.group_id).where(
                ChannelGroupLinkRecord.tenant_id == tenant_id, ChannelGroupLinkRecord.channel_configuration_id == channel_id,
                ChannelGroupLinkRecord.external_group_id == message.group_id, ChannelGroupLinkRecord.enabled.is_(True)))
            if group_id is None:
                raise NotFound("Channel group is not mapped to this Tenant")
        return actor.membership_id, group_id

    async def enqueue(self, *, tenant_id: UUID, channel_id: UUID, kind: MessageKind, message_id: UUID,
            destination: str, delivery_key: str, messages: MessageLoader, reply_context_id: UUID | None = None) -> DeliveryView:
        if kind not in ("session", "group"):
            raise InvalidInput("Channel message source is invalid")
        _bounded(destination, 512)
        _bounded(delivery_key, 512)
        channel = await self._require(channel_id, tenant_id, lock=True)
        existing = await self._repository.delivery_by_key(tenant_id, channel_id, delivery_key)
        if existing is not None:
            view = _delivery(existing)
            if (view.kind, view.message_id, view.destination, existing.reply_context_id) != (kind, message_id, destination, reply_context_id):
                raise Conflict("Delivery identity already refers to another message")
            return view
        await messages(self._tx, tenant_id=tenant_id, agent_id=channel.agent_id, kind=kind, message_id=message_id)
        reply_operation = None
        if reply_context_id is not None and channel.provider == "discord":
            previous = await self._tx.session.scalar(select(ChannelDeliveryRecord.id).where(
                ChannelDeliveryRecord.tenant_id == tenant_id, ChannelDeliveryRecord.channel_configuration_id == channel_id,
                ChannelDeliveryRecord.reply_context_id == reply_context_id).limit(1))
            reply_operation = "original" if previous is None else "followup"
        now = datetime.now(UTC)
        row = ChannelDeliveryRecord(id=uuid4(), tenant_id=tenant_id, agent_id=channel.agent_id,
            channel_configuration_id=channel_id, session_reply_id=message_id if kind == "session" else None,
            group_reply_id=message_id if kind == "group" else None, destination=destination, delivery_key=delivery_key,
            reply_context_id=reply_context_id,
            reply_operation=reply_operation,
            attempt_count=0, delivery_status="pending", provider_acknowledgement=None, last_error=None,
            created_at=now, updated_at=now)
        self._tx.session.add(row)
        await self._flush()
        return _delivery(row)

    async def _require(self, channel_id: UUID, tenant_id: UUID, *, lock: bool = False):
        row = await self._repository.channel(tenant_id, channel_id, lock=lock)
        if row is None:
            raise NotFound("Channel does not exist in this Tenant")
        return row

    async def _flush(self) -> None:
        try:
            await self._tx.session.flush()
        except IntegrityError:
            raise Conflict("Channel facts conflict with existing identities") from None


class InboundService:
    def __init__(self, sessions: async_sessionmaker[AsyncSession], *, credentials: Callable[[TransactionContext], CredentialService],
            adapters: tuple[ChannelAdapter, ...], context_codec: ChannelContextCodec) -> None:
        if len({adapter.provider for adapter in adapters}) != len(adapters):
            raise InvalidInput("Channel adapters must have unique providers")
        self._sessions, self._credentials = sessions, credentials
        self._context_codec = context_codec
        self._adapters = {adapter.provider: adapter for adapter in adapters}

    async def clear_expired_contexts(self, *, tenant_id: UUID, now: datetime, limit: int = 100) -> int:
        """Clear only expired transport ciphertext; caller owns periodic task lifetime."""
        async with transaction(self._sessions) as tx:
            return await ReplyContextRepository(tx, self._context_codec).clear_expired(
                tenant_id=tenant_id, now=now, limit=limit)

    async def private_context(self, channel: ChannelView, *, context_id: UUID):
        """Application media bridge uses only its authenticated event's context."""
        async with transaction(self._sessions) as tx:
            return await ReplyContextRepository(tx, self._context_codec).load(tenant_id=channel.tenant_id,
                agent_id=channel.agent_id, channel_id=channel.id, context_id=context_id, now=datetime.now(UTC))

    async def receive(self, *, tenant_id: UUID, channel_id: UUID, body: bytes,
            headers: dict[str, str], now: datetime) -> ResolvedInbound:
        async with transaction(self._sessions) as tx:
            row = await ChannelRepository(tx.session).channel(tenant_id, channel_id)
            if row is None or not row.enabled:
                raise NotFound("Channel is unavailable")
            channel = _channel(row)
            adapter = self._adapters.get(channel.provider)
            if adapter is None:
                raise InvalidInput("Channel provider transport is not implemented")
            secret = await self._credentials(tx).reveal_secret_for_owner(tenant_id=tenant_id,
                credential_id=channel.credential_id, owner_kind=channel.credential_owner_kind, owner_id=channel.credential_owner_id)
        result = await adapter.receive(channel, secret, body=body, headers=headers, now=now)
        return await self.accept_authenticated(channel=channel, result=result, now=now)

    async def accept_authenticated(self, *, channel: ChannelView, result: InboundResult, now: datetime) -> ResolvedInbound:
        if result.message is None:
            if result.private_context is not None:
                raise InvalidInput("Channel private context requires an observed message")
            return ResolvedInbound(None, challenge=result.challenge, reply=result.reply)
        async with transaction(self._sessions) as tx:
            membership, group = await ChannelService(tx).mapped_subjects(tenant_id=channel.tenant_id, channel_id=channel.id, message=result.message)
            context_id = None
            if result.private_context is not None:
                if result.context_expires_at is None or result.private_context.conversation_id != result.message.conversation_id:
                    raise InvalidInput("Channel reply context does not match its observed message")
                route = await ChannelService(tx).route_for_event(tenant_id=channel.tenant_id, channel_id=channel.id,
                    event_id=result.message.event_id)
                if route is not None:
                    # A committed input keeps its original routing. Retransmission
                    # neither renews expired coordinates nor requires decrypting them.
                    context_id = route.reply_context_id
                else:
                    context_id = await ReplyContextRepository(tx, self._context_codec).save(
                        tenant_id=channel.tenant_id, agent_id=channel.agent_id, channel_id=channel.id,
                        event_id=result.message.event_id, context=result.private_context,
                        expires_at=result.context_expires_at, now=now)
        return ResolvedInbound(result.message, membership, group, reply=result.reply, reply_context_id=context_id)


class DeliveryService:
    def __init__(self, sessions: async_sessionmaker[AsyncSession], *, credentials: Callable[[TransactionContext], CredentialService],
            adapters: tuple[ChannelAdapter, ...], messages: MessageLoader, context_codec: ChannelContextCodec,
            files: DeliveryFileLoader | None = None, concurrency: int = 8) -> None:
        if not 1 <= concurrency <= 32 or len({adapter.provider for adapter in adapters}) != len(adapters):
            raise InvalidInput("Channel delivery configuration is invalid")
        self._sessions, self._credentials, self._messages = sessions, credentials, messages
        self._context_codec = context_codec
        self._files = files
        self._adapters = {adapter.provider: adapter for adapter in adapters}
        self._slots = asyncio.Semaphore(concurrency)

    async def send(self, *, tenant_id: UUID, delivery_id: UUID) -> DeliveryView:
        async with self._slots:
            async with transaction(self._sessions) as tx:
                repo = ChannelRepository(tx.session)
                row = await repo.delivery(tenant_id, delivery_id, lock=True)
                if row is None:
                    raise NotFound("Delivery does not exist in this Tenant")
                if row.delivery_status in ("delivered", "uncertain"):
                    return _delivery(row)
                channel_record = await repo.channel(tenant_id, row.channel_configuration_id)
                if channel_record is None:
                    raise NotFound("Channel does not exist in this Tenant")
                channel = _channel(channel_record)
                adapter = self._adapters.get(channel.provider)
                if adapter is None or not channel.enabled:
                    row.delivery_status, row.last_error = "failed", "channel_adapter_unavailable"
                    await tx.session.flush()
                    return _delivery(row)
                view = _delivery(row)
                content = await self._messages(tx, tenant_id=tenant_id, agent_id=view.agent_id,
                    kind=view.kind, message_id=view.message_id)
                if len(content.text.encode()) > 262144 or len(content.attachments) > 64:
                    raise InvalidInput("Channel message content exceeds its bound")
                credential = await self._credentials(tx).reveal_secret_for_owner(tenant_id=tenant_id,
                    credential_id=channel.credential_id, owner_kind=channel.credential_owner_kind,
                    owner_id=channel.credential_owner_id)
                reply_context = None
                if row.reply_context_id is not None:
                    reply_context = await ReplyContextRepository(tx, self._context_codec).load(
                        tenant_id=tenant_id, agent_id=channel.agent_id, channel_id=channel.id,
                        context_id=row.reply_context_id, now=datetime.now(UTC))
                    if reply_context.provider != channel.provider or reply_context.conversation_id != view.destination:
                        raise InvalidInput("Channel reply coordinates do not match delivery")
                # No external operation can begin without a committed ambiguous attempt.
                row.delivery_status = "uncertain"
                row.attempt_count += 1
                row.updated_at = datetime.now(UTC)
                attempt = row.attempt_count
                operation = cast(Literal["original", "followup"] | None, row.reply_operation)
            if content.attachments:
                outcome = await self._send_files(adapter, channel, credential, view, content,
                    reply_context=reply_context, reply_operation=operation)
            else:
                outcome = await adapter.send(channel, credential, destination=view.destination, content=content,
                    delivery_key=view.key, reply_context=reply_context, reply_operation=operation)
            async with transaction(self._sessions) as tx:
                row = await ChannelRepository(tx.session).delivery(tenant_id, delivery_id, lock=True)
                if row is None or row.attempt_count != attempt or row.delivery_status != "uncertain":
                    raise Conflict("Delivery attempt changed before settlement")
                row.delivery_status, row.provider_acknowledgement = outcome.status, outcome.acknowledgement
                row.provider_reply_ids = list(outcome.provider_reply_ids)
                row.last_error, row.updated_at = outcome.error, datetime.now(UTC)
                await tx.session.flush()
                return _delivery(row)

    async def _send_files(self, adapter: ChannelAdapter, channel: ChannelView, credential: Secret,
            view: DeliveryView, content: DeliveryContent, *, reply_context: ReplyContext | None,
            reply_operation: Literal["original", "followup"] | None) -> SendOutcome:
        if self._files is None or not isinstance(adapter, (FeishuAdapter, SlackAdapter)):
            return SendOutcome("failed", error="channel_file_delivery_unavailable")
        delivered = False
        acknowledgement = None
        reply_ids: list[str] = []
        if content.text:
            outcome = await adapter.send(channel, credential, destination=view.destination,
                content=DeliveryContent(content.text), delivery_key=view.key,
                reply_context=reply_context, reply_operation=reply_operation)
            if outcome.status != "delivered":
                return outcome
            delivered, acknowledgement = True, outcome.acknowledgement
            reply_ids.extend(outcome.provider_reply_ids)
        for index, reference in enumerate(content.attachments):
            try:
                file = await self._files(tenant_id=view.tenant_id, agent_id=view.agent_id,
                    message_id=view.message_id, reference=reference, kind=view.kind)
                handle = await adapter.upload_file(channel, credential, filename=file.name, content=file.content) if isinstance(adapter, FeishuAdapter) else None
            except (DomainError, httpx.HTTPError, TimeoutError):
                # Upload has not sent a user-visible message; earlier acknowledged parts still forbid replay.
                return SendOutcome("uncertain" if delivered else "failed", acknowledgement,
                    "channel_file_preparation_failed", tuple(reply_ids))
            if isinstance(adapter, FeishuAdapter):
                assert handle is not None
                outcome = await adapter.send_file(channel, credential, destination=view.destination,
                    file_key=handle, delivery_key=f"{view.key}:file:{index}")
            else:
                outcome = await adapter.send_file(channel, credential, destination=view.destination,
                    filename=file.name, content=file.content)
            if outcome.status != "delivered":
                return SendOutcome("uncertain" if delivered else outcome.status,
                    outcome.acknowledgement or acknowledgement, outcome.error,
                    tuple(dict.fromkeys((*reply_ids, *outcome.provider_reply_ids))))
            delivered, acknowledgement = True, outcome.acknowledgement
            reply_ids.extend(value for value in outcome.provider_reply_ids if value not in reply_ids)
        return SendOutcome("delivered", acknowledgement, provider_reply_ids=tuple(reply_ids))


def _channel(row: AgentChannelConfigurationRecord) -> ChannelView:
    if (row.configuration_version != 1 or row.credential_id is None or row.credential_owner_id is None or row.credential_owner_kind not in ("tenant", "agent")
            or row.credential_owner_id != (row.tenant_id if row.credential_owner_kind == "tenant" else row.agent_id)):
        raise InvalidInput("Channel configuration version or credential is unsupported")
    if row.provider not in {"feishu", "dingtalk", "discord", "slack", "teams", "wechat", "wecom"}:
        raise InvalidInput("Channel provider is unsupported")
    return ChannelView(row.id, row.tenant_id, row.agent_id, cast(Provider, row.provider), row.external_identity,
        row.credential_id, row.enabled, cast(Literal["tenant", "agent"], row.credential_owner_kind), row.credential_owner_id,
        validate_settings(cast(Provider, row.provider), json.dumps(row.non_secret_config)))


def _delivery(row: ChannelDeliveryRecord) -> DeliveryView:
    if row.delivery_status not in {"pending", "delivered", "failed", "uncertain"}:
        raise InvalidInput("Delivery status is unsupported")
    message_id = row.session_reply_id or row.group_reply_id
    if message_id is None:
        raise InvalidInput("Delivery message reference is missing")
    return DeliveryView(row.id, row.tenant_id, row.agent_id, row.channel_configuration_id,
        "session" if row.session_reply_id else "group", message_id, row.destination, row.delivery_key,
        cast(DeliveryStatus, row.delivery_status), row.attempt_count, row.provider_acknowledgement, row.last_error)
