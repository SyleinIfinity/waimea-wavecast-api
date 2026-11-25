from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from training.tl08b_short_horizons import build_short_samples, split_short


def test_short_horizon_requires_exact_valid_observed_target():
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    rows = [{"observed_at": (start + timedelta(minutes=30 * i)).isoformat(),
             "wave_height_m": float(i), "dominant_period_s": 10.0,
             "quality_status": "valid", "source_kind": "realtime"} for i in range(20)]
    rows[8]["quality_status"] = "suspect"
    samples = build_short_samples(rows, "1h")
    assert all(target != start + timedelta(minutes=30 * 8) for _, target, _, _ in samples)
    assert all(anchor < target for anchor, target, _, _ in samples)


def test_short_split_embargoes_future_target():
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    samples = [(start + timedelta(minutes=30 * i), start + timedelta(minutes=30 * (i + 12)),
                (float(i),), float(i)) for i in range(1000)]
    train, validation, test = split_short(samples, "6h")
    assert train[-1][1] < validation[0][0]
    assert validation[-1][1] < test[0][0]
    hst = ZoneInfo("Pacific/Honolulu")
    assert train[-1][0].astimezone(hst).date() < validation[0][0].astimezone(hst).date()
    assert validation[-1][0].astimezone(hst).date() < test[0][0].astimezone(hst).date()
