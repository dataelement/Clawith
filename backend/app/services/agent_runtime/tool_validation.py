"""Deterministic validation against the schema accepted by one Model Step."""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass

from app.services.agent_runtime.state import JsonObject

MAX_VALIDATION_ISSUES = 20
MAX_VALIDATION_PATH_LENGTH = 240


class ToolValidationContractError(ValueError):
    """The accepted Tool schema is malformed or unsupported."""


@dataclass(frozen=True, slots=True)
class ToolValidationIssue:
    """One bounded, value-free argument problem safe to show the model."""

    code: str
    path: str
    summary: str


def _path(parent: str, child: str) -> str:
    combined = f"{parent}.{child}" if parent != "$" else f"$.{child}"
    return combined[:MAX_VALIDATION_PATH_LENGTH]


def _issue(code: str, path: str, summary: str) -> ToolValidationIssue:
    return ToolValidationIssue(code=code, path=path, summary=summary[:300])


def _matches_type(value: object, expected: str) -> bool:
    if expected == "object":
        return isinstance(value, Mapping)
    if expected == "array":
        return isinstance(value, list)
    if expected == "string":
        return isinstance(value, str)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return (
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and (not isinstance(value, float) or math.isfinite(value))
        )
    if expected == "null":
        return value is None
    raise ToolValidationContractError(f"unsupported schema type {expected!r}")


def _schema_object(value: object, *, field_name: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ToolValidationContractError(f"{field_name} must be an object")
    if any(not isinstance(key, str) for key in value):
        raise ToolValidationContractError(f"{field_name} keys must be strings")
    return value


def _validate(
    value: object,
    schema: Mapping[str, object],
    *,
    path: str,
    issues: list[ToolValidationIssue],
) -> None:
    if len(issues) >= MAX_VALIDATION_ISSUES:
        return
    raw_type = schema.get("type")
    expected_types: tuple[str, ...]
    if raw_type is None:
        expected_types = ()
    elif isinstance(raw_type, str):
        expected_types = (raw_type,)
    elif isinstance(raw_type, list) and raw_type and all(
        isinstance(item, str) for item in raw_type
    ):
        expected_types = tuple(raw_type)
    else:
        raise ToolValidationContractError("schema type must be text or an array of text")
    if expected_types and not any(_matches_type(value, item) for item in expected_types):
        issues.append(
            _issue(
                "type",
                path,
                f"{path} must have type {' or '.join(expected_types)}.",
            )
        )
        return

    enum = schema.get("enum")
    if enum is not None:
        if not isinstance(enum, list) or not enum:
            raise ToolValidationContractError("schema enum must be a non-empty array")
        if value not in enum:
            issues.append(_issue("enum", path, f"{path} must use one allowed value."))

    if isinstance(value, Mapping):
        raw_properties = schema.get("properties", {})
        properties = _schema_object(raw_properties, field_name="schema properties")
        raw_required = schema.get("required", [])
        if not isinstance(raw_required, list) or any(
            not isinstance(item, str) for item in raw_required
        ):
            raise ToolValidationContractError("schema required must be an array of text")
        for required_name in raw_required:
            if required_name not in value:
                missing_path = _path(path, required_name)
                issues.append(
                    _issue(
                        "required",
                        missing_path,
                        f"{missing_path} is required.",
                    )
                )
                if len(issues) >= MAX_VALIDATION_ISSUES:
                    return
        for property_name, property_schema in properties.items():
            if property_name not in value:
                continue
            child_schema = _schema_object(
                property_schema,
                field_name=f"schema property {property_name}",
            )
            _validate(
                value[property_name],
                child_schema,
                path=_path(path, property_name),
                issues=issues,
            )
        additional = schema.get("additionalProperties", True)
        if not isinstance(additional, (bool, Mapping)):
            raise ToolValidationContractError(
                "schema additionalProperties must be a boolean or object"
            )
        for property_name in value:
            if property_name in properties:
                continue
            child_path = _path(path, str(property_name))
            if additional is False:
                issues.append(
                    _issue(
                        "additional_property",
                        child_path,
                        f"{child_path} is not an accepted argument.",
                    )
                )
            elif isinstance(additional, Mapping):
                _validate(
                    value[property_name],
                    _schema_object(
                        additional,
                        field_name="schema additionalProperties",
                    ),
                    path=child_path,
                    issues=issues,
                )
            if len(issues) >= MAX_VALIDATION_ISSUES:
                return

    if isinstance(value, list) and "items" in schema:
        item_schema = _schema_object(schema["items"], field_name="schema items")
        for index, item in enumerate(value):
            _validate(item, item_schema, path=f"{path}[{index}]", issues=issues)
            if len(issues) >= MAX_VALIDATION_ISSUES:
                return

    alternatives = schema.get("anyOf")
    if alternatives is not None:
        if not isinstance(alternatives, list) or not alternatives:
            raise ToolValidationContractError("schema anyOf must be a non-empty array")
        matched = False
        for alternative in alternatives:
            candidate_issues: list[ToolValidationIssue] = []
            _validate(
                value,
                _schema_object(alternative, field_name="schema anyOf entry"),
                path=path,
                issues=candidate_issues,
            )
            if not candidate_issues:
                matched = True
                break
        if not matched:
            issues.append(
                _issue(
                    "any_of",
                    path,
                    f"{path} must satisfy one accepted argument shape.",
                )
            )


def validate_tool_arguments(
    arguments: JsonObject,
    parameters_schema: JsonObject,
) -> tuple[ToolValidationIssue, ...]:
    """Return deterministic, bounded issues without echoing argument values."""
    if not isinstance(arguments, dict):
        return (_issue("type", "$", "$ must have type object."),)
    issues: list[ToolValidationIssue] = []
    _validate(
        arguments,
        _schema_object(parameters_schema, field_name="parameters schema"),
        path="$",
        issues=issues,
    )
    return tuple(issues[:MAX_VALIDATION_ISSUES])


__all__ = [
    "ToolValidationContractError",
    "ToolValidationIssue",
    "validate_tool_arguments",
]
