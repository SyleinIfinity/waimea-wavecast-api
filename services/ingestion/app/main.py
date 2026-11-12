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
import secrets
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from packages.db.fake import FakeDatabaseGateway
from packages.db.http import HttpDatabaseGateway
from packages.gcs.fake import FakeGCSClient
from packages.gcs.client import RealGCSClient
from packages.noaa.client import NOAAClient
from services.ingestion.app.jobs.backfill import run_backfill
from services.ingestion.app.jobs.poll import run_poll
from services.ingestion.app.jobs.reconcile import run_reconcile

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class IngestionSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=Path(__file__).resolve().parents[3] / ".env", extra="ignore")
    app_env: str = "development"
    ingestion_adapter_mode: str = "disabled"
    database_api_url: str = "http://database-api:8000"
    internal_api_token: str = ""
    gcs_raw_bucket: str = ""


class BackfillRequest(BaseModel):
    start_year: int = Field(default=2005, ge=2000, le=2030)
    end_year: int | None = Field(default=None, ge=2000, le=2030)


def create_app(settings: IngestionSettings | None = None, *, noaa_client=None,
               gcs_client=None, db_gateway=None) -> FastAPI:
    settings = settings or IngestionSettings()
    if settings.ingestion_adapter_mode == "fake":
        if settings.app_env != "test":
            raise RuntimeError("fake ingestion adapters are allowed only in APP_ENV=test")
        gcs_client = gcs_client or FakeGCSClient()
        db_gateway = db_gateway or FakeDatabaseGateway()
    elif settings.ingestion_adapter_mode == "real":
        if settings.internal_api_token and settings.gcs_raw_bucket:
            gcs_client = gcs_client or RealGCSClient()
            db_gateway = db_gateway or HttpDatabaseGateway(settings.database_api_url, settings.internal_api_token)
    elif settings.ingestion_adapter_mode == "disabled":
        pass
    else:
        raise RuntimeError("invalid INGESTION_ADAPTER_MODE")

    app = FastAPI(title="Waimea WaveCast - Ingestion Service",
                  description="NOAA NDBC 51201 ingestion: backfill, poll, reconcile")
    app.state.noaa_client = noaa_client or NOAAClient()
    app.state.gcs_client = gcs_client
    app.state.db_gateway = db_gateway
    app.state.bucket = settings.gcs_raw_bucket or "test-bucket"

    def internal_auth(x_internal_token: str | None = Header(default=None)):
        if not settings.internal_api_token or not x_internal_token or not secrets.compare_digest(x_internal_token, settings.internal_api_token):
            raise HTTPException(401, "invalid credentials")
        if app.state.db_gateway is None or app.state.gcs_client is None:
            raise HTTPException(503, "ingestion dependencies unavailable")

    @app.get("/health/live")
    @app.get("/health")
    def health() -> dict:
        return {"service": "ingestion", "status": "live"}

    @app.get("/health/ready")
    def ready() -> dict:
        if settings.ingestion_adapter_mode == "disabled":
            raise HTTPException(503, "ingestion disabled")
        if settings.ingestion_adapter_mode == "real":
            try:
                if app.state.db_gateway is None or app.state.gcs_client is None:
                    raise RuntimeError("ingestion configuration missing")
                if not app.state.db_gateway.ready() or not app.state.gcs_client.bucket_accessible(app.state.bucket):
                    raise RuntimeError("dependency not ready")
            except Exception as exc:
                raise HTTPException(503, "ingestion dependencies unavailable") from exc
        return {"service": "ingestion", "status": "ready", "adapter_mode": settings.ingestion_adapter_mode}


    @app.post("/internal/v1/ingestion/backfill", dependencies=[Depends(internal_auth)])
    def trigger_backfill(req: BackfillRequest) -> dict:
        """Trigger historical backfill."""
        report = run_backfill(start_year=req.start_year, end_year=req.end_year,
                              noaa_client=app.state.noaa_client, gcs_client=app.state.gcs_client,
                              db_gateway=app.state.db_gateway, bucket=app.state.bucket)
        return {"start_year": report.start_year, "end_year": report.end_year,
                "success_count": report.success_count, "unavailable_periods": report.unavailable_periods,
                "failed_periods": report.failed_periods, "total_silver_inserted": report.total_silver_inserted}


    @app.post("/internal/v1/ingestion/poll", dependencies=[Depends(internal_auth)])
    def trigger_poll() -> dict:
        """Trigger a manual realtime poll."""
        poll = run_poll(noaa_client=app.state.noaa_client, gcs_client=app.state.gcs_client,
                        db_gateway=app.state.db_gateway, bucket=app.state.bucket)
        return {"polled_at": poll.polled_at.isoformat(), "success": poll.success,
                "is_empty_run": poll.is_empty_run, "silver_inserted": poll.ingest_result.silver_inserted,
                "silver_revised": poll.ingest_result.silver_revised,
                "silver_duplicate": poll.ingest_result.silver_duplicate,
                "silver_skipped": poll.ingest_result.silver_skipped,
                "error": poll.ingest_result.error}


    @app.post("/internal/v1/ingestion/reconcile", dependencies=[Depends(internal_auth)])
    def trigger_reconcile(lookback_months: int = 3) -> dict:
        """Trigger archive reconciliation."""
        report = run_reconcile(lookback_months=lookback_months,
                               noaa_client=app.state.noaa_client, gcs_client=app.state.gcs_client,
                               db_gateway=app.state.db_gateway, bucket=app.state.bucket)
        return {"months_checked": report.months_checked,
                "corrections_applied": report.corrections_applied}

    return app


app = create_app()
