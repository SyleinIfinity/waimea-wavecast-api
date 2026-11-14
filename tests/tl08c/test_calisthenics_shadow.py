from datetime import date, datetime, timedelta, timezone

import pytest

import training.calisthenics_shadow as shadow
from services.database_api.app.api.forward import day_bounds


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
