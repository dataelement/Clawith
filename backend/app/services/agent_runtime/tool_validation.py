"""Deterministic validation against the schema accepted by one Model Step."""

from __future__ import annotations

import math
import re
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from urllib.parse import urlparse

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


def _positive_integer(value: object, *, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ToolValidationContractError(f"{field_name} must be a non-negative integer")
    return value


def _number(value: object, *, field_name: str) -> int | float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
    ):
        raise ToolValidationContractError(f"{field_name} must be a finite number")
    return value


def _matching_subschema(
    value: object,
    schema: object,
    *,
    field_name: str,
    path: str,
) -> tuple[bool, list[ToolValidationIssue]]:
    candidate_issues: list[ToolValidationIssue] = []
    _validate(
        value,
        _schema_object(schema, field_name=field_name),
        path=path,
        issues=candidate_issues,
    )
    return not candidate_issues, candidate_issues


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

    if "const" in schema and value != schema["const"]:
        issues.append(_issue("const", path, f"{path} must use the required value."))

    if isinstance(value, str):
        if "minLength" in schema:
            minimum_length = _positive_integer(
                schema["minLength"], field_name="schema minLength"
            )
            if len(value) < minimum_length:
                issues.append(
                    _issue(
                        "min_length",
                        path,
                        f"{path} must contain at least {minimum_length} characters.",
                    )
                )
        if "maxLength" in schema:
            maximum_length = _positive_integer(
                schema["maxLength"], field_name="schema maxLength"
            )
            if len(value) > maximum_length:
                issues.append(
                    _issue(
                        "max_length",
                        path,
                        f"{path} must contain at most {maximum_length} characters.",
                    )
                )
        pattern = schema.get("pattern")
        if pattern is not None:
            if not isinstance(pattern, str):
                raise ToolValidationContractError("schema pattern must be text")
            try:
                matches = re.search(pattern, value) is not None
            except re.error as exc:
                raise ToolValidationContractError("schema pattern is invalid") from exc
            if not matches:
                issues.append(
                    _issue("pattern", path, f"{path} does not match the required format.")
                )
        format_name = schema.get("format")
        if format_name is not None:
            if format_name == "uuid":
                try:
                    uuid.UUID(value)
                except ValueError:
                    issues.append(_issue("format", path, f"{path} must be a UUID."))
            elif format_name == "uri":
                parsed = urlparse(value)
                if not parsed.scheme or not parsed.netloc:
                    issues.append(_issue("format", path, f"{path} must be a URI."))
            else:
                raise ToolValidationContractError(
                    f"unsupported schema format {format_name!r}"
                )

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema:
            minimum = _number(schema["minimum"], field_name="schema minimum")
            if value < minimum:
                issues.append(
                    _issue("minimum", path, f"{path} must be at least {minimum}.")
                )
        if "maximum" in schema:
            maximum = _number(schema["maximum"], field_name="schema maximum")
            if value > maximum:
                issues.append(
                    _issue("maximum", path, f"{path} must be at most {maximum}.")
                )

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
        dependent_required = schema.get("dependentRequired", {})
        dependent_required = _schema_object(
            dependent_required,
            field_name="schema dependentRequired",
        )
        for trigger, dependencies in dependent_required.items():
            if not isinstance(dependencies, list) or any(
                not isinstance(item, str) for item in dependencies
            ):
                raise ToolValidationContractError(
                    "schema dependentRequired entries must be arrays of text"
                )
            if trigger not in value:
                continue
            for dependency in dependencies:
                if dependency not in value:
                    dependency_path = _path(path, dependency)
                    issues.append(
                        _issue(
                            "dependent_required",
                            dependency_path,
                            f"{dependency_path} is required when {_path(path, trigger)} is provided.",
                        )
                    )
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

    if isinstance(value, list):
        if "minItems" in schema:
            minimum_items = _positive_integer(
                schema["minItems"], field_name="schema minItems"
            )
            if len(value) < minimum_items:
                issues.append(
                    _issue(
                        "min_items",
                        path,
                        f"{path} must contain at least {minimum_items} items.",
                    )
                )
        if "items" in schema:
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
            matched, _ = _matching_subschema(
                value,
                alternative,
                field_name="schema anyOf entry",
                path=path,
            )
            if matched:
                break
        if not matched:
            issues.append(
                _issue(
                    "any_of",
                    path,
                    f"{path} must satisfy one accepted argument shape.",
                )
            )

    alternatives = schema.get("oneOf")
    if alternatives is not None:
        if not isinstance(alternatives, list) or not alternatives:
            raise ToolValidationContractError("schema oneOf must be a non-empty array")
        match_count = sum(
            _matching_subschema(
                value,
                alternative,
                field_name="schema oneOf entry",
                path=path,
            )[0]
            for alternative in alternatives
        )
        if match_count != 1:
            issues.append(
                _issue(
                    "one_of",
                    path,
                    f"{path} must satisfy exactly one accepted argument shape.",
                )
            )

    combined = schema.get("allOf")
    if combined is not None:
        if not isinstance(combined, list) or not combined:
            raise ToolValidationContractError("schema allOf must be a non-empty array")
        for entry in combined:
            _, entry_issues = _matching_subschema(
                value,
                entry,
                field_name="schema allOf entry",
                path=path,
            )
            issues.extend(entry_issues[: MAX_VALIDATION_ISSUES - len(issues)])

    condition = schema.get("if")
    if condition is not None:
        condition_matches, _ = _matching_subschema(
            value,
            condition,
            field_name="schema if",
            path=path,
        )
        branch_name = "then" if condition_matches else "else"
        if branch_name in schema:
            _validate(
                value,
                _schema_object(schema[branch_name], field_name=f"schema {branch_name}"),
                path=path,
                issues=issues,
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
