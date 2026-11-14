"""Calisthenics 1.5: 2026 continuation and 20-shadow-pair 30-minute updates.

The bootstrap is retrospective, even for stored realtime observations. Only
forward-issued QC-matched Calisthenics shadow pairs are live.
All produced models remain DRAFT; no production model pointer is changed.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
from datetime import datetime, timedelta, timezone
from math import isfinite

import psycopg
from psycopg.rows import dict_row

from packages.gcs.client import RealGCSClient
from packages.ml.artifacts import ModelStore
from packages.ml.features import FEATURE_NAMES
from packages.ml.gateway import HttpMLGateway
from packages.ml.metrics import regression
from packages.ml.worker import WorkerSettings
from services.database_api.app.config import get_settings
from training.calisthenics_v1 import (Event, OnlineWaveModel, HST,
    daily_events, short_events, SHORT_FEATURES)

BASE_IDS = {"30min":21,"1d":18,"7d":19,"30d":20}
START = datetime(2026,1,1,tzinfo=timezone.utc)
LIVE_BATCH = 20


def _connect():
    s = get_settings()
    return psycopg.connect(host=s.db_host,port=s.db_port,dbname=s.db_name,
                           user=s.db_user,password=s.db_password,
                           sslmode=s.db_sslmode,row_factory=dict_row)


def _store_gateway():
    w = WorkerSettings()
    if w.gcs_models_bucket != "waimea-noaa":
        raise RuntimeError("private waimea-noaa bucket required")
    return ModelStore(RealGCSClient(),w.gcs_models_bucket),HttpMLGateway(w.database_api_url,w.internal_api_token)


def _timestamp(value):
    result = datetime.fromisoformat(value.replace("Z","+00:00")) if isinstance(value,str) else value
    if result.tzinfo is None:
        raise ValueError("timezone-aware timestamp required")
    return result.astimezone(timezone.utc)


def load_2026_events(conn,horizon: str,cutoff: datetime) -> tuple[list[Event],dict]:
    if horizon == "30min":
        rows = conn.execute("SELECT observed_at,wave_height_m,dominant_period_s,quality_status,"
            "source_kind,first_seen_at,updated_at FROM silver.wave_observations "
            "WHERE station_id='51201' AND observed_at>='2025-12-31 20:00+00' "
            "AND observed_at<=%s ORDER BY observed_at",(cutoff,)).fetchall()
        source = {_timestamp(r["observed_at"]):r for r in rows}
        counts = {kind:sum(r["source_kind"]==kind for r in rows)
                  for kind in ("historical_monthly","realtime")}
        events = []
        for item in short_events(rows,end=cutoff):
            if item.issue < START:
                continue
            anchor = source[item.anchor]
            if anchor["source_kind"] == "realtime" and any(
                anchor.get(key) and _timestamp(anchor[key]) > item.issue
                for key in ("first_seen_at","updated_at")):
                continue
            events.append(item)
        return events,counts
    rows = conn.execute("SELECT d.*,g.readiness AS gold_readiness "
        "FROM silver.daily_wave_features d LEFT JOIN gold.resolution_metrics g "
        "ON g.station_id=d.station_id AND g.resolution='daily' AND g.metric_code='WVHT' "
        "AND g.period_start_at=(d.date_hst::timestamp AT TIME ZONE 'Pacific/Honolulu') "
        "WHERE d.station_id='51201' AND d.date_hst BETWEEN '2025-12-20' AND %s "
        "ORDER BY d.date_hst",(cutoff.date(),)).fetchall()
    ready = [r for r in rows if r["gold_readiness"]=="GOLD_READY"]
    events = [e for e in daily_events(ready,int(horizon[:-1]),end=cutoff)
              if e.issue >= START]
    return events,{"daily_rows":len(rows),"gold_ready_rows":len(ready)}


def continue_2026(model: OnlineWaveModel,events: list[Event],cutoff: datetime,
                  *, batch_size: int = LIVE_BATCH) -> tuple[list[dict],dict]:
    pending: list[Event] = []
    ready: list[Event] = []
    cursor = 0
    records = []
    for event in sorted(events,key=lambda e:e.issue):
        if event.issue >= cutoff or event.target > cutoff:
            continue
        while cursor<len(pending) and pending[cursor].target<event.issue:
            ready.append(pending[cursor]); cursor+=1
        if len(ready)>=batch_size:
            model.update(ready); ready=[]
        predicted=float(model.predict([event.features])[0])
        baseline=float(event.features[0])
        if not all(isfinite(x) and x>=0 for x in (predicted,baseline,event.actual)):
            raise ValueError("invalid 2026 outcome")
        records.append({"issued_at_simulated":event.issue.isoformat(),"target_at":event.target.isoformat(),
                        "predicted":predicted,"actual":event.actual,"persistence":baseline,
                        "absolute_error":abs(predicted-event.actual),
                        "learned_count_before_issue":model.learned_count})
        pending.append(event)
    ready.extend(e for e in pending[cursor:] if e.target<=cutoff)
    model.update(ready)  # bootstrap includes all known 2026 truth
    if not records:
        raise ValueError("no QC-valid 2026 labels for horizon")
    actual=[r["actual"] for r in records]
    metrics={"model":regression(actual,[r["predicted"] for r in records]),
             "persistence":regression(actual,[r["persistence"] for r in records]),
             "count":len(records),"final_learned_count":model.learned_count}
    return records,metrics


def _write_log(store:ModelStore,horizon:str,kind:str,records:list[dict]):
    raw=("\n".join(json.dumps(r,sort_keys=True,separators=(",",":")) for r in records)+"\n").encode()
    data=gzip.compress(raw,mtime=0)
    sha=hashlib.sha256(data).hexdigest()
    path=f"training/calisthenics/1.5/horizon={horizon}/{kind}-{sha}.jsonl.gz"
    info=store.client.upload_if_absent(store.bucket,path,data,"application/gzip")
    if info.content_sha256!=sha or store.client.download(store.bucket,path,info.generation)!=data:
        raise RuntimeError("Calisthenics 1.5 GCS log verification failed")
    return info,sha


def _register(model:OnlineWaveModel,horizon:str,version:str,base_id:int,cutoff:datetime,
              records:list[dict],metrics:dict,last_forward_id:int,kind:str,seed_learned_count:int,
              last_evaluation_id:int,
              store:ModelStore,gateway:HttpMLGateway,*,last_shadow_forecast_id:int=0,
              last_shadow_evaluation_id:int=0):
    info,sha=_write_log(store,horizon,kind,records)
    names=SHORT_FEATURES if horizon=="30min" else FEATURE_NAMES
    artifact=store.save(model,task=f"calisthenics-1.5-{horizon}",version=version,
        manifest={"task_type":"regression","horizon":horizon,"feature_names":list(names),
                  "parent_model_id":base_id,"replay_sha256":sha,"replay_gcs_uri":info.gcs_uri,
                  "quality_status":"experimental","readiness":"NOT_READY"})
    target_days=[_timestamp(record["target_at"]).astimezone(HST).date() for record in records]
    start_day="2026-01-01" if kind=="bootstrap" else min(target_days).isoformat()
    end_day=max(target_days).isoformat()
    split=gateway.save_dataset_split({"dataset_name":f"calisthenics-1.5-{horizon}",
        "dataset_version":version,"station_id":"51201","task_type":"regression","target_metric":"WVHT",
        "target_horizon_days":{"1d":1,"7d":7,"30d":30}.get(horizon),
        "observation_granularity":"observation" if horizon=="30min" else "day_hst",
        "feature_columns":list(names),"label_definition":{"source":kind,"target":"WVHT"},
        "preprocessing_parameters":{"parent_model_id":base_id,"update_batch":LIVE_BATCH,
        "log_gcs_uri":info.gcs_uri,"log_generation":str(info.generation),
        "historical_publication_time":"simulated" if kind=="bootstrap" else "forward-issued",
        "final_learned_count":model.learned_count},
        "split_method":"walk_forward","train_start_hst":"2005-01-01" if horizon!="30min" else "2008-01-01",
        "train_end_hst":"2025-12-31","test_start_hst":start_day,"test_end_hst":end_day,
        "train_sample_count":seed_learned_count,"test_sample_count":len(records),
        "dataset_gcs_uri":info.gcs_uri,"dataset_gcs_generation":str(info.generation),
        "dataset_sha256":sha},f"dataset:calisthenics:1.5:{horizon}:{version}")["dataset_split_id"]
    saved=gateway.save_model({"model_name":f"calisthenics 1.5 / {horizon}","model_version":version,
        "task_type":"regression","algorithm":"SGDRegressor","status":"draft",
        "artifact_gcs_uri":artifact["artifact_gcs_uri"],"artifact_generation":artifact["artifact_generation"],
        "artifact_sha256":artifact["artifact_sha256"],"artifact_size_bytes":artifact["artifact_size_bytes"],
        "feature_names":list(names),"train_start_hst":"2005-01-01" if horizon!="30min" else "2008-01-01",
        "train_end_hst":cutoff.date().isoformat(),"test_start_hst":start_day,"test_end_hst":end_day,
        "hyperparameters":{"horizon":horizon,"parent_model_id":base_id,
        "last_forward_forecast_id":last_forward_id,
        "last_forward_evaluation_id":last_evaluation_id,
        "last_shadow_forecast_id":last_shadow_forecast_id,
        "last_shadow_evaluation_id":last_shadow_evaluation_id,
        "update_batch":LIVE_BATCH,
        "manifest_sha256":artifact["manifest_sha256"],"manifest_generation":artifact["manifest_generation"],
        "log_sha256":sha,"log_generation":str(info.generation)},
        "evaluation_metrics":{"readiness":"NOT_READY","kind":kind,"metrics":metrics,
        "reason":"independent shadow forward skill under accumulation"}},
        f"model:calisthenics:1.5:{horizon}:{version}")
    model_id=saved["model_id"]
    if saved.get("duplicate"):
        previous=gateway._request("GET",f"/internal/v1/models/{model_id}")
        if previous["artifact_sha256"]!=artifact["artifact_sha256"]:
            raise RuntimeError("immutable Calisthenics 1.5 version conflict")
    run=gateway.save_training_run({"dataset_split_id":split,"model_id":model_id,
        "task_type":"regression","algorithm":"SGDRegressor","status":"success",
        "run_name":f"calisthenics:1.5:{horizon}:{version}","random_seed":42},
        f"run:calisthenics:1.5:{horizon}:{version}")["training_run_id"]
    gateway.save_evaluation({"training_run_id":run,"model_id":model_id,"dataset_split_id":split,
        "evaluation_stage":"backtest",
        "task_type":"regression","evaluation_name":"2026-continuation" if kind=="bootstrap" else "shadow-forward-20",
        **metrics["model"],"additional_metrics":{"persistence":metrics["persistence"],
        "log_sha256":sha,"parent_model_id":base_id,"readiness":"NOT_READY",
        "calisthenics_issued_before_target":kind=="shadow20",
        "scored_model_ids":sorted({r["issued_model_id"] for r in records if "issued_model_id" in r})}},
        f"eval:calisthenics:1.5:{horizon}:{version}")
    return {"model_id":model_id,"horizon":horizon,"version":version,"status":"draft",
            "metrics":metrics,"log_gcs_uri":info.gcs_uri,"log_generation":str(info.generation),
            "log_sha256":sha,"last_forward_forecast_id":last_forward_id,
            "last_forward_evaluation_id":last_evaluation_id,
            "last_shadow_forecast_id":last_shadow_forecast_id,
            "last_shadow_evaluation_id":last_shadow_evaluation_id}


def bootstrap(horizon:str):
    store,gateway=_store_gateway()
    base_id=BASE_IDS[horizon]
    base=gateway._request("GET",f"/internal/v1/models/{base_id}")
    if base["status"]!="draft" or base["model_name"]!=f"calisthenics 1.0 / {horizon}":
        raise RuntimeError("unexpected v1.0 base model")
    model,manifest=store.load(base)
    if type(model) is not OnlineWaveModel or manifest["horizon"]!=horizon:
        raise RuntimeError("incompatible v1.0 artifact")
    with _connect() as conn:
        cutoff=conn.execute("SELECT LEAST(now(),COALESCE(max(observed_at),now())) AS cutoff "
            "FROM silver.wave_observations WHERE station_id='51201' AND source_kind='realtime'").fetchone()["cutoff"]
        events,source_counts=load_2026_events(conn,horizon,cutoff)
        last_id=conn.execute("SELECT COALESCE(max(forecast_id),0) AS n FROM gold.forward_training_examples "
            "WHERE horizon='30min'").fetchone()["n"] if horizon=="30min" else 0
        last_evaluation_id=conn.execute("SELECT COALESCE(max(e.evaluation_id),0) AS n "
            "FROM gold.forward_training_examples v JOIN gold.forward_evaluations e "
            "ON e.forecast_id=v.forecast_id AND e.truth_sha256=v.truth_sha256 "
            "WHERE v.horizon='30min'").fetchone()["n"] if horizon=="30min" else 0
    seed_learned_count=model.learned_count
    records,metrics=continue_2026(model,events,cutoff)
    info,sha=_write_log(store,horizon,"bootstrap",records)
    # Reuse the content-based log path; _register verifies it again.
    version=f"v1.5-base-{sha[:12]}"
    result=_register(model,horizon,version,base_id,cutoff,records,metrics,last_id,"bootstrap",
                     seed_learned_count,last_evaluation_id,store,gateway)
    return {**result,"source_rows":source_counts,"cutoff_utc":cutoff.isoformat()}


def watch_30min():
    store,gateway=_store_gateway()
    with _connect() as conn:
        current=conn.execute("SELECT * FROM gold.model_registry WHERE model_name='calisthenics 1.5 / 30min' "
            "AND status='draft' ORDER BY created_at DESC,model_id DESC LIMIT 1").fetchone()
        if current is None:
            raise RuntimeError("bootstrap 30min model first")
        params=current["hyperparameters"] or {}
        last_shadow_id=int(params.get("last_shadow_forecast_id",0))
        last_shadow_evaluation_id=int(params.get("last_shadow_evaluation_id",0))
        corrected=conn.execute("SELECT e.forecast_id FROM gold.calisthenics_shadow_evaluations e "
            "JOIN gold.calisthenics_shadow_forecasts f USING(forecast_id) "
            "WHERE e.evaluation_id>%s AND f.horizon='30min' "
            "AND EXISTS (SELECT 1 FROM gold.calisthenics_shadow_evaluations prior "
            "WHERE prior.forecast_id=e.forecast_id AND prior.evaluation_id<=%s) LIMIT 1",
            (last_shadow_evaluation_id,last_shadow_evaluation_id)).fetchone()
        if corrected:
            raise RuntimeError("QC truth correction requires model rebuild; forecast_id="
                               +str(corrected["forecast_id"]))
        rows=conn.execute("SELECT f.*,e.evaluation_id,e.actual_value,e.truth_sha256,e.evaluated_at "
            "FROM gold.calisthenics_shadow_forecasts f "
            "JOIN gold.model_registry m ON m.model_id=f.model_id "
            "AND m.model_name='calisthenics 1.5 / 30min' "
            "JOIN LATERAL (SELECT evaluation_id,actual_value,truth_sha256,evaluated_at "
            "FROM gold.calisthenics_shadow_evaluations WHERE forecast_id=f.forecast_id "
            "ORDER BY evaluation_id DESC LIMIT 1) e ON true "
            "WHERE f.horizon='30min' AND f.gcs_uri IS NOT NULL "
            "AND e.evaluation_id>%s ORDER BY e.evaluation_id LIMIT %s",
            (last_shadow_evaluation_id,LIVE_BATCH)).fetchall()
    if len(rows)<LIVE_BATCH:
        return {"status":"waiting_for_shadow_truth","model_id":current["model_id"],
                "new_pairs":len(rows),"required":LIVE_BATCH}
    model,manifest=store.load(current)
    if type(model) is not OnlineWaveModel or manifest["horizon"]!="30min":
        raise RuntimeError("incompatible Calisthenics 1.5 model")
    records=[]; events=[]
    for row in rows:
        snapshot=row["features_snapshot"]
        if set(snapshot)!=set(SHORT_FEATURES):
            raise ValueError("forward feature schema mismatch")
        features=tuple(snapshot[k] for k in SHORT_FEATURES)
        actual=float(row["actual_value"])
        predicted=float(row["predicted_value"])
        baseline=float(features[0])
        if (_timestamp(row["issued_at"])>=_timestamp(row["target_at"])
                or _timestamp(row["evaluated_at"])<_timestamp(row["target_at"])
                or not all(isfinite(x) and x>=0 for x in (predicted,baseline,actual))):
            raise ValueError("invalid forward-issued shadow evidence")
        events.append(Event(_timestamp(row["anchor_at"]),_timestamp(row["issued_at"]),
                            _timestamp(row["target_at"]),features,actual))
        records.append({"forecast_id":row["forecast_id"],"issued_model_id":row["model_id"],
            "issued_model_version":row["model_version"],"issued_at":_timestamp(row["issued_at"]).isoformat(),
            "target_at":_timestamp(row["target_at"]).isoformat(),"predicted":predicted,
            "actual":actual,"persistence":baseline,"absolute_error":abs(predicted-actual),
            "truth_sha256":row["truth_sha256"],"learned_count_before_issue":model.learned_count})
    seed_learned_count=model.learned_count
    model.update(events)
    values=[r["actual"] for r in records]
    metrics={"model":regression(values,[r["predicted"] for r in records]),
             "persistence":regression(values,[r["persistence"] for r in records]),
             "count":len(records),"final_learned_count":model.learned_count}
    last=max(last_shadow_id,*(row["forecast_id"] for row in rows))
    last_shadow_evaluation_id=rows[-1]["evaluation_id"]
    digest=hashlib.sha256(json.dumps([(r["forecast_id"],r["truth_sha256"]) for r in records]).encode()).hexdigest()
    version=f"v1.5-shadow-e{last_shadow_evaluation_id}-{digest[:10]}"
    return _register(model,"30min",version,current["model_id"],datetime.now(timezone.utc),
                     records,metrics,int(params.get("last_forward_forecast_id",0)),"shadow20",
                     seed_learned_count,int(params.get("last_forward_evaluation_id",0)),store,gateway,
                     last_shadow_forecast_id=last,last_shadow_evaluation_id=last_shadow_evaluation_id)


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("action",choices=("bootstrap","watch"))
    parser.add_argument("--horizon",choices=tuple(BASE_IDS))
    args=parser.parse_args()
    if args.action=="bootstrap":
        if not args.horizon:
            parser.error("--horizon required for bootstrap")
        result=bootstrap(args.horizon)
    else:
        result=watch_30min()
    print(json.dumps(result,sort_keys=True))


if __name__=="__main__":
    main()
