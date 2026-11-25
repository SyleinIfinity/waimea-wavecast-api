"""Idempotent QC over observed Silver rows; measurements and lineage are immutable here."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from packages.db.gateway import SilverObservationDTO
from services.preprocessing.app.quality.checks import run_quality_checks

FIELD_MAP = {
    "WDIR": "wind_direction_deg", "WSPD": "wind_speed_ms", "GST": "wind_gust_ms",
    "WVHT": "wave_height_m", "DPD": "dominant_period_s", "APD": "average_period_s",
    "MWD": "mean_wave_direction_deg", "PRES": "sea_level_pressure_hpa",
    "ATMP": "air_temperature_c", "WTMP": "sea_surface_temp_c", "DEWP": "dew_point_c",
    "VIS": "visibility_nmi", "PTDY": "pressure_tendency_hpa", "TIDE": "tide_ft",
}
QC_VERSION = "tl07b-v1"


def decision(row: SilverObservationDTO, *, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    values = {code: getattr(row, field) for code, field in FIELD_MAP.items()}
    result = run_quality_checks(row.station_id, row.observed_at, values)
    issues = [{"field": issue.field_code, "type": issue.issue_type,
               "severity": issue.severity} for issue in result.issues]
    if row.raw_id is None or row.source_file_id <= 0:
        issues.append({"field": "lineage", "type": "missing_provenance", "severity": "error"})
    if row.observed_at.tzinfo is None or row.observed_at > now + timedelta(minutes=10):
        issues.append({"field": "observed_at", "type": "invalid_timestamp", "severity": "error"})
    if row.wave_height_m is None:
        issues.append({"field": "WVHT", "type": "missing_core_measurement", "severity": "warning"})
    if row.dominant_period_s is None:
        issues.append({"field": "DPD", "type": "missing_core_measurement", "severity": "warning"})
    status = ("rejected" if any(i["severity"] == "error" for i in issues)
              else "suspect" if any(i["severity"] == "warning" for i in issues)
              else "valid")
    return {"station_id": row.station_id, "observed_at": row.observed_at,
            "measurement_sha256": row.measurement_sha256, "quality_status": status,
            "quality_flags": {"qc_version": QC_VERSION, "issues": issues}}


def review_range(gateway, start: datetime, end: datetime) -> dict:
    rows = gateway.get_observations("51201", start, end)
    decisions = [decision(row) for row in rows]
    results = gateway.apply_quality_batch(decisions)
    counts = {name: sum(item["quality_status"] == name for item in decisions)
              for name in ("valid", "suspect", "rejected")}
    return {"reviewed": len(rows), "statuses": counts, **results}
