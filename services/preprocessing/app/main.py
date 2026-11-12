"""
services/preprocessing/app/main.py

Preprocessing service FastAPI app.

Internal endpoints:
  POST /internal/v1/preprocessing/daily/rebuild  — rebuild daily features for a date range
  GET  /health                                   — liveness probe
"""
from __future__ import annotations

import logging
from datetime import date

from fastapi import FastAPI
from pydantic import BaseModel

from packages.db.fake import FakeDatabaseGateway
from services.preprocessing.app.jobs.rebuild_days import rebuild_daily_features

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="Waimea WaveCast - Preprocessing Service",
    description="Silver QC and daily feature computation for NOAA 51201",
)

_db_gateway = FakeDatabaseGateway()


@app.get("/health")
def health() -> dict:
    return {"service": "preprocessing", "status": "up", "pipeline_implemented": True}


class RebuildDaysRequest(BaseModel):
    start_hst: date
    end_hst: date
    expected_sample_count: int | None = None


@app.post("/internal/v1/preprocessing/daily/rebuild")
def trigger_rebuild(req: RebuildDaysRequest) -> dict:
    """Recompute daily wave features for a range of HST dates."""
    report = rebuild_daily_features(
        start_hst=req.start_hst,
        end_hst=req.end_hst,
        db_gateway=_db_gateway,
        expected_sample_count=req.expected_sample_count,
    )
    return {
        "start_hst": str(report.start_hst),
        "end_hst": str(report.end_hst),
        "days_processed": report.days_processed,
        "days_empty": report.days_empty,
        "total_upserted": report.total_upserted,
    }
