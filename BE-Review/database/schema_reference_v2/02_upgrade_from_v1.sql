-- Waimea WaveCast v1 -> v2 additive migration for Cloud SQL PostgreSQL 17.
-- Run ONLY if v1 schema (21 tables) exists. Do not use to create a new DB.
-- No DROP / TRUNCATE, historical observations are preserved.
BEGIN;

-- ============================================================================
-- V2.0 ADDITIONS: GCS provenance + explicit ML experiment/evaluation tracking
-- Compatible with the existing v1.0 schema; no DROP/TRUNCATE.
-- Run this block through 02_upgrade_from_v1.sql on an existing v1 instance.
-- ============================================================================

-- Google Cloud Storage object identity: URI is a logical address; generation
-- identifies an exact GCS object version (both must be persisted after upload).
-- Keep SHA-256 of downloaded source bytes separately from GCS CRC32C.
ALTER TABLE bronze.source_files
    ADD COLUMN IF NOT EXISTS gcs_bucket TEXT,
    ADD COLUMN IF NOT EXISTS gcs_object_name TEXT,
    ADD COLUMN IF NOT EXISTS gcs_generation TEXT,
    ADD COLUMN IF NOT EXISTS gcs_crc32c TEXT,
    ADD COLUMN IF NOT EXISTS gcs_uploaded_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS gcs_last_verified_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS source_etag TEXT,
    ADD COLUMN IF NOT EXISTS source_last_modified_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS content_type TEXT,
    ADD COLUMN IF NOT EXISTS gcs_upload_status VARCHAR(16) NOT NULL DEFAULT 'pending';

-- New columns use separate named constraints; DO block avoids duplicate DDL
-- when scripts are executed again against the same database.
DO $v2_constraints$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid='bronze.source_files'::regclass
                   AND conname='chk_source_file_gcs_upload_status') THEN
        ALTER TABLE bronze.source_files
            ADD CONSTRAINT chk_source_file_gcs_upload_status
            CHECK (gcs_upload_status IN ('pending','uploaded','verified','failed','not_applicable'));
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid='bronze.source_files'::regclass
                   AND conname='chk_source_file_gcs_generation') THEN
        ALTER TABLE bronze.source_files
            ADD CONSTRAINT chk_source_file_gcs_generation
            CHECK (gcs_generation IS NULL OR gcs_generation ~ '^[0-9]+$');
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid='bronze.source_files'::regclass
                   AND conname='chk_source_file_gcs_uri') THEN
        ALTER TABLE bronze.source_files
            ADD CONSTRAINT chk_source_file_gcs_uri
            CHECK (gcs_uri IS NULL OR gcs_uri LIKE 'gs://%');
    END IF;
END;
$v2_constraints$;

CREATE INDEX IF NOT EXISTS idx_bronze_file_gcs_uri
    ON bronze.source_files (gcs_uri) WHERE gcs_uri IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_bronze_file_upload_status
    ON bronze.source_files (gcs_upload_status, fetched_at DESC);

-- Additional Silver daily ML features. These values must be computed from
-- actual normalized observations, never populated by fabricated defaults.
ALTER TABLE silver.daily_wave_features
    ADD COLUMN IF NOT EXISTS mean_average_period_s DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS max_dominant_period_s DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS mean_wind_speed_ms DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS mean_air_temperature_c DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS mean_sea_level_pressure_hpa DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS valid_dominant_period_count INTEGER,
    ADD COLUMN IF NOT EXISTS source_data_max_updated_at TIMESTAMPTZ;

DO $v2_silver_constraints$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid='silver.daily_wave_features'::regclass
                   AND conname='chk_daily_valid_dominant_count') THEN
        ALTER TABLE silver.daily_wave_features
            ADD CONSTRAINT chk_daily_valid_dominant_count
            CHECK (valid_dominant_period_count IS NULL OR
                  (valid_dominant_period_count >= 0 AND valid_dominant_period_count <= sample_count));
    END IF;
END;
$v2_silver_constraints$;

-- An artifact's model_version alone is NOT the GCS object generation.
-- A manifest links an immutable model artifact to a dataset snapshot/code build.
ALTER TABLE gold.model_registry
    ADD COLUMN IF NOT EXISTS artifact_generation TEXT,
    ADD COLUMN IF NOT EXISTS artifact_sha256 VARCHAR(64),
    ADD COLUMN IF NOT EXISTS artifact_size_bytes BIGINT,
    ADD COLUMN IF NOT EXISTS preprocessor_gcs_uri TEXT,
    ADD COLUMN IF NOT EXISTS preprocessor_generation TEXT,
    ADD COLUMN IF NOT EXISTS requirements_gcs_uri TEXT,
    ADD COLUMN IF NOT EXISTS model_card_gcs_uri TEXT,
    ADD COLUMN IF NOT EXISTS feature_schema_sha256 VARCHAR(64),
    ADD COLUMN IF NOT EXISTS python_version TEXT,
    ADD COLUMN IF NOT EXISTS promoted_at TIMESTAMPTZ;

DO $v2_model_constraints$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid='gold.model_registry'::regclass
                   AND conname='chk_model_artifact_generation') THEN
        ALTER TABLE gold.model_registry
            ADD CONSTRAINT chk_model_artifact_generation
            CHECK (artifact_generation IS NULL OR artifact_generation ~ '^[0-9]+$');
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid='gold.model_registry'::regclass
                   AND conname='chk_model_artifact_hash') THEN
        ALTER TABLE gold.model_registry
            ADD CONSTRAINT chk_model_artifact_hash
            CHECK (artifact_sha256 IS NULL OR artifact_sha256 ~ '^[a-fA-F0-9]{64}$');
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid='gold.model_registry'::regclass
                   AND conname='chk_model_artifact_size') THEN
        ALTER TABLE gold.model_registry
            ADD CONSTRAINT chk_model_artifact_size
            CHECK (artifact_size_bytes IS NULL OR artifact_size_bytes >= 0);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conrelid='gold.model_registry'::regclass
                   AND conname='chk_model_feature_schema_hash') THEN
        ALTER TABLE gold.model_registry
            ADD CONSTRAINT chk_model_feature_schema_hash
            CHECK (feature_schema_sha256 IS NULL OR feature_schema_sha256 ~ '^[a-fA-F0-9]{64}$');
    END IF;
END;
$v2_model_constraints$;

-- 1/4: Immutable description of a time-aware train/validation/test dataset.
-- Physical Parquet/CSV snapshot goes to GCS; only metadata is persisted here.
-- Clustering may omit validation/test windows; prediction/classification should
-- use chronological windows with no target leakage.
CREATE TABLE IF NOT EXISTS gold.dataset_splits (
    dataset_split_id           BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
    dataset_name               TEXT NOT NULL,
    dataset_version            TEXT NOT NULL,
    station_id                 VARCHAR(12) NOT NULL REFERENCES bronze.stations(station_id),
    task_type                  VARCHAR(16) NOT NULL,
    target_metric              VARCHAR(32) REFERENCES bronze.measurement_definitions(noaa_code),
    target_horizon_days        SMALLINT CHECK (target_horizon_days > 0),
    observation_granularity    VARCHAR(12) NOT NULL DEFAULT 'day_hst',
    feature_window_days        INTEGER CHECK (feature_window_days > 0),
    feature_columns            JSONB NOT NULL DEFAULT '[]'::jsonb,
    label_definition           JSONB NOT NULL DEFAULT '{}'::jsonb,
    preprocessing_parameters   JSONB NOT NULL DEFAULT '{}'::jsonb,
    split_method               VARCHAR(32) NOT NULL DEFAULT 'chronological',
    train_start_hst            DATE NOT NULL,
    train_end_hst              DATE NOT NULL,
    validation_start_hst       DATE,
    validation_end_hst         DATE,
    test_start_hst             DATE,
    test_end_hst               DATE,
    train_sample_count         INTEGER CHECK (train_sample_count >= 0),
    validation_sample_count    INTEGER CHECK (validation_sample_count >= 0),
    test_sample_count          INTEGER CHECK (test_sample_count >= 0),
    data_cutoff_at             TIMESTAMPTZ,
    dataset_gcs_uri            TEXT,
    dataset_gcs_generation     TEXT,
    dataset_sha256             VARCHAR(64),
    code_commit_sha            TEXT,
    created_at                 TIMESTAMPTZ NOT NULL DEFAULT now(),
    notes                      TEXT,
    CONSTRAINT uq_dataset_name_version UNIQUE (dataset_name, dataset_version),
    CONSTRAINT chk_dataset_task CHECK (task_type IN ('regression','classification','clustering')),
    CONSTRAINT chk_dataset_granularity CHECK (observation_granularity IN ('day_hst','hour_utc','observation')),
    CONSTRAINT chk_dataset_split_method CHECK (split_method IN ('chronological','walk_forward','expanding_window','other')),
    CONSTRAINT chk_dataset_train_range CHECK (train_start_hst <= train_end_hst),
    CONSTRAINT chk_dataset_val_range CHECK (
        (validation_start_hst IS NULL AND validation_end_hst IS NULL) OR
        (validation_start_hst IS NOT NULL AND validation_end_hst IS NOT NULL
         AND validation_start_hst <= validation_end_hst)),
    CONSTRAINT chk_dataset_test_range CHECK (
        (test_start_hst IS NULL AND test_end_hst IS NULL) OR
        (test_start_hst IS NOT NULL AND test_end_hst IS NOT NULL
         AND test_start_hst <= test_end_hst)),
    CONSTRAINT chk_dataset_nonleakage CHECK (
        (validation_start_hst IS NULL OR train_end_hst < validation_start_hst)
        AND (test_start_hst IS NULL OR
             COALESCE(validation_end_hst, train_end_hst) < test_start_hst)),
    CONSTRAINT chk_dataset_gcs_uri CHECK (dataset_gcs_uri IS NULL OR dataset_gcs_uri LIKE 'gs://%'),
    CONSTRAINT chk_dataset_gcs_generation CHECK (dataset_gcs_generation IS NULL OR dataset_gcs_generation ~ '^[0-9]+$'),
    CONSTRAINT chk_dataset_sha256 CHECK (dataset_sha256 IS NULL OR dataset_sha256 ~ '^[a-fA-F0-9]{64}$'),
    CONSTRAINT chk_dataset_features_array CHECK (jsonb_typeof(feature_columns) = 'array'),
    CONSTRAINT chk_dataset_label_object CHECK (jsonb_typeof(label_definition) = 'object'),
    CONSTRAINT chk_dataset_preprocessing_object CHECK (jsonb_typeof(preprocessing_parameters) = 'object')
);

CREATE INDEX IF NOT EXISTS idx_gold_dataset_split_task
    ON gold.dataset_splits (task_type, station_id, created_at DESC);

-- 2/4: Training jobs are historical evidence. A trained model can be added to
-- model_registry after GCS artifact upload, then model_id attached to this run.
CREATE TABLE IF NOT EXISTS gold.training_runs (
    training_run_id            BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
    dataset_split_id           BIGINT NOT NULL REFERENCES gold.dataset_splits(dataset_split_id) ON DELETE RESTRICT,
    model_id                   BIGINT REFERENCES gold.model_registry(model_id) ON DELETE SET NULL,
    processing_run_id          BIGINT REFERENCES gold.processing_runs(processing_run_id) ON DELETE SET NULL,
    run_name                   TEXT,
    task_type                  VARCHAR(16) NOT NULL,
    algorithm                  TEXT NOT NULL,
    status                     VARCHAR(16) NOT NULL DEFAULT 'running',
    random_seed                INTEGER,
    hyperparameters            JSONB NOT NULL DEFAULT '{}'::jsonb,
    software_versions          JSONB NOT NULL DEFAULT '{}'::jsonb,
    feature_pipeline_version   TEXT,
    code_commit_sha            TEXT,
    log_gcs_uri                TEXT,
    host_identifier            TEXT,
    started_at                 TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at                TIMESTAMPTZ,
    input_sample_count         INTEGER CHECK (input_sample_count >= 0),
    training_duration_seconds  DOUBLE PRECISION CHECK (training_duration_seconds >= 0),
    error_type                 TEXT,
    error_message              TEXT,
    notes                      TEXT,
    CONSTRAINT chk_training_task CHECK (task_type IN ('regression','classification','clustering')),
    CONSTRAINT chk_training_status CHECK (status IN ('running','success','partial','failed','cancelled')),
    CONSTRAINT chk_training_time CHECK (finished_at IS NULL OR finished_at >= started_at),
    CONSTRAINT chk_training_log_gcs CHECK (log_gcs_uri IS NULL OR log_gcs_uri LIKE 'gs://%'),
    CONSTRAINT chk_training_params_object CHECK (jsonb_typeof(hyperparameters) = 'object'),
    CONSTRAINT chk_training_software_object CHECK (jsonb_typeof(software_versions) = 'object')
);
CREATE INDEX IF NOT EXISTS idx_gold_training_runs_recent
    ON gold.training_runs (started_at DESC);
CREATE INDEX IF NOT EXISTS idx_gold_training_runs_model
    ON gold.training_runs (model_id, started_at DESC) WHERE model_id IS NOT NULL;

-- 3/4: Metrics for exactly one stage/variant of a training run. Use NULL for
-- metrics not applicable to the task; JSONB holds additional metrics.
CREATE TABLE IF NOT EXISTS gold.model_evaluations (
    evaluation_id              BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
    training_run_id            BIGINT NOT NULL REFERENCES gold.training_runs(training_run_id) ON DELETE RESTRICT,
    model_id                   BIGINT REFERENCES gold.model_registry(model_id) ON DELETE SET NULL,
    dataset_split_id           BIGINT NOT NULL REFERENCES gold.dataset_splits(dataset_split_id),
    evaluation_name            TEXT NOT NULL DEFAULT 'default',
    evaluation_stage           VARCHAR(16) NOT NULL,
    task_type                  VARCHAR(16) NOT NULL,
    sample_count               INTEGER NOT NULL CHECK (sample_count >= 0),
    mae                        DOUBLE PRECISION CHECK (mae IS NULL OR mae >= 0),
    mse                        DOUBLE PRECISION CHECK (mse IS NULL OR mse >= 0),
    rmse                       DOUBLE PRECISION CHECK (rmse IS NULL OR rmse >= 0),
    r2_score                   DOUBLE PRECISION,
    accuracy                   DOUBLE PRECISION CHECK (accuracy IS NULL OR accuracy BETWEEN 0 AND 1),
    precision_macro            DOUBLE PRECISION CHECK (precision_macro IS NULL OR precision_macro BETWEEN 0 AND 1),
    recall_macro               DOUBLE PRECISION CHECK (recall_macro IS NULL OR recall_macro BETWEEN 0 AND 1),
    f1_macro                   DOUBLE PRECISION CHECK (f1_macro IS NULL OR f1_macro BETWEEN 0 AND 1),
    silhouette_score           DOUBLE PRECISION CHECK (silhouette_score IS NULL OR silhouette_score BETWEEN -1 AND 1),
    davies_bouldin_score       DOUBLE PRECISION CHECK (davies_bouldin_score IS NULL OR davies_bouldin_score >= 0),
    calinski_harabasz_score    DOUBLE PRECISION CHECK (calinski_harabasz_score IS NULL OR calinski_harabasz_score >= 0),
    confusion_matrix           JSONB,
    additional_metrics         JSONB NOT NULL DEFAULT '{}'::jsonb,
    baseline_name              TEXT,
    baseline_metrics           JSONB NOT NULL DEFAULT '{}'::jsonb,
    evaluation_parameters      JSONB NOT NULL DEFAULT '{}'::jsonb,
    details_gcs_uri            TEXT,
    evaluated_at               TIMESTAMPTZ NOT NULL DEFAULT now(),
    notes                      TEXT,
    CONSTRAINT uq_model_eval_stage UNIQUE (training_run_id, evaluation_stage, evaluation_name),
    CONSTRAINT chk_eval_stage CHECK (evaluation_stage IN ('train','validation','test','walk_forward','backtest','live')),
    CONSTRAINT chk_eval_task CHECK (task_type IN ('regression','classification','clustering')),
    CONSTRAINT chk_eval_details_gcs CHECK (details_gcs_uri IS NULL OR details_gcs_uri LIKE 'gs://%'),
    CONSTRAINT chk_eval_additional_object CHECK (jsonb_typeof(additional_metrics) = 'object'),
    CONSTRAINT chk_eval_baseline_object CHECK (jsonb_typeof(baseline_metrics) = 'object'),
    CONSTRAINT chk_eval_params_object CHECK (jsonb_typeof(evaluation_parameters) = 'object'),
    CONSTRAINT chk_eval_confusion_array CHECK (confusion_matrix IS NULL OR jsonb_typeof(confusion_matrix) = 'array')
);
CREATE INDEX IF NOT EXISTS idx_gold_model_evaluations_recent
    ON gold.model_evaluations (evaluated_at DESC, task_type);
CREATE INDEX IF NOT EXISTS idx_gold_model_evaluations_model
    ON gold.model_evaluations (model_id, evaluation_stage, evaluated_at DESC);

-- 4/4: Fine-grained regression/classification evidence from held-out data.
-- Keep only evaluation samples the project needs in SQL; large detail Parquet
-- may instead be stored in GCS and linked via model_evaluations.details_gcs_uri.
CREATE TABLE IF NOT EXISTS gold.evaluation_samples (
    evaluation_sample_id       BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
    evaluation_id              BIGINT NOT NULL REFERENCES gold.model_evaluations(evaluation_id) ON DELETE CASCADE,
    station_id                 VARCHAR(12) NOT NULL REFERENCES bronze.stations(station_id),
    target_date_hst            DATE NOT NULL,
    target_name                TEXT NOT NULL,
    horizon_days               SMALLINT NOT NULL DEFAULT 1 CHECK (horizon_days > 0),
    actual_value               DOUBLE PRECISION,
    predicted_value            DOUBLE PRECISION,
    residual                   DOUBLE PRECISION GENERATED ALWAYS AS (
        CASE WHEN actual_value IS NOT NULL AND predicted_value IS NOT NULL
             THEN actual_value - predicted_value END) STORED,
    absolute_error             DOUBLE PRECISION GENERATED ALWAYS AS (
        CASE WHEN actual_value IS NOT NULL AND predicted_value IS NOT NULL
             THEN abs(actual_value - predicted_value) END) STORED,
    actual_label               TEXT,
    predicted_label            TEXT,
    is_correct                 BOOLEAN GENERATED ALWAYS AS (
        CASE WHEN actual_label IS NOT NULL AND predicted_label IS NOT NULL
             THEN actual_label = predicted_label END) STORED,
    prediction_probabilities   JSONB NOT NULL DEFAULT '{}'::jsonb,
    feature_cutoff_date_hst    DATE,
    feature_snapshot           JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at                 TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_evaluation_target UNIQUE (evaluation_id, station_id, target_date_hst, target_name, horizon_days),
    CONSTRAINT chk_eval_sample_has_pair CHECK (
         (actual_value IS NOT NULL AND predicted_value IS NOT NULL)
         OR (actual_label IS NOT NULL AND predicted_label IS NOT NULL)),
    CONSTRAINT chk_eval_sample_feature_cutoff CHECK (
         feature_cutoff_date_hst IS NULL OR feature_cutoff_date_hst < target_date_hst),
    CONSTRAINT chk_eval_sample_prob_object CHECK (jsonb_typeof(prediction_probabilities) = 'object'),
    CONSTRAINT chk_eval_sample_feature_object CHECK (jsonb_typeof(feature_snapshot) = 'object')
);
CREATE INDEX IF NOT EXISTS idx_gold_eval_samples_eval_date
    ON gold.evaluation_samples (evaluation_id, target_date_hst);
CREATE INDEX IF NOT EXISTS idx_gold_eval_samples_station_target
    ON gold.evaluation_samples (station_id, target_date_hst DESC);

-- Helpful visibility for model selection; does not depend on any saved artifact
-- being publicly accessible. Private GCS access is handled by IAM in Backend.
CREATE OR REPLACE VIEW gold.v_model_evaluation_summary AS
SELECT e.evaluation_id, e.training_run_id, e.model_id,
       m.model_name, m.model_version,
       e.dataset_split_id, d.dataset_name, d.dataset_version,
       e.task_type, e.evaluation_stage, e.evaluation_name,
       e.sample_count, e.mae, e.rmse, e.r2_score,
       e.accuracy, e.precision_macro, e.recall_macro, e.f1_macro,
       e.silhouette_score, e.davies_bouldin_score,
       e.baseline_name, e.baseline_metrics, e.additional_metrics,
       e.evaluated_at
FROM gold.model_evaluations AS e
LEFT JOIN gold.model_registry AS m ON m.model_id = e.model_id
JOIN gold.dataset_splits AS d ON d.dataset_split_id = e.dataset_split_id;

-- Descriptive comments are visible in DBeaver and pg_catalog.
COMMENT ON SCHEMA bronze IS 'NOAA 51201 file provenance, ingestion audits and raw rows; GCS holds source files';
COMMENT ON SCHEMA silver IS 'Normalized NOAA observations, revision history and Hawaii-local daily features';
COMMENT ON SCHEMA gold IS 'Dashboard patterns, reproducible ML training/evaluation metadata and inference results';
COMMENT ON TABLE bronze.source_files IS 'Immutable NOAA source file hashes and optional precise GCS object generations';
COMMENT ON TABLE silver.wave_observations IS 'One canonical normalized NOAA sample per station and UTC timestamp';
COMMENT ON TABLE silver.wave_observation_revisions IS 'Audit trail of source/QC/value changes; never wipe during dashboard demo';
COMMENT ON TABLE gold.dataset_splits IS 'Time-aware ML dataset snapshots and reproducible split ranges';
COMMENT ON TABLE gold.training_runs IS 'Training histories; saved models are referenced via gold.model_registry';
COMMENT ON TABLE gold.model_evaluations IS 'Metrics per training run and evaluation stage, with baseline comparisons';
COMMENT ON TABLE gold.evaluation_samples IS 'Actual vs predicted samples; residual and correctness are generated';
COMMENT ON COLUMN gold.dataset_splits.data_cutoff_at IS 'Latest NOAA observation/processing timestamp eligible for this immutable dataset snapshot';
COMMENT ON COLUMN bronze.source_files.gcs_generation IS 'Exact GCS generation; URI alone is not a version identifier';

COMMIT;
