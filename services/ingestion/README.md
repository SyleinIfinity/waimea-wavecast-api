# NOAA ingestion service

The service ingests NOAA NDBC station 51201 data through the Database API and
stores each source payload in the private GCS bucket before database writes.
It does not connect directly to PostgreSQL. See
`docs/contributions/TL06_REAL_NOAA_INGESTION_INTEGRATION.md` for test evidence
and deployment status.

## Configuration

- `INGESTION_ADAPTER_MODE=real` enables the Database API HTTP gateway and GCS
  client. `disabled` leaves ingestion unavailable. `fake` is allowed only with
  `APP_ENV=test`.
- `DATABASE_API_URL=http://database-api:8000` and `INTERNAL_API_TOKEN` are
  required for the internal Database API.
- `GCS_RAW_BUCKET=waimea-noaa` selects the private bucket. The GCS client uses
  Application Default Credentials from the VM's attached service account.
- The Compose service reads `.env` locally; the production overlay reads
  `.env.production`. Do not put a service account JSON key in either file.

## Endpoints

- `GET /health/live` checks the process.
- `GET /health/ready` checks Database API and bucket access in real mode.
- `POST /internal/v1/ingestion/poll` ingests the current realtime feed.
- `POST /internal/v1/ingestion/backfill` ingests historical feeds.
- `POST /internal/v1/ingestion/reconcile` rechecks recent feeds.
- `POST /internal/v1/ingestion/replay` accepts a pinned raw GCS object path and generation for automatic or manual recovery. The scheduler records both in PostgreSQL when a downstream step fails.
- `POST /internal/v1/ingestion/reconcile-orphans` scans at most 100 recent realtime GCS snapshots and replays at most three whose Source File registration or Raw row count is incomplete. The scheduler invokes it at startup and daily; it does not delete objects.

The internal endpoints require `X-Internal-Token`. They are not published by
Compose. Keep them off the public Nginx route. `pipeline-scheduler` owns the
00/30 UTC schedule and durable retries; Ingestion itself does not schedule.
The running NOAA client uses conditional ETag or Last-Modified requests when
the source provides them, and honors HTTP 429 `Retry-After`. Set
`NOAA_MAX_RETRIES=0` so the scheduler's ten-attempt cycle is the request budget.

## Recovery

An ingestion result is unsuccessful when a database step fails after GCS
upload. Record the reported GCS object path and generation, then replay that
exact object after resolving the database error:

```bash
docker compose run --rm --no-deps ingestion \
  python -m services.ingestion.scripts.replay \
  --feed realtime --year 2026 \
  --object-path noaa/raw/realtime/YYYY/MM/DD/<sha256>-snapshot.txt \
  --generation <generation>
```

Review the target environment and object identity before running replay.
GCS and PostgreSQL do not share a transaction; replay is idempotent at the
source file, raw row and Silver observation keys.

## Historical backfill preparation

`python -m services.ingestion.scripts.discover_history --max-files 28` audits
listed NOAA station history archives without writing Cloud SQL or GCS. Its
per-variable result is tracked as
`docs/contributions/TL07-B_HISTORY_COVERAGE.json`. To plan at most one verified
annual file, use `python -m services.ingestion.scripts.bounded_backfill
--year 2025`; it prints `PLAN_ONLY`. `--execute` performs one bounded real
ingest and is deliberately not scheduled. Check the manifest, row count,
available Cloud SQL/GCS capacity and backup before executing. A rerun uses
idempotent keys; use the returned pinned object generation for replay if a
database stage fails. No historical bulk backfill was run during TL07-B.
