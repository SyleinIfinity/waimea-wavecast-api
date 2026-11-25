"""Explicit horizon contracts and leakage-safe persistence benchmarks.

This module does not publish predictions. A benchmark with one 45-day window
is exploratory, never evidence that seasonal/yearly forecasts are ready.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from math import sqrt
from statistics import mean
from typing import Any

from packages.ml.data_quality import _get, _utc
from packages.ml.metrics import classification

HORIZONS = {
    "30min": timedelta(minutes=30), "1h": timedelta(hours=1),
    "6h": timedelta(hours=6), "24h": timedelta(hours=24),
    "7d": timedelta(days=7), "30d": timedelta(days=30),
}
OUTLOOK_HORIZONS = ("season", "cycle", "year")


@dataclass(frozen=True)
class HorizonEvidence:
    horizon: str
    status: str  # EXPERIMENTAL | NOT_READY
    train_pairs: int
    test_pairs: int
    persistence_mae: float | None
    persistence_rmse: float | None
    train_mean_mae: float | None
    skill_vs_train_mean: float | None
    interval_coverage: float | None
    interval_half_width: float | None
    reason: str | None = None


@dataclass(frozen=True)
class Forecast:
    forecast_horizon: str
    target_timestamp: datetime | None
    target_period: str | None
    prediction_type: str
    predicted_value: float | None
    predicted_distribution: dict | None
    prediction_interval: tuple[float, float] | None
    model_name: str
    model_version: str
    feature_snapshot: dict
    quality_status: str
    generated_at: datetime


def _metrics(actual: list[float], predicted: list[float]) -> tuple[float, float]:
    errors = [a-p for a,p in zip(actual,predicted)]
    return mean(abs(e) for e in errors), sqrt(mean(e*e for e in errors))


def evaluate_persistence(rows: list[Any], horizon: str, *, split_fraction: float,
                         min_train_pairs: int, min_test_pairs: int,
                         test_end_fraction: float = 1.0) -> HorizonEvidence:
    """Use train-only targets for the mean baseline and interval calibration.

    Train labels must precede the split; test anchors must follow it. This
    embargo prevents target leakage across the chronological boundary.
    """
    if horizon in OUTLOOK_HORIZONS:
        return HorizonEvidence(horizon, "NOT_READY", 0, 0, None, None, None, None,
                               None, None, "multi-year climatology unavailable")
    if horizon not in HORIZONS or not 0 < split_fraction < test_end_fraction <= 1 or min_train_pairs < 1 or min_test_pairs < 1:
        raise ValueError("invalid horizon benchmark settings")
    values = {_utc(_get(row,"observed_at")): float(_get(row,"wave_height_m")) for row in rows
              if _get(row,"wave_height_m") is not None and
              _get(row,"quality_status","unverified") == "valid"}
    times = sorted(values)
    if len(times) < min_train_pairs + min_test_pairs:
        return HorizonEvidence(horizon,"NOT_READY",0,0,None,None,None,None,None,None,"too few observations")
    boundary = times[int(len(times)*split_fraction)]
    test_end = times[min(len(times)-1,int(len(times)*test_end_fraction))] if test_end_fraction < 1 else None
    delta = HORIZONS[horizon]
    pairs = [(anchor,values[anchor],values[anchor+delta]) for anchor in times
             if anchor+delta in values]
    train = [(x,y) for anchor,x,y in pairs if anchor+delta < boundary]
    test = [(x,y) for anchor,x,y in pairs if anchor >= boundary and (test_end is None or anchor < test_end)]
    if len(train) < min_train_pairs or len(test) < min_test_pairs:
        return HorizonEvidence(horizon,"NOT_READY",len(train),len(test),None,None,None,None,None,None,"insufficient chronological folds")
    train_mean = mean(y for _,y in train)
    residuals = sorted(abs(y-x) for x,y in train)
    width = residuals[min(len(residuals)-1,int(.9*(len(residuals)-1)))]
    actual = [y for x,y in test]
    persistence = [x for x,y in test]
    mae,rmse = _metrics(actual,persistence)
    baseline_mae,_ = _metrics(actual,[train_mean]*len(test))
    skill = None if baseline_mae == 0 else 1-mae/baseline_mae
    coverage = mean(abs(y-x) <= width for x,y in test)
    # A single holdout period cannot establish seasonal robustness.
    return HorizonEvidence(horizon,"EXPERIMENTAL",len(train),len(test),mae,rmse,
                           baseline_mae,skill,coverage,width)


def walk_forward_persistence(rows: list[Any], horizon: str, *, first_train_fraction: float,
                             folds: int, min_train_pairs: int,
                             min_test_pairs: int) -> list[HorizonEvidence]:
    """Expanding train window and disjoint chronological test windows."""
    if folds < 2 or not 0 < first_train_fraction < 1:
        raise ValueError("invalid walk-forward windows")
    step = (1-first_train_fraction)/folds
    return [evaluate_persistence(rows,horizon,
                                 split_fraction=first_train_fraction+step*i,
                                 test_end_fraction=first_train_fraction+step*(i+1),
                                 min_train_pairs=min_train_pairs,
                                 min_test_pairs=min_test_pairs)
            for i in range(folds)]


def evaluate_regime_persistence(rows: list[Any], horizon: str, *,
                                split_fraction: float,
                                min_train_pairs: int,
                                min_test_pairs: int) -> dict:
    """Train-only tercile labels; classify test target by anchor's regime."""
    if horizon not in HORIZONS or not 0 < split_fraction < 1:
        raise ValueError("unsupported classification horizon")
    values = {_utc(_get(row,"observed_at")):float(_get(row,"wave_height_m")) for row in rows
              if _get(row,"wave_height_m") is not None and
              _get(row,"quality_status","unverified") == "valid"}
    times = sorted(values)
    if len(times) < min_train_pairs + min_test_pairs:
        return {"status":"NOT_READY","reason":"too few observations"}
    boundary = times[int(len(times)*split_fraction)]
    delta = HORIZONS[horizon]
    pairs = [(anchor,values[anchor],values[anchor+delta]) for anchor in times if anchor+delta in values]
    train_targets = sorted(y for anchor,x,y in pairs if anchor+delta < boundary)
    test = [(x,y) for anchor,x,y in pairs if anchor >= boundary]
    if len(train_targets) < min_train_pairs or len(test) < min_test_pairs:
        return {"status":"NOT_READY","reason":"insufficient chronological folds"}
    lower = train_targets[int((len(train_targets)-1)/3)]
    upper = train_targets[int(2*(len(train_targets)-1)/3)]
    if lower >= upper:
        return {"status":"NOT_READY","reason":"degenerate train-only labels"}
    def label(value: float) -> str:
        return "low" if value < lower else "medium" if value < upper else "high"
    labels=["low","medium","high"]
    train_labels=[label(y) for y in train_targets]
    majority=max(labels,key=lambda item:train_labels.count(item))
    actual_labels=[label(y) for x,y in test]
    score=classification(actual_labels,[label(x) for x,y in test],labels)
    majority_score=classification(actual_labels,[majority]*len(test),labels)
    return {"status":"EXPERIMENTAL","horizon":horizon,"train_pairs":len(train_targets),
            "test_pairs":len(test),"train_only_boundaries_m":[lower,upper],
            "accuracy":score["accuracy"],"f1_macro":score["f1_macro"],
            "train_majority_accuracy":majority_score["accuracy"],
            "test_class_counts":{item:actual_labels.count(item) for item in labels}}


def baseline_forecast(rows: list[Any], horizon: str, evidence: HorizonEvidence,
                      *, as_of: datetime, maximum_freshness: timedelta,
                      minimum_skill: float, minimum_interval_coverage: float,
                      model_version: str, allow_unverified: bool = False) -> Forecast:
    """Return an experimental estimate only if evidence and current data pass."""
    as_of = _utc(as_of)
    if horizon not in HORIZONS and horizon not in OUTLOOK_HORIZONS:
        raise ValueError("unknown horizon")
    if not 0 <= minimum_interval_coverage <= 1:
        raise ValueError("interval coverage threshold must be in [0,1]")
    observed = sorted(((_utc(_get(row,"observed_at")),float(_get(row,"wave_height_m")))
                       for row in rows if _get(row,"wave_height_m") is not None and
                       (_get(row,"quality_status","unverified") == "valid" or
                        (allow_unverified and _get(row,"quality_status","unverified") == "unverified"))
                       and (_get(row,"first_seen_at") is None or _utc(_get(row,"first_seen_at")) <= as_of)),
                      key=lambda value:value[0])
    ready = (horizon in HORIZONS and evidence.horizon == horizon and
             evidence.status == "EXPERIMENTAL" and evidence.skill_vs_train_mean is not None and
             evidence.skill_vs_train_mean >= minimum_skill and bool(observed) and
             evidence.interval_coverage is not None and
             evidence.interval_coverage >= minimum_interval_coverage and
             timedelta(0) <= as_of-observed[-1][0] <= maximum_freshness and
             observed[-1][0] + HORIZONS[horizon] > as_of)
    value = observed[-1][1] if ready else None
    target = observed[-1][0]+HORIZONS[horizon] if ready else None
    width = evidence.interval_half_width if ready else None
    return Forecast(horizon,target,None,"point" if horizon in HORIZONS else "outlook",
                    value,None,(max(0,value-width),value+width) if value is not None and width is not None else None,
                    "persistence",model_version,
                    {"last_observed_at":observed[-1][0].isoformat(),"last_observed_value":observed[-1][1]} if observed else {},
                    "EXPERIMENTAL" if ready else "INSUFFICIENT_DATA",as_of)
