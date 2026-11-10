"""
services/preprocessing/app/quality/checks.py

Quality gate checks for Silver observations.

Hierarchy:
  - hard_reject:  observation is physically impossible → rejected
  - warning:      observation is suspect but keep it (quality_status = suspect)
  - info:         minor annotation only

Checks do NOT interpolate or fabricate values to fix problems.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

Severity = Literal["info", "warning", "error"]


@dataclass
class QualityIssue:
    field_code: str
    issue_type: str
    severity: Severity
    original_value: str
    explanation: str


@dataclass
class QualityCheckResult:
    station_id: str
    observed_at: datetime
    issues: list[QualityIssue] = field(default_factory=list)
    quality_status: str = "valid"   # valid | partial | suspect | rejected

    @property
    def has_errors(self) -> bool:
        return any(i.severity == "error" for i in self.issues)

    @property
    def has_warnings(self) -> bool:
        return any(i.severity == "warning" for i in self.issues)


def run_quality_checks(
    station_id: str,
    observed_at: datetime,
    values: dict[str, float | None],
) -> QualityCheckResult:
    """
    Run domain quality checks on a normalized Silver observation.

    Args:
        station_id:  Station identifier.
        observed_at: UTC-aware observation timestamp.
        values:      Dict of NOAA column code -> float | None.

    Returns:
        QualityCheckResult with any issues found and an overall quality_status.
    """
    result = QualityCheckResult(station_id=station_id, observed_at=observed_at)
    issues = result.issues

    # --- Wave height ---
    wvht = values.get("WVHT")
    if wvht is not None and wvht > 25.0:
        issues.append(QualityIssue(
            field_code="WVHT",
            issue_type="domain_suspect",
            severity="warning",
            original_value=str(wvht),
            explanation=f"Wave height {wvht}m is unusually high for NOAA 51201",
        ))

    # --- Wind speed ---
    wspd = values.get("WSPD")
    if wspd is not None and wspd > 70.0:
        issues.append(QualityIssue(
            field_code="WSPD",
            issue_type="domain_suspect",
            severity="warning",
            original_value=str(wspd),
            explanation=f"Wind speed {wspd} m/s exceeds 70 m/s threshold",
        ))

    # --- Direction range ---
    for dir_field in ("WDIR", "MWD"):
        dval = values.get(dir_field)
        if dval is not None and not (0.0 <= dval <= 360.0):
            issues.append(QualityIssue(
                field_code=dir_field,
                issue_type="domain_violation",
                severity="error",
                original_value=str(dval),
                explanation=f"{dir_field}={dval} outside 0–360 deg",
            ))

    # --- Sea level pressure ---
    pres = values.get("PRES")
    if pres is not None and (pres < 870.0 or pres > 1084.0):
        issues.append(QualityIssue(
            field_code="PRES",
            issue_type="domain_suspect",
            severity="warning",
            original_value=str(pres),
            explanation=f"Pressure {pres} hPa outside plausible sea-level range",
        ))

    # --- Determine overall quality status ---
    if result.has_errors:
        result.quality_status = "rejected"
    elif result.has_warnings:
        result.quality_status = "suspect"
    elif any(v is None for v in values.values()):
        result.quality_status = "partial"
    else:
        result.quality_status = "valid"

    return result
