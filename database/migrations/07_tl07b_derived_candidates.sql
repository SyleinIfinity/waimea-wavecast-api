-- Research-only derived estimates; never part of Bronze or observed Silver.
BEGIN;
CREATE TABLE IF NOT EXISTS gold.derived_imputation_candidates (
    station_id VARCHAR(12) NOT NULL REFERENCES bronze.stations(station_id),
    observation_time TIMESTAMPTZ NOT NULL,
    estimated_value DOUBLE PRECISION NOT NULL CHECK (estimated_value >= 0),
    source_type VARCHAR(12) NOT NULL DEFAULT 'IMPUTED' CHECK (source_type='IMPUTED'),
    imputation_method TEXT NOT NULL,
    model_version TEXT NOT NULL,
    estimated_at TIMESTAMPTZ NOT NULL,
    uncertainty DOUBLE PRECISION NOT NULL CHECK (uncertainty >= 0),
    quality_score DOUBLE PRECISION NOT NULL CHECK (quality_score BETWEEN 0 AND 1),
    provenance JSONB NOT NULL,
    gcs_uri TEXT NOT NULL CHECK (gcs_uri LIKE 'gs://waimea-noaa/datasets/processed/%'),
    gcs_generation BIGINT NOT NULL CHECK (gcs_generation > 0),
    state VARCHAR(12) NOT NULL DEFAULT 'candidate' CHECK (state IN ('candidate','reconciled','rejected')),
    observed_replacement_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (station_id, observation_time, model_version)
);
CREATE INDEX IF NOT EXISTS idx_derived_candidates_pending ON gold.derived_imputation_candidates
    (station_id,observation_time) WHERE state='candidate';
COMMIT;
