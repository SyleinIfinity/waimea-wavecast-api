# Internal and public API contracts

## Implemented starter
- `GET /health` (database-api: Database reachability)
- `GET /api/v1/observations/latest` (returns `data: null` until ingestion runs)

## Planned (NOT implemented yet)
- `POST /internal/batches` (Ingestion -> Bronze + GCS raw URI)
- `POST /internal/preprocessing/run` (Bronze -> Silver)
- `POST /internal/analysis/run` (Silver -> Gold)
- `POST /internal/clustering/run`
- `POST /internal/prediction/run` (inference with saved model)
- `POST /internal/classification/run` (inference with saved model)
- `GET /api/v1/analytics/*`, `/api/v1/clusters`, `/api/v1/predictions/tomorrow`, `/api/v1/classifications/tomorrow`

All internal job/write endpoints require authentication, should stay on Docker network, and must NOT be routed by public Nginx until access control is designed.
