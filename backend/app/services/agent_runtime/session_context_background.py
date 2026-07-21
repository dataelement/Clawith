"""Rebuildable background Session Compact scheduling from durable chat state."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
import asyncio
import hashlib
import json
import logging
import math
import uuid

import sqlalchemy as sa
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, AsyncSession

from app.config import Settings, get_settings
from app.models.agent import Agent
from app.models.chat_session import ChatSession
from app.models.group import Group, GroupMember
from app.models.llm import LLMModel
from app.models.participant import Participant
from app.services.agent_runtime.command_worker import RuntimeSessionFactory
from app.services.agent_runtime.model_capabilities import (
    ModelCapabilityError,
    ModelCapabilityResolver,
)
from app.services.agent_runtime.session_context_completion import (
    SessionCompactRequest,
    SessionContextCompactor,
)
from app.services.agent_runtime.session_context_service import (
    SessionContextCandidate,
    SessionContextConflict,
    SessionContextService,
    SessionContextSnapshot,
)
from app.services.agent_runtime.state import JsonObject
from app.services.llm.utils import get_max_tokens


logger = logging.getLogger(__name__)
_ACTIVE_AGENT_STATUSES = frozenset({"creating", "running", "idle"})
_ACQUIRE_LOCK = sa.text("SELECT pg_try_advisory_lock(:lock_key)")
_RELEASE_LOCK = sa.text("SELECT pg_advisory_unlock(:lock_key)")


class SessionContextBackgroundError(RuntimeError):
    """A message-driven Session Compact cannot proceed safely."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class SessionCompactLockBusy(RuntimeError):
    """Another Runtime worker is already compacting this session."""


@dataclass(frozen=True, slots=True)
class SessionCompactPolicy:
    """The shared-context trigger budget for one active session."""

    source_agent_id: uuid.UUID | None
    threshold_tokens: int
    contributing_model_ids: tuple[uuid.UUID, ...]


def _estimate_tokens(value: object) -> int:
    serialized = json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        default=str,
    )
    return max(1, math.ceil(len(serialized.encode("utf-8")) / 4))


def _model_threshold(model: LLMModel, settings: Settings) -> int:
    requested_output = get_max_tokens(
        model.provider,
        model.model,
        model.max_output_tokens,
    )
    return ModelCapabilityResolver.runtime_budget(
        model,
        requested_max_output_tokens=requested_output,
        reserved_runtime_tokens=256,
        safety_margin_tokens=256,
        compact_threshold_ratio=settings.AGENT_RUNTIME_SUMMARY_THRESHOLD_RATIO,
    ).compact_threshold


def _usable_model(
    model: LLMModel | None,
    *,
    tenant_id: uuid.UUID,
) -> bool:
    return bool(
        model is not None
        and model.enabled
        and model.tenant_id in {None, tenant_id}
    )


class SessionCompactPolicyResolver:
    """Calculate the public Group Session trigger budget."""

    def __init__(self, *, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()

    async def resolve(
        self,
        db: AsyncSession,
        *,
        tenant_id: uuid.UUID,
        session_id: uuid.UUID,
    ) -> SessionCompactPolicy:
        session_result = await db.execute(
            select(ChatSession).where(
                ChatSession.id == session_id,
                ChatSession.tenant_id == tenant_id,
                ChatSession.deleted_at.is_(None),
            )
        )
        session = session_result.scalar_one_or_none()
        if session is None:
            raise SessionContextBackgroundError(
                "session_context_unavailable",
                "Session Compact target no longer exists",
            )

        if session.session_type != "group":
            raise SessionContextBackgroundError(
                "direct_thread_owns_context",
                "Direct Chat context is owned only by its LangGraph Thread",
            )

        if session.group_id is None:
            raise SessionContextBackgroundError(
                "session_compact_budget_unavailable",
                "Group session has no group identity",
            )
        group_result = await db.execute(
            select(Group.id).where(
                Group.id == session.group_id,
                Group.tenant_id == tenant_id,
                Group.deleted_at.is_(None),
            )
        )
        if group_result.scalar_one_or_none() is None:
            raise SessionContextBackgroundError(
                "session_context_unavailable",
                "Group Session Compact target no longer exists",
            )
        agent_result = await db.execute(
            select(Agent)
            .join(
                Participant,
                (Participant.type == "agent") & (Participant.ref_id == Agent.id),
            )
            .join(
                GroupMember,
                GroupMember.participant_id == Participant.id,
            )
            .where(
                GroupMember.group_id == session.group_id,
                GroupMember.removed_at.is_(None),
                Agent.tenant_id == tenant_id,
                Agent.status.in_(_ACTIVE_AGENT_STATUSES),
                Agent.is_expired.is_(False),
                Agent.access_mode != "private",
                Agent.primary_model_id.is_not(None),
            )
        )
        agents = list(agent_result.scalars().all())
        model_ids = {agent.primary_model_id for agent in agents if agent.primary_model_id}
        if not model_ids:
            raise SessionContextBackgroundError(
                "session_compact_budget_unavailable",
                "Group has no valid Agent models for shared compact budgeting",
            )
        model_result = await db.execute(
            select(LLMModel).where(LLMModel.id.in_(model_ids))
        )
        models = {
            model.id: model
            for model in model_result.scalars().all()
            if _usable_model(model, tenant_id=tenant_id)
        }
        missing = model_ids - models.keys()
        if missing:
            raise SessionContextBackgroundError(
                "session_compact_budget_unavailable",
                "At least one active group Agent model is not usable",
            )
        try:
            thresholds = {
                model.id: _model_threshold(model, self._settings)
                for model in models.values()
            }
        except ModelCapabilityError as exc:
            raise SessionContextBackgroundError(exc.code, str(exc)) from exc
        return SessionCompactPolicy(
            source_agent_id=None,
            threshold_tokens=min(thresholds.values()),
            contributing_model_ids=tuple(sorted(thresholds, key=str)),
        )

    def should_compact(
        self,
        *,
        snapshot: SessionContextSnapshot,
        messages: tuple[JsonObject, ...],
        recent_messages: tuple[JsonObject, ...] = (),
        policy: SessionCompactPolicy,
    ) -> bool:
        """Apply early message-count and hard token triggers to the old-message zone."""
        if not messages:
            return False
        message_threshold = self._settings.AGENT_RUNTIME_SESSION_COMPACT_MESSAGE_THRESHOLD
        if message_threshold is not None and len(messages) >= message_threshold:
            return True
        estimated = _estimate_tokens(
            {
                "session_context": snapshot.to_json(),
                "compactable_messages": messages,
                "recent_messages": recent_messages,
            }
        )
        return estimated >= policy.threshold_tokens


def _session_lock_key(session_id: uuid.UUID) -> int:
    digest = hashlib.blake2b(
        session_id.bytes,
        digest_size=8,
        person=b"claw-ctx-v1",
    ).digest()
    return int.from_bytes(digest, byteorder="big", signed=True)


async def _with_session_lock(
    engine: AsyncEngine,
    session_id: uuid.UUID,
    callback: Callable[[AsyncConnection], Awaitable[bool]],
) -> bool:
    lock_key = _session_lock_key(session_id)
    async with engine.connect() as connection:
        acquired = await connection.execute(_ACQUIRE_LOCK, {"lock_key": lock_key})
        if not bool(acquired.scalar_one()):
            raise SessionCompactLockBusy(str(session_id))
        # ``execute`` implicitly starts a transaction.  End that transaction
        # before binding an AsyncSession so its CAS transaction can commit on
        # this connection.  PostgreSQL session advisory locks survive commits.
        await connection.commit()
        try:
            return await callback(connection)
        finally:
            released = await connection.execute(_RELEASE_LOCK, {"lock_key": lock_key})
            if not bool(released.scalar_one()):
                logger.error("Session Compact advisory lock release failed for %s", session_id)
            await connection.commit()


class SessionContextMessageCompactionService:
    """Compact old public messages without creating a Compact Agent or Run."""

    def __init__(
        self,
        *,
        lock_engine: AsyncEngine,
        compactor: SessionContextCompactor,
        context_service: SessionContextService,
        policy_resolver: SessionCompactPolicyResolver,
        max_conflict_retries: int = 3,
    ) -> None:
        if max_conflict_retries <= 0:
            raise ValueError("max_conflict_retries must be positive")
        self._lock_engine = lock_engine
        self._compactor = compactor
        self._context_service = context_service
        self._policy_resolver = policy_resolver
        self._max_conflict_retries = max_conflict_retries

    async def _load_request(
        self,
        connection: AsyncConnection,
        *,
        tenant_id: uuid.UUID,
        session_id: uuid.UUID,
    ) -> SessionCompactRequest | None:
        async with AsyncSession(bind=connection, expire_on_commit=False) as db:
            policy = await self._policy_resolver.resolve(
                db,
                tenant_id=tenant_id,
                session_id=session_id,
            )
            snapshot = await self._context_service.load_snapshot(
                db,
                tenant_id=tenant_id,
                session_id=session_id,
            )
            messages = await self._context_service.load_compactable_messages_after_watermark(
                db,
                tenant_id=tenant_id,
                session_id=session_id,
                covered_through_message_id=snapshot.covered_through_message_id,
            )
            recent_messages = await self._context_service.load_recent_user_visible_messages(
                db,
                tenant_id=tenant_id,
                session_id=session_id,
            )
        if not self._policy_resolver.should_compact(
            snapshot=snapshot,
            messages=messages,
            recent_messages=recent_messages,
            policy=policy,
        ):
            return None
        return SessionCompactRequest(
            tenant_id=tenant_id,
            session_id=session_id,
            source_agent_id=policy.source_agent_id,
            checkpoint_id=(
                f"message-window:{snapshot.version}:{messages[-1]['id']}"
            ),
            snapshot=snapshot,
            messages=messages,
            delta=None,
        )

    async def _commit(
        self,
        connection: AsyncConnection,
        *,
        request: SessionCompactRequest,
        candidate: SessionContextCandidate,
    ) -> None:
        expected_watermark = uuid.UUID(str(request.messages[-1]["id"]))
        if candidate.covered_through_message_id != expected_watermark:
            raise SessionContextBackgroundError(
                "session_context_watermark_mismatch",
                "Message-driven compactor changed the deterministic watermark",
            )
        async with AsyncSession(bind=connection, expire_on_commit=False) as db:
            async with db.begin():
                current = await self._context_service.load_snapshot(
                    db,
                    tenant_id=request.tenant_id,
                    session_id=request.session_id,
                )
                if current != request.snapshot:
                    raise SessionContextConflict()
                await self._context_service.compare_and_swap(
                    db,
                    tenant_id=request.tenant_id,
                    session_id=request.session_id,
                    expected_version=request.snapshot.version,
                    expected_covered_through_message_id=(
                        request.snapshot.covered_through_message_id
                    ),
                    candidate=candidate,
                )

    async def compact_session(
        self,
        *,
        tenant_id: uuid.UUID,
        session_id: uuid.UUID,
    ) -> bool:
        """Return true only when this call advances the compact watermark."""

        async def locked(connection: AsyncConnection) -> bool:
            for _attempt in range(self._max_conflict_retries):
                request = await self._load_request(
                    connection,
                    tenant_id=tenant_id,
                    session_id=session_id,
                )
                if request is None:
                    return False
                candidate = await self._compactor.compact(request)
                try:
                    await self._commit(
                        connection,
                        request=request,
                        candidate=candidate,
                    )
                except SessionContextConflict:
                    continue
                return True
            raise SessionContextBackgroundError(
                "session_context_conflict_limit",
                "Session Context kept changing during background compaction",
            )

        try:
            return await _with_session_lock(
                self._lock_engine,
                session_id,
                locked,
            )
        except SessionCompactLockBusy:
            return False


class SessionContextCompactionScanner:
    """Fairly scan active sessions; all pending work is reconstructible from rows."""

    def __init__(
        self,
        *,
        session_factory: RuntimeSessionFactory,
        service: SessionContextMessageCompactionService,
        settings: Settings | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._service = service
        self._settings = settings or get_settings()
        self._cursor: uuid.UUID | None = None

    async def scan_once(self) -> int:
        async with self._session_factory() as db:
            statement = (
                select(ChatSession.tenant_id, ChatSession.id)
                .where(
                    ChatSession.deleted_at.is_(None),
                    ChatSession.last_message_at.is_not(None),
                    ChatSession.session_type == "group",
                )
                .order_by(ChatSession.id)
                .limit(self._settings.AGENT_RUNTIME_SESSION_COMPACT_SCAN_BATCH_SIZE)
            )
            if self._cursor is not None:
                statement = statement.where(ChatSession.id > self._cursor)
            result = await db.execute(statement)
            candidates = list(result.all())
        if not candidates:
            self._cursor = None
            return 0
        self._cursor = candidates[-1][1]
        compacted = 0
        for tenant_id, session_id in candidates:
            try:
                compacted += int(
                    await self._service.compact_session(
                        tenant_id=tenant_id,
                        session_id=session_id,
                    )
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception(
                    "Background Session Compact failed for session %s",
                    session_id,
                )
        return compacted

    async def run(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            try:
                await self.scan_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Background Session Compact scan failed")
            try:
                await asyncio.wait_for(
                    stop.wait(),
                    timeout=self._settings.AGENT_RUNTIME_SESSION_COMPACT_SCAN_SECONDS,
                )
            except TimeoutError:
                pass


__all__ = [
    "SessionCompactPolicy",
    "SessionCompactPolicyResolver",
    "SessionContextBackgroundError",
    "SessionContextCompactionScanner",
    "SessionContextMessageCompactionService",
]
