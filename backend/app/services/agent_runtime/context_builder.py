"""Build immutable Run inputs and model-facing Runtime Context sections.

The builder accepts only checkpoint contracts from ``state.py``.  It never
loads a mutable Run ORM row and therefore cannot accidentally use a query
projection as execution state.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import asdict, dataclass
from datetime import datetime
import math
from typing import TYPE_CHECKING, Any
import uuid

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.config import Settings, get_settings
from app.models.chat_session import ChatSession
from app.services.agent_runtime.session_context_service import (
    MessagePosition,
    SessionContextPack,
    SessionContextService,
    SessionContextSnapshot,
)
from app.services.agent_runtime.session_context_completion import (
    SessionCompactRequest,
    SessionContextCompactor,
)
from app.services.agent_runtime.state import (
    JsonObject,
    JsonValue,
    RunInputSnapshots,
    RuntimeContext,
    RuntimeGraphState,
    runtime_messages_as_json,
)
from app.services.agent_runtime.tool_exchange import (
    Ledger,
    TokenCounter,
    ToolExchangeCompactionSummary,
    build_recent_tool_safe_window,
)

if TYPE_CHECKING:
    from app.services.agent_runtime.group_context_builder import GroupContextBuilder


class ContextBuildError(RuntimeError):
    """Checkpoint input cannot be assembled into safe model context."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class RuntimeContextBuild:
    """Structured context plus directives produced by Tool Exchange selection."""

    session_context_snapshot: JsonObject
    current_run: JsonObject
    related_run_summaries: tuple[JsonObject, ...]
    recent_session_messages_snapshot: tuple[JsonObject, ...]
    thread_running_summary: JsonObject | None
    recent_thread_messages: tuple[JsonObject, ...]
    initial_input: JsonObject
    resume_input: JsonValue | None
    omitted_tool_exchanges: tuple[ToolExchangeCompactionSummary, ...]
    retry_model: bool
    blocked: bool
    requires_confirmation: bool
    pending_session_messages_snapshot: tuple[JsonObject, ...] = ()

    def to_json(self) -> JsonObject:
        """Return the serializable prompt sections without control metadata loss."""
        return {
            "session_context_snapshot": deepcopy(self.session_context_snapshot),
            "current_run": deepcopy(self.current_run),
            "related_run_summaries": [deepcopy(summary) for summary in self.related_run_summaries],
            "pending_session_messages_snapshot": [
                deepcopy(message) for message in self.pending_session_messages_snapshot
            ],
            "recent_session_messages_snapshot": [
                deepcopy(message) for message in self.recent_session_messages_snapshot
            ],
            "thread_running_summary": deepcopy(self.thread_running_summary),
            "recent_thread_messages": [
                deepcopy(message) for message in self.recent_thread_messages
            ],
            "initial_input": deepcopy(self.initial_input),
            "resume_input": deepcopy(self.resume_input),
            "omitted_tool_exchanges": [_tool_summary_to_json(summary) for summary in self.omitted_tool_exchanges],
            "retry_model": self.retry_model,
            "blocked": self.blocked,
            "requires_confirmation": self.requires_confirmation,
        }


def _json_value(value: object, *, field: str) -> JsonValue:
    if value is None or isinstance(value, (str, int, bool)):
        return deepcopy(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ContextBuildError(
                "invalid_runtime_context",
                f"{field} contains a non-finite number",
            )
        return value
    if isinstance(value, Mapping):
        result: dict[str, JsonValue] = {}
        for key, nested in value.items():
            if not isinstance(key, str):
                raise ContextBuildError(
                    "invalid_runtime_context",
                    f"{field} contains a non-string object key",
                )
            result[key] = _json_value(nested, field=field)
        return result
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_json_value(nested, field=field) for nested in value]
    raise ContextBuildError(
        "invalid_runtime_context",
        f"{field} contains a value that is not JSON serializable",
    )


def _json_object(value: object, *, field: str) -> JsonObject:
    copied = _json_value(value, field=field)
    if not isinstance(copied, dict):
        raise ContextBuildError(
            "invalid_runtime_context",
            f"{field} must be an object",
        )
    return copied


def _json_objects(value: object, *, field: str) -> tuple[JsonObject, ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise ContextBuildError(
            "invalid_runtime_context",
            f"{field} must be an array",
        )
    return tuple(_json_object(item, field=f"{field}[{index}]") for index, item in enumerate(value))


def _tool_summary_to_json(summary: ToolExchangeCompactionSummary) -> JsonObject:
    return _json_object(asdict(summary), field="omitted_tool_exchange")


def _empty_session_snapshot() -> JsonObject:
    return SessionContextSnapshot.empty().to_json()


def _uuid_string(value: object, *, field: str) -> uuid.UUID:
    if not isinstance(value, str):
        raise ContextBuildError(
            "invalid_group_context_cutoff",
            f"{field} must be a UUID string",
        )
    try:
        return uuid.UUID(value)
    except ValueError as exc:
        raise ContextBuildError(
            "invalid_group_context_cutoff",
            f"{field} must be a UUID string",
        ) from exc


def _timestamp(value: object, *, field: str) -> datetime:
    if not isinstance(value, str):
        raise ContextBuildError(
            "invalid_group_context_cutoff",
            f"{field} must be an ISO timestamp",
        )
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ContextBuildError(
            "invalid_group_context_cutoff",
            f"{field} must be an ISO timestamp",
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ContextBuildError(
            "invalid_group_context_cutoff",
            f"{field} must include a timezone",
        )
    return parsed


def _group_cutoff(
    initial_input: Mapping[str, object],
    *,
    source_type: str | None,
    source_id: str | None,
    scheduling_position_created_at: datetime | None,
    scheduling_position_id: uuid.UUID | None,
) -> MessagePosition:
    raw_cutoff = initial_input.get("context_cutoff")
    if not isinstance(raw_cutoff, Mapping):
        raise ContextBuildError(
            "invalid_group_context_cutoff",
            "Group Agent input requires a context_cutoff object",
        )
    cutoff_id = _uuid_string(
        raw_cutoff.get("message_id"),
        field="context_cutoff.message_id",
    )
    cutoff_created_at = _timestamp(
        raw_cutoff.get("created_at"),
        field="context_cutoff.created_at",
    )
    message_id = _uuid_string(
        initial_input.get("message_id"),
        field="message_id",
    )
    if (
        source_type != "chat"
        or source_id != str(cutoff_id)
        or message_id != cutoff_id
        or scheduling_position_id != cutoff_id
        or scheduling_position_created_at is None
        or scheduling_position_created_at.tzinfo is None
        or scheduling_position_created_at.utcoffset() is None
        or scheduling_position_created_at != cutoff_created_at
    ):
        raise ContextBuildError(
            "invalid_group_context_cutoff",
            "Group payload, source, and scheduling Message Position must match",
        )
    return MessagePosition(
        created_at=cutoff_created_at,
        message_id=cutoff_id,
    )


def _current_run_section(
    state: RuntimeGraphState,
    context: RuntimeContext,
) -> JsonObject:
    lifecycle = state["lifecycle"]
    return _json_object(
        {
            "run_id": context.run_id,
            "tenant_id": context.tenant_id,
            "agent_id": context.agent_id,
            "session_id": context.session_id,
            "goal": context.goal,
            "run_kind": context.run_kind,
            "source_type": context.source_type,
            "model_id": context.model_id,
            "graph_name": context.graph_name,
            "graph_version": context.graph_version,
            "system_role": context.system_role,
            "parent_run_id": context.parent_run_id,
            "root_run_id": context.root_run_id,
            "lifecycle_status": lifecycle["status"],
            "next_route": lifecycle["next_route"],
            "reason": lifecycle.get("reason"),
            "pending_tool_calls": lifecycle.get("pending_tool_calls", []),
            "waiting_request": lifecycle.get("waiting_request"),
            "verification_result": lifecycle.get("verification_result"),
        },
        field="current_run",
    )


def _validate_session_messages(messages: Sequence[Mapping[str, Any]]) -> None:
    for message in messages:
        if message.get("role") not in {"user", "assistant"}:
            raise ContextBuildError(
                "invalid_session_message",
                "recent Session messages must contain only user-visible roles",
            )
        message_id = message.get("id")
        if not isinstance(message_id, str) or not message_id:
            raise ContextBuildError(
                "invalid_session_message",
                "recent Session messages require stable IDs",
            )


class ContextBuilder:
    """Capture new-Run snapshots and select a tool-safe active message window."""

    def __init__(
        self,
        session_context_service: SessionContextService,
        *,
        settings: Settings | None = None,
        group_context_builder: GroupContextBuilder | None = None,
        session_context_compactor: SessionContextCompactor | None = None,
    ) -> None:
        runtime_settings = settings or get_settings()
        if group_context_builder is None:
            from app.services.agent_runtime.group_context_builder import (
                GroupContextBuilder,
            )

            group_context_builder = GroupContextBuilder(settings=runtime_settings)
        self.session_context_service = session_context_service
        self.group_context_builder = group_context_builder
        self.session_context_compactor = session_context_compactor

    async def _rebuild_group_context_pack(
        self,
        *,
        tenant_id: uuid.UUID,
        session_id: uuid.UUID,
        cutoff: MessagePosition,
        pack: SessionContextPack,
    ) -> SessionContextPack:
        if not pack.requires_transient_rebuild:
            return pack
        if not pack.pending_messages:
            return SessionContextPack(
                snapshot=SessionContextSnapshot.empty(),
                recent_messages=pack.recent_messages,
                pending_messages=(),
                requires_transient_rebuild=False,
            )
        if self.session_context_compactor is None:
            raise ContextBuildError(
                "group_context_cutoff_rebuild_unavailable",
                "Group cutoff predates the rolling Session Context and no compactor is configured",
            )
        request = SessionCompactRequest(
            tenant_id=tenant_id,
            session_id=session_id,
            source_agent_id=None,
            checkpoint_id=(
                f"group-cutoff:{cutoff.created_at.isoformat()}:{cutoff.message_id}"
            ),
            snapshot=SessionContextSnapshot.empty(),
            messages=pack.pending_messages,
            delta=None,
        )
        try:
            candidate = await self.session_context_compactor.compact(request)
        except Exception as exc:
            raise ContextBuildError(
                "group_context_cutoff_rebuild_failed",
                "Group cutoff Session Context could not be reconstructed safely",
            ) from exc
        expected_watermark = _uuid_string(
            pack.pending_messages[-1].get("id"),
            field="pending_session_messages[-1].id",
        )
        if candidate.covered_through_message_id != expected_watermark:
            raise ContextBuildError(
                "group_context_cutoff_rebuild_failed",
                "Group cutoff compactor changed the deterministic watermark",
            )
        transient_snapshot = SessionContextSnapshot(
            version=0,
            summary=candidate.summary,
            requirements=tuple(candidate.requirements),
            decisions=tuple(candidate.decisions),
            open_items=tuple(candidate.open_items),
            evidence_refs=tuple(candidate.evidence_refs),
            workspace_refs=tuple(candidate.workspace_refs),
            covered_through_message_id=candidate.covered_through_message_id,
        )
        return SessionContextPack(
            snapshot=transient_snapshot,
            recent_messages=pack.recent_messages,
            pending_messages=(),
            requires_transient_rebuild=False,
        )

    async def capture_run_inputs(
        self,
        db: AsyncSession,
        *,
        tenant_id: uuid.UUID,
        session_id: uuid.UUID | None,
        agent_id: uuid.UUID | None = None,
        source_type: str | None = None,
        source_id: str | None = None,
        scheduling_position_created_at: datetime | None = None,
        scheduling_position_id: uuid.UUID | None = None,
        initial_input: Mapping[str, Any],
        related_run_summaries: Sequence[Mapping[str, Any]] = (),
    ) -> RunInputSnapshots:
        """Freeze new-Run inputs; resumed Runs must reuse the checkpoint copy."""
        normalized_input = _json_object(initial_input, field="initial_input")
        normalized_related = _json_objects(
            related_run_summaries,
            field="related_run_summaries",
        )
        if session_id is None:
            session_context = _empty_session_snapshot()
            session_context_version = 0
            pending_messages: tuple[JsonObject, ...] = ()
            recent_messages: tuple[JsonObject, ...] = ()
        else:
            session_result = await db.execute(
                select(ChatSession.session_type).where(
                    ChatSession.tenant_id == tenant_id,
                    ChatSession.id == session_id,
                )
            )
            session_type = session_result.scalar_one_or_none()
            if session_type == "direct":
                # Direct Chat history is already the native LangGraph Thread.
                # Loading Session compact/recent rows here would create a second
                # short-term context truth and duplicate the current input.
                session_context = _empty_session_snapshot()
                session_context_version = 0
                pending_messages = ()
                recent_messages = ()
            elif session_type == "group" and agent_id is None:
                # The internal Planning root reads only its dedicated candidate
                # input. It must never receive mutable public Group history.
                session_context = _empty_session_snapshot()
                session_context_version = 0
                pending_messages = ()
                recent_messages = ()
            else:
                if session_type == "group":
                    cutoff = _group_cutoff(
                        normalized_input,
                        source_type=source_type,
                        source_id=source_id,
                        scheduling_position_created_at=(
                            scheduling_position_created_at
                        ),
                        scheduling_position_id=scheduling_position_id,
                    )
                    pack = await self.session_context_service.load_context_pack_through(
                        db,
                        tenant_id=tenant_id,
                        session_id=session_id,
                        cutoff=cutoff,
                    )
                    pack = await self._rebuild_group_context_pack(
                        tenant_id=tenant_id,
                        session_id=session_id,
                        cutoff=cutoff,
                        pack=pack,
                    )
                else:
                    pack = await self.session_context_service.load_context_pack(
                        db,
                        tenant_id=tenant_id,
                        session_id=session_id,
                    )
                session_context = pack.snapshot.to_json()
                session_context_version = pack.snapshot.version
                pending_messages = _json_objects(
                    pack.pending_messages,
                    field="pending_session_messages",
                )
                _validate_session_messages(pending_messages)
                recent_messages = _json_objects(
                    pack.recent_messages,
                    field="recent_session_messages",
                )
                _validate_session_messages(recent_messages)
                group_capture = await self.group_context_builder.capture(
                    db,
                    tenant_id=tenant_id,
                    session_id=session_id,
                    agent_id=agent_id,
                    initial_input=normalized_input,
                    pending_messages=pending_messages,
                    recent_messages=recent_messages,
                )
                normalized_input = _json_object(
                    group_capture.initial_input,
                    field="initial_input",
                )
                pending_messages = _json_objects(
                    group_capture.pending_messages,
                    field="pending_session_messages",
                )
                _validate_session_messages(pending_messages)
                recent_messages = _json_objects(
                    group_capture.recent_messages,
                    field="recent_session_messages",
                )
                _validate_session_messages(recent_messages)

        return RunInputSnapshots(
            session_context=session_context,
            session_context_version=session_context_version,
            recent_session_messages=recent_messages,
            related_run_summaries=normalized_related,
            initial_input=normalized_input,
            pending_session_messages=pending_messages,
        )

    async def build(
        self,
        state: RuntimeGraphState,
        context: RuntimeContext,
        *,
        resume_input: JsonValue | None = None,
        tool_execution_ledger: Ledger | None = None,
        run_message_token_budget: int | None = None,
        token_counter: TokenCounter | None = None,
    ) -> RuntimeContextBuild:
        """Build from the fixed checkpoint snapshot without refreshing the session."""
        snapshots = state["snapshots"]
        session_context = _json_object(
            snapshots.session_context,
            field="session_context_snapshot",
        )
        if snapshots.session_context_version != session_context.get("version"):
            raise ContextBuildError(
                "invalid_session_context_snapshot",
                "checkpoint Session Context version disagrees with its snapshot",
            )

        recent_session_messages = _json_objects(
            snapshots.recent_session_messages,
            field="recent_session_messages_snapshot",
        )
        _validate_session_messages(recent_session_messages)
        related_run_summaries = _json_objects(
            snapshots.related_run_summaries,
            field="related_run_summaries",
        )
        pending_session_messages = _json_objects(
            snapshots.pending_session_messages,
            field="pending_session_messages_snapshot",
        )
        _validate_session_messages(pending_session_messages)

        try:
            thread_messages = runtime_messages_as_json(state)
        except (TypeError, ValueError) as exc:
            raise ContextBuildError(
                "invalid_thread_messages",
                "checkpoint messages must use the LangGraph messages channel",
            ) from exc
        selection = build_recent_tool_safe_window(
            thread_messages,
            tool_execution_ledger,
            target_messages=None,
            token_budget=run_message_token_budget,
            token_counter=token_counter,
        )
        selected_thread_messages = _json_objects(
            selection.messages,
            field="selected_thread_messages",
        )

        raw_summary = state.get("thread_summary")
        thread_summary = (
            None
            if raw_summary is None
            else _json_object(raw_summary, field="thread_running_summary")
        )

        return RuntimeContextBuild(
            session_context_snapshot=session_context,
            current_run=_current_run_section(state, context),
            related_run_summaries=related_run_summaries,
            pending_session_messages_snapshot=pending_session_messages,
            recent_session_messages_snapshot=recent_session_messages,
            thread_running_summary=thread_summary,
            recent_thread_messages=selected_thread_messages,
            initial_input=_json_object(
                snapshots.initial_input,
                field="initial_input",
            ),
            resume_input=_json_value(resume_input, field="resume_input"),
            omitted_tool_exchanges=selection.compaction_summaries,
            retry_model=selection.retry_model,
            blocked=selection.blocked,
            requires_confirmation=selection.requires_confirmation,
        )


__all__ = [
    "ContextBuildError",
    "ContextBuilder",
    "RuntimeContextBuild",
]
