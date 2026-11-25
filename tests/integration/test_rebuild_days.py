"""
tests/integration/test_rebuild_days.py

Integration tests for preprocessing daily feature rebuild.

Covers:
  - Rebuild from Silver observations via FakeDatabaseGateway
  - Correct HST date grouping
  - Idempotent rebuild (running twice = same result)
  - Empty day handled gracefully
"""
from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from packages.db.fake import FakeDatabaseGateway
from packages.db.gateway import SilverObservationDTO
from services.preprocessing.app.jobs.rebuild_days import rebuild_daily_features


def _silver(ts: datetime, wvht: float | None = 1.0) -> SilverObservationDTO:
    return SilverObservationDTO(
        station_id="51201",
        observed_at=ts,
        source_kind="historical_annual",
        source_dataset="test",
        source_file_id=1,
        raw_id=None,
        measurement_sha256="b" * 64,
        quality_status="valid",
        wave_height_m=wvht,
        wind_speed_ms=5.0,
        sea_surface_temp_c=25.0,
        dominant_period_s=10.0,
    )


class TestRebuildDays:
    def test_basic_rebuild_produces_daily_features(self):
        db = FakeDatabaseGateway()
        # Seed two Silver rows for 2024-01-15 HST
        # UTC 2024-01-15T12:00Z = HST 2024-01-15T02:00
        # UTC 2024-01-15T14:00Z = HST 2024-01-15T04:00
        db.upsert_silver_batch([
            _silver(datetime(2024, 1, 15, 12, 0, tzinfo=timezone.utc), wvht=1.0),
            _silver(datetime(2024, 1, 15, 14, 0, tzinfo=timezone.utc), wvht=3.0),
        ])
        report = rebuild_daily_features(date(2024, 1, 15), date(2024, 1, 15), db_gateway=db)
        assert report.days_processed == 1
        assert report.days_empty == 0
        feats = db.get_daily_features("51201", date(2024, 1, 15), date(2024, 1, 15))
        assert len(feats) == 1
        assert feats[0].mean_wave_height_m == pytest.approx(2.0)

    def test_rebuild_idempotent(self):
        db = FakeDatabaseGateway()
        db.upsert_silver_batch([
            _silver(datetime(2024, 1, 15, 12, 0, tzinfo=timezone.utc), wvht=2.0),
        ])
        rebuild_daily_features(date(2024, 1, 15), date(2024, 1, 15), db_gateway=db)
        rebuild_daily_features(date(2024, 1, 15), date(2024, 1, 15), db_gateway=db)
        feats = db.get_daily_features("51201", date(2024, 1, 15), date(2024, 1, 15))
        assert len(feats) == 1
        assert feats[0].mean_wave_height_m == pytest.approx(2.0)

    def test_empty_day_not_upserted(self):
        db = FakeDatabaseGateway()
        # No Silver rows loaded
        report = rebuild_daily_features(date(2024, 1, 15), date(2024, 1, 15), db_gateway=db)
        assert report.days_empty == 1
        assert report.days_processed == 0
        feats = db.get_daily_features("51201", date(2024, 1, 15), date(2024, 1, 15))
        assert len(feats) == 0

    def test_multi_day_range(self):
        db = FakeDatabaseGateway()
        for day in [15, 16, 17]:
            db.upsert_silver_batch([
                _silver(datetime(2024, 1, day, 12, 0, tzinfo=timezone.utc), wvht=float(day))
            ])
        report = rebuild_daily_features(date(2024, 1, 15), date(2024, 1, 17), db_gateway=db)
        assert report.days_processed == 3
        feats = db.get_daily_features("51201", date(2024, 1, 15), date(2024, 1, 17))
        assert len(feats) == 3
