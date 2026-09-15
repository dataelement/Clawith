"""Session-owned append positions and associations in the caller's transaction."""

from uuid import UUID

from sqlalchemy import Text, cast, func, or_, select
from sqlalchemy.orm import load_only

from app.infrastructure.errors import InvalidInput, NotFound
from app.infrastructure.transactions import TransactionContext
from app.modules.session.models import SessionEntryRecord, SessionRecord, SessionRunLinkRecord

MAX_ENTRY_BYTES = 256 * 1024
MAX_STORED_BYTES = MAX_ENTRY_BYTES + 4096
MAX_PAGE_BYTES = 16 * 1024 * 1024
MAX_GOAL_BYTES = 65536
MAX_STORED_GOAL_BYTES = MAX_GOAL_BYTES + 1024


class SessionRepository:
    def __init__(self, transaction: TransactionContext) -> None:
        self.session = transaction.session

    async def get(self, tenant_id: UUID, session_id: UUID, *, lock: bool = False) -> SessionRecord:
        query = select(SessionRecord).where(SessionRecord.tenant_id == tenant_id, SessionRecord.id == session_id).options(
            load_only(SessionRecord.id, SessionRecord.tenant_id, SessionRecord.agent_id, SessionRecord.membership_id,
                SessionRecord.next_position, SessionRecord.created_at, SessionRecord.updated_at, SessionRecord.goal_enabled,
                SessionRecord.goal_configuration_version, SessionRecord.goal_input_id))
        if lock:
            query = query.with_for_update().execution_options(populate_existing=True)
        row = await self.session.scalar(query)
        if row is None:
            raise NotFound("Session is unavailable")
        return row

    async def goal_session(self, tenant_id: UUID, session_id: UUID, *, lock: bool = False) -> SessionRecord:
        size = func.octet_length(cast(SessionRecord.goal_configuration, Text))
        query = select(SessionRecord).where(SessionRecord.tenant_id == tenant_id, SessionRecord.id == session_id)
        metadata = await self.session.scalar(query.with_only_columns(size))
        if metadata is None:
            raise NotFound("Session is unavailable")
        if metadata > MAX_STORED_GOAL_BYTES:
            raise InvalidInput("Stored Goal configuration exceeds its bound")
        query = query.where(size <= MAX_STORED_GOAL_BYTES).execution_options(populate_existing=True)
        row = await self.session.scalar(query.with_for_update() if lock else query)
        if row is None:
            raise InvalidInput("Goal configuration changed while reading")
        return row

    async def due_goals(self, *, now: str, not_before: str, after_id: UUID | None,
                        limit: int) -> tuple[tuple[SessionRecord, ...], tuple[UUID, ...], UUID | None, bool]:
        config = SessionRecord.goal_configuration
        size = func.octet_length(cast(config, Text))
        query = select(SessionRecord.id, size.label("size")).where(SessionRecord.goal_enabled.is_(True),
            config["due_at"].as_string() <= now, config["scheduled_at"].as_string() >= not_before)
        if after_id is not None:
            query = query.where(SessionRecord.id > after_id)
        metadata = (await self.session.execute(query.order_by(SessionRecord.id).limit(limit + 1))).all()
        if not metadata:
            return (), (), None, False
        selected = metadata[:limit]
        rows = tuple((await self.session.scalars(select(SessionRecord).where(SessionRecord.id.in_([row.id for row in selected]),
            size <= MAX_STORED_GOAL_BYTES).order_by(SessionRecord.id).execution_options(populate_existing=True))).all())
        loaded = {row.id for row in rows}
        invalid = tuple(row.id for row in selected if row.id not in loaded)
        return rows, invalid, selected[-1].id, len(metadata) > limit

    async def list(self, tenant_id: UUID, membership_id: UUID, *, agents: frozenset[UUID] | None,
                   after_id: UUID | None, limit: int) -> tuple[SessionRecord, ...]:
        query = select(SessionRecord).where(SessionRecord.tenant_id == tenant_id, SessionRecord.membership_id == membership_id).options(
            load_only(SessionRecord.id, SessionRecord.tenant_id, SessionRecord.agent_id, SessionRecord.membership_id,
                SessionRecord.next_position, SessionRecord.created_at, SessionRecord.updated_at, SessionRecord.goal_enabled,
                SessionRecord.goal_configuration_version))
        if agents is not None:
            query = query.where(SessionRecord.agent_id.in_(agents))
        if after_id is not None:
            query = query.where(SessionRecord.id > after_id)
        return tuple((await self.session.scalars(query.order_by(SessionRecord.id).limit(limit + 1))).all())

    async def entry_by_key(self, tenant_id: UUID, session_id: UUID, *, key: str, message: bool = False) -> SessionEntryRecord | None:
        column = SessionEntryRecord.message_key if message else SessionEntryRecord.source_key
        metadata = (await self.session.execute(select(SessionEntryRecord.id,
            func.octet_length(cast(SessionEntryRecord.payload, Text))).where(SessionEntryRecord.tenant_id == tenant_id,
                SessionEntryRecord.session_id == session_id, column == key))).one_or_none()
        if metadata is None:
            return None
        return await self.entry(tenant_id, session_id, metadata.id)

    async def entry(self, tenant_id: UUID, session_id: UUID, entry_id: UUID) -> SessionEntryRecord:
        size = await self.session.scalar(select(func.octet_length(cast(SessionEntryRecord.payload, Text))).where(
            SessionEntryRecord.tenant_id == tenant_id, SessionEntryRecord.session_id == session_id, SessionEntryRecord.id == entry_id))
        if size is None:
            raise NotFound("Session entry is unavailable")
        if size > MAX_STORED_BYTES:
            raise InvalidInput("Stored Session entry exceeds its bound")
        row = await self.session.scalar(select(SessionEntryRecord).where(SessionEntryRecord.tenant_id == tenant_id,
            SessionEntryRecord.session_id == session_id, SessionEntryRecord.id == entry_id,
            func.octet_length(cast(SessionEntryRecord.payload, Text)) <= MAX_STORED_BYTES))
        if row is None:
            raise InvalidInput("Stored Session entry changed while reading")
        return row

    async def message(self, tenant_id: UUID, agent_id: UUID, message_id: UUID) -> SessionEntryRecord:
        session_id = await self.session.scalar(select(SessionEntryRecord.session_id).where(
            SessionEntryRecord.tenant_id == tenant_id, SessionEntryRecord.agent_id == agent_id,
            SessionEntryRecord.id == message_id, SessionEntryRecord.kind == "reply"))
        if session_id is None:
            raise NotFound("Session message is unavailable")
        return await self.entry(tenant_id, session_id, message_id)

    async def entries(self, tenant_id: UUID, session_id: UUID, *, after: int, through: int,
                      limit: int, max_bytes: int) -> tuple[SessionEntryRecord, ...]:
        metadata = (await self.session.execute(select(SessionEntryRecord.id, SessionEntryRecord.position,
            func.octet_length(cast(SessionEntryRecord.payload, Text)).label("size")).where(
                SessionEntryRecord.tenant_id == tenant_id, SessionEntryRecord.session_id == session_id,
                SessionEntryRecord.position > after, SessionEntryRecord.position <= through)
                .order_by(SessionEntryRecord.position).limit(limit))).all()
        used = 1024
        selected = []
        for index, item in enumerate(metadata):
            if item.position != after + index + 1 or item.size > MAX_STORED_BYTES:
                raise InvalidInput("Stored Session history is incomplete or oversized")
            if used + item.size + 4096 > max_bytes:
                if not selected:
                    raise InvalidInput("Session entry cannot fit the requested page")
                break
            selected.append(item.id)
            used += item.size + 4096
        if not metadata and after < through:
            raise InvalidInput("Stored Session history is incomplete")
        if not selected:
            return ()
        rows = tuple((await self.session.scalars(select(SessionEntryRecord).where(
            SessionEntryRecord.tenant_id == tenant_id, SessionEntryRecord.session_id == session_id,
            SessionEntryRecord.id.in_(selected), func.octet_length(cast(SessionEntryRecord.payload, Text)) <= MAX_STORED_BYTES)
            .order_by(SessionEntryRecord.position))).all())
        if len(rows) != len(selected):
            raise InvalidInput("Stored Session history changed while reading")
        return rows

    async def context_tail(self, tenant_id: UUID, session_id: UUID, *, through: int, limit: int):
        return (await self.session.execute(select(SessionEntryRecord.id, SessionEntryRecord.position,
            SessionEntryRecord.kind, func.octet_length(cast(SessionEntryRecord.payload, Text)).label("size"))
            .where(SessionEntryRecord.tenant_id == tenant_id, SessionEntryRecord.session_id == session_id,
                SessionEntryRecord.position <= through).order_by(SessionEntryRecord.position.desc()).limit(limit + 1))).all()

    async def selected_entries(self, tenant_id: UUID, session_id: UUID, ids: tuple[UUID, ...]) -> dict[UUID, SessionEntryRecord]:
        if not ids:
            return {}
        rows = (await self.session.scalars(select(SessionEntryRecord).where(SessionEntryRecord.tenant_id == tenant_id,
            SessionEntryRecord.session_id == session_id, SessionEntryRecord.id.in_(ids),
            func.octet_length(cast(SessionEntryRecord.payload, Text)) <= MAX_STORED_BYTES))).all()
        if len(rows) != len(ids):
            raise InvalidInput("Stored Session entries changed while reading")
        return {row.id: row for row in rows}

    async def fragment(self, tenant_id: UUID, session_id: UUID, *, after: int, through: int, offset: int, characters: int):
        # Authorization metadata is not model-visible conversation material.
        body = cast(SessionEntryRecord.payload.op("-")("account_selections"), Text)
        metadata = (await self.session.execute(select(SessionEntryRecord.id, SessionEntryRecord.position, SessionEntryRecord.kind,
            SessionEntryRecord.payload_version, func.octet_length(body).label("size"), func.char_length(body).label("characters"))
            .where(SessionEntryRecord.tenant_id == tenant_id, SessionEntryRecord.session_id == session_id,
                SessionEntryRecord.position > after, SessionEntryRecord.position <= through)
            .order_by(SessionEntryRecord.position).limit(1))).one_or_none()
        if metadata is None:
            if after < through or offset:
                raise InvalidInput("Session history fragment has no valid target")
            return None
        if metadata.position != after + 1 or metadata.payload_version != 1 or metadata.kind not in ("input", "reply") or metadata.size > MAX_STORED_BYTES:
            raise InvalidInput("Stored Session history fragment is invalid")
        if offset >= metadata.characters:
            raise InvalidInput("Session fragment offset is outside the entry")
        content = await self.session.scalar(select(func.substring(body, offset + 1, characters)).where(
            SessionEntryRecord.tenant_id == tenant_id, SessionEntryRecord.session_id == session_id,
            SessionEntryRecord.id == metadata.id, func.octet_length(body) <= MAX_STORED_BYTES))
        if content is None:
            raise InvalidInput("Session history fragment changed while reading")
        return metadata, content

    async def link(self, tenant_id: UUID, session_id: UUID, *, link_id: UUID | None = None,
                   run_id: UUID | None = None, source_key: str | None = None, lock: bool = False) -> SessionRunLinkRecord | None:
        query = select(SessionRunLinkRecord).where(SessionRunLinkRecord.tenant_id == tenant_id,
            SessionRunLinkRecord.session_id == session_id)
        if link_id is not None:
            query = query.where(SessionRunLinkRecord.id == link_id)
        elif run_id is not None:
            query = query.where(SessionRunLinkRecord.run_id == run_id)
        elif source_key is not None:
            query = query.where(SessionRunLinkRecord.source_key == source_key)
        else:
            raise ValueError("Session association requires an identity")
        rows = await self._bounded_links(query.with_for_update().execution_options(populate_existing=True) if lock else query)
        return rows[0] if rows else None

    async def links(self, tenant_id: UUID, session_id: UUID, *, after_id: UUID | None, limit: int) -> tuple[SessionRunLinkRecord, ...]:
        query = select(SessionRunLinkRecord).where(SessionRunLinkRecord.tenant_id == tenant_id, SessionRunLinkRecord.session_id == session_id)
        if after_id is not None:
            query = query.where(SessionRunLinkRecord.id > after_id)
        return await self._bounded_links(query.order_by(SessionRunLinkRecord.id).limit(limit + 1))

    async def _bounded_links(self, query) -> tuple[SessionRunLinkRecord, ...]:
        size = func.octet_length(cast(SessionRunLinkRecord.result, Text))
        metadata = (await self.session.execute(query.with_only_columns(SessionRunLinkRecord.id, size))).all()
        if any(value is not None and value > 8192 for _, value in metadata):
            raise InvalidInput("Stored Session result index exceeds its bound")
        if not metadata:
            return ()
        rows = tuple((await self.session.scalars(query.where(SessionRunLinkRecord.id.in_([identity for identity, _ in metadata]),
            or_(SessionRunLinkRecord.result.is_(None), size <= 8192)))).all())
        if len(rows) != len(metadata):
            raise InvalidInput("Stored Session result index changed while reading")
        return rows
