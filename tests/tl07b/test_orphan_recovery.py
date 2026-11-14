from datetime import datetime, timezone
from types import SimpleNamespace

from services.ingestion.app.jobs.orphans import reconcile_orphans


def test_bounded_orphan_scan_replays_only_unregistered_pinned_generation(monkeypatch):
    objects = [SimpleNamespace(object_path=f"noaa/raw/realtime/2026/11/12/{i}-snapshot.txt",
                               gcs_uri=f"gs://waimea-noaa/noaa/raw/realtime/2026/11/12/{i}-snapshot.txt",
                               generation=i) for i in (1, 2, 3)]

    class GCS:
        def list_raw_objects(self, bucket, prefix, *, max_results):
            return objects if prefix.endswith("2026/11/12/") else []

    class DB:
        def source_file_by_gcs(self, uri, generation):
            return {"found": generation != 3, "source_row_count": 10, "raw_count": 10}

    replays = []

    def replay(feed, year, *, object_path, generation, **kwargs):
        replays.append((feed, year, object_path, generation))
        return SimpleNamespace(success=True, silver_inserted=0, silver_revised=0,
                               changed_from=None, changed_to=None)

    monkeypatch.setattr("services.ingestion.app.jobs.orphans.replay_stored_object", replay)
    result = reconcile_orphans(gcs_client=GCS(), db_gateway=DB(), bucket="waimea-noaa",
                               now=datetime(2026, 11, 12, 12, tzinfo=timezone.utc),
                               max_objects=3, max_replays=1)
    assert len(result["repaired"]) == 1
    assert replays == [("realtime", 2026, objects[-1].object_path, 3)]
    assert result["truncated"] is True


def test_complete_snapshot_with_reused_raw_is_not_replayed(monkeypatch):
    item = SimpleNamespace(object_path="noaa/raw/realtime/2026/11/12/snapshot.txt",
                           gcs_uri="gs://waimea-noaa/noaa/raw/realtime/2026/11/12/snapshot.txt",
                           generation=1)

    class GCS:
        def list_raw_objects(self, bucket, prefix, *, max_results):
            return [item] if prefix.endswith("2026/11/12/") else []

    class DB:
        def source_file_by_gcs(self, uri, generation):
            return {"found": True, "source_row_count": 2100, "raw_count": 0,
                    "ingest_complete": True}

    def unexpected(*args, **kwargs):
        raise AssertionError("complete snapshot must not replay")

    monkeypatch.setattr("services.ingestion.app.jobs.orphans.replay_stored_object", unexpected)
    result = reconcile_orphans(gcs_client=GCS(), db_gateway=DB(), bucket="waimea-noaa",
                               now=datetime(2026, 11, 12, 12, tzinfo=timezone.utc))
    assert result["repaired"] == []
