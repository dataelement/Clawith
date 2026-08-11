"""Accepted Tool schema validation contract tests."""

from app.services.agent_runtime.tool_validation import validate_tool_arguments


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
