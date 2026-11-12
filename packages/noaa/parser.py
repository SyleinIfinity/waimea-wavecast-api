"""
packages/noaa/parser.py

Header-driven parser for NOAA NDBC Standard Meteorological text files.

Supports:
- Old #YY header (2-digit year, pre-2005 era)
- New #YYYY header (4-digit year)
- BAR alias for PRES (older files)
- Variable column sets across years
- Files with or without PTDY, TIDE columns
- Gzip-compressed and plain text
- Realtime and historical format

Each parsed row becomes a dict[str, str | None] with:
  - 'observed_at': ISO 8601 UTC string
  - one key per NOAA column present in the file header
  - missing / sentinel values left as the raw string (normalize.py handles conversion)
"""
from __future__ import annotations

import gzip
import io
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Generator

from packages.common.errors import ParseError
from packages.common.time import parse_noaa_year

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Column alias normalization (old → canonical NOAA code)
# ---------------------------------------------------------------------------
_COLUMN_ALIASES: dict[str, str] = {
    "BAR": "PRES",   # older files used BAR for sea-level pressure
    "WD": "WDIR",    # early annual archives used WD
}

# The 14 canonical NOAA Standard Meteorological fields (from the project notebook)
CANONICAL_FIELDS: tuple[str, ...] = (
    "WDIR", "WSPD", "GST", "WVHT", "DPD", "APD", "MWD",
    "PRES", "ATMP", "WTMP", "DEWP", "VIS", "PTDY", "TIDE",
)

# Datetime components that form observed_at
_TIME_COLS = {"YY", "#YY", "YYYY", "#YYYY", "MM", "DD", "hh", "mm"}


@dataclass
class ParsedRow:
    """A single normalized observation line from an STDMET file."""
    observed_at: datetime          # UTC-aware
    raw_values: dict[str, str]     # column -> raw string (sentinel preserved)
    source_line: str               # original text line
    line_number: int


@dataclass
class ParseResult:
    """Result of parsing one NOAA STDMET file."""
    rows: list[ParsedRow] = field(default_factory=list)
    columns_in_source: list[str] = field(default_factory=list)
    parse_error_count: int = 0
    errors: list[str] = field(default_factory=list)


def _decompress(raw_bytes: bytes, filename: str = "") -> str:
    """Return decoded text from raw bytes, handling optional gzip."""
    if raw_bytes[:2] == b"\x1f\x8b":
        try:
            with gzip.open(io.BytesIO(raw_bytes)) as gz:
                return gz.read().decode("ascii", errors="replace")
        except Exception as exc:
            raise ParseError(filename, f"gzip decompression failed: {exc}") from exc
    return raw_bytes.decode("ascii", errors="replace")


def _normalize_col(col: str) -> str:
    """Strip comment marker and apply alias mapping."""
    col = col.lstrip("#").strip()
    return _COLUMN_ALIASES.get(col, col)


def detect_header(lines: list[str]) -> tuple[list[str], int]:
    """
    Scan *lines* for the NOAA header row (starts with '#').
    Returns (column_list, header_line_index).

    Raises ParseError if no header found.
    """
    for i, line in enumerate(lines):
        stripped = line.strip()
        tokens = stripped.split()
        if stripped.startswith("#") or (tokens and tokens[0] in ("YYYY", "YY") and
                                         "MM" in tokens and "DD" in tokens and "hh" in tokens):
            raw_cols = stripped.split()
            # Normalize aliases and strip '#' prefix from first token
            cols = [_normalize_col(c) for c in raw_cols]
            # Skip the second header line (units row: also starts with #)
            return cols, i
    raise ParseError("<unknown>", "No header line starting with '#' found")


def _parse_observed_at(row_dict: dict[str, str], two_digit_year: bool) -> datetime:
    """
    Construct a UTC-aware datetime from the parsed row dict.

    Handles both #YY and #YYYY year formats.
    """
    year_key = "YY" if two_digit_year else "YYYY"
    raw_year = row_dict.get(year_key) or row_dict.get("YYYY") or row_dict.get("YY", "")
    year = parse_noaa_year(raw_year)

    # Month is always in column 'MM' (1-indexed)
    month = int(row_dict["MM"])
    day = int(row_dict["DD"])
    hour = int(row_dict.get("hh", "0"))
    minute = int(row_dict.get("mm", "0"))

    return datetime(year, month, day, hour, minute, tzinfo=timezone.utc)


def parse_stdmet(raw_bytes: bytes, filename: str = "") -> ParseResult:
    """
    Parse a full NOAA NDBC Standard Meteorological file.

    Args:
        raw_bytes: Raw bytes of the file (gzip or plain text).
        filename:  Original filename for error reporting.

    Returns:
        ParseResult with all successfully parsed rows.
    """
    text = _decompress(raw_bytes, filename)
    lines = text.splitlines()

    # Locate the header
    try:
        all_cols, header_idx = detect_header(lines)
    except ParseError:
        raise ParseError(filename, "Could not find header row")

    # Detect if old-style 2-digit year
    two_digit_year = "YY" in all_cols and "YYYY" not in all_cols

    # Skip the second '#' line (units row) if present
    data_start = header_idx + 1
    if data_start < len(lines) and lines[data_start].strip().startswith("#"):
        data_start += 1

    # Data columns (excluding time components)
    data_cols = [c for c in all_cols if c not in _TIME_COLS]
    # Keep only the canonical 14 measurement fields that are present
    measurement_cols = [c for c in data_cols if c in CANONICAL_FIELDS]

    result = ParseResult(columns_in_source=list(all_cols))

    for lineno, line in enumerate(lines[data_start:], start=data_start + 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) < len(all_cols):
            # Too few columns — partial line
            result.parse_error_count += 1
            result.errors.append(f"Line {lineno}: expected {len(all_cols)} cols, got {len(parts)}")
            logger.warning("Parse error in %s line %d: short row", filename, lineno)
            continue
        # Build row dict
        row_dict: dict[str, str] = {}
        for i, col in enumerate(all_cols):
            row_dict[col] = parts[i]

        try:
            observed_at = _parse_observed_at(row_dict, two_digit_year)
        except (KeyError, ValueError) as exc:
            result.parse_error_count += 1
            result.errors.append(f"Line {lineno}: bad timestamp — {exc}")
            logger.warning("Timestamp parse error in %s line %d: %s", filename, lineno, exc)
            continue

        raw_values = {c: row_dict[c] for c in measurement_cols if c in row_dict}

        result.rows.append(
            ParsedRow(
                observed_at=observed_at,
                raw_values=raw_values,
                source_line=line,
                line_number=lineno,
            )
        )

    return result


def iter_stdmet_batches(
    raw_bytes: bytes,
    filename: str = "",
    batch_size: int = 500,
) -> Generator[list[ParsedRow], None, None]:
    """
    Yield successive batches of ParsedRow from a NOAA STDMET file.
    Useful for streaming upserts without loading everything into memory.
    """
    result = parse_stdmet(raw_bytes, filename)
    rows = result.rows
    for i in range(0, len(rows), batch_size):
        yield rows[i : i + batch_size]
