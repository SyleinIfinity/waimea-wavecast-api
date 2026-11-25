from datetime import date
from types import SimpleNamespace
from unittest.mock import Mock

from training import tl08b_cluster_assignments as clustering


def test_reset_republishes_assignments_even_if_processing_run_survived(monkeypatch):
    anchor = date(2026, 11, 19)
    monkeypatch.setattr(clustering, "build_samples", lambda rows, require_target: [
        SimpleNamespace(anchor_date_hst=anchor, features=(1.0,)),
    ])

    gateway = Mock()
    gateway.get_daily_features.return_value = []
    gateway._request.side_effect = [
        {"task_type": "clustering", "status": "draft", "model_version": "test", "artifact_sha256": "test"},
        {"data": []},
    ]
    gateway.create_job.return_value = {"duplicate": True, "status": "success", "processing_run_id": 9}
    gateway.save_gold_results.return_value = {"saved": 1}
    store = Mock()
    model = Mock()
    model.predict.return_value = [0]
    pipeline = Mock()
    pipeline.transform.return_value = [[1.0]]
    model.__getitem__ = Mock(side_effect=[pipeline, SimpleNamespace(cluster_centers_=[[1.0]])])
    store.load.return_value = model, {"feature_names": list(clustering.FEATURE_NAMES), "synthetic_fixture": False}

    def upload(bucket, path, payload, content_type):
        import hashlib

        store.client.download.return_value = payload
        return SimpleNamespace(content_sha256=hashlib.sha256(payload).hexdigest(), generation=1,
                               gcs_uri=f"gs://test/{path}")

    store.client.upload_if_absent.side_effect = upload
    result = clustering.rebuild_assignments(gateway, store, model_id=10, start_hst=anchor, end_hst=anchor)

    assert result["status"] == "rebuilt"
    assert result["saved"] == 1
    table, rows, _ = gateway.save_gold_results.call_args.args
    assert table == "cluster_assignments"
    assert rows == [{"date_hst": "2026-11-19", "cluster_label": 0, "distance_to_centroid": 0.0,
                     "model_id": 10, "station_id": "51201", "processing_run_id": 9}]
