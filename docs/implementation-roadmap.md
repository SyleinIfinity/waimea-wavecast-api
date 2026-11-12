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

## Project decision: production-first integration and validation (TL07 onward)

For TL07, TL08, TL09 and later implementation tasks, inspect the actual VM,
Cloud SQL, GCS and service configuration before coding. Run unit and contract
tests, deploy the tested change with the production Docker Compose/systemd
configuration, then verify real data and artifacts in Cloud SQL and GCS.
Report observed container, API, database and artifact state. A task requiring
production integration is not complete from fake/local tests alone.

Protect existing data and keep a rollback path. Assess impact and request
approval before breaking schema migrations, bulk backfills, deletion, IAM or
firewall changes, or VM reboot. Do not disturb the Minecraft service. Design
and preparation-only tasks may explicitly stop before production deployment.

## TL07-A result and TL07-B production state

TL07-A implemented cadence-based missing detection, explicit quality gates,
causal derived-data imputation, multi-resolution HST analytics and per-horizon
benchmark contracts. A one-off production Compose container validated these
modules against 2,160 real Silver observations without writing Cloud SQL data.
The current snapshot has 17 inferred 30-minute gaps and all 2,160 rows are
still `unverified`; Daily Features and registered models are absent. Short
horizon persistence scores are exploratory, while 7-day and longer forecasts
are not production-ready. See
`docs/contributions/TL07-A_ALGORITHM_ENHANCEMENT.md` for evidence and limits.

TL07-B has replaced the persistent Preprocessing fake gateway with the
authenticated Database API, reviewed the initial 2,160 Silver rows, built
HST Daily Features and six-resolution descriptive Gold, and activated a
PostgreSQL-leased primary/retry scheduler. The initial production poll added
seven real observations and propagated them through QC and Gold. Archive
discovery counted actual nonmissing variables in 28 listed NOAA files; see
`docs/contributions/TL07-B_HISTORY_COVERAGE.json` and the TL07-B report.

Still required before TL08: calibrate NOAA publication waiting from ongoing
arrivals, approve horizon-specific quality and imputation policies, validate
derived estimates and uncertainty on held-out historical data, safely backfill
selected archives, and test automatic pinned replay during a natural or
isolated downstream failure. Do not treat the historical TL06 bulk load as
publication-latency evidence. Prediction/model workers remain disabled.
