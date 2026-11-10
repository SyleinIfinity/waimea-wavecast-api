"""
packages/gcs/paths.py

Immutable GCS object path builders for raw NOAA files and derived artifacts.

Path scheme:
  noaa/raw/historical/{year}/{sha256}-{station}h{year}.txt.gz
  noaa/raw/monthly/{year}/{month:02d}/{sha256}-source.txt.gz
  noaa/raw/realtime/{year}/{month:02d}/{day:02d}/{sha256}-snapshot.txt

Rules:
  - Paths are content-addressed (sha256 prefix) — cannot collide or be
    accidentally overwritten by a different file.
  - No ".." or absolute path components (path traversal prevention).
  - Caller passes sha256 as the first segment so the path is immutable.
"""
from __future__ import annotations

import re
from datetime import date

_SAFE_PATTERN = re.compile(r'^[a-zA-Z0-9_.\-]+$')


def _safe(value: str, label: str) -> str:
    """Validate that *value* contains only safe path characters."""
    if not _SAFE_PATTERN.match(value):
        raise ValueError(f"{label} {value!r} contains unsafe characters for GCS path")
    if ".." in value or value.startswith("/"):
        raise ValueError(f"{label} {value!r} looks like a path traversal attempt")
    return value


def annual_object_path(station_id: str, year: int, sha256: str) -> str:
    """
    GCS path for a historical annual NOAA file.

    Format: noaa/raw/historical/{year}/{sha256}-{station}h{year}.txt.gz
    """
    _safe(station_id, "station_id")
    _safe(sha256, "sha256")
    return f"noaa/raw/historical/{year}/{sha256}-{station_id}h{year}.txt.gz"


def monthly_object_path(station_id: str, year: int, month: int, sha256: str) -> str:
    """
    GCS path for a monthly NOAA file.

    Format: noaa/raw/monthly/{year}/{month:02d}/{sha256}-source.txt.gz
    """
    _safe(station_id, "station_id")
    _safe(sha256, "sha256")
    return f"noaa/raw/monthly/{year}/{month:02d}/{sha256}-source.txt.gz"


def realtime_object_path(
    station_id: str,
    snapshot_date: date,
    sha256: str,
) -> str:
    """
    GCS path for a realtime NOAA snapshot.

    Format: noaa/raw/realtime/{year}/{month:02d}/{day:02d}/{sha256}-snapshot.txt
    """
    _safe(station_id, "station_id")
    _safe(sha256, "sha256")
    y, m, d = snapshot_date.year, snapshot_date.month, snapshot_date.day
    return f"noaa/raw/realtime/{y}/{m:02d}/{d:02d}/{sha256}-snapshot.txt"


def full_gcs_uri(bucket: str, object_path: str) -> str:
    """Return gs://{bucket}/{object_path}."""
    _safe(bucket, "bucket")
    return f"gs://{bucket}/{object_path}"
