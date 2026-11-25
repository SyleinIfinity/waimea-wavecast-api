"""
packages/noaa/hash.py

SHA-256 helpers for NOAA raw bytes and canonical measurement rows.

Rules:
  - source_hash: SHA-256 of the original file bytes (unchanged, stable)
  - row_hash:    SHA-256 of the canonical measurement values (excludes fetch
                 metadata, source alias, ingest timestamps so the same
                 observation from annual and monthly produces the same hash
                 when values are identical)
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime

# Fields included in the canonical measurement hash.
# Order is fixed alphabetically to ensure stability.
_MEASUREMENT_HASH_FIELDS: tuple[str, ...] = (
    "APD", "ATMP", "DEWP", "DPD", "GST",
    "MWD", "PRES", "PTDY", "TIDE", "VIS",
    "WDIR", "WSPD", "WVHT", "WTMP",
)


def file_sha256(raw_bytes: bytes) -> str:
    """Return the lowercase hex SHA-256 of the raw file bytes."""
    return hashlib.sha256(raw_bytes).hexdigest()


def measurement_sha256(
    station_id: str,
    observed_at: datetime,
    values: dict[str, float | None],
) -> str:
    """
    Compute a stable SHA-256 for a canonical Silver measurement row.

    The hash covers:
      - station_id
      - observed_at (ISO 8601 UTC string)
      - each of the 14 canonical NOAA measurement values in fixed order

    Fields missing from *values* are treated as None.
    Excludes: source_kind, source_file_id, fetch time, run ID.
    """
    payload: dict[str, object] = {
        "station_id": station_id,
        "observed_at": observed_at.isoformat(),
    }
    for field in _MEASUREMENT_HASH_FIELDS:
        v = values.get(field)
        payload[field] = None if v is None else round(float(v), 6)

    # Use compact, sorted JSON as the canonical serialisation
    serialised = json.dumps(payload, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(serialised.encode()).hexdigest()


def row_sha256(raw_line: str) -> str:
    """SHA-256 of a single raw text line (for bronze.raw_observations dedup)."""
    return hashlib.sha256(raw_line.encode()).hexdigest()
