"""Publish reproducible retrospective assignments for a pinned K-means model."""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import date, timedelta
from math import sqrt

from packages.gcs.client import RealGCSClient
from packages.ml.artifacts import ModelStore
from packages.ml.features import FEATURE_NAMES, build_samples, matrix
from packages.ml.gateway import HttpMLGateway


def rebuild_assignments(gateway: HttpMLGateway, store: ModelStore, *, model_id: int,
                        start_hst: date, end_hst: date) -> dict:
    rows = gateway.get_daily_features(start_hst - timedelta(days=6), end_hst)
    clean = [row for row in rows if (row.get("valid_wave_count") or 0) > 0
             and (row.get("coverage_ratio") or 0) >= .9
             and not any(row.get(k) for k in ("suspect_count", "rejected_count", "unverified_count", "imputed_count"))]
    samples = [sample for sample in build_samples(clean, require_target=False)
               if sample.anchor_date_hst >= start_hst]
    if not samples:
        return {"model_id": model_id, "assignment_count": 0, "saved": 0,
                "status": "waiting_for_valid_daily_features"}

    metadata = gateway._request("GET", f"/internal/v1/models/{model_id}")
    if metadata["task_type"] != "clustering" or metadata["status"] not in ("draft", "ready"):
        raise ValueError("expected pinned draft or ready clustering model")
    model, manifest = store.load(metadata)
    if manifest["feature_names"] != list(FEATURE_NAMES) or manifest.get("synthetic_fixture") is not False:
        raise ValueError("expected matching model trained on real data")
    labels = model.predict(matrix(samples))
    transformed = model[:-1].transform(matrix(samples))
    centers = model[-1].cluster_centers_
    snapshot = [{"date_hst": str(sample.anchor_date_hst), "cluster_label": int(label),
                 "distance_to_centroid": float(sqrt(sum((float(a)-float(b)) ** 2
                     for a, b in zip(features, centers[int(label)]))))}
                for sample, label, features in zip(samples, labels, transformed)]
    # A completed job survives reset, but the assignment rows do not. Compare
    # the stored assignments themselves before deciding to skip publication.
    if (end_hst - start_hst).days < 100:
        existing = gateway._request("GET", "/api/v1/clusters", params={
            "start_hst": start_hst.isoformat(), "end_hst": end_hst.isoformat(), "limit": 1000,
        })["data"]
        by_date = {row["date_hst"]: row for row in existing if row["model_id"] == model_id}
        snapshot = [item for item in snapshot if any(
            by_date.get(item["date_hst"], {}).get(key) != value for key, value in item.items()
        )]
        if not snapshot:
            return {"model_id": model_id, "assignment_count": len(samples), "saved": 0, "status": "up_to_date"}
    payload = (json.dumps(snapshot, separators=(",", ":")) + "\n").encode()
    sha = hashlib.sha256(payload).hexdigest()
    object_path = f"training/clusters/model={model_id}/assignments-{sha}.json"
    info = store.client.upload_if_absent(store.bucket, object_path, payload, "application/json")
    if info.content_sha256 != sha or store.client.download(store.bucket, object_path, info.generation) != payload:
        raise RuntimeError("cluster assignment GCS round-trip failed")
    key = f"tl08b:cluster-assignments:{model_id}:{sha}"
    job = gateway.create_job({"job_type": "clustering", "input_period_start": snapshot[0]["date_hst"],
                              "input_period_end": snapshot[-1]["date_hst"], "algorithm_version": metadata["model_version"],
                              "parameters": {"model_id": model_id, "artifact_sha256": metadata["artifact_sha256"],
                                             "gcs_uri": info.gcs_uri, "gcs_generation": str(info.generation),
                                             "sha256": sha}}, key)
    job_id = job["processing_run_id"]
    gold = [{**item, "model_id": model_id, "station_id": "51201", "processing_run_id": job_id}
            for item in snapshot]
    try:
        saved = gateway.save_gold_results("cluster_assignments", gold, key)["saved"]
        gateway.update_job(job_id, "success", saved)
    except Exception as exc:
        if job.get("status") != "success":
            gateway.update_job(job_id, "failed", error=type(exc).__name__)
        raise
    return {"model_id": model_id, "assignment_count": len(samples), "saved": saved, "status": "rebuilt",
            "gcs_uri": info.gcs_uri, "gcs_generation": str(info.generation),
            "sha256": sha, "processing_run_id": job_id}


def main():
    from packages.ml.worker import WorkerSettings

    parser = argparse.ArgumentParser()
    parser.add_argument("--model-id", type=int, required=True)
    parser.add_argument("--start-hst", type=date.fromisoformat, required=True)
    parser.add_argument("--end-hst", type=date.fromisoformat, required=True)
    args = parser.parse_args()
    settings = WorkerSettings()
    gateway = HttpMLGateway(settings.database_api_url, settings.internal_api_token)
    store = ModelStore(RealGCSClient(), settings.gcs_models_bucket)
    print(json.dumps(rebuild_assignments(gateway, store, model_id=args.model_id,
                                         start_hst=args.start_hst, end_hst=args.end_hst)))


if __name__ == "__main__":
    main()
