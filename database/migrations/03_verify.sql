-- ============================================================================
-- WAIMEA WAVECAST - VERIFY INITIALIZATION/MIGRATION (READ ONLY)
-- Run while connected to: waimea_wavecast. No data is inserted/updated/deleted.
-- ============================================================================
SELECT current_database() AS connected_database,
       current_user AS connected_role,
       current_setting('server_version') AS postgres_version,
       current_setting('TimeZone') AS session_timezone;

SELECT table_schema, COUNT(*) AS table_count
FROM information_schema.tables
WHERE table_schema IN ('bronze','silver','gold') AND table_type='BASE TABLE'
GROUP BY table_schema
ORDER BY table_schema;
-- Expected: bronze=6, silver=5, gold=14 => total=25 (unless you add custom tables).

SELECT table_schema, table_name
FROM information_schema.tables
WHERE table_schema IN ('bronze','silver','gold') AND table_type='BASE TABLE'
ORDER BY table_schema, table_name;

SELECT table_schema, table_name
FROM information_schema.views
WHERE table_schema IN ('bronze','silver','gold')
ORDER BY table_schema, table_name;
-- Expected: 5 views (1 silver, 4 gold).

SELECT station_id, station_name, local_timezone, expected_interval_min
FROM bronze.stations;
-- Expected: metadata station 51201. Historical measurements are not inserted by DDL.

SELECT feed_key, data_priority, poll_interval_minutes, enabled
FROM bronze.source_feeds
ORDER BY data_priority DESC;
-- Expected: historical_annual, historical_monthly, realtime.

SELECT noaa_code, silver_column, unit
FROM bronze.measurement_definitions
ORDER BY noaa_code;
-- Expected: 14 definitions.

SELECT event_object_schema, event_object_table, trigger_name
FROM information_schema.triggers
WHERE event_object_schema='silver' AND event_object_table='wave_observations'
ORDER BY trigger_name;
-- Expected: two triggers: trg_wave_revision_prepare, trg_wave_revision_audit.

SELECT COUNT(*) AS bronze_raw_rows FROM bronze.raw_observations;
SELECT COUNT(*) AS silver_observations FROM silver.wave_observations;
SELECT COUNT(*) AS silver_daily_features FROM silver.daily_wave_features;
SELECT COUNT(*) AS model_registry_rows FROM gold.model_registry;
SELECT COUNT(*) AS dataset_split_rows FROM gold.dataset_splits;
SELECT COUNT(*) AS evaluation_rows FROM gold.model_evaluations;

-- Throw an exception if a required physical table or view is missing.
DO $verify_waimea$
DECLARE missing_tables TEXT;
BEGIN
    SELECT string_agg(expected.schema_name || '.' || expected.table_name, ', ' ORDER BY expected.schema_name, expected.table_name)
    INTO missing_tables
    FROM (VALUES
      ('bronze','stations'), ('bronze','measurement_definitions'), ('bronze','source_feeds'),
      ('bronze','ingestion_runs'), ('bronze','source_files'), ('bronze','raw_observations'),
      ('silver','wave_observations'), ('silver','wave_observation_revisions'),
      ('silver','data_quality_issues'), ('silver','observation_gaps'), ('silver','daily_wave_features'),
      ('gold','processing_runs'), ('gold','time_series_metrics'), ('gold','trend_seasonal_components'),
      ('gold','correlations'), ('gold','model_registry'), ('gold','cluster_centroids'),
      ('gold','cluster_assignments'), ('gold','classification_schemes'),
      ('gold','predictions'), ('gold','classifications'),
      ('gold','dataset_splits'), ('gold','training_runs'),
      ('gold','model_evaluations'), ('gold','evaluation_samples')
    ) AS expected(schema_name,table_name)
    LEFT JOIN information_schema.tables AS actual
      ON actual.table_schema=expected.schema_name
     AND actual.table_name=expected.table_name
     AND actual.table_type='BASE TABLE'
    WHERE actual.table_name IS NULL;

    IF missing_tables IS NOT NULL THEN
        RAISE EXCEPTION 'Waimea database missing tables: %', missing_tables;
    END IF;

    IF (SELECT COUNT(*) FROM bronze.measurement_definitions) < 14 THEN
        RAISE EXCEPTION 'Expected 14 NOAA metadata definitions';
    END IF;
    IF (SELECT COUNT(*) FROM bronze.source_feeds WHERE station_id='51201') < 3 THEN
        RAISE EXCEPTION 'Expected 3 NOAA feed definitions';
    END IF;

    IF to_regclass('gold.v_model_evaluation_summary') IS NULL THEN
        RAISE EXCEPTION 'Missing gold.v_model_evaluation_summary';
    END IF;
    RAISE NOTICE 'PASS: Waimea v2 required tables, seed metadata and evaluation view exist';
END;
$verify_waimea$;
