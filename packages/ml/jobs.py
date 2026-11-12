"""TL05 worker jobs; all SQL access goes through MLGateway."""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from math import sqrt
from zoneinfo import ZoneInfo

from packages.ml.analytics import aggregate, daily_correlations, decompose
from packages.ml.artifacts import ModelStore
from packages.ml.features import FEATURE_NAMES, build_samples, matrix
from packages.ml.gateway import MLGateway

HST = ZoneInfo("Pacific/Honolulu")
JOB_TYPES = {"time_series_metrics": "aggregation", "trend_seasonal_components": "seasonality",
             "correlations": "correlation", "predictions": "prediction",
             "classifications": "classification", "cluster_assignments": "clustering"}


def _job(gateway: MLGateway, kind: str, start: date, end: date, key: str) -> tuple[int, bool]:
    result = gateway.create_job({"job_type": JOB_TYPES[kind], "input_period_start": start.isoformat(),
                                 "input_period_end": end.isoformat(), "algorithm_version": "tl05-v1",
                                 "parameters": {"idempotency_key": key, "pattern_type": kind}}, key)
    return int(result["processing_run_id"]), bool(result.get("duplicate") and result.get("status") == "success")


def run_analysis(gateway: MLGateway, kind: str, start: date, end: date, key: str) -> dict:
    if kind not in ("time_series_metrics", "trend_seasonal_components", "correlations") or end < start:
        raise ValueError("invalid analysis request")
    job_id, completed = _job(gateway, kind, start, end, key)
    if completed:
        return {"processing_run_id": job_id, "status": "success", "duplicate": True}
    try:
        fetch_start, fetch_end = start, end
        if kind == "time_series_metrics":
            fetch_start = min(start.replace(day=1), start - timedelta(days=start.weekday()))
            next_month = (end.replace(day=28) + timedelta(days=4)).replace(day=1)
            fetch_end = max(next_month - timedelta(days=1), end + timedelta(days=6 - end.weekday()))
        elif kind == "trend_seasonal_components":
            month_index = start.year * 12 + start.month - 1 - 36
            fetch_start = date(month_index // 12, month_index % 12 + 1, 1)
            fetch_end = (end.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
        utc_start = datetime.combine(fetch_start, time.min, HST).astimezone(timezone.utc)
        utc_end = datetime.combine(fetch_end + timedelta(days=1), time.min, HST).astimezone(timezone.utc)
        observations = gateway.get_observations(utc_start, utc_end)
        if kind == "time_series_metrics":
            rows = [row for period in ("day", "week", "month") for metric in ("WVHT", "DPD")
                    for row in aggregate(observations, period, metric)]
            rows = [row for row in rows if start <= date.fromisoformat(row["period_start_hst"]) <= end]
        elif kind == "trend_seasonal_components":
            monthly = aggregate(observations, "month", "WVHT")
            last_month = (fetch_end.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
            monthly = [row for row in monthly if date.fromisoformat(row["period_start_hst"]).month != last_month.month
                       or date.fromisoformat(row["period_start_hst"]).year != last_month.year
                       or end >= last_month]
            rows = decompose(monthly)
            rows = [row for row in rows if start <= date.fromisoformat(row["period_start_hst"]) <= end]
        else:
            rows = daily_correlations(observations, start, end)
        for row in rows:
            row["processing_run_id"] = job_id
        if rows:
            gateway.save_gold_results(kind, rows, key)
        status = "success" if rows else "skipped"
        gateway.update_job(job_id, status, len(rows))
        return {"processing_run_id": job_id, "status": status, "saved": len(rows)}
    except Exception as exc:
        gateway.update_job(job_id, "failed", error=type(exc).__name__)
        raise


def run_inference(gateway: MLGateway, store: ModelStore, kind: str, cutoff: date, key: str) -> dict:
    task = {"predictions": "regression", "classifications": "classification", "cluster_assignments": "clustering"}.get(kind)
    if task is None:
        raise ValueError("invalid inference task")
    job_id, completed = _job(gateway, kind, cutoff, cutoff + timedelta(days=1), key)
    if completed:
        return {"processing_run_id": job_id, "status": "success", "duplicate": True}
    try:
        metadata = gateway.get_ready_model(task)
        if metadata is None:
            gateway.update_job(job_id, "skipped", error="model_unavailable")
            return {"processing_run_id": job_id, "status": "insufficient_data", "reason": "model_unavailable"}
        model, manifest = store.load(metadata)
        if manifest.get("feature_names") != list(FEATURE_NAMES):
            raise ValueError("model feature order mismatch")
        daily = gateway.get_daily_features(cutoff - timedelta(days=6), cutoff)
        samples = [sample for sample in build_samples(daily, require_target=False) if sample.anchor_date_hst == cutoff]
        if not samples:
            gateway.update_job(job_id, "skipped", error="daily_features_incomplete")
            return {"processing_run_id": job_id, "status": "insufficient_data", "reason": "daily_features_incomplete"}
        sample = samples[0]
        value = model.predict(matrix(samples))[0]
        issued = datetime.now(timezone.utc).isoformat()
        row = {"model_id": metadata["model_id"], "station_id": "51201", "issued_at": issued,
               "target_date_hst": sample.target_date_hst.isoformat(), "feature_cutoff_date_hst": cutoff.isoformat(),
               "processing_run_id": job_id, "features_snapshot": dict(zip(FEATURE_NAMES, sample.features))}
        if task == "regression":
            if float(value) < 0:
                raise ValueError("negative wave height prediction")
            row.update(target_metric="WVHT", horizon_days=1, predicted_value=float(value))
        elif task == "classification":
            scheme_id = manifest.get("scheme_id")
            if not scheme_id:
                raise ValueError("classification scheme_id missing")
            probabilities = {str(label): float(prob) for label, prob in zip(model.classes_, model.predict_proba(matrix(samples))[0])}
            row.update(scheme_id=scheme_id, predicted_label=str(value), confidence=max(probabilities.values()),
                       class_probabilities=probabilities)
        else:
            transformed = model[:-1].transform(matrix(samples))[0]
            center = model[-1].cluster_centers_[int(value)]
            distance = sqrt(sum((a - b) ** 2 for a, b in zip(transformed, center)))
            row = {"model_id": metadata["model_id"], "station_id": "51201", "date_hst": cutoff.isoformat(),
                   "cluster_label": int(value), "distance_to_centroid": distance, "processing_run_id": job_id}
        gateway.save_gold_results(kind, [row], key)
        gateway.update_job(job_id, "success", 1)
        return {"processing_run_id": job_id, "status": "success", "saved": 1, "model_id": metadata["model_id"]}
    except Exception as exc:
        gateway.update_job(job_id, "failed", error=type(exc).__name__)
        raise
