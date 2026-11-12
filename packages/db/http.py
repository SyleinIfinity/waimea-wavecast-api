"""HTTP implementation of the TL04 database gateway.

All writes go through Database API. Stable idempotency keys make retries safe
when a response is lost after the API committed the transaction.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import asdict, fields, is_dataclass
from datetime import date, datetime
from typing import Any

import httpx

from packages.db.gateway import DailyFeaturesDTO, JobStatusDTO, RawObservationDTO, SilverObservationDTO, SourceFileDTO


def _jsonable(value: Any) -> Any:
    if is_dataclass(value):
        return _jsonable(asdict(value))
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value


def _key(operation: str, body: dict) -> str:
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"), default=str)
    return f"tl06:{operation}:{hashlib.sha256(canonical.encode()).hexdigest()}"


class HttpDatabaseGateway:
    def __init__(self, base_url: str | None = None, token: str | None = None,
                 client: httpx.Client | None = None, retries: int = 2) -> None:
        self.base_url = (base_url or os.getenv("DATABASE_API_URL", "")).rstrip("/")
        self.token = token or os.getenv("INTERNAL_API_TOKEN", "")
        if not self.base_url or not self.token:
            raise ValueError("DATABASE_API_URL and INTERNAL_API_TOKEN are required")
        if retries < 0:
            raise ValueError("retries must be nonnegative")
        self.retries = retries
        self._client = client or httpx.Client(timeout=15)
        self._owns_client = client is None

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def ready(self) -> bool:
        return self._request("GET", "/health/ready").get("status") == "ready"

    def _request(self, method: str, path: str, *, body: dict | None = None,
                 params: dict | None = None, key: str | None = None) -> dict:
        headers = {"X-Internal-Token": self.token}
        if key:
            headers["Idempotency-Key"] = key
        for attempt in range(self.retries + 1):
            try:
                response = self._client.request(method, self.base_url + path,
                                                json=body, params=params, headers=headers)
                if response.status_code in (429, 500, 502, 503, 504) and attempt < self.retries:
                    time.sleep(min(0.25 * 2 ** attempt, 1))
                    continue
                response.raise_for_status()
                return response.json()
            except httpx.TransportError:
                if attempt == self.retries:
                    raise
                time.sleep(min(0.25 * 2 ** attempt, 1))
        raise RuntimeError("unreachable retry state")

    def register_source_file(self, dto: SourceFileDTO) -> int:
        body = _jsonable(dto)
        body.pop("feed_id", None)  # Database API resolves the real feed_id.
        body["gcs_generation"] = str(dto.gcs_generation)
        response = self._request("POST", "/internal/v1/source-files", body=body, key=_key("source", body))
        return int(response["file_id"])

    def upsert_raw_batch(self, rows: list[RawObservationDTO]) -> dict:
        if not rows:
            return {"inserted": 0, "duplicate": 0, "raw_ids": {}}
        file_id = rows[0].file_id
        if any(row.file_id != file_id for row in rows):
            raise ValueError("raw batch must contain one source file")
        body = {"file_id": file_id, "rows": [_jsonable({key: value for key, value in asdict(row).items() if key != "file_id"}) for row in rows]}
        return self._request("POST", "/internal/v1/raw-observations/bulk", body=body, key=_key("raw", body))

    def upsert_silver_batch(self, rows: list[SilverObservationDTO]) -> dict:
        if not rows:
            return {"inserted": 0, "updated": 0, "revised": 0, "duplicate": 0, "skipped": 0}
        body = {"rows": _jsonable(rows)}
        result = self._request("POST", "/internal/v1/silver/observations/bulk-upsert", body=body, key=_key("silver", body))
        result.setdefault("revised", result.get("updated", 0))
        return result

    def upsert_daily_features(self, rows: list[DailyFeaturesDTO]) -> dict:
        for row in rows:
            body = _jsonable(row)
            self._request("PUT", "/internal/v1/silver/daily-features", body=body, key=_key("daily", body))
        return {"upserted": len(rows)}

    def get_observations(self, station_id: str, start_at: datetime, end_at: datetime) -> list[SilverObservationDTO]:
        if station_id != "51201":
            raise ValueError("only station 51201 is supported")
        response = self._request("GET", "/internal/v1/silver/observations",
                                 params={"start": start_at.isoformat(), "end": end_at.isoformat(), "limit": 1000})
        rows = response["data"]
        if len(rows) == 1000:
            raise ValueError("observation page full; split the requested window")
        names = {field.name for field in fields(SilverObservationDTO)}
        result = []
        for row in rows:
            values = {key: value for key, value in row.items() if key in names}
            if isinstance(values.get("observed_at"), str):
                values["observed_at"] = datetime.fromisoformat(values["observed_at"].replace("Z", "+00:00"))
            result.append(SilverObservationDTO(**values))
        return result

    def get_daily_features(self, station_id: str, start_hst: date, end_hst: date) -> list[DailyFeaturesDTO]:
        if station_id != "51201":
            raise ValueError("only station 51201 is supported")
        response = self._request("GET", "/internal/v1/silver/daily-features",
                                 params={"start_hst": start_hst.isoformat(), "end_hst": end_hst.isoformat(), "limit": 1000})
        rows = response["data"]
        if len(rows) == 1000:
            raise ValueError("daily feature page full; split the requested window")
        names = {field.name for field in fields(DailyFeaturesDTO)}
        result = []
        for row in rows:
            values = {key: value for key, value in row.items() if key in names}
            if isinstance(values.get("date_hst"), str):
                values["date_hst"] = date.fromisoformat(values["date_hst"])
            result.append(DailyFeaturesDTO(**values))
        return result

    def save_job_status(self, dto: JobStatusDTO) -> None:
        if not dto.job_id.isdecimal():
            raise ValueError("Database API job ID must be numeric")
        body = {"status": dto.status, "error_message": dto.error_message}
        self._request("PATCH", f"/internal/v1/jobs/{dto.job_id}", body=body)

    def get_job_status(self, job_id: str) -> JobStatusDTO | None:
        if not job_id.isdecimal():
            raise ValueError("Database API job ID must be numeric")
        try:
            row = self._request("GET", f"/internal/v1/jobs/{job_id}")
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                return None
            raise
        return JobStatusDTO(job_id=job_id, job_type=row["job_type"], status=row["status"],
                            started_at=datetime.fromisoformat(row["started_at"].replace("Z", "+00:00")),
                            finished_at=datetime.fromisoformat(row["finished_at"].replace("Z", "+00:00")) if row.get("finished_at") else None,
                            error_message=row.get("error_message"), details=row.get("parameters") or {})
