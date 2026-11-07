# Google Cloud Storage object layout

- `gs://waimea-raw-<PROJECT_ID>/noaa/51201/year=YYYY/month=MM/day=DD/<batch>.txt`
- `gs://waimea-raw-<PROJECT_ID>/cdip/106/year=YYYY/month=MM/day=DD/<batch>.json`
- `gs://waimea-raw-<PROJECT_ID>/open-meteo/year=YYYY/month=MM/day=DD/<batch>.json`
- `gs://waimea-models-<PROJECT_ID>/prediction/<model_version>/model.joblib`
- `gs://waimea-models-<PROJECT_ID>/classification/<model_version>/model.joblib`
- `gs://waimea-backups-<PROJECT_ID>/postgres/<date>/<export>.sql.gz`

GCS buckets should be private, use IAM with least privilege, and have lifecycle/versioning policies. Raw payloads are keyed by content hash to avoid storing every identical response again.
