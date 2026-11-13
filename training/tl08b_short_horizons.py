"""QC-filtered, exact-timestamp short-horizon WVHT model training."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from packages.ml.features import Sample
from packages.ml.metrics import regression
from training.train_regression import train as fit_regression

SHORT_HORIZONS = {"30min": timedelta(minutes=30), "1h": timedelta(hours=1),
                  "6h": timedelta(hours=6), "24h": timedelta(hours=24)}
HST = ZoneInfo("Pacific/Honolulu")
FEATURES = ("wave_lag0", "wave_lag30m", "wave_lag1h", "wave_lag3h",
            "period_lag0", "period_lag1h")


def build_short_samples(rows: list[dict], horizon: str) -> list[tuple]:
    delta = SHORT_HORIZONS[horizon]
    valid = {}
    for row in rows:
        if row.get("quality_status") != "valid" or row.get("wave_height_m") is None:
            continue
        source = str(row.get("source_kind") or "").lower()
        if source != "realtime" and not source.startswith("historical"):
            continue
        stamp = datetime.fromisoformat(str(row["observed_at"]).replace("Z", "+00:00"))
        if stamp.tzinfo is None:
            raise ValueError("observation timestamp must be timezone aware")
        valid[stamp.astimezone(timezone.utc)] = row
    output = []
    for anchor in sorted(valid):
        lags = [valid.get(anchor - lag) for lag in (timedelta(), timedelta(minutes=30),
                                                    timedelta(hours=1), timedelta(hours=3))]
        target = valid.get(anchor + delta)
        if target is None or any(row is None for row in lags):
            continue
        features = tuple(float(row["wave_height_m"]) for row in lags) + (
            lags[0].get("dominant_period_s"), lags[2].get("dominant_period_s"))
        # Sample uses date fields; exact UTC timestamps live in the parallel
        # dataset records and the split below, never rounded to HST days.
        output.append((anchor, anchor + delta, features, float(target["wave_height_m"])))
    return output


def split_short(samples: list[tuple], horizon: str):
    ordered = sorted(samples, key=lambda item: item[0])
    first, second = int(len(ordered) * .7), int(len(ordered) * .85)
    # Registry split dates are HST DATE, so make both boundaries full HST days.
    # This is stricter than a timestamp-only embargo and keeps metadata true.
    validation_boundary = datetime.combine(ordered[first][0].astimezone(HST).date(), time.min, HST).astimezone(timezone.utc)
    test_boundary = datetime.combine(ordered[second][0].astimezone(HST).date(), time.min, HST).astimezone(timezone.utc)
    train = [row for row in ordered if row[0] < validation_boundary and row[1] < validation_boundary]
    validation = [row for row in ordered if validation_boundary <= row[0] < test_boundary
                  and row[1] < test_boundary]
    test = [row for row in ordered if row[0] >= test_boundary]
    if min(len(train), len(validation), len(test)) < 100:
        raise ValueError("insufficient exact timestamp samples")
    if train[-1][1] >= validation[0][0] or validation[-1][1] >= test[0][0]:
        raise AssertionError("short-horizon split leakage")
    return train, validation, test


def train_short(rows, store, gateway, *, horizon, version, source_versions):
    if not source_versions:
        raise ValueError("verified source versions required")
    samples = build_short_samples(rows, horizon)
    train, validation, test = split_short(samples, horizon)
    def wrap(items):
        return [Sample(anchor.date(), target_time.date(), tuple(features), target)
                for anchor, target_time, features, target in items]
    trained = fit_regression(wrap(train), wrap(validation), wrap(test))
    outcome = trained["test"]
    train_mean = sum(row[3] for row in train) / len(train)
    mean_baseline = regression(outcome["actual"], [train_mean] * len(test))
    snapshot = [{"anchor_utc": anchor.isoformat(), "target_utc": target_time.isoformat(),
                 "features": features, "target": target} for anchor, target_time, features, target in samples]
    data = (json.dumps(snapshot, separators=(",", ":")) + "\n").encode()
    sha = hashlib.sha256(data).hexdigest()
    info = store.client.upload_if_absent(store.bucket,
        f"datasets/training/wave-height/{version}/{horizon}/{sha}.json", data, "application/json")
    if info.content_sha256 != sha or store.client.download(store.bucket, info.gcs_uri.split("/", 3)[3], info.generation) != data:
        raise RuntimeError("dataset GCS verification failed")
    source_data = (json.dumps(source_versions, sort_keys=True, separators=(",", ":")) + "\n").encode()
    source_sha = hashlib.sha256(source_data).hexdigest()
    source_info = store.client.upload_if_absent(store.bucket,
        f"datasets/training/wave-height/{version}/{horizon}/source-{source_sha}.json", source_data, "application/json")
    if source_info.content_sha256 != source_sha or store.client.download(store.bucket, source_info.gcs_uri.split("/", 3)[3], source_info.generation) != source_data:
        raise RuntimeError("source GCS verification failed")
    split = gateway.save_dataset_split({"dataset_name": f"wave-height-{horizon}", "dataset_version": version,
        "station_id": "51201", "task_type": "regression", "target_metric": "WVHT",
        "observation_granularity": "observation", "feature_columns": list(FEATURES),
        "label_definition": {"target": "WVHT exact UTC timestamp", "horizon": horizon, "unit": "m"},
        "preprocessing_parameters": {"source_manifest_sha256": source_sha,
                                     "source_manifest_uri": source_info.gcs_uri,
                                     "source_manifest_generation": source_info.generation,
                                     "train_only_median_imputation": ["dominant_period_s"]},
        "split_method": "chronological", "train_start_hst": str(train[0][0].astimezone(HST).date()),
        "train_end_hst": str(train[-1][0].astimezone(HST).date()), "validation_start_hst": str(validation[0][0].astimezone(HST).date()),
        "validation_end_hst": str(validation[-1][0].astimezone(HST).date()), "test_start_hst": str(test[0][0].astimezone(HST).date()),
        "test_end_hst": str(test[-1][0].astimezone(HST).date()), "train_sample_count": len(train),
        "validation_sample_count": len(validation), "test_sample_count": len(test),
        "dataset_gcs_uri": info.gcs_uri, "dataset_gcs_generation": str(info.generation), "dataset_sha256": sha},
        f"dataset:regression:{horizon}:{version}")["dataset_split_id"]
    artifact = store.save(trained["model"], task=f"regression-{horizon}", version=version,
        manifest={"task_type": "regression", "horizon": horizon, "feature_names": list(FEATURES),
                  "dataset_sha256": sha, "source_manifest_sha256": source_sha,
                  "target": "exact future UTC WVHT"})
    model_id = gateway.save_model({"model_name": f"waimea-regression-{horizon}", "model_version": version,
        "task_type": "regression", "algorithm": "RandomForestRegressor", "artifact_gcs_uri": artifact["artifact_gcs_uri"],
        "artifact_generation": artifact["artifact_generation"], "artifact_sha256": artifact["artifact_sha256"],
        "artifact_size_bytes": artifact["artifact_size_bytes"], "feature_names": list(FEATURES), "status": "draft",
        "hyperparameters": {"horizon": horizon, "manifest_sha256": artifact["manifest_sha256"],
                            "manifest_generation": artifact["manifest_generation"]},
        "evaluation_metrics": {"readiness": "NOT_READY", "reason": "multi-fold and live availability pending",
                               "quality_gate_version": "tl08b-v1"}},
        f"model:regression:{horizon}:{version}")["model_id"]
    run_id = gateway.save_training_run({"dataset_split_id": split, "model_id": model_id,
        "task_type": "regression", "algorithm": "RandomForestRegressor", "status": "success",
        "run_name": f"regression:{horizon}:{version}", "random_seed": 42},
        f"train:regression:{horizon}:{version}")["training_run_id"]
    gateway.save_evaluation({"training_run_id": run_id, "model_id": model_id, "dataset_split_id": split,
        "evaluation_stage": "test", "task_type": "regression", "evaluation_name": "chronological-holdout",
        **outcome["model"], "additional_metrics": {"persistence": outcome["persistence"],
            "train_mean": mean_baseline, "readiness": "NOT_READY"}},
        f"evaluation:regression:{horizon}:{version}")
    return {"horizon": horizon, "model_id": model_id, "sample_count": len(samples),
            "train": len(train), "validation": len(validation), "test": len(test),
            "dataset_sha256": sha, "source_manifest_sha256": source_sha,
            "model": outcome["model"], "persistence": outcome["persistence"],
            "train_mean": mean_baseline, "readiness": "NOT_READY"}
