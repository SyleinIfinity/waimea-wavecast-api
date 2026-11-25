BEGIN;

CREATE TABLE IF NOT EXISTS gold.observation_reset_backups (
    backup_id UUID PRIMARY KEY,
    environment TEXT NOT NULL CHECK (environment IN ('staging', 'production')),
    purpose TEXT NOT NULL CHECK (purpose IN ('reset', 'pre_restore')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    bucket TEXT NOT NULL,
    object_path TEXT NOT NULL,
    generation BIGINT NOT NULL CHECK (generation > 0),
    sha256 TEXT NOT NULL CHECK (sha256 ~ '^[0-9a-f]{64}$'),
    row_counts JSONB NOT NULL,
    UNIQUE (bucket, object_path)
);

CREATE INDEX IF NOT EXISTS idx_observation_reset_backup_latest
ON gold.observation_reset_backups (environment, created_at DESC, backup_id DESC)
WHERE purpose = 'reset';

-- Restoring a snapshot must preserve its revision numbers and timestamps.
-- Skip application side effects only on the transaction that replays the snapshot.
CREATE OR REPLACE TRIGGER trg_wave_revision_prepare
BEFORE INSERT OR UPDATE ON silver.wave_observations
FOR EACH ROW
WHEN (current_setting('waimea.restoring_observations', true) IS DISTINCT FROM 'on')
EXECUTE FUNCTION silver.prepare_wave_observation_revision();

CREATE OR REPLACE TRIGGER trg_wave_revision_audit
AFTER INSERT OR UPDATE ON silver.wave_observations
FOR EACH ROW
WHEN (current_setting('waimea.restoring_observations', true) IS DISTINCT FROM 'on')
EXECUTE FUNCTION silver.audit_wave_observation_revision();

CREATE OR REPLACE TRIGGER queue_observation_arrival_email
AFTER INSERT ON silver.wave_observations
FOR EACH ROW
WHEN (NEW.station_id = '51201' AND NEW.source_kind = 'realtime' AND NEW.raw_id IS NOT NULL
      AND current_setting('waimea.restoring_observations', true) IS DISTINCT FROM 'on')
EXECUTE FUNCTION gold.queue_observation_arrival_email();

COMMIT;
