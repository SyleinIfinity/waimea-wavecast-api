"""Chronological NOAA replay for the experimental Calisthenics 1.0 family.

Historical outcomes are scored immediately in wall-clock time, but a label is
only supplied to the learner after its simulated target period has closed.
This is a retrospective simulation, never a live-issued forecast.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from math import isfinite
from zoneinfo import ZoneInfo

import numpy as np
import psycopg
from psycopg.rows import dict_row
from sklearn.impute import SimpleImputer
from sklearn.linear_model import SGDRegressor
from sklearn.preprocessing import StandardScaler

from packages.gcs.client import RealGCSClient
from packages.ml.artifacts import ModelStore
from packages.ml.features import FEATURE_NAMES, build_samples
from packages.ml.gateway import HttpMLGateway
from packages.ml.metrics import regression
from packages.ml.worker import WorkerSettings
from services.database_api.app.config import get_settings
from training.tl08b_daily_horizons import samples_for_horizon
from training.tl08b_short_horizons import FEATURES as SHORT_FEATURES, build_short_samples

HST = ZoneInfo("Pacific/Honolulu")
VERSION = "v1.0"
END = datetime(2026, 1, 1, tzinfo=timezone.utc)
HORIZONS = ("30min", "1d", "7d", "30d")


@dataclass(frozen=True)
class Event:
    anchor: datetime
    issue: datetime
    target: datetime
    features: tuple
    actual: float


class OnlineWaveModel:
    """Fixed seed-only preprocessing and bounded, ten-label SGD updates."""

    def __init__(self, seed: list[Event]):
        if len(seed) < 100:
            raise ValueError("at least 100 mature seed samples required")
        x = np.asarray([item.features for item in seed], dtype=float)
        y = np.asarray([item.actual for item in seed], dtype=float)
        self.imputer = SimpleImputer(strategy="median", keep_empty_features=True).fit(x)
        self.scaler = StandardScaler().fit(self.imputer.transform(x))
        self.estimator = SGDRegressor(loss="huber", alpha=0.001, random_state=42,
                                      learning_rate="invscaling", eta0=0.005)
        self.estimator.partial_fit(self.scaler.transform(self.imputer.transform(x)), y)
        self.learned_count = len(seed)
        self.update_count = 0

    def predict(self, rows):
        # Replay has one issue event at a time. Avoid a full sklearn transform
        # allocation for every historical timestamp; coefficients are updated
        # only after each mature batch.
        result = []
        for row in rows:
            values = (float(self.imputer.statistics_[i]) if value is None or np.isnan(value)
                      else float(value) for i, value in enumerate(row))
            score = float(self.estimator.intercept_[0]) + sum(
                ((value-float(mean))/float(scale))*float(coef)
                for value,mean,scale,coef in zip(values,self.scaler.mean_,self.scaler.scale_,
                                                  self.estimator.coef_))
            result.append(max(0.0, score))
        return np.asarray(result)

    def update(self, batch: list[Event]):
        if not batch:
            return
        x = np.asarray([item.features for item in batch], dtype=float)
        y = np.asarray([item.actual for item in batch], dtype=float)
        self.estimator.partial_fit(self.scaler.transform(self.imputer.transform(x)), y)
        self.learned_count += len(batch)
        self.update_count += 1


def _hst_start(day: date) -> datetime:
    return datetime.combine(day, time.min, HST).astimezone(timezone.utc)


def daily_events(rows: list[dict], days: int, *, end: datetime = END) -> list[Event]:
    clean = [{k: v for k, v in row.items() if k != "source_data_max_updated_at"}
             for row in rows if (row.get("valid_wave_count") or 0) > 0
             and (row.get("coverage_ratio") or 0) >= .9
             and not any(row.get(k) for k in ("suspect_count", "rejected_count",
                                              "unverified_count", "imputed_count"))]
    samples = (build_samples(clean) if days == 1 else samples_for_horizon(clean, days))
    events = []
    for item in samples:
        issue = _hst_start(item.anchor_date_hst + timedelta(days=1)) + timedelta(hours=2)
        target = _hst_start(item.target_date_hst + timedelta(days=1))
        if item.target is not None and target <= end and issue < target:
            events.append(Event(_hst_start(item.anchor_date_hst), issue, target,
                                item.features, float(item.target)))
    return sorted(events, key=lambda item: item.issue)


def short_events(rows: list[dict], *, end: datetime = END) -> list[Event]:
    events = []
    for anchor, target, features, actual in build_short_samples(rows, "90min"):
        # Historical publication times are unknown. The 60-minute delay is an
        # explicit simulation assumption, not a claim about NOAA's 2008 feed.
        issue = anchor + timedelta(minutes=60)
        if target <= end and all(v is None or isfinite(float(v)) for v in features):
            events.append(Event(anchor, issue, target, features, actual))
    return sorted(events, key=lambda item: item.issue)


def replay(events: list[Event], horizon: str, *, batch_size: int = 10) -> tuple[OnlineWaveModel, list[dict], dict]:
    if batch_size < 1:
        raise ValueError("batch size must be positive")
    start = datetime(2009 if horizon == "30min" else 2007, 1, 1, tzinfo=timezone.utc)
    seed = [e for e in events if e.target <= start and e.issue < start]
    model = OnlineWaveModel(seed)
    testing = [e for e in events if start <= e.issue < END and e.target <= END]
    pending: list[Event] = []
    ready: list[Event] = []
    outputs = []
    cursor = 0
    for event in testing:
        # Mature previous labels before issuing this prediction. A target at
        # exactly the issue time is conservatively treated as unpublished.
        while cursor < len(pending) and pending[cursor].target < event.issue:
            ready.append(pending[cursor])
            cursor += 1
        if len(ready) >= batch_size:
            model.update(ready)
            ready = []
        predicted = float(model.predict([event.features])[0])
        baseline = float(event.features[0])
        if not all(isfinite(v) and v >= 0 for v in (predicted, event.actual, baseline)):
            raise ValueError("non-finite or negative forecast/target")
        outputs.append({"anchor": event.anchor.isoformat(), "issued_at_simulated": event.issue.isoformat(),
                        "target_at": event.target.isoformat(), "predicted": predicted,
                        "actual": event.actual, "absolute_error": abs(predicted-event.actual),
                        "persistence": baseline, "learned_count_before_issue": model.learned_count})
        pending.append(event)
    # Final v1.0 artifact includes all labels mature by the end of 2025.
    ready.extend(e for e in pending[cursor:] if e.target <= END)
    model.update(ready)
    if not outputs:
        raise ValueError("no historical replay outcomes")
    actual = [item["actual"] for item in outputs]
    metrics = {"model": regression(actual, [item["predicted"] for item in outputs]),
               "persistence": regression(actual, [item["persistence"] for item in outputs]),
               "seed_count": len(seed), "replayed_count": len(outputs),
               "final_learned_count": model.learned_count, "update_count": model.update_count}
    return model, outputs, metrics


def load_daily(conn) -> list[dict]:
    return conn.execute("SELECT * FROM silver.daily_wave_features WHERE station_id='51201' "
                        "AND date_hst BETWEEN '2004-12-20' AND '2025-12-31' ORDER BY date_hst").fetchall()


def load_observations(conn) -> list[dict]:
    # The first years have hourly cadence. 2008 is the 30-minute seed year.
    return conn.execute("SELECT observed_at,wave_height_m,dominant_period_s,quality_status,source_kind "
                        "FROM silver.wave_observations WHERE station_id='51201' "
                        "AND observed_at BETWEEN '2008-01-01' AND '2025-12-31 23:59:59+00' "
                        "ORDER BY observed_at").fetchall()


def publish(horizon: str, model: OnlineWaveModel, outputs: list[dict], metrics: dict,
            store: ModelStore, gateway: HttpMLGateway) -> dict:
    raw = ("\n".join(json.dumps(x, sort_keys=True, separators=(",", ":")) for x in outputs) + "\n").encode()
    data = gzip.compress(raw, mtime=0)
    digest = hashlib.sha256(data).hexdigest()
    path = f"training/calisthenics/1.0/horizon={horizon}/replay-{digest}.jsonl.gz"
    info = store.client.upload_if_absent(store.bucket, path, data, "application/gzip")
    if info.content_sha256 != digest or store.client.download(store.bucket, path, info.generation) != data:
        raise RuntimeError("historical replay GCS round-trip failed")
    names = SHORT_FEATURES if horizon == "30min" else FEATURE_NAMES
    artifact = store.save(model, task=f"calisthenics-1.0-{horizon}",
        version=f"{VERSION}-replay-{digest[:12]}",
        manifest={"task_type":"regression", "horizon":horizon, "feature_names":list(names),
                  "replay_sha256":digest, "replay_gcs_uri":info.gcs_uri,
                  "simulation_assumption":"historical 30min inputs available 60m after observation; daily at HST 02:00",
                  "quality_status":"experimental", "readiness":"NOT_READY"})
    started = "2008-01-01" if horizon == "30min" else "2005-01-01"
    seed_end = "2008-12-31" if horizon == "30min" else "2006-12-31"
    replay_start = "2009-01-01" if horizon == "30min" else "2007-01-01"
    split = gateway.save_dataset_split({"dataset_name":f"calisthenics-1.0-{horizon}",
        "dataset_version":VERSION,"station_id":"51201","task_type":"regression","target_metric":"WVHT",
        "target_horizon_days":{"1d":1,"7d":7,"30d":30}.get(horizon),
        "observation_granularity":"observation" if horizon=="30min" else "day_hst",
        "feature_columns":list(names),"label_definition":{"source":"retrospective QC NOAA",
        "target":"exact WVHT at anchor+90m" if horizon=="30min" else "HST day median" if horizon!="30d" else "mean of 27+ valid days in next 30"},
        "preprocessing_parameters":{"label_maturity":"target period closed before model update",
        "update_batch":10,"replay_gcs_uri":info.gcs_uri,"replay_generation":str(info.generation),
        "publication_time":"simulated; historical first_seen_at unavailable"},
        "split_method":"walk_forward","train_start_hst":started,"train_end_hst":seed_end,
        "test_start_hst":replay_start,"test_end_hst":"2025-12-31",
        "train_sample_count":metrics["seed_count"],"test_sample_count":len(outputs),
        "dataset_gcs_uri":info.gcs_uri,"dataset_gcs_generation":str(info.generation),"dataset_sha256":digest},
        f"dataset:calisthenics:1.0:{horizon}")["dataset_split_id"]
    saved_model = gateway.save_model({"model_name":f"calisthenics 1.0 / {horizon}","model_version":VERSION,
        "task_type":"regression","algorithm":"SGDRegressor","artifact_gcs_uri":artifact["artifact_gcs_uri"],
        "artifact_generation":artifact["artifact_generation"],"artifact_sha256":artifact["artifact_sha256"],
        "artifact_size_bytes":artifact["artifact_size_bytes"],"feature_names":list(names),"status":"draft",
        "train_start_hst":started,"train_end_hst":"2025-12-31",
        "test_start_hst":replay_start,"test_end_hst":"2025-12-31",
        "hyperparameters":{"horizon":horizon,"manifest_sha256":artifact["manifest_sha256"],
        "manifest_generation":artifact["manifest_generation"],"update_batch":10,
        "replay_sha256":digest,"replay_generation":str(info.generation)},
        "evaluation_metrics":{"readiness":"NOT_READY","replay":metrics,
        "reason":"retrospective availability simulated; independent live performance pending"}},
        f"model:calisthenics:1.0:{horizon}")
    model_id = saved_model["model_id"]
    if saved_model.get("duplicate"):
        registered = gateway._request("GET",f"/internal/v1/models/{model_id}")
        if registered["artifact_sha256"] != artifact["artifact_sha256"]:
            raise RuntimeError("v1.0 is immutable; source/model change requires a new version")
    run = gateway.save_training_run({"dataset_split_id":split,"model_id":model_id,
        "task_type":"regression","algorithm":"SGDRegressor","status":"success",
        "run_name":f"calisthenics:1.0:{horizon}","random_seed":42},
        f"run:calisthenics:1.0:{horizon}")["training_run_id"]
    gateway.save_evaluation({"training_run_id":run,"model_id":model_id,"dataset_split_id":split,
        "evaluation_stage":"backtest","task_type":"regression", "evaluation_name":"chronological-replay",
        **metrics["model"],"additional_metrics":{"persistence":metrics["persistence"],
        "seed_count":metrics["seed_count"],"replayed_count":metrics["replayed_count"],
        "update_count":metrics["update_count"],"publication_time_simulated":True,
        "replay_sha256":digest,"readiness":"NOT_READY"}},
        f"eval:calisthenics:1.0:{horizon}")
    return {"horizon":horizon,"model_id":model_id,"status":"draft","metrics":metrics,
            "replay_gcs_uri":info.gcs_uri,"replay_generation":str(info.generation),"replay_sha256":digest}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--horizon", choices=HORIZONS, required=True)
    args = parser.parse_args()
    settings = get_settings()
    worker = WorkerSettings()
    if worker.gcs_models_bucket != "waimea-noaa" or not settings.db_password:
        raise RuntimeError("production database and private model bucket required")
    with psycopg.connect(host=settings.db_host,port=settings.db_port,dbname=settings.db_name,
                         user=settings.db_user,password=settings.db_password,
                         sslmode=settings.db_sslmode,row_factory=dict_row) as conn:
        rows = load_observations(conn) if args.horizon == "30min" else load_daily(conn)
    events = short_events(rows) if args.horizon == "30min" else daily_events(rows,int(args.horizon[:-1]))
    del rows
    model, outcomes, metrics = replay(events,args.horizon)
    store = ModelStore(RealGCSClient(),worker.gcs_models_bucket)
    gateway = HttpMLGateway(worker.database_api_url,worker.internal_api_token)
    print(json.dumps(publish(args.horizon,model,outcomes,metrics,store,gateway),sort_keys=True))


if __name__ == "__main__":
    # Import by its canonical package path before serializing OnlineWaveModel.
    # Otherwise joblib stores __main__.OnlineWaveModel and a fresh worker
    # cannot load the artifact.
    from training.calisthenics_v1 import main as package_main
    package_main()
