from __future__ import annotations

from email import message_from_string
from email.message import Message

import pytest

from app.services import email_service


@pytest.mark.asyncio
async def test_send_email_uses_explicit_smtp_config_plain_text_and_cc(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def capture_send(**kwargs: object) -> None:
        captured.update(kwargs)

    monkeypatch.setattr(email_service, "send_smtp_email", capture_send)

    result = await email_service.send_email(
        {
            "email_provider": "custom",
            "email_address": "sender@example.com",
            "auth_code": "smtp-secret",
            "smtp_host": "smtp.example.com",
            "smtp_port": 2525,
            "smtp_ssl": False,
        },
        "alice@example.com, bob@example.com",
        "Status",
        "Plain body",
        cc="carol@example.com",
    )

    assert result == (
        "✅ Email sent to alice@example.com, bob@example.com "
        "(CC: carol@example.com)"
    )
    assert captured == {
        "host": "smtp.example.com",
        "port": 2525,
        "user": "sender@example.com",
        "password": "smtp-secret",
        "from_addr": "sender@example.com",
        "to_addrs": [
            "alice@example.com",
            "bob@example.com",
            "carol@example.com",
        ],
        "msg_string": captured["msg_string"],
        "use_ssl": False,
        "timeout": 15,
    }

    message = message_from_string(str(captured["msg_string"]))
    assert message["From"] == "sender@example.com"
    assert message["To"] == "alice@example.com, bob@example.com"
    assert message["Cc"] == "carol@example.com"
    assert message["Subject"] == "Status"
    payloads = message.get_payload()
    assert isinstance(payloads, list)
    assert len(payloads) == 1
    plain_text = payloads[0]
    assert isinstance(plain_text, Message)
    assert plain_text.get_content_type() == "text/plain"
    decoded_body = plain_text.get_payload(decode=True)
    assert isinstance(decoded_body, bytes)
    assert decoded_body.decode("utf-8") == "Plain body"


@pytest.mark.asyncio
async def test_send_email_rejects_missing_explicit_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unexpected_send(**_kwargs: object) -> None:
        raise AssertionError("SMTP must not run without explicit credentials")

    monkeypatch.setattr(email_service, "send_smtp_email", unexpected_send)

    result = await email_service.send_email(
        {"email_provider": "custom", "email_address": "sender@example.com"},
        "alice@example.com",
        "Status",
        "Plain body",
    )

    assert result == (
        "❌ Email not configured. Please set email address and authorization code "
        "in tool config."
    )


@pytest.mark.asyncio
async def test_send_email_bounds_provider_failure_detail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider_detail = "x" * 400

    def fail_send(**_kwargs: object) -> None:
        raise RuntimeError(provider_detail)

    monkeypatch.setattr(email_service, "send_smtp_email", fail_send)

    result = await email_service.send_email(
        {
            "email_provider": "custom",
            "email_address": "sender@example.com",
            "auth_code": "smtp-secret",
            "smtp_host": "smtp.example.com",
            "smtp_port": 2525,
        },
        "alice@example.com",
        "Status",
        "Plain body",
    )

    assert result == f"❌ Failed to send email: {provider_detail[:200]}"
