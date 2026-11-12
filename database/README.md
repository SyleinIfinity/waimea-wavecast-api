# Waimea WaveCast database

PostgreSQL 17, database `waimea_wavecast`. The authoritative schema is the
three SQL files in `migrations/`, copied without modification from the reviewed SQL v2 reference.
The scripts do not ingest NOAA observations or create GCS resources.

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
