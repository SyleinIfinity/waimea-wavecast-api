"""Causal NOAA cadence, gap and quality decisions for derived datasets.

Silver rows remain observed source data. This module never writes to Silver.
All time inputs are UTC-aware and all operating thresholds are explicit.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from math import isfinite
from statistics import median
from typing import Any, Iterable


def _get(row: Any, key: str, default=None):
    return row.get(key, default) if isinstance(row, dict) else getattr(row, key, default)


def _utc(value: datetime | str) -> datetime:
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if value.tzinfo is None:
        raise ValueError("timestamps must be timezone-aware")
    return value.astimezone(timezone.utc)


@dataclass(frozen=True)
class Cadence:
    minutes: int
    support_ratio: float
    interval_count: int


@dataclass(frozen=True)
class GapSlot:
    observation_time: datetime
    status: str  # PENDING_PUBLICATION | MISSING
    cadence_minutes: int


@dataclass(frozen=True)
class GapResult:
    cadence: Cadence | None
    slots: tuple[GapSlot, ...]
    duplicate_timestamps: int
    irregular_intervals: int
    late_observations: int
    corrected_observations: int
    invalid_observations: int
    status: str  # OBSERVED | INSUFFICIENT_DATA


@dataclass(frozen=True)
class ObservationState:
    observation_time: datetime
    source_type: str  # OBSERVED | INVALID
    flags: tuple[str, ...]  # DUPLICATE | LATE | CORRECTED | STALE


def classify_observations(rows: Iterable[Any], *, as_of: datetime,
                          publication_wait: timedelta,
                          freshness_limit: timedelta) -> list[ObservationState]:
    """Describe observed rows without rewriting NOAA source or Silver status."""
    as_of = _utc(as_of)
    if publication_wait <= timedelta(0) or freshness_limit <= timedelta(0):
        raise ValueError("positive time limits required")
    rows = list(rows)
    seen = set()
    latest = max((_utc(_get(row,"observed_at")) for row in rows),default=None)
    result = []
    for row in sorted(rows,key=lambda item:_utc(_get(item,"observed_at"))):
        when = _utc(_get(row,"observed_at"))
        value = _get(row,"wave_height_m")
        invalid = value is None or _get(row,"quality_status") in ("rejected", "suspect", "partial") or not isfinite(float(value))
        flags = []
        if when in seen: flags.append("DUPLICATE")
        if _get(row,"first_seen_at") is not None and _utc(_get(row,"first_seen_at")) > when + publication_wait:
            flags.append("LATE")
        if (_get(row,"revision_no") or 1) > 1: flags.append("CORRECTED")
        if latest == when and as_of - when > freshness_limit: flags.append("STALE")
        result.append(ObservationState(when,"INVALID" if invalid else "OBSERVED",tuple(flags)))
        seen.add(when)
    return result


def timeline_statuses(rows: Iterable[Any], gaps: GapResult) -> list[str]:
    """Interleave unique observations and inferred slots for quality gating."""
    states = {}
    for row in rows:
        when = _utc(_get(row,"observed_at"))
        value = _get(row,"wave_height_m")
        states[when] = ("INVALID" if value is None or _get(row,"quality_status") in ("rejected", "suspect", "partial")
                        else "UNVERIFIED" if _get(row,"quality_status") == "unverified"
                        else "OBSERVED")
    for slot in gaps.slots:
        states.setdefault(slot.observation_time,slot.status)
    return [states[when] for when in sorted(states)]


def infer_cadence(rows: Iterable[Any], *, min_intervals: int,
                  min_support_ratio: float) -> Cadence | None:
    """Infer the modal positive interval; refuse a weak/short reporting pattern."""
    if min_intervals < 1 or not 0 < min_support_ratio <= 1:
        raise ValueError("invalid cadence evidence thresholds")
    times = sorted({_utc(_get(row, "observed_at")) for row in rows})
    diffs = [int((b-a).total_seconds() // 60) for a, b in zip(times, times[1:])
             if (b-a).total_seconds() > 0 and (b-a).total_seconds() % 60 == 0]
    if len(diffs) < min_intervals:
        return None
    minute, count = Counter(diffs).most_common(1)[0]
    support = count / len(diffs)
    return Cadence(minute, support, len(diffs)) if support >= min_support_ratio else None


def detect_gaps(rows: Iterable[Any], *, as_of: datetime, publication_wait: timedelta,
                min_intervals: int, min_support_ratio: float,
                max_trailing_slots: int = 48,
                max_internal_gap_slots: int = 48) -> GapResult:
    """Classify expected slots only when the feed's observed cadence is strong.

    The last slot is pending until the publication wait has elapsed. Long or
    non-multiple intervals are marked irregular and never fabricated as gaps.
    """
    as_of = _utc(as_of)
    if publication_wait <= timedelta(0) or max_trailing_slots < 0 or max_internal_gap_slots < 0:
        raise ValueError("positive publication wait and nonnegative cap required")
    rows = list(rows)
    times = sorted({_utc(_get(row, "observed_at")) for row in rows})
    duplicates = len(rows) - len(times)
    cadence = infer_cadence(rows, min_intervals=min_intervals,
                            min_support_ratio=min_support_ratio)
    late = sum(1 for row in rows if _get(row, "first_seen_at") is not None and
               _utc(_get(row, "first_seen_at")) > _utc(_get(row, "observed_at")) + publication_wait)
    corrected = sum(1 for row in rows if (_get(row, "revision_no") or 1) > 1)
    invalid = sum(1 for row in rows if _get(row, "quality_status") in ("rejected", "suspect", "partial") or
                  _get(row, "wave_height_m") is None or
                  (isinstance(_get(row, "wave_height_m"), (int, float)) and
                   not isfinite(float(_get(row, "wave_height_m")))))
    if cadence is None:
        return GapResult(None, (), duplicates, 0, late, corrected, invalid, "INSUFFICIENT_DATA")
    interval = timedelta(minutes=cadence.minutes)
    slots: list[GapSlot] = []
    irregular = 0
    def append_slot(when: datetime) -> None:
        status = "MISSING" if as_of >= when + publication_wait else "PENDING_PUBLICATION"
        slots.append(GapSlot(when, status, cadence.minutes))
    for left, right in zip(times, times[1:]):
        span = (right-left).total_seconds() / interval.total_seconds()
        count = round(span)
        if count < 1 or abs(span-count) > .05 or count > max_internal_gap_slots + 1:
            irregular += 1
            continue
        for offset in range(1, count):
            append_slot(left + interval * offset)
    trailing_truncated = False
    if times and max_trailing_slots:
        expected_trailing = max(0, int((as_of-times[-1]) // interval))
        trailing_truncated = expected_trailing > max_trailing_slots
        trailing = min(expected_trailing, max_trailing_slots)
        for offset in range(1, trailing + 1):
            append_slot(times[-1] + interval * offset)
    return GapResult(cadence, tuple(slots), duplicates, irregular, late,
                     corrected, invalid,
                     "INSUFFICIENT_DATA" if irregular or trailing_truncated else "OBSERVED")


@dataclass(frozen=True)
class QualityThresholds:
    max_missing_ratio: float
    max_imputed_ratio: float
    max_unverified_ratio: float
    max_consecutive_missing: int
    max_freshness: timedelta
    max_uncertainty: float
    min_historical_coverage: float
    min_observed_samples: int

    def __post_init__(self):
        if any(not 0 <= value <= 1 for value in (self.max_missing_ratio,
                self.max_imputed_ratio, self.max_unverified_ratio,
                self.min_historical_coverage)):
            raise ValueError("ratios must be in [0,1]")
        if self.max_consecutive_missing < 0 or self.max_freshness <= timedelta(0) or self.max_uncertainty < 0 or self.min_observed_samples < 1:
            raise ValueError("invalid quality thresholds")


@dataclass(frozen=True)
class QualityDecision:
    status: str
    reasons: tuple[str, ...]
    observed_count: int
    missing_ratio: float
    imputed_ratio: float
    unverified_ratio: float
    historical_coverage: float


def quality_gate(statuses: Iterable[str], *, latest_observed_at: datetime | None,
                 as_of: datetime, uncertainty: float | None,
                 thresholds: QualityThresholds) -> QualityDecision:
    """Fail closed when coverage, freshness or uncertainty cannot be proven."""
    items = list(statuses)
    total = len(items)
    observed = sum(value in ("OBSERVED", "UNVERIFIED") for value in items)
    missing = sum(value in ("MISSING", "INVALID", "STALE") for value in items)
    imputed = sum(value == "IMPUTED" for value in items)
    unverified = sum(value == "UNVERIFIED" for value in items)
    miss_ratio = missing / total if total else 1.0
    imp_ratio = imputed / total if total else 0.0
    unverified_ratio = unverified / total if total else 0.0
    coverage = observed / total if total else 0.0
    run = max_run = 0
    for value in items:
        run = run + 1 if value in ("MISSING", "INVALID", "STALE") else 0
        max_run = max(max_run, run)
    reasons = []
    if observed < thresholds.min_observed_samples: reasons.append("too_few_observations")
    if miss_ratio > thresholds.max_missing_ratio: reasons.append("missing_ratio")
    if imp_ratio > thresholds.max_imputed_ratio: reasons.append("imputed_ratio")
    if unverified_ratio > thresholds.max_unverified_ratio: reasons.append("unverified_ratio")
    if max_run > thresholds.max_consecutive_missing: reasons.append("consecutive_missing")
    if coverage < thresholds.min_historical_coverage: reasons.append("historical_coverage")
    if latest_observed_at is None or not timedelta(0) <= _utc(as_of) - _utc(latest_observed_at) <= thresholds.max_freshness:
        reasons.append("stale_or_unknown_freshness")
    if uncertainty is None or uncertainty > thresholds.max_uncertainty or not isfinite(uncertainty):
        reasons.append("uncertainty_unverified")
    return QualityDecision("OBSERVED" if not reasons else "INSUFFICIENT_DATA",
                           tuple(reasons), observed, miss_ratio, imp_ratio,
                           unverified_ratio, coverage)
