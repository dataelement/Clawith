"""Incremental complete-contract registry for Durable Runtime tools.

The registry is intentionally additive. Existing typed adapters remain on the
legacy compatibility path until their whole execution contract is migrated.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
from typing import cast

from app.services.agent_runtime.state import JsonObject
from app.services.agent_runtime.tool_contracts import (
    ToolBindingKind,
    ToolCancelCapability,
    ToolContractError,
    ToolEffect,
    ToolExecutionBinding,
    ToolRetryPolicy,
    ToolWorksetEntry,
    tool_cancel_capability,
)
from app.services.builtin_tool_definitions import (
    builtin_model_definition,
    builtin_policy,
    is_reserved_custom_tool_name,
)


def _function_contract(model_definition: Mapping[str, object]) -> tuple[str, JsonObject]:
    function = model_definition.get("function")
    if not isinstance(function, Mapping):
        raise ToolContractError("Registered Tool requires a function definition")
    name = function.get("name")
    schema = function.get("parameters")
    if not isinstance(name, str) or not name.strip():
        raise ToolContractError("Registered Tool requires a non-empty name")
    if not isinstance(schema, Mapping):
        raise ToolContractError("Registered Tool requires an object schema")
    return name.strip(), cast(JsonObject, deepcopy(dict(schema)))


@dataclass(frozen=True, slots=True)
class RegisteredTool:
    """One Tool may enter the new Workset only when every policy is explicit."""

    model_definition: JsonObject
    binding_kind: ToolBindingKind
    handler_key: str
    effect: ToolEffect
    retry_policy: ToolRetryPolicy
    authorization_policy: str
    recovery_policy: str
    deadline_policy: str
    cancel_capability: ToolCancelCapability
    contract_version: str

    def __post_init__(self) -> None:
        name, schema = _function_contract(self.model_definition)
        if not self.handler_key.strip():
            raise ToolContractError("Registered Tool requires a handler binding")
        if not self.authorization_policy.strip():
            raise ToolContractError("Registered Tool requires authorization policy")
        if not self.recovery_policy.strip():
            raise ToolContractError("Registered Tool requires recovery policy")
        if not self.contract_version.strip():
            raise ToolContractError("Registered Tool requires a contract version")
        if tool_cancel_capability(self.deadline_policy) != self.cancel_capability:
            raise ToolContractError(
                "Registered Tool cancel capability conflicts with deadline policy"
            )
        # Reuse the checkpoint contract as the final completeness and size gate.
        self.to_workset_entry(name=name, schema=schema)

    @property
    def tool_name(self) -> str:
        return _function_contract(self.model_definition)[0]

    def to_workset_entry(
        self,
        *,
        name: str | None = None,
        schema: JsonObject | None = None,
    ) -> ToolWorksetEntry:
        resolved_name, resolved_schema = _function_contract(self.model_definition)
        return ToolWorksetEntry(
            tool_name=name or resolved_name,
            contract_version=self.contract_version,
            parameters_schema=schema or resolved_schema,
            binding=ToolExecutionBinding(
                kind=self.binding_kind,
                handler_key=self.handler_key,
            ),
            effect=self.effect,
            retry_policy=self.retry_policy,
            authorization_policy=self.authorization_policy,
            deadline_policy=self.deadline_policy,
            recovery_policy=self.recovery_policy,
        )


def _version(name: str, schema: Mapping[str, object], binding_kind: str) -> str:
    encoded = json.dumps(
        {"name": name, "schema": schema, "binding_kind": binding_kind},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"registered:{name}:{hashlib.sha256(encoded).hexdigest()[:16]}"


def _registered_builtin(name: str, *, binding_kind: ToolBindingKind) -> RegisteredTool:
    definition = builtin_model_definition(name)
    if definition is None:  # pragma: no cover - import-time invariant
        raise ToolContractError(f"Registered builtin {name!r} has no model definition")
    function = cast(JsonObject, deepcopy(definition))
    _, schema = _function_contract(function)
    policy = builtin_policy(name)
    deadline_policy = (
        "agentbay_read" if name == "agentbay_code_read_file" else "runtime_default"
    )
    return RegisteredTool(
        model_definition=function,
        binding_kind=binding_kind,
        handler_key=name,
        effect=cast(ToolEffect, policy["effect"]),
        retry_policy=cast(ToolRetryPolicy, policy["retry_policy"]),
        authorization_policy="runtime_default",
        recovery_policy="runtime_default",
        deadline_policy=deadline_policy,
        cancel_capability=tool_cancel_capability(deadline_policy),
        contract_version=_version(name, schema, binding_kind),
    )


_STATIC_REGISTRY = {
    "read_file": _registered_builtin("read_file", binding_kind="builtin"),
    "agentbay_code_read_file": _registered_builtin(
        "agentbay_code_read_file",
        binding_kind="agentbay",
    ),
}
STATIC_REGISTERED_TOOL_NAMES = frozenset(_STATIC_REGISTRY)


def registered_tool(name: str) -> RegisteredTool | None:
    return _STATIC_REGISTRY.get(name)


def registered_dynamic_mcp(model_definition: Mapping[str, object]) -> RegisteredTool:
    name, schema = _function_contract(model_definition)
    definition = cast(JsonObject, deepcopy(dict(model_definition)))
    return RegisteredTool(
        model_definition=definition,
        binding_kind="mcp",
        handler_key=name,
        effect="external_write",
        retry_policy="never",
        authorization_policy="runtime_default",
        recovery_policy="mcp_receipt_or_reconcile",
        deadline_policy="runtime_default",
        cancel_capability="stop_waiting_only",
        contract_version=_version(name, schema, "mcp"),
    )


def resolve_registered_tool(
    model_definition: Mapping[str, object],
    *,
    dynamic_mcp_names: set[str] | frozenset[str] = frozenset(),
) -> RegisteredTool | None:
    """Resolve only exact complete contracts; malformed candidates stay hidden."""
    try:
        name, schema = _function_contract(model_definition)
        static = registered_tool(name)
        if static is not None:
            static_schema = static.to_workset_entry().parameters_schema
            return static if schema == static_schema else None
        if name in dynamic_mcp_names and not is_reserved_custom_tool_name(name):
            return registered_dynamic_mcp(model_definition)
    except ToolContractError:
        return None
    return None


__all__ = [
    "STATIC_REGISTERED_TOOL_NAMES",
    "RegisteredTool",
    "registered_dynamic_mcp",
    "registered_tool",
    "resolve_registered_tool",
]
