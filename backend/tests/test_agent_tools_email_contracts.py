"""D-020 local Email contracts before SMTP writes are typed.

The three Email tools share the configuration stored by ``send_email``.  This
batch locks only deterministic local readiness and model-facing schemas; it
must never probe an IMAP or SMTP provider while resolving the Runtime workset.
"""

from __future__ import annotations

import uuid

import pytest

from app.services import agent_tools, email_service
from app.services.builtin_tool_definitions import (
    builtin_model_definition,
    builtin_readiness,
)


EMAIL_TOOL_NAMES = ("send_email", "read_emails", "reply_email")


def _tool_names(tools: list[dict]) -> set[str]:
    return {
        str(tool.get("function", {}).get("name") or "")
        for tool in tools
    }


def _install_no_provider_io(monkeypatch) -> None:
    class NetworkMustNotBeUsed:
        def __init__(self, *args, **kwargs) -> None:
            del args, kwargs
            raise AssertionError(
                "Email Runtime readiness must not contact IMAP or SMTP"
            )

    def smtp_send_must_not_run(*args, **kwargs) -> None:
        del args, kwargs
        raise AssertionError(
            "Email Runtime readiness must not send or authenticate SMTP"
        )

    monkeypatch.setattr(
        email_service.imaplib,
        "IMAP4_SSL",
        NetworkMustNotBeUsed,
    )
    monkeypatch.setattr(
        email_service.smtplib,
        "SMTP",
        NetworkMustNotBeUsed,
    )
    monkeypatch.setattr(
        email_service.smtplib,
        "SMTP_SSL",
        NetworkMustNotBeUsed,
    )
    monkeypatch.setattr(
        email_service,
        "send_smtp_email",
        smtp_send_must_not_run,
    )


async def _install_runtime_selection(
    monkeypatch,
    *,
    assigned_names: tuple[str, ...] = EMAIL_TOOL_NAMES,
    config: dict,
    include_untyped_email_writes: bool,
) -> None:
    tools = [builtin_model_definition(name) for name in assigned_names]

    async def assigned(_agent_id):
        return tools

    async def email_config(_agent_id):
        return dict(config)

    async def no_dynamic_mcp(_agent_id):
        return set()

    monkeypatch.setattr(agent_tools, "get_agent_tools_for_llm", assigned)
    monkeypatch.setattr(agent_tools, "_get_email_config", email_config)
    monkeypatch.setattr(
        agent_tools,
        "_get_runtime_dynamic_mcp_tool_names",
        no_dynamic_mcp,
    )
    if include_untyped_email_writes:
        # send/reply remain hidden in the real Runtime set in this batch.  The
        # temporary gate lets this test exercise their shared readiness logic
        # without changing that model-visible contract.
        monkeypatch.setattr(
            agent_tools,
            "RUNTIME_TYPED_APPLICATION_TOOL_NAMES",
            frozenset(
                {
                    *agent_tools.RUNTIME_TYPED_APPLICATION_TOOL_NAMES,
                    *EMAIL_TOOL_NAMES,
                }
            ),
        )
    _install_no_provider_io(monkeypatch)


def test_email_tools_share_one_canonical_readiness_kind() -> None:
    for name in EMAIL_TOOL_NAMES:
        assert builtin_readiness(name) == "email_configuration"


def test_all_email_tools_enter_the_typed_runtime_workset() -> None:
    assert "read_emails" in agent_tools.RUNTIME_TYPED_APPLICATION_TOOL_NAMES
    assert "send_email" in agent_tools.RUNTIME_TYPED_APPLICATION_TOOL_NAMES
    assert "reply_email" in agent_tools.RUNTIME_TYPED_APPLICATION_TOOL_NAMES


def test_read_email_limit_is_bounded_to_one_through_thirty() -> None:
    schema = builtin_model_definition("read_emails")["function"]["parameters"]
    limit = schema["properties"]["limit"]

    assert limit["type"] == "integer"
    assert limit["default"] == 10
    assert limit["minimum"] == 1
    assert limit["maximum"] == 30


def test_reply_email_exposes_the_mailbox_folder_used_to_find_the_thread() -> None:
    schema = builtin_model_definition("reply_email")["function"]["parameters"]
    folder = schema["properties"]["folder"]

    assert folder["type"] == "string"
    assert folder["default"] == "INBOX"
    assert folder["minLength"] == 1
    assert schema["required"] == ["message_id", "body"]


@pytest.mark.parametrize(
    ("tool_name", "field"),
    [
        ("send_email", "to"),
        ("send_email", "subject"),
        ("send_email", "body"),
        ("send_email", "cc"),
        ("read_emails", "search"),
        ("read_emails", "folder"),
        ("reply_email", "message_id"),
        ("reply_email", "body"),
        ("reply_email", "folder"),
    ],
)
def test_email_string_arguments_are_nonempty_when_present(
    tool_name: str,
    field: str,
) -> None:
    schema = builtin_model_definition(tool_name)["function"]["parameters"]

    assert schema["properties"][field]["minLength"] == 1


def test_email_attachment_paths_are_nonempty_when_present() -> None:
    schema = builtin_model_definition("send_email")["function"]["parameters"]

    assert schema["properties"]["attachments"]["items"]["minLength"] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("config", "expected"),
    [
        (
            {
                "email_provider": "custom",
                "email_address": "agent@example.test",
                "auth_code": "secret",
            },
            set(),
        ),
        (
            {
                "email_provider": "custom",
                "email_address": "agent@example.test",
                "auth_code": "secret",
                "smtp_host": "smtp.example.test",
                "smtp_port": 465,
            },
            {"send_email"},
        ),
        (
            {
                "email_provider": "custom",
                "email_address": "agent@example.test",
                "auth_code": "secret",
                "imap_host": "imap.example.test",
                "imap_port": 993,
            },
            {"read_emails"},
        ),
        (
            {
                "email_provider": "custom",
                "email_address": "agent@example.test",
                "auth_code": "secret",
                "imap_host": "imap.example.test",
                "imap_port": 993,
                "smtp_host": "smtp.example.test",
                "smtp_port": 465,
            },
            {"send_email", "read_emails", "reply_email"},
        ),
        (
            {
                "email_provider": "gmail",
                "email_address": "agent@example.test",
                "auth_code": "secret",
            },
            {"send_email", "read_emails", "reply_email"},
        ),
    ],
    ids=[
        "credentials-without-custom-endpoints",
        "smtp-only",
        "imap-only",
        "custom-both-protocols",
        "provider-preset",
    ],
)
async def test_email_readiness_is_local_and_protocol_specific(
    monkeypatch,
    config: dict,
    expected: set[str],
) -> None:
    await _install_runtime_selection(
        monkeypatch,
        config=config,
        include_untyped_email_writes=True,
    )

    resolved = await agent_tools.get_runtime_agent_tools_for_llm(uuid.uuid4())

    assert _tool_names(resolved) == expected


@pytest.mark.asyncio
async def test_read_emails_is_visible_only_when_assigned_and_locally_ready(
    monkeypatch,
) -> None:
    ready = {
        "email_provider": "custom",
        "email_address": "agent@example.test",
        "auth_code": "secret",
        "imap_host": "imap.example.test",
        "imap_port": 993,
    }
    await _install_runtime_selection(
        monkeypatch,
        assigned_names=("read_emails",),
        config=ready,
        include_untyped_email_writes=False,
    )

    resolved = await agent_tools.get_runtime_agent_tools_for_llm(uuid.uuid4())

    assert _tool_names(resolved) == {"read_emails"}


@pytest.mark.asyncio
async def test_read_emails_is_hidden_when_assigned_but_not_locally_ready(
    monkeypatch,
) -> None:
    await _install_runtime_selection(
        monkeypatch,
        assigned_names=("read_emails",),
        config={
            "email_provider": "custom",
            "email_address": "agent@example.test",
            "auth_code": "secret",
            "imap_host": "",
            "imap_port": 993,
        },
        include_untyped_email_writes=False,
    )

    resolved = await agent_tools.get_runtime_agent_tools_for_llm(uuid.uuid4())

    assert resolved == []


@pytest.mark.asyncio
async def test_ready_read_emails_is_still_hidden_when_unassigned(
    monkeypatch,
) -> None:
    await _install_runtime_selection(
        monkeypatch,
        assigned_names=(),
        config={
            "email_provider": "custom",
            "email_address": "agent@example.test",
            "auth_code": "secret",
            "imap_host": "imap.example.test",
            "imap_port": 993,
        },
        include_untyped_email_writes=False,
    )

    resolved = await agent_tools.get_runtime_agent_tools_for_llm(uuid.uuid4())

    assert resolved == []
