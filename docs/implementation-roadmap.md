# Sprint order

1. Cloud SQL + migrations + Database API + health.
2. NOAA historical backfill and incremental source polling; CDIP adapter may return HTTP 403; Open-Meteo as model fallback.
3. Bronze raw archive in GCS, metadata in SQL; QC + dedupe + normalize into Silver.
4. Gold analytics: charts (day/week/month), trend, seasonality, residual and correlation.
5. K-means clusters and explanations.
6. Regression: next-day wave height. Classification: next-day wave state. Time-split evaluation, baseline metrics, save model to GCS; inference without retraining.
7. Integrate frontend repo via CORS and stable API contract.
8. Monitoring, alerting, backup restore tests, CI/CD and Gold-rebuild demo.

Polling configuration in starter is 1800s, not a guaranteed upstream publication SLA. Model training should not be coupled to each API request.
