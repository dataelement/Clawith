"""System-owned outbound email service."""

from __future__ import annotations

import asyncio
import inspect
import logging
import smtplib
import ssl
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr, make_msgid

from app.config import get_settings
from app.services.email_service import _force_ipv4

logger = logging.getLogger(__name__)


class SystemEmailConfigError(RuntimeError):
    """Raised when system email configuration is missing or invalid."""


@dataclass(slots=True)
class SystemEmailConfig:
    """Resolved system email configuration."""

    from_address: str
    from_name: str
    smtp_host: str
    smtp_port: int
    smtp_username: str
    smtp_password: str
    smtp_ssl: bool
    smtp_timeout_seconds: int


@dataclass(slots=True)
class BroadcastEmailRecipient:
    """Prepared broadcast recipient payload."""

    email: str
    subject: str
    body: str


def get_system_email_config() -> SystemEmailConfig:
    """Resolve and validate the env-driven system email configuration."""
    settings = get_settings()
    from_address = settings.SYSTEM_EMAIL_FROM_ADDRESS.strip()
    smtp_host = settings.SYSTEM_SMTP_HOST.strip()
    smtp_username = settings.SYSTEM_SMTP_USERNAME.strip() or from_address
    smtp_password = settings.SYSTEM_SMTP_PASSWORD

    if not from_address or not smtp_host or not smtp_password:
        raise SystemEmailConfigError(
            "System email is not configured. Set SYSTEM_EMAIL_FROM_ADDRESS, SYSTEM_SMTP_HOST, and SYSTEM_SMTP_PASSWORD."
        )

    smtp_timeout_seconds = max(1, int(settings.SYSTEM_SMTP_TIMEOUT_SECONDS))

    return SystemEmailConfig(
        from_address=from_address,
        from_name=settings.SYSTEM_EMAIL_FROM_NAME.strip() or "Clawith",
        smtp_host=smtp_host,
        smtp_port=settings.SYSTEM_SMTP_PORT,
        smtp_username=smtp_username,
        smtp_password=smtp_password,
        smtp_ssl=settings.SYSTEM_SMTP_SSL,
        smtp_timeout_seconds=smtp_timeout_seconds,
    )


def _send_system_email_sync(to: str, subject: str, body: str) -> None:
    """Send a plain-text system email synchronously."""
    config = get_system_email_config()

    msg = MIMEMultipart()
    msg["From"] = formataddr((config.from_name, config.from_address))
    msg["To"] = to
    msg["Subject"] = subject
    msg["Message-ID"] = make_msgid()
    msg["Date"] = datetime.now().strftime("%a, %d %b %Y %H:%M:%S %z")
    msg.attach(MIMEText(body, "plain", "utf-8"))

    with _force_ipv4():
        if config.smtp_ssl:
            context = ssl.create_default_context()
            with smtplib.SMTP_SSL(
                config.smtp_host,
                config.smtp_port,
                context=context,
                timeout=config.smtp_timeout_seconds,
            ) as server:
                server.login(config.smtp_username, config.smtp_password)
                server.sendmail(config.from_address, [to], msg.as_string())
        else:
            with smtplib.SMTP(config.smtp_host, config.smtp_port, timeout=config.smtp_timeout_seconds) as server:
                server.ehlo()
                server.starttls(context=ssl.create_default_context())
                server.ehlo()
                server.login(config.smtp_username, config.smtp_password)
                server.sendmail(config.from_address, [to], msg.as_string())


async def send_system_email(to: str, subject: str, body: str) -> None:
    """Send a plain-text system email without blocking the event loop."""
    await asyncio.to_thread(_send_system_email_sync, to, subject, body)


async def send_password_reset_email(
    to: str,
    display_name: str,
    reset_url: str,
    expiry_minutes: int,
) -> None:
    """Send a password reset email."""
    await send_system_email(
        to,
        "Reset your Clawith password",
        (
            f"Hello {display_name},\n\n"
            f"We received a request to reset your Clawith password.\n\n"
            f"Reset link: {reset_url}\n\n"
            f"This link expires in {expiry_minutes} minutes. If you did not request this, you can ignore this email."
        ),
    )


async def deliver_broadcast_emails(recipients: Iterable[BroadcastEmailRecipient]) -> None:
    """Deliver broadcast emails while isolating per-recipient failures."""
    for recipient in recipients:
        try:
            await send_system_email(recipient.email, recipient.subject, recipient.body)
        except Exception as exc:
            logger.warning("Failed to deliver broadcast email to %s: %s", recipient.email, exc)


def fire_and_forget(coro) -> None:
    """Run an awaitable in the background without failing the request."""
    task = asyncio.create_task(coro)

    def _consume_task_result(done_task: asyncio.Task) -> None:
        try:
            done_task.result()
        except Exception as exc:
            logger.warning("Background email task failed: %s", exc)

    task.add_done_callback(_consume_task_result)


def run_background_email_job(job, *args, **kwargs) -> None:
    """Bridge Starlette background tasks to async email jobs."""
    result = job(*args, **kwargs)
    if inspect.isawaitable(result):
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            asyncio.run(result)
        else:
            fire_and_forget(result)
