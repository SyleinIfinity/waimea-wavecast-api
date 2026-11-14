-- Keep early-rollout late daily forecasts for audit, but exclude them from
-- training and aggregate skill until operational lead-time requirements hold.
BEGIN;
CREATE OR REPLACE VIEW gold.forward_training_examples AS
SELECT f.forecast_id, f.station_id, f.model_id, f.model_version, f.horizon,
       f.issued_at, f.anchor_at, f.anchor_date_hst, f.target_at, f.target_date_hst,
       f.features_snapshot, f.source_sha256, f.predicted_value,
       e.actual_value, e.absolute_error, e.persistence_absolute_error,
       e.truth_sha256, e.truth_revision_no, e.truth_as_of_at, e.evaluated_at
FROM gold.forward_forecasts f
JOIN LATERAL (
    SELECT * FROM gold.forward_evaluations
    WHERE forecast_id=f.forecast_id
    ORDER BY evaluated_at DESC,evaluation_id DESC LIMIT 1
) e ON true
WHERE f.gcs_uri IS NOT NULL AND f.quality_status='experimental'
  AND (
    (f.horizon='30min' AND f.target_at-f.issued_at BETWEEN interval '15 minutes' AND interval '45 minutes')
    OR (f.horizon='1d' AND f.target_at-f.issued_at > interval '20 hours')
    OR (f.horizon='7d' AND f.target_at-f.issued_at > interval '1 day')
  );
COMMIT;
