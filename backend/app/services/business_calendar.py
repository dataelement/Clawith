"""Business calendar helpers for scheduled OKR work.

The first layer is intentionally conservative: weekends are authoritative, and
fixed-date public holidays are covered for common regions. Movable holidays can
be added behind the same interface without changing trigger logic.
"""

from datetime import date


# Weekend definitions by country/region code. Default is Monday-Friday workweek.
WEEKEND_DAYS: dict[str, set[int]] = {
    "AE": {5, 6},  # Saturday, Sunday
    "BH": {4, 5},  # Friday, Saturday
    "IL": {4, 5},  # Friday, Saturday
    "SA": {4, 5},  # Friday, Saturday
}


FIXED_HOLIDAYS: dict[str, set[tuple[int, int]]] = {
    "CN": {(1, 1), (5, 1), (10, 1), (10, 2), (10, 3)},
    "HK": {(1, 1), (5, 1), (7, 1), (10, 1), (12, 25), (12, 26)},
    "MO": {(1, 1), (5, 1), (10, 1), (12, 20), (12, 25)},
    "TW": {(1, 1), (2, 28), (10, 10)},
    "US": {(1, 1), (6, 19), (7, 4), (11, 11), (12, 25)},
    "GB": {(1, 1), (12, 25), (12, 26)},
    "JP": {(1, 1), (2, 11), (2, 23), (4, 29), (5, 3), (5, 4), (5, 5), (8, 11), (11, 3), (11, 23)},
    "KR": {(1, 1), (3, 1), (5, 5), (6, 6), (8, 15), (10, 3), (10, 9), (12, 25)},
    "SG": {(1, 1), (5, 1), (8, 9), (12, 25)},
    "IN": {(1, 26), (8, 15), (10, 2)},
    "AU": {(1, 1), (1, 26), (4, 25), (12, 25), (12, 26)},
    "NZ": {(1, 1), (2, 6), (4, 25), (12, 25), (12, 26)},
    "CA": {(1, 1), (7, 1), (12, 25)},
    "DE": {(1, 1), (5, 1), (10, 3), (12, 25), (12, 26)},
    "FR": {(1, 1), (5, 1), (5, 8), (7, 14), (8, 15), (11, 1), (11, 11), (12, 25)},
    "BR": {(1, 1), (4, 21), (5, 1), (9, 7), (10, 12), (11, 2), (11, 15), (12, 25)},
}


def is_non_workday(day: date, country_region: str | None) -> bool:
    """Return True when a date should be skipped for business reporting."""
    code = (country_region or "001").upper()
    weekend_days = WEEKEND_DAYS.get(code, {5, 6})
    if day.weekday() in weekend_days:
        return True
    return (day.month, day.day) in FIXED_HOLIDAYS.get(code, set())
