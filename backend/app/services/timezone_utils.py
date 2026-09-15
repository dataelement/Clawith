"""IANA timezone-name validation."""

from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def validate_timezone_name(value: str) -> str:
    """Return a valid IANA timezone name or raise a validation error."""
    try:
        ZoneInfo(value)
    except (ValueError, ZoneInfoNotFoundError) as error:
        raise ValueError(f"Invalid IANA timezone: {value}") from error
    return value
