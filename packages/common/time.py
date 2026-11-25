"""
packages/common/time.py
UTC-aware datetime helpers and HST (Pacific/Honolulu) day boundary utilities.
"""
from __future__ import annotations

import math
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

HST = ZoneInfo("Pacific/Honolulu")
UTC = timezone.utc

STATION_TIMEZONE = "Pacific/Honolulu"


def utc_now() -> datetime:
    """Return current UTC-aware datetime."""
    return datetime.now(UTC)


def to_utc(dt: datetime) -> datetime:
    """Ensure *dt* is UTC-aware; raise if naive."""
    if dt.tzinfo is None:
        raise ValueError(f"Naive datetime not allowed: {dt!r}")
    return dt.astimezone(UTC)


def to_hst(dt: datetime) -> datetime:
    """Convert *dt* (any tz-aware) to Hawaii Standard Time."""
    return to_utc(dt).astimezone(HST)


def observed_at_to_date_hst(observed_at: datetime) -> date:
    """Return the Hawaii calendar date for a UTC-aware observation instant."""
    return to_hst(observed_at).date()


def hst_day_bounds_utc(date_hst: date) -> tuple[datetime, datetime]:
    """
    Return (start_utc_inclusive, end_utc_exclusive) for a Hawaii calendar day.

    Example:
        2024-01-15 HST  ->  (2024-01-15T10:00:00Z, 2024-01-16T10:00:00Z)
    because HST = UTC-10.
    """
    start_hst = datetime(date_hst.year, date_hst.month, date_hst.day, tzinfo=HST)
    end_hst = datetime(
        date_hst.year, date_hst.month, date_hst.day, 23, 59, 59, 999999, tzinfo=HST
    )
    return to_utc(start_hst), to_utc(end_hst)


def parse_noaa_year(raw_year: str) -> int:
    """
    Parse a 2- or 4-digit NOAA year string into a 4-digit integer.

    NOAA files from the late 1990s/early 2000s used 2-digit years (#YY header).
    Convention: 00–99 -> 1900 + yy for yy >= 50, else 2000 + yy.
    """
    raw_year = raw_year.strip()
    if len(raw_year) == 4:
        return int(raw_year)
    yy = int(raw_year)
    return 1900 + yy if yy >= 50 else 2000 + yy


def circular_mean_direction(degrees: list[float | None]) -> float | None:
    """
    Compute the circular mean of a list of direction values (degrees true).

    Returns None if no valid values or if the resultant vector length is ~0
    (opposing directions cancel out).
    """
    valid = [d for d in degrees if d is not None and not math.isnan(d)]
    if not valid:
        return None
    sin_sum = sum(math.sin(math.radians(d)) for d in valid)
    cos_sum = sum(math.cos(math.radians(d)) for d in valid)
    r = math.hypot(sin_sum, cos_sum)
    if r < 1e-9:
        return None
    angle_rad = math.atan2(sin_sum, cos_sum)
    angle_deg = math.degrees(angle_rad) % 360
    return angle_deg
