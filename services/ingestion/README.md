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

The internal endpoints require `X-Internal-Token`. They are not published by
Compose. Keep them off the public Nginx route. The service does not schedule
polling itself.

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
