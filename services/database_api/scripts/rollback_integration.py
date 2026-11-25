"""Transactional repository checks against an existing DB; always rolls back."""
from datetime import datetime, timezone
from uuid import uuid4

import psycopg
from psycopg.rows import dict_row

from services.database_api.app.config import Settings
from services.database_api.app.repositories import bronze, gold, models, silver
from services.database_api.app.schemas.analytics import GoldBatch
from services.database_api.app.schemas.ml import ClassificationSchemeIn, ClusterCentroidIn, DatasetSplitIn, EvaluationIn, EvaluationSampleIn, ModelIn, TrainingRunIn
from services.database_api.app.schemas.observations import DailyFeatures, RawBatch, SilverBatch, SourceFileIn


def main():
    settings = Settings()
    marker = uuid4().hex
    conn = psycopg.connect(
        host=settings.db_host, port=settings.db_port, dbname=settings.db_name,
        user=settings.db_user, password=settings.db_password,
        sslmode=settings.db_sslmode, connect_timeout=5, row_factory=dict_row,
    )
    try:
        feed = SourceFileIn(
            feed_key="realtime", original_filename="tl01-rollback-test.txt",
            source_url="https://example.invalid/tl01-rollback-test",
            storage_format="txt", local_path="tl01-rollback-test",
            content_sha256=marker * 2,
        )
        file_id = bronze.register_source_file(conn, feed)["file_id"]
        assert bronze.register_source_file(conn, feed)["file_id"] == file_id
        observed = datetime(2004, 1, 1, 0, 0, tzinfo=timezone.utc)
        raw_batch = RawBatch.model_validate({"file_id": file_id, "rows": [{"row_sha256": marker * 2, "raw_line": "TL01 rollback fixture"}]})
        assert bronze.upsert_raw_batch(conn, raw_batch)["inserted"] == 1
        assert bronze.upsert_raw_batch(conn, raw_batch)["duplicate"] == 1
        original = {"rows": [{"observed_at": observed, "source_kind": "realtime", "source_dataset": "tl01-rollback-test", "source_file_id": file_id, "measurement_sha256": marker * 2, "wave_height_m": 1.0}]}
        assert silver.upsert_silver_batch(conn, SilverBatch.model_validate(original))["inserted"] == 1
        assert silver.upsert_silver_batch(conn, SilverBatch.model_validate(original))["duplicate"] == 1
        corrected = {"rows": [{**original["rows"][0], "measurement_sha256": marker[::-1] * 2, "wave_height_m": 1.5}]}
        assert silver.upsert_silver_batch(conn, SilverBatch.model_validate(corrected))["updated"] == 1
        revision = conn.execute("SELECT revision_no FROM silver.wave_observations WHERE station_id='51201' AND observed_at=%s", (observed,)).fetchone()["revision_no"]
        assert revision == 2
        audit_count = conn.execute("SELECT count(*) AS n FROM silver.wave_observation_revisions WHERE station_id='51201' AND observed_at=%s", (observed,)).fetchone()["n"]
        assert audit_count == 2
        result = gold.save_gold_results(conn, GoldBatch(table="time_series_metrics", rows=[{"station_id": "51201", "period_type": "day", "period_start_hst": "2004-01-01", "metric_code": "WVHT", "sample_count": 1, "valid_sample_count": 1, "mean_value": 1.5}]))
        assert result["saved"] == 1
        silver.upsert_daily_features(conn, DailyFeatures(station_id="51201", date_hst="2004-01-01", sample_count=1, valid_wave_count=1, good_quality_count=1, mean_wave_height_m=1.5))
        split_id = models.save_dataset_split(conn, DatasetSplitIn(dataset_name="tl01-rollback-" + marker, dataset_version="v1", task_type="regression", target_metric="WVHT", train_start_hst="2004-01-01", train_end_hst="2004-01-01"))["dataset_split_id"]
        model_id = models.save_model_metadata(conn, ModelIn(model_name="tl01-rollback-" + marker, model_version="v1", task_type="regression", algorithm="rollback-fixture", artifact_gcs_uri="gs://rollback-fixture/model.joblib"))["model_id"]
        training_id = models.save_training_run(conn, TrainingRunIn(dataset_split_id=split_id, model_id=model_id, task_type="regression", algorithm="rollback-fixture", status="success"))["training_run_id"]
        evaluation_id = models.save_evaluation(conn, EvaluationIn(training_run_id=training_id, model_id=model_id, dataset_split_id=split_id, evaluation_stage="test", task_type="regression", sample_count=1, mae=0.1))["evaluation_id"]
        assert models.save_evaluation_sample(conn, EvaluationSampleIn(evaluation_id=evaluation_id, target_date_hst="2004-01-03", target_name="WVHT", actual_value=1.5, predicted_value=1.4, feature_cutoff_date_hst="2004-01-02"))["evaluation_sample_id"] > 0
        assert models.save_cluster_centroid(conn, ClusterCentroidIn(model_id=model_id, cluster_label=0, centroid_features={"wave_height_m": 1.5}))["cluster_label"] == 0
        assert models.save_classification_scheme(conn, ClassificationSchemeIn(scheme_name="tl01-rollback-" + marker, scheme_version="v1", target_metric="WVHT", label_rules={"fixture": True}, rationale="rollback-only fixture"))["scheme_id"] > 0
        print("PASS: Bronze dedup, Silver correction/revisions/daily, Gold upsert, ML metadata/evaluation")
    finally:
        conn.rollback()
        conn.close()
        print("ROLLBACK: no test rows retained")


if __name__ == "__main__":
    main()
