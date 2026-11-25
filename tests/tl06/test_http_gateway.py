"""HTTP gateway wire contract, retries and authentication."""
from __future__ import annotations

from datetime import datetime, timezone

import httpx
import pytest

from packages.db.gateway import RawObservationDTO, SilverObservationDTO, SourceFileDTO
from packages.db.http import HttpDatabaseGateway


def test_source_raw_silver_contract_and_stable_retry_key():
    seen = []
    attempts = 0

    def handle(request):
        nonlocal attempts
        assert request.headers["X-Internal-Token"] == "private-token"
        seen.append((request.url.path, request.headers.get("Idempotency-Key"), request.content))
        if request.url.path.endswith("source-files"):
            return httpx.Response(200, json={"file_id": 7})
        if request.url.path.endswith("raw-observations/bulk"):
            attempts += 1
            if attempts == 1:
                raise httpx.ConnectError("response lost")
            return httpx.Response(200, json={"inserted": 1, "duplicate": 0, "raw_ids": {"a" * 64: 99}})
        return httpx.Response(200, json={"inserted": 1, "updated": 0, "duplicate": 0, "skipped": 0})

    client = httpx.Client(transport=httpx.MockTransport(handle))
    gw = HttpDatabaseGateway("http://database-api:8000", "private-token", client=client)
    file = SourceFileDTO("51201", 0, "realtime", "raw.txt", "https://www.ndbc.noaa.gov/raw.txt",
                         "ndbc_stdmet_txt", "gs://waimea-noaa/noaa/raw/raw.txt", 123, "a" * 64, 5, "none")
    assert gw.register_source_file(file) == 7
    raw = RawObservationDTO(7, "51201", 1, datetime(2023, 6, 1, tzinfo=timezone.utc), "line", {"WVHT": "1.5"}, "a" * 64)
    result = gw.upsert_raw_batch([raw])
    assert result["raw_ids"]["a" * 64] == 99
    silver = SilverObservationDTO("51201", raw.observed_at, "realtime", "noaa", 7, 99, "b" * 64)
    assert gw.upsert_silver_batch([silver])["revised"] == 0
    assert seen[1][1] == seen[2][1]
    assert b"private-token" not in b"".join(body for _, _, body in seen)


def test_auth_failure_is_not_retried():
    calls = 0

    def handle(request):
        nonlocal calls
        calls += 1
        return httpx.Response(401, json={"error": "unauthorized"})

    gw = HttpDatabaseGateway("http://database-api:8000", "bad-token", client=httpx.Client(transport=httpx.MockTransport(handle)))
    with pytest.raises(httpx.HTTPStatusError):
        gw.ready()
    assert calls == 1


def test_transient_503_retried_with_same_key(monkeypatch):
    monkeypatch.setattr("packages.db.http.time.sleep", lambda _: None)
    keys = []

    def handle(request):
        keys.append(request.headers["Idempotency-Key"])
        return httpx.Response(503 if len(keys) == 1 else 200, json={"file_id": 4})

    gw = HttpDatabaseGateway("http://database-api:8000", "private-token", client=httpx.Client(transport=httpx.MockTransport(handle)))
    file = SourceFileDTO("51201", 0, "realtime", "raw.txt", "https://www.ndbc.noaa.gov/raw.txt",
                         "ndbc_stdmet_txt", "gs://waimea-noaa/noaa/raw/raw.txt", 123, "a" * 64, 5, "none")
    assert gw.register_source_file(file) == 4
    assert len(keys) == 2 and keys[0] == keys[1]
