"""Durable TL07-B cycle claims with a database-backed lease and retry budget."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field, field_validator

from services.database_api.app.repositories.common import json_safe
from services.database_api.app.schemas.common import require_utc
from services.database_api.app.security import internal_auth

router = APIRouter(prefix="/internal/v1/pipeline", dependencies=[Depends(internal_auth)], tags=["internal"])


class Claim(BaseModel):
    slot_at: datetime

    @field_validator("slot_at")
    @classmethod
    def utc(cls, value):
        return require_utc(value)


class Finish(BaseModel):
    slot_at: datetime
    lease_token: UUID
    success: bool
    silver_inserted: int = Field(default=0, ge=0)
    silver_revised: int = Field(default=0, ge=0)
    latest_observed_at: datetime | None = None
    changed_from: datetime | None = None
    changed_to: datetime | None = None
    gcs_uri: str | None = None
    gcs_generation: int | None = None
    failure_stage: str | None = None
    error_class: str | None = None
    retry_after_seconds: int | None = Field(default=None, ge=0, le=3600)

    @field_validator("slot_at", "latest_observed_at", "changed_from", "changed_to")
    @classmethod
    def utc(cls, value):
        return require_utc(value) if value is not None else value


@router.get("/cycles/due")
def due(request: Request):
    with request.app.state.db.connection() as conn:
        rows = conn.execute(
            "SELECT slot_at FROM gold.pipeline_cycles WHERE station_id='51201' AND "
            "((status IN ('WAITING_FOR_SOURCE','RETRY_SCHEDULED') AND next_attempt_at<=now()) "
            "OR (status='RUNNING' AND lease_until<=now())) ORDER BY slot_at LIMIT 2"
        ).fetchall()
    return {"slots": [row["slot_at"].isoformat() for row in rows]}


@router.post("/cycles/claim")
def claim(request: Request, body: Claim):
    now = datetime.now(timezone.utc)
    slot = body.slot_at.astimezone(timezone.utc)
    if slot.minute not in (0, 30) or slot.second or slot.microsecond or not now - timedelta(days=30) <= slot <= now:
        raise HTTPException(422, "invalid cycle slot")
    with request.app.state.db.connection() as conn:
        conn.execute("SELECT pg_advisory_xact_lock(hashtextextended('tl07b:51201:poll',0))")
        busy = conn.execute(
            "SELECT 1 FROM gold.pipeline_cycles WHERE station_id='51201' AND status='RUNNING' "
            "AND lease_until>now() LIMIT 1"
        ).fetchone()
        if busy:
            return {"claimed": False, "reason": "busy"}
        source_backoff = conn.execute(
            "SELECT 1 FROM gold.pipeline_cycles WHERE station_id='51201' AND status='RETRY_SCHEDULED' "
            "AND error_class='HTTP429' AND next_attempt_at>now() AND slot_at<>%s LIMIT 1", (slot,)
        ).fetchone()
        if source_backoff:
            return {"claimed": False, "reason": "source_backoff"}
        conn.execute("INSERT INTO gold.pipeline_cycles(station_id,slot_at) VALUES ('51201',%s) ON CONFLICT DO NOTHING", (slot,))
        row = conn.execute("SELECT * FROM gold.pipeline_cycles WHERE station_id='51201' AND slot_at=%s FOR UPDATE", (slot,)).fetchone()
        if row["status"] in ("SUCCESS", "NO_NEW_DATA", "FAILED") or row["attempt_count"] >= 10:
            return {"claimed": False, "reason": "complete"}
        if row["status"] in ("WAITING_FOR_SOURCE", "RETRY_SCHEDULED") and row["next_attempt_at"] > now:
            return {"claimed": False, "reason": "not_due"}
        token = uuid4()
        conn.execute(
            "UPDATE gold.pipeline_cycles SET status='RUNNING',attempt_count=attempt_count+1,"
            "lease_until=now()+interval '10 minutes',lease_token=%s,last_poll_at=now(),updated_at=now() "
            "WHERE station_id='51201' AND slot_at=%s",
            (token, slot),
        )
        return {"claimed": True, "slot_at": slot.isoformat(), "lease_token": str(token),
                "attempt_count": row["attempt_count"] + 1, "gcs_uri": row["gcs_uri"],
                "gcs_generation": row["gcs_generation"],
                "failure_stage": row["failure_stage"], "changed_from": row["changed_from"],
                "changed_to": row["changed_to"]}


@router.post("/cycles/finish")
def finish(request: Request, body: Finish):
    now = datetime.now(timezone.utc)
    with request.app.state.db.connection() as conn:
        row = conn.execute(
            "SELECT * FROM gold.pipeline_cycles WHERE station_id='51201' AND slot_at=%s FOR UPDATE",
            (body.slot_at,),
        ).fetchone()
        if row is None or row["status"] != "RUNNING" or row["lease_token"] != body.lease_token:
            raise HTTPException(409, "cycle lease is no longer active")
        attempt = row["attempt_count"]
        next_slot = body.slot_at + timedelta(minutes=30)
        if body.success:
            # NOAA 51201 measurements are stamped :26/:56. Live first-detection
            # evidence on 2026-11-12 shows roughly 24-35 minutes of delay.
            # At slot :00/:30, the previous half-hour point is the due one.
            expected = body.slot_at - timedelta(minutes=34)
            if body.latest_observed_at is not None and body.latest_observed_at >= expected:
                had_changes = body.silver_inserted or body.silver_revised or row["silver_inserted"] or row["silver_revised"]
                status, next_at = ("SUCCESS" if had_changes else "NO_NEW_DATA"), None
            elif attempt < 10:
                offsets = (1, 2, 3, 4, 5, 10, 15, 20, 25)
                next_at = max(now + timedelta(minutes=1), body.slot_at + timedelta(minutes=offsets[attempt - 1]))
                status = "WAITING_FOR_SOURCE" if next_at < next_slot else "NO_NEW_DATA"
                if status == "NO_NEW_DATA":
                    next_at = None
            else:
                status, next_at = "NO_NEW_DATA", None
        elif attempt < 10 and (body.retry_after_seconds is not None or row["gcs_uri"] or body.gcs_uri or now + timedelta(minutes=1) < next_slot):
            status = "RETRY_SCHEDULED"
            next_at = now + timedelta(seconds=body.retry_after_seconds) if body.retry_after_seconds is not None else now + timedelta(minutes=min(5, 2 ** min(attempt-1, 3)))
        else:
            status, next_at = "FAILED", None
        gcs_uri = body.gcs_uri or row["gcs_uri"]
        generation = body.gcs_generation or row["gcs_generation"]
        conn.execute(
            "UPDATE gold.pipeline_cycles SET status=%s,next_attempt_at=%s,lease_until=NULL,lease_token=NULL,"
            "latest_observed_at=COALESCE(%s,latest_observed_at),"
            "changed_from=COALESCE(LEAST(changed_from,%s),changed_from,%s),"
            "changed_to=COALESCE(GREATEST(changed_to,%s),changed_to,%s),"
            "first_new_observation_at=CASE WHEN %s>0 AND first_new_observation_at IS NULL THEN now() ELSE first_new_observation_at END,"
            "silver_inserted=silver_inserted+%s,silver_revised=silver_revised+%s,gcs_uri=%s,gcs_generation=%s,"
            "failure_stage=%s,error_class=%s,updated_at=now() WHERE station_id='51201' AND slot_at=%s",
            (status, next_at, body.latest_observed_at, body.changed_from, body.changed_from,
             body.changed_to, body.changed_to, body.silver_inserted,
             body.silver_inserted, body.silver_revised, gcs_uri, generation,
             body.failure_stage, body.error_class, body.slot_at),
        )
    return {"status": status, "next_attempt_at": next_at.isoformat() if next_at else None}


@router.get("/status")
def status(request: Request):
    with request.app.state.db.connection() as conn:
        latest = conn.execute("SELECT * FROM gold.pipeline_cycles WHERE station_id='51201' ORDER BY slot_at DESC LIMIT 1").fetchone()
        counts = conn.execute("SELECT status,count(*) AS n FROM gold.pipeline_cycles WHERE station_id='51201' AND slot_at>now()-interval '24 hours' GROUP BY status").fetchall()
        metrics = conn.execute(
            "SELECT count(*) AS cycles,coalesce(sum(attempt_count),0) AS poll_attempts,"
            "coalesce(sum(greatest(attempt_count-1,0)),0) AS retry_attempts,"
            "coalesce(sum(silver_inserted),0) AS silver_inserted,"
            "coalesce(sum(silver_revised),0) AS silver_revised,"
            "count(*) FILTER (WHERE status='FAILED') AS failed_cycles,"
            "max(last_poll_at) AS latest_poll_at,"
            "max(last_poll_at) FILTER (WHERE status='SUCCESS') AS latest_successful_cycle_at "
            "FROM gold.pipeline_cycles WHERE station_id='51201' AND slot_at>now()-interval '24 hours'"
        ).fetchone()
        observations = conn.execute("SELECT max(observed_at) AS latest_observed_at,count(*) AS total,"
                                    "count(*) FILTER (WHERE quality_status='valid') AS valid,"
                                    "count(*) FILTER (WHERE quality_status='suspect') AS suspect,"
                                    "count(*) FILTER (WHERE quality_status='rejected') AS rejected,"
                                    "count(*) FILTER (WHERE quality_status='unverified') AS unverified "
                                    "FROM silver.wave_observations WHERE observed_at>now()-interval '7 days'").fetchone()
        gaps = conn.execute("SELECT count(*) AS open FROM silver.observation_gaps WHERE station_id='51201' AND resolved_at IS NULL").fetchone()
        gold = conn.execute("SELECT max(computed_at) AS last_computed_at,count(*) AS total FROM gold.resolution_metrics WHERE station_id='51201'").fetchone()
    if latest:
        latest = {key: value for key, value in latest.items() if key not in ("lease_token", "gcs_uri")}
    return {"latest": json_safe(latest), "last_24h": json_safe(counts),
            "metrics_last_24h": json_safe(metrics),
            "silver_last_7d": json_safe(observations), "open_gap_candidates": gaps["open"],
            "gold": json_safe(gold)}


@router.get("/gold/resolution-metrics")
def resolution_metrics(request: Request, resolution: str, start: datetime, end: datetime,
                       after: datetime | None = None, limit: int = Query(500, ge=1, le=1000)):
    if resolution not in ("30min", "daily", "weekly", "monthly", "seasonal", "yearly"):
        raise HTTPException(422, "invalid resolution")
    if start.tzinfo is None or end.tzinfo is None or end <= start or end-start > timedelta(days=366):
        raise HTTPException(422, "invalid time window")
    with request.app.state.db.connection() as conn:
        rows = conn.execute(
            "SELECT * FROM gold.resolution_metrics WHERE station_id='51201' AND metric_code='WVHT' "
            "AND resolution=%s AND period_start_at>=%s AND period_start_at<%s "
            "AND (%s::timestamptz IS NULL OR period_start_at>%s) ORDER BY period_start_at LIMIT %s",
            (resolution, start, end, after, after, limit),
        ).fetchall()
    return {"data": json_safe(rows), "next_after": rows[-1]["period_start_at"].isoformat() if len(rows) == limit else None}


@router.get("/readiness")
def downstream_readiness(request: Request, resolution: str, horizon: str,
                         start: datetime, end: datetime):
    """Fail-closed TL08 handoff until an approved horizon policy exists."""
    if resolution not in ("30min", "daily", "weekly", "monthly", "seasonal", "yearly"):
        raise HTTPException(422, "invalid resolution")
    if horizon not in ("30min", "1h", "6h", "24h", "7d", "30d", "season", "cycle", "year"):
        raise HTTPException(422, "invalid horizon")
    if start.tzinfo is None or end.tzinfo is None or end <= start or end-start > timedelta(days=366):
        raise HTTPException(422, "invalid time window")
    with request.app.state.db.connection() as conn:
        summary = conn.execute(
            "SELECT count(*) AS observed_count,"
            "count(*) FILTER (WHERE quality_status='valid' AND wave_height_m IS NOT NULL) AS valid_count,"
            "count(*) FILTER (WHERE quality_status='unverified') AS unverified_count,"
            "count(*) FILTER (WHERE quality_status='suspect') AS suspect_count,"
            "count(*) FILTER (WHERE quality_status='rejected') AS rejected_count,"
            "max(observed_at) AS latest_observed_at "
            "FROM silver.wave_observations WHERE station_id='51201' AND observed_at>=%s AND observed_at<%s",
            (start, end),
        ).fetchone()
        gold = conn.execute(
            "SELECT count(*) AS periods,count(*) FILTER (WHERE readiness='GOLD_READY') AS descriptive_ready "
            "FROM gold.resolution_metrics WHERE station_id='51201' AND resolution=%s AND period_start_at>=%s AND period_start_at<%s",
            (resolution, start, end),
        ).fetchone()
    return {"station_id": "51201", "resolution": resolution, "horizon": horizon,
            "start": start.isoformat(), "end": end.isoformat(),
            "status": "INSUFFICIENT_DATA", "ml_ready": False,
            "reasons": ["production_horizon_policy_unapproved", "model_validation_unavailable"],
            "quality_counts": json_safe(summary), "gold_periods": json_safe(gold),
            "auto_imputation_enabled": False}
