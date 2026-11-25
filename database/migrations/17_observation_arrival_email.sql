BEGIN;

CREATE TABLE IF NOT EXISTS gold.observation_arrival_outbox (
    station_id VARCHAR(12) NOT NULL,
    observed_at TIMESTAMPTZ NOT NULL,
    first_seen_at TIMESTAMPTZ NOT NULL,
    next_attempt_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    emailed_at TIMESTAMPTZ,
    PRIMARY KEY (station_id, observed_at)
);

CREATE INDEX IF NOT EXISTS idx_observation_arrival_pending
ON gold.observation_arrival_outbox (next_attempt_at, first_seen_at) WHERE emailed_at IS NULL;

CREATE OR REPLACE FUNCTION gold.queue_observation_arrival_email() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    INSERT INTO gold.observation_arrival_outbox (station_id, observed_at, first_seen_at)
    VALUES (NEW.station_id, NEW.observed_at, NEW.first_seen_at)
    ON CONFLICT (station_id, observed_at) DO NOTHING;
    IF FOUND THEN
        PERFORM pg_notify('waimea_observation_arrival', '');
    END IF;
    RETURN NEW;
END;
$$;

CREATE OR REPLACE TRIGGER queue_observation_arrival_email
AFTER INSERT ON silver.wave_observations
FOR EACH ROW
WHEN (NEW.station_id = '51201' AND NEW.source_kind = 'realtime' AND NEW.raw_id IS NOT NULL)
EXECUTE FUNCTION gold.queue_observation_arrival_email();

COMMIT;
