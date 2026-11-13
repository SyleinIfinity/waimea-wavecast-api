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

At the close of TL07-B, calibration of NOAA publication waiting, approved
horizon-specific quality/imputation policies, held-out validation of derived
estimates, historical import and pinned replay remained open. The TL08-A
historical import and source/GCS reconciliation are now recorded separately
below. Do not treat historical bulk loading as publication-latency evidence.
Prediction/model workers remain disabled.

## TL08-A production historical data lake

The controlled TL08-A import loaded all 28 archives listed for station 51201:
22 annual files from 2004–2025 and six monthly files from 2026. It persisted
312,702 historical Raw and 312,702 canonical Silver observations in Cloud SQL;
312,686 passed QC and 16 were marked suspect. Original NOAA bytes and monthly
Bronze/Silver/Gold exports use immutable GCS objects and durable PostgreSQL
manifests. The bounded daily archive reconciler is independent of the
30-minute realtime scheduler. See
`docs/contributions/TL08-A_HISTORICAL_BACKFILL_AND_GCS_DATA_LAKE.md` and its
machine-readable coverage report for per-month evidence, archive gaps,
versioned GCS checksums and operational limits.
The daily reconciler also completes durable pending year publications: it
verifies immutable year manifests and a versioned full coverage report in
GCS, then records pinned generations in Cloud SQL. A production systemd run
published pending 2026 successfully; a second run reused the same generations
with no pending work.

TL08-B should build time-split training datasets only from verified manifests,
with causal feature generation, approved imputation and horizon-specific
quality gates. Descriptive Gold readiness does not establish forecast
readiness. No model was trained or enabled by TL08-A.

## TL08-B/TL08-C checkpoint (2026-11-13 UTC)

TL08-B has trained real NOAA next-day regression/classification and K-means
models plus exact-timestamp 30-minute, 1-hour, 6-hour and 24-hour regression
models, and 7-day and 30-day daily regression models. All nine are DRAFT/NOT_READY;
the 7/30-day models passed three retrospective embargoed walk-forward folds,
but publication-time feature availability and live performance remain
unverified. All four exact-observation models underperform persistence on
held-out MAE. The Database API now rejects promotion without an explicit READY
quality gate.
See `docs/contributions/TL08-B_MODEL_TRAINING_AND_VALIDATION.md`.

TL08-C inference remains disabled because there is no qualified READY model.
The next implementation work is per-horizon readiness evidence, short-horizon
validation, live feature checks, immutable GCS forecast persistence and
ground-truth reconciliation. Do not present historical backtests as live
forecast accuracy. See
`docs/contributions/TL08-C_AUTOMATED_FORECASTING_AND_EVALUATION.md`.
