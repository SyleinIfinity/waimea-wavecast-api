"""Read-only public view of experimental Calisthenics 1.5 shadow forecasts."""
from fastapi import APIRouter, HTTPException, Query, Request

from services.database_api.app.repositories.common import json_safe

router=APIRouter(prefix="/api/v1/forecasts/shadow",tags=["public"])
HORIZONS={"30min","1d","7d","30d"}


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
