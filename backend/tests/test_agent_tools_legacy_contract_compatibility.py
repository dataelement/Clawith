from __future__ import annotations

from pathlib import Path
import uuid

import pytest

from app.services import agent_tools
from app.services.agent_runtime.tool_execution import ToolExecutionOutcome
from app.services.agent_seeder import OKR_AGENT_SOUL
from app.services.builtin_tool_definitions import builtin_model_definition


def _definition(name: str) -> dict:
    definition = builtin_model_definition(name)
    assert definition is not None
    return definition["function"]


def test_feishu_drive_share_schema_does_not_offer_unsupported_name_lookup() -> None:
    schema = _definition("feishu_drive_share")["parameters"]

    assert "member_open_ids" in schema["properties"]
    assert "member_names" not in schema["properties"]


@pytest.mark.asyncio
async def test_typed_doc_create_rejects_legacy_wiki_arguments_before_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider_calls = 0

    async def credentials(*args, **kwargs):
        nonlocal provider_calls
        del args, kwargs
        provider_calls += 1
        return None, None, ToolExecutionOutcome(
            status="failed",
            result_summary="provider path must not run",
            result_ref=None,
            error_code="unexpected_provider_access",
        )

    monkeypatch.setattr(agent_tools, "_feishu_credentials_outcome", credentials)
    outcome = await agent_tools.execute_builtin_tool_outcome(
        "feishu_doc_create",
        {
            "title": "Legacy Wiki document",
            "wiki_space_id": "space-legacy",
            "parent_node_token": "node-legacy",
        },
        uuid.uuid4(),
        uuid.uuid4(),
    )

    assert isinstance(outcome, ToolExecutionOutcome)
    assert outcome.status == "failed"
    assert outcome.error_code == "legacy_tool_arguments_unsupported"
    assert provider_calls == 0


@pytest.mark.asyncio
async def test_typed_calendar_create_rejects_legacy_direct_attendees_before_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider_calls = 0

    async def calendar_context(*args, **kwargs):
        nonlocal provider_calls
        del args, kwargs
        provider_calls += 1
        return None, None, ToolExecutionOutcome(
            status="failed",
            result_summary="provider path must not run",
            result_ref=None,
            error_code="unexpected_provider_access",
        )

    monkeypatch.setattr(
        agent_tools,
        "_feishu_calendar_context_outcome",
        calendar_context,
    )
    outcome = await agent_tools.execute_builtin_tool_outcome(
        "feishu_calendar_create",
        {
            "summary": "Legacy attendee event",
            "start_time": "2026-07-16T09:00:00+08:00",
            "end_time": "2026-07-16T10:00:00+08:00",
            "attendee_open_ids": ["ou_legacy"],
            "attendee_emails": ["legacy@example.com"],
        },
        uuid.uuid4(),
        uuid.uuid4(),
    )

    assert isinstance(outcome, ToolExecutionOutcome)
    assert outcome.status == "failed"
    assert outcome.error_code == "legacy_tool_arguments_unsupported"
    assert provider_calls == 0


def test_okr_report_contracts_describe_bounded_receipts_not_full_markdown() -> None:
    for name in ("generate_okr_report", "generate_monthly_okr_report"):
        description = _definition(name)["description"].lower()
        assert "full" not in description
        assert "plaza" not in description
        assert "receipt" in description or "reference" in description


def test_okr_agent_prompt_uses_report_receipt_without_disabled_plaza_tool() -> None:
    normalized = OKR_AGENT_SOUL.lower()

    assert "plaza_create_post" not in normalized
    assert "generate_okr_report" in normalized
    assert "receipt" in normalized or "reference" in normalized


@pytest.mark.asyncio
async def test_legacy_image_generation_only_serializes_the_typed_outcome(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    calls = 0

    async def typed_outcome(agent_id, workspace, arguments, provider):
        nonlocal calls
        calls += 1
        assert workspace == tmp_path
        assert arguments == {"prompt": "a quiet mountain"}
        assert provider == "openai"
        return ToolExecutionOutcome(
            status="succeeded",
            result_summary="Image generated with a durable workspace receipt.",
            result_ref=f"workspace://{agent_id}/workspace/images/result.png",
        )

    monkeypatch.setattr(agent_tools, "_generate_image_outcome", typed_outcome)
    result = await agent_tools._generate_image(
        uuid.uuid4(),
        tmp_path,
        {"prompt": "a quiet mountain"},
        "openai",
    )

    assert calls == 1
    assert result == "✅ Image generated with a durable workspace receipt."
