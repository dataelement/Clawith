"""D-020 typed SMTP write facts for send and reply Email tools.

All provider interactions in this module are local fakes.  The tests lock the
boundary between failures known before SMTP DATA, recipient receipts returned
by ``sendmail()``, and transport loss after the write may have been accepted.
"""

from __future__ import annotations

from contextlib import nullcontext
from dataclasses import asdict
import email as email_lib
import email.utils as email_utils
import json
from pathlib import Path
import smtplib
import socket
import uuid

import pytest

from app.core import email as core_email
from app.services import activity_logger, agent_tools, email_service
from app.services import storage as storage_service
from app.services.agent_runtime.tool_execution import ToolExecutionOutcome


OUTBOUND_MESSAGE_ID = "<outbound-1@example.test>"
AUTH_SECRET = "smtp-super-secret-do-not-leak"
LARGE_BODY_TAIL = "BODY-TAIL-MUST-NOT-BE-ECHOED"

ORIGINAL_EMAIL = (
    b"From: Alice Example <alice@example.test>\r\n"
    b"Subject: Quarterly plan\r\n"
    b"Date: Thu, 16 Jul 2026 09:00:00 +0800\r\n"
    b"Message-ID: <original-1@example.test>\r\n"
    b"Content-Type: text/plain; charset=utf-8\r\n"
    b"\r\n"
    b"Original message body."
)

ORIGINAL_WITHOUT_SENDER = (
    b"Subject: Quarterly plan\r\n"
    b"Date: Thu, 16 Jul 2026 09:00:00 +0800\r\n"
    b"Message-ID: <original-1@example.test>\r\n"
    b"Content-Type: text/plain; charset=utf-8\r\n"
    b"\r\n"
    b"Original message body."
)


class FakeSMTP:
    def __init__(
        self,
        *,
        refusals: dict | None = None,
        login_error: BaseException | None = None,
        sendmail_error: BaseException | None = None,
        events: list[str] | None = None,
    ) -> None:
        self.refusals = dict(refusals or {})
        self.login_error = login_error
        self.sendmail_error = sendmail_error
        self.events = events if events is not None else []
        self.connections = 0
        self.login_calls = 0
        self.sendmail_calls: list[dict] = []

    def connect(self) -> None:
        self.connections += 1
        self.events.append("smtp:connect")

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def ehlo(self):
        return 250, b"OK"

    @property
    def esmtp_features(self):
        return {"auth": "PLAIN", "starttls": ""}

    def starttls(self, **_kwargs):
        return 220, b"Ready"

    def login(self, _user: str, _password: str):
        self.login_calls += 1
        if self.login_error is not None:
            raise self.login_error
        return 235, b"Authenticated"

    def sendmail(
        self,
        from_addr: str,
        to_addrs: list[str],
        msg_string: str,
    ):
        self.events.append("smtp:sendmail")
        self.sendmail_calls.append(
            {
                "from_addr": from_addr,
                "to_addrs": list(to_addrs),
                "msg_string": msg_string,
            }
        )
        if self.sendmail_error is not None:
            raise self.sendmail_error
        return dict(self.refusals)


class FakeIMAP:
    def __init__(
        self,
        *,
        raw_email: bytes = ORIGINAL_EMAIL,
        select_result=("OK", [b"1"]),
        search_result=("OK", [b"1"]),
        fetch_result=None,
    ) -> None:
        self.raw_email = raw_email
        self.select_result = select_result
        self.search_result = search_result
        self.fetch_result = fetch_result
        self.calls: list[str] = []
        self.selected_folders: list[str] = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def login(self, _address: str, _password: str):
        self.calls.append("login")
        return "OK", [b"LOGIN completed"]

    def select(self, folder: str, *, readonly: bool = False):
        assert readonly is True
        self.calls.append("select")
        self.selected_folders.append(folder)
        return self.select_result

    def search(self, _charset, _criteria: str):
        self.calls.append("search")
        return self.search_result

    def fetch(self, _message_id: bytes, _query: str):
        self.calls.append("fetch")
        if self.fetch_result is not None:
            return self.fetch_result
        return "OK", [(b"1 (RFC822)", self.raw_email)]


class FakeStorage:
    def __init__(
        self,
        files: dict[str, bytes] | None = None,
        *,
        events: list[str] | None = None,
    ) -> None:
        self.files = dict(files or {})
        self.events = events if events is not None else []

    def _path(self, key) -> str | None:
        normalized = str(key).replace("\\", "/")
        for path in self.files:
            if normalized == path or normalized.endswith(f"/{path}"):
                return path
        return None

    async def exists(self, key) -> bool:
        normalized = str(key).replace("\\", "/")
        self.events.append(f"storage:exists:{normalized}")
        return self._path(key) is not None

    async def is_file(self, key) -> bool:
        normalized = str(key).replace("\\", "/")
        self.events.append(f"storage:is_file:{normalized}")
        return self._path(key) is not None

    async def read_bytes(self, key) -> bytes:
        normalized = str(key).replace("\\", "/")
        self.events.append(f"storage:read:{normalized}")
        path = self._path(key)
        if path is None:
            raise FileNotFoundError(normalized)
        return self.files[path]


def _install_provider(
    monkeypatch,
    tmp_path: Path,
    smtp: FakeSMTP,
    *,
    imap: FakeIMAP | None = None,
    storage: FakeStorage | None = None,
    auth_code: str = AUTH_SECRET,
) -> None:
    async def email_config(_agent_id):
        return {
            "email_provider": "custom",
            "email_address": "agent@example.test",
            "auth_code": auth_code,
            "imap_host": "imap.example.test",
            "imap_port": 993,
            "smtp_host": "smtp.example.test",
            "smtp_port": 465,
            "smtp_ssl": True,
        }

    async def no_tenant(_agent_id):
        return None

    async def no_activity(*args, **kwargs):
        del args, kwargs

    def smtp_factory(*args, **kwargs):
        del args, kwargs
        smtp.connect()
        return smtp

    def imap_factory(*args, **kwargs):
        del args, kwargs
        if imap is None:
            raise AssertionError("send_email must not open IMAP")
        return imap

    fake_storage = storage or FakeStorage()

    monkeypatch.setattr(agent_tools, "_get_email_config", email_config)
    monkeypatch.setattr(agent_tools, "_get_agent_tenant_id", no_tenant)
    monkeypatch.setattr(
        agent_tools,
        "_agent_workspace_root",
        lambda _agent_id: tmp_path,
    )
    monkeypatch.setattr(agent_tools, "get_storage_backend", lambda: fake_storage)
    monkeypatch.setattr(
        storage_service,
        "get_storage_backend",
        lambda: fake_storage,
    )
    monkeypatch.setattr(activity_logger, "log_activity", no_activity)

    monkeypatch.setattr(email_service, "force_ipv4", lambda: nullcontext())
    monkeypatch.setattr(core_email, "force_ipv4", lambda: nullcontext())
    monkeypatch.setattr(email_service.ssl, "create_default_context", lambda: object())
    monkeypatch.setattr(core_email.ssl, "create_default_context", lambda: object())
    monkeypatch.setattr(email_service.smtplib, "SMTP_SSL", smtp_factory)
    monkeypatch.setattr(email_service.smtplib, "SMTP", smtp_factory)
    monkeypatch.setattr(core_email.smtplib, "SMTP_SSL", smtp_factory)
    monkeypatch.setattr(core_email.smtplib, "SMTP", smtp_factory)
    monkeypatch.setattr(email_service.imaplib, "IMAP4_SSL", imap_factory)

    def fixed_message_id():
        return OUTBOUND_MESSAGE_ID

    monkeypatch.setattr(email_utils, "make_msgid", fixed_message_id)
    monkeypatch.setattr(email_service, "make_msgid", fixed_message_id)
    monkeypatch.setattr(
        agent_tools,
        "make_msgid",
        fixed_message_id,
        raising=False,
    )


async def _execute(
    tool_name: str,
    arguments: dict,
) -> ToolExecutionOutcome | str:
    return await agent_tools.execute_builtin_tool_outcome(
        tool_name,
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


def _recipient_set(value) -> set[str]:
    if isinstance(value, dict):
        return set(value)
    return {str(item) for item in value}


def _assert_message_receipt(
    outcome: ToolExecutionOutcome,
    *,
    accepted: set[str],
    refused: set[str],
) -> None:
    assert OUTBOUND_MESSAGE_ID in (outcome.result_ref or "")
    assert outcome.metadata["message_id"] == OUTBOUND_MESSAGE_ID
    assert _recipient_set(outcome.metadata["accepted_recipients"]) == accepted
    assert _recipient_set(outcome.metadata["refused_recipients"]) == refused


def _arguments(tool_name: str) -> dict:
    if tool_name == "send_email":
        return {
            "to": "alice@example.test,bob@example.test",
            "subject": "Quarterly plan",
            "body": "Please review the plan.",
        }
    return {
        "message_id": "<original-1@example.test>",
        "body": "Thanks, I will review it.",
        "folder": "INBOX",
    }


def _recipients(tool_name: str) -> list[str]:
    if tool_name == "send_email":
        return ["alice@example.test", "bob@example.test"]
    return ["alice@example.test"]


@pytest.mark.asyncio
@pytest.mark.parametrize("tool_name", ["send_email", "reply_email"])
async def test_email_write_empty_refusal_map_is_typed_success_with_receipt(
    monkeypatch,
    tmp_path: Path,
    tool_name: str,
) -> None:
    smtp = FakeSMTP(refusals={})
    imap = FakeIMAP() if tool_name == "reply_email" else None
    _install_provider(monkeypatch, tmp_path, smtp, imap=imap)

    outcome = _assert_outcome(
        await _execute(tool_name, _arguments(tool_name)),
        "succeeded",
    )

    recipients = set(_recipients(tool_name))
    _assert_message_receipt(outcome, accepted=recipients, refused=set())
    assert outcome.retryable is False
    assert smtp.connections == 1
    assert len(smtp.sendmail_calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("tool_name", ["send_email", "reply_email"])
async def test_email_write_all_recipients_refused_is_failed_not_retryable(
    monkeypatch,
    tmp_path: Path,
    tool_name: str,
) -> None:
    recipients = _recipients(tool_name)
    refusals = {
        recipient: (550, b"Mailbox unavailable")
        for recipient in recipients
    }
    # smtplib returns a refusal mapping for partial acceptance, but raises
    # SMTPRecipientsRefused when no recipient was accepted.
    smtp = FakeSMTP(sendmail_error=smtplib.SMTPRecipientsRefused(refusals))
    imap = FakeIMAP() if tool_name == "reply_email" else None
    _install_provider(monkeypatch, tmp_path, smtp, imap=imap)

    outcome = _assert_outcome(
        await _execute(tool_name, _arguments(tool_name)),
        "failed",
    )

    assert outcome.error_code
    assert outcome.retryable is False
    assert _recipient_set(outcome.metadata["accepted_recipients"]) == set()
    assert _recipient_set(outcome.metadata["refused_recipients"]) == set(
        recipients
    )
    assert len(smtp.sendmail_calls) == 1


@pytest.mark.asyncio
async def test_send_email_partial_acceptance_is_unknown_with_both_receipts(
    monkeypatch,
    tmp_path: Path,
) -> None:
    smtp = FakeSMTP(
        refusals={"bob@example.test": (550, b"Mailbox unavailable")}
    )
    _install_provider(monkeypatch, tmp_path, smtp)

    outcome = _assert_outcome(
        await _execute("send_email", _arguments("send_email")),
        "unknown",
    )

    _assert_message_receipt(
        outcome,
        accepted={"alice@example.test"},
        refused={"bob@example.test"},
    )
    assert outcome.error_code
    assert outcome.retryable is False
    assert len(smtp.sendmail_calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("tool_name", ["send_email", "reply_email"])
async def test_email_write_auth_failure_before_data_is_failed_without_sendmail(
    monkeypatch,
    tmp_path: Path,
    tool_name: str,
) -> None:
    smtp = FakeSMTP(
        login_error=smtplib.SMTPAuthenticationError(
            535,
            f"authentication rejected: {AUTH_SECRET}".encode(),
        )
    )
    imap = FakeIMAP() if tool_name == "reply_email" else None
    _install_provider(monkeypatch, tmp_path, smtp, imap=imap)

    outcome = _assert_outcome(
        await _execute(tool_name, _arguments(tool_name)),
        "failed",
    )

    assert outcome.error_code
    assert outcome.retryable is False
    assert smtp.connections == 1
    assert smtp.sendmail_calls == []
    assert AUTH_SECRET not in json.dumps(
        asdict(outcome),
        ensure_ascii=False,
        default=str,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("tool_name", ["send_email", "reply_email"])
@pytest.mark.parametrize(
    "sendmail_error",
    [
        socket.timeout("SMTP DATA timed out"),
        smtplib.SMTPServerDisconnected("SMTP disconnected after DATA"),
    ],
    ids=["timeout", "disconnect"],
)
async def test_email_write_transport_loss_inside_sendmail_is_unknown_once(
    monkeypatch,
    tmp_path: Path,
    tool_name: str,
    sendmail_error: BaseException,
) -> None:
    smtp = FakeSMTP(sendmail_error=sendmail_error)
    imap = FakeIMAP() if tool_name == "reply_email" else None
    _install_provider(monkeypatch, tmp_path, smtp, imap=imap)

    outcome = _assert_outcome(
        await _execute(tool_name, _arguments(tool_name)),
        "unknown",
    )

    assert outcome.error_code
    assert outcome.retryable is False
    assert smtp.connections == 1
    assert len(smtp.sendmail_calls) == 1


@pytest.mark.asyncio
async def test_send_email_missing_attachment_fails_before_any_smtp_connection(
    monkeypatch,
    tmp_path: Path,
) -> None:
    events: list[str] = []
    smtp = FakeSMTP(events=events)
    storage = FakeStorage(
        {"workspace/present.txt": b"present"},
        events=events,
    )
    _install_provider(monkeypatch, tmp_path, smtp, storage=storage)
    arguments = {
        **_arguments("send_email"),
        "attachments": [
            "workspace/present.txt",
            "workspace/missing.txt",
        ],
    }

    outcome = _assert_outcome(
        await _execute("send_email", arguments),
        "failed",
    )

    assert outcome.error_code
    assert outcome.retryable is False
    assert smtp.connections == 0
    assert smtp.sendmail_calls == []
    assert not any(event.startswith("smtp:") for event in events)


@pytest.mark.asyncio
async def test_send_email_preflights_all_attachments_before_single_sendmail(
    monkeypatch,
    tmp_path: Path,
) -> None:
    events: list[str] = []
    smtp = FakeSMTP(events=events)
    storage = FakeStorage(
        {
            "workspace/first.txt": b"first attachment",
            "workspace/second.txt": b"second attachment",
        },
        events=events,
    )
    _install_provider(monkeypatch, tmp_path, smtp, storage=storage)
    arguments = {
        **_arguments("send_email"),
        "attachments": [
            "workspace/first.txt",
            "workspace/second.txt",
        ],
    }

    outcome = _assert_outcome(
        await _execute("send_email", arguments),
        "succeeded",
    )

    assert len(smtp.sendmail_calls) == 1
    smtp_connect_index = events.index("smtp:connect")
    read_indexes = [
        index
        for index, event in enumerate(events)
        if event.startswith("storage:read:")
    ]
    assert len(read_indexes) == 2
    assert max(read_indexes) < smtp_connect_index

    message = email_lib.message_from_string(
        smtp.sendmail_calls[0]["msg_string"]
    )
    filenames = {
        part.get_filename()
        for part in message.walk()
        if part.get_filename()
    }
    assert filenames == {"first.txt", "second.txt"}
    assert outcome.status == "succeeded"


@pytest.mark.asyncio
async def test_email_write_outcome_does_not_echo_credentials_or_large_body(
    monkeypatch,
    tmp_path: Path,
) -> None:
    smtp = FakeSMTP()
    _install_provider(monkeypatch, tmp_path, smtp)
    body = ("x" * 12_000) + LARGE_BODY_TAIL
    arguments = {
        **_arguments("send_email"),
        "body": body,
    }

    outcome = _assert_outcome(
        await _execute("send_email", arguments),
        "succeeded",
    )
    serialized = json.dumps(
        asdict(outcome),
        ensure_ascii=False,
        default=str,
    )

    assert AUTH_SECRET not in serialized
    assert LARGE_BODY_TAIL not in serialized
    assert body not in serialized
    assert len(outcome.summary or "") <= 1000


@pytest.mark.asyncio
async def test_reply_email_uses_requested_folder_before_smtp(
    monkeypatch,
    tmp_path: Path,
) -> None:
    smtp = FakeSMTP()
    imap = FakeIMAP()
    _install_provider(monkeypatch, tmp_path, smtp, imap=imap)
    arguments = {
        **_arguments("reply_email"),
        "folder": "Archive/2026",
    }

    outcome = await _execute("reply_email", arguments)

    assert imap.selected_folders == ["Archive/2026"]
    _assert_outcome(outcome, "succeeded")
    assert len(smtp.sendmail_calls) == 1


@pytest.mark.asyncio
async def test_reply_email_missing_original_fails_before_smtp(
    monkeypatch,
    tmp_path: Path,
) -> None:
    smtp = FakeSMTP()
    imap = FakeIMAP(search_result=("OK", [b""]))
    _install_provider(monkeypatch, tmp_path, smtp, imap=imap)

    outcome = _assert_outcome(
        await _execute("reply_email", _arguments("reply_email")),
        "failed",
    )

    assert outcome.error_code
    assert outcome.retryable is False
    assert imap.calls == ["login", "select", "search"]
    assert smtp.connections == 0
    assert smtp.sendmail_calls == []


@pytest.mark.asyncio
async def test_reply_email_invalid_original_sender_fails_before_smtp(
    monkeypatch,
    tmp_path: Path,
) -> None:
    smtp = FakeSMTP()
    imap = FakeIMAP(raw_email=ORIGINAL_WITHOUT_SENDER)
    _install_provider(monkeypatch, tmp_path, smtp, imap=imap)

    outcome = _assert_outcome(
        await _execute("reply_email", _arguments("reply_email")),
        "failed",
    )

    assert outcome.error_code
    assert outcome.retryable is False
    assert imap.calls == ["login", "select", "search", "fetch"]
    assert smtp.connections == 0
    assert smtp.sendmail_calls == []
