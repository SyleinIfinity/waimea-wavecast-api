-- TL07-B additive quality counts and multi-resolution descriptive Gold.
BEGIN;
ALTER TABLE silver.daily_wave_features
    ADD COLUMN IF NOT EXISTS missing_count INTEGER NOT NULL DEFAULT 0 CHECK (missing_count >= 0),
    ADD COLUMN IF NOT EXISTS suspect_count INTEGER NOT NULL DEFAULT 0 CHECK (suspect_count >= 0),
    ADD COLUMN IF NOT EXISTS rejected_count INTEGER NOT NULL DEFAULT 0 CHECK (rejected_count >= 0),
    ADD COLUMN IF NOT EXISTS unverified_count INTEGER NOT NULL DEFAULT 0 CHECK (unverified_count >= 0);

CREATE TABLE IF NOT EXISTS gold.resolution_metrics (
    station_id VARCHAR(12) NOT NULL REFERENCES bronze.stations(station_id),
    resolution VARCHAR(12) NOT NULL CHECK (resolution IN ('30min','daily','weekly','monthly','seasonal','yearly')),
    period_start_at TIMESTAMPTZ NOT NULL,
    period_end_at TIMESTAMPTZ NOT NULL,
    metric_code VARCHAR(32) NOT NULL REFERENCES bronze.measurement_definitions(noaa_code),
    sample_count INTEGER NOT NULL CHECK (sample_count >= 0),
    valid_sample_count INTEGER NOT NULL CHECK (valid_sample_count >= 0),
    expected_sample_count INTEGER NOT NULL CHECK (expected_sample_count >= 0),
    missing_count INTEGER NOT NULL DEFAULT 0 CHECK (missing_count >= 0),
    suspect_count INTEGER NOT NULL DEFAULT 0 CHECK (suspect_count >= 0),
    rejected_count INTEGER NOT NULL DEFAULT 0 CHECK (rejected_count >= 0),
    unverified_count INTEGER NOT NULL DEFAULT 0 CHECK (unverified_count >= 0),
    completeness DOUBLE PRECISION NOT NULL CHECK (completeness BETWEEN 0 AND 1),
    readiness VARCHAR(32) NOT NULL CHECK (readiness IN ('GOLD_READY','GOLD_READY_WITH_IMPUTATION','WAITING_FOR_SOURCE','INSUFFICIENT_DATA','STALE_DATA','PROCESSING_FAILED')),
    mean_value DOUBLE PRECISION,
    median_value DOUBLE PRECISION,
    min_value DOUBLE PRECISION,
    max_value DOUBLE PRECISION,
    variance_value DOUBLE PRECISION,
    stddev_value DOUBLE PRECISION,
    rolling_mean DOUBLE PRECISION,
    trend_per_period DOUBLE PRECISION,
    residual_value DOUBLE PRECISION,
    calculation_version TEXT NOT NULL,
    computed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (station_id,resolution,period_start_at,metric_code),
    CHECK (period_end_at > period_start_at),
    CHECK (valid_sample_count <= sample_count)
);
CREATE INDEX IF NOT EXISTS idx_resolution_metrics_recent ON gold.resolution_metrics
    (station_id,resolution,period_start_at DESC);
COMMIT;
