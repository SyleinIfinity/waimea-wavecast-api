"""
services/preprocessing/app/jobs/rebuild_days.py

Rebuild daily wave features for a range of HST dates.

Reads Silver observations from the database gateway,
computes daily features per HST day, and upserts them.

Idempotent: running twice for the same date range produces the same results.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, timedelta

from packages.common.time import hst_day_bounds_utc
from packages.db.gateway import DatabaseGateway
from services.preprocessing.app.features.daily import (
    compute_daily_features,
    group_observations_by_hst_date,
)

logger = logging.getLogger(__name__)

STATION_ID = "51201"


@dataclass
class RebuildReport:
    start_hst: date
    end_hst: date
    days_processed: int = 0
    days_empty: int = 0
    total_upserted: int = 0


def rebuild_daily_features(
    start_hst: date,
    end_hst: date,
    *,
    db_gateway: DatabaseGateway,
    expected_sample_count: int | None = None,
) -> RebuildReport:
    """
    Recompute and upsert daily wave features for [start_hst, end_hst] inclusive.

    Args:
        start_hst, end_hst:    Inclusive HST date range.
        db_gateway:            Database gateway implementation.
        expected_sample_count: Station cadence hint (None = unknown → coverage_ratio=None).
    """
    report = RebuildReport(start_hst=start_hst, end_hst=end_hst)

    # Fetch all Silver observations covering the requested HST days
    # HST day start/end in UTC
    start_utc, _ = hst_day_bounds_utc(start_hst)
    _, end_utc = hst_day_bounds_utc(end_hst)

    observations = db_gateway.get_observations(
        station_id=STATION_ID,
        start_at=start_utc,
        end_at=end_utc,
    )

    # Group by HST date (handles midnight HST crossing)
    by_date = group_observations_by_hst_date(observations)

    daily_dtos = []
    current = start_hst
    while current <= end_hst:
        day_obs = by_date.get(current, [])
        if not day_obs:
            logger.debug("No observations for HST date %s — skipping", current)
            report.days_empty += 1
        else:
            dto = compute_daily_features(
                current,
                day_obs,
                expected_sample_count=expected_sample_count,
            )
            daily_dtos.append(dto)
            report.days_processed += 1
        current += timedelta(days=1)

    if daily_dtos:
        result = db_gateway.upsert_daily_features(daily_dtos)
        report.total_upserted = result.get("upserted", 0)

    logger.info(
        "Rebuild daily features %s–%s: processed=%d empty=%d upserted=%d",
        start_hst, end_hst, report.days_processed, report.days_empty, report.total_upserted,
    )
    return report
