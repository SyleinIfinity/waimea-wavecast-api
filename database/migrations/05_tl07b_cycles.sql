-- Durable, single-station polling cycles and pinned GCS replay metadata.
BEGIN;
CREATE TABLE IF NOT EXISTS gold.pipeline_cycles (
    station_id VARCHAR(12) NOT NULL REFERENCES bronze.stations(station_id),
    slot_at TIMESTAMPTZ NOT NULL,
    status VARCHAR(24) NOT NULL DEFAULT 'PENDING' CHECK (status IN
        ('PENDING','RUNNING','NO_NEW_DATA','WAITING_FOR_SOURCE','RETRY_SCHEDULED','SUCCESS','FAILED')),
    attempt_count INTEGER NOT NULL DEFAULT 0 CHECK (attempt_count BETWEEN 0 AND 10),
    next_attempt_at TIMESTAMPTZ,
    lease_until TIMESTAMPTZ,
    lease_token UUID,
    last_poll_at TIMESTAMPTZ,
    latest_observed_at TIMESTAMPTZ,
    changed_from TIMESTAMPTZ,
    changed_to TIMESTAMPTZ,
    first_new_observation_at TIMESTAMPTZ,
    silver_inserted INTEGER NOT NULL DEFAULT 0,
    silver_revised INTEGER NOT NULL DEFAULT 0,
    gcs_uri TEXT,
    gcs_generation BIGINT,
    failure_stage TEXT,
    error_class TEXT,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (station_id,slot_at),
    CHECK (date_part('minute',slot_at) IN (0,30)),
    CHECK (date_part('second',slot_at)=0),
    CHECK ((gcs_uri IS NULL AND gcs_generation IS NULL) OR
           (gcs_uri IS NOT NULL AND gcs_generation IS NOT NULL)),
    CHECK (silver_inserted >= 0 AND silver_revised >= 0)
);
CREATE INDEX IF NOT EXISTS idx_pipeline_cycles_due ON gold.pipeline_cycles
    (next_attempt_at) WHERE status IN ('WAITING_FOR_SOURCE','RETRY_SCHEDULED','RUNNING');
COMMIT;
