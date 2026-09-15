"""Private provider-notice pagination cursors, not Agent execution recovery."""

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from typing import Literal, cast
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from app.infrastructure.errors import Conflict, InvalidInput, NotFound
from app.infrastructure.transactions import TransactionContext
from app.modules.channel.contracts import ChannelView
from app.modules.channel.models import ChannelSyncCursorRecord
from app.modules.channel.reply_context import ChannelContextCodec, SealedContext
from app.modules.credential.public import Secret


@dataclass(frozen=True, slots=True)
class SyncCursorView:
    id: UUID
    tenant_id: UUID
    channel_id: UUID
    event_id: str
    open_kfid: str
    version: int
    kind: Literal["token", "cursor", "done"]
    coordinate: Secret | None


def _aad(row: ChannelSyncCursorRecord) -> bytes:
    return json.dumps(["clawith:channel:sync-cursor:v1", str(row.id), str(row.tenant_id), str(row.agent_id),
        str(row.channel_configuration_id), row.stream_key, row.cursor_version, row.coordinate_kind,
        row.external_event_id], separators=(",", ":")).encode()


class ChannelSyncCursors:
    def __init__(self, tx: TransactionContext, codec: ChannelContextCodec) -> None:
        self.tx, self.codec = tx, codec

    def _read(self, row: ChannelSyncCursorRecord) -> SyncCursorView:
        if row.cursor_version < 1 or row.coordinate_kind not in ("token", "cursor", "done"):
            raise InvalidInput("Channel synchronization cursor is unsupported")
        raw = self.codec._open_sync(SealedContext(1, row.key_version, row.nonce, row.ciphertext), _aad(row))
        try:
            value = json.loads(raw)
            if not isinstance(value, dict) or set(value) != {"version", "open_kfid", "coordinate"} or type(value["version"]) is not int or value["version"] != 1:
                raise ValueError
            if not isinstance(value["open_kfid"], str) or not value["open_kfid"] or len(value["open_kfid"].encode()) > 512:
                raise ValueError
            coordinate = value["coordinate"]
            if row.coordinate_kind == "done":
                if coordinate is not None:
                    raise ValueError
            elif not isinstance(coordinate, str) or not coordinate or len(coordinate.encode()) > 8192:
                raise ValueError
        except (ValueError, TypeError, UnicodeError):
            raise InvalidInput("Channel synchronization cursor payload is invalid") from None
        return SyncCursorView(row.id, row.tenant_id, row.channel_configuration_id, row.external_event_id,
            value["open_kfid"], row.cursor_version, cast(Literal["token", "cursor", "done"], row.coordinate_kind),
            Secret(coordinate) if coordinate is not None else None)

    def _seal(self, row: ChannelSyncCursorRecord, open_kfid: str, coordinate: Secret | None) -> None:
        if len(open_kfid.encode()) > 512 or (coordinate is not None and len(coordinate.value.encode()) > 8192):
            raise InvalidInput("Channel synchronization coordinate is too large")
        sealed = self.codec._seal_sync(json.dumps({"version": 1, "open_kfid": open_kfid,
            "coordinate": coordinate.value if coordinate is not None else None}, separators=(",", ":")).encode(), _aad(row))
        row.key_version, row.nonce, row.ciphertext = sealed.key_version, sealed.nonce, sealed.ciphertext

    async def accept(self, channel: ChannelView, *, event_id: str, open_kfid: str, event_token: Secret) -> SyncCursorView:
        if channel.provider != "wecom" or not event_id or len(event_id.encode()) > 512 or not open_kfid or not event_token.value:
            raise InvalidInput("Customer-service synchronization identity is invalid")
        # Notifications can overlap; never overwrite another notice's unfinished pagination.
        key = "kf:" + sha256(f"{open_kfid}\0{event_id}".encode()).hexdigest()
        now = datetime.now(UTC)
        row = ChannelSyncCursorRecord(id=uuid4(), tenant_id=channel.tenant_id, agent_id=channel.agent_id,
            channel_configuration_id=channel.id, stream_key=key, external_event_id=event_id,
            cursor_version=1, coordinate_kind="token", created_at=now, updated_at=now)
        self._seal(row, open_kfid, event_token)
        await self.tx.session.execute(insert(ChannelSyncCursorRecord).values(id=row.id, tenant_id=row.tenant_id,
            agent_id=row.agent_id, channel_configuration_id=row.channel_configuration_id, stream_key=key,
            external_event_id=event_id, cursor_version=1, coordinate_kind="token", key_version=row.key_version,
            nonce=row.nonce, ciphertext=row.ciphertext, created_at=now, updated_at=now).on_conflict_do_nothing(
                index_elements=["tenant_id", "channel_configuration_id", "stream_key"]))
        saved = await self.tx.session.scalar(select(ChannelSyncCursorRecord).where(ChannelSyncCursorRecord.tenant_id == channel.tenant_id,
            ChannelSyncCursorRecord.channel_configuration_id == channel.id, ChannelSyncCursorRecord.stream_key == key))
        assert saved is not None
        return self._read(saved)

    async def pending(self, *, after_id: UUID | None = None, limit: int = 100) -> tuple[SyncCursorView, ...]:
        if type(limit) is not int or not 1 <= limit <= 100:
            raise InvalidInput("Channel synchronization page is invalid")
        query = select(ChannelSyncCursorRecord).where(ChannelSyncCursorRecord.coordinate_kind != "done")
        if after_id is not None:
            query = query.where(ChannelSyncCursorRecord.id > after_id)
        return tuple(self._read(row) for row in (await self.tx.session.scalars(query.order_by(ChannelSyncCursorRecord.id).limit(limit))).all())

    async def advance(self, previous: SyncCursorView, *, next_cursor: Secret | None) -> SyncCursorView:
        row = await self.tx.session.scalar(select(ChannelSyncCursorRecord).where(ChannelSyncCursorRecord.tenant_id == previous.tenant_id,
            ChannelSyncCursorRecord.id == previous.id).with_for_update().execution_options(populate_existing=True))
        if row is None:
            raise NotFound("Channel synchronization cursor is unavailable")
        if row.cursor_version != previous.version:
            raise Conflict("Channel synchronization cursor advanced concurrently")
        current = self._read(row)
        row.cursor_version += 1
        row.coordinate_kind = "cursor" if next_cursor is not None else "done"
        row.updated_at = datetime.now(UTC)
        self._seal(row, current.open_kfid, next_cursor)
        await self.tx.session.flush()
        return self._read(row)
