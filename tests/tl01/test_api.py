from contextlib import contextmanager
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from services.database_api.app.config import Settings
from services.database_api.app.main import create_app
from services.database_api.app.repositories.gold import save_gold_results
from services.database_api.app.schemas.analytics import GoldBatch


class FakeResult:
    def __init__(self, row=None, rows=None):
        self.row = row
        self.rows = rows or []
        self.rowcount = 2

    def fetchone(self):
        return self.row

    def fetchall(self):
        return self.rows


class FakeConnection:
    def execute(self, query, params=None):
        if isinstance(query, str) and query.startswith("SELECT raw_id"):
            return FakeResult({"raw_id": 1})
        return FakeResult()


class FakeDB:
    @contextmanager
    def connection(self):
        yield FakeConnection()

    def ready(self):
        return True

    def close(self):
        pass


@pytest.fixture
def client():
    app = create_app(Settings(internal_api_token="internal-test", admin_api_token="admin-test", app_env="production"), FakeDB())
    with TestClient(app) as test_client:
        yield test_client


def test_health_and_empty_public_response(client):
    assert client.get("/health/ready").status_code == 200
    response = client.get("/api/v1/observations/latest")
    assert response.status_code == 200
    assert response.json() == {"status": "empty", "data_type": "observation", "data": None}


def test_internal_requires_token_and_idempotency(client):
    payload = {"file_id": 1, "rows": [{"row_sha256": "a" * 64, "raw_line": "test"}]}
    assert client.post("/internal/v1/raw-observations/bulk", json=payload).status_code == 401
    assert client.post("/internal/v1/raw-observations/bulk", json=payload, headers={"X-Internal-Token": "internal-test"}).status_code == 422
    accepted = client.post("/internal/v1/raw-observations/bulk", json=payload, headers={"X-Internal-Token": "internal-test", "Idempotency-Key": "raw-test"})
    assert accepted.status_code == 200
    assert accepted.json() == {"inserted": 0, "duplicate": 1, "raw_ids": {"a" * 64: 1}}


def test_production_delete_is_blocked(client):
    response = client.post("/api/v1/admin/demo/patterns/preview-delete", json={"station_id": "51201", "from_hst": "2026-11-01", "to_hst": "2026-11-02", "pattern_types": ["time_series_metrics"]}, headers={"X-Admin-Token": "admin-test"})
    assert response.status_code == 403


def test_admin_cors_preflight_for_frontend(client):
    response = client.options(
        "/api/v1/admin/demo/patterns/rebuild",
        headers={"Origin": "http://localhost:5173", "Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "x-admin-token,idempotency-key,content-type"},
    )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:5173"


def test_bad_time_window_and_bad_metric(client):
    assert client.get("/api/v1/observations", params={"start": "2026-01-01T00:00:00", "end": "2026-01-02T00:00:00"}).status_code == 422
    response = client.get("/api/v1/metrics/summary", params={"metric": "arbitrary SQL"}, headers={"X-Request-ID": "tl01-error-test"})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INVALID_PARAMETER"
    assert response.json()["error"]["request_id"] == "tl01-error-test"


def test_gold_bulk_rejects_unlisted_columns():
    batch = GoldBatch(table="time_series_metrics", rows=[{"station_id": "51201", "period_type": "day", "period_start_hst": "2026-11-01", "metric_code": "WVHT", "drop_database": True}])
    with pytest.raises(ValueError):
        save_gold_results(FakeConnection(), batch)


def test_staging_delete_requires_preview_and_only_scoped_gold_sql():
    statements = []

    class DemoConnection(FakeConnection):
        def execute(self, query, params=None):
            statements.append((query, params))
            if query.startswith("SELECT count"):
                return FakeResult({"n": 2})
            return FakeResult()

    class DemoDB(FakeDB):
        @contextmanager
        def connection(self):
            yield DemoConnection()

    settings = Settings(admin_api_token="admin-test", app_env="staging", demo_delete_enabled=True, demo_backup_confirmed=True)
    with TestClient(create_app(settings, DemoDB())) as test_client:
        headers = {"X-Admin-Token": "admin-test", "X-Actor-ID": "tl01-test"}
        scope = {"station_id": "51201", "from_hst": "2026-11-01", "to_hst": "2026-11-02", "pattern_types": ["time_series_metrics"]}
        preview = test_client.post("/api/v1/admin/demo/patterns/preview-delete", json=scope, headers=headers)
        assert preview.status_code == 200
        assert preview.json()["row_count"] == 2
        body = {"dry_run_id": preview.json()["dry_run_id"], "confirmation": "DELETE GOLD DEMO"}
        deleted = test_client.post("/api/v1/admin/demo/patterns/delete", json=body, headers=headers)
        assert deleted.status_code == 200
        assert deleted.json()["deleted"] == {"time_series_metrics": 2}
        assert test_client.post("/api/v1/admin/demo/patterns/delete", json=body, headers=headers).status_code == 409
    delete_sql = [query for query, _ in statements if query.startswith("DELETE")]
    assert delete_sql == ["DELETE FROM gold.time_series_metrics WHERE station_id=%s AND period_start_hst BETWEEN %s AND %s"]


def test_admin_rebuild_forwards_without_training(monkeypatch):
    calls = []

    class WorkerResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {"job_id": 12}

    def post(url, json, headers, timeout):
        calls.append((url, json, headers))
        return WorkerResponse()

    monkeypatch.setattr("services.database_api.app.api.admin.httpx.post", post)
    settings = Settings(admin_api_token="admin-test", internal_api_token="internal-test")
    with TestClient(create_app(settings, FakeDB())) as test_client:
        response = test_client.post(
            "/api/v1/admin/demo/patterns/rebuild",
            json={"station_id": "51201", "from_hst": "2026-11-01", "to_hst": "2026-11-02", "pattern_types": ["time_series_metrics"]},
            headers={"X-Admin-Token": "admin-test", "Idempotency-Key": "test-rebuild"},
        )
    assert response.status_code == 202
    assert response.json()["jobs"][0]["worker_response"]["job_id"] == 12
    assert calls[0][1]["retrain"] is False
    assert calls[0][2]["X-Internal-Token"] == "internal-test"


def test_unavailable_database_and_worker_return_503(monkeypatch):
    import httpx

    class DownDB(FakeDB):
        def ready(self):
            raise ConnectionError("offline")

    with TestClient(create_app(Settings(), DownDB())) as test_client:
        assert test_client.get("/health/ready").status_code == 503

    def fail(*args, **kwargs):
        raise httpx.ConnectError("offline")

    monkeypatch.setattr("services.database_api.app.api.admin.httpx.post", fail)
    settings = Settings(admin_api_token="admin-test", internal_api_token="internal-test")
    with TestClient(create_app(settings, FakeDB())) as test_client:
        response = test_client.post(
            "/api/v1/admin/demo/patterns/rebuild",
            json={"station_id": "51201", "from_hst": "2026-11-01", "to_hst": "2026-11-02", "pattern_types": ["time_series_metrics"]},
            headers={"X-Admin-Token": "admin-test", "Idempotency-Key": "worker-down"},
        )
    assert response.status_code == 503
