-- PostgreSQL checks referencing rows on every Raw delete. Build these online
-- before one-time duplicate pruning; they also speed lineage lookups.
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_silver_wave_raw_id
ON silver.wave_observations (raw_id) WHERE raw_id IS NOT NULL;
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_silver_revision_raw_id
ON silver.wave_observation_revisions (raw_id) WHERE raw_id IS NOT NULL;
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_quality_issue_raw_id
ON silver.data_quality_issues (raw_id) WHERE raw_id IS NOT NULL;
