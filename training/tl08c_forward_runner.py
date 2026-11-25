"""Production forward forecasts from pinned NOAA models with QC truth reconciliation.

Forecasts from DRAFT models are real, immutable production observations marked
experimental. They never enter the approved Gold predictions table.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
from datetime import date, datetime, timedelta, timezone
from math import isfinite

import httpx
from packages.gcs.client import RealGCSClient
from packages.ml.artifacts import ModelStore
from packages.ml.features import FEATURE_NAMES, build_samples
from packages.ml.gateway import HttpMLGateway
from packages.ml.worker import WorkerSettings
from training.tl08b_short_horizons import FEATURES as SHORT_FEATURES
from services.database_api.app.api.forward import HST, day_bounds

logger = logging.getLogger(__name__)
LAGS = (timedelta(), timedelta(minutes=30), timedelta(hours=1), timedelta(hours=3))


def utc(value):
    result = datetime.fromisoformat(value.replace("Z", "+00:00")) if isinstance(value, str) else value
    if result.tzinfo is None:
        raise ValueError("timezone offset required")
    return result.astimezone(timezone.utc)


def valid_observation(row: dict, issued_at: datetime) -> bool:
    if row.get("quality_status") != "valid" or str(row.get("source_kind", "")).lower() != "realtime":
        return False
    value = row.get("wave_height_m")
    if value is None or not isfinite(float(value)) or float(value) < 0:
        return False
    if utc(row["observed_at"]) > issued_at:
        return False
    for key in ("first_seen_at", "updated_at"):
        if row.get(key) and utc(row[key]) > issued_at:
            return False
    return True


def short_features(rows: list[dict], issued_at: datetime):
    valid = {utc(row["observed_at"]): row for row in rows if valid_observation(row, issued_at)}
    for anchor in sorted(valid, reverse=True):
        target = anchor + timedelta(minutes=90)  # publication latency leaves ~30-minute issued lead
        if not timedelta(minutes=15) <= target-issued_at <= timedelta(minutes=45):
            continue
        history = [valid.get(anchor - lag) for lag in LAGS]
        if any(item is None for item in history):
            continue
        features = tuple(float(item["wave_height_m"]) for item in history) + (
            history[0].get("dominant_period_s"),history[2].get("dominant_period_s"))
        return anchor,target,features,valid[anchor]
    return None


def ready_daily_row(row: dict | None, issued_at: datetime) -> bool:
    return bool(
        row is not None
        and row.get("gold_readiness") == "GOLD_READY"
        and row.get("median_wave_height_m") is not None
        and row.get("gold_median_value") is not None
        and abs(float(row["median_wave_height_m"]) - float(row["gold_median_value"])) <= 1e-6
        and (row.get("valid_wave_count") or 0) > 0
        and (row.get("coverage_ratio") or 0) >= .9
        and not any(row.get(key) for key in ("suspect_count", "rejected_count", "unverified_count", "imputed_count"))
        and row.get("gold_period_end_at")
        and row.get("gold_computed_at")
        and utc(row["gold_period_end_at"]) <= utc(row["gold_computed_at"]) <= issued_at
        and row.get("calculated_at")
        and utc(row["gold_period_end_at"]) <= utc(row["calculated_at"]) <= issued_at
        and (not row.get("source_data_max_updated_at") or utc(row["source_data_max_updated_at"]) <= issued_at)
    )


def daily_features(rows: list[dict], issued_at: datetime):
    by_day = {date.fromisoformat(str(row["date_hst"])): row for row in rows}
    # Use the latest complete HST day with seven continuous QC Gold-ready days.
    for anchor_day in sorted(by_day, reverse=True):
        if day_bounds(anchor_day)[1] > issued_at:
            continue
        history = [by_day.get(anchor_day-timedelta(days=i)) for i in range(7)]
        if not all(ready_daily_row(row, issued_at) for row in history):
            continue
        # build_samples' retrospective T+1 02:00 cutoff is intentionally
        # replaced by the real issued_at check above for forward issuance.
        safe = [{k:v for k,v in row.items() if k != "source_data_max_updated_at"} for row in history]
        samples = [item for item in build_samples(safe, require_target=False)
                   if item.anchor_date_hst == anchor_day]
        if not samples:
            continue
        source = [{"date_hst":str(row["date_hst"]),"median_wave_height_m":row["median_wave_height_m"],
                   "mean_dominant_period_s":row.get("mean_dominant_period_s"),
                   "valid_wave_count":row["valid_wave_count"],"coverage_ratio":row["coverage_ratio"],
                   "gold_computed_at":row["gold_computed_at"],"gold_readiness":row["gold_readiness"]}
                  for row in history]
        source_sha = hashlib.sha256(json.dumps(source,sort_keys=True,default=str,separators=(",", ":")).encode()).hexdigest()
        return anchor_day,samples[0].features,source_sha
    return None


def _payload(forecast: dict) -> bytes:
    keys = ("forecast_id","station_id","model_id","model_version","horizon","target_kind",
            "quality_status","issued_at","anchor_at","anchor_date_hst","target_at",
            "target_date_hst","predicted_value","features_snapshot","source_sha256")
    return (json.dumps({key:forecast[key] for key in keys},sort_keys=True,separators=(",", ":")) + "\n").encode()


def persist_forecast(gateway, store, forecast: dict) -> bool:
    if forecast.get("gcs_uri"):
        return False
    payload = _payload(forecast)
    sha = hashlib.sha256(payload).hexdigest()
    path = f"predictions/forward/model={forecast['model_id']}/forecast={forecast['forecast_id']}/{sha}.json"
    info = store.client.upload_if_absent(store.bucket,path,payload,"application/json")
    if info.content_sha256 != sha or store.client.download(store.bucket,path,info.generation) != payload:
        raise RuntimeError("forward forecast GCS round-trip failed")
    gateway._request("POST",f"/internal/v1/forward/forecasts/{forecast['forecast_id']}/finalize",body={
        "gcs_uri":info.gcs_uri,"gcs_generation":str(info.generation),"gcs_sha256":sha})
    return True


def recover_unfinalized(gateway, store) -> int:
    rows = gateway._request("GET","/internal/v1/forward/forecasts/unfinalized",params={"limit":500})["data"]
    return sum(persist_forecast(gateway,store,row) for row in rows)


def _load_model(gateway, store, model_id: int, expected_features: tuple[str,...]):
    metadata = gateway._request("GET",f"/internal/v1/models/{model_id}")
    if metadata["task_type"] != "regression" or metadata["status"] not in ("draft","ready"):
        raise ValueError("configured model unavailable")
    model,manifest = store.load(metadata)
    if manifest.get("feature_names") != list(expected_features):
        raise ValueError("model feature schema mismatch")
    return metadata,model,manifest


def issue_short(gateway, store, model_id: int, now: datetime) -> dict:
    rows = gateway.get_observations(now-timedelta(hours=5),now+timedelta(seconds=1))
    found = short_features(rows,now)
    if found is None:
        return {"issued":0,"reason":"no prospective QC-valid 30-minute target"}
    anchor,target,features,source = found
    metadata,model,manifest = _load_model(gateway,store,model_id,SHORT_FEATURES)
    if (metadata.get("hyperparameters") or {}).get("horizon") != "90min" or manifest.get("horizon") != "90min":
        raise ValueError("wrong source-lead model for issued 30-minute forecast")
    prediction = float(model.predict([list(features)])[0])
    if not isfinite(prediction) or prediction < 0:
        raise ValueError("invalid prediction")
    try:
        forecast = gateway._request("POST","/internal/v1/forward/forecasts/reserve",body={
            "station_id":"51201","model_id":model_id,"model_version":metadata["model_version"],
            "horizon":"30min","target_kind":"instant_wvht","issued_at":now.isoformat(),
            "anchor_at":anchor.isoformat(),"target_at":target.isoformat(),"predicted_value":prediction,
            "features_snapshot":dict(zip(SHORT_FEATURES,features)),"source_sha256":source["measurement_sha256"]})
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 422 and "QC Gold source is not ready" in exc.response.text:
            return {"issued":0,"reason":"anchor Gold quality gate pending"}
        raise
    return {"issued":int(persist_forecast(gateway,store,forecast)),"forecast_id":forecast["forecast_id"],
            "target_at":target.isoformat(),"lead_minutes":round((target-now).total_seconds()/60,1)}


def issue_daily(gateway, store, model_ids: dict[str,int], now: datetime) -> dict:
    today = now.astimezone(HST).date()
    rows = gateway._request("GET","/internal/v1/forward/daily-inputs",params={
        "start_hst":str(today-timedelta(days=8)),"end_hst":str(today)})["data"]
    found = daily_features(rows,now)
    if found is None:
        return {"issued":0,"reason":"no complete seven-day QC Gold window"}
    anchor_day,features,source_sha = found
    snapshot = dict(zip(FEATURE_NAMES,features))
    issued = []
    for horizon,days in (("1d",1),("7d",7)):
        model_id = model_ids[horizon]
        target_day = anchor_day + timedelta(days=days)
        target_start,target_end = day_bounds(target_day)
        if target_end <= now:
            continue
        if horizon == "1d" and now >= target_start + timedelta(hours=4):
            continue
        if horizon == "7d" and now >= target_start:
            continue
        metadata,model,manifest = _load_model(gateway,store,model_id,FEATURE_NAMES)
        if horizon == "7d" and manifest.get("horizon_days") != 7 and manifest.get("operational_horizon") != "7d":
            raise ValueError("wrong seven-day model")
        if horizon == "1d" and manifest.get("task_type") != "regression":
            raise ValueError("wrong next-day model")
        prediction = float(model.predict([list(features)])[0])
        if not isfinite(prediction) or prediction < 0:
            raise ValueError("invalid daily prediction")
        forecast = gateway._request("POST","/internal/v1/forward/forecasts/reserve",body={
            "station_id":"51201","model_id":model_id,"model_version":metadata["model_version"],
            "horizon":horizon,"target_kind":"hst_day_median_wvht","issued_at":now.isoformat(),
            "anchor_at":day_bounds(anchor_day)[1].isoformat(),"anchor_date_hst":anchor_day.isoformat(),
            "target_at":target_end.isoformat(),"target_date_hst":target_day.isoformat(),
            "predicted_value":prediction,"features_snapshot":snapshot,"source_sha256":source_sha})
        issued.append({"horizon":horizon,"forecast_id":forecast["forecast_id"],
                       "issued":int(persist_forecast(gateway,store,forecast)),
                       "target_date_hst":target_day.isoformat()})
    return {"issued":sum(item["issued"] for item in issued),"forecasts":issued}


def export_training_examples(gateway, store, horizon: str) -> dict:
    rows = []
    cursor = 0
    while True:
        result = gateway._request("GET","/internal/v1/forward/training-examples",params={
            "horizon":horizon,"after_id":cursor,"limit":1000})
        page = result["data"]
        rows.extend(page)
        if len(page) < 1000:
            break
        cursor = result["next_after_id"]
    if not rows:
        return {"horizon":horizon,"examples":0}
    payload = (json.dumps(rows,sort_keys=True,separators=(",", ":")) + "\n").encode()
    sha = hashlib.sha256(payload).hexdigest()
    path = f"datasets/training/forward/horizon={horizon}/snapshot-{sha}.json"
    info = store.client.upload_if_absent(store.bucket,path,payload,"application/json")
    if info.content_sha256 != sha or store.client.download(store.bucket,path,info.generation) != payload:
        raise RuntimeError("forward training snapshot GCS round-trip failed")
    return {"horizon":horizon,"examples":len(rows),"gcs_uri":info.gcs_uri,
            "generation":str(info.generation),"sha256":sha}


def run_once(now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    settings = WorkerSettings()
    if not settings.gcs_models_bucket or settings.gcs_models_bucket != "waimea-noaa":
        raise RuntimeError("private waimea-noaa model bucket required")
    ids = {h:int(os.environ[f"FORWARD_{h.upper()}_MODEL_ID"]) for h in ("30MIN","1D","7D")}
    # Keys above are normalized explicitly to avoid dynamically built env names.
    gateway = HttpMLGateway(settings.database_api_url,settings.internal_api_token)
    store = ModelStore(RealGCSClient(),settings.gcs_models_bucket)
    failures = []
    def stage(name, action):
        try:
            return action()
        except Exception as exc:
            logger.exception("forward stage failed: %s",name)
            failures.append(name)
            return {"error":type(exc).__name__}
    recovered = stage("recover",lambda:recover_unfinalized(gateway,store))
    days = 90 if now.hour == 3 and now.minute < 5 else 8
    truth = stage("reconcile",lambda:gateway._request("POST","/internal/v1/forward/reconcile",params={"days":days}))
    should_export = bool(isinstance(truth,dict) and truth.get("evaluations_inserted")) or now.minute < 5
    exports = stage("export-training",lambda:[export_training_examples(gateway,store,h)
                    for h in ("30min","1d","7d")]) if should_export else []
    short = stage("issue-30min",lambda:issue_short(gateway,store,ids["30MIN"],datetime.now(timezone.utc)))
    daily = stage("issue-daily",lambda:issue_daily(gateway,store,{"1d":ids["1D"],"7d":ids["7D"]},datetime.now(timezone.utc)))
    result = {"recovered":recovered,"truth":truth,"training_exports":exports,
              "short":short,"daily":daily,"failed_stages":failures}
    if failures:
        logger.error("forward cycle partial failure: %s",json.dumps(result,sort_keys=True))
        raise RuntimeError("forward cycle had failed stages: "+",".join(failures))
    return result


def main():
    logging.basicConfig(level=logging.INFO)
    result = run_once()
    logger.info("forward cycle: %s",json.dumps(result,sort_keys=True))


if __name__ == "__main__":
    main()
