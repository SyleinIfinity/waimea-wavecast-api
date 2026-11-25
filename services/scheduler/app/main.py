"""Dependency-aware internal dispatch. Training is never scheduled here."""
from __future__ import annotations

import os
import secrets
import threading
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import httpx
from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from pathlib import Path


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=Path(__file__).resolve().parents[3] / ".env", extra="ignore")
    internal_api_token: str = ""
    ingestion_worker_url: str = "http://ingestion:8000"
    analysis_worker_url: str = "http://analysis:8000"
    clustering_worker_url: str = "http://clustering:8000"
    prediction_worker_url: str = "http://prediction:8000"
    classification_worker_url: str = "http://classification:8000"


class DispatchRequest(BaseModel):
    phase: str = Field(pattern="^(poll|analysis|inference|rebuild)$")
    from_hst: date
    to_hst: date


settings = Settings()
app = FastAPI(title="Waimea WaveCast scheduler")
app.state.http_client_factory = lambda: httpx.Client(timeout=15)
_lock = threading.Lock()

WORKERS = {
    "time_series_metrics": ("analysis_worker_url", "/internal/v1/analysis/rebuild"),
    "trend_seasonal_components": ("analysis_worker_url", "/internal/v1/analysis/rebuild"),
    "correlations": ("analysis_worker_url", "/internal/v1/analysis/rebuild"),
    "cluster_assignments": ("clustering_worker_url", "/internal/v1/clustering/assign"),
    "predictions": ("prediction_worker_url", "/internal/v1/prediction/run"),
    "classifications": ("classification_worker_url", "/internal/v1/classification/run"),
}


def due_phases(now: datetime) -> list[str]:
    """Half-hour poll; Gold/inference after the HST day ends."""
    local = now.astimezone(ZoneInfo("Pacific/Honolulu"))
    phases = ["poll"] if local.minute in (0, 30) else []
    if local.hour == 2 and local.minute == 0:
        phases.extend(["analysis", "inference"])
    return phases


@app.get("/health")
def health():
    return {"service": "scheduler", "status": "up", "pipeline_implemented": True}


@app.post("/internal/v1/scheduler/dispatch")
def dispatch(body: DispatchRequest, x_internal_token: str | None = Header(default=None),
             idempotency_key: str | None = Header(default=None)):
    if not settings.internal_api_token or not x_internal_token or not secrets.compare_digest(x_internal_token, settings.internal_api_token):
        raise HTTPException(401, "invalid credentials")
    if not idempotency_key or len(idempotency_key) > 128 or body.to_hst < body.from_hst or (body.to_hst - body.from_hst).days > 30:
        raise HTTPException(422, "invalid range or idempotency key")
    if not _lock.acquire(blocking=False):
        raise HTTPException(409, "scheduler already dispatching")
    try:
        headers = {"X-Internal-Token": settings.internal_api_token, "Idempotency-Key": idempotency_key}
        if body.phase == "poll":
            targets = [("ingestion_worker_url", "/internal/v1/ingestion/poll", None, "poll")]
        else:
            kinds = ["time_series_metrics", "correlations"] if body.phase == "analysis" else (
                ["cluster_assignments", "predictions", "classifications"] if body.phase == "inference" else list(WORKERS))
            if body.phase == "analysis" and (body.to_hst + timedelta(days=1)).day == 1:
                kinds.append("trend_seasonal_components")
            targets = [(*WORKERS[kind], {"station_id": "51201", "from_hst": body.from_hst.isoformat(),
                        "to_hst": body.to_hst.isoformat(), "pattern_type": kind, "retrain": False}, kind) for kind in kinds]
        results = []
        with app.state.http_client_factory() as client:
            for attr, path, payload, kind in targets:
                url = getattr(settings, attr).rstrip("/") + path
                try:
                    response = client.post(url, json=payload, headers={**headers, "Idempotency-Key": f"{idempotency_key}:{kind}"})
                    response.raise_for_status()
                    results.append({"kind": kind, "status": response.status_code, "response": response.json()})
                except httpx.HTTPError as exc:
                    raise HTTPException(503, {"accepted": results, "failed": kind}) from exc
        return {"accepted": results}
    finally:
        _lock.release()
