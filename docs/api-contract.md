# TL01 Database API contract

The checked-in TL01 schema is `services/database_api/contracts/openapi.json`.
The current runtime also exposes additive TL07/TL08 routes described below.
In development, Swagger UI is `/docs`; in production, the restricted route
is `/private/docs`.

- Public reads: `/api/v1/stations/51201`, observations, analytics, clusters,
  predictions, classifications, models, data quality and freshness.
- Worker writes/reads: `/internal/v1/*`, require `X-Internal-Token` and
  `Idempotency-Key` for writes. Nginx does not expose these routes.
- Demo administration: `/api/v1/admin/*`, require `X-Admin-Token`; selective
  Gold deletion is disabled outside staging and requires preview/confirmation.

The original TL01 contract file is a baseline snapshot. Check the protected
runtime OpenAPI for later additive routes; production Ingestion,
Preprocessing, Scheduler and forward forecast jobs use real database/GCS
integrations. Do not infer that every legacy TL04/TL05 worker is enabled.

## Forecast contract for the frontend (TL08-C / Calisthenics 1.5)

The current public experimental endpoint is
`GET /api/v1/forecasts/experimental/latest?limit=20` (`limit`: 1–100). It
returns `{ "status": "ok" | "empty", "data": [...] }`; an empty result has
`data: []`. Each row includes `forecast_id`, `model_id`, `model_version`,
`horizon`, `target_kind`, `issued_at`, `anchor_at`, `target_at`,
`target_date_hst`, `predicted_value`, `quality_status`, `actual_value`,
`absolute_error`, and `evaluated_at`. All timestamps include a timezone.
The currently supported horizons are `30min`, `1d`, and `7d`. The endpoint
returns only lead-eligible, GCS-finalized experimental forecasts.

Frontend handling for this contract:

- Display `quality_status: "experimental"` clearly. A DRAFT model is not an
  approved production model merely because it has an experimental forecast.
- Show both issue time and target time. For daily horizons,
  `target_date_hst` is the target date in Hawaii local time; it is null for
  `30min`. Do not infer the target date from the browser timezone.
- `actual_value`, `absolute_error`, and `evaluated_at` are null until a
  QC-matched observation arrives. Show a pending state, not zero error or
  an invented observed value. A later NOAA correction may update the latest
  evaluation while leaving the issued forecast unchanged.
- Use `horizon` and `model_version` from each response. Do not hardcode a
  model ID in the frontend; Backend can retrain or change the selected model
  without requiring a frontend release.

Backend-only work does not change this public contract: the Calisthenics 1.5
20-pair update, retrospective evaluation, quality gates, missing-data review,
and operational monitoring.

### Calisthenics 1.5 shadow API

Calisthenics 1.5 now issues its own **experimental shadow forecasts** through
an additive, separate ledger. `GET /api/v1/forecasts/shadow/latest?limit=20`
accepts optional `horizon=30min|1d|7d|30d`; it returns
`{ "status": "ok" | "empty", "quality_status": "experimental",
"model_family": "calisthenics 1.5", "data": [...] }`. Each row contains
`forecast_id`, `model_id`, `model_version`, `horizon`, `target_kind`,
`issued_at`, `anchor_at`, `target_at`, `target_date_hst`, `predicted_value`,
`actual_value`, `absolute_error`, and `evaluated_at`. Pending truth fields
are null. `GET /api/v1/forecasts/shadow/metrics` reports evaluated count,
MAE, RMSE and persistence MAE per model/horizon once truth exists. The
existing `/experimental/latest` response is unchanged.

For `30d`, `target_date_hst` is the **last** HST date in the next-30-day
window, while `predicted_value` is the **mean of at least 27 QC-valid daily
medians over that window**. A frontend that chooses to show shadow forecasts
should explain that distinction and add a 30-day option; existing clients
need no change. Promotion to an approved forecast is a separate API/product
decision. Keep the experimental label until that contract exists.
