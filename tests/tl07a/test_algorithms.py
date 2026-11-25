from datetime import datetime, timedelta, timezone

import pytest

from packages.gcs.fake import FakeGCSClient
from packages.ml.analytics import daily_correlations, decompose
from packages.ml.data_quality import (GapSlot, QualityThresholds, classify_observations,
                                      detect_gaps, quality_gate, timeline_statuses)
from packages.ml.forecast import (baseline_forecast, evaluate_persistence,
                                  evaluate_regime_persistence, walk_forward_persistence)
from packages.ml.features import build_samples
from packages.ml.imputation import (DerivedPoint, ImputationPolicy,
                                    TrailingMedianBaseline, ValidationEvidence,
                                    estimate_missing, fit_lag_ridge, reconcile_observed,
                                    save_derived_dataset)
from packages.ml.timeseries import (aggregate_resolution, autocorrelation,
                                    cyclical_features, seasonal_anomalies,
                                    trailing_statistics)
from services.preprocessing.app.features.daily import compute_daily_features
from services.preprocessing.app.quality.checks import run_quality_checks

UTC = timezone.utc
T0 = datetime(2026, 1, 1, tzinfo=UTC)


def obs(index, value=1.0, **extra):
    return {"observed_at": T0 + timedelta(minutes=30*index),
            "wave_height_m": value, "quality_status": "valid", **extra}


def test_gap_cadence_pending_missing_and_source_states():
    rows = [obs(i) for i in (0, 1, 2, 4, 5, 6, 7, 8)]
    rows.append(dict(rows[0]))
    rows[2]["revision_no"] = 2
    rows[3]["first_seen_at"] = T0 + timedelta(hours=6)
    rows[4]["quality_status"] = "rejected"
    result = detect_gaps(rows, as_of=T0+timedelta(hours=3),
                         publication_wait=timedelta(hours=2),
                         min_intervals=5, min_support_ratio=.7,
                         max_trailing_slots=0)
    assert result.cadence.minutes == 30
    assert [(slot.observation_time,slot.status) for slot in result.slots] == [
        (T0+timedelta(minutes=90), "PENDING_PUBLICATION")]
    assert (result.duplicate_timestamps,result.late_observations,
            result.corrected_observations,result.invalid_observations) == (1,1,1,1)
    later = detect_gaps(rows, as_of=T0+timedelta(hours=5),
                        publication_wait=timedelta(hours=2),
                        min_intervals=5, min_support_ratio=.7,
                        max_trailing_slots=0)
    assert later.slots[0].status == "MISSING"
    timeline=timeline_statuses(rows,later)
    assert timeline.count("MISSING")==1
    classified=classify_observations(rows,as_of=T0+timedelta(days=1),
                                     publication_wait=timedelta(hours=2),freshness_limit=timedelta(hours=3))
    assert any("DUPLICATE" in item.flags for item in classified)
    assert any("CORRECTED" in item.flags for item in classified)
    assert any(item.source_type=="INVALID" for item in classified)
    assert detect_gaps(rows[:2], as_of=T0, publication_wait=timedelta(hours=2),
                       min_intervals=5,min_support_ratio=.7).status == "INSUFFICIENT_DATA"


def test_missing_whole_day_is_detected_without_fabricating_values():
    rows=[obs(i) for i in range(60) if i < 6 or i >= 54]
    result=detect_gaps(rows,as_of=T0+timedelta(days=4),
                       publication_wait=timedelta(hours=2),
                       min_intervals=5,min_support_ratio=.8,max_trailing_slots=0)
    assert result.status=="OBSERVED"
    assert len(result.slots)==48
    assert all(slot.status=="MISSING" for slot in result.slots)
    assert timeline_statuses(rows,result).count("MISSING")==48


def test_imputation_has_validation_availability_cap_and_reconciliation():
    target = T0+timedelta(hours=2)
    rows = [obs(i,float(i+1)) for i in (0,1,2,3)]
    rows.append(obs(1,999.0,first_seen_at=target+timedelta(days=1)))
    evidence = ValidationEvidence("trailing-median","v1",T0,T0,.2,30)
    policy = ImputationPolicy(3,1,timedelta(hours=3),.5,20)
    slots=[GapSlot(target,"MISSING",30),GapSlot(target+timedelta(minutes=30),"MISSING",30)]
    points=estimate_missing(slots,rows,as_of=target+timedelta(hours=3),
                            model=TrailingMedianBaseline(),evidence=evidence,policy=policy)
    assert points[0].value == 3.0
    assert points[0].source_type == "IMPUTED" and points[0].uncertainty == .2
    assert points[1].source_type == "INSUFFICIENT_DATA"
    reconciled=reconcile_observed(points,[obs(4,5.0)])
    assert reconciled.points[0].source_type == "OBSERVED"
    assert reconciled.points[0].provenance[-1]["value"] == 3.0
    assert reconciled.affected_hst_days and reconciled.affected_hst_weeks
    client=FakeGCSClient()
    artifact=save_derived_dataset(list(reconciled.points),client=client,bucket="test",version="v1")
    assert artifact["record_count"] == 2 and artifact["gcs_uri"].startswith("gs://test/datasets/processed/")
    future=ValidationEvidence("bad","v2",target+timedelta(days=1),target+timedelta(days=1),.1,30)
    with pytest.raises(ValueError,match="future"):
        estimate_missing(slots,rows,as_of=target,model=TrailingMedianBaseline(),evidence=future,policy=policy)
    uncertain=ValidationEvidence("weak","v2",T0,T0,2.0,30)
    assert estimate_missing(slots[:1],rows,as_of=target+timedelta(hours=3),
                            model=TrailingMedianBaseline(),evidence=uncertain,
                            policy=policy)[0].source_type=="INSUFFICIENT_DATA"


def test_lag_ridge_uses_chronological_verified_history_only():
    rows=[obs(i,1+0.01*i) for i in range(80)]
    model,evidence=fit_lag_ridge(rows,cadence_minutes=30,train_fraction=.7,
                                 min_train_samples=30,min_validation_samples=10,
                                 alpha=1.0,version="test-v1")
    assert evidence.trained_until < evidence.validated_at
    assert evidence.validation_mae >= 0
    target=rows[-1]["observed_at"]+timedelta(minutes=30)
    point=estimate_missing([GapSlot(target,"MISSING",30)],rows,as_of=target+timedelta(hours=3),
                           model=model,evidence=evidence,
                           policy=ImputationPolicy(3,1,timedelta(hours=2),1.0,10))[0]
    assert point.source_type=="IMPUTED" and point.model_version=="test-v1"
    with pytest.raises(ValueError,match="insufficient verified"):
        fit_lag_ridge([{**row,"quality_status":"unverified"} for row in rows],
                      cadence_minutes=30,train_fraction=.7,min_train_samples=30,
                      min_validation_samples=10,alpha=1.0,version="test-v1")


def test_quality_gate_fails_closed():
    thresholds=QualityThresholds(.1,.1,0,1,timedelta(hours=2),.5,.8,4)
    good=quality_gate(["OBSERVED"]*9+["MISSING"],latest_observed_at=T0,
                      as_of=T0+timedelta(minutes=30),uncertainty=.2,thresholds=thresholds)
    assert good.status == "OBSERVED"
    bad=quality_gate(["OBSERVED"]*2+["MISSING"]*3,latest_observed_at=T0,
                     as_of=T0+timedelta(hours=5),uncertainty=None,thresholds=thresholds)
    assert bad.status == "INSUFFICIENT_DATA"
    assert {"missing_ratio","consecutive_missing","stale_or_unknown_freshness","uncertainty_unverified"} <= set(bad.reasons)


def test_multi_resolution_completeness_and_causal_seasonality():
    rows=[obs(i,1+i/10) for i in range(48)]
    daily=aggregate_resolution(rows,"daily",cadence_minutes=30,minimum_coverage=.8)
    assert sum(row["valid_sample_count"] for row in daily)==48
    assert any(row["quality_status"]=="INSUFFICIENT_DATA" for row in daily)
    half=aggregate_resolution(rows[:1],"30min",cadence_minutes=30,minimum_coverage=1)
    assert half[0]["completeness"]==1
    assert "calendar_season" in cyclical_features(T0)
    assert autocorrelation([1,2,3,1,2,3,1,2,3],3)==pytest.approx(1)
    assert autocorrelation([1,1,1,1,1,1],1) is None
    periods=[{"period_start_hst":datetime(2024+i//12,i%12+1,1,tzinfo=UTC).isoformat(),
              "median":float(i),"quality_status":"OBSERVED"} for i in range(24)]
    anomalies=seasonal_anomalies(periods,minimum_prior_years=1)
    assert anomalies[0]["seasonal_anomaly"] is None
    assert anomalies[12]["seasonal_anomaly"]==12
    rolling=trailing_statistics([{"period_start_hst":str(i),"median":float(i),"quality_status":"OBSERVED"} for i in range(3)],window=2)
    assert rolling[0]["rolling_mean"] is None and rolling[2]["rolling_mean"]==1.5


def test_forecast_horizon_split_and_long_horizon_refusal():
    rows=[obs(i,1+i/100) for i in range(200)]
    result=evaluate_persistence(rows,"1h",split_fraction=.7,min_train_pairs=50,min_test_pairs=10)
    assert result.status=="EXPERIMENTAL"
    assert result.train_pairs>50 and result.test_pairs>10
    folds=walk_forward_persistence(rows,"1h",first_train_fraction=.5,folds=2,
                                   min_train_pairs=20,min_test_pairs=10)
    assert len(folds)==2 and all(fold.test_pairs>10 for fold in folds)
    assert evaluate_persistence(rows,"year",split_fraction=.7,min_train_pairs=50,min_test_pairs=10).status=="NOT_READY"
    forecast=baseline_forecast(rows,"1h",result,as_of=rows[-1]["observed_at"],
                               maximum_freshness=timedelta(hours=1),minimum_skill=-10,
                               minimum_interval_coverage=0,
                               model_version="baseline-v1")
    assert forecast.quality_status=="EXPERIMENTAL"
    assert forecast.target_timestamp==rows[-1]["observed_at"]+timedelta(hours=1)
    stale=baseline_forecast(rows,"1h",result,as_of=rows[-1]["observed_at"]+timedelta(days=1),
                            maximum_freshness=timedelta(hours=1),minimum_skill=-10,
                            minimum_interval_coverage=0,
                            model_version="baseline-v1")
    assert stale.quality_status=="INSUFFICIENT_DATA"
    classified=evaluate_regime_persistence(rows,"1h",split_fraction=.7,
                                           min_train_pairs=50,min_test_pairs=10)
    assert classified["status"]=="EXPERIMENTAL"
    assert 0 <= classified["accuracy"] <= 1


def test_correlation_uses_same_observation_pairs():
    rows=[]
    for day in range(3):
        when=T0+timedelta(days=day)
        rows += [{"observed_at":when,"wave_height_m":float(day+1),"dominant_period_s":None},
                 {"observed_at":when+timedelta(minutes=30),"wave_height_m":None,"dominant_period_s":float(day+5)}]
    assert daily_correlations(rows,T0.date(),(T0+timedelta(days=3)).date())==[]


def test_decomposition_does_not_use_future_months_for_past_season():
    monthly=[]
    for i in range(30):
        year, month=divmod(2023*12+i,12)
        monthly.append({"period_start_hst":f"{year}-{month+1:02d}-01","median_value":float(i),"metric_code":"WVHT"})
    before=decompose(monthly)
    monthly[-1]["median_value"]=9999.0
    after=decompose(monthly)
    assert before[17]["seasonal_value"]==after[17]["seasonal_value"]


def test_training_samples_exclude_imputed_features_and_targets():
    from datetime import date
    rows=[{"date_hst":(date(2026,1,1)+timedelta(days=i)).isoformat(),
           "median_wave_height_m":float(i+1),"mean_dominant_period_s":10.0,
           "circular_mean_direction_deg":20.0,"valid_wave_count":48,
           "coverage_ratio":1.0,"source_type":"OBSERVED"} for i in range(12)]
    assert build_samples(rows)
    rows[7]["source_type"]="IMPUTED"
    assert all(sample.target_date_hst != date(2026,1,8) and sample.anchor_date_hst != date(2026,1,8)
               for sample in build_samples(rows))


def test_daily_features_do_not_count_unverified_or_rejected_as_good():
    from types import SimpleNamespace
    from datetime import date
    def row(status,value):
        return SimpleNamespace(wave_height_m=value,dominant_period_s=10.0,
                               mean_wave_direction_deg=20.0,sea_surface_temp_c=25.0,
                               wind_speed_ms=3.0,quality_status=status)
    dto=compute_daily_features(date(2026,1,1),[row("unverified",1.0),row("valid",2.0),row("rejected",99.0)],
                               expected_sample_count=2)
    assert dto.good_quality_count==1
    assert dto.valid_wave_count==1
    assert dto.max_wave_height_m==2.0
    assert dto.coverage_ratio==0.5


def test_qc_rejects_physically_invalid_measurements():
    result=run_quality_checks("51201",T0,{"WVHT":-1.0,"DPD":0.0,"WSPD":float("nan")})
    assert result.quality_status=="rejected"
    assert {issue.field_code for issue in result.issues}=={"WVHT","DPD","WSPD"}
