"""Leakage-safe daily features for next-HST-day wave-height prediction."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from math import cos, pi, radians, sin
from typing import Any, Iterable
from zoneinfo import ZoneInfo

FEATURE_NAMES = tuple(
    f"wave_height_median_lag{lag}" for lag in range(7)
) + tuple(f"dominant_period_lag{lag}" for lag in range(7)) + (
    "direction_sin", "direction_cos", "day_of_year_sin", "day_of_year_cos",
    "valid_wave_count", "coverage_ratio",
)


@dataclass(frozen=True)
class Sample:
    anchor_date_hst: date
    target_date_hst: date
    features: tuple[float | None, ...]
    target: float | None


def _get(row: Any, name: str) -> Any:
    return row.get(name) if isinstance(row, dict) else getattr(row, name)


def _day(row: Any) -> date:
    value = _get(row, "date_hst")
    return date.fromisoformat(value) if isinstance(value, str) else value


def build_samples(rows: Iterable[Any], *, require_target: bool = True) -> list[Sample]:
    """Use only dates <= anchor for X; never bridge missing calendar days."""
    def observed_only(row: Any) -> bool:
        if row is None:
            return False
        source = _get(row, "source_type") if (isinstance(row, dict) and "source_type" in row) or hasattr(row, "source_type") else "OBSERVED"
        imputed_count = _get(row, "imputed_count") if (isinstance(row, dict) and "imputed_count" in row) or hasattr(row, "imputed_count") else 0
        return source == "OBSERVED" and not imputed_count

    days = {_day(row): row for row in rows}
    samples: list[Sample] = []
    for anchor in sorted(days):
        history = [days.get(anchor - timedelta(days=lag)) for lag in range(7)]
        if any(not observed_only(row) or _get(row, "median_wave_height_m") is None for row in history):
            continue
        cutoff = datetime.combine(anchor + timedelta(days=1), time(hour=2),
                                  ZoneInfo("Pacific/Honolulu")).astimezone(timezone.utc)
        for row in history:
            updated = _get(row, "source_data_max_updated_at") if isinstance(row, dict) and "source_data_max_updated_at" in row else getattr(row, "source_data_max_updated_at", None)
            if updated is not None:
                updated = datetime.fromisoformat(updated.replace("Z", "+00:00")) if isinstance(updated, str) else updated
                if updated.tzinfo is None or updated > cutoff:
                    raise ValueError("feature source updated after prediction cutoff")
        target_day = anchor + timedelta(days=1)
        target_row = days.get(target_day)
        target = None if not observed_only(target_row) else _get(target_row, "median_wave_height_m")
        if require_target and target is None:
            continue
        direction = _get(history[0], "circular_mean_direction_deg")
        angle = None if direction is None else radians(direction)
        doy = anchor.timetuple().tm_yday
        features = (
            *(float(_get(row, "median_wave_height_m")) for row in history),
            *(_get(row, "mean_dominant_period_s") for row in history),
            None if angle is None else sin(angle),
            None if angle is None else cos(angle),
            sin(2 * pi * doy / 365.25), cos(2 * pi * doy / 365.25),
            _get(history[0], "valid_wave_count"),
            _get(history[0], "coverage_ratio"),
        )
        samples.append(Sample(anchor, target_day, tuple(features), target))
    return samples


def chronological_split(samples: list[Sample], *, train_fraction: float = .7,
                        validation_fraction: float = .15, gap: int = 1
                        ) -> tuple[list[Sample], list[Sample], list[Sample]]:
    """Keep future labels out of earlier folds using a calendar embargo."""
    if not 0 < train_fraction < 1 or not 0 < validation_fraction < 1 or train_fraction + validation_fraction >= 1:
        raise ValueError("invalid split fractions")
    if gap < 1:
        raise ValueError("one-day target requires at least one-day gap")
    ordered = sorted(samples, key=lambda row: row.anchor_date_hst)
    if len({row.anchor_date_hst for row in ordered}) != len(ordered):
        raise ValueError("duplicate anchor date")
    n = len(ordered)
    first = int(n * train_fraction)
    second = int(n * (train_fraction + validation_fraction))
    train = ordered[:first]
    validation = ordered[first + gap:second]
    test = ordered[second + gap:]
    if min(map(len, (train, validation, test))) < 2:
        raise ValueError("insufficient samples after chronological split")
    if train[-1].target_date_hst >= validation[0].anchor_date_hst or validation[-1].target_date_hst >= test[0].anchor_date_hst:
        raise ValueError("target leakage across split")
    return train, validation, test


def matrix(samples: list[Sample]) -> list[list[float | None]]:
    return [list(sample.features) for sample in samples]
