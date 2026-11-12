# Google Cloud Storage object layout

All objects live in the private `gs://waimea-noaa/` bucket in `asia-southeast1`.
`GCS_BUCKET`, `GCS_RAW_BUCKET`, `GCS_MODELS_BUCKET`, and `GCS_BACKUP_BUCKET`
all name this same bucket; the latter three preserve existing module settings.

| Data | Object prefix | Current use |
| --- | --- | --- |
| NOAA raw datasets | `noaa/raw/` | Ingestion path builders; production ingestion still uses a fake adapter |
| Processed datasets | `datasets/processed/` | Reserved |
| Training datasets | `datasets/training/` | Live training CLI writes Parquet snapshots |
| ML models | `models/` | Live training CLI and inference workers |
| Training logs and reports | `reports/training/` | Reserved |
| Model evaluations | `evaluations/` | Reserved; metrics currently go to Database API |
| Prediction results | `predictions/` | Reserved; predictions currently go to Database API |
| PostgreSQL exports | `backups/postgres/` | Reserved for separately managed exports |
| Connectivity checks | `test/` | Temporary objects, deleted after each check |

Raw payloads and model artifacts use immutable object paths and generation checks.
Keep uniform bucket-level access and public access prevention enabled, and grant
only the VM service account the object permissions its jobs require. The application
uses Compute Engine Application Default Credentials (ADC), not a JSON key file.
