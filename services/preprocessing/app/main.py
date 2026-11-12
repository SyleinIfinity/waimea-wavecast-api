"""Authenticated production preprocessing through the internal Database API."""
from __future__ import annotations

import secrets
from datetime import date, datetime, timedelta, timezone

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel
from pydantic_settings import BaseSettings

from packages.db.http import HttpDatabaseGateway
from services.preprocessing.app.jobs.quality import review_range
from services.preprocessing.app.jobs.rebuild_days import rebuild_daily_features
from services.preprocessing.app.jobs.gold import rebuild_gold
from services.preprocessing.app.jobs.gaps import reconcile_recent, reconcile_window


class Settings(BaseSettings):
    app_env: str = "development"
    database_api_url: str = "http://database-api:8000"
    internal_api_token: str = ""


class RangeRequest(BaseModel):
    start: datetime
    end: datetime


class RebuildDaysRequest(BaseModel):
    start_hst: date
    end_hst: date
    expected_sample_count: int | None = None


class GoldRequest(BaseModel):
    year: int
    changed_from: datetime | None = None
    changed_to: datetime | None = None


class GapRequest(BaseModel):
    start: datetime | None = None
    end: datetime | None = None


def create_app(settings: Settings | None = None, gateway=None) -> FastAPI:
    settings = settings or Settings()
    if gateway is None and settings.internal_api_token:
        gateway = HttpDatabaseGateway(settings.database_api_url, settings.internal_api_token)
    app = FastAPI(title="Waimea WaveCast - Preprocessing Service")
    app.state.gateway = gateway

    def auth(x_internal_token: str | None = Header(default=None)):
        if not settings.internal_api_token or not x_internal_token or not secrets.compare_digest(x_internal_token, settings.internal_api_token):
            raise HTTPException(401, "invalid credentials")

    @app.get("/health/live")
    @app.get("/health")
    def live():
        return {"service": "preprocessing", "status": "live"}

    @app.get("/health/ready")
    def ready():
        try:
            if gateway is None or not gateway.ready():
                raise RuntimeError("database gateway unavailable")
        except Exception as exc:
            raise HTTPException(503, "preprocessing dependency unavailable") from exc
        return {"service": "preprocessing", "status": "ready"}

    @app.post("/internal/v1/preprocessing/quality/review", dependencies=[Depends(auth)])
    def review(req: RangeRequest):
        if req.start.tzinfo is None or req.end.tzinfo is None or req.end <= req.start or (req.end-req.start).days > 30:
            raise HTTPException(422, "UTC-aware range of at most 30 days required")
        if gateway is None:
            raise HTTPException(503, "database gateway unavailable")
        return review_range(gateway, req.start.astimezone(timezone.utc), req.end.astimezone(timezone.utc))

    @app.post("/internal/v1/preprocessing/daily/rebuild", dependencies=[Depends(auth)])
    def rebuild(req: RebuildDaysRequest):
        if req.end_hst < req.start_hst or (req.end_hst-req.start_hst).days > 30:
            raise HTTPException(422, "range of at most 31 days required")
        if gateway is None:
            raise HTTPException(503, "database gateway unavailable")
        report = rebuild_daily_features(req.start_hst, req.end_hst, db_gateway=gateway,
                                        expected_sample_count=req.expected_sample_count)
        return {"days_processed": report.days_processed, "days_empty": report.days_empty,
                "total_upserted": report.total_upserted}

    @app.post("/internal/v1/preprocessing/gold/rebuild", dependencies=[Depends(auth)])
    def gold(req: GoldRequest):
        if gateway is None:
            raise HTTPException(503, "database gateway unavailable")
        return rebuild_gold(gateway, year=req.year,
                            changed_from=req.changed_from, changed_to=req.changed_to)

    @app.post("/internal/v1/preprocessing/gaps/reconcile", dependencies=[Depends(auth)])
    def gaps(req: GapRequest):
        if gateway is None:
            raise HTTPException(503, "database gateway unavailable")
        if req.start is None and req.end is None:
            return reconcile_recent(gateway)
        if req.start is None or req.end is None or req.start.tzinfo is None or req.end.tzinfo is None or req.end <= req.start or req.end-req.start > timedelta(days=8):
            raise HTTPException(422, "UTC-aware gap window of at most 8 days required")
        return reconcile_window(gateway, req.start, req.end)

    return app


app = create_app()
