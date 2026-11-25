# TL07-A — Algorithm Review, Missing Data Intelligence & Multi-Horizon Forecasting

**Date:** 12/11/2026 (Asia/Saigon)  
**Branch:** `feature/tl06-Ingestion` (existing TL06 working tree preserved)  
**Status:** Core algorithms and contracts implemented; production read-only validation completed. Forecast publishing and automatic imputation remain **NOT READY**.

## 1. Algorithm Audit

| Area | Existing implementation and audit result | TL07-A action |
|---|---|---|
| NOAA ingestion / Silver | Real TL06 pipeline and lineage; 2,160 Silver rows from one 45-day realtime file. All have `quality_status=unverified`. | Read-only quality assessment; no Raw/Silver edits. |
| Preprocessing / QC | Daily HST aggregation existed, but its entrypoint still uses `FakeDatabaseGateway`; `good_quality_count` treated `unverified` as good, and suspect/rejected values entered aggregates. | Corrected daily statistics and physical validity checks. Persistent preprocessing worker remains TL07-B work. |
| Missing data | No cadence-based gap detector or publication-wait distinction. | Added inferred cadence, pending/missing slots, late/corrected/duplicate/invalid flags and fail-closed quality gate. |
| Imputation | No production algorithm or separate provenance store. | Added causal, validated-model contract, bounded trailing-median baseline, immutable derived point schema and GCS dataset writer. Auto-imputation stays disabled. |
| Analytics | Day/week/month aggregation, monthly decomposition, correlation and plots existed. Monthly season used future months; daily correlation paired independently computed medians. | Removed look-ahead from decomposition; paired values from the same rows; added 30-minute/seasonal/yearly aggregates, completeness, rolling stats, autocorrelation and causal anomalies. |
| Regression / classification | TL05 Random Forests use chronological daily samples and train-only class terciles; production has no Daily Features. | Added horizon-specific persistence benchmarks, train-only mean and class baselines, walk-forward checks and refusal states. No production training. |
| Clustering | K-means with train-only scaler/imputer and fixture tests exists, but no production daily dataset. | Audited; production evaluation deferred until TL07-B Daily Features. |
| Inference / registry / GCS | TL05 workers use `HttpMLGateway`, ready model registry and versioned, SHA-verified GCS artifacts. Production model registry is empty. | Kept these contracts; no ready model is assumed or promoted. New derived datasets use a separate versioned prefix. |

## 2. Confirmed Defects and Fixes

1. Seasonal decomposition computed month effects from the entire input series, including future months. `packages/ml/analytics.py` now uses only prior same-month residuals; early months return undefined components.
2. Daily correlation previously took median wave and median comparison metric from different observation subsets. It now builds same-row pairs first.
3. Daily feature statistics included rejected/suspect measurements and called unverified rows “good”. Those values are excluded from valid aggregates; only `valid` counts as good.
4. QC omitted non-finite, negative wave/wind/visibility and non-positive period checks. The checks now reject those physically invalid inputs.
5. Training sample construction did not explicitly exclude derived `IMPUTED` rows. It now rejects imputed features and labels.
6. The first gap-detector test exposed a cap coupling bug: disabling trailing slots also disabled internal gaps. Separate caps now preserve both behaviors.

## 3. Changed Files

| File | Purpose |
|---|---|
| `packages/ml/data_quality.py` | Cadence inference, gap states, observed/invalid flags, timeline and configurable quality gate. |
| `packages/ml/imputation.py` | Causal validated lag-Ridge/trailing-median imputation, derived provenance, reconciliation and immutable GCS writer. |
| `packages/ml/timeseries.py` | Multi-resolution HST statistics, completeness, rolling trend, autocorrelation, seasonal anomalies and cyclical encodings. |
| `packages/ml/forecast.py` | Horizon contracts, chronological/walk-forward persistence and train-only class benchmark. |
| `packages/ml/analytics.py` | Fix causal decomposition and paired correlation. |
| `packages/ml/features.py` | Exclude imputed features and targets from supervised samples. |
| `services/preprocessing/app/features/daily.py`, `services/preprocessing/app/quality/checks.py` | Correct daily validity and physical QC. |
| `services/preprocessing/scripts/review.py` | Read-only production review through authenticated Database API. |
| `docs/tl07a_quality_policy.example.json` | Research-only threshold proposal; auto-imputation disabled and publication wait unset. |
| `tests/tl07a/test_algorithms.py` | Gap, leakage, imputation, reconciliation, quality, resolution and horizon tests. |
| `docs/implementation-roadmap.md` | TL07-A outcome and TL07-B prerequisites. |

No schema migration, production data mutation, persistent worker startup, scheduler activation, model promotion or commit/push was performed for TL07-A.

## 4. Missing Data and Imputation Design

The detector infers modal observation spacing from timestamps; it never equates a 30-minute poll timer with a 30-minute observation guarantee. Cadence requires an explicit minimum number of intervals and support ratio. Gaps between known observations must be integer multiples of that cadence. Unclear/long intervals return `INSUFFICIENT_DATA`, rather than invented slots. Trailing slots are bounded and classified `PENDING_PUBLICATION` until an explicitly configured wait expires; only then can they become `MISSING`.

Observed rows retain their NOAA source identity. Additional flags indicate `LATE`, `CORRECTED`, `DUPLICATE` or `STALE`; invalid values are `INVALID`. An imputation is a separate `DerivedPoint` with value, observation time, `IMPUTED`, method, model version, estimated time, uncertainty, score and provenance. It never overwrites Raw or Silver. A model must carry training cutoff and held-out validation evidence before imputation. History is filtered to observations available by the target time; earlier imputed points are never fed back as history. Consecutive estimates and historical age are capped. Weak validation, unavailable history or high uncertainty returns `INSUFFICIENT_DATA`. `fit_lag_ridge` uses three observed lags, their rolling mean and local trend, fits only verified contiguous train history, and measures MAE on later validation labels. The trailing-median baseline remains a transparent comparison. Neither model was trained on production because all current Silver rows are unverified.

When NOAA later supplies the observation, `reconcile_observed` changes the derived view to `OBSERVED`, retains the former estimate in provenance and returns affected HST day/week/month/season keys. TL07-B must persist those recomputation requests. `save_derived_dataset` stores versioned JSON at `datasets/processed/tl07-a/<version>/<sha256>.json` with GCS generation and hash. This is a separate artifact contract because the current Silver schema only admits real source rows and does not contain imputation fields. A future queryable derived table would require an additive migration and review; none was applied here.

## 5. Quality Gate

`QualityThresholds` requires explicit missing, imputed and unverified ratios; maximum consecutive missing slots, freshness, uncertainty, historical coverage and minimum observed count. An unavailable uncertainty estimate fails closed. The example policy is marked **research only** and disables automatic imputation. Its 2% missing-ratio proposal is above the observed 17/2,177 = 0.78% missing-slot rate, but one 45-day sample cannot establish an operating SLA. NOAA publication delay cannot be estimated from `first_seen_at` here: 2,149 historical rows appear late because TL06 loaded the whole 45-day file at once. The example therefore leaves `publication_wait_minutes` and `max_uncertainty_m` unset; TL07-B must calibrate them on ongoing arrivals and validation residuals before enabling automation.

Read-only production gate with exploratory limits (2% missing, 0% unverified, 95% coverage, 2-hour freshness, uncertainty required) returned `INSUFFICIENT_DATA` for `unverified_ratio` and `uncertainty_unverified`. This is the correct operational decision for current data.

## 6. Multi-Resolution Analytics

All source timestamps are UTC; grouping uses `Pacific/Honolulu`. The new module covers 30-minute, daily, weekly, monthly, calendar-quarter seasonal and yearly periods. Each period computes sample/valid/expected counts, completeness, mean, median, min, max, standard deviation and variance. Rolling mean, period trend and residual use trailing periods only. Autocorrelation returns undefined for insufficient or constant data. Seasonal anomaly compares a month only with **prior** same-month periods.

With inferred 30-minute cadence and an exploratory 90% full-period coverage gate, production data yielded: 2,160/2,160 passing 30-minute bins; 45/46 HST days; 6/8 weeks; 1/3 months; 0/2 calendar quarters; 0/1 years. The first and last partial periods are not treated as complete just because they contain observations. A read-only daily calculation for 10/11/2026 HST found 48 samples, 48 valid wave heights, coverage 1.0 and median wave height 2.1 m, but `good_quality_count=0` because all source rows remain unverified. No Daily Feature was written.

## 7. Seasonal and Cyclical Features

`cyclical_features` emits sine/cosine hour, day-of-year and month signals plus an HST calendar-season label. `seasonal_anomalies` needs configurable prior-year evidence and never uses future climatology. The 45-day production snapshot spans only late September to mid-November; it cannot support a reliable seasonal distribution, annual trend or year-ahead storm-wave forecast. Official NOAA research describes stronger, more frequent North Pacific swells on Hawaii’s north-facing shores in part of the year, but that climatology is context, not a deterministic forecast for a specific future day ([NOAA coastal report](https://repository.library.noaa.gov/view/noaa/5708/noaa_5708_DS1.pdf)).

## 8. Forecast Horizon Architecture

`Forecast` carries `forecast_horizon`, target timestamp/period, prediction type, value/distribution, interval, model name/version, feature snapshot, quality status and generation time. Fixed horizons: 30 min, 1 h, 6 h, 24 h, 7 d and 30 d. Season/cycle/year return `NOT_READY` until multi-year data and appropriate probabilistic outlook models exist. Different horizons can use different validated models in TL08; no precise hourly year-ahead prediction is generated.

`evaluate_persistence` uses chronological train labels strictly before the split and test anchors after it. The comparison mean is computed from train targets only. Interval half-width comes from train residuals; held-out interval coverage is measured. `walk_forward_persistence` uses expanding train and nonoverlapping test windows. `baseline_forecast` refuses unverified rows by default and requires explicit skill, interval coverage and freshness thresholds. A positive single-fold score does not promote a model.

## 9. Historical NOAA Availability

[NDBC station history](https://www.ndbc.noaa.gov/station_history.php?station=51201) listed 22 annual standard meteorological archives from 2004 through 2025 and six 2026 monthly archives: January, February, May, June, July and August when checked. Bounded HEAD checks returned 200 for the 2004 and 2025 annual files and 2026 May monthly file. Constructed October 2025 and October 2026 monthly URLs returned 404; those months were not listed by the station history page. This validates that archive discovery must precede backfill, and a constructed URL is not proof of publication. Per-year row completeness was **not** measured: no bulk historical download or Cloud SQL backfill was performed. NDBC documents realtime files as a recent approximately 45-day window ([NDBC FAQ](https://www.ndbc.noaa.gov/faq/rt_data_access.shtml)).

## 10. Benchmark and Model Evaluation

Production snapshot: 2,160 Silver observations from 28/09 to 12/11/2026 UTC, 2,142 of 2,159 adjacent intervals at 30 minutes (99.21% modal support), 17 isolated 60-minute intervals, no null wave heights or periods, no Silver revisions, all `unverified`. The 17 missing slots are 0.78% of 2,177 inferred slots. These are source-cadence findings, not publication-latency estimates.

One chronological 75/25 holdout produced these **exploratory** persistence benchmarks. MAE/RMSE are metres; skill is `1 − persistence MAE / train-only mean MAE`. The nominal 90% interval was calibrated on train residuals only.

| Horizon | Train / test pairs | MAE | RMSE | Skill | Interval coverage | Decision |
|---|---:|---:|---:|---:|---:|---|
| 30 min | 1,604 / 537 | 0.116 | 0.159 | 0.867 | 77.5% | EXPERIMENTAL; interval undercovers. |
| 1 h | 1,603 / 536 | 0.128 | 0.179 | 0.853 | 74.6% | EXPERIMENTAL; interval undercovers. |
| 6 h | 1,594 / 526 | 0.261 | 0.357 | 0.707 | 68.3% | EXPERIMENTAL; interval undercovers. |
| 24 h | 1,565 / 490 | 0.574 | 0.740 | 0.383 | 55.7% | NOT READY for publication. |
| 7 d | 1,280 / 205 | 0.718 | 0.911 | −0.017 | 63.9% | NOT READY; worse than train-mean baseline. |
| 30 d | 191 / 0 | — | — | — | — | NOT READY; insufficient chronological folds. |
| Season / cycle / year | — | — | — | — | — | NOT READY; insufficient multi-year evidence. |

Three disjoint walk-forward folds gave 30-minute MAE 0.072/0.099/0.111 m, 1-hour 0.081/0.110/0.121 m and 6-hour 0.160/0.229/0.229 m. The 24-hour first fold had negative skill (−0.049). Seven days had one fold with only 27 test pairs, below the requested minimum of 50. Autocorrelation at lags 1, 48 and 336 was 0.977, 0.626 and 0.309; this short record does not prove an annual or stable weekly cycle.

A train-only-tercile 1-hour class persistence benchmark gave 98.5% accuracy and 0.796 macro F1 on 536 test pairs. **527 of 536 test targets were in the high class**, so this score is highly imbalanced and not evidence of robust three-class performance. K-means and the existing Random Forest models were not trained on production because Daily Features and a quality-approved dataset do not yet exist.

These retrospective benchmarks use observation timestamps. Because the initial 45-day NOAA file was loaded in one batch, historical `first_seen_at` cannot reconstruct what was actually available at each earlier issue time. Operational real-time skill remains **NOT VERIFIED** until a forward-collected availability record exists.

## 11. Unit and Integration Evidence

- Full suite in Docker with current source: **147 passed, 2 skipped**, one upstream Starlette deprecation warning. TL07-A tests cover single/multiple/whole-day gaps, pending publication, corrections, duplicates, invalid and late flags, high uncertainty, no imputation chaining, chronological lag-Ridge training, reconciliation, HST completeness, causal seasonal decomposition, paired correlation, chronology and horizon refusal.
- A production Compose `preprocessing` image was built, then its **one-off** read-only `services.preprocessing.scripts.review` command called the real authenticated Database API on the production internal network. It read all 2,160 Silver rows and reproduced cadence 30 minutes, 17 missing slots and 2,160 unverified rows. The persistent Preprocessing service was not started.
- A read-only SQL transaction on Cloud SQL independently checked counts and computed one HST daily feature without an upsert. Production `silver.daily_wave_features`, `gold.model_registry`, `gold.time_series_metrics`, `gold.predictions` and `gold.classifications` each remained at 0 rows.
- Real GCS ADC from a one-off container wrote one **synthetic** derived JSON object under `gs://waimea-noaa/test/tl07a/derived/`, downloaded it, verified SHA-256 and deleted that exact generation. No official derived artifact or model was published.
- A direct `docker run --env-file .env.production` first failed because Docker preserves shell-style quotes in the URL; the Compose overlay parsed the file correctly. The successful production validation used Compose. No source or service data was changed by this error.

## 12. Production Validation and Deployment State

TL07-A code ran against live Cloud SQL and GCS from one-off containers on the VM. The built `waimea-wavecast-backend-preprocessing:latest` image contains the review command, but the long-running Preprocessing worker remains stopped because its current FastAPI entrypoint still instantiates `FakeDatabaseGateway`. Database API, Ingestion, Nginx and Minecraft configuration were not restarted or modified by TL07-A. No Scheduler, Analysis, Prediction, Classification or ML training job was started. The quality decision from real data is `INSUFFICIENT_DATA`; therefore no production forecast or imputed observation was written.

## 13. Horizon Readiness

| Horizon | Research capability | Production prediction |
|---|---|---|
| 30 min, 1 h, 6 h | Persistence benchmark and contract implemented; positive single-window skill. | NOT READY: unverified source QC, undercovered intervals, no forward availability test. |
| 24 h | Benchmark implemented; unstable fold skill and weak interval coverage. | NOT READY. |
| 7 d | Benchmark implemented; one negative holdout skill and a short fold. | NOT READY. |
| 30 d | Contract and refusal implemented. | NOT READY: no held-out target pairs. |
| Season, cycle, year | Probabilistic outlook contract; explicit refusal. | NOT READY: insufficient multi-year data. |

## 14. Handoff Contracts for TL07-B and TL08

- TL07-B: replace the Preprocessing FastAPI fake gateway with authenticated HTTP gateway; calibrate station publication delay from **ongoing** arrivals; set approved quality thresholds; persist gap/quality audit and derived dataset references; run QC before Daily Features; recompute affected HST day/week/month/season after late or corrected NOAA observations. Keep imputed values out of Silver Raw lineage and ML ground truth. Add job idempotency and bounded scheduling separately.
- TL08: collect longer, quality-checked history; train models per supported horizon; evaluate against persistence, seasonal-naive and train-only mean baselines over multiple seasons; calibrate prediction intervals and test coverage; register versioned artifacts in GCS/model registry as draft until approved. Do not promote long-horizon storm-wave claims from the current snapshot.
- API/DTO: `GapResult`, `ObservationState`, `QualityDecision`, `DerivedPoint`, `Reconciliation`, `HorizonEvidence` and `Forecast` are pure Python contracts. `save_derived_dataset` returns GCS URI/generation/hash/count; a queryable production derived table needs a separately reviewed additive migration.

## Acceptance

| Criterion | Status | Evidence |
|---|---|---|
| Audit and fix existing algorithm defects | PASS | Causal seasonality, paired correlation, daily QC and imputed-target exclusion. |
| Missing detection based on observed cadence | PASS | 30-minute 99.21% support; 17 real snapshot gaps; synthetic edge tests. |
| Causal imputation contract and provenance | PASS | Unit tests and real GCS test-prefix round trip; auto-imputation disabled. |
| Quality gate calibrated for unattended production | NOT VERIFIED | Publication latency and uncertainty cannot be calibrated from one bulk load. |
| Multi-resolution descriptive analytics | PASS | Six resolutions executed on real Silver; incomplete periods flagged. |
| Short-horizon benchmark | PASS | Chronological holdout and walk-forward metrics; marked experimental. |
| Medium/long production forecast skill | FAIL | 7-day negative holdout skill; 30-day/season/year lack sufficient evidence. |
| Production read-only integration | PASS | Real Database API/Cloud SQL and VM ADC GCS test; no source mutation. |
| Automated preprocessing/scheduler | NOT VERIFIED | Deliberately deferred to TL07-B; persistent worker still uses fake gateway. |
