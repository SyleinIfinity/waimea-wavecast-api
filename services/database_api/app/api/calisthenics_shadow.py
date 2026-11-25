"""Read-only public view of experimental Calisthenics 1.5 shadow forecasts."""
from math import isclose

from fastapi import APIRouter, HTTPException, Query, Request

from services.database_api.app.repositories.common import json_safe

router=APIRouter(prefix="/api/v1/forecasts/shadow",tags=["public"])
HORIZONS={"30min","1d","7d","30d"}
MIN_IMPROVEMENT = 0.08
MIN_TEST_TARGETS = 300
MIN_TEST_DAYS = 14


def assess_30min_readiness(scores: dict) -> dict:
    baseline = scores["persistence_mae"]
    candidate = scores["candidate_mae"]
    checks = {
        "sample_size": scores["target_count"] >= MIN_TEST_TARGETS,
        "day_coverage": scores["test_days"] >= MIN_TEST_DAYS,
    }
    improvement = None
    if baseline and candidate is not None:
        improvement = 100 * (1 - candidate / baseline)
    checks.update({
        "beats_persistence_by_8pct": candidate is not None and bool(baseline) and (
            candidate <= baseline * (1 - MIN_IMPROVEMENT)
            or isclose(candidate, baseline * (1 - MIN_IMPROVEMENT), rel_tol=1e-12)
        ),
        "beats_reference_mae": candidate is not None and candidate < scores["reference_mae"],
        "does_not_increase_rmse": candidate is not None and scores["candidate_rmse"] <= scores["reference_rmse"],
    })
    if not checks["sample_size"] or not checks["day_coverage"]:
        status = "insufficient_data"
    else:
        status = "passed" if all(checks.values()) else "not_passed"
    return {"status": status, "quality_gate_version": "calisthenics-30min-v1",
            "requirements": {"improvement_pct": 8, "targets": MIN_TEST_TARGETS, "days": MIN_TEST_DAYS},
            "improvement_pct": improvement, "checks": checks, "scores": scores}


@router.get("/readiness")
def readiness(request: Request, candidate_model_id: int = Query(gt=0), reference_model_id: int = Query(gt=0)):
    if candidate_model_id == reference_model_id:
        raise HTTPException(422, "choose two different model versions")
    with request.app.state.db.connection() as conn:
        models = conn.execute(
            "SELECT model_id,created_at FROM gold.model_registry "
            "WHERE model_name='calisthenics 1.5 / 30min' AND model_id=ANY(%s)",
            ([candidate_model_id, reference_model_id],),
        ).fetchall()
        registered = {row["model_id"]: row["created_at"] for row in models}
        if len(registered) != 2:
            raise HTTPException(404, "Calisthenics 1.5 30-minute model not found")
        if registered[reference_model_id] >= registered[candidate_model_id]:
            raise HTTPException(422, "reference model must predate candidate model")
        scores = conn.execute("""
            WITH paired AS (
                SELECT DISTINCT ON (c.target_at) c.target_at,
                    ce.absolute_error AS candidate_error, ce.squared_error AS candidate_squared,
                    re.absolute_error AS reference_error, re.squared_error AS reference_squared,
                    ce.persistence_absolute_error AS persistence_error
                FROM gold.calisthenics_shadow_forecasts c
                JOIN gold.calisthenics_shadow_forecasts r
                    ON r.station_id=c.station_id AND r.target_at=c.target_at AND r.anchor_at=c.anchor_at
                    AND r.source_sha256=c.source_sha256 AND r.features_snapshot=c.features_snapshot
                JOIN LATERAL (
                    SELECT * FROM gold.calisthenics_shadow_evaluations
                    WHERE forecast_id=c.forecast_id ORDER BY evaluation_id DESC LIMIT 1
                ) ce ON true
                JOIN LATERAL (
                    SELECT * FROM gold.calisthenics_shadow_evaluations
                    WHERE forecast_id=r.forecast_id AND truth_sha256=ce.truth_sha256
                    ORDER BY evaluation_id DESC LIMIT 1
                ) re ON true
                WHERE c.model_id=%s AND r.model_id=%s AND c.station_id='51201'
                    AND c.horizon='30min' AND r.horizon='30min'
                    AND c.gcs_uri IS NOT NULL AND r.gcs_uri IS NOT NULL
                    AND c.issued_at >= %s AND r.issued_at >= %s
                    AND ce.evaluated_at >= c.target_at AND re.evaluated_at >= c.target_at
                    AND ce.actual_value=re.actual_value
                ORDER BY c.target_at,c.issued_at,r.issued_at,c.forecast_id,r.forecast_id
            )
            SELECT count(*) AS target_count,
                count(DISTINCT (target_at AT TIME ZONE 'UTC')::date) AS test_days,
                avg(candidate_error) AS candidate_mae, avg(reference_error) AS reference_mae,
                avg(persistence_error) AS persistence_mae,
                sqrt(avg(candidate_squared)) AS candidate_rmse, sqrt(avg(reference_squared)) AS reference_rmse
            FROM paired
        """, (candidate_model_id, reference_model_id,
              registered[candidate_model_id], registered[candidate_model_id])).fetchone()
    return {"candidate_model_id": candidate_model_id, "reference_model_id": reference_model_id,
            **json_safe(assess_30min_readiness(scores))}


@router.get("/latest")
def latest(request:Request,horizon:str|None=None,limit:int=Query(20,ge=1,le=100)):
    if horizon is not None and horizon not in HORIZONS:
        raise HTTPException(422,"invalid shadow horizon")
    with request.app.state.db.connection() as conn:
        rows=conn.execute("SELECT f.forecast_id,f.model_id,f.model_version,f.horizon,f.target_kind,"
            "f.issued_at,f.anchor_at,f.target_at,f.target_date_hst,f.predicted_value,"
            "e.actual_value,e.absolute_error,e.evaluated_at "
            "FROM gold.calisthenics_shadow_forecasts f LEFT JOIN LATERAL "
            "(SELECT actual_value,absolute_error,evaluated_at FROM "
            "gold.calisthenics_shadow_evaluations WHERE forecast_id=f.forecast_id "
            "ORDER BY evaluation_id DESC LIMIT 1) e ON true "
            "WHERE f.gcs_uri IS NOT NULL AND (%s::varchar IS NULL OR f.horizon=%s::varchar) "
            "ORDER BY f.issued_at DESC,f.forecast_id DESC LIMIT %s",
            (horizon,horizon,limit)).fetchall()
    return {"status":"ok" if rows else "empty","quality_status":"experimental",
            "model_family":"calisthenics 1.5","data":json_safe(rows)}


@router.get("/metrics")
def metrics(request:Request):
    with request.app.state.db.connection() as conn:
        rows=conn.execute("SELECT f.model_id,f.horizon,count(*) AS evaluated_count,"
            "avg(e.absolute_error) AS mae,"
            "sqrt(avg(e.squared_error)) AS rmse,"
            "avg(e.persistence_absolute_error) AS persistence_mae "
            "FROM gold.calisthenics_shadow_forecasts f JOIN LATERAL "
            "(SELECT absolute_error,squared_error,persistence_absolute_error "
            "FROM gold.calisthenics_shadow_evaluations WHERE forecast_id=f.forecast_id "
            "ORDER BY evaluation_id DESC LIMIT 1) e ON true "
            "WHERE f.gcs_uri IS NOT NULL GROUP BY f.model_id,f.horizon "
            "ORDER BY f.model_id,f.horizon").fetchall()
    return {"data":json_safe(rows)}
