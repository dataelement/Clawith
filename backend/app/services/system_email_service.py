"""System-owned outbound email service.

Supports both:
1. Platform-level configuration via environment variables
2. Tenant-level configuration via system_settings table
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import smtplib
import ssl
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr, make_msgid

from app.core.email import force_ipv4, send_smtp_email

logger = logging.getLogger(__name__)


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





async def resolve_email_config_async(db) -> SystemEmailConfig | None:
    """Resolve email configuration by searching in order:
    1. Platform-level settings in DB ('system_email_platform')
    2. Environment variables (Settings class)
    """
    from sqlalchemy import select
    from app.models.system_settings import SystemSetting

    # 1. Try platform-level config in DB
    try:
        result = await db.execute(select(SystemSetting).where(SystemSetting.key == "system_email_platform"))
        setting = result.scalar_one_or_none()
        if setting and setting.value:
            v = setting.value
            if v.get("SYSTEM_EMAIL_FROM_ADDRESS") and v.get("SYSTEM_SMTP_HOST"):
                return SystemEmailConfig(
                    from_address=str(v.get("SYSTEM_EMAIL_FROM_ADDRESS", "")).strip(),
                    from_name=str(v.get("SYSTEM_EMAIL_FROM_NAME", "Clawith")).strip() or "Clawith",
                    smtp_host=str(v.get("SYSTEM_SMTP_HOST", "")).strip(),
                    smtp_port=int(v.get("SYSTEM_SMTP_PORT", 465)),
                    smtp_username=str(v.get("SYSTEM_SMTP_USERNAME", "")).strip() or str(v.get("SYSTEM_EMAIL_FROM_ADDRESS", "")).strip(),
                    smtp_password=str(v.get("SYSTEM_SMTP_PASSWORD", "")),
                    smtp_ssl=bool(v.get("SYSTEM_SMTP_SSL", True)),
                    smtp_timeout_seconds=max(1, int(v.get("SYSTEM_SMTP_TIMEOUT_SECONDS", 15))),
                )
    except Exception as e:
        logger.warning(f"Error resolving platform email config: {e}")

    return None


async def send_system_email(to: str, subject: str, body: str, db=None) -> None:
    """Send a plain-text system email without blocking the event loop.

    Args:
        to: Recipient email address
        subject: Email subject
        body: Email body text
        db: Optional database session
    """
    if not db:
        from app.database import async_session
        async with async_session() as session:
            config = await resolve_email_config_async(session)
    else:
        config = await resolve_email_config_async(db)

    if not config:
        logger.warning(f"System email not configured, skipped sending to {to}")
        return

    await asyncio.to_thread(_send_email_with_config_sync, config, to, subject, body)


def _send_email_with_config_sync(config: SystemEmailConfig, to: str, subject: str, body: str) -> None:
    """Send email with provided config."""
    msg = MIMEMultipart()
    msg["From"] = formataddr((config.from_name, config.from_address))
    msg["To"] = to
    msg["Subject"] = subject
    msg["Message-ID"] = make_msgid()
    msg["Date"] = datetime.now().strftime("%a, %d %b %Y %H:%M:%S %z")
    msg.attach(MIMEText(body, "plain", "utf-8"))

    send_smtp_email(
        host=config.smtp_host,
        port=config.smtp_port,
        user=config.smtp_username,
        password=config.smtp_password,
        from_addr=config.from_address,
        to_addrs=[to],
        msg_string=msg.as_string(),
        use_ssl=config.smtp_ssl,
        timeout=config.smtp_timeout_seconds,
    )


async def send_password_reset_email(
    to: str,
    display_name: str,
    reset_url: str,
    expiry_minutes: int,
    db=None,
) -> None:
    """Send a password reset email using the configured template.

    Args:
        to: Recipient email
        display_name: User display name
        reset_url: Password reset URL
        expiry_minutes: Token expiry time in minutes
        db: Optional database session
    """
    variables = {
        "display_name": display_name,
        "reset_url": reset_url,
        "expiry_minutes": str(expiry_minutes),
    }
    subject, body = await render_email_template("password_reset", variables, db=db)
    await send_system_email(to, subject, body, db=db)


async def send_company_invitation_email(
    to: str,
    inviter_name: str,
    company_name: str,
    invite_url: str,
    db=None,
) -> None:
    """Send a company invitation email using the configured template.

    Args:
        to: Recipient email
        inviter_name: Name of the person inviting
        company_name: Name of the company
        invite_url: Registration URL with invitation code
        db: Optional database session
    """
    variables = {
        "inviter_name": inviter_name,
        "company_name": company_name,
        "invite_url": invite_url,
    }
    subject, body = await render_email_template("company_invitation", variables, db=db)
    await send_system_email(to, subject, body, db=db)


async def deliver_broadcast_emails(recipients: Iterable[BroadcastEmailRecipient]) -> None:
    """Deliver broadcast emails while isolating per-recipient failures."""
    for recipient in recipients:
        try:
            await send_system_email(recipient.email, recipient.subject, recipient.body)
        except Exception as exc:
            logger.warning("Failed to deliver broadcast email to %s: %s", recipient.email, exc)


# ── Email Templates ──────────────────────────────────────────────────────────

# Default templates for each email scenario.
# Each scenario has a fixed set of available variables (using {{variable}} syntax).
DEFAULT_EMAIL_TEMPLATES: dict[str, dict[str, str]] = {
    "email_verification": {
        "subject": "Verify your Clawith email address",
        "body": (
            "Hello {{display_name}},\n\n"
            "Welcome to Clawith! Please use the following 6-digit code to verify your email address:\n\n"
            "Verification code: {{verification_code}}\n\n"
            "This code expires in {{expiry_minutes}} minutes. "
            "If you did not create an account, you can ignore this email."
        ),
    },
    "password_reset": {
        "subject": "Reset your Clawith password",
        "body": (
            "Hello {{display_name}},\n\n"
            "We received a request to reset your Clawith password.\n\n"
            "Reset link: {{reset_url}}\n\n"
            "This link expires in {{expiry_minutes}} minutes. "
            "If you did not request this, you can ignore this email."
        ),
    },
    "company_invitation": {
        "subject": "{{inviter_name}} invited you to join {{company_name}} on Clawith",
        "body": (
            "Hello,\n\n"
            "{{inviter_name}} has invited you to join their team '{{company_name}}' on Clawith.\n\n"
            "To accept the invitation and create your account, please click the link below:\n\n"
            "{{invite_url}}\n\n"
            "If you don't want to join this team or didn't expect this invitation, you can ignore this email."
        ),
    },
}

# Fixed available variables per scenario (for frontend display)
EMAIL_TEMPLATE_VARIABLES: dict[str, list[str]] = {
    "email_verification": ["display_name", "verification_code", "expiry_minutes"],
    "password_reset": ["display_name", "reset_url", "expiry_minutes"],
    "company_invitation": ["inviter_name", "company_name", "invite_url"],
}


async def get_email_templates(db=None) -> dict[str, dict[str, str]]:
    """Load email templates from DB, falling back to defaults.

    Returns:
        A dict mapping scenario_key -> {"subject": str, "body": str}
    """
    from sqlalchemy import select
    from app.models.system_settings import SystemSetting

    templates = dict(DEFAULT_EMAIL_TEMPLATES)  # start with defaults

    if not db:
        from app.database import async_session
        async with async_session() as session:
            return await _load_templates_from_db(session, templates)
    return await _load_templates_from_db(db, templates)


async def _load_templates_from_db(db, templates: dict) -> dict:
    """Internal helper: overlay DB-saved templates on top of defaults."""
    from sqlalchemy import select
    from app.models.system_settings import SystemSetting

    try:
        result = await db.execute(
            select(SystemSetting).where(SystemSetting.key == "email_templates")
        )
        setting = result.scalar_one_or_none()
        if setting and setting.value:
            saved = setting.value
            for key in templates:
                if key in saved and isinstance(saved[key], dict):
                    # Only override subject/body if present and non-empty
                    if saved[key].get("subject"):
                        templates[key]["subject"] = saved[key]["subject"]
                    if saved[key].get("body"):
                        templates[key]["body"] = saved[key]["body"]
    except Exception as e:
        logger.warning(f"Error loading email templates from DB: {e}")

    return templates


def _render_template(template_str: str, variables: dict[str, str]) -> str:
    """Replace {{variable_name}} placeholders with actual values."""
    result = template_str
    for key, value in variables.items():
        result = result.replace(f"{{{{{key}}}}}", str(value))
    return result


async def render_email_template(
    scenario_key: str,
    variables: dict[str, str],
    db=None,
) -> tuple[str, str]:
    """Render an email template for a given scenario.

    Args:
        scenario_key: One of the known scenario keys (e.g. 'email_verification')
        variables: Dict of variable_name -> value to substitute
        db: Optional database session

    Returns:
        (subject, body) tuple with variables substituted
    """
    templates = await get_email_templates(db=db)
    template = templates.get(scenario_key, DEFAULT_EMAIL_TEMPLATES.get(scenario_key, {}))

    subject = _render_template(template.get("subject", ""), variables)
    body = _render_template(template.get("body", ""), variables)
    return subject, body


async def send_test_email(to: str, db=None) -> None:
    """Send a test email to verify SMTP configuration.

    Args:
        to: Recipient email address
        db: Optional database session for resolving config
    """
    subject = "Clawith Test Email"
    body = (
        "This is a test email from your Clawith platform.\n\n"
        "If you received this email, your SMTP configuration is working correctly.\n\n"
        "-- Clawith System"
    )
    await send_system_email(to, subject, body, db=db)

