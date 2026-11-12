# TL06 — Real NOAA Ingestion Integration

**Date:** 2026-11-12  
**Branch:** `developer`  
**Scope:** NOAA NDBC station 51201 ingestion only. No commit, push, schema migration, IAM change, production database write, or unrelated service restart.

## 1. Executive Summary

The Ingestion service now uses an HTTP Database API gateway and the real GCS
client in production mode. It downloads and validates NOAA data, writes an
immutable raw object, registers source metadata, upserts Bronze raw rows, and
upserts Silver observations with Raw and Source File lineage. A real NOAA
realtime sample completed this path through a temporary Database API and
isolated PostgreSQL database, with real objects in the private
`waimea-noaa/test/tl06/` namespace. The production Ingestion service has **not**
been started and the production database has **not** received TL06 writes.

## 2. Initial Findings

- The production Ingestion entrypoint instantiated `FakeDatabaseGateway` and
  `FakeGCSClient`; no HTTP `DatabaseGateway` implementation existed.
- Raw bulk upsert returned counters but no `raw_id`, so Silver lineage could
  not be linked reliably. Silver response exposed `updated` while the pipeline
  expected `revised`.
- Reusing the same NOAA measurement in a new source file could count as a
  revision solely because `source_file_id` changed.
- Ingestion internal routes had no token check. Empty or malformed data could
  proceed too far through the pipeline.
- The existing NOAA client already had bounded retry/backoff for 429, 5xx and
  transport errors; 404 was non-retriable. This behavior was retained and
  covered by tests.

## 3. Implementation Details

| File | Change |
|---|---|
| `packages/db/http.py` | New authenticated HTTP Database API gateway with stable idempotency keys and bounded retries. |
| `packages/db/gateway.py`, `packages/db/fake.py` | Source metadata DTO and fake contract aligned with real responses. Fake remains test-only. |
| `services/database_api/app/repositories/bronze.py` | Raw bulk response includes `raw_ids` for inserts and duplicates. |
| `services/database_api/app/repositories/silver.py` | Adds `revised` alias and treats same-feed, same-measurement content as duplicate. |
| `services/database_api/app/schemas/observations.py` | Accepts optional parser metadata on source files. |
| `packages/gcs/client.py` | Adds read-only bucket access check for readiness. |
| `packages/noaa/urls.py` | Adds monthly filename suffixes for October–December, based on existing migration notes; live availability still needs verification. |
| `services/ingestion/app/services/ingest_pipeline.py` | Real adapters required; parse before upload; write source metadata and `raw_id`; report counters and failures; replay pinned GCS generation. |
| `services/ingestion/app/main.py` | Real/fake/disabled mode, token-protected internal routes, live and dependency readiness endpoints. |
| `services/ingestion/app/jobs/poll.py`, `services/ingestion/app/jobs/reconcile.py` | Correct empty-run and revision counters. |
| `services/ingestion/scripts/replay.py` | Operator command to replay one stored raw object after partial failure. |
| `services/ingestion/Dockerfile`, `services/ingestion/README.md` | Standalone image dependencies and operating instructions. |
| `.env.example`, `.env.production.example` | Explicit adapter mode; production example uses real adapters and `waimea-noaa`. |
| `tests/tl06/`, `tests/tl01/test_api.py` | Unit/contract coverage and additive API response fixture. |

No database schema was changed. The Compose Ingestion service already uses the
common image, internal network, and `.env` file; production overlay already
uses `.env.production`. No port is published for Ingestion.

## 4. Architecture & Data Flow

`NOAA NDBC HTTP → Ingestion parser/normalizer → GCS noaa/raw/... → Database API
→ bronze.source_files → bronze.raw_observations → silver.wave_observations`

Ingestion uses `DATABASE_API_URL` and `INTERNAL_API_TOKEN`, never a direct
PostgreSQL connection. GCS uses Application Default Credentials from the VM
service account. Content-derived object paths and `if_generation_match=0`
prevent accidental replacement. The source record includes GCS URI, bucket,
path, generation, SHA-256, source URL, period, row count, parse status and
parser version. Silver rows reference both `raw_id` and `source_file_id`.

## 5. API Contract Changes

- `POST /internal/v1/raw-observations/bulk` retains `inserted` and `duplicate`
  and adds `raw_ids: {row_sha256: raw_id}`. Duplicate rows return their existing
  ID. The pipeline rejects a response missing a required lineage ID.
- `POST /internal/v1/silver/observations/bulk-upsert` retains `updated` and
  adds `revised` with the same count. `inserted`, `duplicate`, and `skipped`
  remain available. Same-feed/same-measurement resubmissions are duplicates.
- Source file request accepts optional parser metadata. Existing clients may
  omit it. The HTTP gateway resolves `feed_id` through the Database API.
- Ingestion internal POST routes require `X-Internal-Token`. Missing or wrong
  token returns 401; unavailable dependencies return 503.

## 6. Data Integrity and Recovery

Raw objects are keyed by content hash. Source files are unique by station,
feed and file SHA-256; raw observations by source file and row SHA-256; Silver
observations by station and observation time with feed priority arbitration.
The HTTP gateway uses a deterministic idempotency key for each write payload.
Bounded HTTP retries cover transient transport, 429 and 5xx responses.

If GCS succeeds and a database step fails, the result remains unsuccessful
and includes the GCS URI/generation. The operator can replay exactly that
generation with `python -m services.ingestion.scripts.replay`. A replay after
raw commit and before Silver commit is safe: raw rows are duplicates and Silver
can finish. GCS and PostgreSQL have no distributed transaction. Recovery is
currently operator initiated; there is no automatic orphan scanner or durable
replay queue. Avoid unattended large backfills until those controls are added.

## 7. Testing Evidence

| Test | Data and environment | Result |
|---|---|---|
| Full Python suite in Docker image with mounted source: `APP_ENV=test INGESTION_ADAPTER_MODE=fake PUBLIC_ORIGIN=http://localhost:5173 python -m pytest -q` | Unit and contract fixtures | **PASS:** 133 passed, 2 skipped (live DB tests lacking credentials), 1 Starlette deprecation warning. |
| NOAA realtime HTTP download/parse | Actual NDBC payload, 203,134 bytes | **PASS:** 2,159 parsed rows, 0 parse errors. |
| GCS upload/download/content check/delete | Actual full NOAA payload, `gs://waimea-noaa/test/tl06/...`, VM ADC inside container | **PASS:** bytes/hash matched; test object deleted. |
| Ingestion container readiness and auth | Ephemeral container on Compose network, real production Database API and GCS, no writes | **PASS:** `/health/live` 200, `/health/ready` 200, unauthenticated poll 401. |
| NOAA → GCS → Database API → PostgreSQL | First 3 rows of actual NOAA realtime data; real GCS `test/tl06/...`; temporary Database API and isolated PostgreSQL 17 | **PASS:** 3 Raw and 3 Silver inserted, 3/3 Silver→Raw→Source File→GCS lineage joins; same data repeated gave 3 Raw duplicates and 3 Silver duplicates. |
| Correction | One controlled measurement change derived from a real NOAA row, isolated DB | **PASS:** after fixing source-file-only revision, 1 revised and 2 duplicate Silver observations. Controlled change is synthetic; NOAA correction in the wild was not observed. |
| GCS succeeds, DB registration fails, then replay | Real GCS object and isolated DB with injected failure | **PASS:** first run unsuccessful; pinned-generation replay completed; object deleted after test. |
| NOAA errors and auth | Unit/contract tests for 404, 429, 5xx, timeout, invalid/empty payload, wrong token | **PASS** for specified cases. |
| Compose and existing production Database API | `docker compose config -q`; `curl` against localhost port 8000 | **PASS:** Compose valid; production Database API live 200 and ready 200. |
| Standalone Ingestion image | `docker build -f services/ingestion/Dockerfile` and application import in the built image | **PASS:** build and import succeeded. |
| GCS namespace cleanup | Bucket listing for `test/tl06/` from container | **PASS:** 0 objects remaining. |

The isolated database used Docker network `waimea-tl06-net`, PostgreSQL
container `waimea-tl06-pg`, and temporary API container `waimea-tl06-api`.
Only the isolated database received test writes. Production Database API was
queried for health/readiness and an empty internal read only. The 2,159-row
NOAA payload was **not** fully inserted into PostgreSQL; only three source
rows were used for bounded integration testing. Both temporary containers and
their Docker network were removed after verification; the production Database
API still returned live 200 and ready 200 afterward.

## 8. Deployment Status

Code and example configuration are prepared on branch `developer`. The
production Database API container remains running its previous image. The
Ingestion service is not running in the production Compose project. Its
dependency readiness was verified only in an ephemeral container. The VM
service account successfully accessed the private bucket through ADC; no key
file or IAM change was needed for the tested operations.

After review, deploy the updated Database API first because Ingestion requires
the additive `raw_ids` response; then start only Ingestion with the production
overlay. Confirm `.env.production` has `INGESTION_ADAPTER_MODE=real`,
`DATABASE_API_URL=http://database-api:8000`, a matching `INTERNAL_API_TOKEN`,
and `GCS_RAW_BUCKET=waimea-noaa`. Run `docker compose -f compose.yaml -f
deploy/compose.prod.yaml up -d --build database-api ingestion` during a
planned service window; check both services' `/health/live` and
`/health/ready` from the internal network. Keep Ingestion off public Nginx.
Starting it does not schedule polling; trigger a small, reviewed poll or
backfill only when production writes are authorized.

## 9. Known Limitations

- Production NOAA→GCS→Cloud SQL write path is **not verified**. The safe
  end-to-end test used isolated PostgreSQL and the real private GCS bucket.
- No safe staging Cloud SQL database was available. No production rows were
  created, changed or deleted by TL06 tests.
- Historical annual/monthly sources were not run end to end against live NOAA;
  a 2025 October monthly URL check returned 404. NOAA publication rules and
  year/month availability need a targeted live check before bulk backfill.
- Replay is manual. A process crash after upload may require locating an
  orphaned GCS object; there is no durable run state or automatic repair job.
- The GCS readiness probe checks bucket listing, while upload permission is
  proven separately by the real upload/download/delete test.
- Partial parse errors are reported and valid rows may proceed; no automatic
  quarantine workflow exists yet.
- Polling remains manual/externally scheduled. No 20-minute scheduler was
  added in TL06.

## 10. Next Phase Dependencies (TL07)

TL07 may read Silver observations through the HTTP Database gateway and build
daily features only after a controlled production ingest (or staging dataset)
has established real source coverage. It should use `raw_id` and Source File
GCS metadata for traceability, define quality thresholds/quarantine for parse
errors, and verify date/timezone boundaries. Automatic replay/orphan detection
and historical URL validation should precede large unattended backfills.

## Acceptance Matrix

| Criterion | Status | Evidence / remaining work |
|---|---|---|
| Real HTTP DatabaseGateway and production real GCS adapter | PASS | Implemented, Docker tests, ephemeral container readiness. |
| Actual NOAA realtime download and parse | PASS | 203,134 bytes, 2,159 rows parsed. |
| Real GCS raw object write/read/delete | PASS | Full payload round trip via VM ADC in container; test namespace empty afterward. |
| Actual NOAA sample through GCS and PostgreSQL | PASS | Three real source rows through isolated PostgreSQL and temporary API, with 3/3 lineage. |
| Idempotent repeat and controlled correction | PASS | 3/3 duplicates on repeat; 1 revised and 2 duplicates on controlled change. |
| Partial failure and pinned replay | PASS | Injected DB failure then successful replay against real GCS/isolated DB. |
| NOAA error/auth/health checks | PASS | Unit/contract tests; ephemeral Ingestion 200/200/401; production Database API 200/200. |
| Compose validity and no production service interruption | PASS | `docker compose config -q`; only existing Database API running in production Compose. |
| Live production Cloud SQL ingestion | NOT VERIFIED | Requires reviewed production write or safe staging DB. |
| Historical backfill availability across years | NOT VERIFIED | URLs and missing-period behavior need live bounded validation. |
| Automatic crash recovery/orphan replay | NOT VERIFIED | Manual replay exists; automated durable recovery is outside TL06. |
