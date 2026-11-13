from datetime import date, timedelta

from training.tl08b_daily_horizons import samples_for_horizon


def _rows(days=90):
    first = date(2020, 1, 1)
    return [{"date_hst": str(first + timedelta(days=i)), "median_wave_height_m": 1 + i / 100,
             "valid_wave_count": 24, "coverage_ratio": 1.0, "suspect_count": 0,
             "rejected_count": 0, "unverified_count": 0} for i in range(days)]


def test_future_label_never_enters_features_and_gaps_are_excluded():
    rows = _rows()
    short = samples_for_horizon(rows, 7)
    long = samples_for_horizon(rows, 30)
    assert short and long
    before = short[0].features
    rows[13]["median_wave_height_m"] = 1000
    assert samples_for_horizon(rows, 7)[0].features == before
    assert samples_for_horizon(rows, 7)[0].target == 1000
    rows[3]["suspect_count"] = 1
    assert all(item.anchor_date_hst >= date(2020, 1, 10) for item in samples_for_horizon(rows, 7))


def test_30_day_target_requires_at_least_27_observed_days():
    rows = _rows(45)
    for row in rows[10:15]:
        row["coverage_ratio"] = .1
    assert all(item.anchor_date_hst >= date(2020, 1, 14) for item in samples_for_horizon(rows, 30)) or not samples_for_horizon(rows, 30)
