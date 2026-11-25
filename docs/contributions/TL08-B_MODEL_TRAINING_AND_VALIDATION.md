# TL08-B — NOAA model training and validation

Status: **PARTIALLY VERIFIED IN PRODUCTION; NOT READY FOR PROMOTION** (2026-11-13 UTC). This report distinguishes retrospective model fitting from forward forecasting.

## Source and reproducibility

- Station 51201; historical NOAA Silver and daily Gold derived through the TL08-A pipeline. Training reads verified active GCS data lake export metadata and records a source-version manifest. It rejects suspect, rejected, unverified and imputed Daily Features. The exact dataset snapshot and SHA-256 are stored on private GCS and checked after download.
- Version `tl08b-20261113-v1`; next-day dataset SHA-256 `d18d56980c08c2bae22fcca0e76bd1f0157f9810767c6c21d53517f47592645e`; source manifest SHA-256 `2783dde516d02d1961b286c60a97e817618006f2d2224105b9d5304180f8d08e`. Models use RandomForest with 100 trees, fixed seed 42, train-only median imputation inside the artifact, and chronological train/validation/test splits with target embargo.
- Seven-day lagged daily wave-height and dominant-period values, direction sine/cosine, season sine/cosine, valid wave count and coverage are used. Missing history days are never bridged. Daily historical features lack reliable publication-time metadata, so historical as-of availability remains unproven. This is a material readiness blocker.
- Exact source snapshots, runs, model IDs and evaluation metrics are in Cloud SQL registry; artifact round trips were verified by `ModelStore.save`. JSON evidence: [next day](TL08-B_NEXT_DAY_RESULTS.json), [daily horizons](TL08-B_DAILY_HORIZON_RESULTS.json), [short horizons](TL08-B_SHORT_HORIZON_RESULTS.json), [24 hours](TL08-B_24H_RESULTS.json), [walk-forward](TL08-B_WALKFORWARD_RESULTS.json), [classification baselines](TL08-B_CLASSIFICATION_BASELINES.json), [cluster assignments](TL08-B_CLUSTER_ASSIGNMENTS.json). All seven JSON reports were also uploaded to private GCS, downloaded and byte-verified; [GCS URI, generation and SHA-256 index](TL08-B_GCS_EVIDENCE.json).

## Retrospective holdout

| Task / horizon | Model ID | Test n | Model result | Baseline / other evidence | Decision |
|---|---:|---:|---|---|---|
| WVHT next HST day median | 8 | 745 | MAE 0.264 m; RMSE 0.392 m; R² 0.645 | Persistence MAE 0.274 m | NOT_READY |
| WVHT exact UTC +30 min | 13 | 1925 | MAE 0.0848 m; RMSE 0.1149 m | Persistence MAE 0.0846 m | NOT_READY |
| WVHT exact UTC +1 h | 14 | 1924 | MAE 0.0966 m; RMSE 0.1323 m | Persistence MAE 0.0938 m | NOT_READY |
| WVHT exact UTC +6 h | 15 | 1914 | MAE 0.1919 m; RMSE 0.2782 m | Persistence MAE 0.1755 m | NOT_READY |
| WVHT exact UTC +24 h | 16 | 1883 | MAE 0.4237 m; RMSE 0.5680 m | Persistence MAE 0.3655 m | NOT_READY |
| Next-day LOW/MEDIUM/HIGH | 9 | 745 | Accuracy 0.718; macro F1 0.716 | Train-majority accuracy 0.332; persistence accuracy 0.717 | NOT_READY |
| Daily K-means | 10 | 3476 train | K=2; sampled silhouette 0.269; Davies–Bouldin 1.394 | Calinski–Harabasz 1668.67; 2 centroids registered | NOT_READY |
| WVHT 7 HST days ahead, day median | 11 | 717 | MAE 0.376 m; RMSE 0.507 m; R² 0.413 | Persistence MAE 0.504 m; interval 90% empirical coverage 0.948 | NOT_READY |
| WVHT next 30 HST days mean | 12 | 581 | MAE 0.117 m; RMSE 0.146 m; R² 0.897 | Persistence MAE 0.359 m; interval 90% empirical coverage 0.916 | NOT_READY |

The 30-day target is the **mean of the next 30 observed daily medians** with at least 27 valid days, not an individual sea-state prediction. The 7-day target is one future day's median. Neither result is evidence of a live forecast. Regression intervals use the 90th percentile of validation absolute residuals; they are experimental marginal intervals. Short-horizon samples use exact UTC target timestamps and complete observed 30-minute lag windows; no interpolated or suspect target is admitted. All four exact-observation models underperform persistence on MAE, so high R² on the shorter horizons is not a promotion argument.

The classification confusion matrix (actual rows, predicted columns; low/medium/high) is `[[229,58,1],[58,141,48],[7,38,165]]`. Precision by class is 0.779/0.595/0.771 and recall is 0.795/0.571/0.786. Thresholds 1.25 m and 1.79 m came from train only. The train-majority label was medium (holdout accuracy 0.332, macro F1 0.166). Persistence accuracy was 0.717 and macro F1 0.714, nearly matching the model. These are retrospective estimates. Class probabilities have not been calibrated. The K-means silhouette uses a deterministic 1000-row sample to bound VM memory; training uses all 3476 rows. A separate pinned-model run generated **5160 retrospective cluster assignments** for model 10, verified immutable GCS object `gs://waimea-noaa/training/clusters/model=10/assignments-4eed31a19d1925c4bbf80cb7ca5e882bfb404d8d3e91214c3f96bf068b83767c.json` (generation `1794544158682544`, SHA-256 `4eed31a19d1925c4bbf80cb7ca5e882bfb404d8d3e91214c3f96bf068b83767c`), and persisted them through processing run 2. `/api/v1/clusters` returned model 10 assignments in production. These are exploratory clusters, not a verified public safety rating.

## Expanding-window audit

Three disjoint test blocks with label embargo were fitted on real daily data. Seven-day model MAE by fold: **0.393, 0.463, 0.376 m**, versus persistence **0.555, 0.603, 0.505 m**. Thirty-day mean model MAE: **0.129, 0.132, 0.114 m**, versus persistence **0.348, 0.437, 0.355 m**. Each fold used an expanding earlier training window and separate validation period; exact dates and counts are in the evidence JSON. This supports retrospective stability, while publication-time availability and live performance remain unverified.

## Quality gate and remaining work

All nine registry models remain DRAFT. Production promotion now requires an explicit `evaluation_metrics.readiness=READY`, gate version, successful training, test evaluation and verified artifact generation/SHA-256. The current models do not meet this gate. This protects the realtime scheduler from producing unsupported forecasts.

Seasonal and annual probabilistic outlooks are NOT_READY: multi-year climatology, uncertainty calibration and independent validation have not been established. Short-horizon folds, seasonal-naive baselines, missing-period and seasonal error breakdowns, calibrated class probabilities, verified forward feature availability and live ground truth are also outstanding. The production API remains healthy independently of these training jobs.

Tests: 14 TL05/TL08-B tests passed in an isolated container. They include future-label exclusion, exact timestamp target QC and split embargo. No synthetic test outcome is counted as production model evidence.
