"""
services/ingestion/app/main.py

Ingestion service FastAPI app.

Internal endpoints (not exposed through Nginx):
  POST /internal/v1/ingestion/poll      — trigger a realtime poll
  POST /internal/v1/ingestion/backfill  — trigger historical backfill
  GET  /health                          — liveness probe
"""
from __future__ import annotations

import logging

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from packages.db.fake import FakeDatabaseGateway
from packages.gcs.fake import FakeGCSClient
from packages.noaa.client import NOAAClient
from services.ingestion.app.jobs.backfill import run_backfill
from services.ingestion.app.jobs.poll import run_poll
from services.ingestion.app.jobs.reconcile import run_reconcile

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="Waimea WaveCast - Ingestion Service",
    description="NOAA NDBC 51201 ingestion: backfill, poll, reconcile",
)

# In production these would be injected from env/config
# For local development + tests: use fake adapters
_noaa_client = NOAAClient()
_gcs_client = FakeGCSClient()
_db_gateway = FakeDatabaseGateway()


@app.get("/health")
def health() -> dict:
    return {"service": "ingestion", "status": "up", "pipeline_implemented": True}


class BackfillRequest(BaseModel):
    start_year: int = Field(default=2005, ge=2000, le=2030)
    end_year: int | None = Field(default=None, ge=2000, le=2030)


@app.post("/internal/v1/ingestion/backfill")
def trigger_backfill(req: BackfillRequest) -> dict:
    """Trigger historical backfill (admin only in production)."""
    report = run_backfill(
        start_year=req.start_year,
        end_year=req.end_year,
        noaa_client=_noaa_client,
        gcs_client=_gcs_client,
        db_gateway=_db_gateway,
    )
    return {
        "start_year": report.start_year,
        "end_year": report.end_year,
        "success_count": report.success_count,
        "unavailable_periods": report.unavailable_periods,
        "failed_periods": report.failed_periods,
        "total_silver_inserted": report.total_silver_inserted,
    }


@app.post("/internal/v1/ingestion/poll")
def trigger_poll() -> dict:
    """Trigger a manual realtime poll."""
    poll = run_poll(
        noaa_client=_noaa_client,
        gcs_client=_gcs_client,
        db_gateway=_db_gateway,
    )
    return {
        "polled_at": poll.polled_at.isoformat(),
        "success": poll.success,
        "is_empty_run": poll.is_empty_run,
        "silver_inserted": poll.ingest_result.silver_inserted,
        "silver_revised": poll.ingest_result.silver_revised,
        "error": poll.ingest_result.error,
    }


@app.post("/internal/v1/ingestion/reconcile")
def trigger_reconcile(lookback_months: int = 3) -> dict:
    """Trigger archive reconciliation."""
    report = run_reconcile(
        lookback_months=lookback_months,
        noaa_client=_noaa_client,
        gcs_client=_gcs_client,
        db_gateway=_db_gateway,
    )
    return {
        "months_checked": report.months_checked,
        "corrections_applied": report.corrections_applied,
    }
