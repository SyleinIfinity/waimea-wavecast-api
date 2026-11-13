BEGIN;
CREATE TABLE IF NOT EXISTS gold.history_publications (
  year INTEGER PRIMARY KEY CHECK (year BETWEEN 2004 AND 2100),
  state TEXT NOT NULL DEFAULT 'PENDING' CHECK (state IN ('PENDING','VERIFIED')),
  manifest_sha256 CHAR(64),
  manifest_gcs_uri TEXT,
  manifest_generation BIGINT,
  coverage_sha256 CHAR(64),
  coverage_gcs_uri TEXT,
  coverage_generation BIGINT,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  verified_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_history_publications_pending
  ON gold.history_publications(updated_at) WHERE state='PENDING';
INSERT INTO gold.history_publications(year,state)
SELECT 2026,'PENDING' WHERE EXISTS (
  SELECT 1 FROM bronze.historical_backfill_jobs
  WHERE source_period LIKE '2026%' AND status='VERIFIED'
) ON CONFLICT DO NOTHING;
COMMIT;
