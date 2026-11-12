"""
services/preprocessing/app/features/daily.py

Compute silver.daily_wave_features from Silver observations for one HST day.

Rules (from 03_NOAA_PIPELINE_QUALITY.md §6):
  - Aggregations are based on Pacific/Honolulu calendar date
  - circular mean for MWD using atan2(mean(sin), mean(cos)); None if no valid readings
    or vector length ≈ 0
  - coverage_ratio is None unless expected_sample_count is known with confidence
  - sample_count != valid_wave_count (sample_count counts ALL rows for that day)
  - Idempotent: rebuilding same day produces same result
"""
from __future__ import annotations

import math
import statistics
from datetime import date
from typing import Sequence

from packages.common.time import observed_at_to_date_hst
from packages.db.gateway import DailyFeaturesDTO, SilverObservationDTO

STATION_ID = "51201"
CALCULATION_VERSION = "v1"


def _percentile(data: list[float], p: float) -> float | None:
    """Return the p-th percentile (0–100) of *data*, or None if empty."""
    if not data:
        return None
    n = len(data)
    if n == 1:
        return data[0]
    # Linear interpolation
    sorted_data = sorted(data)
    index = (p / 100) * (n - 1)
    lower = int(index)
    upper = lower + 1
    frac = index - lower
    if upper >= n:
        return sorted_data[-1]
    return sorted_data[lower] * (1 - frac) + sorted_data[upper] * frac


def _circular_mean(degrees: Sequence[float | None]) -> float | None:
    """
    Circular mean of direction values (degrees true).
    Returns None if no valid readings or resultant vector length ≈ 0.
    """
    valid = [d for d in degrees if d is not None and not math.isnan(d)]
    if not valid:
        return None
    sin_sum = sum(math.sin(math.radians(d)) for d in valid)
    cos_sum = sum(math.cos(math.radians(d)) for d in valid)
    r = math.hypot(sin_sum, cos_sum)
    if r < 1e-9:
        return None
    angle_deg = math.degrees(math.atan2(sin_sum, cos_sum)) % 360
    return angle_deg


def compute_daily_features(
    date_hst: date,
    observations: Sequence[SilverObservationDTO],
    *,
    expected_sample_count: int | None = None,
) -> DailyFeaturesDTO:
    """
    Compute daily wave features for one Hawaii calendar day.

    Args:
        date_hst:              The Hawaii (HST) calendar date.
        observations:          All Silver observations for this day
                               (filtered upstream by HST date boundary).
        expected_sample_count: If known from station metadata; None = unknown.

    Returns:
        DailyFeaturesDTO ready for upsert.
    """
    sample_count = len(observations)
    wave_heights = [o.wave_height_m for o in observations if o.wave_height_m is not None]
    valid_wave_count = len(wave_heights)

    # Coverage ratio: only compute when expected is known
    coverage_ratio: float | None = None
    if expected_sample_count is not None and expected_sample_count > 0:
        coverage_ratio = valid_wave_count / expected_sample_count

    # Wave height statistics
    mean_wave = statistics.mean(wave_heights) if wave_heights else None
    median_wave = statistics.median(wave_heights) if wave_heights else None
    max_wave = max(wave_heights) if wave_heights else None
    min_wave = min(wave_heights) if wave_heights else None
    p95_wave = _percentile(wave_heights, 95)
    stddev_wave = statistics.stdev(wave_heights) if len(wave_heights) >= 2 else None

    # Dominant period
    periods = [o.dominant_period_s for o in observations if o.dominant_period_s is not None]
    mean_period = statistics.mean(periods) if periods else None

    # Circular mean wave direction (MWD)
    directions = [o.mean_wave_direction_deg for o in observations]
    circ_dir = _circular_mean(directions)

    # Sea surface temperature
    sst_values = [o.sea_surface_temp_c for o in observations if o.sea_surface_temp_c is not None]
    mean_sst = statistics.mean(sst_values) if sst_values else None

    # Max wind speed
    winds = [o.wind_speed_ms for o in observations if o.wind_speed_ms is not None]
    max_wind = max(winds) if winds else None

    # Good quality count
    good_quality_count = sum(
        1 for o in observations if o.quality_status in ("valid", "unverified")
    )

    return DailyFeaturesDTO(
        station_id=STATION_ID,
        date_hst=date_hst,
        sample_count=sample_count,
        valid_wave_count=valid_wave_count,
        expected_sample_count=expected_sample_count,
        coverage_ratio=coverage_ratio,
        mean_wave_height_m=mean_wave,
        median_wave_height_m=median_wave,
        max_wave_height_m=max_wave,
        min_wave_height_m=min_wave,
        p95_wave_height_m=p95_wave,
        stddev_wave_height_m=stddev_wave,
        mean_dominant_period_s=mean_period,
        circular_mean_direction_deg=circ_dir,
        mean_sea_surface_temp_c=mean_sst,
        max_wind_speed_ms=max_wind,
        good_quality_count=good_quality_count,
        calculation_version=CALCULATION_VERSION,
    )


def group_observations_by_hst_date(
    observations: Sequence[SilverObservationDTO],
) -> dict[date, list[SilverObservationDTO]]:
    """
    Group Silver observations by their Hawaii calendar date.
    Handles the HST midnight crossing (a UTC 2024-01-15T10:00Z is HST 2024-01-15T00:00).
    """
    groups: dict[date, list[SilverObservationDTO]] = {}
    for obs in observations:
        d = observed_at_to_date_hst(obs.observed_at)
        groups.setdefault(d, []).append(obs)
    return groups
