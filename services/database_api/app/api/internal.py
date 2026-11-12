from datetime import date, datetime

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request

from services.database_api.app.repositories import bronze, gold, models, silver
from services.database_api.app.repositories.common import json_safe
from services.database_api.app.schemas.analytics import GoldBatch
from services.database_api.app.schemas.common import JobCreate, JobUpdate
from services.database_api.app.schemas.ml import ClassificationSchemeIn, ClusterCentroidIn, DatasetSplitIn, EvaluationIn, EvaluationSampleIn, ModelIn, TrainingRunIn
from services.database_api.app.schemas.observations import DailyFeatures, GapIn, GapReconcileIn, IngestionRunIn, IngestionRunUpdate, QualityIssueIn, QualityBatch, RawBatch, SilverBatch, SourceFileIn
from services.database_api.app.security import internal_auth


router = APIRouter(prefix="/internal/v1", tags=["internal"], dependencies=[Depends(internal_auth)])


def connection(request: Request):
    return request.app.state.db.connection()


def key_required(idempotency_key: str | None) -> str:
    if not idempotency_key or len(idempotency_key) > 128:
        raise HTTPException(422, "Idempotency-Key header required (max 128 characters)")
    return idempotency_key


@router.post("/source-files")
def register_source_file(request: Request, body: SourceFileIn, idempotency_key: str | None = Header(default=None)):
    key_required(idempotency_key)
    if not body.gcs_uri and not body.local_path:
        raise HTTPException(422, "a storage location is required")
    try:
        with connection(request) as conn:
            return bronze.register_source_file(conn, body)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get("/source-files/by-gcs")
def source_file_by_gcs(request: Request, uri: str, generation: int):
    if not uri.startswith("gs://waimea-noaa/noaa/raw/") or generation <= 0:
        raise HTTPException(422, "invalid pinned raw object")
    with connection(request) as conn:
        row = conn.execute(
            "SELECT f.file_id,f.source_row_count,"
            "(SELECT count(*) FROM bronze.raw_observations r WHERE r.file_id=f.file_id) AS raw_count "
            "FROM bronze.source_files f WHERE f.gcs_uri=%s AND f.gcs_generation=%s LIMIT 1",
            (uri, str(generation)),
        ).fetchone()
    return {"found": row is not None, "file_id": row["file_id"] if row else None,
            "source_row_count": row["source_row_count"] if row else None,
            "raw_count": row["raw_count"] if row else None}


@router.post("/raw-observations/bulk")
def upsert_raw_batch(request: Request, body: RawBatch, idempotency_key: str | None = Header(default=None)):
    key_required(idempotency_key)
    if len(body.rows) > request.app.state.settings.max_batch_size:
        raise HTTPException(422, "batch too large")
    with connection(request) as conn:
        return bronze.upsert_raw_batch(conn, body)


@router.post("/ingestion-runs", status_code=201)
def create_ingestion_run(request: Request, body: IngestionRunIn, idempotency_key: str | None = Header(default=None)):
    key = key_required(idempotency_key)
    with connection(request) as conn:
        conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (f"ingest:{key}",))
        existing = conn.execute("SELECT run_id FROM bronze.ingestion_runs WHERE details->>'idempotency_key'=%s ORDER BY run_id DESC LIMIT 1", (key,)).fetchone()
        if existing:
            return existing
        return bronze.create_ingestion_run(conn, body, key)


@router.patch("/ingestion-runs/{run_id}")
def update_ingestion_run(request: Request, run_id: int, body: IngestionRunUpdate):
    with connection(request) as conn:
        result = bronze.update_ingestion_run(conn, run_id, body)
    if result is None:
        raise HTTPException(404, "ingestion run not found")
    return result


@router.post("/silver/observations/bulk-upsert")
def upsert_silver_batch(request: Request, body: SilverBatch, idempotency_key: str | None = Header(default=None)):
    key_required(idempotency_key)
    if len(body.rows) > request.app.state.settings.max_batch_size:
        raise HTTPException(422, "batch too large")
    with connection(request) as conn:
        return silver.upsert_silver_batch(conn, body)


@router.get("/silver/observations")
def get_observations(request: Request, start: datetime, end: datetime, limit: int = Query(500, ge=1, le=1000), after: datetime | None = None):
    if start.tzinfo is None or end.tzinfo is None or end < start or (end - start).days > request.app.state.settings.max_query_days:
        raise HTTPException(422, "invalid UTC time window")
    with connection(request) as conn:
        rows = conn.execute("SELECT * FROM silver.wave_observations WHERE station_id='51201' AND observed_at >= %s AND observed_at < %s AND (%s::timestamptz IS NULL OR observed_at > %s) ORDER BY observed_at LIMIT %s", (start, end, after, after, limit)).fetchall()
    return {"data": json_safe(rows)}


@router.patch("/silver/observations/quality")
def apply_quality(request: Request, body: QualityBatch, idempotency_key: str | None = Header(default=None)):
    key_required(idempotency_key)
    if len(body.rows) > request.app.state.settings.max_batch_size:
        raise HTTPException(422, "batch too large")
    with connection(request) as conn:
        return silver.apply_quality_batch(conn, body)


@router.put("/silver/daily-features")
def upsert_daily_features(request: Request, body: DailyFeatures, idempotency_key: str | None = Header(default=None)):
    key_required(idempotency_key)
    with connection(request) as conn:
        return silver.upsert_daily_features(conn, body)


@router.post("/silver/quality-issues", status_code=201)
def save_quality_issue(request: Request, body: QualityIssueIn, idempotency_key: str | None = Header(default=None)):
    key = key_required(idempotency_key)
    with connection(request) as conn:
        conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (f"qc:{key}",))
        return silver.save_quality_issue(conn, body, key)


@router.post("/silver/gaps")
def save_gap(request: Request, body: GapIn, idempotency_key: str | None = Header(default=None)):
    key_required(idempotency_key)
    with connection(request) as conn:
        return silver.save_gap(conn, body) or {"duplicate": True}


@router.post("/silver/gaps/reconcile")
def reconcile_gaps(request: Request, body: GapReconcileIn):
    from datetime import timedelta
    with connection(request) as conn:
        conn.execute("SELECT pg_advisory_xact_lock(hashtextextended('tl07b:51201:gaps',0))")
        resolved = conn.execute(
            "UPDATE silver.observation_gaps SET resolved_at=now() WHERE station_id='51201' "
            "AND gap_start_at>%s AND gap_start_at<%s AND resolved_at IS NULL "
            "AND NOT (gap_start_at=ANY(%s::timestamptz[])) RETURNING gap_id",
            (body.window_start, body.window_end, body.missing_slots),
        ).fetchall()
        for slot in body.missing_slots:
            conn.execute(
                "INSERT INTO silver.observation_gaps(station_id,gap_start_at,gap_end_at,assumed_interval_min,estimated_missing_slots,notes) "
                "VALUES ('51201',%s,%s,%s,1,'source_gap_candidate') "
                "ON CONFLICT (station_id,gap_start_at,gap_end_at) DO UPDATE SET resolved_at=NULL",
                (slot, slot + timedelta(minutes=body.cadence_minutes), body.cadence_minutes),
            )
    return {"open": len(body.missing_slots), "resolved": len(resolved)}


@router.get("/silver/daily-features")
def get_daily_features(request: Request, start_hst: date, end_hst: date, limit: int = Query(500, ge=1, le=1000)):
    if end_hst < start_hst or (end_hst - start_hst).days > request.app.state.settings.max_query_days:
        raise HTTPException(422, "invalid HST date window")
    with connection(request) as conn:
        rows = conn.execute("SELECT * FROM silver.daily_wave_features WHERE station_id='51201' AND date_hst BETWEEN %s AND %s ORDER BY date_hst LIMIT %s", (start_hst, end_hst, limit)).fetchall()
    return {"data": json_safe(rows)}


@router.post("/gold/results/bulk")
def save_gold_results(request: Request, body: GoldBatch, idempotency_key: str | None = Header(default=None)):
    key_required(idempotency_key)
    if len(body.rows) > request.app.state.settings.max_batch_size:
        raise HTTPException(422, "batch too large")
    try:
        with connection(request) as conn:
            return gold.save_gold_results(conn, body)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post("/models")
def save_model_metadata(request: Request, body: ModelIn, idempotency_key: str | None = Header(default=None)):
    key_required(idempotency_key)
    if body.status != "draft":
        raise HTTPException(422, "new models must start as draft; use promotion after evaluation")
    with connection(request) as conn:
        return models.save_model_metadata(conn, body)


@router.post("/models/{model_id}/promote")
def promote_model(request: Request, model_id: int, idempotency_key: str | None = Header(default=None)):
    key_required(idempotency_key)
    with connection(request) as conn:
        model = conn.execute("SELECT model_id, task_type, status, artifact_generation, artifact_sha256 FROM gold.model_registry WHERE model_id=%s FOR UPDATE", (model_id,)).fetchone()
        if model is None:
            raise HTTPException(404, "model not found")
        if model["status"] == "retired":
            raise HTTPException(409, "retired model cannot be promoted")
        if not model["artifact_generation"] or not model["artifact_sha256"]:
            raise HTTPException(422, "verified artifact generation and SHA-256 required")
        stages = ("test", "validation", "train") if model["task_type"] == "clustering" else ("test",)
        evaluated = conn.execute("SELECT 1 FROM gold.model_evaluations e JOIN gold.training_runs t USING(training_run_id) WHERE e.model_id=%s AND e.evaluation_stage=ANY(%s) AND t.status='success' LIMIT 1", (model_id, list(stages))).fetchone()
        if not evaluated:
            raise HTTPException(422, "successful training and test evaluation required")
        conn.execute("UPDATE gold.model_registry SET status='ready', promoted_at=now(), updated_at=now() WHERE model_id=%s", (model_id,))
    return {"model_id": model_id, "status": "ready"}


@router.get("/models/{model_id}")
def get_model_metadata(request: Request, model_id: int):
    with connection(request) as conn:
        result = models.get_model_metadata(conn, model_id)
    if result is None:
        raise HTTPException(404, "model not found")
    return json_safe(result)


@router.post("/ml/training-runs")
def save_training_run(request: Request, body: TrainingRunIn, idempotency_key: str | None = Header(default=None)):
    key = key_required(idempotency_key)
    body.run_name = body.run_name or f"idempotency:{key}"
    with connection(request) as conn:
        conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (f"training:{body.run_name}",))
        return models.save_training_run(conn, body)


@router.post("/ml/dataset-splits")
def save_dataset_split(request: Request, body: DatasetSplitIn, idempotency_key: str | None = Header(default=None)):
    key_required(idempotency_key)
    with connection(request) as conn:
        return models.save_dataset_split(conn, body)


@router.post("/ml/evaluations")
def save_evaluation(request: Request, body: EvaluationIn, idempotency_key: str | None = Header(default=None)):
    key_required(idempotency_key)
    with connection(request) as conn:
        return models.save_evaluation(conn, body)


@router.post("/ml/evaluation-samples")
def save_evaluation_sample(request: Request, body: EvaluationSampleIn, idempotency_key: str | None = Header(default=None)):
    key_required(idempotency_key)
    with connection(request) as conn:
        return models.save_evaluation_sample(conn, body)


@router.put("/ml/cluster-centroids")
def save_cluster_centroid(request: Request, body: ClusterCentroidIn, idempotency_key: str | None = Header(default=None)):
    key_required(idempotency_key)
    with connection(request) as conn:
        return models.save_cluster_centroid(conn, body)


@router.post("/ml/classification-schemes")
def save_classification_scheme(request: Request, body: ClassificationSchemeIn, idempotency_key: str | None = Header(default=None)):
    key_required(idempotency_key)
    with connection(request) as conn:
        return models.save_classification_scheme(conn, body)


@router.post("/jobs", status_code=201)
def create_job(request: Request, body: JobCreate, idempotency_key: str | None = Header(default=None)):
    key = key_required(idempotency_key)
    with connection(request) as conn:
        conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (f"job-key:{key}",))
        conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (f"job-scope:{body.job_type}:{body.input_period_start}:{body.input_period_end}",))
        existing = conn.execute("SELECT processing_run_id, status FROM gold.processing_runs WHERE parameters->>'idempotency_key'=%s ORDER BY processing_run_id DESC LIMIT 1", (key,)).fetchone()
        if existing:
            return existing
        active = conn.execute("SELECT processing_run_id FROM gold.processing_runs WHERE job_type=%s AND status='running' AND input_period_start IS NOT DISTINCT FROM %s AND input_period_end IS NOT DISTINCT FROM %s LIMIT 1", (body.job_type, body.input_period_start, body.input_period_end)).fetchone()
        if active:
            raise HTTPException(409, {"running_job_id": active["processing_run_id"]})
        from services.database_api.app.repositories.common import insert_row
        values = body.model_dump(exclude_none=True)
        values["parameters"] = {**body.parameters, "idempotency_key": key}
        return insert_row(conn, "gold", "processing_runs", values, returning="processing_run_id").fetchone()


@router.patch("/jobs/{job_id}")
def save_job_status(request: Request, job_id: int, body: JobUpdate):
    with connection(request) as conn:
        result = gold.save_job_status(conn, job_id, body.status, body.output_record_count, body.error_message)
    if result is None:
        raise HTTPException(404, "job not found")
    return result


@router.get("/jobs/{job_id}")
def get_job_status(request: Request, job_id: int):
    with connection(request) as conn:
        result = gold.get_job_status(conn, job_id)
    if result is None:
        raise HTTPException(404, "job not found")
    return json_safe(result)
