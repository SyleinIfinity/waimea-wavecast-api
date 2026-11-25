# Cloud SQL payload retention and Raw deduplication — 2026-11-14 UTC

The VM retains detailed Raw and revision payloads for the current month plus
24 complete UTC months. Older full payloads are pinned to the private
`waimea-noaa` GCS bucket in monthly JSONL gzip objects. Cloud SQL keeps
`raw_id`, source-file foreign keys, revision IDs, observation times, hashes,
correction metadata, and the GCS export/archive pointers. Canonical Silver
and Gold remain queryable in Cloud SQL for production forecasting and training.

## Production migration and safety checks

- A private PostgreSQL 17 custom-format backup was created before the new
  migrations at `/home/mcadmin/.local/share/waimea/backups/pre-raw-dedup-20261114.dump`.
  It is 111,360,852 bytes, mode 0600, SHA-256
  `0ddf06a01cedff07309c6773e5f1392fce7e98a188e8b279a24fc15f4f734022`.
  `pg_restore --list` succeeded. Restore into an isolated database before
  relying on it for disaster recovery; this turn did not rehearse a full
  restore of this specific backup.
- A post-change custom-format backup is at
  `/home/mcadmin/.local/share/waimea/backups/post-raw-archive-20261114.dump`
  (80,306,468 bytes, mode 0600, SHA-256
  `095a3a09133f446ef36b2a900660675e17eb8e7dc88a356064f2801fadfba3b3`).
  `pg_restore --list` succeeded; a full isolated restore was not run.
- Migrations 14, 15, and 16 were applied. Index migrations 14 and 16 were
  executed with `CREATE INDEX CONCURRENTLY` outside a transaction.
- `docker compose config --quiet` passed with the production overlay and
  archive profile. The Python suite passed: 202 tests, 2 skipped.

## Verified production results

| Check | Result |
| --- | ---: |
| Old Raw payloads replaced by pinned monthly Bronze export IDs | 284,319 |
| Distinct pinned old Bronze month exports | 231 |
| Old revision snapshots replaced by pinned monthly archive IDs | 568,638 |
| Verified revision archive months | 231 |
| Legacy unreferenced duplicate Raw rows removed | 151,783 |
| Remaining exact cross-file Raw duplicates | 0 |
| Total Raw rows after cleanup | 314,941 |
| `/health/live`, `/health/ready` | HTTP 200 / HTTP 200 |

The first archived Raw month (2004-09) was independently rehydrated and
matched its active GCS export: 509 of 509 rows. The last eligible month
(2024-10) also matched: 1,470 rows. The first archived revision month
(2004-09) downloaded from its pinned GCS generation with matching compressed
SHA-256 and 1,018 of 1,018 Cloud SQL pointers. The internal revision read
endpoint retrieved revision 4368 and restored its 28-field snapshot from
GCS. Three affected 2026 Bronze exports were republished; the 2026-11
partition is still in progress and will change with later NOAA updates.

A production NOAA poll processed 2,144 repeated rows and inserted zero Raw
and zero Silver rows. A transaction-rolled-back cross-file call reused the
existing Raw ID with zero inserts. The old source-file registration endpoint
returned `ingest_complete=true` even when only 1 of 2,144 Raw rows remains
in that file, so orphan recovery will not replay a fully processed snapshot.
The next scheduled 01:30 UTC poll created a distinct source file with 2,145
rows. It inserted exactly 1 new Raw and 1 new Silver observation, reused the
other 2,144 Raw readings, set `ingest_complete=true`, and its pinned GCS
object downloaded with the recorded SHA-256. The cycle finished `SUCCESS`.

The original 71 realtime GCS source files were read from their pinned
generations and checked against their recorded SHA-256 before cleanup.
Deleting the duplicate rows preserved all Silver, revision and QC references.

## Physical Cloud SQL size

Payload compaction and regular `VACUUM (ANALYZE)` make table pages reusable,
but PostgreSQL does not necessarily return allocated pages to the filesystem.
The measured `pg_database_size` after this work is 1,805,457,075 bytes; it
is **not** a reduction from the roughly 1.65 GB observed before the rewrite.
The rewrite and new indexes temporarily increased allocated size. Do not
claim a Cloud SQL storage saving yet. An online `pg_repack` operation could
shrink the physical tables, but it needs roughly twice the target table size
in free disk and short exclusive locks. Check Cloud SQL free capacity and
schedule that maintenance separately; `VACUUM FULL` blocks writes and was not
run on the live pipeline.

## Reboot and ongoing retention

`waimea-revision-archive.timer` and `waimea-raw-archive.timer` are installed
and enabled on the VM. They run at 04:15 and 04:45 UTC daily with
`Persistent=true`, one eligible month per run. The one-off archive containers
use the VM's Application Default Credentials and the private GCS bucket.
`RAW_ARCHIVE_RETENTION_MONTHS=24` and
`REVISION_ARCHIVE_RETENTION_MONTHS=24` are in `.env.production` and both
examples. Verify after reboot with `systemctl list-timers
'waimea-*-archive.timer'`, the service journals, and both health endpoints.

No Git commit or push was made.

## Files changed for this retention task

- SQL: `database/migrations/14_raw_snapshot_dedup.sql`,
  `15_revision_payload_archives.sql`, `16_raw_fk_lookup_indexes.sql`.
- Ingestion and storage: `packages/db/{fake,http}.py`,
  `services/ingestion/app/jobs/orphans.py`,
  `services/ingestion/app/services/{datalake_export,ingest_pipeline}.py`,
  `services/ingestion/scripts/{archive_raw,archive_revisions,prune_duplicate_raw}.py`.
- Database API: `services/database_api/app/api/{datalake,internal}.py`,
  `services/database_api/app/repositories/bronze.py`.
- Operations: `.env.example`, `.env.production.example`, `compose.yaml`,
  `deploy/compose.prod.yaml`, four `deploy/systemd/waimea-*-archive.*`
  units, `database/README.md`, `docs/deployment.md`,
  `storage/gcs_layout.md`, and the live ignored `.env.production`.
- Tests: `tests/tl06/test_ingestion.py`,
  `tests/tl07b/test_orphan_recovery.py`,
  `tests/tl08a/test_datalake_export.py`.
