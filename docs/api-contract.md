# TL01 Database API contract

Implemented FastAPI schema: `services/database_api/contracts/openapi.json`.
Runtime schema: `/openapi.json`; Swagger UI: `/docs` in development.

- Public reads: `/api/v1/stations/51201`, observations, analytics, clusters,
  predictions, classifications, models, data quality and freshness.
- Worker writes/reads: `/internal/v1/*`, require `X-Internal-Token` and
  `Idempotency-Key` for writes. Nginx does not expose these routes.
- Demo administration: `/api/v1/admin/*`, require `X-Admin-Token`; selective
  Gold deletion is disabled outside staging and requires preview/confirmation.

The TL04/TL05 workers still use `FakeDatabaseGateway` until their HTTP
adapters and integration tests are implemented. OpenAPI describes TL01's
actual endpoints, not a claim that those workers are already connected.
