"""Model-backed Session Compact with strict output and deterministic batching."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
import json
import math
from typing import Protocol
import uuid

from sqlalchemy import select

from app.config import Settings, get_settings
from app.models.agent import Agent
from app.models.chat_session import ChatSession
from app.models.llm import LLMModel
from app.services.agent_runtime.command_worker import RuntimeSessionFactory
from app.services.agent_runtime.model_capabilities import (
    ModelCapabilityError,
    ModelCapabilityResolver,
    PlatformModelConfigurationError,
    resolve_multi_agent_compact_model,
)
from app.services.agent_runtime.session_context_completion import (
    SessionCompactRequest,
)
from app.services.agent_runtime.session_context_service import (
    SessionContextCandidate,
    SessionContextSnapshot,
)
from app.services.agent_runtime.state import JsonObject, JsonValue
from app.services.llm.client import LLMMessage
from app.services.llm.single_step import LLMCompletionStep, complete_llm_once
from app.services.llm.utils import get_max_tokens


_COMPACT_TOOL_NAME = "commit_session_context"
_SYSTEM_PROMPT = """You compact one Clawith chat session into durable context.
Preserve confirmed requirements and literal constraints exactly. Merge new facts,
remove only explicitly resolved open items, keep references stable, and produce a
concise summary usable by a later model. Call commit_session_context exactly once.
Do not answer the user and do not propose or execute business tools."""
_COMPACT_TOOL: dict = {
    "type": "function",
    "function": {
        "name": _COMPACT_TOOL_NAME,
        "description": "Commit the complete replacement Session Context candidate.",
        "parameters": {
            "type": "object",
            "properties": {
                "summary": {"type": "string"},
                "requirements": {"type": "array", "items": {}},
                "decisions": {"type": "array", "items": {}},
                "open_items": {"type": "array", "items": {}},
                "evidence_refs": {"type": "array", "items": {}},
                "workspace_refs": {"type": "array", "items": {}},
            },
            "required": [
                "summary",
                "requirements",
                "decisions",
                "open_items",
                "evidence_refs",
                "workspace_refs",
            ],
            "additionalProperties": False,
        },
    },
}
_OUTPUT_FIELDS = frozenset(
    {
        "summary",
        "requirements",
        "decisions",
        "open_items",
        "evidence_refs",
        "workspace_refs",
    }
)


class SessionContextCompactorError(RuntimeError):
    """Session Compact cannot produce a trustworthy candidate."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class CompactModelSelection:
    """The one model allowed for a Session Compact operation."""

    primary: LLMModel
    usage_agent_id: uuid.UUID | None


class CompactCompletionPort(Protocol):
    async def __call__(
        self,
        model: LLMModel,
        messages: list[LLMMessage],
        *,
        tools: list[dict] | None = None,
        agent_id: uuid.UUID | None = None,
        supports_vision: bool = False,
    ) -> LLMCompletionStep: ...


CompactModelResolver = Callable[
    [SessionCompactRequest],
    Awaitable[CompactModelSelection],
]


def _json_value(value: object, *, field: str) -> JsonValue:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise SessionContextCompactorError(
                "invalid_session_compact_output",
                f"{field} contains a non-finite number",
            )
        return value
    if isinstance(value, Mapping):
        copied: dict[str, JsonValue] = {}
        for key, nested in value.items():
            if not isinstance(key, str):
                raise SessionContextCompactorError(
                    "invalid_session_compact_output",
                    f"{field} contains a non-string key",
                )
            copied[key] = _json_value(nested, field=field)
        return copied
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_json_value(nested, field=field) for nested in value]
    raise SessionContextCompactorError(
        "invalid_session_compact_output",
        f"{field} is not JSON serializable",
    )


def _json_array(value: object, *, field: str) -> list[JsonValue]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise SessionContextCompactorError(
            "invalid_session_compact_output",
            f"{field} must be an array",
        )
    return [_json_value(item, field=field) for item in value]


def _estimate_tokens(value: object) -> int:
    serialized = json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)
    return max(1, math.ceil(len(serialized.encode("utf-8")) / 4))


def _snapshot_payload(snapshot: SessionContextSnapshot) -> JsonObject:
    return snapshot.to_json()


def _request_payload(
    snapshot: SessionContextSnapshot,
    messages: Sequence[JsonObject],
    delta: JsonObject | None,
) -> JsonObject:
    return {
        "schema_version": "session_context_v1",
        "current_context": _snapshot_payload(snapshot),
        "new_messages": [dict(message) for message in messages],
        "terminal_delta": dict(delta) if delta is not None else None,
    }


def _messages(payload: JsonObject) -> list[LLMMessage]:
    return [
        LLMMessage(role="system", content=_SYSTEM_PROMPT),
        LLMMessage(
            role="user",
            content=json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        ),
    ]


def _call_name(call: Mapping[str, object]) -> str | None:
    function = call.get("function")
    if isinstance(function, Mapping) and isinstance(function.get("name"), str):
        return str(function["name"])
    name = call.get("name")
    return str(name) if isinstance(name, str) else None


def _call_arguments(call: Mapping[str, object]) -> Mapping[str, object]:
    function = call.get("function")
    raw = function.get("arguments") if isinstance(function, Mapping) else call.get("arguments")
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise SessionContextCompactorError(
                "invalid_session_compact_output",
                "compact tool arguments are not valid JSON",
            ) from exc
    else:
        parsed = raw
    if not isinstance(parsed, Mapping):
        raise SessionContextCompactorError(
            "invalid_session_compact_output",
            "compact tool arguments must be an object",
        )
    return parsed


def _candidate_from_step(
    step: LLMCompletionStep,
    *,
    watermark: uuid.UUID | None,
) -> SessionContextCandidate:
    if len(step.tool_calls) != 1 or _call_name(step.tool_calls[0]) != _COMPACT_TOOL_NAME:
        raise SessionContextCompactorError(
            "invalid_session_compact_output",
            "compact model must call commit_session_context exactly once",
        )
    arguments = _call_arguments(step.tool_calls[0])
    if set(arguments) != _OUTPUT_FIELDS:
        raise SessionContextCompactorError(
            "invalid_session_compact_output",
            "compact output fields do not match session_context_v1",
        )
    summary = arguments.get("summary")
    if not isinstance(summary, str) or not summary.strip():
        raise SessionContextCompactorError(
            "invalid_session_compact_output",
            "compact summary must be a non-empty string",
        )
    return SessionContextCandidate(
        summary=summary.strip(),
        requirements=_json_array(arguments.get("requirements"), field="requirements"),
        decisions=_json_array(arguments.get("decisions"), field="decisions"),
        open_items=_json_array(arguments.get("open_items"), field="open_items"),
        evidence_refs=_json_array(arguments.get("evidence_refs"), field="evidence_refs"),
        workspace_refs=_json_array(arguments.get("workspace_refs"), field="workspace_refs"),
        covered_through_message_id=watermark,
    )


def _snapshot_from_candidate(
    candidate: SessionContextCandidate,
    *,
    version: int,
) -> SessionContextSnapshot:
    return SessionContextSnapshot(
        version=version,
        summary=candidate.summary,
        requirements=tuple(candidate.requirements),
        decisions=tuple(candidate.decisions),
        open_items=tuple(candidate.open_items),
        evidence_refs=tuple(candidate.evidence_refs),
        workspace_refs=tuple(candidate.workspace_refs),
        covered_through_message_id=candidate.covered_through_message_id,
    )


class LLMSessionContextCompactor:
    """Compact a direct or shared session without mutating either state source."""

    def __init__(
        self,
        *,
        session_factory: RuntimeSessionFactory,
        settings: Settings | None = None,
        completion: CompactCompletionPort = complete_llm_once,
        model_resolver: CompactModelResolver | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._settings = settings or get_settings()
        self._completion = completion
        self._model_resolver = model_resolver or self._resolve_models

    @staticmethod
    def _usable_model(
        model: LLMModel | None,
        *,
        tenant_id: uuid.UUID,
    ) -> LLMModel | None:
        if model is None or not model.enabled:
            return None
        if model.tenant_id not in {None, tenant_id}:
            return None
        return model

    async def _resolve_models(
        self,
        request: SessionCompactRequest,
    ) -> CompactModelSelection:
        async with self._session_factory() as db:
            session_result = await db.execute(
                select(ChatSession).where(
                    ChatSession.tenant_id == request.tenant_id,
                    ChatSession.id == request.session_id,
                    ChatSession.deleted_at.is_(None),
                )
            )
            session = session_result.scalar_one_or_none()
            if session is None:
                raise SessionContextCompactorError(
                    "session_context_unavailable",
                    "Session Compact target no longer exists",
                )
            if session.session_type == "group":
                model = await resolve_multi_agent_compact_model(
                    db,
                    self._settings,
                    tenant_id=request.tenant_id,
                )
                return CompactModelSelection(
                    primary=model,
                    usage_agent_id=None,
                )

            if session.agent_id is None or session.agent_id != request.source_agent_id:
                raise SessionContextCompactorError(
                    "session_context_agent_mismatch",
                    "direct Session Compact source Agent does not match the session",
                )
            agent_result = await db.execute(
                select(Agent).where(
                    Agent.id == session.agent_id,
                    Agent.tenant_id == request.tenant_id,
                )
            )
            agent = agent_result.scalar_one_or_none()
            if agent is None or agent.primary_model_id is None:
                raise SessionContextCompactorError(
                    "session_compact_model_unavailable",
                    "Session Agent has no current primary model",
                )
            primary_result = await db.execute(
                select(LLMModel).where(LLMModel.id == agent.primary_model_id)
            )
            primary = self._usable_model(
                primary_result.scalar_one_or_none(),
                tenant_id=request.tenant_id,
            )
            if primary is None:
                raise SessionContextCompactorError(
                    "session_compact_model_unavailable",
                    "Session Agent primary model is not usable",
                )
            return CompactModelSelection(
                primary=primary,
                usage_agent_id=agent.id,
            )

    def _budget(self, model: LLMModel):
        requested_output = get_max_tokens(
            model.provider,
            model.model,
            model.max_output_tokens,
        )
        return ModelCapabilityResolver.runtime_budget(
            model,
            requested_max_output_tokens=requested_output,
            static_prompt_tokens=_estimate_tokens(_SYSTEM_PROMPT),
            tool_schema_tokens=_estimate_tokens(_COMPACT_TOOL),
            reserved_runtime_tokens=128,
            safety_margin_tokens=256,
            compact_threshold_ratio=self._settings.AGENT_RUNTIME_SUMMARY_THRESHOLD_RATIO,
        )

    async def _complete_batch(
        self,
        *,
        model: LLMModel,
        usage_agent_id: uuid.UUID | None,
        payload: JsonObject,
        watermark: uuid.UUID | None,
    ) -> SessionContextCandidate:
        step = await self._completion(
            model,
            _messages(payload),
            tools=[_COMPACT_TOOL],
            agent_id=usage_agent_id,
            supports_vision=False,
        )
        return _candidate_from_step(step, watermark=watermark)

    async def _compact_with_model(
        self,
        request: SessionCompactRequest,
        *,
        model: LLMModel,
        usage_agent_id: uuid.UUID | None,
    ) -> SessionContextCandidate:
        budget = self._budget(model)
        current = request.snapshot
        remaining = list(request.messages)
        delta: JsonObject | None = (
            request.delta.to_json() if request.delta is not None else None
        )
        candidate: SessionContextCandidate | None = None

        while remaining or candidate is None:
            batch: list[JsonObject] = []
            base_payload = _request_payload(current, batch, delta)
            if _estimate_tokens(base_payload) > budget.compact_threshold:
                raise SessionContextCompactorError(
                    "session_compact_input_too_large",
                    "Session Context and terminal delta do not fit the compact model",
                )
            while remaining:
                proposed = [*batch, remaining[0]]
                payload = _request_payload(current, proposed, delta)
                if _estimate_tokens(payload) > budget.compact_threshold:
                    break
                batch.append(remaining.pop(0))
            if remaining and not batch:
                raise SessionContextCompactorError(
                    "session_compact_message_too_large",
                    "one complete ChatMessage does not fit the compact model",
                )

            watermark = current.covered_through_message_id
            if batch:
                raw_message_id = batch[-1].get("id")
                if not isinstance(raw_message_id, str):
                    raise SessionContextCompactorError(
                        "invalid_session_compact_input",
                        "Session Compact message has no stable ID",
                    )
                try:
                    watermark = uuid.UUID(raw_message_id)
                except ValueError as exc:
                    raise SessionContextCompactorError(
                        "invalid_session_compact_input",
                        "Session Compact message ID is not a UUID",
                    ) from exc
            payload = _request_payload(current, batch, delta)
            candidate = await self._complete_batch(
                model=model,
                usage_agent_id=usage_agent_id,
                payload=payload,
                watermark=watermark,
            )
            current = _snapshot_from_candidate(
                candidate,
                version=request.snapshot.version,
            )
            delta = None
        return candidate

    async def compact(self, request: SessionCompactRequest) -> SessionContextCandidate:
        try:
            selection = await self._model_resolver(request)
            return await self._compact_with_model(
                request,
                model=selection.primary,
                usage_agent_id=selection.usage_agent_id,
            )
        except (SessionContextCompactorError, ModelCapabilityError):
            raise
        except PlatformModelConfigurationError as exc:
            raise SessionContextCompactorError(
                "session_compact_model_unavailable",
                str(exc),
            ) from exc
        except Exception as exc:
            raise SessionContextCompactorError(
                "session_compact_model_failed",
                "Session Compact model failed; the previous Session Context remains active",
            ) from exc


__all__ = [
    "CompactModelSelection",
    "LLMSessionContextCompactor",
    "SessionContextCompactorError",
]
