import logging
import secrets
import time

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException, Request

from services.database_api.app.repositories.common import json_safe
from services.database_api.app.schemas.analytics import DeleteConfirmation, DemoScope
from services.database_api.app.security import admin_auth


router = APIRouter(prefix="/api/v1/admin", tags=["admin"], dependencies=[Depends(admin_auth)])
logger = logging.getLogger(__name__)

PATTERN_DATES = {
    "time_series_metrics": "period_start_hst",
    "trend_seasonal_components": "period_start_hst",
    "correlations": "date_start_hst",
    "cluster_assignments": "date_hst",
    "predictions": "target_date_hst",
    "classifications": "target_date_hst",
}
WORKERS = {
    "time_series_metrics": ("analysis_worker_url", "/internal/v1/analysis/rebuild"),
    "trend_seasonal_components": ("analysis_worker_url", "/internal/v1/analysis/rebuild"),
    "correlations": ("analysis_worker_url", "/internal/v1/analysis/rebuild"),
    "cluster_assignments": ("clustering_worker_url", "/internal/v1/clustering/assign"),
    "predictions": ("prediction_worker_url", "/internal/v1/prediction/run"),
    "classifications": ("classification_worker_url", "/internal/v1/classification/run"),
}


def check_scope(request: Request, scope: DemoScope):
    if scope.to_hst < scope.from_hst or (scope.to_hst - scope.from_hst).days > request.app.state.settings.demo_max_days:
        raise HTTPException(422, "invalid demo window")
    if len(scope.pattern_types) != len(set(scope.pattern_types)):
        raise HTTPException(422, "duplicate pattern type")


def deletion_allowed(request: Request) -> bool:
    settings = request.app.state.settings
    return settings.app_env == "staging" and settings.demo_delete_enabled and settings.demo_backup_confirmed


def counts(conn, scope: DemoScope) -> dict[str, int]:
    result = {}
    for table in scope.pattern_types:
        # Every table and date column comes from fixed server-side allowlists.
        column = PATTERN_DATES[table]
        result[table] = conn.execute(
            f"SELECT count(*) AS n FROM gold.{table} WHERE station_id=%s AND {column} BETWEEN %s AND %s",
            (scope.station_id, scope.from_hst, scope.to_hst),
        ).fetchone()["n"]
    return result


@router.get("/demo/preflight")
def preflight(request: Request):
    with request.app.state.db.connection() as conn:
        model_count = conn.execute("SELECT count(*) AS n FROM gold.model_registry WHERE status='ready'").fetchone()["n"]
    return {"environment": request.app.state.settings.app_env, "database_ready": True, "ready_model_count": model_count, "allow_delete": deletion_allowed(request)}


@router.post("/demo/patterns/preview-delete")
def preview_delete(request: Request, scope: DemoScope):
    check_scope(request, scope)
    if not deletion_allowed(request):
        raise HTTPException(403, "demo deletion disabled")
    with request.app.state.db.connection() as conn:
        before = counts(conn, scope)
    token = secrets.token_urlsafe(32)
    request.app.state.previews[token] = (time.monotonic() + 300, scope, before)
    return {"dry_run_id": token, "affected_tables": before, "row_count": sum(before.values()), "protected_tables": ["bronze.*", "silver.*", "gold.model_registry", "gold.training_runs", "gold.model_evaluations"], "expires_in_seconds": 300, "safe_to_delete": True}


@router.post("/demo/patterns/delete")
def delete_patterns(request: Request, body: DeleteConfirmation, x_actor_id: str | None = Header(default=None)):
    if not deletion_allowed(request):
        raise HTTPException(403, "demo deletion disabled")
    if body.confirmation != "DELETE GOLD DEMO" or not x_actor_id:
        raise HTTPException(422, "confirmation and X-Actor-ID required")
    preview = request.app.state.previews.pop(body.dry_run_id, None)
    if preview is None or preview[0] < time.monotonic():
        raise HTTPException(409, "preview expired or already used")
    _, scope, expected = preview
    with request.app.state.db.connection() as conn:
        for table in sorted(scope.pattern_types):
            conn.execute(f"LOCK TABLE gold.{table} IN SHARE ROW EXCLUSIVE MODE")
        current = counts(conn, scope)
        if current != expected:
            raise HTTPException(409, "data changed after preview")
        deleted = {}
        for table in sorted(scope.pattern_types):
            column = PATTERN_DATES[table]
            result = conn.execute(
                f"DELETE FROM gold.{table} WHERE station_id=%s AND {column} BETWEEN %s AND %s",
                (scope.station_id, scope.from_hst, scope.to_hst),
            )
            deleted[table] = result.rowcount
    logger.warning("gold_demo_delete actor=%s station=%s from=%s to=%s counts=%s", x_actor_id, scope.station_id, scope.from_hst, scope.to_hst, deleted)
    return {"deleted": deleted, "station_id": scope.station_id, "from_hst": scope.from_hst, "to_hst": scope.to_hst}


@router.post("/demo/patterns/rebuild", status_code=202)
def rebuild(request: Request, scope: DemoScope, idempotency_key: str | None = Header(default=None)):
    check_scope(request, scope)
    if not idempotency_key:
        raise HTTPException(422, "Idempotency-Key required")
    settings = request.app.state.settings
    if not settings.internal_api_token:
        raise HTTPException(503, "worker auth unavailable")
    accepted = []
    for table in dict.fromkeys(scope.pattern_types):
        base_attr, path = WORKERS[table]
        url = getattr(settings, base_attr).rstrip("/") + path
        payload = {"station_id": scope.station_id, "from_hst": scope.from_hst.isoformat(), "to_hst": scope.to_hst.isoformat(), "pattern_type": table, "retrain": False}
        try:
            response = httpx.post(url, json=payload, headers={"X-Internal-Token": settings.internal_api_token, "Idempotency-Key": f"{idempotency_key}:{table}"}, timeout=10)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            logger.error("worker_rebuild_failed pattern=%s error=%s", table, type(exc).__name__)
            raise HTTPException(503, {"accepted": accepted, "failed_pattern": table}) from exc
        accepted.append({"pattern_type": table, "worker_response": response.json()})
    return {"status": "accepted", "jobs": accepted}


@router.get("/jobs/{job_id}")
def admin_job(request: Request, job_id: int):
    with request.app.state.db.connection() as conn:
        row = conn.execute("SELECT * FROM gold.processing_runs WHERE processing_run_id=%s", (job_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "job not found")
    return json_safe(row)
