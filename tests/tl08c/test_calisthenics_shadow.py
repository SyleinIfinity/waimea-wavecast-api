from datetime import date, datetime, timedelta, timezone

import pytest

import training.calisthenics_shadow as shadow
from services.database_api.app.api.forward import day_bounds
from training.calisthenics_next_day import input_cutoff, live_features


def _row(day, *, ready="GOLD_READY"):
    return {"date_hst":day,"median_wave_height_m":1.5,"median_value":1.5,
            "coverage_ratio":1.0,"suspect_count":0,"rejected_count":0,
            "unverified_count":0,"imputed_count":0,"readiness":ready,
            "valid_sample_count":48,"calculation_version":"v1",
            "computed_at":day_bounds(day)[1]+timedelta(minutes=5)}


class _Result:
    def __init__(self, rows): self.rows=rows
    def fetchall(self): return self.rows


class _ThirtyDayDB:
    def __init__(self, rows): self.rows=rows
    def execute(self,sql,params):
        assert "BETWEEN %s AND %s" in sql
        return _Result(self.rows)


def test_thirty_day_truth_needs_27_qc_days_and_is_mean_of_observed_days():
    anchor=date(2026,11,11)
    forecast={"anchor_date_hst":anchor,"target_at":day_bounds(anchor+timedelta(days=30))[1]}
    rows=[_row(anchor+timedelta(days=i)) for i in range(1,31)]
    rows[0]["median_value"]=rows[0]["median_wave_height_m"]=2.0
    for i in (27,28,29): rows[i]["readiness"]="WAITING_FOR_SOURCE"
    result=shadow._thirty_day_truth(_ThirtyDayDB(rows),forecast)
    assert result is not None
    assert result[0]==pytest.approx((26*1.5+2.0)/27)
    rows[26]["readiness"]="WAITING_FOR_SOURCE"
    assert shadow._thirty_day_truth(_ThirtyDayDB(rows),forecast) is None


def test_reconcile_never_uses_truth_before_target():
    now=datetime(2026,11,13,tzinfo=timezone.utc)
    class DB:
        def execute(self,sql,params):
            assert "target_at<=%s" in sql
            assert params[0]==now
            return _Result([])
    assert shadow.reconcile(DB(),now)=={"scanned":0,"evaluations_inserted":0,"pending_truth":0}


def test_shadow_model_family_is_pinned(monkeypatch):
    class Result:
        def fetchone(self): return None
    class DB:
        def execute(self,sql,params):
            assert params==("calisthenics 1.5 / 30d",)
            return Result()
    with pytest.raises(RuntimeError,match="unavailable"):
        shadow._current_model(DB(),"30d",object())


def test_tomorrow_features_use_the_morning_and_six_closed_days():
    day = date(2026, 12, 31)
    start, end = day_bounds(day)
    cutoff = input_cutoff(day)
    issued = cutoff + timedelta(hours=1, minutes=10)
    history = []
    for lag in range(1, 7):
        previous = day - timedelta(days=lag)
        row = _row(previous)
        history.append({**row, "valid_wave_count": 48, "gold_readiness": "GOLD_READY",
                        "gold_median_value": 1.5, "gold_period_end_at": day_bounds(previous)[1],
                        "gold_computed_at": row["computed_at"], "calculated_at": row["computed_at"]})
    observations = [{"observed_at": start + timedelta(minutes=30 * i + 26),
                     "station_id": "51201", "source_kind": "realtime", "source_dataset": "noaa",
                     "source_file_id": 1, "raw_id": None, "measurement_sha256": f"{i:064x}",
                     "quality_status": "valid", "wave_height_m": 2.0, "dominant_period_s": 11.0}
                    for i in range(24)]
    observations.extend({**observations[-1], "observed_at": cutoff + timedelta(minutes=30 * i),
                         "wave_height_m": 9.0, "measurement_sha256": f"{i + 24:064x}"}
                        for i in range(24))

    features, digest = live_features(day, history, observations, issued)
    assert features[:7] == (2.0, *([1.5] * 6))
    assert features[-2:] == (24, 1.0)
    assert live_features(day, history, observations[:24], issued) == (features, digest)
    assert live_features(day, history, observations, cutoff) is None
    assert live_features(day, history, observations, end) is None
    assert live_features(day, history[:-1], observations, issued) is None
    assert live_features(day, history, observations[:21], issued) is None
    observations[0]["updated_at"] = issued + timedelta(seconds=1)
    assert live_features(day, history, observations, issued) is None


def test_tomorrow_issuance_reserves_next_hawaii_day_at_year_boundary(monkeypatch):
    day = date(2026, 12, 31)
    cutoff = input_cutoff(day)
    issued = cutoff + timedelta(hours=1)
    target_day = date(2027, 1, 1)

    class Result:
        def fetchone(self):
            return None

    class DB:
        def execute(self, sql, params):
            assert "horizon='1d'" in sql
            assert params == (cutoff, target_day)
            return Result()

        def commit(self):
            pass

    class Gateway:
        def get_observations(self, start, end):
            assert (start, end) == (day_bounds(day)[0], cutoff)
            return []

    def reserve(conn, model, horizon, now, anchor, target, features, sha, anchor_day, forecast_day, kind):
        assert (horizon, now, anchor_day, forecast_day) == ("1d", issued, day, target_day)
        assert anchor == cutoff and target == day_bounds(target_day)[1]
        assert now < day_bounds(forecast_day)[0]
        return {"forecast_id": 123}

    monkeypatch.setattr(shadow, "live_features", lambda *_: ((2.0,), "a" * 64))
    monkeypatch.setattr(shadow, "_current_model", lambda *_: ({"model_id": 1}, object()))
    monkeypatch.setattr(shadow, "_reserve", reserve)
    monkeypatch.setattr(shadow, "_persist", lambda *_: True)
    assert shadow.issue_tomorrow(DB(), Gateway(), object(), issued, []) == {
        "horizon": "1d", "forecast_id": 123, "issued": 1, "target_date_hst": "2027-01-01",
    }


def test_reservation_refuses_a_daily_forecast_for_today():
    day = date(2026, 11, 22)
    cutoff = input_cutoff(day)
    with pytest.raises(ValueError, match="next Hawaii day"):
        shadow._reserve(None, {}, "1d", cutoff + timedelta(hours=1), cutoff, day_bounds(day)[1],
                        (2.0,), "a" * 64, day, day, "hst_day_median_wvht")
