"""Email service — IMAP/SMTP email operations for agent tools.

Supports all major email providers via preset configurations.
Each agent stores its own email credentials in per-agent tool config.
"""

import imaplib
import socket
import smtplib
import ssl
import email as email_lib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email import encoders
from email.header import decode_header
from email.utils import parseaddr, formataddr, make_msgid
from datetime import datetime
from pathlib import Path
from typing import Optional

from app.core.email import force_ipv4, send_smtp_email

# Preset email provider configurations
EMAIL_PROVIDERS = {
    "qq": {
        "label": "QQ Mail",
        "imap_host": "imap.qq.com",
        "imap_port": 993,
        "smtp_host": "smtp.qq.com",
        "smtp_port": 465,
        "smtp_ssl": True,
        "help_url": "https://service.mail.qq.com/detail/0/310",
        "help_text": "Settings → Account → POP3/IMAP/SMTP → Enable IMAP → Generate authorization code",
    },
    "163": {
        "label": "163 Mail",
        "imap_host": "imap.163.com",
        "imap_port": 993,
        "smtp_host": "smtp.163.com",
        "smtp_port": 465,
        "smtp_ssl": True,
        "help_url": "https://help.mail.163.com/faqDetail.do?code=d7a5dc8471cd0c0e8b4b8f4f8e49998b374173cfe9171305fa1ce630d7f67ac2",
        "help_text": "Settings → POP3/SMTP/IMAP → Enable IMAP → Set authorization code",
    },
    "gmail": {
        "label": "Gmail",
        "imap_host": "imap.gmail.com",
        "imap_port": 993,
        "smtp_host": "smtp.gmail.com",
        "smtp_port": 465,
        "smtp_ssl": True,
        "help_url": "https://support.google.com/accounts/answer/185833",
        "help_text": "Google Account → Security → App passwords → Generate app password",
    },
    "outlook": {
        "label": "Outlook / Microsoft 365",
        "imap_host": "outlook.office365.com",
        "imap_port": 993,
        "smtp_host": "smtp.office365.com",
        "smtp_port": 587,
        "smtp_ssl": False,  # Uses STARTTLS
        "help_url": "https://support.microsoft.com/en-us/account-billing/manage-app-passwords-for-two-step-verification-d6dc8c6d-4bf7-4851-ad95-6d07799387e9",
        "help_text": "Microsoft Account → Security → App passwords",
    },
    "qq_enterprise": {
        "label": "Tencent Enterprise Mail",
        "imap_host": "imap.exmail.qq.com",
        "imap_port": 993,
        "smtp_host": "smtp.exmail.qq.com",
        "smtp_port": 465,
        "smtp_ssl": True,
        "help_url": "https://open.work.weixin.qq.com/help2/pc/18624",
        "help_text": "Enterprise Mail → Settings → Client-specific password → Generate new password",
    },
    "aliyun": {
        "label": "Alibaba Enterprise Mail",
        "imap_host": "imap.qiye.aliyun.com",
        "imap_port": 993,
        "smtp_host": "smtp.qiye.aliyun.com",
        "smtp_port": 465,
        "smtp_ssl": True,
        "help_url": "",
        "help_text": "Use your email password directly",
    },
}


def resolve_config(config: dict) -> dict:
    """Resolve a user config into full IMAP/SMTP settings using provider presets."""
    provider = config.get("email_provider", "custom")
    result = {
        "email_address": config.get("email_address", ""),
        "auth_code": config.get("auth_code", ""),
        "imap_host": config.get("imap_host", ""),
        "imap_port": int(config.get("imap_port", 993)),
        "smtp_host": config.get("smtp_host", ""),
        "smtp_port": int(config.get("smtp_port", 465)),
        "smtp_ssl": config.get("smtp_ssl", True),
    }

    if provider != "custom" and provider in EMAIL_PROVIDERS:
        preset = EMAIL_PROVIDERS[provider]
        result["imap_host"] = preset["imap_host"]
        result["imap_port"] = preset["imap_port"]
        result["smtp_host"] = preset["smtp_host"]
        result["smtp_port"] = preset["smtp_port"]
        result["smtp_ssl"] = preset["smtp_ssl"]

    return result


def _decode_header_value(value: str) -> str:
    """Decode an email header value (handles encoded words)."""
    if not value:
        return ""
    decoded_parts = decode_header(value)
    result = []
    for part, charset in decoded_parts:
        if isinstance(part, bytes):
            result.append(part.decode(charset or "utf-8", errors="replace"))
        else:
            result.append(part)
    return "".join(result)


def _extract_body(msg) -> str:
    """Extract the plain text body from an email message."""
    if msg.is_multipart():
        for part in msg.walk():
            content_type = part.get_content_type()
            content_disposition = str(part.get("Content-Disposition", ""))
            if content_type == "text/plain" and "attachment" not in content_disposition:
                payload = part.get_payload(decode=True)
                if payload:
                    charset = part.get_content_charset() or "utf-8"
                    return payload.decode(charset, errors="replace")
        # Fallback to HTML if no plain text
        for part in msg.walk():
            content_type = part.get_content_type()
            if content_type == "text/html":
                payload = part.get_payload(decode=True)
                if payload:
                    charset = part.get_content_charset() or "utf-8"
                    return f"[HTML content]\n{payload.decode(charset, errors='replace')[:2000]}"
    else:
        payload = msg.get_payload(decode=True)
        if payload:
            charset = msg.get_content_charset() or "utf-8"
            return payload.decode(charset, errors="replace")
    return ""


async def send_email(
    config: dict,
    to: str,
    subject: str,
    body: str,
    cc: Optional[str] = None,
    attachments: Optional[list[str]] = None,
    workspace_path: Optional[Path] = None,
) -> str:
    """Send an email via SMTP.

    Args:
        config: Resolved email config (from resolve_config)
        to: Recipient email address(es), comma-separated
        subject: Email subject
        body: Email body text
        cc: CC recipients, comma-separated
        attachments: List of workspace-relative file paths to attach
        workspace_path: Agent workspace root for resolving attachment paths
    """
    cfg = resolve_config(config)
    addr = cfg["email_address"]
    password = cfg["auth_code"]

    if not addr or not password:
        return "❌ Email not configured. Please set email address and authorization code in tool config."

    msg = MIMEMultipart()
    msg["From"] = addr
    msg["To"] = to
    msg["Subject"] = subject
    if cc:
        msg["Cc"] = cc
    msg["Message-ID"] = make_msgid()
    msg["Date"] = datetime.now().strftime("%a, %d %b %Y %H:%M:%S %z")

    msg.attach(MIMEText(body, "plain", "utf-8"))

    # Attach files
    if attachments and workspace_path:
        for rel_path in attachments:
            full_path = workspace_path / rel_path
            if full_path.exists() and full_path.is_file():
                with open(full_path, "rb") as f:
                    part = MIMEBase("application", "octet-stream")
                    part.set_payload(f.read())
                encoders.encode_base64(part)
                part.add_header("Content-Disposition", f"attachment; filename={full_path.name}")
                msg.attach(part)

    try:
        recipients = [r.strip() for r in to.split(",")]
        if cc:
            recipients += [r.strip() for r in cc.split(",")]
            
        send_smtp_email(
            host=cfg["smtp_host"],
            port=cfg["smtp_port"],
            user=addr,
            password=password,
            from_addr=addr,
            to_addrs=recipients,
            msg_string=msg.as_string(),
            use_ssl=cfg.get("smtp_ssl", True),
            timeout=15,
        )

        return f"✅ Email sent to {to}" + (f" (CC: {cc})" if cc else "")
    except smtplib.SMTPAuthenticationError:
        return "❌ SMTP authentication failed. Please check your email address and authorization code."
    except Exception as e:
        return f"❌ Failed to send email: {str(e)[:200]}"


async def read_emails(
    config: dict,
    limit: int = 10,
    search: Optional[str] = None,
    folder: str = "INBOX",
) -> str:
    """Read emails from IMAP mailbox.

    Args:
        config: Resolved email config
        limit: Max number of emails to return
        search: Optional IMAP search criteria (e.g. 'FROM "john"', 'SUBJECT "hello"')
        folder: Mailbox folder (default INBOX)
    """
    cfg = resolve_config(config)
    addr = cfg["email_address"]
    password = cfg["auth_code"]

    if not addr or not password:
        return "❌ Email not configured. Please set email address and authorization code in tool config."

    limit = min(limit, 30)  # Cap at 30

    try:
      with force_ipv4():
        context = ssl.create_default_context()
        with imaplib.IMAP4_SSL(cfg["imap_host"], cfg["imap_port"], ssl_context=context) as mail:
            mail.login(addr, password)
            mail.select(folder, readonly=True)

            # Search
            if search:
                _, msg_nums = mail.search(None, search)
            else:
                _, msg_nums = mail.search(None, "ALL")

            msg_ids = msg_nums[0].split()
            if not msg_ids:
                return "📭 No emails found."

            # Get latest N emails
            latest_ids = msg_ids[-limit:]
            latest_ids.reverse()  # Newest first

            results = []
            for mid in latest_ids:
                _, msg_data = mail.fetch(mid, "(RFC822)")
                if not msg_data or not msg_data[0]:
                    continue
                raw = msg_data[0][1]
                msg = email_lib.message_from_bytes(raw)

                from_addr = _decode_header_value(msg.get("From", ""))
                subject = _decode_header_value(msg.get("Subject", "(No subject)"))
                date_str = msg.get("Date", "")
                message_id = msg.get("Message-ID", "")
                body = _extract_body(msg)
                # Truncate body for readability
                if len(body) > 500:
                    body = body[:500] + "..."

                results.append(
                    f"---\n"
                    f"**From:** {from_addr}\n"
                    f"**Subject:** {subject}\n"
                    f"**Date:** {date_str}\n"
                    f"**Message-ID:** {message_id}\n"
                    f"**Body:**\n{body}"
                )

            header = f"📬 {len(results)} email(s) from {folder}:\n\n"
            return header + "\n\n".join(results)

    except imaplib.IMAP4.error as e:
        err = str(e)
        if "LOGIN" in err.upper() or "AUTH" in err.upper():
            return "❌ IMAP authentication failed. Please check your email address and authorization code."
        return f"❌ IMAP error: {err[:200]}"
    except Exception as e:
        return f"❌ Failed to read emails: {str(e)[:200]}"


async def reply_email(
    config: dict,
    message_id: str,
    body: str,
    folder: str = "INBOX",
) -> str:
    """Reply to an email by Message-ID.

    Args:
        config: Resolved email config
        message_id: Message-ID of the email to reply to
        body: Reply body text
        folder: Mailbox folder to search in
    """
    cfg = resolve_config(config)
    addr = cfg["email_address"]
    password = cfg["auth_code"]

    if not addr or not password:
        return "❌ Email not configured."

    try:
      with force_ipv4():
        # First, fetch the original email to get From/Subject
        context = ssl.create_default_context()
        original_from = ""
        original_subject = ""

        with imaplib.IMAP4_SSL(cfg["imap_host"], cfg["imap_port"], ssl_context=context) as mail:
            mail.login(addr, password)
            mail.select(folder, readonly=True)
            _, msg_nums = mail.search(None, f'HEADER Message-ID "{message_id}"')
            msg_ids = msg_nums[0].split()
            if not msg_ids:
                return f"❌ Original email not found with Message-ID: {message_id}"

            _, msg_data = mail.fetch(msg_ids[0], "(RFC822)")
            raw = msg_data[0][1]
            original = email_lib.message_from_bytes(raw)
            original_from = original.get("From", "")
            original_subject = _decode_header_value(original.get("Subject", ""))

        # Build reply
        reply_subject = original_subject if original_subject.lower().startswith("re:") else f"Re: {original_subject}"

        reply_msg = MIMEMultipart()
        reply_msg["From"] = addr
        reply_msg["To"] = parseaddr(original_from)[1] or original_from
        reply_msg["Subject"] = reply_subject
        reply_msg["In-Reply-To"] = message_id
        reply_msg["References"] = message_id
        reply_msg["Message-ID"] = make_msgid()

        reply_msg.attach(MIMEText(body, "plain", "utf-8"))

        # Send
        send_smtp_email(
            host=cfg["smtp_host"],
            port=cfg["smtp_port"],
            user=addr,
            password=password,
            from_addr=addr,
            to_addrs=[reply_msg["To"]],
            msg_string=reply_msg.as_string(),
            use_ssl=cfg.get("smtp_ssl", True),
            timeout=15,
        )

        return f"✅ Reply sent to {reply_msg['To']} (Subject: {reply_subject})"

    except Exception as e:
        return f"❌ Failed to reply: {str(e)[:200]}"


async def test_connection(config: dict) -> dict:
    """Test IMAP and SMTP connections.

    Returns dict with 'ok' (bool), 'imap' (str), 'smtp' (str) status messages.
    """
    cfg = resolve_config(config)
    addr = cfg["email_address"]
    password = cfg["auth_code"]

    if not addr or not password:
        return {"ok": False, "error": "Email address and authorization code are required."}

    result = {"ok": True, "imap": "", "smtp": ""}

    # Test IMAP
    try:
      with force_ipv4():
        context = ssl.create_default_context()
        with imaplib.IMAP4_SSL(cfg["imap_host"], cfg["imap_port"], ssl_context=context) as mail:
            mail.login(addr, password)
            mail.select("INBOX", readonly=True)
            _, msg_nums = mail.search(None, "ALL")
            count = len(msg_nums[0].split()) if msg_nums[0] else 0
            result["imap"] = f"✅ IMAP connected ({count} emails in INBOX)"
    except imaplib.IMAP4.error as e:
        result["ok"] = False
        result["imap"] = f"❌ IMAP failed: {str(e)[:150]}"
    except Exception as e:
        result["ok"] = False
        result["imap"] = f"❌ IMAP error: {str(e)[:150]}"

    # Test SMTP
    try:
        send_smtp_email(
            host=cfg["smtp_host"],
            port=cfg["smtp_port"],
            user=addr,
            password=password,
            from_addr=addr,
            to_addrs=[addr],  # Send to self for test
            msg_string="Subject: Clawith Connection Test\n\nSMTP Connection Successful.",
            use_ssl=cfg.get("smtp_ssl", True),
            timeout=10,
        )
        result["smtp"] = "✅ SMTP connected"
    except smtplib.SMTPAuthenticationError:
        result["ok"] = False
        result["smtp"] = "❌ SMTP authentication failed"
    except Exception as e:
        result["ok"] = False
        result["smtp"] = f"❌ SMTP error: {str(e)[:150]}"

    return result
