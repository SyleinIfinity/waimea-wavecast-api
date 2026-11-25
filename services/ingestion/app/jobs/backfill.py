"""
services/ingestion/app/jobs/backfill.py

Historical backfill job: download annual (and monthly for the current year)
NOAA files for station 51201 from 2005 to the current year.

Rules:
  - Only fetches years/periods NOAA actually provides (404 = unavailable, logged)
  - Does NOT fabricate missing data (2005–2006 often 404 on some feeds)
  - Idempotent: running twice for the same period is safe
  - Annual feed is authoritative for closed years; monthly/realtime for current year
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date

from packages.db.gateway import DatabaseGateway, JobStatusDTO
from packages.gcs.client import GCSClientProtocol
from packages.noaa.client import NOAAClient
from services.ingestion.app.services.ingest_pipeline import IngestResult, run_ingest

logger = logging.getLogger(__name__)

BACKFILL_START_YEAR = 2005
STATION_ID = "51201"


@dataclass
class BackfillReport:
    """Aggregate result of a full historical backfill."""
    start_year: int
    end_year: int
    results: list[IngestResult] = field(default_factory=list)
    unavailable_periods: list[str] = field(default_factory=list)
    failed_periods: list[str] = field(default_factory=list)

    @property
    def success_count(self) -> int:
        return sum(1 for r in self.results if r.success)

    @property
    def total_silver_inserted(self) -> int:
        return sum(r.silver_inserted for r in self.results)


def run_backfill(
    start_year: int = BACKFILL_START_YEAR,
    end_year: int | None = None,
    *,
    noaa_client: NOAAClient | None = None,
    gcs_client: GCSClientProtocol | None = None,
    db_gateway: DatabaseGateway | None = None,
    bucket: str = "waimea-noaa",
) -> BackfillReport:
    """
    Download historical NOAA data for station 51201.

    For closed years (< current year): annual feed.
    For the current year: monthly feeds for completed months + realtime.

    Args:
        start_year: First year to attempt (default 2005).
        end_year:   Last year to attempt (default: current year).
        noaa_client, gcs_client, db_gateway: Injectable for testing.
        bucket:     GCS bucket name.
    """
    if noaa_client is None:
        noaa_client = NOAAClient()

    today = date.today()
    if end_year is None:
        end_year = today.year

    if start_year > end_year:
        raise ValueError(f"start_year {start_year} > end_year {end_year}")

    report = BackfillReport(start_year=start_year, end_year=end_year)

    for year in range(start_year, end_year + 1):
        if year < today.year:
            # Closed year — annual feed is authoritative
            result = run_ingest(
                feed_key="historical_annual",
                year=year,
                noaa_client=noaa_client,
                gcs_client=gcs_client,
                db_gateway=db_gateway,
                bucket=bucket,
            )
            report.results.append(result)
            if not result.success:
                if result.error and "unavailable" in result.error.lower():
                    logger.warning("Year %d annual feed unavailable: %s", year, result.error)
                    report.unavailable_periods.append(str(year))
                else:
                    logger.error("Year %d annual feed failed: %s", year, result.error)
                    report.failed_periods.append(str(year))

        else:
            # Current year — download completed months via monthly feed
            for month in range(1, today.month):
                result = run_ingest(
                    feed_key="historical_monthly",
                    year=year,
                    month=month,
                    noaa_client=noaa_client,
                    gcs_client=gcs_client,
                    db_gateway=db_gateway,
                    bucket=bucket,
                )
                report.results.append(result)
                period = f"{year}-{month:02d}"
                if not result.success:
                    if result.error and "unavailable" in result.error.lower():
                        report.unavailable_periods.append(period)
                    else:
                        report.failed_periods.append(period)

            # Current month — realtime
            result = run_ingest(
                feed_key="realtime",
                year=year,
                noaa_client=noaa_client,
                gcs_client=gcs_client,
                db_gateway=db_gateway,
                bucket=bucket,
            )
            report.results.append(result)

    logger.info(
        "Backfill complete: %d–%d | success=%d unavailable=%d failed=%d | silver_total=%d",
        start_year, end_year,
        report.success_count,
        len(report.unavailable_periods),
        len(report.failed_periods),
        report.total_silver_inserted,
    )
    return report
