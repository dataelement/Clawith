"""Encrypted transport coordinates remain private to their Channel event."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import select, update

from app.infrastructure.errors import Conflict, InvalidInput, NotFound
from app.infrastructure.transactions import TransactionContext
from app.modules.channel.models import ChannelReplyContextRecord
from app.modules.channel.reply_context import ChannelContextCodec, ContextScope, ReplyContext, SealedContext
from app.modules.channel.repository import ChannelRepository


class ReplyContextRepository:
    def __init__(self, tx: TransactionContext, codec: ChannelContextCodec) -> None:
        self._tx, self._codec = tx, codec

    async def save(self, *, tenant_id: UUID, agent_id: UUID, channel_id: UUID,
            event_id: str, context: ReplyContext, expires_at: datetime, now: datetime) -> UUID:
        channel = await ChannelRepository(self._tx.session).channel(tenant_id, channel_id, lock=True)
        if channel is None or channel.agent_id != agent_id or not channel.enabled or channel.provider != context.provider:
            raise NotFound("Channel reply owner is unavailable")
        if expires_at.tzinfo is None or now.tzinfo is None or expires_at <= now:
            raise InvalidInput("Channel reply expiration is invalid")
        row = await self._tx.session.scalar(select(ChannelReplyContextRecord).where(
            ChannelReplyContextRecord.tenant_id == tenant_id,
            ChannelReplyContextRecord.channel_configuration_id == channel_id,
            ChannelReplyContextRecord.external_event_id == event_id))
        if row is not None:
            if self._decode(row, now=now) != context:
                raise Conflict("Channel event already has different reply coordinates")
            return row.id
        scope = ContextScope(uuid4(), tenant_id, agent_id, channel_id, event_id, expires_at)
        sealed = self._codec.seal(context, scope=scope)
        self._tx.session.add(ChannelReplyContextRecord(id=scope.id, tenant_id=tenant_id, agent_id=agent_id,
            channel_configuration_id=channel_id, external_event_id=event_id,
            context_version=sealed.context_version, key_version=sealed.key_version,
            nonce=sealed.nonce, ciphertext=sealed.ciphertext, expires_at=expires_at,
            created_at=now, updated_at=now))
        await self._tx.session.flush()
        return scope.id

    async def load(self, *, tenant_id: UUID, agent_id: UUID, channel_id: UUID,
            context_id: UUID, now: datetime) -> ReplyContext:
        row = await self._tx.session.scalar(select(ChannelReplyContextRecord).where(
            ChannelReplyContextRecord.id == context_id, ChannelReplyContextRecord.tenant_id == tenant_id,
            ChannelReplyContextRecord.agent_id == agent_id, ChannelReplyContextRecord.channel_configuration_id == channel_id))
        if row is None:
            raise NotFound("Channel reply context is unavailable")
        return self._decode(row, now=now)

    async def clear_expired(self, *, tenant_id: UUID, now: datetime, limit: int) -> int:
        if type(limit) is not int or not 1 <= limit <= 100 or now.tzinfo is None:
            raise InvalidInput("Channel context cleanup boundary is invalid")
        ids = tuple(await self._tx.session.scalars(select(ChannelReplyContextRecord.id).where(
            ChannelReplyContextRecord.tenant_id == tenant_id, ChannelReplyContextRecord.expires_at <= now,
            ChannelReplyContextRecord.ciphertext != b"").order_by(ChannelReplyContextRecord.expires_at,
            ChannelReplyContextRecord.id).limit(limit).with_for_update(skip_locked=True)))
        if ids:
            await self._tx.session.execute(update(ChannelReplyContextRecord).where(
                ChannelReplyContextRecord.tenant_id == tenant_id, ChannelReplyContextRecord.id.in_(ids))
                .values(ciphertext=b"", nonce=b"", updated_at=now))
        await self._tx.session.flush()
        return len(ids)

    def _decode(self, row: ChannelReplyContextRecord, *, now: datetime) -> ReplyContext:
        return self._codec.open(SealedContext(row.context_version, row.key_version, row.nonce, row.ciphertext),
            scope=ContextScope(row.id, row.tenant_id, row.agent_id, row.channel_configuration_id,
                row.external_event_id, row.expires_at), now=now)
