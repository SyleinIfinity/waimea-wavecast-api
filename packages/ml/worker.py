"""Authenticated TL05 workers with background execution and job IDs."""
from __future__ import annotations

import logging
import secrets
from datetime import date, timedelta
from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, Header, HTTPException
from pydantic import BaseModel, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from packages.gcs.client import RealGCSClient
from packages.ml.artifacts import ModelStore
from packages.ml.gateway import HttpMLGateway
from packages.ml.jobs import JOB_TYPES, run_analysis, run_inference

logger = logging.getLogger(__name__)


class WorkerSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=Path(__file__).resolve().parents[2] / ".env", extra="ignore")
    internal_api_token: str = ""
    database_api_url: str = "http://database-api:8000"
    gcs_models_bucket: str = ""


class RunRequest(BaseModel):
    station_id: str = "51201"
    from_hst: date
    to_hst: date
    pattern_type: str | None = None
    retrain: bool = False

    @model_validator(mode="after")
    def valid(self):
        if self.station_id != "51201" or self.to_hst < self.from_hst or (self.to_hst - self.from_hst).days > 30 or self.retrain:
            raise ValueError("invalid station, date window, or retrain flag")
        return self


ROUTES = {"analysis": ("/internal/v1/analysis/rebuild", {"time_series_metrics", "trend_seasonal_components", "correlations"}),
          "clustering": ("/internal/v1/clustering/assign", {"cluster_assignments"}),
          "prediction": ("/internal/v1/prediction/run", {"predictions"}),
          "classification": ("/internal/v1/classification/run", {"classifications"})}


def create_worker(name: str, settings: WorkerSettings | None = None) -> FastAPI:
    path, allowed = ROUTES[name]
    settings = settings or WorkerSettings()
    app = FastAPI(title=f"Waimea WaveCast {name}")
    app.state.gateway_factory = lambda: HttpMLGateway(settings.database_api_url, settings.internal_api_token)
    app.state.store_factory = lambda: ModelStore(RealGCSClient(), settings.gcs_models_bucket)

    @app.get("/health")
    def health():
        return {"service": name, "status": "up", "pipeline_implemented": True}

    def execute(gateway, body: RunRequest, key: str, kind: str, outer_id: int):
        try:
            if name == "analysis":
                run_analysis(gateway, kind, body.from_hst, body.to_hst, key)
            else:
                task = {"predictions": "regression", "classifications": "classification", "cluster_assignments": "clustering"}[kind]
                store = app.state.store_factory() if gateway.get_ready_model(task) is not None else None
                current = body.from_hst
                saved = 0
                while current <= body.to_hst:
                    cutoff = current if name == "clustering" else current - timedelta(days=1)
                    result = run_inference(gateway, store, kind, cutoff, f"{key}:{current}")
                    saved += result.get("saved", 0)
                    current += timedelta(days=1)
                gateway.update_job(outer_id, "success" if saved else "skipped", saved)
        except Exception:
            logger.exception("TL05 worker failed: %s", name)
            gateway.update_job(outer_id, "failed", error="worker_failed")

    @app.post(path, status_code=202)
    def submit(body: RunRequest, background: BackgroundTasks,
               x_internal_token: str | None = Header(default=None),
               idempotency_key: str | None = Header(default=None)):
        if not settings.internal_api_token or not x_internal_token or not secrets.compare_digest(x_internal_token, settings.internal_api_token):
            raise HTTPException(401, "invalid credentials")
        if not idempotency_key or len(idempotency_key) > 128:
            raise HTTPException(422, "Idempotency-Key required")
        kind = body.pattern_type or ("time_series_metrics" if name == "analysis" else next(iter(allowed)))
        if kind not in allowed:
            raise HTTPException(422, "invalid pattern type")
        try:
            gateway = app.state.gateway_factory()
            result = gateway.create_job({"job_type": JOB_TYPES[kind], "input_period_start": body.from_hst.isoformat(),
                "input_period_end": body.to_hst.isoformat(), "algorithm_version": "tl05-v1",
                "parameters": {"idempotency_key": idempotency_key, "pattern_type": kind}}, idempotency_key)
        except Exception as exc:
            raise HTTPException(503, "database API unavailable") from exc
        job_id = result["processing_run_id"]
        if not result.get("duplicate"):
            background.add_task(execute, gateway, body, idempotency_key, kind, job_id)
        return {"processing_run_id": job_id, "status": "accepted", "duplicate": bool(result.get("duplicate"))}

    return app
