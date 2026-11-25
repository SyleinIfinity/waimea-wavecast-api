-- Keep the existing public view shape, but expose only QC-approved observations.
BEGIN;
CREATE OR REPLACE VIEW gold.v_latest_observation AS
SELECT
  o.station_id, o.observed_at, o.wave_height_m, o.dominant_period_s,
  o.average_period_s, o.mean_wave_direction_deg, o.wind_speed_ms,
  o.sea_surface_temp_c, o.quality_status, o.source_kind,
  o.first_seen_at, o.last_seen_at,
  (o.observed_at AT TIME ZONE 'Pacific/Honolulu') AS observed_local_hst
FROM silver.wave_observations AS o
WHERE o.wave_height_m IS NOT NULL AND o.quality_status = 'valid'
ORDER BY o.observed_at DESC
LIMIT 1;
COMMIT;
