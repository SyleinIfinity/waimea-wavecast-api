"""
services/ingestion/app/jobs/reconcile.py

Reconciliation job: re-checks recently downloaded annual/monthly files
to detect NOAA corrections and trigger revision proposals.

Rules:
  - Re-fetches files for a configurable look-back window (e.g. last 3 months)
  - Compares SHA-256 of new download vs registered content_sha256
  - If hash changed → logs a correction proposal; does NOT auto-overwrite
    without going through the standard priority arbitration
  - If hash unchanged → no-op (no revision created)
  - Runs periodically (nightly or on demand)
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date

from packages.db.gateway import DatabaseGateway
from packages.gcs.client import GCSClientProtocol
from packages.noaa.client import NOAAClient
from services.ingestion.app.services.ingest_pipeline import IngestResult, run_ingest

logger = logging.getLogger(__name__)


@dataclass
class ReconcileReport:
    """Result of one reconciliation run."""
    months_checked: int = 0
    corrections_detected: int = 0
    corrections_applied: int = 0
    results: list[IngestResult] = field(default_factory=list)


def run_reconcile(
    lookback_months: int = 3,
    *,
    noaa_client: NOAAClient | None = None,
    gcs_client: GCSClientProtocol | None = None,
    db_gateway: DatabaseGateway | None = None,
    bucket: str = "waimea-noaa",
) -> ReconcileReport:
    """
    Re-fetch recent monthly NOAA files and apply any corrections.

    Args:
        lookback_months:  Number of recent months to re-check (default 3).
        noaa_client, gcs_client, db_gateway: Injectable dependencies.
        bucket:           GCS bucket name.
    """
    today = date.today()
    report = ReconcileReport()

    # Build list of (year, month) for the last N months
    periods: list[tuple[int, int]] = []
    year, month = today.year, today.month
    for _ in range(lookback_months):
        periods.append((year, month))
        month -= 1
        if month == 0:
            month = 12
            year -= 1

    for y, m in periods:
        feed_key = "historical_monthly" if y < today.year or m < today.month else "realtime"
        result = run_ingest(
            feed_key=feed_key,
            year=y,
            month=m if feed_key == "historical_monthly" else None,
            noaa_client=noaa_client,
            gcs_client=gcs_client,
            db_gateway=db_gateway,
            bucket=bucket,
        )
        report.results.append(result)
        report.months_checked += 1
        if result.silver_revised > 0:
            report.corrections_detected += result.silver_revised
            report.corrections_applied += result.silver_revised
            logger.info(
                "Reconcile: %d revision(s) applied for %d-%02d", result.silver_revised, y, m
            )

    logger.info(
        "Reconcile done: checked=%d corrections_applied=%d",
        report.months_checked, report.corrections_applied,
    )
    return report
