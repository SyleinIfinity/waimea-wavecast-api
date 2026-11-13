from datetime import date, datetime, timedelta, timezone
from math import sin

import pytest
from fastapi.testclient import TestClient

from packages.gcs.fake import FakeGCSClient
from packages.ml.analytics import aggregate, correlation, decompose, histogram, scatter
from packages.ml.artifacts import ModelStore
from packages.ml.features import FEATURE_NAMES, build_samples, chronological_split
from packages.ml.gateway import FakeMLGateway, HttpMLGateway
from packages.ml.jobs import run_analysis, run_inference
from packages.ml.worker import WorkerSettings, create_worker
from services.scheduler.app.main import due_phases
from training.run import train_suite
from services.database_api.app.schemas.analytics import GoldBatch
from services.database_api.app.schemas.ml import (ModelIn, DatasetSplitIn, TrainingRunIn,
    EvaluationIn, EvaluationSampleIn, ClusterCentroidIn, ClassificationSchemeIn)


class ContractFake(FakeMLGateway):
    """Reject TL05 payloads that do not match TL01's implemented API schemas."""
    def save_gold_results(self, table, rows, key):
        if rows:
            GoldBatch(table=table, rows=rows)
        return super().save_gold_results(table, rows, key)

    def save_model(self, body, key):
        ModelIn(**body)
        return super().save_model(body, key)

    def save_dataset_split(self, body, key):
        DatasetSplitIn(**body)
        return super().save_dataset_split(body, key)

    def save_scheme(self, body, key):
        ClassificationSchemeIn(**body)
        return super().save_scheme(body, key)

    def save_centroid(self, body, key):
        ClusterCentroidIn(**body)
        return super().save_centroid(body, key)

    def save_training_run(self, body, key):
        TrainingRunIn(**body)
        return super().save_training_run(body, key)

    def save_evaluation(self, body, key):
        EvaluationIn(**body)
        return super().save_evaluation(body, key)

    def save_evaluation_sample(self, body, key):
        EvaluationSampleIn(**body)
        return super().save_evaluation_sample(body, key)


def daily_fixture(count=130):
    first = date(2025, 1, 1)
    return [{"date_hst": (first + timedelta(days=i)).isoformat(), "median_wave_height_m": 1.5 + .5 * sin(i / 5) + .002 * i,
             "mean_dominant_period_s": 10 + sin(i / 9), "circular_mean_direction_deg": (350 + i) % 360,
             "valid_wave_count": 42, "coverage_ratio": .875} for i in range(count)]


def observations(count=30):
    first = datetime(2025, 1, 1, 12, tzinfo=timezone.utc)
    return [{"observed_at": (first + timedelta(days=i)).isoformat(), "wave_height_m": 1 + i / 10,
             "quality_status": "valid",
             "dominant_period_s": 8 + i / 20, "sea_surface_temp_c": 24 + i / 30,
             "wind_speed_ms": 4 + i / 15, "mean_wave_direction_deg": 359 if i % 2 else 1} for i in range(count)]


def test_features_never_use_target_in_x_and_split_has_gap():
    rows = daily_fixture()
    samples = build_samples(rows)
    assert samples[0].anchor_date_hst == date(2025, 1, 7)
    assert samples[0].target_date_hst == date(2025, 1, 8)
    before = samples[0].features
    rows[7]["median_wave_height_m"] = 999
    assert build_samples(rows)[0].features == before
    train, val, test = chronological_split(samples)
    assert train[-1].target_date_hst < val[0].anchor_date_hst
    assert val[-1].target_date_hst < test[0].anchor_date_hst
    assert len(before) == len(FEATURE_NAMES)
    assert len(build_samples(rows[:6])) == 0
    rows[0]["source_data_max_updated_at"] = "2025-01-09T12:00:00+00:00"
    with pytest.raises(ValueError, match="after prediction cutoff"):
        build_samples(rows)


def test_analytics_hst_circular_and_undefined_correlation():
    rows = observations(3)
    result = aggregate(rows, "day", "MWD")
    assert result[0]["mean_value"] in (1, 359)
    assert correlation([(1, 4), (2, 4), (3, 4)])[1] is None
    assert correlation([(1, 1), (2, 2)], "spearman")[1] is None
    assert correlation([(1, 1), (2, 2), (3, 3)], "spearman")[1] == pytest.approx(1)
    assert decompose(aggregate(rows, "month")) == []
    assert sum(bin["count"] for bin in histogram(rows, bins=3)) == 3
    assert len(scatter(rows, limit=2)) == 2
    boundary = [{"observed_at": "2025-01-02T05:00:00+00:00", "wave_height_m": 2.0}]
    assert aggregate(boundary, "day")[0]["period_start_hst"] == "2025-01-01"


def test_complete_monthly_seasonality():
    rows = []
    for i in range(30):
        year, month = divmod(2023 * 12 + i, 12)
        rows.append({"observed_at": datetime(year, month + 1, 10, 12, tzinfo=timezone.utc).isoformat(),
                         "wave_height_m": 1.5 + .4 * sin(i / 2), "dominant_period_s": 9.0,
                         "quality_status": "valid"})
    fake = FakeMLGateway(observations=rows)
    result = run_analysis(fake, "trend_seasonal_components", date(2025, 6, 1), date(2025, 6, 30), "season-test")
    assert result["saved"] == 1
    assert fake.gold["trend_seasonal_components"][0]["trend_value"] is not None


def test_gold_rebuild_uses_fake_gateway_without_training():
    fake = ContractFake(observations=observations(30))
    before = len(fake.training_runs)
    for kind in ("time_series_metrics", "correlations", "trend_seasonal_components"):
        result = run_analysis(fake, kind, date(2025, 1, 1), date(2025, 1, 30), f"{kind}-test")
        assert result["status"] in ("success", "skipped")
    assert fake.gold["time_series_metrics"]
    assert fake.gold["correlations"]
    assert all(row["metric_x"] < row["metric_y"] for row in fake.gold["correlations"])
    original_count = len(fake.gold["time_series_metrics"])
    fake.gold["time_series_metrics"].clear()
    run_analysis(fake, "time_series_metrics", date(2025, 1, 1), date(2025, 1, 30), "rebuild-after-delete")
    assert len(fake.gold["time_series_metrics"]) == original_count
    assert len(fake.training_runs) == before


def test_training_artifacts_and_inference_do_not_retrain():
    rows = daily_fixture()
    fake = ContractFake(daily=rows)
    store = ModelStore(FakeGCSClient(), "test-models")
    trained = train_suite(rows, store, fake, version="fixture-v1", synthetic_fixture=True)
    assert set(trained["models"]) == {"regression", "classification", "clustering"}
    assert len(set(trained["dataset_split_ids"].values())) == 3
    assert trained["models"]["regression"]["metrics"]["sample_count"] > 0
    assert len(fake.training_runs) == 3
    assert len(fake.evaluation_samples) > 0
    assert {row["task_type"] for row in fake.dataset_splits} == {"regression", "classification", "clustering"}
    with pytest.raises(ValueError, match="immutable|different"):
        store.save(object(), task="regression", version="fixture-v1", manifest={})
    next_version = store.save(object(), task="regression", version="fixture-v2", manifest={})
    assert next_version["artifact_gcs_uri"] != trained["models"]["regression"]["artifact"]["artifact_gcs_uri"]
    for model in fake.models.values():
        model["status"] = "ready"
    cutoff = date(2025, 1, 1) + timedelta(days=129)
    for kind in ("predictions", "classifications", "cluster_assignments"):
        result = run_inference(fake, store, kind, cutoff, f"infer:{kind}")
        assert result["status"] == "success"
        assert fake.gold[kind]
    assert len(fake.training_runs) == 3
    model = fake.get_ready_model("regression")
    model["artifact_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="hash mismatch"):
        run_inference(fake, store, "predictions", cutoff, "bad-hash")
    assert len(fake.gold["predictions"]) == 1


def test_worker_auth_and_background_job():
    app = create_worker("analysis", WorkerSettings(internal_api_token="test-token"))
    fake = FakeMLGateway(observations=observations(30))
    app.state.gateway_factory = lambda: fake
    body = {"from_hst": "2025-01-01", "to_hst": "2025-01-30", "pattern_type": "time_series_metrics"}
    with TestClient(app) as client:
        assert client.post("/internal/v1/analysis/rebuild", json=body).status_code == 401
        assert client.post("/internal/v1/analysis/rebuild", json=body, headers={"X-Internal-Token": "test-token"}).status_code == 422
        response = client.post("/internal/v1/analysis/rebuild", json=body,
                               headers={"X-Internal-Token": "test-token", "Idempotency-Key": "analysis-test"})
        assert response.status_code == 202
        assert fake.jobs[response.json()["processing_run_id"]]["status"] == "success"
        count = len(fake.gold["time_series_metrics"])
        repeated = client.post("/internal/v1/analysis/rebuild", json=body,
                               headers={"X-Internal-Token": "test-token", "Idempotency-Key": "analysis-test"})
        assert repeated.json()["duplicate"] is True
        assert len(fake.gold["time_series_metrics"]) == count


def test_prediction_worker_without_model_does_not_open_gcs():
    app = create_worker("prediction", WorkerSettings(internal_api_token="test-token"))
    fake = ContractFake(daily=daily_fixture())
    app.state.gateway_factory = lambda: fake
    app.state.store_factory = lambda: (_ for _ in ()).throw(AssertionError("GCS opened"))
    with TestClient(app) as client:
        response = client.post("/internal/v1/prediction/run", json={"from_hst": "2025-05-11", "to_hst": "2025-05-11"},
                               headers={"X-Internal-Token": "test-token", "Idempotency-Key": "no-model"})
    assert response.status_code == 202
    assert fake.jobs[response.json()["processing_run_id"]]["status"] == "skipped"


def test_scheduler_registry_no_training():
    assert due_phases(datetime(2025, 1, 1, 12, 0, tzinfo=timezone.utc)) == ["poll", "analysis", "inference"]
    assert due_phases(datetime(2025, 1, 1, 12, 30, tzinfo=timezone.utc)) == ["poll"]


def test_http_gateway_recognizes_tl01_existing_job(monkeypatch):
    gateway = HttpMLGateway("http://example.invalid", "test-token")
    monkeypatch.setattr(gateway, "_request", lambda *args, **kwargs: {"processing_run_id": 7, "status": "success"})
    assert gateway.create_job({"job_type": "analysis"}, "same-key")["duplicate"] is True
