-- Realtime NOAA files are rolling snapshots. Keep each original object on GCS
-- but permit indexed lookup of an identical Raw reading across source files.
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_raw_station_observed_hash
ON bronze.raw_observations (station_id, observed_at, row_sha256)
WHERE observed_at IS NOT NULL;
