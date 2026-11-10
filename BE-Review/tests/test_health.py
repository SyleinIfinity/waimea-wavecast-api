from fastapi.testclient import TestClient
from app.main import create_app


def test_process_liveness():
    client = TestClient(create_app("ingestion"))
    response = client.get("/health/live")
    assert response.status_code == 200
    assert response.json()["service"] == "ingestion"


def test_readiness_not_fake():
    client = TestClient(create_app("database-api"))
    assert client.get("/health/ready").status_code == 503


def test_unimplemented_routes_do_not_return_success():
    client = TestClient(create_app("database-api"))
    assert client.get("/api/v1/_scaffold").status_code == 501
