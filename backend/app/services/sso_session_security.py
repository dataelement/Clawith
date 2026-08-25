"""Browser-binding helpers for temporary SSO scan sessions."""

import hashlib
import hmac
import uuid

from app.config import get_settings


_COOKIE_PREFIX = "sso_browser_"


def sso_browser_cookie_name(session_id: uuid.UUID) -> str:
    """Return the per-session cookie name used to bind a scan session to a browser."""
    return f"{_COOKIE_PREFIX}{session_id.hex}"


def sign_sso_browser_binding(session_id: uuid.UUID) -> str:
    """Create an HttpOnly-cookie value that cannot be forged for another session."""
    secret_key = get_settings().SECRET_KEY.encode()
    return hmac.new(secret_key, str(session_id).encode(), hashlib.sha256).hexdigest()


def is_valid_sso_browser_binding(session_id: uuid.UUID, cookie_value: str | None) -> bool:
    """Verify that a browser cookie was minted for this exact scan session."""
    if not cookie_value:
        return False
    return hmac.compare_digest(cookie_value, sign_sso_browser_binding(session_id))
