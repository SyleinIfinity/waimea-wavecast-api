# TL08-A — Historical NOAA Backfill and Multi-Layer GCS Data Lake

> Production evidence is recorded after the controlled import and independent
> reconciliation. `VERIFIED` below means Cloud SQL rows and pinned GCS object
> bytes were compared; a source file being listed by NOAA alone is not enough.

## 1. Executive Summary

TL08-A adds a single-worker, resumable historical import for NOAA NDBC station
51201. It preserves source bytes in the existing private GCS bucket, writes
Bronze and canonical Silver to Cloud SQL, applies QC, computes HST Daily
Features and six descriptive Gold resolutions, and exports every populated
month of each layer to versioned GCS objects. Source jobs and export manifests
are persisted in PostgreSQL. No training or prediction is enabled.

## 2. Existing Code Audit

The TL06 `run_ingest` already had authentic NOAA download, byte-addressed raw
GCS upload, source-file lineage, 500-row Raw/Silver chunks and feed-priority
upsert. TL07-B provided QC, Daily/Gold computations, realtime cycle leases and
the read-only 28-file NOAA coverage snapshot. `bounded_backfill.py` could run
one verified annual file but had no durable historical job, Silver/Gold GCS
exports or periodic correction detection. The production branch at task start
was `feature/tl06-Ingestion`; the working tree was clean. The 2004–2026
archive import did not exist in Cloud SQL before TL08-A.
The VM had about 17 GiB available RAM and 53 GiB free disk at the initial
check; Cloud SQL reported 15 sessions against `max_connections=400` during
the import. A sampled historical container used 75–101 MiB within its 1.5 GiB
limit. The pre-change realtime snapshot had 2,173 Silver observations.

## 3. Historical Source Discovery

The runner parses links from the live [NOAA 51201 station-history page](https://www.ndbc.noaa.gov/station_history.php?station=51201) and
accepts only validated NOAA annual/monthly filenames and matching official
URLs. It never treats a constructed URL as proof of publication. The TL07-B
snapshot and live listing both contained 28 source links at the start of this
run. A source is rechecked by HEAD metadata and a periodic downloaded SHA-256;
HEAD metadata alone never suppresses full checksum reconciliation forever.

## 4. Actual Archive Availability

The listed set has 22 annual `stdmet` files, 2004–2025, and six monthly files
for 2026: January, February, May, June, July and August. The existing read-only
parser audit found 312,702 parsed rows in those 28 files. The 2004 annual
archive starts on 2004-09-08 UTC; a file named for a year does not imply a
complete year. Source availability is independent of actual database import;
the latter is measured in the final coverage sections.

## 5. Parser and Normalization Compatibility

`packages/noaa/parser.py` accepts gzip/plain text, two/four-digit years,
headers with/without `#`, changing columns, and legacy `WD`/`BAR` aliases.
`normalize.py` maps NOAA field-specific sentinels (including equivalent
decimal forms such as `99.00`) to NULL and keeps wave, temperature, pressure
and direction units in the schema's documented units. Optional missing
meteorological fields do not reject a usable wave observation. The TL07-B
snapshot found zero normalized WSPD and TIDE values across all 28 files;
these fields are reported as unavailable rather than filled with zero. A
pinned GCS read of the authentic 2004 archive independently found WSPD
`99.0` in all 2,436 rows and TIDE `99.00` in all 2,436 rows, matching NOAA's
missing-value sentinels rather than a column-mapping error for that file.

## 6. RAW/Bronze Storage Architecture

Original NOAA bytes remain immutable under `noaa/raw/historical/` and
`noaa/raw/monthly/`. `bronze.source_files` stores URL, SHA-256, GCS URI and
generation. `bronze.raw_observations` keeps each source line and parsed raw
values. Monthly `datasets/bronze/station=51201/year=YYYY/month=MM/` gzip JSONL
exports include raw IDs, file IDs and joined source URL/hash/GCS lineage.
Production containers use Compute Engine ADC from the VM-attached service
account `879076836916-compute@developer.gserviceaccount.com` in project
`project-25a1dc1a-77da-4236-b82`; no JSON key environment variable is set.

## 7. Silver Storage Architecture

`silver.wave_observations` remains one canonical row per station/timestamp,
using the existing annual > monthly > realtime priority. QC status, flags,
missing fields, revision number, processing times and source lineage are
included in monthly `datasets/silver/` exports. Original measurements are
never replaced by imputed values.

## 8. Gold Storage Architecture

Existing HST-bound Daily Features and 30-minute, daily, weekly, monthly,
seasonal and yearly WVHT metrics are recomputed from QC-reviewed Silver.
Monthly `datasets/gold/` exports include both Daily Features and all six
resolution metric rows, with algorithm version, readiness, valid/missing/
excluded counts and source Silver export hashes. `GOLD_READY` remains
descriptive; it does not approve a forecast model.

## 9. PostgreSQL ↔ GCS Consistency

`gold.dataset_exports` is a durable outbox/manifest. The worker prepares a
content-keyed row, uploads with `if_generation_match=0`, downloads the pinned
GCS generation, verifies compressed SHA-256 and decompressed canonical JSONL,
reads the DB partition a second time, and only then marks the row active
`VERIFIED`. If DB or GCS fails, the pending record remains retryable. A later
independent publisher repeats the DB/GCS comparison for every populated
partition before writing a year manifest.
The publisher completes all UTC Bronze/Silver partitions, including next
January's Silver dependency for HST December, before any Gold partition of
the year. This prevents a Gold export from pinning a Silver version that is
replaced later in the same publication pass.
The exporter marks recoverable failures `RETRY_SCHEDULED` when the Database
API is reachable; if that metadata write also fails, the prepared row stays
durable as `PENDING_EXPORT` for the next reconciliation.

## 10. Dataset Versioning and Manifests

Canonical JSONL uses `tl08a-jsonl-v1`, sorted keys, UTC timestamps and
deterministic gzip (`mtime=0`). Object paths contain the compressed payload's
SHA-256. The hash is the dataset version. A repeated identical export reuses
the same object and manifest row. The PostgreSQL active pointer changes only
after verification; older versions remain. Immutable year manifests live at
`manifests/station=51201/historical/year=YYYY/{sha256}.json` and list source
files and every verified partition URI, checksum, generation and row count.

## 11. Incremental Backfill Algorithm

One bounded Compose worker lists NOAA sources, checks persisted jobs, skips
recently checked/unchanged sources, and imports only new or corrected files.
The 500-row Raw/Silver batches use existing idempotent keys. A source with
the same SHA-256 resumes after its last committed Silver batch offset. QC,
Daily/Gold and GCS exports are rerunnable; only changed content receives a
new GCS version. The worker remains independent of realtime polling.

## 12. Source Change Detection

For a verified source, the runner compares available ETag or Last-Modified
plus Content-Length. Defaults recheck metadata every 24 hours and force a
downloaded SHA-256 reconciliation at least every 30 days. NOAA headers may
be missing or unreliable, so SHA-256 is authoritative for a full check. A
changed hash creates a new immutable raw object, source-file registration,
Silver revisions and affected exports. A removed historical row is retained
for provenance and requires explicit review; the pipeline never deletes
production observations automatically.

## 13. Durable Jobs and Recovery

`bronze.historical_backfill_jobs` stores URL/period, source metadata and SHA,
source GCS URI/generation, batch offset, lease token, heartbeat, retry count,
failure stage and last exported partition. A worker renews its lease every
45 seconds. After a crash, the five-minute lease expires and a new worker
can claim the source. A failed source does not discard completed jobs for
other files. The checkpoint is after a committed Silver batch; an interrupted
uncheckpointed batch is replayed idempotently.
Migration 10 adds `gold.history_publications`: when a changed archive reaches
`VERIFIED`, its UTC year and the preceding HST year become `PENDING` in the
same database transaction. The daily service publishes at most two pending
years, creates a full versioned coverage report, checks both pinned GCS
generations, and records both hashes/generations before setting `VERIFIED`.
If publication or coverage fails, `PENDING` survives reboot for retry.

## 14. Production Import Execution

A private PostgreSQL 17 `pg_dump` was taken before migration 09 and validated
with PostgreSQL 17 `pg_restore --list`. Migration 09 was first tested on an
isolated PostgreSQL 17 container, then applied to Cloud SQL. Database API was
recreated without restarting Ingestion, Preprocessing, scheduler or unrelated
services. A controlled 2004 batch imported 2,436 Raw and 2,436 Silver rows
and verified 12 monthly layer artifacts across September–December 2004.
The full source import completed 28/28 listed archives, with 304,450 annual
and 8,252 monthly source rows. Cloud SQL has exactly 312,702 corresponding
historical Raw rows and 312,702 canonical historical Silver rows. The
historical Silver range is 2004-09-08 16:00 UTC through 2026-08-31 23:56 UTC.
Realtime Silver remained separate and continued advancing during the import.

## 15. Historical QC Results

QC uses the existing TL07-B checks and preserves missing optional fields.
Of 312,702 historical Silver rows, 312,686 are `valid`, 16 are `suspect`, and
none is `rejected` or `unverified` at the import checkpoint. The 16 suspect
rows match the discovery count of missing WVHT/DPD core fields; they remain
in Bronze/Silver and are excluded from valid wave statistics. Monthly counts
and field availability are in `TL08-A_COVERAGE.json` after capture. No
synthetic corrections are applied to Cloud SQL.

## 16. Historical Gold Rebuild

Each imported source triggers its affected HST year rebuild through the
existing six-resolution algorithm. The final reconciliation uses a cadence
inferred from at least 200 source intervals with at least 55% support; early
mostly hourly archives get an hourly denominator for Daily/Weekly/Monthly/
Seasonal/Yearly completeness, while the 30-minute resolution remains
insufficient when the archive reports hourly. Daily Features use Pacific/Honolulu day
boundaries. Aggregate readiness retains incomplete-period semantics. The
yearly GCS reconciliation also repairs any populated Gold month whose
active export no longer matches the database.
Measured modal cadence was 60 minutes in 2004 (99.01% interval support)
and 2005 (99.2%), versus 30 minutes in 2008 (93.33%). The final rebuild
set 2004–2007 Daily Feature denominators to 24 samples/day and kept the
30-minute Gold resolution insufficient for hourly source periods.

## 17. Per-Year and Per-Month Coverage

The machine-readable `TL08-A_COVERAGE.json` records every observed month,
Raw count, Silver count, QC statuses, available fields, correction revisions,
Gold resolutions and active export versions. Its 30-minute ratio is labelled
nominal: old NOAA reporting cadence and archive gaps may differ. Source
availability by year must not be interpreted as continuous temporal coverage.
Observed monthly discontinuities include 2004 January–August absent,
October 2008 absent, March–September 2018 absent, February 2021 absent, and
March/July 2023 absent. In 2026, only January, February, May–August archive
months and recent realtime September–November observations are present at the
time of this run. The gaps are recorded as gaps, not zero-valued waves.
Gold-only months can occur when a seasonal or yearly aggregate starts before
the first observation in its period; they do not imply Bronze/Silver coverage
for that calendar month.
The initial import created 31 active, zero-record monthly exports across the
three layers for archive gaps. They contain only a schema header, no synthetic
observations, and the year publisher excludes them from populated-partition
manifests. The runner now skips empty partitions on subsequent imports; old
immutable objects remain available for audit.

| Year | Archive months with observations | Historical Silver | QC valid | QC suspect |
|---:|---:|---:|---:|---:|
| 2004 | 4 | 2,436 | 2,436 | 0 |
| 2005 | 12 | 8,296 | 8,296 | 0 |
| 2006 | 12 | 8,596 | 8,596 | 0 |
| 2007 | 12 | 8,063 | 8,063 | 0 |
| 2008 | 11 | 13,025 | 13,024 | 1 |
| 2009 | 12 | 16,807 | 16,803 | 4 |
| 2010 | 12 | 15,722 | 15,720 | 2 |
| 2011 | 12 | 16,542 | 16,538 | 4 |
| 2012 | 12 | 16,614 | 16,614 | 0 |
| 2013 | 12 | 15,855 | 15,855 | 0 |
| 2014 | 12 | 17,121 | 17,121 | 0 |
| 2015 | 12 | 17,079 | 17,079 | 0 |
| 2016 | 12 | 16,107 | 16,107 | 0 |
| 2017 | 12 | 16,736 | 16,736 | 0 |
| 2018 | 5 | 6,037 | 6,037 | 0 |
| 2019 | 12 | 16,970 | 16,970 | 0 |
| 2020 | 12 | 16,491 | 16,491 | 0 |
| 2021 | 11 | 13,929 | 13,929 | 0 |
| 2022 | 12 | 16,540 | 16,540 | 0 |
| 2023 | 10 | 11,396 | 11,391 | 5 |
| 2024 | 12 | 16,869 | 16,869 | 0 |
| 2025 | 12 | 17,219 | 17,219 | 0 |
| 2026 | 6 monthly archives | 8,252 | 8,252 | 0 |
| **Total** | | **312,702** | **312,686** | **16** |

## 18. GCS Export Verification

Each `VERIFIED` monthly export was checked from inside a production Compose
worker with VM service-account ADC: pinned generation download, compressed
hash, decompressed canonical records, DB reread and row count. The yearly
publisher performs a separate pass before emitting the immutable GCS
manifest. It also downloads each original NOAA source generation, checks its
SHA-256 and matches its Cloud SQL source-file registration. The final pass
returned exit code 0 for all 23 years, checked 28 original source objects and
767 populated monthly artifacts, and wrote 23 immutable year manifests.
An independent container downloaded all 23 pinned manifest generations and
verified each SHA-256 and artifact count. The 31 old zero-record exports are
excluded from populated year manifests. Production uses VM Compute Engine ADC.

## 19. Repeat-Run Idempotency Evidence

The final `--all --force-check` repeat downloaded all 28 listed sources and
returned `UNCHANGED_SHA256` for every one (exit code 0). It performed no new
ingestion, QC or Gold rebuild. After the repeat, Cloud SQL still had 28
`VERIFIED` jobs, 28 historical source registrations and 312,702 historical
Raw rows. Active populated export count remained 767. Immutable source and
dataset objects were reused. `TL08-A_REPEAT_RUN.jsonl` records all outcomes.
The automated publication service was started twice in production. The first
run completed pending 2026 and stored a new year manifest and full coverage
snapshot. A separate Compose container downloaded both exact generations and
verified SHA-256. The second run exited 0 with zero pending publications;
the manifest and coverage generations remained unchanged.

## 20. Realtime Pipeline Isolation

The one-off backfill container has a 0.5 CPU / 1.5 GiB memory cap and checks
VM available memory (minimum 3 GiB) and disk (minimum 10 GiB) before each
source/partition. Only one historical worker is run. The 30-minute realtime
scheduler and its four core containers remain independent. The backfill does
not touch Minecraft or any unrelated service.

## 21. Automated Tests

The TL08-A tests inject DB-batch interruption, GCS upload failure, manifest
commit failure, a mid-export DB change and a corrected partition; they verify
retry, content-version behavior, Silver-before-Gold dependencies and the
daily full-SHA download limit. Scheduled-publication tests verify that a
coverage upload failure leaves publication pending and that a no-op run
does not rescan or upload.
Existing TL06/TL07 tests cover NOAA 404, 429/backoff, parser/normalizer
variants, source priority and QC/Gold rules. An isolated PostgreSQL 17 API
integration checked job claim/checkpoint, source reactivation and export
prepare/complete/active. The final full suite passed 171 tests; two live-DB
tests were skipped because the local test environment has no `DB_PASSWORD`.
The isolated PostgreSQL 17 checks supply the migration/API integration
evidence; production Cloud SQL and GCS are checked separately below.

## 22. Production Health Checks

The production health script confirmed Database API, Ingestion,
Preprocessing and scheduler ready. Nginx `/health/live` and `/health/ready`
returned HTTP 200; `/internal/v1/datalake/jobs` returned 404 through Nginx.
The 02:30 UTC realtime poll was `SUCCESS`, with zero failed cycles in the
preceding 24 hours. The historical timer is enabled and active in systemd,
with `Persistent=true` for reboot recovery. A post-import PostgreSQL 17
custom-format backup (101 MiB, mode 600) was validated with `pg_restore
--list` (263 TOC entries). The 23 year manifests were read from GCS in a
production Compose container. A manual start of
`waimea-history-reconcile.service` completed with exit 0; the journal showed
`UNCHANGED_RECENT` for recently checked sources. A health endpoint alone is
not a historical data acceptance test.
After migration 10, the same systemd service completed the pending 2026
publication with exit 0. Cloud SQL recorded manifest generation
`1794539214563678` and coverage generation `1794539318217271`, both
independently downloaded and SHA-256 verified from a production container.
The next service run exited 0 with no pending publications and the same
generations. The timer remains enabled for daily runs and reboot recovery.
The systemd timer itself triggered at 2026-11-13 03:15:27 UTC (10:15:27
Asia/Saigon) and finished at 03:15:42 UTC with exit 0. Cloud
SQL still reported zero pending publications and the same two GCS generations.

## 23. Known Limitations

NOAA archive gaps remain real gaps; the system does not fabricate values.
The importer retains removed old-source rows until a reviewed correction
policy exists, because production deletion is outside TL08-A. A full
historical ML training policy, model evaluation and approved prediction
readiness remain TL08-B work. Older VIS unit headers need explicit evidence
before any nonmissing VIS data can be converted automatically.

## 24. TL08-B Training Dataset Handoff

Use only verified active Silver/Gold GCS versions and their year manifests,
joined to Cloud SQL lineage and QC flags. Split by time and station before
model selection; do not treat imputed rows as ground truth. Descriptive Gold
readiness does not establish horizon-specific training or forecast approval.
No ML model was trained or published in this phase.

## 25. Acceptance Matrix

| Criterion | Evidence status |
|---|---|
| Live NOAA source discovery | VERIFIED_IN_PRODUCTION |
| Controlled 2004 Raw/Silver import | VERIFIED_IN_PRODUCTION |
| All listed historical sources imported | VERIFIED_IN_PRODUCTION: 28 sources, 312,702 Raw/Silver |
| Bronze/Silver/Gold GCS export consistency | VERIFIED_IN_PRODUCTION: 767 populated exports and 23 manifests |
| Durable job recovery and same-source resume | TESTED_IN_ISOLATION; 28-source repeat VERIFIED_IN_PRODUCTION |
| Source correction reconciliation | TESTED_IN_ISOLATION; authentic correction not observed |
| Historical QC and Gold | VERIFIED_IN_PRODUCTION: 312,686 valid, 16 suspect; Gold in 767 multi-layer artifacts |
| Realtime pipeline and VM health | VERIFIED_IN_PRODUCTION; timer ENABLED_IN_PRODUCTION |
| Automatic manifest and coverage after archive changes | VERIFIED_IN_PRODUCTION for pending 2026; durable retry ENABLED_IN_PRODUCTION |
| TL08-B model training | Out of scope; disabled |
