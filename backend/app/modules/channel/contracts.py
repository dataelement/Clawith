"""Typed Channel observations and source-owned delivery content."""

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol
from uuid import UUID

from app.infrastructure.transactions import TransactionContext
from app.modules.channel.reply_context import ReplyContext
from app.modules.credential.public import Secret

Provider = Literal["feishu", "dingtalk", "discord", "slack", "teams", "wechat", "wecom"]
MessageKind = Literal["session", "group"]
DeliveryStatus = Literal["pending", "delivered", "failed", "uncertain"]


class ListenerDisconnected(Exception):
    """A terminated transport may reconnect; its old execution must not replay."""


@dataclass(frozen=True, slots=True)
class ChannelView:
    id: UUID
    tenant_id: UUID
    agent_id: UUID
    provider: Provider
    external_identity: str
    credential_id: UUID
    enabled: bool
    credential_owner_kind: Literal["tenant", "agent"]
    credential_owner_id: UUID
    settings_json: str = "{}"


@dataclass(frozen=True, slots=True)
class DeliveryContent:
    text: str
    attachments: tuple[str, ...] = ()


class MessageLoader(Protocol):
    async def __call__(self, transaction_context: TransactionContext, *, tenant_id: UUID,
        agent_id: UUID, kind: MessageKind, message_id: UUID) -> DeliveryContent: ...


class DeliveryFile(Protocol):
    @property
    def name(self) -> str: ...
    @property
    def media_type(self) -> str: ...
    @property
    def content(self) -> bytes: ...


class DeliveryFileLoader(Protocol):
    async def __call__(self, *, tenant_id: UUID, agent_id: UUID, message_id: UUID,
        reference: str, kind: MessageKind) -> DeliveryFile: ...


@dataclass(frozen=True, slots=True)
class DeliveryView:
    id: UUID
    tenant_id: UUID
    agent_id: UUID
    channel_id: UUID
    kind: MessageKind
    message_id: UUID
    destination: str
    key: str
    status: DeliveryStatus
    attempts: int
    acknowledgement: str | None
    error: str | None


@dataclass(frozen=True, slots=True)
class SendOutcome:
    status: Literal["delivered", "failed", "uncertain"]
    acknowledgement: str | None = None
    error: str | None = None
    provider_reply_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        from app.infrastructure.errors import InvalidInput
        try:
            valid = (len(self.provider_reply_ids) <= 256 and all(
                isinstance(value, str) and value and len(value.encode()) <= 512
                for value in self.provider_reply_ids))
        except UnicodeError:
            valid = False
        if not valid or len(set(self.provider_reply_ids)) != len(self.provider_reply_ids):
            raise InvalidInput("Channel reply identities exceed their bound")


@dataclass(frozen=True, slots=True)
class AttachmentReference:
    external_id: str
    name: str
    media_type: str | None


@dataclass(frozen=True, slots=True)
class IncomingMessage:
    event_id: str
    actor_id: str
    conversation_id: str
    group_id: str | None
    text: str
    reply_to: str | None
    attachments: tuple[AttachmentReference, ...] = ()


@dataclass(frozen=True, slots=True)
class WebhookReply:
    status: int
    content_type: Literal["application/json", "text/plain"]
    body: str


@dataclass(frozen=True, slots=True)
class InboundResult:
    message: IncomingMessage | None = None
    challenge: str | None = None
    reply: WebhookReply | None = None
    private_context: ReplyContext | None = None
    context_expires_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class ResolvedInbound:
    message: IncomingMessage | None
    membership_id: UUID | None = None
    group_id: UUID | None = None
    challenge: str | None = None
    reply: WebhookReply | None = None
    reply_context_id: UUID | None = None


class ChannelAdapter(Protocol):
    provider: Provider

    async def send(self, channel: ChannelView, credential: Secret, *, destination: str,
        content: DeliveryContent, delivery_key: str, reply_context: ReplyContext | None = None,
        reply_operation: Literal["original", "followup"] | None = None) -> SendOutcome: ...

    async def receive(self, channel: ChannelView, credential: Secret, *, body: bytes,
        headers: dict[str, str], now: datetime) -> InboundResult: ...


class ChannelListener(Protocol):
    async def listen(self, channel: ChannelView, credential: Secret,
        on_message: Callable[[InboundResult], Awaitable[None]]) -> None: ...


@dataclass(frozen=True, slots=True)
class ChannelAdapters:
    adapters: tuple[ChannelAdapter, ...]
    listeners: Mapping[Provider, ChannelListener]
