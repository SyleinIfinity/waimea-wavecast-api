"""
tests/unit/test_daily_features.py

Unit tests for preprocessing daily wave features computation.

Covers:
  - Basic statistics (mean, median, max, p95, stddev)
  - Circular mean direction (atan2 method)
  - HST midnight crossing — observations near UTC 10:00 land on correct HST date
  - All-NULL day (no valid wave heights)
  - coverage_ratio is None when expected_sample_count is not given
  - Idempotent: same input → same output
"""
from __future__ import annotations

import math
from datetime import date, datetime, timezone

import pytest

from packages.db.gateway import SilverObservationDTO
from services.preprocessing.app.features.daily import (
    compute_daily_features,
    group_observations_by_hst_date,
)


def _make_obs(
    observed_at: datetime,
    wave_height_m: float | None = 1.0,
    mean_wave_direction_deg: float | None = 90.0,
    dominant_period_s: float | None = 10.0,
    sea_surface_temp_c: float | None = 25.0,
    wind_speed_ms: float | None = 5.0,
    quality_status: str = "valid",
) -> SilverObservationDTO:
    return SilverObservationDTO(
        station_id="51201",
        observed_at=observed_at,
        source_kind="historical_annual",
        source_dataset="test",
        source_file_id=1,
        raw_id=None,
        measurement_sha256="a" * 64,
        quality_status=quality_status,
        wave_height_m=wave_height_m,
        mean_wave_direction_deg=mean_wave_direction_deg,
        dominant_period_s=dominant_period_s,
        sea_surface_temp_c=sea_surface_temp_c,
        wind_speed_ms=wind_speed_ms,
    )


class TestBasicStats:
    def test_mean_wave_height(self):
        obs = [
            _make_obs(datetime(2024, 1, 15, 12, 0, tzinfo=timezone.utc), wave_height_m=1.0),
            _make_obs(datetime(2024, 1, 15, 14, 0, tzinfo=timezone.utc), wave_height_m=3.0),
        ]
        dto = compute_daily_features(date(2024, 1, 15), obs)
        assert dto.mean_wave_height_m == pytest.approx(2.0)

    def test_median_wave_height(self):
        obs = [
            _make_obs(datetime(2024, 1, 15, 10, 0, tzinfo=timezone.utc), wave_height_m=1.0),
            _make_obs(datetime(2024, 1, 15, 12, 0, tzinfo=timezone.utc), wave_height_m=2.0),
            _make_obs(datetime(2024, 1, 15, 14, 0, tzinfo=timezone.utc), wave_height_m=3.0),
        ]
        dto = compute_daily_features(date(2024, 1, 15), obs)
        assert dto.median_wave_height_m == pytest.approx(2.0)

    def test_max_min(self):
        obs = [
            _make_obs(datetime(2024, 1, 15, 10, 0, tzinfo=timezone.utc), wave_height_m=0.5),
            _make_obs(datetime(2024, 1, 15, 12, 0, tzinfo=timezone.utc), wave_height_m=4.0),
        ]
        dto = compute_daily_features(date(2024, 1, 15), obs)
        assert dto.max_wave_height_m == pytest.approx(4.0)
        assert dto.min_wave_height_m == pytest.approx(0.5)

    def test_valid_wave_count_excludes_none(self):
        obs = [
            _make_obs(datetime(2024, 1, 15, 10, 0, tzinfo=timezone.utc), wave_height_m=None),
            _make_obs(datetime(2024, 1, 15, 12, 0, tzinfo=timezone.utc), wave_height_m=1.5),
        ]
        dto = compute_daily_features(date(2024, 1, 15), obs)
        assert dto.sample_count == 2
        assert dto.valid_wave_count == 1

    def test_all_none_wave_heights(self):
        obs = [
            _make_obs(datetime(2024, 1, 15, 10, 0, tzinfo=timezone.utc), wave_height_m=None),
        ]
        dto = compute_daily_features(date(2024, 1, 15), obs)
        assert dto.mean_wave_height_m is None
        assert dto.valid_wave_count == 0

    def test_empty_observations(self):
        dto = compute_daily_features(date(2024, 1, 15), [])
        assert dto.sample_count == 0
        assert dto.valid_wave_count == 0
        assert dto.mean_wave_height_m is None


class TestCircularMeanDirection:
    def test_simple_direction(self):
        obs = [
            _make_obs(datetime(2024, 1, 15, 10, 0, tzinfo=timezone.utc), mean_wave_direction_deg=90.0),
            _make_obs(datetime(2024, 1, 15, 12, 0, tzinfo=timezone.utc), mean_wave_direction_deg=90.0),
        ]
        dto = compute_daily_features(date(2024, 1, 15), obs)
        assert dto.circular_mean_direction_deg == pytest.approx(90.0, abs=0.1)

    def test_direction_wrapping_359_1(self):
        # 359° and 1° → mean should be ~0° (or ~360°)
        obs = [
            _make_obs(datetime(2024, 1, 15, 10, 0, tzinfo=timezone.utc), mean_wave_direction_deg=359.0),
            _make_obs(datetime(2024, 1, 15, 12, 0, tzinfo=timezone.utc), mean_wave_direction_deg=1.0),
        ]
        dto = compute_daily_features(date(2024, 1, 15), obs)
        # Circular mean of 359° and 1° → 0° (or 360°, treated as equivalent)
        angle = dto.circular_mean_direction_deg
        assert angle is not None
        assert abs(angle % 360) < 1.0 or abs(angle % 360 - 360) < 1.0

    def test_opposing_directions_returns_none(self):
        # 0° and 180° cancel out — vector length ≈ 0 → None
        obs = [
            _make_obs(datetime(2024, 1, 15, 10, 0, tzinfo=timezone.utc), mean_wave_direction_deg=0.0),
            _make_obs(datetime(2024, 1, 15, 12, 0, tzinfo=timezone.utc), mean_wave_direction_deg=180.0),
        ]
        dto = compute_daily_features(date(2024, 1, 15), obs)
        assert dto.circular_mean_direction_deg is None

    def test_all_direction_none(self):
        obs = [
            _make_obs(datetime(2024, 1, 15, 10, 0, tzinfo=timezone.utc), mean_wave_direction_deg=None),
        ]
        dto = compute_daily_features(date(2024, 1, 15), obs)
        assert dto.circular_mean_direction_deg is None


class TestHSTMidnightCrossing:
    def test_obs_at_utc_10_belongs_to_hst_day(self):
        """
        UTC 2024-01-15T10:00:00Z = HST 2024-01-15T00:00:00
        Should land on HST date 2024-01-15, not 2024-01-14.
        """
        obs_at_utc_10 = datetime(2024, 1, 15, 10, 0, 0, tzinfo=timezone.utc)
        obs = [_make_obs(obs_at_utc_10)]
        grouped = group_observations_by_hst_date(obs)
        assert date(2024, 1, 15) in grouped
        assert date(2024, 1, 14) not in grouped

    def test_obs_at_utc_09_59_belongs_to_previous_hst_day(self):
        """
        UTC 2024-01-15T09:59:00Z = HST 2024-01-14T23:59:00
        Should land on HST date 2024-01-14.
        """
        obs_before_midnight_hst = datetime(2024, 1, 15, 9, 59, 0, tzinfo=timezone.utc)
        obs = [_make_obs(obs_before_midnight_hst)]
        grouped = group_observations_by_hst_date(obs)
        assert date(2024, 1, 14) in grouped
        assert date(2024, 1, 15) not in grouped


class TestCoverageRatio:
    def test_coverage_ratio_none_when_expected_unknown(self):
        obs = [_make_obs(datetime(2024, 1, 15, 10, 0, tzinfo=timezone.utc))]
        dto = compute_daily_features(date(2024, 1, 15), obs, expected_sample_count=None)
        assert dto.coverage_ratio is None

    def test_coverage_ratio_computed_when_expected_given(self):
        obs = [
            _make_obs(datetime(2024, 1, 15, 10, 0, tzinfo=timezone.utc)),
            _make_obs(datetime(2024, 1, 15, 12, 0, tzinfo=timezone.utc)),
        ]
        dto = compute_daily_features(date(2024, 1, 15), obs, expected_sample_count=48)
        assert dto.coverage_ratio == pytest.approx(2 / 48)


class TestIdempotency:
    def test_same_input_same_output(self):
        obs = [
            _make_obs(datetime(2024, 1, 15, 10, 0, tzinfo=timezone.utc), wave_height_m=1.5),
            _make_obs(datetime(2024, 1, 15, 12, 0, tzinfo=timezone.utc), wave_height_m=2.5),
        ]
        dto1 = compute_daily_features(date(2024, 1, 15), obs)
        dto2 = compute_daily_features(date(2024, 1, 15), obs)
        assert dto1.mean_wave_height_m == dto2.mean_wave_height_m
        assert dto1.circular_mean_direction_deg == dto2.circular_mean_direction_deg
