"""Sanitize sensitive fields from tool call arguments before sending to clients."""

import re
from copy import deepcopy
from urllib.parse import urlparse, urlunparse

# Field names whose values should be completely hidden (replaced with "***REDACTED***")
SENSITIVE_FIELD_NAMES = {
    "password", "secret", "token", "api_key", "apikey", "api_secret",
    "access_token", "refresh_token", "private_key", "secret_key",
    "authorization", "credentials", "auth",
    # Connection/credential strings — hide entirely, not partially
    "connection_string", "database_url", "db_url", "dsn", "uri",
    "connection_uri", "jdbc_url", "mongo_uri", "redis_url",
}


def sanitize_tool_args(args: dict | None) -> dict | None:
    """Return a sanitized copy of tool call arguments.

    - Fields matching SENSITIVE_FIELD_NAMES are replaced with "***REDACTED***"
    - Values that look like connection URIs are also replaced with "***REDACTED***"
    - Original dict is NOT modified (returns a deep copy)
    """
    if not args:
        return args

    sanitized = deepcopy(args)

    for key in list(sanitized.keys()):
        key_lower = key.lower()

        # Fully mask sensitive fields by name
        if key_lower in SENSITIVE_FIELD_NAMES:
            sanitized[key] = "***REDACTED***"
            continue

        # Fully mask values that look like connection URIs regardless of field name
        if isinstance(sanitized[key], str) and _looks_like_connection_uri(sanitized[key]):
            sanitized[key] = "***REDACTED***"

    # Special case: hide content when writing to secrets.md
    path_val = sanitized.get("path", "") or ""
    if _is_secrets_file_path(path_val):
        if "content" in sanitized:
            sanitized["content"] = "***REDACTED***"

    return sanitized


def is_secrets_path(path: str) -> bool:
    """Check if a path references secrets.md."""
    normalized = path.strip("/")
    return normalized == "secrets.md" or normalized.endswith("/secrets.md")


# Keep private alias for backward compatibility within this module
_is_secrets_file_path = is_secrets_path


def _mask_uri_password(uri: str) -> str:
    """Mask the password portion of a connection URI.

    mysql://user:secret123@host:3306/db -> mysql://user:***REDACTED***@host:3306/db
    """
    try:
        parsed = urlparse(uri)
        if parsed.password:
            # Reconstruct with masked password
            netloc = parsed.hostname or ""
            if parsed.port:
                netloc = f"{netloc}:{parsed.port}"
            if parsed.username:
                netloc = f"{parsed.username}:***REDACTED***@{netloc}"
            return urlunparse((parsed.scheme, netloc, parsed.path, parsed.params, parsed.query, parsed.fragment))
    except Exception:
        pass

    # Fallback: regex-based masking for non-standard URIs
    return re.sub(r'(://[^:]+:)[^@]+(@)', r'\1***REDACTED***\2', uri)


def _looks_like_connection_uri(value: str) -> bool:
    """Check if a string value looks like a database connection URI."""
    prefixes = ("mysql://", "postgresql://", "postgres://", "sqlite://",
                "mongodb://", "redis://", "mssql://", "oracle://",
                "mysql+", "postgresql+", "postgres+")
    return any(value.lower().startswith(p) for p in prefixes)
