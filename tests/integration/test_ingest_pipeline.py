"""
tests/integration/test_ingest_pipeline.py

Integration tests for the ingestion pipeline using FakeGCSClient + FakeDatabaseGateway.

Covers:
  - Full pipeline: raw bytes -> GCS -> DB -> Silver
  - Idempotent: running same file twice, same result
  - Source priority: annual does not get overwritten by realtime
  - 404 source unavailable → no fabricated data
  - GCS 412 (object already present, same hash) → reuse existing
  - Partial parse errors do not block successful rows
"""
from __future__ import annotations

import gzip
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from packages.common.errors import SourceUnavailableError
from packages.db.fake import FakeDatabaseGateway
from packages.gcs.fake import FakeGCSClient
from packages.noaa.client import NOAAClient
from services.ingestion.app.services.ingest_pipeline import run_ingest

HEADER = b"#YYYY MM DD hh mm WDIR WSPD GST WVHT DPD APD MWD PRES ATMP WTMP DEWP VIS PTDY TIDE\n"
UNITS  = b"#yr   mo dy hr mn degT m/s m/s  m   sec sec degT  hPa degC degC degC  nmi hPa  ft\n"


def _row(year=2023, month=6, day=1, hour=12, wvht="1.5"):
    return f"{year} {month:02d} {day:02d} {hour:02d} 00  120  3.5 4.5 {wvht}   8  7.0 100 1015 22.0 24.0 18.0   8  0.1  0.3\n".encode()


def _noaa_file(*rows: bytes) -> bytes:
    return gzip.compress(HEADER + UNITS + b"".join(rows))


def _mock_client(raw_bytes: bytes) -> NOAAClient:
    client = MagicMock(spec=NOAAClient)
    client.download.return_value = raw_bytes
    return client


def _unavailable_client() -> NOAAClient:
    client = MagicMock(spec=NOAAClient)
    client.download.side_effect = SourceUnavailableError("http://noaa/fake", 404)
    return client


class TestBasicIngest:
    def test_full_pipeline_success(self):
        raw = _noaa_file(_row(2023, 6, 1, 12, "1.5"), _row(2023, 6, 1, 14, "2.0"))
        gcs = FakeGCSClient()
        db = FakeDatabaseGateway()
        result = run_ingest(
            "historical_annual", 2023,
            noaa_client=_mock_client(raw),
            gcs_client=gcs,
            db_gateway=db,
        )
        assert result.success
        assert result.silver_inserted == 2
        assert result.silver_duplicate == 0
        assert result.file_id is not None

    def test_silver_rows_have_correct_wave_height(self):
        raw = _noaa_file(_row(2023, 6, 1, 12, "3.14"))
        gcs = FakeGCSClient()
        db = FakeDatabaseGateway()
        run_ingest("historical_annual", 2023, noaa_client=_mock_client(raw), gcs_client=gcs, db_gateway=db)
        # Verify Silver has the row
        obs = db.get_observations(
            "51201",
            datetime(2023, 6, 1, 0, 0, tzinfo=timezone.utc),
            datetime(2023, 6, 2, 0, 0, tzinfo=timezone.utc),
        )
        assert any(abs((o.wave_height_m or 0) - 3.14) < 0.01 for o in obs)


class TestIdempotency:
    def test_same_file_twice_no_duplicate_silver(self):
        raw = _noaa_file(_row(2023, 6, 1, 12, "1.5"))
        gcs = FakeGCSClient()
        db = FakeDatabaseGateway()
        client = _mock_client(raw)

        r1 = run_ingest("historical_annual", 2023, noaa_client=client, gcs_client=gcs, db_gateway=db)
        r2 = run_ingest("historical_annual", 2023, noaa_client=client, gcs_client=gcs, db_gateway=db)

        assert r1.success and r2.success
        # Second run: same file hash → GCS reuse, DB row is duplicate
        assert r2.silver_duplicate == 1
        assert r2.silver_inserted == 0

    def test_gcs_object_reused_on_second_run(self):
        raw = _noaa_file(_row(2023, 6, 1, 12, "1.5"))
        gcs = FakeGCSClient()
        db = FakeDatabaseGateway()
        client = _mock_client(raw)

        r1 = run_ingest("historical_annual", 2023, noaa_client=client, gcs_client=gcs, db_gateway=db)
        r2 = run_ingest("historical_annual", 2023, noaa_client=client, gcs_client=gcs, db_gateway=db)

        # GCS should have exactly one object
        assert len(gcs._store) == 1


class TestSourcePriority:
    def test_realtime_does_not_overwrite_annual(self):
        # First ingest annual (priority 3)
        annual_row = _row(2023, 6, 1, 12, "1.5")
        annual_raw = _noaa_file(annual_row)
        gcs = FakeGCSClient()
        db = FakeDatabaseGateway()

        run_ingest("historical_annual", 2023, noaa_client=_mock_client(annual_raw),
                   gcs_client=gcs, db_gateway=db)

        # Then ingest realtime with DIFFERENT value for same timestamp
        realtime_row = _row(2023, 6, 1, 12, "9.9")   # different wave height
        realtime_raw = _noaa_file(realtime_row)

        result = run_ingest("realtime", 2023, noaa_client=_mock_client(realtime_raw),
                            gcs_client=gcs, db_gateway=db)

        # Silver should still have the annual value
        obs = db.get_observations(
            "51201",
            datetime(2023, 6, 1, 0, 0, tzinfo=timezone.utc),
            datetime(2023, 6, 2, 0, 0, tzinfo=timezone.utc),
        )
        assert len(obs) == 1
        # realtime was refused — annual value preserved (not 9.9)
        assert abs((obs[0].wave_height_m or 0) - 1.5) < 0.01

    def test_annual_upgrades_realtime(self):
        # First realtime
        rt_raw = _noaa_file(_row(2023, 6, 1, 12, "1.0"))
        gcs = FakeGCSClient()
        db = FakeDatabaseGateway()
        run_ingest("realtime", 2023, noaa_client=_mock_client(rt_raw), gcs_client=gcs, db_gateway=db)

        # Then annual with correction
        annual_raw = _noaa_file(_row(2023, 6, 1, 12, "2.5"))
        result = run_ingest("historical_annual", 2023, noaa_client=_mock_client(annual_raw),
                            gcs_client=gcs, db_gateway=db)
        assert result.silver_revised == 1

        obs = db.get_observations("51201",
            datetime(2023, 6, 1, 0, 0, tzinfo=timezone.utc),
            datetime(2023, 6, 2, 0, 0, tzinfo=timezone.utc))
        assert abs((obs[0].wave_height_m or 0) - 2.5) < 0.01


class TestSourceUnavailable:
    def test_404_no_fabricated_data(self):
        gcs = FakeGCSClient()
        db = FakeDatabaseGateway()
        result = run_ingest("historical_annual", 2005, noaa_client=_unavailable_client(),
                            gcs_client=gcs, db_gateway=db)
        assert not result.success
        assert result.silver_inserted == 0
        assert len(gcs._store) == 0

    def test_404_logs_error_not_raises(self):
        # Should NOT raise — returns a failed IngestResult
        gcs = FakeGCSClient()
        db = FakeDatabaseGateway()
        result = run_ingest("historical_annual", 2005, noaa_client=_unavailable_client(),
                            gcs_client=gcs, db_gateway=db)
        assert result.error is not None


class TestMMSentinelHandling:
    def test_all_mm_row_produces_null_wave_height(self):
        mm_row = b"2023 06 01 12 00   MM   MM  MM  MM  MM  MM  MM   MM   MM   MM   MM  MM   MM   MM\n"
        raw = _noaa_file(mm_row)
        gcs = FakeGCSClient()
        db = FakeDatabaseGateway()
        result = run_ingest("historical_annual", 2023, noaa_client=_mock_client(raw),
                            gcs_client=gcs, db_gateway=db)
        assert result.success
        obs = db.get_observations(
            "51201",
            datetime(2023, 6, 1, 0, 0, tzinfo=timezone.utc),
            datetime(2023, 6, 2, 0, 0, tzinfo=timezone.utc),
        )
        assert len(obs) == 1
        assert obs[0].wave_height_m is None


class TestPartialParseErrors:
    def test_partial_errors_do_not_block_good_rows(self):
        good_row = _row(2023, 6, 1, 12, "1.5")
        bad_row = b"2023 06 02 00 00  180\n"  # Too short
        raw = _noaa_file(good_row, bad_row)
        gcs = FakeGCSClient()
        db = FakeDatabaseGateway()
        result = run_ingest("historical_annual", 2023, noaa_client=_mock_client(raw),
                            gcs_client=gcs, db_gateway=db)
        assert result.success
        assert result.silver_inserted == 1
        assert result.parse_errors == 1
