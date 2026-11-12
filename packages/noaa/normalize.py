"""
packages/noaa/normalize.py

Convert raw NOAA STDMET string values to typed Python values.

Rules per field (from 03_NOAA_PIPELINE_QUALITY.md):
  WDIR   : 0–360 deg true;   sentinel 999  -> None
  WSPD   : >= 0 m/s;         sentinel 99   -> None
  GST    : >= 0 m/s;         sentinel 99   -> None
  WVHT   : >= 0 m;           sentinel 99   -> None
  DPD    : > 0 s;            sentinel 99   -> None
  APD    : > 0 s;            sentinel 99   -> None
  MWD    : 0–360 deg true;   sentinel 999  -> None
  PRES   : > 0 hPa;          sentinel 9999 -> None  (alias BAR)
  ATMP   : any °C;           sentinel 999  -> None
  WTMP   : any °C;           sentinel 99 / 999 -> None (context-dependent)
  DEWP   : any °C;           sentinel 999  -> None
  VIS    : >= 0 nmi;         no specific sentinel listed; convert mi->nmi if header says mi
  PTDY   : any hPa/3h;       often missing; sentinel 99 -> None
  TIDE   : any ft (can be negative); 99 -> None

"MM" in raw value always means missing regardless of field.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable

logger = logging.getLogger(__name__)

# Sentinel values -> None, keyed by NOAA column code
# Each entry is a set of string literals that mean "missing" for that field.
_SENTINELS: dict[str, set[str]] = {
    "WDIR": {"999", "MM"},
    "WSPD": {"99", "99.0", "MM"},
    "GST":  {"99", "99.0", "MM"},
    "WVHT": {"99", "99.0", "MM"},
    "DPD":  {"99", "99.0", "MM"},
    "APD":  {"99", "99.0", "MM"},
    "MWD":  {"999", "MM"},
    "PRES": {"9999", "9999.0", "MM"},
    "ATMP": {"999", "999.0", "MM"},
    "WTMP": {"99", "99.0", "999", "999.0", "MM"},
    "DEWP": {"999", "999.0", "MM"},
    "VIS":  {"99", "99.0", "MM"},
    "PTDY": {"99", "99.0", "MM"},
    "TIDE": {"99", "99.0", "MM"},
}

# Physical domain validators: (field_code, validator_fn, error_msg)
def _ge0(v: float) -> bool: return v >= 0
def _gt0(v: float) -> bool: return v > 0
def _dir(v: float) -> bool: return 0.0 <= v <= 360.0
def _pres(v: float) -> bool: return v > 0

_VALIDATORS: dict[str, tuple[Callable[[float], bool], str]] = {
    "WDIR": (_dir,  "must be 0–360"),
    "WSPD": (_ge0,  "must be >= 0"),
    "GST":  (_ge0,  "must be >= 0"),
    "WVHT": (_ge0,  "must be >= 0"),
    "DPD":  (_gt0,  "must be > 0"),
    "APD":  (_gt0,  "must be > 0"),
    "MWD":  (_dir,  "must be 0–360"),
    "PRES": (_pres, "must be > 0"),
    "VIS":  (_ge0,  "must be >= 0"),
}

# Fields whose sentinel check is context-sensitive (WTMP can be 99 or 999)
# Already handled generically in _SENTINELS above.


@dataclass
class NormalizedMeasurement:
    """A single NOAA field value after sentinel mapping and validation."""
    field: str
    raw_value: str
    value: float | None           # None = sentinel / missing / invalid
    is_sentinel: bool = False
    validation_issue: str | None = None


def normalize_field(field: str, raw_value: str) -> NormalizedMeasurement:
    """
    Normalize one raw NOAA field value.

    Args:
        field:     NOAA column code (e.g. "WVHT", "PRES").
        raw_value: Raw string from the parsed file.

    Returns:
        NormalizedMeasurement with typed value or None.
    """
    raw_stripped = raw_value.strip()

    # 1. Sentinel check — "MM" is universal; field-specific sentinels follow.
    sentinels = _SENTINELS.get(field, {"MM"})
    if raw_stripped in sentinels:
        return NormalizedMeasurement(field=field, raw_value=raw_value, value=None, is_sentinel=True)

    # 2. Parse to float
    try:
        numeric = float(raw_stripped)
    except ValueError:
        logger.debug("Non-numeric value %r for field %s — treated as missing", raw_value, field)
        return NormalizedMeasurement(
            field=field,
            raw_value=raw_value,
            value=None,
            validation_issue=f"non-numeric: {raw_value!r}",
        )

    # 3. Physical domain check
    validator = _VALIDATORS.get(field)
    if validator is not None:
        fn, msg = validator
        if not fn(numeric):
            logger.warning(
                "Physical domain violation: field=%s value=%s (%s)", field, numeric, msg
            )
            return NormalizedMeasurement(
                field=field,
                raw_value=raw_value,
                value=None,
                validation_issue=f"domain violation: {numeric} ({msg})",
            )

    return NormalizedMeasurement(field=field, raw_value=raw_value, value=numeric)


def normalize_row(raw_values: dict[str, str]) -> dict[str, float | None]:
    """
    Normalize a full row dict of raw NOAA string values.

    Returns a dict mapping NOAA column code -> typed float or None.
    Missing-code handling is per-field, not global.
    """
    result: dict[str, float | None] = {}
    for field, raw in raw_values.items():
        nm = normalize_field(field, raw)
        result[field] = nm.value
    return result


def normalize_visibility(raw_value: str, header_unit: str = "nmi") -> float | None:
    """
    Normalize VIS field, optionally converting from miles to nautical miles.

    Some older NOAA headers list VIS in statute miles (mi); the canonical
    Silver column is nautical miles (nmi). 1 mi = 0.868976 nmi.
    """
    nm = normalize_field("VIS", raw_value)
    if nm.value is None:
        return None
    if header_unit.lower() in ("mi", "statute_miles", "statute miles"):
        return nm.value * 0.868976
    return nm.value
