"""Minimal executable scaffold, not an implemented NOAA backend.

All seven worker roles can be started independently for network/health testing.
Production endpoints in docs/05_API_CONTRACT.md must be implemented separately.
"""
from __future__ import annotations

import os

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel


class HealthResponse(BaseModel):
    status: str
    service: str
    version: str


def create_app(service_name: str | None = None) -> FastAPI:
    service_name = service_name or os.getenv("SERVICE_NAME", "database-api")
    app = FastAPI(
        title=f"Waimea WaveCast {service_name} — infrastructure scaffold",
        version="0.1.0",
        docs_url="/docs" if os.getenv("ENABLE_DOCS", "false").lower() == "true" else None,
        redoc_url=None,
    )

    @app.get("/health/live", response_model=HealthResponse)
    def liveness() -> HealthResponse:
        """Process liveness only; NOT a DB/GCS readiness assertion."""
        return HealthResponse(status="live", service=service_name, version="0.1.0")

    @app.get("/health/ready", response_model=HealthResponse)
    def readiness() -> HealthResponse:
        """Reject readiness until DB, GCS, and downstream checks are coded."""
        raise HTTPException(status_code=503, detail="readiness checks not implemented")

    @app.get("/api/v1/_scaffold", include_in_schema=False)
    def scaffold_warning() -> dict[str, str]:
        """Do not present this route as a real business API."""
        raise HTTPException(status_code=501, detail="business API not implemented")

    return app


app = create_app()
