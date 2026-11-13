"""Isolated orchestration failure recovery; this is not a live NOAA event."""
import httpx
from datetime import datetime, timezone

from services.scheduler.app.tl07b_runner import run_once


def test_pinned_replay_resumes_downstream_without_new_noaa_fetch(monkeypatch):
    monkeypatch.setenv("INTERNAL_API_TOKEN", "isolated-token")
    monkeypatch.setenv("DATABASE_API_URL", "http://database-api:8000")
    monkeypatch.setenv("INGESTION_WORKER_URL", "http://ingestion:8000")
    monkeypatch.setenv("PREPROCESSING_WORKER_URL", "http://preprocessing:8000")
    calls = []
    slot = "2026-11-12T11:30:00+00:00"
    changed = "2026-11-12T10:26:00+00:00"

    def handler(request):
        calls.append((request.url.path, request.content))
        path = request.url.path
        if path.endswith("/cycles/due"):
            data = {"slots": [slot]}
        elif path.endswith("/cycles/claim"):
            data = {"claimed": True, "slot_at": slot, "lease_token": "isolated-lease",
                    "attempt_count": 2, "failure_stage": "PREPROCESSING",
                    "gcs_uri": "gs://waimea-noaa/noaa/raw/realtime/2026/11/12/object.txt",
                    "gcs_generation": 123, "changed_from": changed, "changed_to": changed}
        elif path.endswith("/ingestion/replay"):
            data = {"success": True, "silver_inserted": 0, "silver_revised": 0,
                    "latest_observed_at": changed, "gcs_uri": "gs://waimea-noaa/noaa/raw/realtime/2026/11/12/object.txt",
                    "gcs_generation": 123, "changed_from": None, "changed_to": None}
        elif path.endswith("/cycles/finish"):
            data = {"status": "SUCCESS", "next_attempt_at": None}
        else:
            data = {"ok": True}
        return httpx.Response(200, json=data)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = run_once(datetime(2026, 11, 12, 12, tzinfo=timezone.utc), client)
    assert result["status"] == "SUCCESS"
    paths = [path for path, _ in calls]
    assert "/internal/v1/ingestion/replay" in paths
    assert "/internal/v1/ingestion/poll" not in paths
    assert "/internal/v1/preprocessing/quality/review" in paths
    assert "/internal/v1/derived/reconcile" in paths
    assert "/internal/v1/preprocessing/gold/rebuild" in paths
    assert b'"generation":123' in next(body for path, body in calls if path.endswith("/ingestion/replay"))
