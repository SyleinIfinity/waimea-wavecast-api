"""Causal, provenance-preserving estimates for derived datasets only."""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from statistics import mean, median
from typing import Any, Protocol
from zoneinfo import ZoneInfo

from packages.ml.data_quality import GapSlot, _get, _utc

HST = ZoneInfo("Pacific/Honolulu")


class ImputationModel(Protocol):
    def predict(self, history: list[float]) -> float: ...


class TrailingMedianBaseline:
    """Transparent causal baseline; never consumes earlier estimates."""
    def predict(self, history: list[float]) -> float:
        return float(median(history[-3:]))


class LagRidgeImputer:
    """Small autoregressive model with lag, rolling mean and local trend."""
    def __init__(self, regressor):
        self.regressor = regressor

    @staticmethod
    def features(history: list[float]) -> list[float]:
        if len(history) < 3:
            raise ValueError("three observed lags required")
        recent = history[-3:]
        return [*recent, mean(recent), recent[-1]-recent[0]]

    def predict(self, history: list[float]) -> float:
        return float(self.regressor.predict([self.features(history)])[0])


def fit_lag_ridge(rows: list[Any], *, cadence_minutes: int, train_fraction: float,
                  min_train_samples: int, min_validation_samples: int,
                  alpha: float, version: str) -> tuple[LagRidgeImputer, "ValidationEvidence"]:
    """Fit on verified contiguous history, validate on later labels only."""
    from sklearn.linear_model import Ridge

    if cadence_minutes <= 0 or not 0 < train_fraction < 1 or alpha < 0 or not version or min_train_samples < 1 or min_validation_samples < 1:
        raise ValueError("invalid ridge training configuration")
    clean = sorted((_utc(_get(row,"observed_at")),float(_get(row,"wave_height_m")))
                   for row in rows if _get(row,"wave_height_m") is not None and
                   _get(row,"quality_status") == "valid")
    delta = timedelta(minutes=cadence_minutes)
    samples = []
    for i in range(3,len(clean)):
        window = clean[i-3:i+1]
        if any(b[0]-a[0] != delta for a,b in zip(window,window[1:])):
            continue
        samples.append((window[-1][0],LagRidgeImputer.features([value for _,value in window[:3]]),window[-1][1]))
    boundary = int(len(samples)*train_fraction)
    train,validation = samples[:boundary],samples[boundary:]
    if len(train) < min_train_samples or len(validation) < min_validation_samples:
        raise ValueError("insufficient verified chronological training data")
    regressor = Ridge(alpha=alpha)
    regressor.fit([features for _,features,_ in train],[target for _,_,target in train])
    predicted=regressor.predict([features for _,features,_ in validation])
    mae=mean(abs(target-prediction) for (_,_,target),prediction in zip(validation,predicted))
    evidence=ValidationEvidence("lag-ridge",version,validation[-1][0],train[-1][0],
                                float(mae),len(validation))
    return LagRidgeImputer(regressor),evidence


@dataclass(frozen=True)
class ValidationEvidence:
    model_name: str
    model_version: str
    validated_at: datetime
    trained_until: datetime
    validation_mae: float
    validation_samples: int


@dataclass(frozen=True)
class ImputationPolicy:
    minimum_history: int
    maximum_consecutive: int
    maximum_history_age: timedelta
    maximum_uncertainty: float
    minimum_validation_samples: int

    def __post_init__(self):
        if self.minimum_history < 3 or self.maximum_consecutive < 1 or self.maximum_history_age <= timedelta(0) or self.maximum_uncertainty < 0 or self.minimum_validation_samples < 1:
            raise ValueError("invalid imputation policy")


@dataclass(frozen=True)
class DerivedPoint:
    observation_time: datetime
    value: float | None
    source_type: str  # OBSERVED | IMPUTED | MISSING | INVALID | INSUFFICIENT_DATA
    imputation_method: str | None = None
    model_version: str | None = None
    estimated_at: datetime | None = None
    uncertainty: float | None = None
    quality_score: float | None = None
    provenance: tuple[dict, ...] = ()


def estimate_missing(slots: list[GapSlot], observations: list[Any], *,
                     as_of: datetime, model: ImputationModel,
                     evidence: ValidationEvidence, policy: ImputationPolicy) -> list[DerivedPoint]:
    """Estimate confirmed gaps from data available before each decision time.

    A trained model must have held-out validation evidence. Missing slots are
    never fed back as history. All output is a derived point, never a Silver row.
    """
    as_of = _utc(as_of)
    if _utc(evidence.trained_until) > as_of or _utc(evidence.validated_at) > as_of:
        raise ValueError("model evidence comes from the future")
    if evidence.validation_samples < policy.minimum_validation_samples or evidence.validation_mae > policy.maximum_uncertainty:
        return [DerivedPoint(slot.observation_time, None, "INSUFFICIENT_DATA") for slot in slots]
    ordered = sorted(observations, key=lambda row: _utc(_get(row, "observed_at")))
    result = []
    run = 0
    for slot in sorted(slots, key=lambda value: value.observation_time):
        if slot.status != "MISSING":
            run = 0
            result.append(DerivedPoint(slot.observation_time, None, "MISSING"))
            continue
        run += 1
        target = _utc(slot.observation_time)
        if target > as_of:
            result.append(DerivedPoint(target, None, "MISSING"))
            continue
        if _utc(evidence.trained_until) > target or _utc(evidence.validated_at) > target:
            result.append(DerivedPoint(target, None, "INSUFFICIENT_DATA"))
            continue
        if run > policy.maximum_consecutive:
            result.append(DerivedPoint(target, None, "INSUFFICIENT_DATA"))
            continue
        # first_seen_at is the availability clock when supplied. Values
        # collected later cannot leak into retrospective decisions.
        history_rows = [row for row in ordered if _utc(_get(row, "observed_at")) < target
                        and _get(row, "wave_height_m") is not None
                        and _get(row, "quality_status", "unverified") == "valid"
                        and (_get(row, "first_seen_at") is None or _utc(_get(row, "first_seen_at")) <= target)]
        history_rows = [row for row in history_rows if target - _utc(_get(row, "observed_at")) <= policy.maximum_history_age]
        if len(history_rows) < policy.minimum_history:
            result.append(DerivedPoint(target, None, "INSUFFICIENT_DATA"))
            continue
        value = float(model.predict([float(_get(row, "wave_height_m")) for row in history_rows]))
        if value < 0:
            result.append(DerivedPoint(target, None, "INVALID"))
            continue
        uncertainty = evidence.validation_mae
        result.append(DerivedPoint(target, value, "IMPUTED", evidence.model_name,
                                   evidence.model_version, as_of, uncertainty,
                                   max(0.0, 1.0 - uncertainty / policy.maximum_uncertainty) if policy.maximum_uncertainty else 1.0,
                                   ({"validated_at": _utc(evidence.validated_at).isoformat(),
                                     "training_cutoff": _utc(evidence.trained_until).isoformat(),
                                     "history_count": len(history_rows)},)))
    return result


@dataclass(frozen=True)
class Reconciliation:
    points: tuple[DerivedPoint, ...]
    affected_hst_days: tuple[str, ...]
    affected_hst_weeks: tuple[str, ...]
    affected_hst_months: tuple[str, ...]
    affected_hst_seasons: tuple[str, ...]


def reconcile_observed(points: list[DerivedPoint], observed: list[Any]) -> Reconciliation:
    """Replace estimates in a derived view, retaining the old value as history."""
    incoming = {_utc(_get(row, "observed_at")): row for row in observed
                if _get(row, "wave_height_m") is not None and
                _get(row, "quality_status", "unverified") == "valid"}
    changed = []
    output = []
    for point in points:
        row = incoming.get(_utc(point.observation_time))
        if row is None:
            output.append(point)
            continue
        if point.source_type != "OBSERVED" or point.value != float(_get(row, "wave_height_m")):
            changed.append(point.observation_time.astimezone(HST).date())
            previous = {"source_type": point.source_type, "value": point.value,
                        "model_version": point.model_version,
                        "estimated_at": point.estimated_at.isoformat() if point.estimated_at else None}
            output.append(DerivedPoint(point.observation_time, float(_get(row, "wave_height_m")),
                                       "OBSERVED", provenance=(*point.provenance, previous)))
        else:
            output.append(point)
    weeks = {str(day - timedelta(days=day.weekday())) for day in changed}
    months = {day.strftime("%Y-%m") for day in changed}
    seasons = {f"{day.year}-{(day.month-1)//3+1}" for day in changed}
    return Reconciliation(tuple(output), tuple(sorted(map(str, changed))),
                          tuple(sorted(weeks)), tuple(sorted(months)), tuple(sorted(seasons)))


def save_derived_dataset(points: list[DerivedPoint], *, client, bucket: str,
                         version: str,
                         prefix: str = "datasets/processed/tl07-a") -> dict:
    """Store versioned derived JSON in GCS; never mutate NOAA raw objects."""
    if not version or "/" in version or ".." in version:
        raise ValueError("invalid version")
    if prefix not in ("datasets/processed/tl07-a", "test/tl07a/derived"):
        raise ValueError("unapproved derived dataset prefix")
    data = json.dumps([asdict(point) for point in points], default=str, sort_keys=True).encode()
    digest = hashlib.sha256(data).hexdigest()
    path = f"{prefix}/{version}/{digest}.json"
    info = client.upload_if_absent(bucket, path, data, "application/json",
                                   {"content_sha256": digest, "source_type": "derived"})
    return {"gcs_uri": info.gcs_uri, "generation": info.generation,
            "sha256": digest, "record_count": len(points)}
