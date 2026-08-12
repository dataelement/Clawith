"""Accepted Tool schema validation contract tests."""

import pytest

from app.services.agent_runtime.tool_validation import validate_tool_arguments
from app.services.builtin_tool_definitions import BUILTIN_TOOL_DEFINITIONS


_BUILTIN_SCHEMAS = {
    item["name"]: item["parameters_schema"] for item in BUILTIN_TOOL_DEFINITIONS
}


def _schema() -> dict:
    return {
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "count": {"type": "integer"},
            "mode": {"type": "string", "enum": ["fast", "safe"]},
            "options": {
                "type": "object",
                "properties": {"dry_run": {"type": "boolean"}},
                "additionalProperties": False,
            },
            "tags": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["path", "mode"],
        "additionalProperties": False,
    }


def test_valid_arguments_match_the_accepted_schema() -> None:
    assert validate_tool_arguments(
        {
            "path": "notes.md",
            "count": 2,
            "mode": "safe",
            "options": {"dry_run": True},
            "tags": ["one", "two"],
        },
        _schema(),
    ) == ()


def test_missing_required_wrong_type_enum_and_unknown_fields_are_bounded() -> None:
    issues = validate_tool_arguments(
        {
            "count": True,
            "mode": "dangerous",
            "options": {"unexpected": "secret-value-must-not-echo"},
            "extra": "private-value-must-not-echo",
        },
        _schema(),
    )

    assert [(issue.code, issue.path) for issue in issues] == [
        ("required", "$.path"),
        ("type", "$.count"),
        ("enum", "$.mode"),
        ("additional_property", "$.options.unexpected"),
        ("additional_property", "$.extra"),
    ]
    assert all("secret-value" not in issue.summary for issue in issues)
    assert all("private-value" not in issue.summary for issue in issues)


def test_array_item_and_nested_object_types_are_validated() -> None:
    issues = validate_tool_arguments(
        {
            "path": "notes.md",
            "mode": "fast",
            "options": {"dry_run": "yes"},
            "tags": ["ok", 2],
        },
        _schema(),
    )

    assert [(issue.code, issue.path) for issue in issues] == [
        ("type", "$.options.dry_run"),
        ("type", "$.tags[1]"),
    ]


def test_any_of_required_alternatives_accept_one_complete_branch() -> None:
    schema = {
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "document_id": {"type": "string"},
        },
        "anyOf": [
            {"required": ["path"]},
            {"required": ["document_id"]},
        ],
    }

    assert validate_tool_arguments({"document_id": "doc-1"}, schema) == ()
    issues = validate_tool_arguments({}, schema)
    assert [(issue.code, issue.path) for issue in issues] == [("any_of", "$")]


@pytest.mark.parametrize(
    ("tool_name", "arguments", "expected_code"),
    [
        ("upload_image", {}, "one_of"),
        ("send_email", {"to": "", "subject": "", "body": ""}, "min_length"),
        ("write_file", {"path": "x", "content": "x" * 6001}, "max_length"),
        ("query_directory", {"limit": 0}, "minimum"),
        ("query_directory", {"limit": 51}, "maximum"),
        (
            "vercel_deploy",
            {"project_name": "demo", "deploy_method": "upload"},
            "required",
        ),
    ],
)
def test_builtin_schema_constraints_are_enforced_before_execution(
    tool_name: str,
    arguments: dict,
    expected_code: str,
) -> None:
    issues = validate_tool_arguments(arguments, _BUILTIN_SCHEMAS[tool_name])

    assert expected_code in {issue.code for issue in issues}


def test_const_pattern_format_dependent_required_and_min_items() -> None:
    schema = {
        "type": "object",
        "properties": {
            "mode": {"const": "safe"},
            "path": {"type": "string", "pattern": "^[a-z]+$"},
            "request_id": {"type": "string", "format": "uuid"},
            "url": {"type": "string", "format": "uri"},
            "token": {"type": "string"},
            "secret": {"type": "string"},
            "targets": {"type": "array", "minItems": 1},
        },
        "dependentRequired": {"token": ["secret"]},
    }

    issues = validate_tool_arguments(
        {
            "mode": "unsafe",
            "path": "../bad",
            "request_id": "not-a-uuid",
            "url": "not-a-uri",
            "token": "present",
            "targets": [],
        },
        schema,
    )

    assert {issue.code for issue in issues} == {
        "const",
        "pattern",
        "format",
        "dependent_required",
        "min_items",
    }
