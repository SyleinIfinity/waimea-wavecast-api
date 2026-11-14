"""Forward-issued experimental NOAA forecasts and append-only QC evaluation."""
from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, time, timedelta, timezone
from math import isfinite
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field, model_validator
from psycopg.types.json import Jsonb

from services.database_api.app.repositories.common import json_safe
from services.database_api.app.security import internal_auth

HST = ZoneInfo("Pacific/Honolulu")
router = APIRouter(prefix="/internal/v1/forward", tags=["internal"], dependencies=[Depends(internal_auth)])
public_router = APIRouter(prefix="/api/v1/forecasts/experimental", tags=["public"])


def day_bounds(day: date) -> tuple[datetime, datetime]:
    start = datetime.combine(day, time.min, HST).astimezone(timezone.utc)
    return start, start + timedelta(days=1)


class ReserveForecast(BaseModel):
    station_id: str = "51201"
    model_id: int = Field(gt=0)
    model_version: str
    horizon: str
    target_kind: str
    issued_at: datetime
    anchor_at: datetime
    anchor_date_hst: date | None = None
    target_at: datetime
    target_date_hst: date | None = None
    predicted_value: float = Field(ge=0)
    features_snapshot: dict
    source_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def valid(self):
        if self.station_id != "51201" or self.horizon not in ("30min", "1d", "7d"):
            raise ValueError("invalid station or horizon")
        if not all(x.tzinfo is not None for x in (self.issued_at, self.anchor_at, self.target_at)):
            raise ValueError("timezone offsets required")
        if not self.anchor_at <= self.issued_at < self.target_at:
            raise ValueError("forecast must precede target")
        if not isfinite(self.predicted_value):
            raise ValueError("prediction must be finite")
        if self.horizon == "30min":
            if self.target_kind != "instant_wvht" or self.anchor_date_hst or self.target_date_hst:
                raise ValueError("invalid instant target")
            if self.target_at - self.anchor_at != timedelta(minutes=90):
                raise ValueError("30-minute issued forecast requires pinned 90-minute source lead")
        else:
            days = 1 if self.horizon == "1d" else 7
            if self.target_kind != "hst_day_median_wvht" or self.anchor_date_hst is None or self.target_date_hst != self.anchor_date_hst + timedelta(days=days):
                raise ValueError("invalid HST daily target")
            if self.anchor_at != day_bounds(self.anchor_date_hst)[1] or self.target_at != day_bounds(self.target_date_hst)[1]:
                raise ValueError("HST period boundaries required")
        return self


class FinalizeForecast(BaseModel):
    gcs_uri: str = Field(pattern=r"^gs://waimea-noaa/predictions/forward/")
    gcs_generation: str = Field(pattern=r"^[0-9]+$")
    gcs_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


@router.post("/forecasts/reserve")
def reserve(request: Request, body: ReserveForecast):
    now = datetime.now(timezone.utc)
    if body.issued_at > now + timedelta(seconds=10) or body.issued_at < now - timedelta(minutes=3) or body.target_at <= now:
        raise HTTPException(422, "new forecast must be issued before its future target")
    if body.horizon == "30min" and not timedelta(minutes=15) <= body.target_at - body.issued_at <= timedelta(minutes=45):
        raise HTTPException(422, "30-minute issued lead must be 15–45 minutes")
    if body.horizon == "1d" and body.issued_at >= day_bounds(body.target_date_hst)[0] + timedelta(hours=4):
        raise HTTPException(422, "daily forecast issued too late in target HST day")
    if body.horizon == "7d" and body.issued_at >= day_bounds(body.target_date_hst)[0]:
        raise HTTPException(422, "seven-day target period must be entirely future")
    with request.app.state.db.connection() as conn:
        model = conn.execute("SELECT model_version,status,task_type,model_name,hyperparameters "
                             "FROM gold.model_registry WHERE model_id=%s", (body.model_id,)).fetchone()
        if not model or model["status"] not in ("draft", "ready") or model["task_type"] != "regression" or model["model_version"] != body.model_version:
            raise HTTPException(422, "pinned regression model required")
        params = model["hyperparameters"] or {}
        if body.horizon == "30min":
            if params.get("horizon") != "90min":
                raise HTTPException(422, "90-minute source-lead model required")
            source = conn.execute("SELECT 1 FROM silver.wave_observations o "
                "JOIN gold.resolution_metrics g ON g.station_id=o.station_id AND g.metric_code='WVHT' "
                "AND g.resolution='30min' AND o.observed_at>=g.period_start_at AND o.observed_at<g.period_end_at "
                "WHERE o.station_id='51201' AND o.observed_at=%s AND o.measurement_sha256=%s "
                "AND o.quality_status='valid' AND o.source_kind='realtime' AND g.readiness='GOLD_READY' LIMIT 1",
                (body.anchor_at,body.source_sha256)).fetchone()
        else:
            expected = 1 if body.horizon == "1d" else 7
            if (expected == 1 and model["model_name"] != "waimea-regression" and params.get("operational_horizon") != "1d") or (expected == 7 and params.get("horizon_days") != 7 and params.get("operational_horizon") != "7d"):
                raise HTTPException(422, "wrong daily model for horizon")
            start, end = day_bounds(body.anchor_date_hst)
            source = conn.execute("SELECT 1 FROM gold.resolution_metrics WHERE station_id='51201' "
                "AND metric_code='WVHT' AND resolution='daily' AND period_start_at=%s AND period_end_at=%s "
                "AND readiness='GOLD_READY' AND computed_at<=%s LIMIT 1", (start,end,body.issued_at)).fetchone()
        if source is None:
            raise HTTPException(422, "QC Gold source is not ready")
        conn.execute("INSERT INTO gold.forward_forecasts "
            "(station_id,model_id,model_version,horizon,target_kind,issued_at,anchor_at,anchor_date_hst,"
            "target_at,target_date_hst,predicted_value,features_snapshot,source_sha256) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
            "ON CONFLICT (model_id,anchor_at) DO NOTHING",
            (body.station_id,body.model_id,body.model_version,body.horizon,body.target_kind,
             body.issued_at,body.anchor_at,body.anchor_date_hst,body.target_at,body.target_date_hst,
             body.predicted_value,Jsonb(body.features_snapshot),body.source_sha256))
        row = conn.execute("SELECT * FROM gold.forward_forecasts WHERE model_id=%s AND anchor_at=%s",
                           (body.model_id,body.anchor_at)).fetchone()
    return json_safe(row)


@router.post("/forecasts/{forecast_id}/finalize")
def finalize(request: Request, forecast_id: int, body: FinalizeForecast):
    if f"forecast={forecast_id}/" not in body.gcs_uri:
        raise HTTPException(422, "forecast artifact path mismatch")
    with request.app.state.db.connection() as conn:
        row = conn.execute("SELECT gcs_uri,gcs_generation,gcs_sha256 FROM gold.forward_forecasts "
                           "WHERE forecast_id=%s FOR UPDATE", (forecast_id,)).fetchone()
        if row is None:
            raise HTTPException(404, "forecast not found")
        if row["gcs_uri"] is None:
            conn.execute("UPDATE gold.forward_forecasts SET gcs_uri=%s,gcs_generation=%s,gcs_sha256=%s "
                         "WHERE forecast_id=%s", (body.gcs_uri,body.gcs_generation,body.gcs_sha256,forecast_id))
        elif (row["gcs_uri"],row["gcs_generation"],row["gcs_sha256"]) != (body.gcs_uri,body.gcs_generation,body.gcs_sha256):
            raise HTTPException(409, "forecast artifact is immutable")
    return {"forecast_id": forecast_id, "status": "finalized"}


@router.get("/forecasts/unfinalized")
def unfinalized(request: Request, limit: int = Query(100, ge=1, le=500)):
    with request.app.state.db.connection() as conn:
        rows = conn.execute("SELECT * FROM gold.forward_forecasts WHERE gcs_uri IS NULL "
                            "ORDER BY forecast_id LIMIT %s", (limit,)).fetchall()
    return {"data": json_safe(rows)}


@router.get("/daily-inputs")
def daily_inputs(request: Request, start_hst: date, end_hst: date):
    if end_hst < start_hst or (end_hst-start_hst).days > 8:
        raise HTTPException(422, "bounded HST window required")
    with request.app.state.db.connection() as conn:
        rows = conn.execute("SELECT d.*,g.readiness AS gold_readiness,g.median_value AS gold_median_value,"
            "g.period_end_at AS gold_period_end_at,"
            "g.computed_at AS gold_computed_at FROM silver.daily_wave_features d "
            "LEFT JOIN gold.resolution_metrics g ON g.station_id=d.station_id AND g.metric_code='WVHT' "
            "AND g.resolution='daily' AND g.period_start_at=(d.date_hst::timestamp AT TIME ZONE 'Pacific/Honolulu') "
            "WHERE d.station_id='51201' AND d.date_hst BETWEEN %s AND %s ORDER BY d.date_hst",
            (start_hst,end_hst)).fetchall()
    return {"data": json_safe(rows)}


def _truth_digest(payload: dict) -> str:
    return hashlib.sha256(json.dumps(payload,sort_keys=True,separators=(",", ":"),default=str).encode()).hexdigest()


@router.post("/reconcile")
def reconcile(request: Request, days: int = Query(8, ge=1, le=90)):
    """Match finalized forward forecasts to QC Gold/Silver truth, including corrections."""
    now = datetime.now(timezone.utc)
    since = now - timedelta(days=days)
    inserted = 0
    pending = 0
    with request.app.state.db.connection() as conn:
        instant = conn.execute("SELECT f.*,o.wave_height_m AS actual_value,o.measurement_sha256 AS truth_sha,"
            "o.revision_no AS truth_revision,o.updated_at AS truth_as_of,"
            "g.readiness AS target_readiness,g.median_value AS gold_value,g.computed_at AS gold_computed_at "
            "FROM gold.forward_forecasts f LEFT JOIN silver.wave_observations o "
            "ON o.station_id=f.station_id AND o.observed_at=f.target_at AND o.quality_status='valid' "
            "LEFT JOIN gold.resolution_metrics g ON g.station_id=f.station_id AND g.metric_code='WVHT' "
            "AND g.resolution='30min' AND f.target_at>=g.period_start_at AND f.target_at<g.period_end_at "
            "WHERE f.horizon='30min' AND f.gcs_uri IS NOT NULL AND f.target_at<=%s "
            "AND f.target_at>=%s ORDER BY f.target_at DESC LIMIT 5000", (now,since)).fetchall()
        daily = conn.execute("SELECT f.*,g.median_value AS actual_value,g.valid_sample_count,g.completeness,"
            "g.suspect_count,g.rejected_count,g.unverified_count,g.calculation_version,"
            "g.computed_at AS truth_as_of,g.readiness AS target_readiness "
            "FROM gold.forward_forecasts f LEFT JOIN gold.resolution_metrics g "
            "ON g.station_id=f.station_id AND g.metric_code='WVHT' AND g.resolution='daily' "
            "AND g.period_start_at=(f.target_date_hst::timestamp AT TIME ZONE 'Pacific/Honolulu') "
            "AND g.period_end_at=f.target_at WHERE f.horizon IN ('1d','7d') AND f.gcs_uri IS NOT NULL "
            "AND f.target_at<=%s AND f.target_at>=%s ORDER BY f.target_at DESC LIMIT 1000", (now,since)).fetchall()
        for row in [*instant,*daily]:
            actual = row.get("actual_value")
            if actual is None or not isfinite(float(actual)) or float(actual) < 0 or row.get("target_readiness") != "GOLD_READY":
                pending += 1
                continue
            if row["horizon"] == "30min":
                if not row.get("truth_sha"):
                    pending += 1
                    continue
                if (row.get("gold_value") is None or row.get("gold_computed_at") is None
                    or abs(float(row["gold_value"])-float(actual)) > 1e-6
                    or row["gold_computed_at"] < row["truth_as_of"]):
                    pending += 1
                    continue
                truth_sha = row["truth_sha"]
                revision = row["truth_revision"]
            else:
                truth_sha = _truth_digest({key: row[key] for key in (
                    "target_date_hst","actual_value","valid_sample_count","completeness",
                    "suspect_count","rejected_count","unverified_count","calculation_version")})
                revision = None
            as_of = row["truth_as_of"]
            if as_of is None or as_of < row["target_at"]:
                pending += 1
                continue
            baseline_key = "wave_lag0" if row["horizon"] == "30min" else "wave_height_median_lag0"
            baseline = row["features_snapshot"].get(baseline_key)
            if baseline is None or not isfinite(float(baseline)):
                pending += 1
                continue
            error = abs(float(actual)-row["predicted_value"])
            baseline_error = abs(float(actual)-float(baseline))
            result = conn.execute("INSERT INTO gold.forward_evaluations "
                "(forecast_id,actual_value,absolute_error,squared_error,persistence_absolute_error,"
                "truth_sha256,truth_revision_no,truth_as_of_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s) "
                "ON CONFLICT (forecast_id,truth_sha256) DO NOTHING RETURNING evaluation_id",
                (row["forecast_id"],float(actual),error,error*error,baseline_error,
                 truth_sha,revision,as_of)).fetchone()
            inserted += result is not None
    return {"scanned":len(instant)+len(daily),"evaluations_inserted":inserted,"pending_truth":pending}


@router.get("/metrics")
def metrics(request: Request):
    with request.app.state.db.connection() as conn:
        rows = conn.execute("SELECT model_id,horizon,count(*) AS evaluated_count,avg(absolute_error) AS mae,"
            "sqrt(avg(absolute_error * absolute_error)) AS rmse,avg(persistence_absolute_error) AS persistence_mae "
            "FROM gold.forward_training_examples GROUP BY model_id,horizon ORDER BY model_id").fetchall()
    return {"data":json_safe(rows)}


@router.get("/training-examples")
def training_examples(request: Request, horizon: str, after_id: int = 0, limit: int = Query(500, ge=1, le=1000)):
    if horizon not in ("30min","1d","7d"):
        raise HTTPException(422,"invalid horizon")
    with request.app.state.db.connection() as conn:
        rows = conn.execute("SELECT * FROM gold.forward_training_examples WHERE horizon=%s "
                            "AND forecast_id>%s ORDER BY forecast_id LIMIT %s",
                            (horizon,after_id,limit)).fetchall()
    return {"data":json_safe(rows),"next_after_id":rows[-1]["forecast_id"] if rows else None}


@public_router.get("/latest")
def public_latest(request: Request, limit: int = Query(20, ge=1, le=100)):
    with request.app.state.db.connection() as conn:
        rows = conn.execute("SELECT f.forecast_id,f.model_id,f.model_version,f.horizon,f.target_kind,"
            "f.issued_at,f.anchor_at,f.target_at,f.target_date_hst,f.predicted_value,f.quality_status,"
            "e.actual_value,e.absolute_error,e.evaluated_at "
            "FROM gold.forward_forecasts f LEFT JOIN LATERAL "
            "(SELECT actual_value,absolute_error,evaluated_at FROM gold.forward_evaluations "
            "WHERE forecast_id=f.forecast_id ORDER BY evaluated_at DESC,evaluation_id DESC LIMIT 1) e ON true "
            "WHERE f.gcs_uri IS NOT NULL AND ((f.horizon='30min' AND f.target_at-f.issued_at "
            "BETWEEN interval '15 minutes' AND interval '45 minutes') OR "
            "(f.horizon='1d' AND f.target_at-f.issued_at > interval '20 hours') OR "
            "(f.horizon='7d' AND f.target_at-f.issued_at > interval '1 day')) "
            "ORDER BY f.issued_at DESC LIMIT %s", (limit,)).fetchall()
    return {"status":"ok" if rows else "empty","data":json_safe(rows)}
