"""Explicit offline training command; no model fit occurs in API requests."""
from __future__ import annotations

import hashlib
import io
import json
import sys
from datetime import datetime, timezone
from typing import Any

import pandas as pd
import numpy as np
import sklearn

from packages.ml.artifacts import ModelStore
from packages.gcs.paths import TRAINING_DATASET_PREFIX
from packages.ml.features import FEATURE_NAMES, build_samples, chronological_split
from training import train_classification, train_clustering, train_regression


def train_suite(daily_rows: list[Any], store: ModelStore, gateway: Any, *, version: str,
                synthetic_fixture: bool = False) -> dict:
    if not version or "/" in version or ".." in version:
        raise ValueError("invalid dataset/model version")
    samples = build_samples(daily_rows)
    train, validation, test = chronological_split(samples)
    snapshot = pd.DataFrame([{"anchor_date_hst": s.anchor_date_hst.isoformat(),
                              "target_date_hst": s.target_date_hst.isoformat(),
                              **dict(zip(FEATURE_NAMES, s.features)), "actual_next_day_median_m": s.target} for s in samples])
    buffer = io.BytesIO()
    snapshot.to_parquet(buffer, index=False, engine="pyarrow")
    dataset_bytes = buffer.getvalue()
    dataset_hash = hashlib.sha256(dataset_bytes).hexdigest()
    dataset_path = f"{TRAINING_DATASET_PREFIX}/wave-height/{version}/dataset.parquet"
    dataset_info = store.client.upload_if_absent(store.bucket, dataset_path,
                                                 dataset_bytes, "application/vnd.apache.parquet")
    if dataset_info.content_sha256 != dataset_hash:
        raise ValueError("dataset snapshot hash mismatch")
    if hashlib.sha256(store.client.download(store.bucket, dataset_path, dataset_info.generation)).hexdigest() != dataset_hash:
        raise ValueError("stored dataset snapshot hash mismatch")
    split_body = {"dataset_name": "wave-height-next-day", "dataset_version": version,
        "station_id": "51201", "task_type": "regression", "target_metric": "WVHT", "target_horizon_days": 1,
        "observation_granularity": "day_hst", "feature_window_days": 7, "feature_columns": list(FEATURE_NAMES),
        "label_definition": {"target": "next HST day median WVHT", "unit": "m", "synthetic_fixture": synthetic_fixture},
        "preprocessing_parameters": {"missing": "train-only median imputation",
                                     "as_of_policy": "reject source_data_max_updated_at after anchor T+1 02:00 HST",
                                     "snapshot_created_at": datetime.now(timezone.utc).isoformat()},
        "split_method": "chronological",
        "train_start_hst": train[0].anchor_date_hst.isoformat(), "train_end_hst": train[-1].anchor_date_hst.isoformat(),
        "validation_start_hst": validation[0].anchor_date_hst.isoformat(), "validation_end_hst": validation[-1].anchor_date_hst.isoformat(),
        "test_start_hst": test[0].anchor_date_hst.isoformat(), "test_end_hst": test[-1].anchor_date_hst.isoformat(),
        "train_sample_count": len(train), "validation_sample_count": len(validation), "test_sample_count": len(test),
        "dataset_gcs_uri": dataset_info.gcs_uri, "dataset_gcs_generation": str(dataset_info.generation),
        "dataset_sha256": dataset_hash}
    outcomes = {
        "regression": train_regression.train(train, validation, test),
        "classification": train_classification.train(train, validation, test),
        "clustering": train_clustering.train(train),
    }
    result = {"dataset_sha256": dataset_hash, "synthetic_fixture": synthetic_fixture,
              "models": {}}
    for task, outcome in outcomes.items():
        task_split = {**split_body, "dataset_name": f"wave-height-{task}", "task_type": task,
                      "target_metric": "WVHT" if task != "clustering" else None,
                      "target_horizon_days": 1 if task != "clustering" else None,
                      "label_definition": {"target": "daily HST cluster", "synthetic_fixture": synthetic_fixture}
                      if task == "clustering" else split_body["label_definition"]}
        split_id = gateway.save_dataset_split(task_split, f"dataset:{task}:{version}")["dataset_split_id"]
        result.setdefault("dataset_split_ids", {})[task] = split_id
        scheme_id = None
        if task == "classification":
            outcome["scheme"]["scheme_version"] = version
            scheme_id = gateway.save_scheme(outcome["scheme"], f"scheme:{version}")["scheme_id"]
        feature_schema_sha = hashlib.sha256(json.dumps(list(FEATURE_NAMES), separators=(",", ":")).encode()).hexdigest()
        manifest = {"task_type": task, "feature_names": list(FEATURE_NAMES), "feature_window_days": 7,
                    "target": "median WVHT HST T+1" if task != "clustering" else "daily HST cluster",
                    "target_unit": "m" if task != "clustering" else None,
                    "dataset_sha256": dataset_hash, "synthetic_fixture": synthetic_fixture, "scheme_id": scheme_id,
                    "feature_schema_sha256": feature_schema_sha, "python_version": f"{sys.version_info.major}.{sys.version_info.minor}",
                    "sklearn_version": sklearn.__version__, "numpy_version": np.__version__,
                    "training_code_version": "tl05-v1"}
        artifact = store.save(outcome["model"], task=task, version=version, manifest=manifest)
        model_result = gateway.save_model({"model_name": f"waimea-{task}", "model_version": version,
            "task_type": task, "algorithm": "RandomForest" if task != "clustering" else "KMeans",
            "artifact_gcs_uri": artifact["artifact_gcs_uri"], "artifact_generation": artifact["artifact_generation"],
            "artifact_sha256": artifact["artifact_sha256"], "artifact_size_bytes": artifact["artifact_size_bytes"],
            "feature_names": list(FEATURE_NAMES), "status": "draft",
            "feature_schema_sha256": feature_schema_sha, "python_version": manifest["python_version"],
            "hyperparameters": {"manifest_sha256": artifact["manifest_sha256"],
                                 "manifest_generation": artifact["manifest_generation"],
                                 "training_code_version": "tl05-v1"}}, f"model:{task}:{version}")
        model_id = model_result["model_id"]
        run = gateway.save_training_run({"dataset_split_id": split_id, "model_id": model_id, "task_type": task,
            "algorithm": "RandomForest" if task != "clustering" else "KMeans", "status": "success",
            "run_name": f"{task}:{version}", "random_seed": 42}, f"train:{task}:{version}")
        if task == "clustering":
            metrics = {"sample_count": outcome["sample_count"], "silhouette_score": outcome["silhouette_score"],
                       "davies_bouldin_score": outcome["davies_bouldin_score"], "additional_metrics": {"k": outcome["k"]}}
            for cluster_label, center in enumerate(outcome["model"][-1].cluster_centers_):
                original_center = outcome["model"].named_steps["scaler"].inverse_transform([center])[0]
                gateway.save_centroid({"model_id": model_id, "cluster_label": cluster_label,
                                       "centroid_features": dict(zip(FEATURE_NAMES, map(float, original_center))),
                                       "sample_count": outcome["labels"].count(cluster_label)},
                                      f"centroid:{task}:{version}:{cluster_label}")
            metrics["calinski_harabasz_score"] = outcome["calinski_harabasz_score"]
        elif task == "regression":
            metrics = {**outcome["test"]["model"], "additional_metrics": {"baseline": outcome["test"]["persistence"],
                        "unit": "m", "synthetic_fixture": synthetic_fixture}}
        else:
            metrics = {"sample_count": outcome["test"]["sample_count"], "accuracy": outcome["test"]["accuracy"],
                       "f1_macro": outcome["test"]["f1_macro"],
                       "additional_metrics": {"confusion_matrix": outcome["test"]["confusion_matrix"],
                                              "labels": outcome["test"]["labels"], "synthetic_fixture": synthetic_fixture}}
        evaluation = gateway.save_evaluation({"training_run_id": run["training_run_id"], "model_id": model_id,
            "dataset_split_id": split_id, "evaluation_stage": "train" if task == "clustering" else "test", "task_type": task,
            "evaluation_name": "held-out", **metrics}, f"evaluation:{task}:{version}")
        if task in ("regression", "classification"):
            for sample, actual, predicted in zip(test, outcome["test"]["actual"], outcome["test"]["predicted"]):
                row = {"evaluation_id": evaluation["evaluation_id"], "station_id": "51201",
                       "target_date_hst": sample.target_date_hst.isoformat(), "target_name": "next_day_median_wvht",
                       "horizon_days": 1, "feature_cutoff_date_hst": sample.anchor_date_hst.isoformat()}
                if task == "regression":
                    row.update(actual_value=float(actual), predicted_value=float(predicted))
                else:
                    row.update(actual_label=str(actual), predicted_label=str(predicted))
                gateway.save_evaluation_sample(row, f"evaluation-sample:{task}:{version}:{sample.target_date_hst}")
        result["models"][task] = {"model_id": model_id, "artifact": artifact, "metrics": metrics}
    return result
