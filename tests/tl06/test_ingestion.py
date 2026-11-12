"""TL06 contract and failure-path tests. No production writes."""
from __future__ import annotations

import gzip
from datetime import datetime, timezone
from unittest.mock import Mock

from fastapi.testclient import TestClient

from packages.db.fake import FakeDatabaseGateway
from packages.gcs.fake import FakeGCSClient
from services.ingestion.app.main import IngestionSettings, create_app
from services.ingestion.app.services.ingest_pipeline import replay_stored_object, run_ingest

HEADER = b"#YYYY MM DD hh mm WDIR WSPD GST WVHT DPD APD MWD PRES ATMP WTMP DEWP VIS PTDY TIDE\n"
UNITS = b"#yr mo dy hr mn deg m/s m/s m sec sec deg hPa deg deg deg nmi hPa ft\n"


def payload(wave_height: str = "1.5") -> bytes:
    line = f"2023 06 01 12 00 120 3.5 4.5 {wave_height} 8 7.0 100 1015 22 24 18 8 0.1 0.3\n".encode()
    return gzip.compress(HEADER + UNITS + line)


def source(data: bytes):
    return Mock(download=Mock(return_value=data))


def test_lineage_idempotency_and_correction():
    db, gcs = FakeDatabaseGateway(), FakeGCSClient()
    original = source(payload())
    first = run_ingest("realtime", 2023, noaa_client=original, gcs_client=gcs, db_gateway=db)
    second = run_ingest("realtime", 2023, noaa_client=original, gcs_client=gcs, db_gateway=db)
    correction = run_ingest("realtime", 2023, noaa_client=source(payload("2.5")), gcs_client=gcs, db_gateway=db)
    row = db.get_observations("51201", datetime(2023, 6, 1, tzinfo=timezone.utc), datetime(2023, 6, 2, tzinfo=timezone.utc))[0]
    assert first.success and first.raw_inserted == first.silver_inserted == 1
    assert second.success and second.raw_duplicate == second.silver_duplicate == 1
    assert correction.success and correction.silver_revised == 1
    assert row.wave_height_m == 2.5 and row.raw_id is not None
    assert (row.source_file_id, row.raw_id) in {(file_id, raw_id) for (file_id, _), raw_id in db._raw_ids.items()}
    assert len(gcs._store) == 2


def test_gcs_success_db_failure_then_replay():
    db, gcs = FakeDatabaseGateway(), FakeGCSClient()
    original = db.register_source_file
    calls = 0

    def fail_once(dto):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise ConnectionError("temporary database failure")
        return original(dto)

    db.register_source_file = fail_once
    noaa = source(payload())
    failed = run_ingest("realtime", 2023, noaa_client=noaa, gcs_client=gcs, db_gateway=db)
    recovered = replay_stored_object("realtime", 2023,
                                     object_path=failed.gcs_uri.removeprefix("gs://waimea-noaa/"),
                                     generation=failed.gcs_generation, gcs_client=gcs, db_gateway=db)
    assert not failed.success and failed.error.startswith("DB source file registration failed")
    assert recovered.success and recovered.silver_inserted == 1
    assert len(gcs._store) == 1 and len(db._source_files) == 1


def test_raw_committed_silver_failure_then_replay():
    db, gcs = FakeDatabaseGateway(), FakeGCSClient()
    original = db.upsert_silver_batch
    calls = 0

    def fail_once(rows):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise ConnectionError("silver temporarily unavailable")
        return original(rows)

    db.upsert_silver_batch = fail_once
    noaa = source(payload())
    failed = run_ingest("realtime", 2023, noaa_client=noaa, gcs_client=gcs, db_gateway=db)
    recovered = run_ingest("realtime", 2023, noaa_client=noaa, gcs_client=gcs, db_gateway=db)
    assert not failed.success and failed.raw_inserted == 1
    assert recovered.success and recovered.raw_duplicate == 1 and recovered.silver_inserted == 1
    assert len(db._raw_obs) == len(db._silver) == len(gcs._store) == 1


def test_empty_invalid_and_timeout_do_not_write():
    db, gcs = FakeDatabaseGateway(), FakeGCSClient()
    for client in (source(b""), source(b"garbage"), Mock(download=Mock(side_effect=TimeoutError("NOAA timeout")))):
        result = run_ingest("realtime", 2023, noaa_client=client, gcs_client=gcs, db_gateway=db)
        assert not result.success and result.error
    assert not gcs._store and not db._source_files


def test_internal_endpoint_requires_token_and_test_mode_is_explicit():
    settings = IngestionSettings(app_env="test", ingestion_adapter_mode="fake", internal_api_token="test-token", gcs_raw_bucket="test-bucket")
    with TestClient(create_app(settings, noaa_client=source(payload()))) as client:
        assert client.get("/health/live").status_code == 200
        assert client.get("/health/ready").status_code == 200
        assert client.post("/internal/v1/ingestion/poll").status_code == 401
        assert client.post("/internal/v1/ingestion/poll", headers={"X-Internal-Token": "wrong"}).status_code == 401
        assert client.post("/internal/v1/ingestion/poll", headers={"X-Internal-Token": "test-token"}).status_code == 200


def test_fake_mode_rejected_outside_test():
    settings = IngestionSettings(app_env="production", ingestion_adapter_mode="fake", internal_api_token="token", gcs_raw_bucket="bucket")
    try:
        create_app(settings)
    except RuntimeError as exc:
        assert "APP_ENV=test" in str(exc)
    else:
        raise AssertionError("fake production adapter accepted")
