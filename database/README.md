# Waimea WaveCast database

PostgreSQL 17, database `waimea_wavecast`. `01_init_full.sql` is the baseline
v2 schema; numbered additive migrations 04–16 provide production pipeline,
quality, Gold, TL08-A export/job state and forward forecast evaluation. The SQL scripts do not ingest NOAA
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

After the baseline or v1 upgrade, apply 04–16 in numeric order. Migration 09
creates `bronze.historical_backfill_jobs` and `gold.dataset_exports` for
source checkpoints and immutable Bronze/Silver/Gold GCS export manifests.
Migration 10 adds durable yearly publication state. A changed historical
source enqueues its year and the preceding HST year; the scheduled worker
marks each year verified only after both year manifest and coverage objects
round-trip through GCS.
It is additive; back up Cloud SQL before applying it and validate on an
isolated PostgreSQL 17 instance first. Do not drop those tables on image
rollback: they preserve recovery state and content-version provenance.

Migration 11 adds forward-issued experimental forecasts, append-only NOAA
ground-truth evaluations, and `gold.forward_training_examples`. It does not
change Raw/Silver data or approved Gold predictions. Back up Cloud SQL first,
apply `11_tl08c_forward_forecasts.sql` once with PostgreSQL 17, then verify the
three new relations and the Database API `/health/ready` endpoint. Migration 12
updates the training view to exclude forecasts with insufficient lead time:
15–45 minutes for the short horizon, more than 20 hours for 1d, and more than
one day for 7d. Late forecasts remain in the audit tables. The view exposes
only finalized forward forecasts subsequently matched to QC-valid truth; it
feeds the scheduled controlled retraining job.

Migration 13 adds an isolated Calisthenics 1.5 shadow forecast/evaluation
ledger with immutable GCS pointers. It does not alter existing forward
training examples, observed Bronze/Silver data, or approved Gold predictions.
Back up and verify PostgreSQL 17 first; apply
`13_calisthenics_shadow.sql` before starting the shadow worker or its public
read-only API.
Migration 14 adds the indexed `(station_id, observed_at, row_sha256)` lookup
used to avoid storing the same realtime NOAA reading on every rolling snapshot.
Run its `CREATE INDEX CONCURRENTLY` outside a transaction. Migration 15 adds
`silver.revision_payload_archives`, the pinned GCS manifest for full historical
revision snapshots. Raw archive pointers use the existing verified
`gold.dataset_exports` table and leave Raw IDs and source FKs intact.
Migration 16 builds three `raw_id` indexes on the Silver and QC referencing
tables. Run its `CREATE INDEX CONCURRENTLY` statements outside a transaction
before the one-time duplicate cleanup; without them PostgreSQL checks a large
revision table for every deleted Raw row.
