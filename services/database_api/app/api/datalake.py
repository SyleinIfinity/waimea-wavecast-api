"""Internal, bounded source reads and durable GCS export/backfill state."""
from datetime import datetime, timezone
from typing import Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from services.database_api.app.repositories.common import json_safe
from services.database_api.app.security import internal_auth

router = APIRouter(prefix="/internal/v1/datalake", tags=["internal"], dependencies=[Depends(internal_auth)])


class ExportPrepare(BaseModel):
    layer: Literal["bronze", "silver", "gold"]
    partition_key: str = Field(pattern=r"^station=51201/year=[0-9]{4}/month=(0[1-9]|1[0-2])$")
    schema_version: Literal["tl08a-jsonl-v1"] = "tl08a-jsonl-v1"
    content_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    row_count: int = Field(ge=0)
    min_observed_at: datetime | None = None
    max_observed_at: datetime | None = None
    source_watermark: datetime | None = None
    gcs_uri: str
    processing_run_id: str | None = Field(default=None, max_length=80)


class ExportComplete(BaseModel):
    export_id: int = Field(gt=0)
    gcs_generation: int = Field(gt=0)


class ExportFailure(BaseModel):
    export_id: int = Field(gt=0)
    error_class: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_]{0,79}$")


class JobClaim(BaseModel):
    feed_key: Literal["historical_annual", "historical_monthly"]
    source_url: str = Field(max_length=300)
    source_period: str = Field(pattern=r"^20[0-9]{2}(-(?:0[1-9]|1[0-2]))?$")


class JobCheckpoint(BaseModel):
    lease_token: UUID
    status: Literal["RUNNING", "RAW_STORED", "BRONZE_APPLIED", "SILVER_APPLIED", "QC_APPLIED", "GOLD_APPLIED", "VERIFIED", "FAILED"]
    last_batch_offset: int | None = Field(default=None, ge=0)
    source_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    source_row_count: int | None = Field(default=None, ge=0)
    source_etag: str | None = None
    source_last_modified: str | None = None
    source_content_length: int | None = Field(default=None, ge=0)
    gcs_uri: str | None = None
    gcs_generation: int | None = Field(default=None, gt=0)
    last_exported_partition: str | None = None
    error_stage: str | None = None


class JobHeartbeat(BaseModel):
    lease_token: UUID


class JobChecked(BaseModel):
    full_checksum: bool = False
    source_etag: str | None = None
    source_last_modified: str | None = None
    source_content_length: int | None = Field(default=None, ge=0)


class PublicationComplete(BaseModel):
    pending_updated_at: datetime
    manifest_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    manifest_gcs_uri: str
    manifest_generation: int = Field(gt=0)
    coverage_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    coverage_gcs_uri: str
    coverage_generation: int = Field(gt=0)


@router.get("/bronze/raw")
def raw_rows(request: Request, start: datetime, end: datetime, after_raw_id: int = 0,
             limit: int = Query(500, ge=1, le=1000)):
    if start.tzinfo is None or end.tzinfo is None or end <= start or (end-start).days > 32:
        raise HTTPException(422, "invalid bounded time window")
    with request.app.state.db.connection() as conn:
        rows = conn.execute(
            "SELECT r.raw_id,r.file_id,r.station_id,r.source_line_number,r.observed_at,r.raw_line,r.raw_values,"
            "r.row_sha256,r.parse_ok,r.parse_error,f.source_url,f.content_sha256 AS source_sha256,"
            "f.gcs_uri AS source_gcs_uri,f.gcs_generation AS source_gcs_generation,feed.feed_key "
            "FROM bronze.raw_observations r JOIN bronze.source_files f ON f.file_id=r.file_id "
            "JOIN bronze.source_feeds feed ON feed.feed_id=f.feed_id "
            "WHERE r.station_id='51201' AND r.observed_at>=%s AND r.observed_at<%s AND r.raw_id>%s "
            "ORDER BY r.raw_id LIMIT %s", (start,end,after_raw_id,limit),
        ).fetchall()
    return {"data": json_safe(rows), "next_after": rows[-1]["raw_id"] if len(rows)==limit else None}


@router.get("/silver/rows")
def silver_rows(request: Request, start: datetime, end: datetime, after: datetime | None = None,
                limit: int = Query(500, ge=1, le=1000)):
    if start.tzinfo is None or end.tzinfo is None or end <= start or (end-start).days > 32:
        raise HTTPException(422, "invalid bounded time window")
    with request.app.state.db.connection() as conn:
        rows = conn.execute(
            "SELECT s.*,f.source_url,f.content_sha256 AS source_sha256,f.gcs_uri AS source_gcs_uri,"
            "f.gcs_generation AS source_gcs_generation,feed.feed_key "
            "FROM silver.wave_observations s JOIN bronze.source_files f ON f.file_id=s.source_file_id "
            "JOIN bronze.source_feeds feed ON feed.feed_id=f.feed_id "
            "WHERE s.station_id='51201' AND s.observed_at>=%s AND s.observed_at<%s "
            "AND (%s::timestamptz IS NULL OR s.observed_at>%s) ORDER BY s.observed_at LIMIT %s",
            (start,end,after,after,limit),
        ).fetchall()
    return {"data": json_safe(rows), "next_after": rows[-1]["observed_at"] if len(rows)==limit else None}


@router.get("/gold/rows")
def gold_rows(request: Request, start: datetime, end: datetime, after_start: datetime | None = None,
              after_resolution: str = "", limit: int = Query(500, ge=1, le=1000)):
    if start.tzinfo is None or end.tzinfo is None or end <= start or (end-start).days > 32:
        raise HTTPException(422, "invalid bounded time window")
    with request.app.state.db.connection() as conn:
        rows = conn.execute(
            "SELECT * FROM gold.resolution_metrics WHERE station_id='51201' "
            "AND period_start_at>=%s AND period_start_at<%s "
            "AND (%s::timestamptz IS NULL OR (period_start_at,resolution)>(%s,%s)) "
            "ORDER BY period_start_at,resolution LIMIT %s",
            (start,end,after_start,after_start,after_resolution,limit),
        ).fetchall()
    return {"data": json_safe(rows), "next_after": {"start": rows[-1]["period_start_at"], "resolution": rows[-1]["resolution"]} if len(rows)==limit else None}


@router.get("/jobs")
def jobs(request: Request, limit: int = Query(50, ge=1, le=100)):
    with request.app.state.db.connection() as conn:
        rows = conn.execute("SELECT * FROM bronze.historical_backfill_jobs ORDER BY job_id LIMIT %s", (limit,)).fetchall()
    return {"data": json_safe(rows)}


@router.get("/coverage")
def coverage(request: Request):
    """Read-only production coverage by UTC month, with HST Gold grouped by period start."""
    with request.app.state.db.connection() as conn:
        raw = conn.execute(
            "SELECT date_trunc('month',r.observed_at AT TIME ZONE 'UTC')::date AS month_utc,"
            "count(*) AS raw_rows,count(DISTINCT r.file_id) AS source_files,"
            "array_agg(DISTINCT r.file_id ORDER BY r.file_id) AS source_file_ids,"
            "min(r.observed_at) AS earliest,max(r.observed_at) AS latest "
            "FROM bronze.raw_observations r WHERE r.station_id='51201' GROUP BY 1 ORDER BY 1"
        ).fetchall()
        silver = conn.execute(
            "SELECT date_trunc('month',observed_at AT TIME ZONE 'UTC')::date AS month_utc,"
            "count(*) AS observations,count(*) FILTER (WHERE quality_status='valid') AS valid,"
            "count(*) FILTER (WHERE quality_status='suspect') AS suspect,"
            "count(*) FILTER (WHERE quality_status='rejected') AS rejected,"
            "count(*) FILTER (WHERE quality_status='partial') AS partial,"
            "count(*) FILTER (WHERE quality_status='unverified') AS unverified,"
            "count(*) FILTER (WHERE wave_height_m IS NOT NULL) AS wave_height_available,"
            "count(*) FILTER (WHERE dominant_period_s IS NOT NULL) AS dominant_period_available,"
            "count(*) FILTER (WHERE wind_speed_ms IS NOT NULL) AS wind_speed_available,"
            "count(*) FILTER (WHERE tide_ft IS NOT NULL) AS tide_available,"
            "min(observed_at) AS earliest,max(observed_at) AS latest "
            "FROM silver.wave_observations WHERE station_id='51201' GROUP BY 1 ORDER BY 1"
        ).fetchall()
        gold = conn.execute(
            "SELECT date_trunc('month',(period_start_at AT TIME ZONE 'Pacific/Honolulu'))::date AS month_hst,"
            "resolution,count(*) AS periods,count(*) FILTER (WHERE readiness='GOLD_READY') AS descriptive_ready,"
            "avg(completeness)::double precision AS mean_completeness,"
            "min(period_start_at) AS earliest,max(period_start_at) AS latest "
            "FROM gold.resolution_metrics WHERE station_id='51201' AND metric_code='WVHT' GROUP BY 1,2 ORDER BY 1,2"
        ).fetchall()
        exports = conn.execute(
            "SELECT layer,partition_key,row_count,content_sha256,gcs_uri,gcs_generation,state "
            "FROM gold.dataset_exports WHERE active ORDER BY layer,partition_key"
        ).fetchall()
        files = conn.execute(
            "SELECT f.file_id,feed.feed_key,f.source_url,f.content_sha256,f.source_row_count,f.parse_error_count,"
            "f.source_period_start,f.source_period_end,f.gcs_uri,f.gcs_generation,"
            "(SELECT count(*) FROM bronze.raw_observations r WHERE r.file_id=f.file_id) AS raw_rows "
            "FROM bronze.source_files f JOIN bronze.source_feeds feed ON feed.feed_id=f.feed_id "
            "WHERE f.station_id='51201' ORDER BY f.file_id"
        ).fetchall()
        corrections = conn.execute(
            "SELECT date_trunc('month',r.observed_at AT TIME ZONE 'UTC')::date AS month_utc,"
            "count(*) AS corrected_revisions FROM silver.wave_observation_revisions r "
            "JOIN silver.wave_observation_revisions previous ON previous.station_id=r.station_id "
            "AND previous.observed_at=r.observed_at AND previous.revision_no=r.revision_no-1 "
            "WHERE r.station_id='51201' AND r.change_type='correction' "
            "AND r.measurement_sha256<>previous.measurement_sha256 GROUP BY 1 ORDER BY 1"
        ).fetchall()
    return {"raw_months": json_safe(raw), "silver_months": json_safe(silver),
            "gold_months": json_safe(gold), "active_exports": json_safe(exports),
            "source_files": json_safe(files), "corrections": json_safe(corrections)}


@router.post("/jobs/claim")
def claim_job(request: Request, body: JobClaim):
    from packages.noaa.urls import annual_url, monthly_url
    year = int(body.source_period[:4])
    expected = annual_url(year) if body.feed_key == "historical_annual" and len(body.source_period) == 4 else (
        monthly_url(year, int(body.source_period[5:])) if body.feed_key == "historical_monthly" and len(body.source_period) == 7 else None)
    if body.source_url != expected:
        raise HTTPException(422, "source URL does not match NOAA archive")
    with request.app.state.db.connection() as conn:
        conn.execute("INSERT INTO bronze.historical_backfill_jobs(feed_key,source_url,source_period) VALUES (%s,%s,%s) ON CONFLICT DO NOTHING",
                     (body.feed_key,body.source_url,body.source_period))
        row = conn.execute("SELECT * FROM bronze.historical_backfill_jobs WHERE feed_key=%s AND source_url=%s FOR UPDATE",
                           (body.feed_key,body.source_url)).fetchone()
        if row["lease_token"] is not None and row["lease_until"] and row["lease_until"] > datetime.now(timezone.utc):
            return {"claimed": False, "reason": "busy"}
        token = uuid4()
        conn.execute("UPDATE bronze.historical_backfill_jobs SET status='RUNNING',lease_token=%s,"
                     "lease_until=now()+interval '5 minutes',started_at=COALESCE(started_at,now()),"
                     "retry_count=retry_count+1,updated_at=now() WHERE job_id=%s", (token,row["job_id"]))
    return {"claimed": True, "job": json_safe(row), "lease_token": str(token)}


@router.post("/jobs/{job_id}/checkpoint")
def checkpoint_job(request: Request, job_id: int, body: JobCheckpoint):
    with request.app.state.db.connection() as conn:
        row = conn.execute("SELECT * FROM bronze.historical_backfill_jobs WHERE job_id=%s FOR UPDATE", (job_id,)).fetchone()
        if row is None or row["lease_token"] != body.lease_token:
            raise HTTPException(409, "backfill lease is no longer active")
        terminal = body.status in ("VERIFIED", "FAILED")
        conn.execute(
            "UPDATE bronze.historical_backfill_jobs SET status=%s,last_batch_offset=CASE WHEN %s::text IS NOT NULL AND source_sha256 IS DISTINCT FROM %s "
            "THEN %s ELSE GREATEST(last_batch_offset,COALESCE(%s,last_batch_offset)) END,"
            "source_sha256=COALESCE(%s,source_sha256),source_row_count=COALESCE(%s,source_row_count),"
            "source_etag=COALESCE(%s,source_etag),source_last_modified=COALESCE(%s,source_last_modified),"
            "source_content_length=COALESCE(%s,source_content_length),gcs_uri=COALESCE(%s,gcs_uri),"
            "gcs_generation=COALESCE(%s,gcs_generation),last_exported_partition=COALESCE(%s,last_exported_partition),"
            "error_stage=%s,lease_until=CASE WHEN %s THEN NULL ELSE now()+interval '5 minutes' END,"
            "lease_token=CASE WHEN %s THEN NULL ELSE lease_token END,"
            "completed_at=CASE WHEN %s THEN now() ELSE completed_at END,"
            "last_verified_at=CASE WHEN %s THEN now() ELSE last_verified_at END,"
            "last_checked_at=CASE WHEN %s THEN now() ELSE last_checked_at END,updated_at=now() WHERE job_id=%s",
            (body.status,body.source_sha256,body.source_sha256,body.last_batch_offset or 0,body.last_batch_offset,
             body.source_sha256,body.source_row_count,
             body.source_etag,body.source_last_modified,body.source_content_length,body.gcs_uri,
             body.gcs_generation,body.last_exported_partition,body.error_stage,terminal,terminal,
             terminal,body.status=="VERIFIED",body.status=="VERIFIED",job_id),
        )
        if body.status == "VERIFIED":
            archive_year = int(row["source_period"][:4])
            for publication_year in (archive_year - 1, archive_year):
                if publication_year < 2004:
                    continue
                conn.execute(
                    "INSERT INTO gold.history_publications(year,state) VALUES (%s,'PENDING') "
                    "ON CONFLICT (year) DO UPDATE SET state='PENDING',updated_at=now()",
                    (publication_year,),
                )
    return {"status": body.status}


@router.get("/publications/pending")
def pending_publications(request: Request, limit: int = Query(2, ge=1, le=2)):
    with request.app.state.db.connection() as conn:
        rows = conn.execute(
            "SELECT year,state,updated_at FROM gold.history_publications "
            "WHERE state='PENDING' ORDER BY updated_at,year LIMIT %s", (limit,),
        ).fetchall()
    return {"data": json_safe(rows)}


@router.get("/publications")
def publications(request: Request, limit: int = Query(100, ge=1, le=100)):
    with request.app.state.db.connection() as conn:
        rows = conn.execute("SELECT * FROM gold.history_publications ORDER BY year LIMIT %s", (limit,)).fetchall()
    return {"data": json_safe(rows)}


@router.post("/publications/{year}/complete")
def complete_publication(request: Request, year: int, body: PublicationComplete):
    if not 2004 <= year <= 2100:
        raise HTTPException(422, "invalid historical year")
    if body.manifest_gcs_uri != f"gs://waimea-noaa/manifests/station=51201/historical/year={year}/{body.manifest_sha256}.json":
        raise HTTPException(422, "invalid manifest path")
    if body.coverage_gcs_uri != f"gs://waimea-noaa/manifests/station=51201/coverage/{body.coverage_sha256}.json":
        raise HTTPException(422, "invalid coverage path")
    with request.app.state.db.connection() as conn:
        row = conn.execute("UPDATE gold.history_publications SET state='VERIFIED',manifest_sha256=%s,"
                           "manifest_gcs_uri=%s,manifest_generation=%s,coverage_sha256=%s,"
                           "coverage_gcs_uri=%s,coverage_generation=%s,verified_at=now(),updated_at=now() "
                           "WHERE year=%s AND state='PENDING' AND updated_at=%s RETURNING year",
                           (body.manifest_sha256,body.manifest_gcs_uri,body.manifest_generation,
                            body.coverage_sha256,body.coverage_gcs_uri,body.coverage_generation,year,
                            body.pending_updated_at)).fetchone()
    if row is None:
        raise HTTPException(409, "year is not pending publication")
    return {"year": year, "state": "VERIFIED"}


@router.post("/jobs/{job_id}/checked")
def checked_job(request: Request, job_id: int, body: JobChecked = JobChecked()):
    with request.app.state.db.connection() as conn:
        row = conn.execute("UPDATE bronze.historical_backfill_jobs SET last_checked_at=now(),"
                           "last_verified_at=CASE WHEN %s THEN now() ELSE last_verified_at END,"
                           "source_etag=COALESCE(%s,source_etag),source_last_modified=COALESCE(%s,source_last_modified),"
                           "source_content_length=COALESCE(%s,source_content_length),updated_at=now() "
                           "WHERE job_id=%s AND status='VERIFIED' RETURNING job_id",
                           (body.full_checksum,body.source_etag,body.source_last_modified,
                            body.source_content_length,job_id)).fetchone()
    if row is None:
        raise HTTPException(409, "source is not verified")
    return {"checked": True}


@router.post("/jobs/{job_id}/heartbeat")
def heartbeat_job(request: Request, job_id: int, body: JobHeartbeat):
    with request.app.state.db.connection() as conn:
        row = conn.execute("UPDATE bronze.historical_backfill_jobs SET lease_until=now()+interval '5 minutes',"
                           "updated_at=now() WHERE job_id=%s AND lease_token=%s AND status NOT IN ('VERIFIED','FAILED') "
                           "RETURNING job_id", (job_id,body.lease_token)).fetchone()
    if row is None:
        raise HTTPException(409, "backfill lease is no longer active")
    return {"renewed": True}


@router.post("/exports/prepare")
def prepare(request: Request, body: ExportPrepare):
    expected = f"gs://waimea-noaa/datasets/{body.layer}/{body.partition_key}/{body.content_sha256}.jsonl.gz"
    if body.gcs_uri != expected:
        raise HTTPException(422, "content-addressed GCS path required")
    with request.app.state.db.connection() as conn:
        conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", (f"export:{body.layer}:{body.partition_key}",))
        row = conn.execute(
            "INSERT INTO gold.dataset_exports(layer,partition_key,schema_version,content_sha256,row_count,"
            "min_observed_at,max_observed_at,source_watermark,gcs_uri,processing_run_id) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
            "ON CONFLICT (layer,partition_key,schema_version,content_sha256) DO NOTHING "
            "RETURNING export_id,state,gcs_generation,active",
            (body.layer,body.partition_key,body.schema_version,body.content_sha256,body.row_count,
             body.min_observed_at,body.max_observed_at,body.source_watermark,body.gcs_uri,body.processing_run_id),
        ).fetchone()
        if row is None:
            row = conn.execute(
                "SELECT export_id,state,gcs_generation,active FROM gold.dataset_exports "
                "WHERE layer=%s AND partition_key=%s AND schema_version=%s AND content_sha256=%s FOR UPDATE",
                (body.layer,body.partition_key,body.schema_version,body.content_sha256),
            ).fetchone()
            if row["state"] != "VERIFIED":
                row = conn.execute("UPDATE gold.dataset_exports SET attempt_count=attempt_count+1 "
                                   "WHERE export_id=%s RETURNING export_id,state,gcs_generation,active",
                                   (row["export_id"],)).fetchone()
    return json_safe(row)


@router.post("/exports/complete")
def complete(request: Request, body: ExportComplete):
    with request.app.state.db.connection() as conn:
        key = conn.execute("SELECT layer,partition_key FROM gold.dataset_exports WHERE export_id=%s", (body.export_id,)).fetchone()
        if key is None:
            raise HTTPException(404, "export not found")
        conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", (f"export:{key['layer']}:{key['partition_key']}",))
        row = conn.execute("SELECT * FROM gold.dataset_exports WHERE export_id=%s FOR UPDATE", (body.export_id,)).fetchone()
        if row["state"] == "VERIFIED" and row["gcs_generation"] == body.gcs_generation and row["active"]:
            return {"state": "VERIFIED", "duplicate": True}
        conn.execute("UPDATE gold.dataset_exports SET active=FALSE WHERE layer=%s AND partition_key=%s AND active",
                     (row["layer"],row["partition_key"]))
        conn.execute("UPDATE gold.dataset_exports SET state='VERIFIED',active=TRUE,gcs_generation=%s,"
                     "verified_at=now() WHERE export_id=%s", (body.gcs_generation,body.export_id))
    return {"state": "VERIFIED", "duplicate": False}


@router.post("/exports/fail")
def fail(request: Request, body: ExportFailure):
    with request.app.state.db.connection() as conn:
        row = conn.execute("UPDATE gold.dataset_exports SET state='RETRY_SCHEDULED' "
                           "WHERE export_id=%s AND state<>'VERIFIED' RETURNING export_id",
                           (body.export_id,)).fetchone()
    if row is None:
        raise HTTPException(409, "export is already verified or missing")
    return {"state": "RETRY_SCHEDULED", "error_class": body.error_class}


@router.get("/exports/active")
def active(request: Request, layer: Literal["bronze","silver","gold"], partition_key: str):
    with request.app.state.db.connection() as conn:
        row = conn.execute("SELECT * FROM gold.dataset_exports WHERE layer=%s AND partition_key=%s AND active",
                           (layer,partition_key)).fetchone()
    return json_safe(row) if row else {"found": False}
