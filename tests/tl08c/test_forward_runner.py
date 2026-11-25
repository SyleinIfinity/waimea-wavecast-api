from datetime import date, datetime, timedelta, timezone

from services.database_api.app.api.forward import HST, day_bounds
from training.tl08c_forward_runner import daily_features, short_features, valid_observation


def observation(when, *, quality="valid", first_seen=None):
    return {"observed_at":when.isoformat(),"wave_height_m":1.5,"dominant_period_s":10.0,
            "quality_status":quality,"source_kind":"realtime",
            "first_seen_at":(first_seen or when).isoformat(),
            "updated_at":(first_seen or when).isoformat()}


def test_short_issued_target_remains_future_after_noaa_latency():
    anchor = datetime(2026,11,13,4,tzinfo=timezone.utc)
    rows = [observation(anchor-lag) for lag in (timedelta(),timedelta(minutes=30),
                                                  timedelta(hours=1),timedelta(hours=3))]
    result = short_features(rows,anchor+timedelta(minutes=35))
    assert result is None  # source is still too fresh for a 90-minute target
    result = short_features(rows,anchor+timedelta(minutes=65))
    assert result is not None and result[1] == anchor+timedelta(minutes=90)
    assert result[1]-anchor-timedelta(minutes=65) == timedelta(minutes=25)
    assert short_features(rows,anchor+timedelta(minutes=95)) is None
    rows[1]["quality_status"] = "suspect"
    assert short_features(rows,anchor+timedelta(minutes=65)) is None


def test_short_rejects_data_published_after_issue():
    when = datetime(2026,11,13,4,tzinfo=timezone.utc)
    assert not valid_observation(observation(when,first_seen=when+timedelta(hours=1)),
                                 when+timedelta(minutes=35))


def daily_row(day, *, ready="GOLD_READY", computed_after_end=True):
    _, end = day_bounds(day)
    computed = end+timedelta(minutes=5) if computed_after_end else end-timedelta(hours=2)
    return {"date_hst":day.isoformat(),"median_wave_height_m":1.5,"mean_dominant_period_s":10.0,
            "circular_mean_direction_deg":180.0,"valid_wave_count":48,"coverage_ratio":1.0,
            "suspect_count":0,"rejected_count":0,"unverified_count":0,
            "gold_readiness":ready,"gold_median_value":1.5,"gold_period_end_at":end.isoformat(),
            "gold_computed_at":computed.isoformat(),"calculated_at":computed.isoformat()}


def test_daily_requires_seven_complete_gold_days_and_future_period():
    anchor = date(2026,11,11)
    rows = [daily_row(anchor-timedelta(days=i)) for i in range(7)]
    now = day_bounds(anchor)[1]+timedelta(minutes=10)
    result = daily_features(rows,now)
    assert result is not None and result[0] == anchor
    assert len(result[1]) == 20
    rows[3]["gold_readiness"] = "WAITING_FOR_SOURCE"
    assert daily_features(rows,now) is None
    rows[3] = daily_row(anchor-timedelta(days=3))
    rows[0] = daily_row(anchor,computed_after_end=False)
    assert daily_features(rows,now) is None


def test_hst_period_end_uses_hawaii_calendar():
    start,end = day_bounds(date(2026,11,12))
    assert start.astimezone(HST).date() == date(2026,11,12)
    assert end-start == timedelta(days=1)
