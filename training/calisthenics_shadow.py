"""Forward-issued, isolated Calisthenics 1.5 shadow predictions.

Every forecast is reserved before its target, byte-verified in private GCS,
and evaluated only after QC truth exists. No model is promoted here.
"""
from __future__ import annotations

import hashlib
import json
import logging
from datetime import date, datetime, timedelta, timezone
from math import isfinite

from psycopg.types.json import Jsonb

from packages.gcs.client import RealGCSClient
from packages.ml.artifacts import ModelStore
from packages.ml.features import FEATURE_NAMES
from packages.ml.gateway import HttpMLGateway
from packages.ml.worker import WorkerSettings
from services.database_api.app.api.forward import day_bounds
from training.calisthenics_15 import _connect
from training.calisthenics_next_day import INPUT_CUTOFF_HOUR_HST, PUBLICATION_WAIT, input_cutoff, live_features
from training.calisthenics_v1 import OnlineWaveModel, SHORT_FEATURES, HST
from training.tl08c_forward_runner import daily_features, short_features, utc

logger = logging.getLogger(__name__)
HORIZONS = ("30min", "1d", "7d", "30d")
LEAD_DAYS = {"7d":7,"30d":30}


def _sha(payload) -> str:
    return hashlib.sha256(json.dumps(payload,sort_keys=True,default=str,
                                    separators=(",", ":")).encode()).hexdigest()


def _current_model(conn, horizon: str, store: ModelStore):
    params = (f"calisthenics 1.5 / {horizon}",)
    cutoff_filter = ""
    if horizon == "1d":
        cutoff_filter = "AND hyperparameters->>'input_cutoff_hour_hst'=%s "
        params += (str(INPUT_CUTOFF_HOUR_HST),)
    row = conn.execute("SELECT * FROM gold.model_registry WHERE model_name=%s "
        "AND status='draft' " + cutoff_filter + "ORDER BY created_at DESC,model_id DESC LIMIT 1",
        params).fetchone()
    if horizon == "1d" and row is None:
        return None
    if row is None or row["task_type"] != "regression":
        raise RuntimeError(f"Calisthenics 1.5 {horizon} draft model unavailable")
    model, manifest = store.load(row)
    names = SHORT_FEATURES if horizon == "30min" else FEATURE_NAMES
    if type(model) is not OnlineWaveModel or manifest.get("horizon") != horizon \
            or manifest.get("feature_names") != list(names):
        raise RuntimeError(f"Calisthenics 1.5 {horizon} artifact mismatch")
    if horizon == "1d" and manifest.get("input_cutoff_hour_hst") != INPUT_CUTOFF_HOUR_HST:
        raise RuntimeError("Calisthenics 1.5 next-day artifact cutoff mismatch")
    return row, model


def _reserve(conn, model_row: dict, horizon: str, issued: datetime,
             anchor: datetime, target: datetime, features: tuple,
             source_sha: str, anchor_day: date | None = None,
             target_day: date | None = None, target_kind: str = "instant_wvht") -> dict:
    if not anchor <= issued < target or not isfinite(float(features[0])):
        raise ValueError("invalid prospective forecast times or baseline")
    if horizon == "1d":
        issue_day = issued.astimezone(HST).date()
        tomorrow = issue_day + timedelta(days=1)
        if (anchor_day != issue_day or target_day != tomorrow
                or anchor != input_cutoff(issue_day) or target != day_bounds(tomorrow)[1]):
            raise ValueError("1d forecast must target the next Hawaii day")
    prediction = float(model_row["loaded_model"].predict([features])[0])
    if not isfinite(prediction) or prediction < 0:
        raise ValueError("invalid shadow prediction")
    conn.execute("INSERT INTO gold.calisthenics_shadow_forecasts "
        "(station_id,model_id,model_version,horizon,target_kind,issued_at,anchor_at,"
        "anchor_date_hst,target_at,target_date_hst,predicted_value,features_snapshot,source_sha256) "
        "VALUES ('51201',%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
        "ON CONFLICT (model_id,anchor_at) DO NOTHING",
        (model_row["model_id"],model_row["model_version"],horizon,target_kind,issued,
         anchor,anchor_day,target,target_day,prediction,
         Jsonb(dict(zip(SHORT_FEATURES if horizon=="30min" else FEATURE_NAMES,features))),source_sha))
    row = conn.execute("SELECT * FROM gold.calisthenics_shadow_forecasts "
        "WHERE model_id=%s AND anchor_at=%s",
        (model_row["model_id"],anchor)).fetchone()
    if row["horizon"] != horizon or row["target_at"] != target:
        raise RuntimeError("immutable shadow forecast collision")
    conn.commit()  # a reserved row survives a crash before GCS finalization
    return row


def _persist(conn, store: ModelStore, row: dict) -> bool:
    if row["gcs_uri"]:
        return False
    fields=("forecast_id","station_id","model_id","model_version","horizon",
        "target_kind","issued_at","anchor_at","anchor_date_hst","target_at",
        "target_date_hst","predicted_value","features_snapshot","source_sha256")
    payload=(json.dumps({key:row[key] for key in fields},sort_keys=True,
        separators=(",", ":"),default=str)+"\n").encode()
    sha=hashlib.sha256(payload).hexdigest()
    path=f"predictions/shadow/calisthenics-1.5/model={row['model_id']}/forecast={row['forecast_id']}/{sha}.json"
    info=store.client.upload_if_absent(store.bucket,path,payload,"application/json")
    if info.content_sha256 != sha or store.client.download(store.bucket,path,info.generation) != payload:
        raise RuntimeError("shadow GCS round-trip verification failed")
    conn.execute("UPDATE gold.calisthenics_shadow_forecasts SET gcs_uri=%s,"
        "gcs_generation=%s,gcs_sha256=%s WHERE forecast_id=%s AND gcs_uri IS NULL",
        (info.gcs_uri,str(info.generation),sha,row["forecast_id"]))
    saved=conn.execute("SELECT gcs_uri,gcs_generation,gcs_sha256 FROM "
        "gold.calisthenics_shadow_forecasts WHERE forecast_id=%s",
        (row["forecast_id"],)).fetchone()
    if (saved["gcs_uri"],saved["gcs_generation"],saved["gcs_sha256"]) != \
            (info.gcs_uri,str(info.generation),sha):
        raise RuntimeError("immutable shadow GCS pointer conflict")
    return True


def recover(conn, store):
    rows=conn.execute("SELECT * FROM gold.calisthenics_shadow_forecasts "
        "WHERE gcs_uri IS NULL ORDER BY forecast_id LIMIT 100").fetchall()
    recovered=0
    for row in rows:
        recovered+=_persist(conn,store,row)
        conn.commit()
    return recovered


def issue_tomorrow(conn, gateway, store, now: datetime, daily: list[dict]) -> dict | None:
    today = now.astimezone(HST).date()
    anchor = input_cutoff(today)
    if now < anchor + PUBLICATION_WAIT:
        return None
    target_day = today + timedelta(days=1)
    if conn.execute("SELECT 1 FROM gold.calisthenics_shadow_forecasts "
                    "WHERE horizon='1d' AND anchor_at=%s AND target_date_hst=%s LIMIT 1",
                    (anchor, target_day)).fetchone():
        return None
    observations = gateway.get_observations(day_bounds(today)[0], anchor)
    found = live_features(today, daily, observations, now)
    if found is None:
        return None
    current = _current_model(conn, "1d", store)
    if current is None:
        logger.info("No day-ahead Calisthenics 1.5 model for the Hawaii noon cutoff")
        return None
    metadata, model = current
    features, source_sha = found
    row = _reserve(conn, {**metadata, "loaded_model": model}, "1d", now, anchor,
                   day_bounds(target_day)[1], features, source_sha, today, target_day,
                   "hst_day_median_wvht")
    persisted = _persist(conn, store, row)
    conn.commit()
    return {"horizon": "1d", "forecast_id": row["forecast_id"], "issued": int(persisted),
            "target_date_hst": target_day.isoformat()}


def issue(conn, gateway, store, now: datetime) -> list[dict]:
    issued=[]
    observations=gateway.get_observations(now-timedelta(hours=5),now+timedelta(seconds=1))
    found=short_features(observations,now)
    if found:
        anchor,target,features,source=found
        if conn.execute("SELECT 1 FROM gold.resolution_metrics WHERE station_id='51201' "
            "AND metric_code='WVHT' AND resolution='30min' AND period_start_at<=%s "
            "AND period_end_at>%s AND readiness='GOLD_READY' AND computed_at<=%s LIMIT 1",
            (anchor,anchor,now)).fetchone():
            metadata,model=_current_model(conn,"30min",store)
            row=_reserve(conn,{**metadata,"loaded_model":model},"30min",now,anchor,target,
                         features,source["measurement_sha256"])
            issued.append({"horizon":"30min","forecast_id":row["forecast_id"],
                           "issued":int(_persist(conn,store,row))})
            conn.commit()

    today=now.astimezone(HST).date()
    rows=gateway._request("GET","/internal/v1/forward/daily-inputs",params={
        "start_hst":str(today-timedelta(days=8)),"end_hst":str(today)})["data"]
    day_ahead = issue_tomorrow(conn, gateway, store, now, rows)
    if day_ahead is not None:
        issued.append(day_ahead)
    daily=daily_features(rows,now)
    if daily:
        anchor_day,features,source_sha=daily
        anchor=day_bounds(anchor_day)[1]
        for horizon,days in LEAD_DAYS.items():
            target_day=anchor_day+timedelta(days=days)
            target_start,target=day_bounds(target_day)
            if now >= target_start:
                continue
            metadata,model=_current_model(conn,horizon,store)
            kind="next_30_hst_days_mean_wvht" if horizon=="30d" else "hst_day_median_wvht"
            row=_reserve(conn,{**metadata,"loaded_model":model},horizon,now,anchor,target,
                         features,source_sha,anchor_day,target_day,kind)
            issued.append({"horizon":horizon,"forecast_id":row["forecast_id"],
                           "issued":int(_persist(conn,store,row)),
                           "target_date_hst":str(target_day)})
            conn.commit()
    return issued


def _daily_truth(conn, row: dict):
    g=conn.execute("SELECT g.*,d.median_wave_height_m AS daily_median,d.coverage_ratio,"
        "d.suspect_count AS daily_suspect,"
        "d.rejected_count AS daily_rejected,d.unverified_count AS daily_unverified "
        "FROM gold.resolution_metrics g "
        "JOIN silver.daily_wave_features d ON d.station_id=g.station_id "
        "AND d.date_hst=(g.period_start_at AT TIME ZONE 'Pacific/Honolulu')::date "
        "WHERE g.station_id='51201' AND g.metric_code='WVHT' AND g.resolution='daily' "
        "AND g.period_start_at=%s AND g.period_end_at=%s",
        day_bounds(row["target_date_hst"])).fetchone()
    if not g or g["readiness"]!="GOLD_READY" or g["median_value"] is None \
            or g["daily_median"] is None \
            or g["computed_at"]<row["target_at"] or (g["coverage_ratio"] or 0)<.9 \
            or any(g[key] for key in ("daily_suspect","daily_rejected","daily_unverified")) \
            or abs(float(g["median_value"])-float(g["daily_median"]))>1e-6:
        return None
    actual=float(g["median_value"])
    if not isfinite(actual) or actual<0:
        return None
    return actual,_sha((row["target_date_hst"],actual,g["valid_sample_count"],
                        g["calculation_version"])),g["computed_at"]


def _thirty_day_truth(conn, row: dict):
    first=row["anchor_date_hst"]+timedelta(days=1)
    last=row["anchor_date_hst"]+timedelta(days=30)
    rows=conn.execute("SELECT d.date_hst,d.median_wave_height_m,d.coverage_ratio,"
        "d.suspect_count,d.rejected_count,d.unverified_count,"
        "g.median_value,g.readiness,g.valid_sample_count,g.calculation_version,g.computed_at "
        "FROM silver.daily_wave_features d JOIN gold.resolution_metrics g "
        "ON g.station_id=d.station_id AND g.metric_code='WVHT' AND g.resolution='daily' "
        "AND g.period_start_at=(d.date_hst::timestamp AT TIME ZONE 'Pacific/Honolulu') "
        "WHERE d.station_id='51201' AND d.date_hst BETWEEN %s AND %s ORDER BY d.date_hst",
        (first,last)).fetchall()
    valid=[]
    for item in rows:
        if item["readiness"]!="GOLD_READY" or item["median_value"] is None \
                or item["median_wave_height_m"] is None \
                or (item["coverage_ratio"] or 0)<.9 \
                or any(item[key] for key in ("suspect_count","rejected_count","unverified_count")):
            continue
        if abs(float(item["median_value"])-float(item["median_wave_height_m"]))>1e-6:
            continue
        if item["computed_at"] < day_bounds(item["date_hst"])[1]:
            continue
        valid.append(item)
    if len(valid)<27:
        return None
    value=sum(float(item["median_value"]) for item in valid)/len(valid)
    return value,_sha([(item["date_hst"],item["median_value"],item["valid_sample_count"],
                        item["calculation_version"]) for item in valid]),max(
                            row["target_at"],*(item["computed_at"] for item in valid))


def _instant_truth(conn, row: dict):
    source=conn.execute("SELECT o.wave_height_m,o.measurement_sha256,o.updated_at,"
        "g.readiness,g.median_value,g.computed_at FROM silver.wave_observations o "
        "JOIN gold.resolution_metrics g ON g.station_id=o.station_id "
        "AND g.metric_code='WVHT' AND g.resolution='30min' "
        "AND o.observed_at>=g.period_start_at AND o.observed_at<g.period_end_at "
        "WHERE o.station_id='51201' AND o.observed_at=%s AND o.quality_status='valid' "
        "AND o.source_kind='realtime'",(row["target_at"],)).fetchone()
    if not source or source["readiness"]!="GOLD_READY" or source["wave_height_m"] is None \
            or source["median_value"] is None \
            or source["computed_at"]<source["updated_at"] \
            or abs(float(source["median_value"])-float(source["wave_height_m"]))>1e-6:
        return None
    actual=float(source["wave_height_m"])
    if not isfinite(actual) or actual<0:
        return None
    return actual,source["measurement_sha256"],source["updated_at"]


def reconcile(conn, now: datetime, days: int = 8) -> dict:
    rows=conn.execute("SELECT * FROM gold.calisthenics_shadow_forecasts WHERE gcs_uri IS NOT NULL "
        "AND target_at<=%s AND target_at>=%s ORDER BY target_at DESC LIMIT 5000",
        (now,now-timedelta(days=days))).fetchall()
    inserted=0;pending=0
    for row in rows:
        if row["horizon"]=="30min": truth=_instant_truth(conn,row)
        elif row["horizon"]=="30d": truth=_thirty_day_truth(conn,row)
        else: truth=_daily_truth(conn,row)
        if truth is None:
            pending+=1;continue
        actual,sha,as_of=truth
        key="wave_lag0" if row["horizon"]=="30min" else "wave_height_median_lag0"
        baseline=row["features_snapshot"].get(key)
        if baseline is None or not isfinite(float(baseline)) or float(baseline)<0:
            pending+=1;continue
        error=abs(actual-float(row["predicted_value"]))
        result=conn.execute("INSERT INTO gold.calisthenics_shadow_evaluations "
            "(forecast_id,actual_value,absolute_error,squared_error,"
            "persistence_absolute_error,truth_sha256,truth_as_of_at) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (forecast_id,truth_sha256) "
            "DO NOTHING RETURNING evaluation_id",
            (row["forecast_id"],actual,error,error*error,abs(actual-float(baseline)),sha,as_of)).fetchone()
        inserted+=result is not None
    return {"scanned":len(rows),"evaluations_inserted":inserted,"pending_truth":pending}


def run_once(now: datetime | None = None):
    now=now or datetime.now(timezone.utc)
    settings=WorkerSettings()
    if settings.gcs_models_bucket!="waimea-noaa":
        raise RuntimeError("private waimea-noaa bucket required")
    store=ModelStore(RealGCSClient(),settings.gcs_models_bucket)
    gateway=HttpMLGateway(settings.database_api_url,settings.internal_api_token)
    with _connect() as conn:
        recovered=recover(conn,store)
        truth=reconcile(conn,now,90 if now.hour==3 and now.minute<5 else 8)
        forecast=issue(conn,gateway,store,datetime.now(timezone.utc))
    return {"recovered":recovered,"truth":truth,"forecasts":forecast}


def main():
    logging.basicConfig(level=logging.INFO)
    print(json.dumps(run_once(),sort_keys=True))


if __name__=="__main__":
    main()
