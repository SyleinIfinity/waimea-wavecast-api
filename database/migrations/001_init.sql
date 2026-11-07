-- Apply once to an EMPTY database, as a trusted migration user.
CREATE SCHEMA IF NOT EXISTS bronze;
CREATE SCHEMA IF NOT EXISTS silver;
CREATE SCHEMA IF NOT EXISTS gold;

CREATE TABLE IF NOT EXISTS bronze.raw_batches (
  id BIGSERIAL PRIMARY KEY,
  source TEXT NOT NULL,
  station_id TEXT NOT NULL,
  fetched_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  sha256 TEXT NOT NULL,
  gcs_uri TEXT NOT NULL,
  UNIQUE (source, station_id, sha256)
);
CREATE TABLE IF NOT EXISTS bronze.ingestion_runs (
  id BIGSERIAL PRIMARY KEY,
  source TEXT NOT NULL,
  requested_at TIMESTAMPTZ NOT NULL,
  received_at TIMESTAMPTZ,
  status TEXT NOT NULL,
  new_records INTEGER NOT NULL DEFAULT 0,
  http_status INTEGER,
  error_message TEXT
);
CREATE TABLE IF NOT EXISTS silver.wave_observations (
  station_id TEXT NOT NULL,
  source TEXT NOT NULL,
  observed_at TIMESTAMPTZ NOT NULL,
  wave_height_m DOUBLE PRECISION,
  dominant_period_s DOUBLE PRECISION,
  wave_direction_deg DOUBLE PRECISION,
  sea_temperature_c DOUBLE PRECISION,
  quality_flag TEXT NOT NULL DEFAULT 'unverified',
  PRIMARY KEY(station_id, source, observed_at)
);
CREATE TABLE IF NOT EXISTS silver.model_forecasts (
  provider TEXT NOT NULL,
  model_name TEXT NOT NULL,
  issued_at TIMESTAMPTZ NOT NULL,
  valid_at TIMESTAMPTZ NOT NULL,
  latitude DOUBLE PRECISION NOT NULL,
  longitude DOUBLE PRECISION NOT NULL,
  wave_height_m DOUBLE PRECISION,
  payload JSONB NOT NULL DEFAULT '{}'::jsonb,
  PRIMARY KEY(provider, model_name, issued_at, valid_at, latitude, longitude)
);
CREATE TABLE IF NOT EXISTS silver.daily_wave_features (
  date_hst DATE NOT NULL,
  source TEXT NOT NULL,
  features JSONB NOT NULL,
  sample_count INTEGER NOT NULL,
  coverage_ratio DOUBLE PRECISION NOT NULL,
  PRIMARY KEY(date_hst, source)
);
CREATE TABLE IF NOT EXISTS gold.analysis_patterns (
  kind TEXT NOT NULL,
  scope TEXT NOT NULL,
  source TEXT NOT NULL,
  payload JSONB NOT NULL,
  model_version TEXT,
  computed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY(kind, scope, source)
);
CREATE TABLE IF NOT EXISTS gold.model_registry (
  name TEXT NOT NULL,
  version TEXT NOT NULL,
  gcs_uri TEXT NOT NULL,
  metrics JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY(name, version)
);
