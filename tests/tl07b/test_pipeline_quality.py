from datetime import datetime, timedelta, timezone

from packages.db.gateway import SilverObservationDTO
from services.preprocessing.app.jobs.quality import decision
from services.preprocessing.app.jobs.gold import rebuild_gold


def row(when, *, quality="unverified", height=1.0, period=8.0):
    return SilverObservationDTO(
        station_id="51201", observed_at=when, source_kind="realtime",
        source_dataset="test", source_file_id=1, raw_id=1,
        measurement_sha256="a" * 64, quality_status=quality,
        wave_height_m=height, dominant_period_s=period,
    )


def test_qc_requires_core_wave_values_and_preserves_measurement_identity():
    when = datetime(2026, 11, 12, 10, tzinfo=timezone.utc)
    accepted = decision(row(when), now=when)
    assert accepted["quality_status"] == "valid"
    assert accepted["measurement_sha256"] == "a" * 64
    assert decision(row(when, height=None), now=when)["quality_status"] == "suspect"
    assert decision(row(when, height=-1), now=when)["quality_status"] == "rejected"


def test_gold_excludes_unverified_values_and_uses_full_period_coverage():
    when = datetime(2026, 11, 12, 10, tzinfo=timezone.utc)

    class Gateway:
        saved = []

        def get_observations(self, *_):
            return [row(when, quality="valid", height=2.0),
                    row(when.replace(minute=30), quality="unverified", height=99.0)]

        def save_resolution_metrics(self, rows):
            self.saved.extend(rows)
            return {"saved": len(rows)}

    gateway = Gateway()
    rebuild_gold(gateway, year=2026,
                 now=datetime(2027, 1, 1, 12, tzinfo=timezone.utc))
    daily = next(item for item in gateway.saved if item["resolution"] == "daily")
    assert daily["mean_value"] == 2.0
    assert daily["valid_sample_count"] == 1
    assert daily["unverified_count"] == 1
    assert daily["readiness"] == "INSUFFICIENT_DATA"
    assert -1 <= daily["hour_sin"] <= 1
    assert daily["calendar_season"] == "SON"


def test_late_correction_rewrites_only_affected_and_rolling_successors():
    first = datetime(2026, 11, 12, 10, tzinfo=timezone.utc)

    class Gateway:
        saved = []

        def get_observations(self, *_):
            return [row(first + timedelta(minutes=30*i), quality="valid", height=float(i+1))
                    for i in range(8)]

        def save_resolution_metrics(self, rows):
            self.saved.extend(rows)
            return {"saved": len(rows)}

    gateway = Gateway()
    changed = first + timedelta(minutes=30)
    rebuild_gold(gateway, year=2026, changed_from=changed, changed_to=changed,
                 now=datetime(2027, 1, 1, 12, tzinfo=timezone.utc))
    halfhours = [item for item in gateway.saved if item["resolution"] == "30min"]
    assert len(halfhours) == 3
    assert [item["mean_value"] for item in halfhours] == [2.0, 3.0, 4.0]
    assert halfhours[0]["lag1_median_value"] == 1.0
