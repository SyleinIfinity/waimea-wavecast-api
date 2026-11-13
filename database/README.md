# Waimea WaveCast database

PostgreSQL 17, database `waimea_wavecast`. `01_init_full.sql` is the baseline
v2 schema; numbered additive migrations 04–10 provide production pipeline,
quality, Gold and TL08-A export/job state. The SQL scripts do not ingest NOAA
observations or create GCS resources.

Choose **one** schema path for a database, using a migration-owner role:

* New database: `psql -v ON_ERROR_STOP=1 -d waimea_wavecast -f database/migrations/01_init_full.sql`
* Existing 21-table v1 database: back up first, then run `02_upgrade_from_v1.sql` instead.

Run `psql -v ON_ERROR_STOP=1 -d waimea_wavecast -f database/migrations/03_verify.sql`
after either path. Inspect the printed table, view, trigger, and row counts;
the final `PASS` notice alone does not assert exact view/trigger counts.
Do not run both initialization paths routinely. `IF NOT EXISTS` cannot repair
arbitrary manual schema drift. Never use these scripts to drop data.

The API role needs `USAGE` on all three schemas; SELECT on public read tables;
INSERT/UPDATE on its write tables; and USAGE on identity sequences. Grant
these explicitly from the migration owner. Keep credentials in environment
variables or a secret store, never in SQL or Git.

The older `migrations/001_init.sql` is a scaffold with an incompatible table
layout. Do not run it against the v2 `waimea_wavecast` database. Use the v2
files above for new installs or the documented v1 upgrade path.

After the baseline or v1 upgrade, apply 04–10 in numeric order. Migration 09
creates `bronze.historical_backfill_jobs` and `gold.dataset_exports` for
source checkpoints and immutable Bronze/Silver/Gold GCS export manifests.
Migration 10 adds durable yearly publication state. A changed historical
source enqueues its year and the preceding HST year; the scheduled worker
marks each year verified only after both year manifest and coverage objects
round-trip through GCS.
It is additive; back up Cloud SQL before applying it and validate on an
isolated PostgreSQL 17 instance first. Do not drop those tables on image
rollback: they preserve recovery state and content-version provenance.
