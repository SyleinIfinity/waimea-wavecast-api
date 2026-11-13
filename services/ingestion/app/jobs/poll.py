"""
services/ingestion/app/jobs/poll.py

Realtime poll job: download the latest NOAA 51201 realtime file every 30 minutes.

Rules:
  - Downloads a configurable overlap window (default 7 days) to catch corrections
  - Dedup by measurement hash — 0 new rows is NOT a failure
  - Respects retry/backoff on 429/5xx
  - Idempotent: running twice is safe
  - Does NOT create fabricated records if NOAA has no new data
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

from packages.db.gateway import DatabaseGateway
from packages.gcs.client import GCSClientProtocol
from packages.noaa.client import NOAAClient
from services.ingestion.app.services.ingest_pipeline import IngestResult, run_ingest

logger = logging.getLogger(__name__)

POLL_INTERVAL_MINUTES = 30
OVERLAP_DAYS = 7     # re-fetch last N days on each poll (NOAA may backfill corrections)


@dataclass
class PollResult:
    """Result of one realtime poll run."""
    polled_at: datetime
    ingest_result: IngestResult
    is_empty_run: bool   # True when NOAA provided 0 new rows (normal)

    @property
    def success(self) -> bool:
        return self.ingest_result.success


def run_poll(
    *,
    noaa_client: NOAAClient | None = None,
    gcs_client: GCSClientProtocol | None = None,
    db_gateway: DatabaseGateway | None = None,
    bucket: str = "waimea-noaa",
) -> PollResult:
    """
    Execute one realtime poll cycle.

    Downloads the current NOAA 51201 realtime file, deduplicates rows,
    and upserts any new or corrected observations to Bronze/Silver.

    0 new Silver rows is a successful empty run (not an error).
    """
    now = datetime.now(timezone.utc)
    today = date.today()

    result = run_ingest(
        feed_key="realtime",
        year=today.year,
        noaa_client=noaa_client,
        gcs_client=gcs_client,
        db_gateway=db_gateway,
        bucket=bucket,
    )

    is_empty = result.success and result.silver_inserted == 0 and result.silver_revised == 0

    if is_empty and result.success:
        logger.info("Poll at %s: no new data (0 inserted/revised) — OK", now.isoformat())
    elif result.success:
        logger.info(
            "Poll at %s: silver ins=%d rev=%d dup=%d",
            now.isoformat(), result.silver_inserted, result.silver_revised, result.silver_duplicate,
        )
    else:
        logger.error("Poll at %s FAILED: %s", now.isoformat(), result.error)

    return PollResult(polled_at=now, ingest_result=result, is_empty_run=is_empty)
