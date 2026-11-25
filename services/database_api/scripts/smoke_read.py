"""Read-only API smoke against the configured PostgreSQL database."""
from fastapi.testclient import TestClient

from services.database_api.app.main import create_app


def main():
    with TestClient(create_app()) as client:
        for path in (
            "/health/ready",
            "/api/v1/stations/51201",
            "/api/v1/observations/latest",
            "/api/v1/observations?start=2026-11-01T00:00:00Z&end=2026-11-02T00:00:00Z",
            "/api/v1/metrics/summary?metric=WVHT&group_by=day",
            "/api/v1/analytics/trend-seasonal?metric=WVHT&period=month",
            "/api/v1/analytics/correlations?method=pearson",
            "/api/v1/analytics/histogram?metric=WVHT",
            "/api/v1/analytics/scatter?x=WVHT&y=DPD",
            "/api/v1/clusters?start_hst=2026-11-01&end_hst=2026-11-02",
            "/api/v1/clusters/profiles?model_id=1",
            "/api/v1/predictions/latest?target_metric=WVHT",
            "/api/v1/predictions/history?start_hst=2026-11-01&end_hst=2026-11-02",
            "/api/v1/classifications/latest",
            "/api/v1/classifications/schemes",
            "/api/v1/models",
            "/api/v1/models/1/evaluations",
            "/api/v1/models/1/evaluation-samples",
            "/api/v1/data-quality/overview?start_hst=2026-11-01&end_hst=2026-11-02",
            "/api/v1/data-freshness",
        ):
            response = client.get(path)
            assert response.status_code == 200, (path, response.status_code, response.text)
            print(path, response.status_code, response.json().get("status", "ready"))


if __name__ == "__main__":
    main()
