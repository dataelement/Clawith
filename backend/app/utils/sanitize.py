"""Sanitize sensitive fields from tool call arguments before sending to clients."""

import re
from copy import deepcopy
from urllib.parse import urlparse, urlunparse

# Field names that should be fully masked
SENSITIVE_FIELD_NAMES = {
    "password", "secret", "token", "api_key", "apikey", "api_secret",
    "access_token", "refresh_token", "private_key", "secret_key",
    "authorization", "credentials", "auth",
}

# Field names that contain connection URIs (need special parsing)
CONNECTION_URI_FIELDS = {
    "connection_string", "database_url", "db_url", "dsn", "uri",
    "connection_uri", "jdbc_url", "mongo_uri", "redis_url",
}


def sanitize_tool_args(args: dict | None) -> dict | None:
    """Return a sanitized copy of tool call arguments.

    - Fields matching SENSITIVE_FIELD_NAMES are replaced with "******"
    - Fields matching CONNECTION_URI_FIELDS have passwords masked in the URI
    - Original dict is NOT modified (returns a deep copy)
    """
    if not args:
        return args

    sanitized = deepcopy(args)

    for key in list(sanitized.keys()):
        key_lower = key.lower()

        # Fully mask sensitive fields
        if key_lower in SENSITIVE_FIELD_NAMES:
            sanitized[key] = "******"
            continue

        # Mask password in connection URI fields
        if key_lower in CONNECTION_URI_FIELDS:
            sanitized[key] = _mask_uri_password(str(sanitized[key]))
            continue

        # Check if value looks like a connection URI even if field name doesn't match
        if isinstance(sanitized[key], str):
            val = sanitized[key]
            if _looks_like_connection_uri(val):
                sanitized[key] = _mask_uri_password(val)

    return sanitized


def _mask_uri_password(uri: str) -> str:
    """Mask the password portion of a connection URI.

    mysql://user:secret123@host:3306/db -> mysql://user:******@host:3306/db
    """
    try:
        parsed = urlparse(uri)
        if parsed.password:
            # Reconstruct with masked password
            netloc = parsed.hostname or ""
            if parsed.port:
                netloc = f"{netloc}:{parsed.port}"
            if parsed.username:
                netloc = f"{parsed.username}:******@{netloc}"
            return urlunparse((parsed.scheme, netloc, parsed.path, parsed.params, parsed.query, parsed.fragment))
    except Exception:
        pass

    # Fallback: regex-based masking for non-standard URIs
    return re.sub(r'(://[^:]+:)[^@]+(@)', r'\1******\2', uri)


def _looks_like_connection_uri(value: str) -> bool:
    """Check if a string value looks like a database connection URI."""
    prefixes = ("mysql://", "postgresql://", "postgres://", "sqlite://",
                "mongodb://", "redis://", "mssql://", "oracle://",
                "mysql+", "postgresql+", "postgres+")
    return any(value.lower().startswith(p) for p in prefixes)
