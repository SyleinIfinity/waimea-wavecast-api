"""
packages/noaa/urls.py

NOAA NDBC URL builders for station 51201.

Annual:  https://www.ndbc.noaa.gov/data/historical/stdmet/{station}h{year}.txt.gz
Monthly: https://www.ndbc.noaa.gov/data/stdmet/{Mon}/{station}{month_code}{year}.txt.gz
         where Mon = Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec
         and   month_code = 1..12 (integer, NOT zero-padded in older files)
Realtime: https://www.ndbc.noaa.gov/data/realtime2/{station}.txt

References: Notebook Waimea_NOAA_51201_Incremental_2005_Present.ipynb
"""
from __future__ import annotations

from datetime import date
from typing import Final

STATION_ID: Final[str] = "51201"
_BASE: Final[str] = "https://www.ndbc.noaa.gov"

# Allowed station IDs (allowlist prevents path traversal/injection)
_ALLOWED_STATIONS: Final[frozenset[str]] = frozenset({"51201"})

# Maps month number 1..12 to NOAA directory component
_MONTH_DIRS: Final[dict[int, str]] = {
    1: "Jan", 2: "Feb", 3: "Mar", 4: "Apr",
    5: "May", 6: "Jun", 7: "Jul", 8: "Aug",
    9: "Sep", 10: "Oct", 11: "Nov", 12: "Dec",
}


def _check_station(station_id: str) -> None:
    if station_id not in _ALLOWED_STATIONS:
        raise ValueError(f"Station {station_id!r} is not in the allowed list")


def annual_url(year: int, station_id: str = STATION_ID) -> str:
    """Return the NOAA annual Standard Meteorological URL for *year*."""
    _check_station(station_id)
    if year < 1900 or year > date.today().year:
        raise ValueError(f"Year {year} out of reasonable range")
    return f"{_BASE}/data/historical/stdmet/{station_id}h{year}.txt.gz"


def monthly_url(year: int, month: int, station_id: str = STATION_ID) -> str:
    """Return the NOAA monthly URL for *year* / *month* (1-indexed)."""
    _check_station(station_id)
    if month < 1 or month > 12:
        raise ValueError(f"Month {month} out of range 1–12")
    mon_dir = _MONTH_DIRS[month]
    return f"{_BASE}/data/stdmet/{mon_dir}/{station_id}{month}{year}.txt.gz"


def realtime_url(station_id: str = STATION_ID) -> str:
    """Return the NOAA realtime Standard Meteorological URL."""
    _check_station(station_id)
    return f"{_BASE}/data/realtime2/{station_id}.txt"


def feed_urls(station_id: str = STATION_ID) -> dict[str, str]:
    """
    Return a summary dict with the URL templates used for each feed type.
    Use this for documentation / feed registration only.
    """
    return {
        "annual_template": f"{_BASE}/data/historical/stdmet/{station_id}h{{year}}.txt.gz",
        "monthly_template": f"{_BASE}/data/stdmet/{{Mon}}/{station_id}{{month}}{{year}}.txt.gz",
        "realtime": realtime_url(station_id),
    }
