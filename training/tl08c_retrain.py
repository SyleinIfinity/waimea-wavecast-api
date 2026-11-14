"""Controlled DRAFT retraining from forward-issued, QC-matched NOAA truth."""
from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timedelta
from math import isfinite

from packages.ml.features import FEATURE_NAMES, Sample
from packages.ml.metrics import regression
from training.train_regression import train as fit_regression
from training.tl08b_short_horizons import FEATURES as SHORT_FEATURES
from services.database_api.app.api.forward import HST

FEATURES = {"30min":SHORT_FEATURES,"1d":FEATURE_NAMES,"7d":FEATURE_NAMES}
MINIMUM = {"30min":300,"1d":180,"7d":180}


def verified_examples(rows: list[dict], horizon: str) -> list[dict]:
    features = FEATURES[horizon]
    result = []
    for row in rows:
        if row.get("horizon") != horizon or row.get("actual_value") is None or not row.get("truth_sha256"):
            continue
        issued = datetime.fromisoformat(str(row["issued_at"]).replace("Z","+00:00"))
        target = datetime.fromisoformat(str(row["target_at"]).replace("Z","+00:00"))
        if issued.tzinfo is None or target.tzinfo is None or issued >= target:
            continue
        lead = target-issued
        if (horizon=="30min" and not timedelta(minutes=15)<=lead<=timedelta(minutes=45)) or (horizon=="1d" and lead<=timedelta(hours=20)) or (horizon=="7d" and lead<=timedelta(days=1)):
            continue
        actual = float(row["actual_value"])
        if not isfinite(actual) or actual < 0:
            continue
        snapshot = row["features_snapshot"]
        if set(snapshot) != set(features):
            continue
        x = tuple(None if snapshot[name] is None else float(snapshot[name]) for name in features)
        if not isfinite(float(x[0])):
            continue
        result.append({"forecast_id":int(row["forecast_id"]),"anchor_at":datetime.fromisoformat(
            str(row["anchor_at"]).replace("Z","+00:00")),"target_at":target,"features":x,
            "actual":actual,"truth_sha256":row["truth_sha256"],"source_sha256":row["source_sha256"]})
    return sorted(result,key=lambda item:item["anchor_at"])


def split_examples(rows: list[dict], horizon: str):
    if len(rows) < MINIMUM[horizon]:
        raise ValueError("insufficient forward NOAA truth")
    n = len(rows)
    # Choose whole HST day boundaries because dataset_splits records DATE ranges.
    first_day = rows[int(n*.7)]["anchor_at"].astimezone(HST).date()
    second_day = rows[int(n*.85)]["anchor_at"].astimezone(HST).date()
    train = [x for x in rows if x["anchor_at"].astimezone(HST).date()<first_day
             and x["target_at"].astimezone(HST).date()<first_day]
    validation = [x for x in rows if first_day<=x["anchor_at"].astimezone(HST).date()<second_day
                  and x["target_at"].astimezone(HST).date()<second_day]
    test = [x for x in rows if x["anchor_at"].astimezone(HST).date()>=second_day]
    if min(map(len,(train,validation,test))) < 20:
        raise ValueError("insufficient embargoed forward folds")
    if train[-1]["target_at"] >= validation[0]["anchor_at"] or validation[-1]["target_at"] >= test[0]["anchor_at"]:
        raise ValueError("future-label leakage")
    return train,validation,test


def train_forward(rows: list[dict], horizon: str, gateway, store) -> dict:
    verified = verified_examples(rows,horizon)
    minimum = MINIMUM[horizon]
    if len(verified) < minimum:
        return {"horizon":horizon,"status":"insufficient_truth","count":len(verified),"minimum":minimum}
    # Immutable batches avoid fitting again on every new evaluation.
    batch_size = minimum
    n = (len(verified)//batch_size)*batch_size
    verified = verified[:n]
    train,validation,test = split_examples(verified,horizon)
    def wrap(items):
        return [Sample(x["anchor_at"].astimezone(HST).date(),
                       x["target_at"].astimezone(HST).date(),x["features"],x["actual"]) for x in items]
    trained = fit_regression(wrap(train),wrap(validation),wrap(test))
    outcome = trained["test"]
    train_mean = sum(x["actual"] for x in train)/len(train)
    mean_baseline = regression(outcome["actual"],[train_mean]*len(test))
    snapshot = [{"forecast_id":x["forecast_id"],"anchor_at":x["anchor_at"].isoformat(),
                 "target_at":x["target_at"].isoformat(),"features":x["features"],
                 "actual":x["actual"],"truth_sha256":x["truth_sha256"],
                 "source_sha256":x["source_sha256"]} for x in verified]
    payload = (json.dumps(snapshot,sort_keys=True,separators=(",", ":"))+"\n").encode()
    sha = hashlib.sha256(payload).hexdigest()
    version = f"forward-{horizon}-n{n}-{sha[:10]}"
    dataset_path = f"datasets/training/forward/horizon={horizon}/{version}/dataset-{sha}.json"
    info = store.client.upload_if_absent(store.bucket,dataset_path,payload,"application/json")
    if info.content_sha256 != sha or store.client.download(store.bucket,dataset_path,info.generation) != payload:
        raise RuntimeError("forward training dataset GCS verification failed")
    names = FEATURES[horizon]
    hst = lambda item:item["anchor_at"].astimezone(HST).date().isoformat()
    split_id = gateway.save_dataset_split({"dataset_name":f"forward-noaa-{horizon}","dataset_version":version,
        "station_id":"51201","task_type":"regression","target_metric":"WVHT",
        "target_horizon_days":{"1d":1,"7d":7}.get(horizon),
        "observation_granularity":"observation" if horizon=="30min" else "day_hst",
        "feature_columns":list(names),"label_definition":{"source":"QC-valid NOAA after forward-issued forecast",
            "horizon":horizon,"unit":"m","never_use_prediction_as_label":True},
        "preprocessing_parameters":{"imputation":"train-only median","source":"gold.forward_training_examples",
                                    "embargo":"target before next fold anchor"},
        "split_method":"chronological","train_start_hst":hst(train[0]),"train_end_hst":hst(train[-1]),
        "validation_start_hst":hst(validation[0]),"validation_end_hst":hst(validation[-1]),
        "test_start_hst":hst(test[0]),"test_end_hst":hst(test[-1]),
        "train_sample_count":len(train),"validation_sample_count":len(validation),
        "test_sample_count":len(test),"dataset_gcs_uri":info.gcs_uri,
        "dataset_gcs_generation":str(info.generation),"dataset_sha256":sha},
        f"dataset:forward:{horizon}:{version}")["dataset_split_id"]
    artifact = store.save(trained["model"],task=f"forward-regression-{horizon}",version=version,
        manifest={"task_type":"regression","horizon":"90min" if horizon=="30min" else horizon,
                  "operational_horizon":horizon,"horizon_days":{"1d":1,"7d":7}.get(horizon),
                  "feature_names":list(names),
                  "dataset_sha256":sha,"source":"forward NOAA truth","quality_status":"experimental"})
    model_id = gateway.save_model({"model_name":f"waimea-forward-retrained-{horizon}",
        "model_version":version,"task_type":"regression","algorithm":"RandomForestRegressor",
        "artifact_gcs_uri":artifact["artifact_gcs_uri"],"artifact_generation":artifact["artifact_generation"],
        "artifact_sha256":artifact["artifact_sha256"],"artifact_size_bytes":artifact["artifact_size_bytes"],
        "feature_names":list(names),"status":"draft",
        "hyperparameters":{"horizon":"90min" if horizon=="30min" else horizon,
                           "operational_horizon":horizon,
                           "horizon_days":{"1d":1,"7d":7}.get(horizon),
                           "manifest_sha256":artifact["manifest_sha256"],
                           "manifest_generation":artifact["manifest_generation"]},
        "evaluation_metrics":{"readiness":"NOT_READY","reason":"new draft requires independent forward validation",
                              "quality_gate_version":"tl08c-forward-v1"}},
        f"model:forward:{horizon}:{version}")["model_id"]
    run_id = gateway.save_training_run({"dataset_split_id":split_id,"model_id":model_id,
        "task_type":"regression","algorithm":"RandomForestRegressor","status":"success",
        "run_name":f"forward:{horizon}:{version}","random_seed":42},
        f"train:forward:{horizon}:{version}")["training_run_id"]
    gateway.save_evaluation({"training_run_id":run_id,"model_id":model_id,"dataset_split_id":split_id,
        "evaluation_stage":"test","task_type":"regression","evaluation_name":"forward-truth-holdout",
        **outcome["model"],"additional_metrics":{"persistence":outcome["persistence"],
            "train_mean":mean_baseline,"source":"forward-issued QC NOAA truth","readiness":"NOT_READY"}},
        f"evaluation:forward:{horizon}:{version}")
    return {"horizon":horizon,"status":"trained_draft","model_id":model_id,
            "dataset_sha256":sha,"sample_count":n,"test":outcome["model"],
            "persistence":outcome["persistence"]}
