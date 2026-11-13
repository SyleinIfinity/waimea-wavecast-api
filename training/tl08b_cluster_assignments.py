"""Publish reproducible retrospective assignments for a pinned draft K-means model."""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import date
from math import sqrt

from packages.gcs.client import RealGCSClient
from packages.ml.artifacts import ModelStore
from packages.ml.features import FEATURE_NAMES, build_samples, matrix
from packages.ml.gateway import HttpMLGateway
from packages.ml.worker import WorkerSettings


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--model-id", type=int, required=True)
    parser.add_argument("--start-hst", type=date.fromisoformat, required=True)
    parser.add_argument("--end-hst", type=date.fromisoformat, required=True)
    args = parser.parse_args()
    settings = WorkerSettings()
    gateway = HttpMLGateway(settings.database_api_url, settings.internal_api_token)
    metadata = gateway._request("GET", f"/internal/v1/models/{args.model_id}")
    if metadata["task_type"] != "clustering" or metadata["status"] != "draft":
        raise ValueError("expected pinned draft clustering model")
    store = ModelStore(RealGCSClient(), settings.gcs_models_bucket)
    model, manifest = store.load(metadata)
    if manifest["feature_names"] != list(FEATURE_NAMES):
        raise ValueError("feature schema mismatch")
    rows = gateway.get_daily_features(args.start_hst, args.end_hst)
    clean = [row for row in rows if (row.get("valid_wave_count") or 0) > 0
             and (row.get("coverage_ratio") or 0) >= .9
             and not any(row.get(k) for k in ("suspect_count", "rejected_count", "unverified_count", "imputed_count"))]
    samples = build_samples(clean, require_target=False)
    labels = model.predict(matrix(samples))
    transformed = model[:-1].transform(matrix(samples))
    centers = model[-1].cluster_centers_
    snapshot = [{"date_hst": str(sample.anchor_date_hst), "cluster_label": int(label),
                 "distance_to_centroid": float(sqrt(sum((float(a)-float(b)) ** 2
                     for a, b in zip(features, centers[int(label)]))))}
                for sample, label, features in zip(samples, labels, transformed)]
    payload = (json.dumps(snapshot, separators=(",", ":")) + "\n").encode()
    sha = hashlib.sha256(payload).hexdigest()
    object_path = f"training/clusters/model={args.model_id}/assignments-{sha}.json"
    info = store.client.upload_if_absent(store.bucket, object_path, payload, "application/json")
    if info.content_sha256 != sha or store.client.download(store.bucket, object_path, info.generation) != payload:
        raise RuntimeError("cluster assignment GCS round-trip failed")
    key = f"tl08b:cluster-assignments:{args.model_id}:{sha}"
    job = gateway.create_job({"job_type": "clustering", "input_period_start": args.start_hst.isoformat(),
                              "input_period_end": args.end_hst.isoformat(), "algorithm_version": metadata["model_version"],
                              "parameters": {"model_id": args.model_id, "artifact_sha256": metadata["artifact_sha256"],
                                             "gcs_uri": info.gcs_uri, "gcs_generation": str(info.generation),
                                             "sha256": sha}}, key)
    job_id = job["processing_run_id"]
    if not (job.get("duplicate") and job.get("status") == "success"):
        gold = [{**item, "model_id": args.model_id, "station_id": "51201", "processing_run_id": job_id}
                for item in snapshot]
        try:
            saved = gateway.save_gold_results("cluster_assignments", gold, key)
            gateway.update_job(job_id, "success", saved["saved"])
        except Exception as exc:
            gateway.update_job(job_id, "failed", error=type(exc).__name__)
            raise
    print(json.dumps({"model_id": args.model_id, "assignment_count": len(snapshot),
                      "gcs_uri": info.gcs_uri, "gcs_generation": str(info.generation),
                      "sha256": sha, "processing_run_id": job_id}))


if __name__ == "__main__":
    main()
