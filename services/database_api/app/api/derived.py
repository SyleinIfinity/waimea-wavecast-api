"""Isolated derived-imputation candidate contract; never updates observed rows."""
from datetime import datetime, timedelta
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, field_validator
from psycopg.types.json import Jsonb

from services.database_api.app.security import internal_auth
from services.database_api.app.schemas.common import require_utc

router = APIRouter(prefix="/internal/v1/derived", tags=["internal"], dependencies=[Depends(internal_auth)])


class Candidate(BaseModel):
    station_id: Literal["51201"] = "51201"
    observation_time: datetime
    estimated_value: float = Field(ge=0)
    source_type: Literal["IMPUTED"] = "IMPUTED"
    imputation_method: str = Field(min_length=1, max_length=80)
    model_version: str = Field(min_length=1, max_length=80)
    estimated_at: datetime
    uncertainty: float = Field(ge=0)
    quality_score: float = Field(ge=0, le=1)
    provenance: dict[str, Any]
    gcs_uri: str = Field(pattern=r"^gs://waimea-noaa/datasets/processed/[^ ]+$")
    gcs_generation: int = Field(gt=0)

    @field_validator("observation_time", "estimated_at")
    @classmethod
    def utc(cls, value):
        return require_utc(value)


class Window(BaseModel):
    start: datetime
    end: datetime

    @field_validator("start", "end")
    @classmethod
    def utc(cls, value):
        return require_utc(value)


@router.post("/candidates", status_code=201)
def save_candidate(request: Request, body: Candidate):
    if not body.provenance.get("validation_evidence"):
        raise HTTPException(422, "held-out validation evidence reference required")
    with request.app.state.db.connection() as conn:
        inserted = conn.execute(
            "INSERT INTO gold.derived_imputation_candidates "
            "(station_id,observation_time,estimated_value,source_type,imputation_method,model_version,"
            "estimated_at,uncertainty,quality_score,provenance,gcs_uri,gcs_generation) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
            "ON CONFLICT (station_id,observation_time,model_version) DO NOTHING RETURNING observation_time",
            (body.station_id, body.observation_time, body.estimated_value, body.source_type,
             body.imputation_method, body.model_version, body.estimated_at, body.uncertainty,
             body.quality_score, Jsonb(body.provenance), body.gcs_uri, body.gcs_generation),
        ).fetchone()
    return {"inserted": inserted is not None}


@router.post("/reconcile")
def reconcile(request: Request, body: Window):
    if body.end <= body.start or body.end-body.start > timedelta(days=8):
        raise HTTPException(422, "window must be at most eight days")
    with request.app.state.db.connection() as conn:
        rows = conn.execute(
            "UPDATE gold.derived_imputation_candidates d SET state='reconciled',"
            "observed_replacement_at=now(),updated_at=now() "
            "FROM silver.wave_observations o WHERE d.station_id=o.station_id "
            "AND d.observation_time=o.observed_at AND d.state='candidate' "
            "AND o.quality_status='valid' AND o.wave_height_m IS NOT NULL "
            "AND d.observation_time>=%s AND d.observation_time<%s "
            "RETURNING d.observation_time",
            (body.start, body.end),
        ).fetchall()
    return {"reconciled": len(rows), "affected_observation_times": [row["observation_time"].isoformat() for row in rows]}
