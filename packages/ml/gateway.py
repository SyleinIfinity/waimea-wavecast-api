"""TL05 database boundary: an offline fake and the TL01 HTTP adapter."""
from __future__ import annotations

import os
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Protocol
from uuid import uuid4

import httpx


class MLGateway(Protocol):
    def get_daily_features(self, start: date, end: date) -> list[dict]: ...
    def get_observations(self, start: datetime, end: datetime) -> list[dict]: ...
    def save_gold_results(self, table: str, rows: list[dict], key: str) -> dict: ...
    def create_job(self, body: dict, key: str) -> dict: ...
    def update_job(self, job_id: int, status: str, count: int = 0, error: str | None = None) -> dict: ...
    def get_ready_model(self, task: str) -> dict | None: ...


class FakeMLGateway:
    def __init__(self, daily: list[dict] | None = None, observations: list[dict] | None = None):
        self.daily = list(daily or [])
        self.observations = list(observations or [])
        self.gold: dict[str, list[dict]] = {}
        self.jobs: dict[int, dict] = {}
        self.models: dict[int, dict] = {}
        self.training_runs: list[dict] = []
        self.evaluations: list[dict] = []
        self.evaluation_samples: list[dict] = []
        self.dataset_splits: list[dict] = []
        self._keys: dict[str, int] = {}

    def get_daily_features(self, start: date, end: date) -> list[dict]:
        return [row for row in self.daily if start <= date.fromisoformat(str(row["date_hst"])) <= end]

    def get_observations(self, start: datetime, end: datetime) -> list[dict]:
        def timestamp(row: dict) -> datetime:
            value = row["observed_at"]
            return datetime.fromisoformat(value.replace("Z", "+00:00")) if isinstance(value, str) else value
        return [row for row in self.observations if start <= timestamp(row) < end]

    def save_gold_results(self, table: str, rows: list[dict], key: str) -> dict:
        if not rows:
            return {"saved": 0, "table": table}
        self.gold.setdefault(table, [])
        self.gold[table] = [row for row in self.gold[table] if row not in rows] + list(rows)
        return {"saved": len(rows), "table": table}

    def create_job(self, body: dict, key: str) -> dict:
        if key in self._keys:
            existing = self._keys[key]
            return {"processing_run_id": existing, "duplicate": True, "status": self.jobs[existing]["status"]}
        job_id = len(self.jobs) + 1
        self._keys[key] = job_id
        self.jobs[job_id] = {**body, "processing_run_id": job_id, "status": "running"}
        return {"processing_run_id": job_id}

    def update_job(self, job_id: int, status: str, count: int = 0, error: str | None = None) -> dict:
        self.jobs[job_id].update(status=status, output_record_count=count, error_message=error)
        return self.jobs[job_id]

    def get_ready_model(self, task: str) -> dict | None:
        return next((row for row in self.models.values() if row["task_type"] == task and row["status"] == "ready"), None)

    def save_model(self, body: dict, key: str) -> dict:
        model_id = len(self.models) + 1
        self.models[model_id] = {**body, "model_id": model_id}
        return {"model_id": model_id}

    def save_training_run(self, body: dict, key: str) -> dict:
        run_id = len(self.training_runs) + 1
        self.training_runs.append({**body, "training_run_id": run_id})
        return {"training_run_id": run_id}

    def save_dataset_split(self, body: dict, key: str) -> dict:
        split_id = len(self.dataset_splits) + 1
        self.dataset_splits.append({**body, "dataset_split_id": split_id})
        return {"dataset_split_id": split_id}

    def save_scheme(self, body: dict, key: str) -> dict:
        return {"scheme_id": 1}

    def save_centroid(self, body: dict, key: str) -> dict:
        self.gold.setdefault("cluster_centroids", []).append(body)
        return {"model_id": body["model_id"], "cluster_label": body["cluster_label"]}

    def save_evaluation(self, body: dict, key: str) -> dict:
        evaluation_id = len(self.evaluations) + 1
        self.evaluations.append({**body, "evaluation_id": evaluation_id})
        return {"evaluation_id": evaluation_id}

    def save_evaluation_sample(self, body: dict, key: str) -> dict:
        sample_id = len(self.evaluation_samples) + 1
        self.evaluation_samples.append({**body, "evaluation_sample_id": sample_id})
        return {"evaluation_sample_id": sample_id}


class HttpMLGateway:
    def __init__(self, base_url: str | None = None, token: str | None = None):
        self.base_url = (base_url or os.getenv("DATABASE_API_URL", "http://database-api:8000")).rstrip("/")
        self.token = token or os.getenv("INTERNAL_API_TOKEN", "")
        if not self.token:
            raise ValueError("INTERNAL_API_TOKEN is required")

    def _request(self, method: str, path: str, *, params: dict | None = None,
                 body: dict | None = None, key: str | None = None) -> dict:
        headers = {"X-Internal-Token": self.token}
        if key:
            headers["Idempotency-Key"] = key
        with httpx.Client(timeout=30) as client:
            response = client.request(method, self.base_url + path, params=params, json=body, headers=headers)
            response.raise_for_status()
            return response.json()

    def get_daily_features(self, start: date, end: date) -> list[dict]:
        result = []
        current = start
        while current <= end:
            last = min(current + timedelta(days=180), end)
            page = self._request("GET", "/internal/v1/silver/daily-features", params={"start_hst": current.isoformat(), "end_hst": last.isoformat(), "limit": 1000})
            result.extend(page["data"])
            current = last + timedelta(days=1)
        return result

    def get_observations(self, start: datetime, end: datetime) -> list[dict]:
        result = []
        current = start
        while current < end:
            last = min(current + timedelta(days=7), end)
            page = self._request("GET", "/internal/v1/silver/observations", params={"start": current.isoformat(), "end": last.isoformat(), "limit": 1000})
            if len(page["data"]) >= 1000:
                raise ValueError("observation page full; refusing truncated analytics")
            result.extend(row for row in page["data"] if datetime.fromisoformat(row["observed_at"].replace("Z", "+00:00")) < last)
            current = last
        return result

    def save_gold_results(self, table: str, rows: list[dict], key: str) -> dict:
        saved = 0
        for offset in range(0, len(rows), 500):
            result = self._request("POST", "/internal/v1/gold/results/bulk", body={"table": table, "rows": rows[offset:offset + 500]}, key=f"{key}:{offset}")
            saved += result["saved"]
        return {"saved": saved, "table": table}

    def create_job(self, body: dict, key: str) -> dict:
        result = self._request("POST", "/internal/v1/jobs", body=body, key=key)
        # TL01 returns `status` only for an existing idempotency key.
        return {**result, "duplicate": "status" in result}

    def update_job(self, job_id: int, status: str, count: int = 0, error: str | None = None) -> dict:
        return self._request("PATCH", f"/internal/v1/jobs/{job_id}", body={"status": status, "output_record_count": count, "error_message": error})

    def get_ready_model(self, task: str) -> dict | None:
        with httpx.Client(timeout=30) as client:
            response = client.get(self.base_url + "/api/v1/models", params={"task_type": task, "status": "ready", "limit": 1})
            response.raise_for_status()
            rows = response.json()["data"]
        return self._request("GET", f"/internal/v1/models/{rows[0]['model_id']}") if rows else None

    def save_model(self, body: dict, key: str) -> dict:
        return self._request("POST", "/internal/v1/models", body=body, key=key)

    def save_training_run(self, body: dict, key: str) -> dict:
        return self._request("POST", "/internal/v1/ml/training-runs", body=body, key=key)

    def save_dataset_split(self, body: dict, key: str) -> dict:
        return self._request("POST", "/internal/v1/ml/dataset-splits", body=body, key=key)

    def save_scheme(self, body: dict, key: str) -> dict:
        return self._request("POST", "/internal/v1/ml/classification-schemes", body=body, key=key)

    def save_centroid(self, body: dict, key: str) -> dict:
        return self._request("PUT", "/internal/v1/ml/cluster-centroids", body=body, key=key)

    def save_evaluation(self, body: dict, key: str) -> dict:
        return self._request("POST", "/internal/v1/ml/evaluations", body=body, key=key)

    def save_evaluation_sample(self, body: dict, key: str) -> dict:
        return self._request("POST", "/internal/v1/ml/evaluation-samples", body=body, key=key)
