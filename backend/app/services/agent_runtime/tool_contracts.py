"""Checkpoint-safe Tool Workset and accepted-call contracts.

These values contain execution routing facts, never live clients, callables, or
decrypted credentials.  The LangGraph checkpoint owns them because they decide
how an already accepted Tool Call resumes.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Literal, cast

from app.services.agent_runtime.state import JsonObject, JsonValue

ToolBindingKind = Literal["builtin", "mcp", "group", "a2a", "agentbay", "legacy"]
ToolEffect = Literal["read", "write", "external_write"]
ToolRetryPolicy = Literal["safe", "conditional", "never"]
ToolCancelCapability = Literal["cooperative", "stop_waiting_only"]

STEP_TOOL_CONTEXT_VERSION = 1
MAX_TOOL_CONTEXT_BYTES = 256 * 1024
MAX_TOOL_SCHEMA_BYTES = 64 * 1024
MAX_TOOL_BINDING_BYTES = 16 * 1024
MAX_ID_LENGTH = 255
MAX_TOOL_NAME_LENGTH = 200

_SENSITIVE_KEYS = {
    "access_token",
    "api_key",
    "apikey",
    "authorization",
    "bearer",
    "client_secret",
    "cookie",
    "password",
    "private_key",
    "refresh_token",
    "secret",
    "token",
}


class ToolContractError(ValueError):
    """A checkpoint Tool contract is missing, malformed, or unsafe."""


@dataclass(frozen=True, slots=True)
class ToolDeadlinePolicy:
    name: str
    default_seconds: float
    max_seconds: float
    cancel_capability: ToolCancelCapability

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name.strip():
            raise ToolContractError("deadline policy name must be non-empty text")
        if self.default_seconds <= 0 or self.max_seconds < self.default_seconds:
            raise ToolContractError("deadline policy bounds are invalid")


_DEADLINE_POLICIES = {
    "runtime_default": ToolDeadlinePolicy(
        "runtime_default", 60.0, 300.0, "stop_waiting_only"
    ),
    "network_read": ToolDeadlinePolicy(
        "network_read", 30.0, 60.0, "stop_waiting_only"
    ),
    "local_code": ToolDeadlinePolicy(
        "local_code", 30.0, 3600.0, "cooperative"
    ),
    "agentbay_read": ToolDeadlinePolicy(
        "agentbay_read", 30.0, 60.0, "stop_waiting_only"
    ),
    "agentbay_code": ToolDeadlinePolicy(
        "agentbay_code", 30.0, 300.0, "stop_waiting_only"
    ),
}


def deadline_policy_for_tool(tool_name: str) -> ToolDeadlinePolicy:
    if tool_name in {"execute_code", "execute_code_e2b"}:
        return _DEADLINE_POLICIES["local_code"]
    if tool_name == "agentbay_code_execute":
        return _DEADLINE_POLICIES["agentbay_code"]
    if tool_name in {
        "agentbay_code_read_file",
        "agentbay_browser_extract",
        "agentbay_browser_observe",
    }:
        return _DEADLINE_POLICIES["agentbay_read"]
    if tool_name in {"read_emails", "read_webpage", "jina_read"}:
        return _DEADLINE_POLICIES["network_read"]
    return _DEADLINE_POLICIES["runtime_default"]


def resolve_tool_deadline_seconds(
    policy_name: str,
    requested_seconds: object = None,
) -> float:
    policy = _DEADLINE_POLICIES.get(policy_name)
    if policy is None:
        raise ToolContractError(f"unknown deadline policy {policy_name!r}")
    if requested_seconds is None:
        return policy.default_seconds
    if (
        isinstance(requested_seconds, bool)
        or not isinstance(requested_seconds, (int, float))
        or requested_seconds <= 0
    ):
        raise ToolContractError("requested Tool deadline must be positive")
    return min(float(requested_seconds), policy.max_seconds)


def tool_cancel_capability(policy_name: str) -> ToolCancelCapability:
    policy = _DEADLINE_POLICIES.get(policy_name)
    if policy is None:
        raise ToolContractError(f"unknown deadline policy {policy_name!r}")
    return policy.cancel_capability


def _required_text(value: object, *, field_name: str, max_length: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ToolContractError(f"{field_name} must be non-empty text")
    normalized = value.strip()
    if len(normalized) > max_length:
        raise ToolContractError(f"{field_name} exceeds its length limit")
    return normalized


def _optional_text(value: object, *, field_name: str, max_length: int) -> str | None:
    if value is None:
        return None
    return _required_text(value, field_name=field_name, max_length=max_length)


def _json_object(value: object, *, field_name: str) -> JsonObject:
    if not isinstance(value, Mapping):
        raise ToolContractError(f"{field_name} must be one JSON object")
    try:
        copied = json.loads(json.dumps(value, ensure_ascii=False))
    except (TypeError, ValueError) as exc:
        raise ToolContractError(f"{field_name} must be JSON serializable") from exc
    if not isinstance(copied, dict):
        raise ToolContractError(f"{field_name} must be one JSON object")
    return cast(JsonObject, copied)


def _json_size(value: object) -> int:
    try:
        return len(
            json.dumps(
                value,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        )
    except (TypeError, ValueError) as exc:
        raise ToolContractError("Tool contract must be JSON serializable") from exc


def _contains_secret(value: JsonValue) -> bool:
    if isinstance(value, dict):
        for raw_key, child in value.items():
            key = raw_key.strip().lower().replace("-", "_")
            if key in _SENSITIVE_KEYS:
                return True
            if _contains_secret(child):
                return True
    elif isinstance(value, list):
        return any(_contains_secret(child) for child in value)
    return False


@dataclass(frozen=True, slots=True)
class ToolExecutionBinding:
    """Secret-free stable route for an accepted Tool Call."""

    kind: ToolBindingKind
    handler_key: str
    target: JsonObject = field(default_factory=dict)
    credential_ref: str | None = None

    def __post_init__(self) -> None:
        if self.kind not in {"builtin", "mcp", "group", "a2a", "agentbay", "legacy"}:
            raise ToolContractError("binding kind is unsupported")
        object.__setattr__(
            self,
            "handler_key",
            _required_text(
                self.handler_key,
                field_name="binding.handler_key",
                max_length=MAX_TOOL_NAME_LENGTH,
            ),
        )
        target = _json_object(self.target, field_name="binding.target")
        if _json_size(target) > MAX_TOOL_BINDING_BYTES:
            raise ToolContractError("binding target exceeds its size limit")
        if _contains_secret(target):
            raise ToolContractError("binding target contains secret material")
        object.__setattr__(self, "target", target)
        object.__setattr__(
            self,
            "credential_ref",
            _optional_text(
                self.credential_ref,
                field_name="binding.credential_ref",
                max_length=MAX_ID_LENGTH,
            ),
        )

    def to_json(self) -> JsonObject:
        return {
            "kind": self.kind,
            "handler_key": self.handler_key,
            "target": dict(self.target),
            "credential_ref": self.credential_ref,
        }

    @classmethod
    def from_json(cls, value: object) -> ToolExecutionBinding:
        payload = _json_object(value, field_name="binding")
        return cls(
            kind=cast(ToolBindingKind, payload.get("kind")),
            handler_key=cast(str, payload.get("handler_key")),
            target=_json_object(payload.get("target", {}), field_name="binding.target"),
            credential_ref=cast(str | None, payload.get("credential_ref")),
        )


@dataclass(frozen=True, slots=True)
class ToolWorksetEntry:
    """One model-visible Tool definition joined to its execution contract."""

    tool_name: str
    contract_version: str
    parameters_schema: JsonObject
    binding: ToolExecutionBinding
    effect: ToolEffect
    retry_policy: ToolRetryPolicy
    authorization_policy: str = "runtime_default"
    deadline_policy: str = "runtime_default"
    recovery_policy: str = "runtime_default"

    def __post_init__(self) -> None:
        tool_name = _required_text(
            self.tool_name,
            field_name="tool_name",
            max_length=MAX_TOOL_NAME_LENGTH,
        )
        object.__setattr__(self, "tool_name", tool_name)
        object.__setattr__(
            self,
            "contract_version",
            _required_text(
                self.contract_version,
                field_name="contract_version",
                max_length=MAX_ID_LENGTH,
            ),
        )
        schema = _json_object(self.parameters_schema, field_name="parameters_schema")
        if _json_size(schema) > MAX_TOOL_SCHEMA_BYTES:
            raise ToolContractError("parameters schema exceeds its size limit")
        object.__setattr__(self, "parameters_schema", schema)
        if self.effect not in {"read", "write", "external_write"}:
            raise ToolContractError("Tool effect is unsupported")
        if self.retry_policy not in {"safe", "conditional", "never"}:
            raise ToolContractError("Tool retry policy is unsupported")
        for field_name in (
            "authorization_policy",
            "deadline_policy",
            "recovery_policy",
        ):
            object.__setattr__(
                self,
                field_name,
                _required_text(
                    getattr(self, field_name),
                    field_name=field_name,
                    max_length=MAX_ID_LENGTH,
                ),
            )
        if self.deadline_policy not in _DEADLINE_POLICIES:
            raise ToolContractError("Tool deadline policy is unsupported")
        if self.binding.kind == "builtin" and self.binding.handler_key != tool_name:
            raise ToolContractError("builtin binding must match the Tool name")

    def to_json(self) -> JsonObject:
        return {
            "tool_name": self.tool_name,
            "contract_version": self.contract_version,
            "parameters_schema": dict(self.parameters_schema),
            "binding": self.binding.to_json(),
            "effect": self.effect,
            "retry_policy": self.retry_policy,
            "authorization_policy": self.authorization_policy,
            "deadline_policy": self.deadline_policy,
            "recovery_policy": self.recovery_policy,
        }

    @classmethod
    def from_json(cls, value: object) -> ToolWorksetEntry:
        payload = _json_object(value, field_name="workset entry")
        return cls(
            tool_name=cast(str, payload.get("tool_name")),
            contract_version=cast(str, payload.get("contract_version")),
            parameters_schema=_json_object(
                payload.get("parameters_schema"),
                field_name="parameters_schema",
            ),
            binding=ToolExecutionBinding.from_json(payload.get("binding")),
            effect=cast(ToolEffect, payload.get("effect")),
            retry_policy=cast(ToolRetryPolicy, payload.get("retry_policy")),
            authorization_policy=cast(
                str,
                payload.get("authorization_policy", "runtime_default"),
            ),
            deadline_policy=cast(
                str,
                payload.get("deadline_policy", "runtime_default"),
            ),
            recovery_policy=cast(
                str,
                payload.get("recovery_policy", "runtime_default"),
            ),
        )


@dataclass(frozen=True, slots=True)
class AcceptedToolCall:
    """One accepted assistant Tool Call and its frozen Workset entry."""

    call_instance_id: str
    provider_call_id: str | None
    entry: ToolWorksetEntry

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "call_instance_id",
            _required_text(
                self.call_instance_id,
                field_name="call_instance_id",
                max_length=MAX_ID_LENGTH,
            ),
        )
        object.__setattr__(
            self,
            "provider_call_id",
            _optional_text(
                self.provider_call_id,
                field_name="provider_call_id",
                max_length=MAX_ID_LENGTH,
            ),
        )

    def to_json(self) -> JsonObject:
        return {
            "call_instance_id": self.call_instance_id,
            "provider_call_id": self.provider_call_id,
            **self.entry.to_json(),
        }

    @classmethod
    def from_json(cls, value: object) -> AcceptedToolCall:
        payload = _json_object(value, field_name="accepted call")
        return cls(
            call_instance_id=cast(str, payload.get("call_instance_id")),
            provider_call_id=cast(str | None, payload.get("provider_call_id")),
            entry=ToolWorksetEntry.from_json(payload),
        )


def workset_version(entries: tuple[ToolWorksetEntry, ...]) -> str:
    """Return an order-independent digest of the executable Workset contract."""
    names = [entry.tool_name for entry in entries]
    if len(set(names)) != len(names):
        raise ToolContractError("Workset contains duplicate Tool names")
    payload = [entry.to_json() for entry in sorted(entries, key=lambda item: item.tool_name)]
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


@dataclass(frozen=True, slots=True)
class StepToolContext:
    """The exact Tool contract accepted for one Assistant message."""

    assistant_message_id: str
    model_step: int
    workset_version: str
    accepted_calls: tuple[AcceptedToolCall, ...]
    legacy_resolved: bool = False
    version: int = STEP_TOOL_CONTEXT_VERSION

    def __post_init__(self) -> None:
        if self.version != STEP_TOOL_CONTEXT_VERSION:
            raise ToolContractError("Step Tool Context version is unsupported")
        if not isinstance(self.legacy_resolved, bool):
            raise ToolContractError("legacy_resolved must be a boolean")
        object.__setattr__(
            self,
            "assistant_message_id",
            _required_text(
                self.assistant_message_id,
                field_name="assistant_message_id",
                max_length=MAX_ID_LENGTH,
            ),
        )
        if isinstance(self.model_step, bool) or self.model_step <= 0:
            raise ToolContractError("model_step must be a positive integer")
        object.__setattr__(
            self,
            "workset_version",
            _required_text(
                self.workset_version,
                field_name="workset_version",
                max_length=MAX_ID_LENGTH,
            ),
        )
        calls = tuple(self.accepted_calls)
        call_ids = [call.call_instance_id for call in calls]
        if len(call_ids) != len(set(call_ids)):
            raise ToolContractError("Step Tool Context contains duplicate Call Instances")
        provider_ids = [call.provider_call_id for call in calls if call.provider_call_id]
        if len(provider_ids) != len(set(provider_ids)):
            raise ToolContractError("Step Tool Context contains duplicate Provider Call IDs")
        object.__setattr__(self, "accepted_calls", calls)
        if _json_size(self.to_json()) > MAX_TOOL_CONTEXT_BYTES:
            raise ToolContractError("Step Tool Context exceeds its size limit")

    def to_json(self) -> JsonObject:
        return {
            "version": self.version,
            "assistant_message_id": self.assistant_message_id,
            "model_step": self.model_step,
            "workset_version": self.workset_version,
            "accepted_calls": [call.to_json() for call in self.accepted_calls],
            "legacy_resolved": self.legacy_resolved,
        }

    def accepted_call(self, call_instance_id: str) -> AcceptedToolCall:
        matches = [
            call
            for call in self.accepted_calls
            if call.call_instance_id == call_instance_id
        ]
        if len(matches) != 1:
            raise ToolContractError("Call Instance is missing from Step Tool Context")
        return matches[0]

    @classmethod
    def from_json(cls, value: object) -> StepToolContext:
        payload = _json_object(value, field_name="step_tool_context")
        raw_calls = payload.get("accepted_calls")
        if not isinstance(raw_calls, list):
            raise ToolContractError("accepted_calls must be an array")
        return cls(
            version=cast(int, payload.get("version")),
            assistant_message_id=cast(str, payload.get("assistant_message_id")),
            model_step=cast(int, payload.get("model_step")),
            workset_version=cast(str, payload.get("workset_version")),
            accepted_calls=tuple(AcceptedToolCall.from_json(call) for call in raw_calls),
            legacy_resolved=cast(bool, payload.get("legacy_resolved", False)),
        )


def parse_step_tool_context(
    value: object,
    *,
    allow_legacy_missing: bool = False,
) -> StepToolContext | None:
    """Decode one checkpoint context without silently upgrading corruption."""
    if value is None:
        if allow_legacy_missing:
            return None
        raise ToolContractError("Step Tool Context is missing")
    return StepToolContext.from_json(value)


__all__ = [
    "AcceptedToolCall",
    "StepToolContext",
    "ToolCancelCapability",
    "ToolContractError",
    "ToolDeadlinePolicy",
    "ToolExecutionBinding",
    "ToolWorksetEntry",
    "deadline_policy_for_tool",
    "parse_step_tool_context",
    "resolve_tool_deadline_seconds",
    "tool_cancel_capability",
    "workset_version",
]
