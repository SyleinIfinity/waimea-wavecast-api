import os
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import create_engine, text

app = FastAPI(title="Waimea WaveCast Database API", version="0.1.0")
origins = [s.strip() for s in os.getenv("ALLOWED_ORIGINS", "").split(",") if s.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=False,
    allow_methods=["GET"],
    allow_headers=["Content-Type"],
)

_engine = None

def get_engine():
    global _engine
    if _engine is None:
        url = os.environ.get("DATABASE_URL")
        if not url:
            raise HTTPException(status_code=503, detail="Database connection not configured")
        _engine = create_engine(url, pool_pre_ping=True, pool_size=3, max_overflow=2)
    return _engine

@app.get("/health")
def health():
    try:
        with get_engine().connect() as conn:
            conn.execute(text("SELECT 1"))
        return {"status": "ok", "service": "database-api"}
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Database unavailable") from exc

@app.get("/api/v1/observations/latest")
def latest_observation():
    sql = text("""
      SELECT station_id, source, observed_at, wave_height_m,
             dominant_period_s, wave_direction_deg, sea_temperature_c, quality_flag
      FROM silver.wave_observations
      ORDER BY observed_at DESC LIMIT 1
    """)
    try:
        with get_engine().connect() as conn:
            row = conn.execute(sql).mappings().first()
        return {"status": "ok" if row else "empty", "data_type": "observation", "data": dict(row) if row else None}
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Observations query unavailable") from exc
