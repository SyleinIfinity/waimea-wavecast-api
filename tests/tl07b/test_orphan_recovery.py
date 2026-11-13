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
