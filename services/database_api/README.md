# TL01 Database API

Python 3.13, FastAPI, psycopg 3, PostgreSQL 17. The SQL schema and migration
instructions are in `../../database/README.md`. This service is the only SQL
writer for the TL04/TL05 worker contract; it does not download NOAA or train
models.

## Local run

Create a virtual environment at the repository root and install `.[dev]`. For local
development, copy the root `.env.example` to the root `.env` and fill in the DB settings;
`services/database_api/app/config.py` loads that file automatically. Deployment can pass the same
variables through a protected host secret store. Keep
`DB_PASSWORD`, `INTERNAL_API_TOKEN`, and `ADMIN_API_TOKEN` out of Git.
For production, set `DB_HOST=dbwaimea.liems.io.vn`, `DB_PORT=5432`, the
database name and user, and `DB_SSLMODE=require`. The service reads these
settings directly; workers continue to use `DATABASE_API_URL` for internal HTTP.

```sh
python -m pip install -e ".[dev]"
python -m pytest -q tests/tl01
python -m services.database_api.scripts.inspect_db
python -m services.database_api.scripts.rollback_integration
python -m services.database_api.scripts.http_rollback
python -m services.database_api.scripts.smoke_read
python -m services.database_api.scripts.export_openapi
uvicorn services.database_api.app.main:app --host 127.0.0.1 --port 8000
```

`rollback_integration` inserts synthetic rows in **one transaction and always
rolls it back**. Run it with a test role/database when available. `inspect_db`
and `smoke_read` are read-only. The service does not apply migrations at boot.

## API

* Public `GET /api/v1/...`: station, observations, Gold metrics and patterns,
  model evaluations and data quality. Empty queries return `status: empty`.
* Worker `/internal/v1/...`: pass `X-Internal-Token`; all writes also require
  `Idempotency-Key`. Batch size defaults to 500. Workers should reuse a key on
  retries. Responses for jobs distinguish accepted from completed.
* Admin `/api/v1/admin/...`: pass `X-Admin-Token`. Demo deletion requires
  `APP_ENV=staging`, `DEMO_DELETE_ENABLED=true`, and
  `DEMO_BACKUP_CONFIRMED=true`. Preview first; use its one-time token within
  five minutes and send `{"dry_run_id":"...","confirmation":"DELETE GOLD DEMO"}`
  with `X-Actor-ID`. Only six regenerable Gold pattern tables are accepted,
  scoped to station 51201 and at most 30 HST days. Bronze, Silver, model,
  evaluation and GCS artifacts are never deleted. Run one API process for
  one-time preview tokens; a shared token store is needed before scaling out.

`POST /api/v1/admin/demo/patterns/rebuild` forwards work to configured
TL04/TL05 worker URLs and returns `202` when the workers accept the jobs.
It requires those worker services to exist; this service does not synthesize
their results. No training is requested by rebuild.

The API's OpenAPI schema is at `/openapi.json`; `/docs` is disabled in
production. `/health/live` checks only this process, while `/health/ready`
checks PostgreSQL with `SELECT 1`. Configure Nginx to expose only `/api/v1`
and health routes; keep `/internal/v1` on the private service network.
`contracts/examples.json` contains mock and empty responses for TL02–TL05.
The container receives the same `GCS_*` bucket settings as the workers, but the
Database API does not itself read or write GCS objects. GCS connectivity is
verified from inside this container with the real `RealGCSClient` and VM ADC.
