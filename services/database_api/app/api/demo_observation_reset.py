"""Back up, reset, and restore observation-derived records."""
from __future__ import annotations

import secrets
import hashlib
import time
from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from psycopg import sql
from pydantic import BaseModel, SecretStr

from packages.gcs.client import RealGCSClient
from services.database_api.app.db.observation_backups import (
    BackupError, RESET_TABLES, TABLE_SCOPE, counts, describe_backup, find_backup,
    load_backup, restore_backup, save_backup,
)
from services.database_api.app.security import admin_auth

router = APIRouter(prefix="/api/v1/admin/demo/observations", tags=["admin"],
                   dependencies=[Depends(admin_auth)])
private_router = APIRouter(prefix="/private/admin/observations", tags=["observation backups"],
                           dependencies=[Depends(admin_auth)])

LEGACY_PREVIEW = "observation_reset"
BACKUP_PREVIEW = "observation_backup_reset"


class ResetConfirmation(BaseModel):
    dry_run_id: str
    confirmation: str
    password: SecretStr | None = None


class RestoreConfirmation(BaseModel):
    confirmation: str
    password: SecretStr | None = None
    backup_id: UUID | None = None


def _reset_environment(request: Request, *, automatic_backup: bool = False) -> str:
    settings = request.app.state.settings
    if (settings.app_env == "staging" and settings.demo_delete_enabled
            and (automatic_backup or settings.demo_backup_confirmed)
            and settings.db_name == "waimea_rehearsal"
            and settings.db_host == "waimea-rehearsal-postgres-local-1"):
        return "staging"
    if (settings.app_env == "production" and settings.observation_reset_production_enabled
            and (automatic_backup or settings.observation_reset_production_backup_confirmed)
            and settings.db_name == "waimea_wavecast"
            and settings.db_host == "dbwaimea.liems.io.vn"):
        return "production"
    raise HTTPException(403, "observation reset disabled for this database")


def _require_password(request: Request, environment: str, password: SecretStr | None) -> None:
    if environment != "production":
        return
    expected_hash = request.app.state.settings.observation_reset_password_sha256
    supplied = password.get_secret_value() if password else ""
    supplied_hash = hashlib.sha256(supplied.encode()).hexdigest()
    if not (len(expected_hash) == 64 and supplied
            and secrets.compare_digest(supplied_hash, expected_hash.lower())):
        raise HTTPException(403, "invalid reset password")


def _backup_store(request: Request) -> tuple[RealGCSClient, str]:
    bucket = request.app.state.settings.gcs_backup_bucket.strip()
    if not bucket:
        raise BackupError("observation backup bucket is not configured")
    try:
        return RealGCSClient(), bucket
    except Exception as exc:
        raise BackupError("observation backup storage is unavailable") from exc


def _lock_scope(conn) -> None:
    conn.execute(sql.SQL("LOCK TABLE {} IN ACCESS EXCLUSIVE MODE").format(TABLE_SCOPE))


def _preview(request: Request, kind: str):
    environment = _reset_environment(request, automatic_backup=kind == BACKUP_PREVIEW)
    with request.app.state.db.connection() as conn:
        before = counts(conn)
    token = secrets.token_urlsafe(32)
    request.app.state.previews[token] = (time.monotonic() + 300, kind, before, environment)
    return {"dry_run_id": token, "affected_tables": before,
            "row_count": sum(before.values()), "expires_in_seconds": 300,
            "environment": environment,
            "preserved": ["bronze.stations", "bronze.source_feeds",
                          "bronze.measurement_definitions", "bronze.source_files",
                          "gold.model_registry", "gold.cluster_centroids", "gold.training_runs", "gold.model_evaluations",
                          "gold.dataset_splits", "gold.dataset_exports"]}


@router.post("/preview-reset", summary="Preview reset with a manual backup")
def preview_reset(request: Request):
    return _preview(request, LEGACY_PREVIEW)


@private_router.post("/preview-reset", summary="Preview reset with automatic GCS backup")
def preview_backup_reset(request: Request):
    return _preview(request, BACKUP_PREVIEW)


def _reset(request: Request, body: ResetConfirmation, kind: str):
    automatic_backup = kind == BACKUP_PREVIEW
    environment = _reset_environment(request, automatic_backup=automatic_backup)
    _require_password(request, environment, body.password)
    confirmation = "RESET PRODUCTION OBSERVATIONS" if environment == "production" else "RESET LOCAL OBSERVATIONS"
    if body.confirmation != confirmation:
        raise HTTPException(422, "invalid confirmation")
    preview = request.app.state.previews.pop(body.dry_run_id, None)
    if preview is None or preview[0] < time.monotonic() or preview[1] != kind or preview[3] != environment:
        raise HTTPException(409, "preview expired or invalid")
    backup = None
    try:
        with request.app.state.db.connection() as conn:
            _lock_scope(conn)
            before = counts(conn)
            if before != preview[2]:
                raise HTTPException(409, "data changed after preview")
            if automatic_backup:
                if not any(before.values()):
                    return {"status": "already_empty", "deleted": before, "row_count": 0, "backup": None}
                gcs, bucket = _backup_store(request)
                backup = save_backup(conn, gcs, bucket, environment, before, "reset")
            conn.execute(sql.SQL("TRUNCATE TABLE {}").format(TABLE_SCOPE))
            deleted_at = datetime.now(timezone.utc)
    except BackupError as exc:
        raise HTTPException(503, str(exc)) from exc
    # Rebuilding here would race the next NOAA ingestion and find empty Silver.
    # The cleared cycle ledger makes the scheduler run the recovery pipeline.
    model_id = request.app.state.settings.cluster_rebuild_model_id
    result = {"status": "reset", "deleted_at_utc": deleted_at.isoformat(),
              "deleted": before, "row_count": sum(before.values()),
              "kmeans_recovery": {"status": "scheduled" if model_id > 0 else "disabled",
                                  "model_id": model_id or None}}
    if backup is not None:
        result["backup"] = describe_backup(backup)
    return result


@router.post("/reset", summary="Reset observations after a confirmed manual backup")
def reset(request: Request, body: ResetConfirmation):
    return _reset(request, body, LEGACY_PREVIEW)


@private_router.post("/backup-and-reset", summary="Save and verify a GCS snapshot, then reset observations")
def backup_and_reset(request: Request, body: ResetConfirmation):
    return _reset(request, body, BACKUP_PREVIEW)


@private_router.post("/restore-latest", summary="Restore the latest automatic reset snapshot")
def restore_latest(request: Request, body: RestoreConfirmation):
    environment = _reset_environment(request, automatic_backup=True)
    _require_password(request, environment, body.password)
    if body.confirmation != "RESTORE LATEST OBSERVATIONS":
        raise HTTPException(422, "invalid restore confirmation")

    safety_backup = None
    try:
        with request.app.state.db.connection() as conn:
            _lock_scope(conn)
            backup = find_backup(conn, environment, body.backup_id)
            if backup is None:
                raise HTTPException(404, "observation backup not found")
            gcs, bucket = _backup_store(request)
            data = load_backup(gcs, backup)
            current = counts(conn)
            if any(current.values()):
                safety_backup = save_backup(conn, gcs, bucket, environment, current, "pre_restore")
            restore_backup(conn, data, backup["row_counts"])
    except BackupError as exc:
        raise HTTPException(503, str(exc)) from exc
    return {
        "status": "restored",
        "environment": environment,
        "backup": describe_backup(backup),
        "safety_backup": describe_backup(safety_backup) if safety_backup else None,
        "restored": backup["row_counts"],
        "row_count": sum(backup["row_counts"].values()),
    }
