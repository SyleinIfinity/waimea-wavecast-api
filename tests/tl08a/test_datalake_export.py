"""Failure/retry contracts for deterministic cross-storage exports."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from packages.gcs.client import GCSObjectInfo
from packages.db.fake import FakeDatabaseGateway
from packages.gcs.fake import FakeGCSClient
from services.ingestion.app.services.datalake_export import export_month, verify_month
from services.ingestion.app.services.ingest_pipeline import run_ingest
from services.ingestion.scripts.historical_backfill import _source, _unchanged
from services.ingestion.scripts.discover_history import discover
from services.preprocessing.app.jobs.gold import rebuild_gold


class Gateway:
    def __init__(self):
        self.rows = [{"raw_id": 1, "file_id": 4, "observed_at": "2004-01-01T00:26:00Z",
                      "raw_line": "2004 01 01 00 26", "source_url": "https://www.ndbc.noaa.gov/",
                      "source_sha256": "a" * 64}]
        self.pending = {}
        self.active = {}
        self.fail_complete = False
        self.mutate_after_prepare = False

    def _request(self, method, path, *, params=None, body=None):
        if path.endswith("/bronze/raw"):
            return {"data": list(self.rows), "next_after": None}
        if path.endswith("/exports/prepare"):
            key = (body["layer"], body["partition_key"], body["content_sha256"])
            self.pending.setdefault(key, {"export_id": len(self.pending) + 1, "state": "PENDING_EXPORT", **body})
            if self.mutate_after_prepare:
                self.rows[0]["raw_line"] = "corrected"
            return self.pending[key]
        if path.endswith("/exports/complete"):
            if self.fail_complete:
                raise RuntimeError("manifest commit unavailable")
            self.active[body["export_id"]] = body["gcs_generation"]
            row = next(item for item in self.pending.values() if item["export_id"] == body["export_id"])
            row.update(state="VERIFIED", gcs_generation=body["gcs_generation"])
            return {"state": "VERIFIED"}
        if path.endswith("/exports/fail"):
            row = next(item for item in self.pending.values() if item["export_id"] == body["export_id"])
            row["state"] = "RETRY_SCHEDULED"
            return {"state": "RETRY_SCHEDULED"}
        if path.endswith("/exports/active"):
            for row in self.pending.values():
                if row["export_id"] in self.active and row["layer"] == params["layer"] and row["partition_key"] == params["partition_key"]:
                    return row
            return {"found": False}
        raise AssertionError(path)


class GCS:
    def __init__(self):
        self.objects = {}
        self.upload_count = 0
        self.fail_upload = False

    def upload_if_absent(self, bucket, object_path, data, content_type, metadata):
        if self.fail_upload:
            raise RuntimeError("GCS unavailable")
        if object_path not in self.objects:
            self.objects[object_path] = data
            self.upload_count += 1
        else:
            assert self.objects[object_path] == data
        return GCSObjectInfo(bucket, object_path, f"gs://{bucket}/{object_path}", 99,
                             metadata["content_sha256"], len(data))

    def download(self, bucket, object_path, generation):
        assert generation == 99
        return self.objects[object_path]


def test_export_roundtrip_and_repeat_reuses_object():
    db, gcs = Gateway(), GCS()
    first = export_month(db, gcs, bucket="waimea-noaa", layer="bronze", year=2004, month=1)
    second = export_month(db, gcs, bucket="waimea-noaa", layer="bronze", year=2004, month=1)
    assert first == second
    assert len(db.pending) == 1
    assert gcs.upload_count == 1
    assert first["verified"] and first["rows"] == 1


def test_database_committed_gcs_failed_then_retry():
    db, gcs = Gateway(), GCS()
    gcs.fail_upload = True
    with pytest.raises(RuntimeError, match="GCS unavailable"):
        export_month(db, gcs, bucket="waimea-noaa", layer="bronze", year=2004, month=1)
    assert len(db.pending) == 1 and not db.active
    assert next(iter(db.pending.values()))["state"] == "RETRY_SCHEDULED"
    gcs.fail_upload = False
    assert export_month(db, gcs, bucket="waimea-noaa", layer="bronze", year=2004, month=1)["verified"]


def test_gcs_uploaded_manifest_failed_then_retry():
    db, gcs = Gateway(), GCS()
    db.fail_complete = True
    with pytest.raises(RuntimeError, match="manifest"):
        export_month(db, gcs, bucket="waimea-noaa", layer="bronze", year=2004, month=1)
    assert gcs.upload_count == 1 and not db.active
    db.fail_complete = False
    export_month(db, gcs, bucket="waimea-noaa", layer="bronze", year=2004, month=1)
    assert gcs.upload_count == 1 and len(db.active) == 1


def test_database_change_during_export_prevents_activation():
    db, gcs = Gateway(), GCS()
    db.mutate_after_prepare = True
    with pytest.raises(RuntimeError, match="PostgreSQL partition changed"):
        export_month(db, gcs, bucket="waimea-noaa", layer="bronze", year=2004, month=1)
    assert not db.active


def test_corrected_partition_creates_new_immutable_version():
    db, gcs = Gateway(), GCS()
    old = export_month(db, gcs, bucket="waimea-noaa", layer="bronze", year=2004, month=1)
    db.rows[0]["raw_line"] = "authentic revised NOAA line"
    new = export_month(db, gcs, bucket="waimea-noaa", layer="bronze", year=2004, month=1)
    assert old["sha256"] != new["sha256"]
    assert len(gcs.objects) == 2


def test_independent_reconciliation_detects_gcs_corruption():
    db, gcs = Gateway(), GCS()
    item = export_month(db, gcs, bucket="waimea-noaa", layer="bronze", year=2004, month=1)
    verified = verify_month(db, gcs, bucket="waimea-noaa", layer="bronze", year=2004, month=1)
    assert verified["row_count"] == item["rows"]
    object_path = item["gcs_uri"].removeprefix("gs://waimea-noaa/")
    gcs.objects[object_path] = b"tampered"
    with pytest.raises(RuntimeError, match="checksum mismatch"):
        verify_month(db, gcs, bucket="waimea-noaa", layer="bronze", year=2004, month=1)


def test_discovery_url_validation_and_reconciliation_window():
    annual = _source("51201h2004.txt.gz", "https://www.ndbc.noaa.gov/data/historical/stdmet/51201h2004.txt.gz")
    monthly = _source("51201a2026.txt.gz", "https://www.ndbc.noaa.gov/data/stdmet/Oct/51201a2026.txt.gz")
    assert annual["source_period"] == "2004"
    assert monthly["source_period"] == "2026-10"
    with pytest.raises(ValueError, match="URL mismatch"):
        _source("51201h2004.txt.gz", "https://example.invalid/source")
    now = datetime.now(timezone.utc).isoformat()
    job = {"status": "VERIFIED", "last_checked_at": now, "last_verified_at": now,
           "source_etag": "same", "source_sha256": "a" * 64}
    assert _unchanged(job, {}, force_check=False)
    job["last_checked_at"] = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
    assert _unchanged(job, {"source_etag": "same"}, force_check=False)
    assert _unchanged(job, {"not_modified": True}, force_check=False)
    assert not _unchanged(job, {"source_etag": "same"}, force_check=True)
    assert not _unchanged(job, {"source_etag": "changed"}, force_check=True)
    job.update(source_last_modified="Wed, 12 Nov 2026 00:00:00 GMT", source_content_length=100)
    assert not _unchanged(job, {"source_etag": "changed", "source_last_modified": job["source_last_modified"],
                                "source_content_length": 100}, force_check=False)
    assert not _unchanged(job, {"source_etag": "same", "source_last_modified": job["source_last_modified"],
                                "source_content_length": 101}, force_check=False)
    job["last_verified_at"] = (datetime.now(timezone.utc) - timedelta(days=31)).isoformat()
    assert not _unchanged(job, {"source_etag": "same"}, force_check=False)


def test_interrupted_raw_batch_resumes_from_committed_silver_checkpoint():
    import gzip

    class InterruptOnce(FakeDatabaseGateway):
        def __init__(self):
            super().__init__()
            self.raw_calls = 0

        def upsert_raw_batch(self, rows):
            self.raw_calls += 1
            if self.raw_calls == 2:
                raise RuntimeError("injected database interruption")
            return super().upsert_raw_batch(rows)

    header = b"#YYYY MM DD hh mm WVHT DPD APD MWD WTMP\n#yr mo dy hr mn m s s deg C\n"
    rows = [f"2004 09 {8+i//24:02d} {i%24:02d} 00 1.5 8 7 100 24\n".encode() for i in range(501)]
    payload = gzip.compress(header + b"".join(rows))
    db, gcs = InterruptOnce(), FakeGCSClient()
    checkpoints = []
    first = run_ingest("historical_annual", 2004, source_bytes=payload,
                       gcs_client=gcs, db_gateway=db, checkpoint=checkpoints.append)
    assert not first.success and first.silver_inserted == 500
    assert checkpoints[-1]["last_batch_offset"] == 500
    second = run_ingest("historical_annual", 2004, source_bytes=payload,
                        gcs_client=gcs, db_gateway=db, resume_offset=500,
                        checkpoint=checkpoints.append)
    assert second.success and second.silver_inserted == 1
    assert len(db.get_observations("51201", datetime(2004, 9, 1, tzinfo=timezone.utc),
                                   datetime(2004, 10, 1, tzinfo=timezone.utc))) == 501


def test_hourly_history_uses_hourly_completeness_denominator():
    class GoldGateway:
        def __init__(self):
            self.rows = [SimpleNamespace(observed_at=datetime(2005, 1, 1, 10, tzinfo=timezone.utc)
                                                     + timedelta(hours=i), wave_height_m=1.5,
                                         dominant_period_s=8.0, quality_status="valid")
                         for i in range(240)]
            self.saved = []

        def get_observations(self, station_id, start, end):
            return self.rows

        def save_resolution_metrics(self, rows):
            self.saved.extend(rows)
            return {"saved": len(rows)}

    gateway = GoldGateway()
    result = rebuild_gold(gateway, year=2005)
    assert result["cadence_minutes"] == 60
    daily = [row for row in gateway.saved if row["resolution"] == "daily"]
    assert len(daily) == 10
    assert all(row["expected_sample_count"] == 24 for row in daily)
    assert all(row["readiness"] == "GOLD_READY" for row in daily)
    finer = [row for row in gateway.saved if row["resolution"] == "30min"]
    assert finer and all(row["readiness"] == "INSUFFICIENT_DATA" for row in finer)


def test_revised_noaa_archive_preserves_old_raw_and_revises_canonical_silver():
    import gzip

    header = b"#YYYY MM DD hh mm WVHT DPD APD MWD WTMP\n#yr mo dy hr mn m s s deg C\n"
    def payload(height):
        return gzip.compress(header + f"2005 01 02 12 00 {height} 8 7 100 24\n".encode(), mtime=0)

    db, gcs = FakeDatabaseGateway(), FakeGCSClient()
    first = run_ingest("historical_annual", 2005, source_bytes=payload("1.5"),
                       gcs_client=gcs, db_gateway=db)
    corrected = run_ingest("historical_annual", 2005, source_bytes=payload("2.5"),
                           gcs_client=gcs, db_gateway=db)
    assert first.success and corrected.success and corrected.silver_revised == 1
    assert len(gcs._store) == 2
    rows = db.get_observations("51201", datetime(2005, 1, 2, tzinfo=timezone.utc),
                               datetime(2005, 1, 3, tzinfo=timezone.utc))
    assert len(rows) == 1 and rows[0].wave_height_m == 2.5


def test_new_archives_are_discovered_only_from_official_listing():
    class Response:
        text = ('<a href="/download_data.php?filename=51201h2025.txt.gz&dir=data/historical/stdmet/">annual</a>'
                '<a href="/download_data.php?filename=5120112026.txt.gz&dir=data/stdmet/Jan/">monthly</a>'
                '<a href="/download_data.php?filename=51201h2024.txt.gz&dir=evil/">invalid</a>')

        def raise_for_status(self):
            pass

    class Session:
        def get(self, url, timeout):
            return Response()

    found = discover(Session(), 100)
    assert len(found) == 2
    assert _source(*found[0])["source_period"] == "2025"
    assert _source(*found[1])["source_period"] == "2026-01"


def test_daily_reconciler_limits_full_sha_downloads(monkeypatch):
    from services.ingestion.scripts import historical_backfill as history

    class Session:
        headers = {}

        def close(self):
            pass

    class DB:
        def _request(self, method, path, *, params=None):
            return {"data": []}

        def close(self):
            pass

    downloaded = []
    monkeypatch.setattr(history, "discover", lambda session, limit: [
        (f"51201h{year}.txt.gz", f"https://www.ndbc.noaa.gov/data/historical/stdmet/51201h{year}.txt.gz")
        for year in (2004, 2005, 2006)])
    monkeypatch.setattr(history.requests, "Session", Session)
    monkeypatch.setattr(history, "HttpDatabaseGateway", DB)
    monkeypatch.setattr(history, "RealGCSClient", lambda: object())
    monkeypatch.setattr(history, "_safe_resources", lambda: None)
    monkeypatch.setattr(history.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(history, "_process", lambda source, *args, **kwargs: (
        downloaded.append(source["source_period"]) or
        {"period": source["source_period"], "status": "UNCHANGED_SHA256"}))
    monkeypatch.setattr("sys.argv", ["historical_backfill", "--max-files", "1"])
    history.main()
    assert downloaded == ["2006"]


def test_year_manifest_exports_all_silver_dependencies_before_gold(monkeypatch):
    from services.ingestion.scripts import publish_history_manifest as publisher

    populated = {("bronze", 2026, 1), ("silver", 2026, 1),
                 ("silver", 2026, 2), ("silver", 2027, 1),
                 ("gold", 2026, 1), ("gold", 2026, 2)}
    uploads = []
    monkeypatch.setattr(publisher, "_rows", lambda gateway, layer, year, month: (
        [{"row": 1}] if (layer, year, month) in populated else []))
    monkeypatch.setattr(publisher, "export_month", lambda gateway, gcs, *, bucket, layer, year, month,
                        processing_run_id: uploads.append((layer, year, month)))
    monkeypatch.setattr(publisher, "verify_month", lambda gateway, gcs, *, bucket, layer, year, month:
                        {"layer": layer, "partition_key": f"{year}-{month:02d}"})
    artifacts = publisher._publish_monthly_artifacts(object(), object(), 2026, "waimea-noaa", True)
    assert len(artifacts) == 5
    assert uploads.index(("silver", 2026, 2)) < uploads.index(("gold", 2026, 1))
    assert uploads.index(("silver", 2027, 1)) < uploads.index(("gold", 2026, 1))
