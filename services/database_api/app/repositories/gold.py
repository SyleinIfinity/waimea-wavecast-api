from services.database_api.app.repositories.common import insert_row, upsert_row
from services.database_api.app.schemas.analytics import GoldBatch


GOLD_COLUMNS = {
    "resolution_metrics": ({"station_id", "resolution", "period_start_at", "period_end_at", "metric_code", "sample_count", "valid_sample_count", "expected_sample_count", "missing_count", "suspect_count", "rejected_count", "unverified_count", "completeness", "readiness", "mean_value", "median_value", "min_value", "max_value", "variance_value", "stddev_value", "rolling_mean", "trend_per_period", "residual_value", "calculation_version"}, ("station_id", "resolution", "period_start_at", "metric_code")),
    "time_series_metrics": ({"station_id", "period_type", "period_start_hst", "metric_code", "sample_count", "valid_sample_count", "mean_value", "median_value", "min_value", "max_value", "stddev_value", "p95_value", "method", "processing_run_id"}, ("station_id", "period_type", "period_start_hst", "metric_code")),
    "trend_seasonal_components": ({"station_id", "metric_code", "period_type", "period_start_hst", "observed_value", "trend_value", "seasonal_value", "residual_value", "decomposition_method", "seasonal_periods", "analysis_version", "processing_run_id"}, ("station_id", "metric_code", "period_type", "period_start_hst", "analysis_version")),
    "correlations": ({"station_id", "metric_x", "metric_y", "method", "date_start_hst", "date_end_hst", "paired_sample_count", "coefficient", "processing_run_id"}, ("station_id", "metric_x", "metric_y", "method", "date_start_hst", "date_end_hst")),
    "cluster_assignments": ({"model_id", "station_id", "date_hst", "cluster_label", "distance_to_centroid", "processing_run_id"}, ("model_id", "station_id", "date_hst")),
    "predictions": ({"model_id", "station_id", "issued_at", "target_date_hst", "feature_cutoff_date_hst", "target_metric", "horizon_days", "predicted_value", "predicted_lower", "predicted_upper", "actual_value", "absolute_error", "evaluated_at", "processing_run_id", "features_snapshot"}, ("model_id", "station_id", "issued_at", "target_date_hst", "target_metric")),
    "classifications": ({"model_id", "scheme_id", "station_id", "issued_at", "target_date_hst", "feature_cutoff_date_hst", "predicted_label", "confidence", "class_probabilities", "actual_label", "is_correct", "evaluated_at", "processing_run_id", "features_snapshot"}, ("model_id", "scheme_id", "station_id", "issued_at", "target_date_hst")),
}


def save_gold_results(conn, batch: GoldBatch) -> dict:
    allowed, keys = GOLD_COLUMNS[batch.table]
    for row in batch.rows:
        if not row or set(row) - allowed or not set(keys) <= set(row):
            raise ValueError("invalid Gold result columns")
        if row.get("station_id") != "51201":
            raise ValueError("only station 51201 is supported")
        upsert_row(conn, "gold", batch.table, row, keys)
    return {"saved": len(batch.rows), "table": batch.table}


def save_job_status(conn, job_id: int, status: str, output_record_count=None, error_message=None):
    result = conn.execute(
        "UPDATE gold.processing_runs SET status=%s, output_record_count=COALESCE(%s,output_record_count), "
        "error_message=%s, finished_at=CASE WHEN %s IN ('success','partial','failed','skipped') THEN now() ELSE NULL END "
        "WHERE processing_run_id=%s RETURNING processing_run_id, status",
        (status, output_record_count, error_message, status, job_id),
    ).fetchone()
    return result


def get_job_status(conn, job_id: int):
    return conn.execute("SELECT * FROM gold.processing_runs WHERE processing_run_id=%s", (job_id,)).fetchone()
