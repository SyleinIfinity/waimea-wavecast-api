"""Durable scheduled publication contracts; production uses RealGCSClient."""
import hashlib
import json

import pytest

from packages.gcs.client import GCSObjectInfo
from services.ingestion.scripts import scheduled_history_reconcile as task


class Gateway:
    def __init__(self):
        self.pending = [{"year": 2026, "updated_at": "2026-11-13T00:00:00Z"}]
        self.completed = []

    def _request(self, method, path, *, params=None, body=None):
        if path.endswith("/publications/pending"):
            return {"data": self.pending[:2]}
        if path.endswith("/complete"):
            self.completed.append(body)
            return {"state": "VERIFIED"}
        raise AssertionError(path)


class GCS:
    def __init__(self):
        self.objects = {}
        self.fail = False

    def upload_if_absent(self, bucket, path, payload, *, content_type, metadata):
        if self.fail:
            raise RuntimeError("GCS unavailable")
        self.objects.setdefault(path, payload)
        return GCSObjectInfo(bucket, path, f"gs://{bucket}/{path}", 17,
                             hashlib.sha256(payload).hexdigest(), len(payload))

    def download(self, bucket, path, generation):
        assert generation == 17
        return self.objects[path]


def test_pending_publication_requires_pinned_coverage_before_completion(monkeypatch):
    gateway, gcs = Gateway(), GCS()
    manifest = {"manifest_sha256": "a" * 64,
                "gcs_uri": "gs://waimea-noaa/manifests/station=51201/historical/year=2026/" + "a" * 64 + ".json",
                "generation": 9}
    monkeypatch.setattr(task, "publish_year", lambda *args, **kwargs: manifest)
    monkeypatch.setattr(task, "build_report", lambda *args: {"schema_version": "tl08a-coverage-v1", "monthly": []})
    gcs.fail = True
    with pytest.raises(RuntimeError, match="GCS unavailable"):
        task.publish_pending(gateway, gcs)
    assert gateway.completed == []
    gcs.fail = False
    result = task.publish_pending(gateway, gcs)
    assert result[0]["status"] == "PUBLISHED"
    assert len(gateway.completed) == 1
    assert gateway.completed[0]["pending_updated_at"] == gateway.pending[0]["updated_at"]
    assert json.loads(next(iter(gcs.objects.values())))["schema_version"] == "tl08a-coverage-v1"


def test_no_pending_publication_does_not_scan_or_upload(monkeypatch):
    gateway, gcs = Gateway(), GCS()
    gateway.pending = []
    monkeypatch.setattr(task, "build_report", lambda *args: pytest.fail("unnecessary coverage scan"))
    assert task.publish_pending(gateway, gcs) == []
    assert gcs.objects == {}
