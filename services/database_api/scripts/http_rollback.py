"""End-to-end HTTP/SQL fixture check in one database transaction; rolls back."""
from contextlib import contextmanager
from uuid import uuid4

import psycopg
from fastapi.testclient import TestClient
from psycopg.rows import dict_row

from services.database_api.app.config import Settings
from services.database_api.app.main import create_app


class TransactionalDB:
    def __init__(self, conn):
        self.conn = conn

    @contextmanager
    def connection(self):
        yield self.conn

    def ready(self):
        return self.conn.execute("SELECT 1").fetchone()["?column?"] == 1

    def close(self):
        pass


def main():
    settings = Settings(internal_api_token="tl01-rollback-token", admin_api_token="tl01-rollback-admin", app_env="development")
    marker = uuid4().hex
    conn = psycopg.connect(
        host=settings.db_host, port=settings.db_port, dbname=settings.db_name,
        user=settings.db_user, password=settings.db_password,
        sslmode=settings.db_sslmode, connect_timeout=5, row_factory=dict_row,
    )
    try:
        with TestClient(create_app(settings, TransactionalDB(conn))) as client:
            headers = {"X-Internal-Token": settings.internal_api_token, "Idempotency-Key": marker}
            source = client.post("/internal/v1/source-files", json={
                "station_id": "51201", "feed_key": "realtime", "original_filename": "tl01-http-rollback.txt",
                "source_url": "https://example.invalid/tl01-http-rollback", "storage_format": "txt",
                "local_path": "tl01-http-rollback", "content_sha256": marker * 2,
            }, headers=headers)
            assert source.status_code == 200, source.text
            file_id = source.json()["file_id"]
            raw = client.post("/internal/v1/raw-observations/bulk", json={"file_id": file_id, "rows": [{"row_sha256": marker * 2, "raw_line": "rollback fixture"}]}, headers=headers)
            assert raw.status_code == 200 and raw.json()["inserted"] == 1, raw.text
            observed = "2004-01-01T00:00:00Z"
            silver = client.post("/internal/v1/silver/observations/bulk-upsert", json={"rows": [{
                "observed_at": observed, "source_kind": "realtime", "source_dataset": "tl01-rollback",
                "source_file_id": file_id, "measurement_sha256": marker * 2, "wave_height_m": 1.25,
            }, {
                "observed_at": "2004-01-01T01:00:00Z", "source_kind": "realtime", "source_dataset": "tl01-rollback",
                "source_file_id": file_id, "measurement_sha256": marker[::-1] * 2, "wave_height_m": 2.25,
            }]}, headers=headers)
            assert silver.status_code == 200 and silver.json()["inserted"] == 2, silver.text
            gold = client.post("/internal/v1/gold/results/bulk", json={"table": "time_series_metrics", "rows": [{
                "station_id": "51201", "period_type": "day", "period_start_hst": "2004-01-01",
                "metric_code": "WVHT", "sample_count": 2, "valid_sample_count": 2, "mean_value": 1.75,
            }]}, headers=headers)
            assert gold.status_code == 200 and gold.json()["saved"] == 1, gold.text
            observations = client.get("/api/v1/observations", params={"start": observed, "end": "2004-01-02T00:00:00Z"})
            assert observations.status_code == 200 and observations.json()["data"][0]["wave_height_m"] == 2.25, observations.text
            latest = client.get("/api/v1/observations/latest")
            assert latest.status_code == 200 and latest.json()["data"]["wave_height_m"] == 2.25, latest.text
            first_page = client.get("/api/v1/observations", params={"start": observed, "end": "2004-01-02T00:00:00Z", "limit": 1}).json()
            assert first_page["has_more"] and first_page["next_cursor"]
            second_page = client.get("/api/v1/observations", params={"start": observed, "end": "2004-01-02T00:00:00Z", "limit": 1, "cursor": first_page["next_cursor"]}).json()
            assert len(second_page["data"]) == 1 and not second_page["has_more"]
            metrics = client.get("/api/v1/metrics/summary", params={"metric": "WVHT", "group_by": "day", "start_hst": "2004-01-01", "end_hst": "2004-01-02"})
            assert metrics.status_code == 200 and metrics.json()["data"][0]["mean_value"] == 1.75, metrics.text
            histogram = client.get("/api/v1/analytics/histogram", params={"metric": "WVHT", "bins": 2, "start": observed, "end": "2004-01-02T00:00:00Z"})
            assert histogram.status_code == 200 and [part["count"] for part in histogram.json()["data"]] == [1, 1], histogram.text
            split = client.post("/internal/v1/ml/dataset-splits", json={"dataset_name": "tl01-http-rollback-" + marker, "dataset_version": "v1", "task_type": "regression", "target_metric": "WVHT", "train_start_hst": "2004-01-01", "train_end_hst": "2004-01-02"}, headers=headers)
            assert split.status_code == 200, split.text
            model = client.post("/internal/v1/models", json={"model_name": "tl01-http-rollback-" + marker, "model_version": "v1", "task_type": "regression", "algorithm": "rollback-fixture", "artifact_gcs_uri": "gs://rollback-fixture/model.joblib", "artifact_generation": "1", "artifact_sha256": marker * 2}, headers=headers)
            assert model.status_code == 200, model.text
            model_id = model.json()["model_id"]
            training = client.post("/internal/v1/ml/training-runs", json={"dataset_split_id": split.json()["dataset_split_id"], "model_id": model_id, "task_type": "regression", "algorithm": "rollback-fixture", "status": "success"}, headers=headers)
            assert training.status_code == 200, training.text
            evaluation = client.post("/internal/v1/ml/evaluations", json={"training_run_id": training.json()["training_run_id"], "model_id": model_id, "dataset_split_id": split.json()["dataset_split_id"], "evaluation_stage": "test", "task_type": "regression", "sample_count": 1, "mae": 0.1}, headers=headers)
            assert evaluation.status_code == 200, evaluation.text
            promoted = client.post(f"/internal/v1/models/{model_id}/promote", headers=headers)
            assert promoted.status_code == 200 and promoted.json()["status"] == "ready", promoted.text
            print("PASS: HTTP Bronze/Silver/Gold, cursor, ML metadata/promotion and public GETs")
    finally:
        conn.rollback()
        conn.close()
        print("ROLLBACK: no HTTP test rows retained")


if __name__ == "__main__":
    main()
