"""D-020 typed IMAP read outcomes using a fully local fake provider."""

from __future__ import annotations

from contextlib import nullcontext
import socket
import uuid

import pytest

from app.services import activity_logger, agent_tools, email_service
from app.services.agent_runtime.tool_execution import ToolExecutionOutcome


RAW_EMAIL = (
    b"From: Alice Example <alice@example.test>\r\n"
    b"Subject: Quarterly plan\r\n"
    b"Date: Thu, 16 Jul 2026 09:00:00 +0800\r\n"
    b"Message-ID: <msg-1@example.test>\r\n"
    b"Content-Type: text/plain; charset=utf-8\r\n"
    b"\r\n"
    b"Please review the attached plan."
)


class FakeIMAP:
    def __init__(
        self,
        *,
        select_result=("OK", [b"1"]),
        search_result=("OK", [b"1"]),
        fetch_result=None,
        login_error: BaseException | None = None,
        select_error: BaseException | None = None,
        search_error: BaseException | None = None,
        fetch_error: BaseException | None = None,
    ) -> None:
        self.select_result = select_result
        self.search_result = search_result
        self.fetch_result = fetch_result or (
            "OK",
            [(b"1 (RFC822)", RAW_EMAIL)],
        )
        self.login_error = login_error
        self.select_error = select_error
        self.search_error = search_error
        self.fetch_error = fetch_error
        self.calls: list[str] = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def login(self, _address: str, _password: str):
        self.calls.append("login")
        if self.login_error is not None:
            raise self.login_error
        return "OK", [b"LOGIN completed"]

    def select(self, _folder: str, *, readonly: bool = False):
        assert readonly is True
        self.calls.append("select")
        if self.select_error is not None:
            raise self.select_error
        return self.select_result

    def search(self, _charset, _criteria: str):
        self.calls.append("search")
        if self.search_error is not None:
            raise self.search_error
        return self.search_result

    def fetch(self, _message_id: bytes, _query: str):
        self.calls.append("fetch")
        if self.fetch_error is not None:
            raise self.fetch_error
        return self.fetch_result


def _install_provider(
    monkeypatch,
    fake: FakeIMAP | None = None,
    *,
    connection_error: BaseException | None = None,
) -> None:
    async def email_config(_agent_id):
        return {
            "email_provider": "custom",
            "email_address": "agent@example.test",
            "auth_code": "secret",
            "imap_host": "imap.example.test",
            "imap_port": 993,
        }

    async def no_tenant(_agent_id):
        return None

    async def no_activity(*args, **kwargs):
        del args, kwargs

    def imap_factory(*args, **kwargs):
        del args, kwargs
        if connection_error is not None:
            raise connection_error
        if fake is None:
            raise AssertionError("IMAP must not be constructed")
        return fake

    class SMTPMustNotBeUsed:
        def __init__(self, *args, **kwargs) -> None:
            del args, kwargs
            raise AssertionError("read_emails must never open SMTP")

    monkeypatch.setattr(agent_tools, "_get_email_config", email_config)
    monkeypatch.setattr(agent_tools, "_get_agent_tenant_id", no_tenant)
    monkeypatch.setattr(activity_logger, "log_activity", no_activity)
    monkeypatch.setattr(email_service, "force_ipv4", lambda: nullcontext())
    monkeypatch.setattr(
        email_service.ssl,
        "create_default_context",
        lambda: object(),
    )
    monkeypatch.setattr(email_service.imaplib, "IMAP4_SSL", imap_factory)
    monkeypatch.setattr(email_service.smtplib, "SMTP", SMTPMustNotBeUsed)
    monkeypatch.setattr(email_service.smtplib, "SMTP_SSL", SMTPMustNotBeUsed)


async def _execute(arguments: dict) -> ToolExecutionOutcome | str:
    return await agent_tools.execute_builtin_tool_outcome(
        "read_emails",
        arguments,
        agent_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
    )


def _assert_outcome(
    value: ToolExecutionOutcome | str,
    status: str,
) -> ToolExecutionOutcome:
    assert isinstance(value, ToolExecutionOutcome)
    assert value.status == status
    return value


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "arguments",
    [
        {"limit": 0},
        {"limit": 31},
        {"limit": "ten"},
        {"folder": ""},
        {"search": ""},
    ],
    ids=["limit-zero", "limit-too-high", "limit-type", "folder-empty", "search-empty"],
)
async def test_read_emails_rejects_invalid_arguments_before_imap(
    monkeypatch,
    arguments: dict,
) -> None:
    _install_provider(monkeypatch)

    outcome = _assert_outcome(await _execute(arguments), "failed")

    assert outcome.error_code == "invalid_tool_arguments"
    assert outcome.retryable is False


@pytest.mark.asyncio
async def test_read_emails_uses_an_ok_fetch_fact_for_typed_success(
    monkeypatch,
) -> None:
    fake = FakeIMAP()
    _install_provider(monkeypatch, fake)

    outcome = _assert_outcome(await _execute({"limit": 1}), "succeeded")

    assert fake.calls == ["login", "select", "search", "fetch"]
    assert "Quarterly plan" in (outcome.summary or "")
    assert "<msg-1@example.test>" in (outcome.summary or "")


@pytest.mark.asyncio
async def test_imap_ok_zero_message_count_is_empty_success(monkeypatch) -> None:
    fake = FakeIMAP(
        select_result=("OK", [b"0"]),
        search_result=("OK", [b""]),
    )
    _install_provider(monkeypatch, fake)

    outcome = _assert_outcome(await _execute({}), "succeeded")

    assert "no email" in (outcome.summary or "").lower()
    assert "fetch" not in fake.calls


@pytest.mark.asyncio
async def test_imap_select_rejection_is_nonretryable_and_short_circuits(
    monkeypatch,
) -> None:
    fake = FakeIMAP(
        select_result=("NO", [b"Mailbox does not exist"]),
    )
    _install_provider(monkeypatch, fake)

    outcome = await _execute({"folder": "missing-folder"})

    assert fake.calls == ["login", "select"]
    typed = _assert_outcome(outcome, "failed")
    assert typed.error_code
    assert typed.retryable is False


@pytest.mark.asyncio
async def test_imap_search_status_is_checked_before_fetch(monkeypatch) -> None:
    fake = FakeIMAP(
        search_result=("BAD", [b"Could not parse search criteria"]),
    )
    _install_provider(monkeypatch, fake)

    outcome = await _execute({"search": 'SUBJECT "plan"'})

    assert fake.calls == ["login", "select", "search"]
    typed = _assert_outcome(outcome, "failed")
    assert typed.error_code


@pytest.mark.asyncio
async def test_imap_fetch_status_is_checked_before_parsing(monkeypatch) -> None:
    fake = FakeIMAP(
        fetch_result=("NO", [b"Message is no longer available"]),
    )
    _install_provider(monkeypatch, fake)

    outcome = await _execute({"limit": 1})

    assert fake.calls == ["login", "select", "search", "fetch"]
    typed = _assert_outcome(outcome, "failed")
    assert typed.error_code


@pytest.mark.asyncio
async def test_imap_authentication_failure_is_not_retryable(monkeypatch) -> None:
    fake = FakeIMAP(
        login_error=email_service.imaplib.IMAP4.error(
            "AUTHENTICATIONFAILED invalid credentials"
        )
    )
    _install_provider(monkeypatch, fake)

    outcome = _assert_outcome(await _execute({}), "failed")

    assert fake.calls == ["login"]
    assert outcome.error_code
    assert outcome.retryable is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("fake", "connection_error"),
    [
        (
            FakeIMAP(search_error=socket.timeout("IMAP search timed out")),
            None,
        ),
        (
            FakeIMAP(fetch_error=ConnectionResetError("IMAP reset")),
            None,
        ),
        (None, socket.timeout("IMAP connect timed out")),
    ],
    ids=["search-timeout", "fetch-reset", "connect-timeout"],
)
async def test_imap_transient_transport_failures_are_retryable(
    monkeypatch,
    fake: FakeIMAP | None,
    connection_error: BaseException | None,
) -> None:
    _install_provider(
        monkeypatch,
        fake,
        connection_error=connection_error,
    )

    outcome = _assert_outcome(await _execute({}), "failed")

    assert outcome.error_code
    assert outcome.retryable is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "fake",
    [
        FakeIMAP(search_result=("OK", None)),
        FakeIMAP(fetch_result=("OK", [(b"metadata without RFC822 body",)])),
    ],
    ids=["malformed-search", "malformed-fetch"],
)
async def test_imap_malformed_responses_are_retryable_failures(
    monkeypatch,
    fake: FakeIMAP,
) -> None:
    _install_provider(monkeypatch, fake)

    outcome = _assert_outcome(await _execute({}), "failed")

    assert outcome.error_code
    assert outcome.retryable is True
