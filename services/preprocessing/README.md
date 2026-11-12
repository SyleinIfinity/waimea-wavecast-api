# preprocessing

This production service uses `HttpDatabaseGateway` and the authenticated internal
Database API. It has no direct PostgreSQL credentials and no `FakeDatabaseGateway`
in the running path.

Endpoints (all writes require `X-Internal-Token`):

- `GET /health/live` and `/health/ready`.
- `POST /internal/v1/preprocessing/quality/review` with a UTC `start`/`end` range of at most 30 days. QC writes only status and flags after checking the measurement hash; lineage and measurements remain unchanged.
- `POST /internal/v1/preprocessing/daily/rebuild` with `start_hst`, `end_hst`, `expected_sample_count`.
- `POST /internal/v1/preprocessing/gold/rebuild` with `year` and optional `changed_from`/`changed_to` UTC timestamps. Gold recomputes only intersecting periods and two later rolling dependencies.
- `POST /internal/v1/preprocessing/gaps/reconcile` with `{}` for the latest seven days, or an explicit UTC `start`/`end` window of at most eight days.

Only `quality_status=valid` contributes to verified numerical aggregates.
Gold `GOLD_READY` means a *descriptive period* has at least 90% source
coverage and has closed. It does not approve that period for TL08 models.
The long-horizon, imputation and publication-delay policies remain unapproved.
