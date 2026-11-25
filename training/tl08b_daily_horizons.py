"""Train audited 7/30-day retrospective NOAA models; never auto-promote."""
from __future__ import annotations

import hashlib
import json
from datetime import date, timedelta

from packages.ml.features import FEATURE_NAMES, Sample, build_samples, chronological_split
from packages.ml.metrics import regression
from packages.ml.artifacts import ModelStore
from training.train_regression import train as train_regressor


def samples_for_horizon(rows: list[dict], days: int) -> list[Sample]:
    if days not in (7, 30):
        raise ValueError("unsupported daily horizon")
    clean = [row for row in rows if (row.get("valid_wave_count") or 0) > 0
             and (row.get("coverage_ratio") or 0) >= .9
             and not any(row.get(key) for key in ("suspect_count", "rejected_count", "unverified_count", "imputed_count"))]
    by_day = {date.fromisoformat(str(row["date_hst"])): row for row in clean}
    base = build_samples(clean, require_target=False)
    result = []
    for item in base:
        if days == 7:
            target_day = item.anchor_date_hst + timedelta(days=7)
            target_row = by_day.get(target_day)
            if target_row is None or target_row.get("median_wave_height_m") is None:
                continue
            target = float(target_row["median_wave_height_m"])
        else:
            future = [by_day.get(item.anchor_date_hst + timedelta(days=offset)) for offset in range(1, 31)]
            if sum(row is not None and row.get("median_wave_height_m") is not None for row in future) < 27:
                continue
            values = [float(row["median_wave_height_m"]) for row in future
                      if row is not None and row.get("median_wave_height_m") is not None]
            target = sum(values) / len(values)
            target_day = item.anchor_date_hst + timedelta(days=30)
        result.append(Sample(item.anchor_date_hst, target_day, item.features, target))
    return result


def train_horizon(rows: list[dict], store: ModelStore, gateway, *, days: int, version: str,
                  source_versions: list[dict]) -> dict:
    samples = samples_for_horizon(rows, days)
    train, validation, test = chronological_split(samples, gap=days)
    if train[-1].target_date_hst >= validation[0].anchor_date_hst or validation[-1].target_date_hst >= test[0].anchor_date_hst:
        raise RuntimeError("horizon embargo failed")
    snapshot = [{"anchor": str(item.anchor_date_hst), "target_date": str(item.target_date_hst),
                 "features": item.features, "target": item.target} for item in samples]
    payload = (json.dumps(snapshot, separators=(",", ":")) + "\n").encode()
    sha = hashlib.sha256(payload).hexdigest()
    path = f"datasets/training/wave-height/{version}/{days}d/{sha}.json"
    info = store.client.upload_if_absent(store.bucket, path, payload, "application/json")
    if info.content_sha256 != sha or store.client.download(store.bucket, path, info.generation) != payload:
        raise RuntimeError("training dataset GCS round-trip failed")
    source_payload = (json.dumps(source_versions, sort_keys=True, separators=(",", ":")) + "\n").encode()
    source_sha = hashlib.sha256(source_payload).hexdigest()
    source_path = f"datasets/training/wave-height/{version}/{days}d/source-{source_sha}.json"
    source_info = store.client.upload_if_absent(store.bucket, source_path, source_payload, "application/json")
    if source_info.content_sha256 != source_sha or store.client.download(store.bucket, source_path, source_info.generation) != source_payload:
        raise RuntimeError("source version GCS round-trip failed")
    trained = train_regressor(train, validation, test)
    val = trained["validation"]
    outcome = trained["test"]
    residuals = sorted(abs(a-p) for a,p in zip(val["actual"], val["predicted"]))
    width = residuals[min(len(residuals)-1, int(.9 * (len(residuals)-1)))]
    coverage = sum(abs(a-p) <= width for a,p in zip(outcome["actual"], outcome["predicted"])) / len(test)
    train_mean = sum(item.target for item in train) / len(train)
    mean_baseline = regression(outcome["actual"], [train_mean] * len(test))
    persistence = outcome["persistence"]
    skill = 1 - outcome["model"]["mae"] / persistence["mae"] if persistence["mae"] else None
    # Retrospective skill is necessary but not sufficient for live approval.
    readiness = "NOT_READY"
    reason = "forward availability and multi-fold stability not yet verified"
    split = gateway.save_dataset_split({"dataset_name": f"wave-height-{days}d-regression",
        "dataset_version": version, "station_id": "51201", "task_type": "regression", "target_metric": "WVHT",
        "target_horizon_days": days, "observation_granularity": "day_hst", "feature_window_days": 7,
        "feature_columns": list(FEATURE_NAMES), "label_definition": {"target": "HST day median" if days == 7 else "next 30 HST days mean", "unit": "m"},
        "preprocessing_parameters": {"source_manifest_sha256": source_sha, "source_manifest_uri": source_info.gcs_uri,
                                     "source_manifest_generation": source_info.generation, "quality": "valid observed days, >=90% coverage", "imputation": "train-only median"},
        "split_method": "chronological", "train_start_hst": str(train[0].anchor_date_hst),
        "train_end_hst": str(train[-1].anchor_date_hst), "validation_start_hst": str(validation[0].anchor_date_hst),
        "validation_end_hst": str(validation[-1].anchor_date_hst), "test_start_hst": str(test[0].anchor_date_hst),
        "test_end_hst": str(test[-1].anchor_date_hst), "train_sample_count": len(train),
        "validation_sample_count": len(validation), "test_sample_count": len(test),
        "dataset_gcs_uri": info.gcs_uri, "dataset_gcs_generation": str(info.generation), "dataset_sha256": sha},
        f"dataset:regression:{days}d:{version}")["dataset_split_id"]
    manifest = {"task_type": "regression", "horizon_days": days, "feature_names": list(FEATURE_NAMES),
                "dataset_sha256": sha, "source_manifest_sha256": source_sha,
                "prediction_target": "daily median" if days == 7 else "30-day mean"}
    artifact = store.save(trained["model"], task=f"regression-{days}d", version=version, manifest=manifest)
    model_id = gateway.save_model({"model_name": f"waimea-regression-{days}d", "model_version": version,
        "task_type": "regression", "algorithm": "RandomForestRegressor", "artifact_gcs_uri": artifact["artifact_gcs_uri"],
        "artifact_generation": artifact["artifact_generation"], "artifact_sha256": artifact["artifact_sha256"],
        "artifact_size_bytes": artifact["artifact_size_bytes"], "feature_names": list(FEATURE_NAMES), "status": "draft",
        "hyperparameters": {"horizon_days": days, "manifest_sha256": artifact["manifest_sha256"],
                            "manifest_generation": artifact["manifest_generation"]},
        "evaluation_metrics": {"readiness": readiness, "reason": reason, "quality_gate_version": "tl08b-v1"}},
        f"model:regression:{days}d:{version}")["model_id"]
    run_id = gateway.save_training_run({"dataset_split_id": split, "model_id": model_id,
        "task_type": "regression", "algorithm": "RandomForestRegressor", "status": "success",
        "run_name": f"regression:{days}d:{version}", "random_seed": 42},
        f"train:regression:{days}d:{version}")["training_run_id"]
    gateway.save_evaluation({"training_run_id": run_id, "model_id": model_id, "dataset_split_id": split,
        "evaluation_stage": "test", "task_type": "regression", "evaluation_name": "chronological-holdout",
        **outcome["model"], "additional_metrics": {"persistence": persistence, "train_mean": mean_baseline,
            "skill_vs_persistence": skill, "interval_90_half_width_m": width,
            "interval_90_test_coverage": coverage, "readiness": readiness, "reason": reason}},
        f"evaluation:regression:{days}d:{version}")
    return {"horizon": f"{days}d", "model_id": model_id, "dataset_sha256": sha,
            "source_manifest_sha256": source_sha, "train": len(train), "validation": len(validation), "test": len(test),
            "model": outcome["model"], "persistence": persistence, "train_mean": mean_baseline,
            "skill_vs_persistence": skill, "interval_90_coverage": coverage,
            "readiness": readiness, "reason": reason}
