from datetime import date, datetime, timedelta, timezone
from typing import Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException, Query, Request
from psycopg import sql

from services.database_api.app.repositories.common import json_safe


router = APIRouter(prefix="/api/v1", tags=["public"])
METRICS = {
    "WDIR": "wind_direction_deg", "WSPD": "wind_speed_ms", "GST": "wind_gust_ms",
    "WVHT": "wave_height_m", "DPD": "dominant_period_s", "APD": "average_period_s",
    "MWD": "mean_wave_direction_deg", "PRES": "sea_level_pressure_hpa",
    "ATMP": "air_temperature_c", "WTMP": "sea_surface_temp_c", "DEWP": "dew_point_c",
    "VIS": "visibility_nmi", "PTDY": "pressure_tendency_hpa", "TIDE": "tide_ft",
}


def db(request: Request):
    return request.app.state.db


def item(row):
    return {"status": "ok" if row is not None else "empty", "data": json_safe(row) if row is not None else None}


def page(rows, limit: int, cursor_key: str | None = None):
    more = len(rows) > limit
    data = rows[:limit]
    cursor = json_safe(data[-1][cursor_key]) if more and data and cursor_key else None
    return {"status": "ok" if data else "empty", "data": json_safe(data), "has_more": more, "next_cursor": cursor}


def require_window(request: Request, start: date | datetime, end: date | datetime):
    if isinstance(start, datetime) and (start.tzinfo is None or end.tzinfo is None):
        raise HTTPException(422, "UTC offset required")
    if end < start or (end - start).days > request.app.state.settings.max_query_days:
        raise HTTPException(422, "invalid or oversized time window")


@router.get("/stations/51201")
def station(request: Request):
    with db(request).connection() as conn:
        return item(conn.execute("SELECT * FROM bronze.stations WHERE station_id='51201'").fetchone())


@router.get("/observations/latest")
def latest_observation(request: Request):
    with db(request).connection() as conn:
        row = conn.execute("SELECT * FROM gold.v_latest_observation WHERE station_id='51201' LIMIT 1").fetchone()
    if row is None:
        return {"status": "empty", "data_type": "observation", "data": None}
    data = json_safe(row)
    # Compatibility fields for the existing lightweight frontend client.
    data.update({"source": row["source_kind"], "wave_direction_deg": row["mean_wave_direction_deg"], "sea_temperature_c": row["sea_surface_temp_c"], "quality_flag": row["quality_status"]})
    return {"status": "ok", "data_type": "observation", "data": data}


@router.get("/observations")
def observations(request: Request, start: datetime, end: datetime, limit: int = Query(100, ge=1, le=1000), cursor: datetime | None = None):
    if start.tzinfo is None or end.tzinfo is None or (cursor is not None and cursor.tzinfo is None):
        raise HTTPException(422, "UTC offset required")
    require_window(request, start, end)
    with db(request).connection() as conn:
        rows = conn.execute(
            "SELECT * FROM silver.wave_observations WHERE station_id='51201' AND observed_at >= %s "
            "AND observed_at <= %s AND (%s::timestamptz IS NULL OR observed_at < %s) "
            "ORDER BY observed_at DESC LIMIT %s",
            (start, end, cursor, cursor, limit + 1),
        ).fetchall()
    return page(rows, limit, "observed_at")


@router.get("/metrics/summary")
def metrics_summary(request: Request, metric: str = "WVHT", group_by: Literal["day", "week", "month"] = "day", start_hst: date | None = None, end_hst: date | None = None, limit: int = Query(100, ge=1, le=1000)):
    if metric not in METRICS:
        raise HTTPException(422, "unknown metric")
    end_hst = end_hst or datetime.now(ZoneInfo("Pacific/Honolulu")).date()
    start_hst = start_hst or end_hst - timedelta(days=365)
    require_window(request, start_hst, end_hst)
    with db(request).connection() as conn:
        rows = conn.execute("SELECT * FROM gold.time_series_metrics WHERE station_id='51201' AND metric_code=%s AND period_type=%s AND period_start_hst BETWEEN %s AND %s ORDER BY period_start_hst DESC LIMIT %s", (metric, group_by, start_hst, end_hst, limit + 1)).fetchall()
    return page(rows, limit, "period_start_hst")


@router.get("/analytics/trend-seasonal")
def trend(request: Request, metric: str = "WVHT", period: Literal["week", "month"] = "month", start_hst: date | None = None, end_hst: date | None = None, limit: int = Query(100, ge=1, le=1000)):
    if metric not in METRICS:
        raise HTTPException(422, "unknown metric")
    end_hst = end_hst or datetime.now(ZoneInfo("Pacific/Honolulu")).date()
    start_hst = start_hst or end_hst - timedelta(days=365)
    require_window(request, start_hst, end_hst)
    with db(request).connection() as conn:
        rows = conn.execute("SELECT * FROM gold.trend_seasonal_components WHERE station_id='51201' AND metric_code=%s AND period_type=%s AND period_start_hst BETWEEN %s AND %s ORDER BY period_start_hst DESC LIMIT %s", (metric, period, start_hst, end_hst, limit + 1)).fetchall()
    return page(rows, limit, "period_start_hst")


@router.get("/analytics/correlations")
def correlations(request: Request, method: Literal["pearson", "spearman"] = "pearson", start_hst: date | None = None, end_hst: date | None = None, limit: int = Query(100, ge=1, le=1000)):
    end_hst = end_hst or datetime.now(ZoneInfo("Pacific/Honolulu")).date()
    start_hst = start_hst or end_hst - timedelta(days=365)
    require_window(request, start_hst, end_hst)
    with db(request).connection() as conn:
        rows = conn.execute("SELECT * FROM gold.correlations WHERE station_id='51201' AND method=%s AND date_start_hst >= %s AND date_end_hst <= %s ORDER BY date_end_hst DESC LIMIT %s", (method, start_hst, end_hst, limit + 1)).fetchall()
    return page(rows, limit, "date_end_hst")


@router.get("/analytics/histogram")
def histogram(request: Request, metric: str = "WVHT", bins: int = Query(20, ge=2, le=100), start: datetime | None = None, end: datetime | None = None):
    if metric not in METRICS:
        raise HTTPException(422, "unknown metric")
    end = end or datetime.now(timezone.utc)
    start = start or end - timedelta(days=365)
    require_window(request, start, end)
    column = sql.Identifier(METRICS[metric])
    query = sql.SQL("SELECT min({c}) AS min_v, max({c}) AS max_v, count({c}) AS n FROM silver.wave_observations WHERE station_id='51201' AND observed_at BETWEEN %s AND %s AND {c} IS NOT NULL").format(c=column)
    with db(request).connection() as conn:
        bounds = conn.execute(query, (start, end)).fetchone()
        if bounds["n"] == 0:
            return {"status": "empty", "data": []}
        low, high = bounds["min_v"], bounds["max_v"]
        if low == high:
            return {"status": "ok", "data": [{"start": low, "end": high, "count": bounds["n"]}]}
        rows = conn.execute(sql.SQL("SELECT LEAST(width_bucket({c}, %s, %s, %s), %s) AS bucket, count(*) AS count FROM silver.wave_observations WHERE station_id='51201' AND observed_at BETWEEN %s AND %s AND {c} IS NOT NULL GROUP BY bucket ORDER BY bucket").format(c=column), (low, high, bins, bins, start, end)).fetchall()
    width = (high - low) / bins
    return {"status": "ok", "data": [{"start": low + i * width, "end": low + (i + 1) * width, "count": next((r["count"] for r in rows if r["bucket"] == i + 1), 0)} for i in range(bins)]}


@router.get("/analytics/scatter")
def scatter(request: Request, x: str = "WVHT", y: str = "DPD", start: datetime | None = None, end: datetime | None = None, limit: int = Query(500, ge=1, le=1000)):
    if x not in METRICS or y not in METRICS:
        raise HTTPException(422, "unknown metric")
    end = end or datetime.now(timezone.utc)
    start = start or end - timedelta(days=365)
    require_window(request, start, end)
    query = sql.SQL("SELECT observed_at, {x} AS x, {y} AS y FROM silver.wave_observations WHERE station_id='51201' AND observed_at BETWEEN %s AND %s AND {x} IS NOT NULL AND {y} IS NOT NULL ORDER BY observed_at DESC LIMIT %s").format(x=sql.Identifier(METRICS[x]), y=sql.Identifier(METRICS[y]))
    with db(request).connection() as conn:
        rows = conn.execute(query, (start, end, limit + 1)).fetchall()
    return page(rows, limit, "observed_at")


@router.get("/clusters")
def clusters(request: Request, start_hst: date, end_hst: date, limit: int = Query(100, ge=1, le=1000)):
    require_window(request, start_hst, end_hst)
    with db(request).connection() as conn:
        rows = conn.execute("SELECT a.*, c.cluster_name, c.centroid_features FROM gold.cluster_assignments a JOIN gold.cluster_centroids c USING(model_id,cluster_label) WHERE a.station_id='51201' AND a.date_hst BETWEEN %s AND %s ORDER BY a.date_hst DESC LIMIT %s", (start_hst, end_hst, limit + 1)).fetchall()
    return page(rows, limit, "date_hst")


@router.get("/predictions/latest")
def latest_prediction(request: Request, target_metric: str = "WVHT"):
    if target_metric != "WVHT":
        raise HTTPException(422, "only WVHT forecast is supported")
    with db(request).connection() as conn:
        row = conn.execute("SELECT p.*, m.model_name, m.model_version FROM gold.predictions p JOIN gold.model_registry m USING(model_id) WHERE p.station_id='51201' AND p.target_metric=%s ORDER BY p.issued_at DESC LIMIT 1", (target_metric,)).fetchone()
    if row is None:
        return item(None)
    data = json_safe(row)
    data.update({"predicted_value_m": row["predicted_value"], "actual_value_m": row["actual_value"], "absolute_error_m": row["absolute_error"], "lower_m": row["predicted_lower"], "upper_m": row["predicted_upper"], "model": {"model_id": row["model_id"], "name": row["model_name"], "version": row["model_version"]}})
    return {"status": "ok", "data": data}


@router.get("/predictions/history")
def prediction_history(request: Request, start_hst: date, end_hst: date, limit: int = Query(100, ge=1, le=1000)):
    require_window(request, start_hst, end_hst)
    with db(request).connection() as conn:
        rows = conn.execute("SELECT * FROM gold.predictions WHERE station_id='51201' AND target_date_hst BETWEEN %s AND %s ORDER BY target_date_hst DESC, issued_at DESC LIMIT %s", (start_hst, end_hst, limit + 1)).fetchall()
    return page(rows, limit, "target_date_hst")


@router.get("/classifications/latest")
def latest_classification(request: Request):
    with db(request).connection() as conn:
        return item(conn.execute("SELECT * FROM gold.v_latest_classifications WHERE station_id='51201' ORDER BY issued_at DESC LIMIT 1").fetchone())


@router.get("/classifications/schemes")
def schemes(request: Request):
    with db(request).connection() as conn:
        rows = conn.execute("SELECT * FROM gold.classification_schemes ORDER BY created_at DESC LIMIT 100").fetchall()
    return page(rows, 100)


@router.get("/models")
def models(request: Request, task_type: Literal["clustering", "regression", "classification"] | None = None, status: Literal["draft", "ready", "retired"] | None = None, limit: int = Query(100, ge=1, le=1000)):
    with db(request).connection() as conn:
        rows = conn.execute("SELECT model_id, model_name, model_version, task_type, algorithm, feature_names, evaluation_metrics, status, trained_at, created_at FROM gold.model_registry WHERE (%s::text IS NULL OR task_type=%s) AND (%s::text IS NULL OR status=%s) ORDER BY created_at DESC LIMIT %s", (task_type, task_type, status, status, limit + 1)).fetchall()
    return page(rows, limit, "model_id")


@router.get("/models/{model_id}/evaluations")
def evaluations(request: Request, model_id: int, stage: Literal["train", "validation", "test", "walk_forward", "backtest", "live"] | None = None):
    with db(request).connection() as conn:
        rows = conn.execute("SELECT * FROM gold.v_model_evaluation_summary WHERE model_id=%s AND (%s::text IS NULL OR evaluation_stage=%s) ORDER BY evaluated_at DESC LIMIT 100", (model_id, stage, stage)).fetchall()
    return page(rows, 100)


@router.get("/data-quality/overview")
def quality(request: Request, start_hst: date, end_hst: date):
    require_window(request, start_hst, end_hst)
    with db(request).connection() as conn:
        features = conn.execute("SELECT date_hst, sample_count, valid_wave_count, coverage_ratio FROM silver.daily_wave_features WHERE station_id='51201' AND date_hst BETWEEN %s AND %s ORDER BY date_hst", (start_hst, end_hst)).fetchall()
        issues = conn.execute("SELECT status, severity, count(*) AS count FROM silver.data_quality_issues WHERE station_id='51201' AND (observed_at AT TIME ZONE 'Pacific/Honolulu')::date BETWEEN %s AND %s GROUP BY status,severity", (start_hst, end_hst)).fetchall()
    return {"status": "ok" if features or issues else "empty", "data": json_safe({"daily": features, "issues": issues})}


@router.get("/data-freshness")
def freshness(request: Request):
    with db(request).connection() as conn:
        row = conn.execute("SELECT (SELECT max(observed_at) FROM silver.wave_observations WHERE station_id='51201') AS last_observed_at, (SELECT max(fetched_at) FROM bronze.source_files WHERE station_id='51201') AS last_fetched_at, (SELECT max(updated_at) FROM silver.wave_observations WHERE station_id='51201') AS last_silver_updated_at, (SELECT max(computed_at) FROM gold.time_series_metrics WHERE station_id='51201') AS last_gold_processed_at, (SELECT poll_interval_minutes FROM bronze.source_feeds WHERE station_id='51201' AND feed_key='realtime') AS ingestion_poll_interval_min").fetchone()
    observed = row["last_observed_at"]
    age = (datetime.now(timezone.utc) - observed).total_seconds() / 60 if observed else None
    threshold = request.app.state.settings.freshness_stale_after_minutes
    data = json_safe(row)
    data.update({"station_id": "51201", "source_age_minutes": age, "is_stale": age is not None and age > threshold, "source_status": "unknown" if age is None else "delayed" if age > threshold else "ok"})
    return {"status": "ok", "data": data}


@router.get("/clusters/profiles")
def cluster_profiles(request: Request, model_id: int):
    with db(request).connection() as conn:
        rows = conn.execute("SELECT * FROM gold.cluster_centroids WHERE model_id=%s ORDER BY cluster_label LIMIT 100", (model_id,)).fetchall()
    return page(rows, 100)


@router.get("/models/{model_id}/evaluation-samples")
def evaluation_samples(request: Request, model_id: int, limit: int = Query(100, ge=1, le=1000)):
    with db(request).connection() as conn:
        rows = conn.execute("SELECT s.evaluation_sample_id, s.evaluation_id, s.station_id, s.target_date_hst, s.target_name, s.actual_value, s.predicted_value, s.residual, s.absolute_error, s.actual_label, s.predicted_label, s.is_correct FROM gold.evaluation_samples s JOIN gold.model_evaluations e USING(evaluation_id) WHERE e.model_id=%s ORDER BY s.target_date_hst DESC LIMIT %s", (model_id, limit + 1)).fetchall()
    return page(rows, limit, "target_date_hst")


@router.get("/jobs/{job_id}")
def job(request: Request, job_id: int):
    with db(request).connection() as conn:
        row = conn.execute("SELECT * FROM gold.processing_runs WHERE processing_run_id=%s", (job_id,)).fetchone()
    return item(row)
