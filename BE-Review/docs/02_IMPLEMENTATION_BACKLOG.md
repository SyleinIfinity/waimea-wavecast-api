# 02 — Backlog lập trình cụ thể theo service, module và file

> **Quy ước:** Các path sau là cấu trúc đích của repository. Tất cả module dưới `apps/` và `packages/` đang là **việc phải lập trình**, không tồn tại sẵn trong scaffold của bộ tài liệu này.
>
> **Định nghĩa hoàn thành một module:** có type hints, validation, log, retry khi phù hợp, unit test, integration test/mock, tài liệu input/output, xử lý lỗi, idempotency và OpenAPI contract (nếu có API).

## A. Shared packages — code một lần, tái sử dụng mọi service

| File/path phải viết | Hàm / trách nhiệm | Test tối thiểu |
|---|---|---|
| `packages/common/config.py` | `Settings(BaseSettings)` đọc env; kiểm tra `DB_NAME`, bucket, timeout, poll, CORS; không log secrets | env thiếu/trống, validation |
| `packages/common/logging.py` | `configure_logging`, JSON line, UTC, request_id/run_id, mask password/token | log không chứa bí mật |
| `packages/common/http.py` | `RetryingHTTPClient` timeout, retries 429/502/503, jitter, User-Agent, 304 conditional | 200/304/403/404/429/5xx |
| `packages/common/errors.py` | DomainError, SourceUnavailable, InvalidMeasurement, DependencyUnavailable, HTTP response mapping | 422/503 mapping |
| `packages/common/security.py` | `verify_internal_token`, `verify_admin_token`, constant-time compare / OAuth strategy | missing/wrong/valid tokens |
| `packages/common/schemas.py` | Pydantic page/cursor, time-window, job statuses, error response, idempotency key | timezone parsing, pagination |
| `packages/common/time.py` | UTC `aware datetime`, HST day boundaries, effective cutoff | midnight HST / UTC conversion |
| `packages/noaa/urls.py` | `annual_url(year)`, `monthly_url(year,month)`, realtime URL builder, allowlist | 2005, current month |
| `packages/noaa/parser.py` | `detect_header`, `parse_stdmet`, `parse_observed_at`, map old header aliases `BAR -> PRES`, 2/4-digit year, inspect `#` units | 3 file variants, corrupted row |
| `packages/noaa/normalize.py` | `normalize_measurement`, sentinel per-field (not global), visibility mi→nmi if header says mi, preserve null, negatives PTDY/TIDE | 99/999/9999, VIS, -TIDE |
| `packages/noaa/hash.py` | SHA-256 original bytes, canonical measurement hash **excluding fetch time/source alias**, row hash | order independent, stable |
| `packages/noaa/priority.py` | Source arbitration (annual > monthly > realtime), correction/quality precedence, conflict rules | no downgrade to realtime |
| `packages/gcs/client.py` | upload_if_absent(... `if_generation_match=0`), download pinned generation, metadata/checksum verification | 412 object exists, retry |
| `packages/gcs/paths.py` | Generate immutable object paths by station, feed, year, sha/version | no collisions/path traversal |
| `packages/gcs/manifest.py` | JSON manifest feature order, code hash, model sha, source snapshot, generation | serialize/verify |
| `packages/ml/features.py` | `build_daily_lags`, `encode_direction_sin_cos`, `build_next_day_features`, no target leakage | first/last day, missing |
| `packages/ml/labels.py` | `assign_wave_category` from versioned scheme, no invented safety rating | boundaries, null |
| `packages/ml/metrics.py` | regression/classification/clustering metrics with appropriate nulls | fixtures |
| `packages/ml/artifacts.py` | `save_bundle`, `load_bundle`, exact generation+hash, cache loaded model, compatible feature names | tampered artifact, feature mismatch |

## B. `database_api` — đơn vị truy cập SQL duy nhất

**Cần viết:** `apps/database_api/app/main.py`, `api/v1/{stations,observations,statistics,analysis,clusters,predictions,classifications,models,health}.py`, `api/internal/{ingest,bronze,silver,gold,training,jobs}.py`, `db/{engine,transaction,health}.py`, `repositories/{bronze,silver,gold,models}.py`, `schemas/{noaa,analytics,ml,api}.py`, `services/{query,bulk_upsert,revision,job_state}.py`.

| Nghiệp vụ | Chi tiết cần code | Bảng thật v2.0 |
|---|---|---|
| Connection | SQLAlchemy 2 + psycopg/asyncpg, pool max, transaction timeout, no SQL interpolation, schema-qualified SQL | Cloud SQL |
| NOAA metadata | repository CRUD station/feed/file/run; GCS generation validated, unique hash | bronze.stations/source_feeds/source_files/ingestion_runs |
| Batch raw | bulk insert structured rows JSONB/copy, dedup by `(file_id,row_sha256)`, store raw malformed line | bronze.raw_observations |
| Current observations | atomic `INSERT ... ON CONFLICT` consistent with revision triggers; source priority/correction policy; deadlocks retried | silver.wave_observations / revisions |
| Quality | record issues and gaps; rejected reasons | silver.data_quality_issues / observation_gaps |
| Features | upsert daily features with calculation version and QC info | silver.daily_wave_features |
| Gold results | atomic replace of **one specific period/run/version**, not wholesale truncate | gold.time_series_metrics / trend_seasonal_components / correlations |
| ML metadata | datasets, training, model versions, evaluations and sample-level backtest | gold.dataset_splits/training_runs/model_registry/model_evaluations/evaluation_samples |
| Inference output | write predictions/classifications and later actual reconciliation | gold.predictions / classifications |
| Job control | running/success/fail, idempotency, version, duration, `409` already running | gold.processing_runs |
| Public API | paging, filters, UTC/HST explicit, JSON-safe nulls, known 404, 422 | views & tables gold/silver |

**Cảnh báo:** Không gọi PostgreSQL trực tiếp từ Frontend; không publish `postgres:5432`; user `waimea_app` phải được cấp quyền trên schema/bảng/sequences.

## C. `ingestion` — NOAA historical + incremental

**Files:** `apps/ingestion/app/main.py`, `clients/ndbc_client.py`, `jobs/backfill.py`, `jobs/poll_realtime.py`, `jobs/reconcile_archive.py`, `services/upload_raw.py`, `services/ingest_batch.py`, `services/checkpoint.py`, `schemas/source_batch.py`.

- `ndbc_client.py`: annual archive `/data/historical/stdmet/{station}h{year}.txt.gz`; monthly `/data/stdmet/{Mon}/{station}{month}{year}.txt.gz`; realtime endpoint chính thức kiểm chứng từ notebook. HTTP timeouts, response size cap, redirects allowlisted, status 404=nguồn chưa có chứ không phải lỗi parser.
- `backfill.py`: chỉ chạy lúc khởi tạo hoặc yêu cầu admin; dải năm 2005→năm hiện tại; ghi unavailable periods, không fabricate missing; xử lý tháng hiện tại cập nhật.
- `poll_realtime.py`: 30-minute check; thu thập cửa sổ overlap (7 ngày trong notebook hoặc config), nhưng dedup; không coi 0 new rows là thất bại.
- `reconcile_archive.py`: lịch kiểm tra lại archive vài ngày/tháng gần đây hoặc annual khi xuất hiện file; ghi correction revisions, giữ bytes gốc trên GCS.
- `upload_raw.py`: SHA bytes, immutable object path, if generation 0, content-type, object generation; không lưu local path máy developer làm production reference.
- `ingest_batch.py`: POST internal database API metadata + rows theo batch; state machine `downloaded→uploaded→registered→parsed→applied`; retry independent step.
- `checkpoint.py`: nguồn & period + hashes, last-success-at, current-run, compare ETag/Last-Modified (chỉ là optimization, không thay checksum).
- API nội bộ (worker tự cung cấp): `POST /internal/v1/ingestion/poll`, `POST /internal/v1/ingestion/backfill` (auth admin/internal; job async).
- Tests: same payload twice, updated old timestamp, partial HTTP response, GCS upload succeeded/DB failed, archives unavailable, wrong header, 2005/2006 absence.

## D. `preprocessing` — Bronze→Silver→daily features

**Files:** `apps/preprocessing/app/main.py`, `jobs/process_batch.py`, `jobs/rebuild_days.py`, `quality/{checks,rules,issue_recorder}.py`, `features/{daily,coverage,direction}.py`, `services/source_arbitration.py`.

- Hệ thống đọc raw/source_file + source priority từ internal API; parser hoàn thành ở shared package, tránh parse hai lần khác logic.
- Validation 14 cột; NULL với sentinel; negative allowed PTDY/TIDE; wave_height >= 0, direction 0..360; hard reject vs soft warning phân biệt; không tự interpolate raw NOAA để hợp thức hóa.
- Provenance: chọn record canonical, hash, source_file_id, revision_no trigger; quality_flags JSONB, missing_fields TEXT[].
- Daily features theo HST: `count`, `valid_wave_count`, mean, median, max, p95, stddev, circular mean direction sin/cos, period and sea-temp; **không lấy trung bình số học góc**.
- Coverage: tính `valid / expected` CHỈ khi có bằng chứng cadence station thực tế; 48 là kỳ vọng *giả định*, không hard-code; đưa `coverage_ratio` NULL khi không rõ expected.
- Recompute affected Hawaii days including earlier corrections, invalidate Gold period/model forecast actual reconciliation; tính theo idempotent upsert.
- API: `POST /internal/v1/preprocessing/batch/{file_id}`, `POST /internal/v1/preprocessing/daily/rebuild?start=...&end=...`.
- Tests: duplicate timestamps, partial missing 14 fields, stale annual vs realtime, HST boundaries, direction 359 & 1 deg, missing-heavy day.

## E. `analysis` — chuỗi thời gian, trend/seasonal, correlation

**Files:** `apps/analysis/app/main.py`, `jobs/rebuild_metrics.py`, `statistics/{aggregation,histogram,scatter}.py`, `timeseries/{resampling,decomposition}.py`, `correlation/{matrix,methods}.py`, `schemas/analysis.py`.

- `aggregation.py`: day/week/month by HST. For week define week starting Monday HST; month date 1st HST. Save count, valid count, median, p95, min, max, stddev; coverage + source QC.
- `histogram.py` and `scatter.py`: output can be dynamically computed from Silver per request **or** materialized in Gold if expensive; document which. `gold.time_series_metrics` already stores aggregated stats, not a histogram bins table. Do not invent an existing histogram table.
- `decomposition.py`: choose stable monthly series, `seasonal_decompose` or STL, period=12 months if enough continuous samples; if insufficient data return explicit `insufficient_data`, not fabricate. Trend/seasonal/residual into `gold.trend_seasonal_components`.
- `matrix.py`: Pearson/Spearman on paired non-null samples, minimum sample count, no numeric encoding arbitrary labels, circular direction uses sin/cos or exclude; store `gold.correlations`.
- API worker: `POST /internal/v1/analysis/rebuild`, GET public via database-api: stats/day-week-month, trend, seasonal, correlations.
- Tests: 2-point correlation not enough, constant series (undefined corr), irregular sampling, January DST irrelevant HST, month/week windows.

## F. `clustering` — K-means, daily assignments

**Files:** `apps/clustering/app/main.py`, `jobs/assign_daily.py`, `inference/load_clusterer.py`, `inference/assign.py`, `metrics/drift.py`.

- K-means **fit trong `training/`**, không fit trong inference; use StandardScaler/RobustScaler from training bundle.
- Predict cluster ID on daily features (wave height median/max, period, optional sin/cos direction), stable feature ordering.
- Store `gold.cluster_centroids`, `gold.cluster_assignments` with model version; labels are descriptive post-hoc only, ID arbitrary.
- Evaluate silhouette/DBI/Calinski where applicable on held-out characterization, not probability; avoid label leakage or manual relabeling to improve score.
- API worker: `POST /internal/v1/clustering/assign?date_hst=...` ; public `GET /api/v1/clusters`.

## G. `prediction` — infer only (hồi quy)

**Files:** `apps/prediction/app/main.py`, `inference/{model_loader,feature_builder,predictor}.py`, `jobs/daily_predict.py`, `services/reconcile_actual.py`.

- Select `gold.model_registry` `status='active'`, task regression; load immutable GCS generation / SHA; verify feature version and snapshot.
- Latest complete HST day T → create lag features T−6..T; target T+1; enforce all source times <= feature cutoff; if insufficient, record no prediction / error status.
- `predictor.py`: return numeric significant wave height (m), model version, feature cutoff, target day, interval if calibrated; prevent negative values by separately validated post-processing (not silently modify metrics).
- `reconcile_actual.py`: when NOAA observations for target day finalized, backfill actual_value/absolute_error and evaluated_at; do not treat forecast as observation.
- `gold.predictions` stores issued_at; avoid repeated identical forecasts via idempotency policy/application-level unique key if necessary (v2 table does not require unique issued-at constraint).
- Worker API: `POST /internal/v1/prediction/run`; public `GET /api/v1/predictions/latest`, historical.

## H. `classification` — predicted wave category

**Files:** `apps/classification/app/main.py`, `inference/{model_loader,predictor,label_scheme}.py`, `jobs/daily_classify.py`, `services/reconcile_actual.py`.

- Cần lựa chọn scheme `gold.classification_schemes` **trước khi code train**: học từ quantile trên TRAIN only, hoặc nghiệp vụ có tham khảo nguồn học thuật; không tùy ý gán nhãn an toàn tắm biển.
- Huấn luyện classifier riêng (ví dụ RandomForestClassifier), lưu preprocessing, label schema/version, model artifact lên GCS; inference đọc observation cutoff.
- `confidence` là max class score/probability theo model, **không mặc nhiên calibrated confidence**. Chỉ gọi xác suất hiệu chỉnh khi thực hiện calibration/test calibration.
- Reconcile actual_label từ quan trắc thực tế cùng scheme; save `gold.classifications`.
- Worker API: `POST /internal/v1/classification/run`; public GET current/history and scheme definition.

## I. `training` — offline experiments và model promotion

**Files:** `training/{build_dataset,split_by_time,train_regression,train_classification,train_clustering,evaluate,promote_model}.py`, `training/configs/{regression,classification,clustering}.yaml`.

- Freeze immutable Parquet snapshot to GCS, save `gold.dataset_splits`: dataset_sha256, GCS generation, feature/window, cutoff, label.
- For regression train baseline persistence/seasonal naive + Ridge + RandomForestRegressor (candidate), or HistGradientBoosting if measured benefit; use pipeline with imputer/scaler as required, fit on train only.
- Chronological train-validation-test, separate future holdout (no shuffle), time-window generation and embargo/gap if horizon overlap, MAE/RMSE/R²; compare with baseline.
- Classification label rules train only; optimize macro-F1, classification report, confusion matrix, handle imbalance.
- K-means choose k (silhouette etc), scaler, save centroids and assignments.
- Artifacts: `model.joblib`, `preprocessor.joblib` or one Pipeline, `manifest.json`, requirements/code hash; upload immutable GCS objects; model registry and evaluation metadata in DB; only promote validated model after verification.
- Explicit `train` CLI/one-off job, not an endpoint reachable to public clients. Admin approval for model promotion.

## J. `scheduler` — dependency-aware orchestration

**Files:** `apps/scheduler/app/main.py`, `schedules/registry.py`, `schedules/dispatch.py`, `locks/advisory_lock.py`, `services/job_status.py`.

- 30 min: poll, then preprocess only if changed; hourly/daily: analysis; daily after Hawaii day ends and data reasonably complete: prediction/classification/cluster assignment; nightly catch-up old corrections.
- POST internal worker endpoint with `job_id`, `idempotency_key`, request scope; track state in `gold.processing_runs`/`bronze.ingestion_runs`, use PostgreSQL advisory lock or equivalent to avoid overlap (application implementation).
- Scheduler should not run duplicate replicas until distributed lock implemented. Backoff + error budget. Jobs may span more than HTTP timeout: return `202 Accepted` and poll status.

## K. Nginx, Docker, CI/CD & observability

**Files:** root `Dockerfile`, `compose.yaml`, `compose.prod.yaml`, `nginx/nginx.http.conf`, `nginx/nginx.https.conf`, `.dockerignore`, `.env.example`, `.github/workflows/{lint_test,build}.yml` (workflow to implement).

- Build per-service images (can share base stage), run unprivileged; expose only Nginx 80/443; cloud-sql-proxy internal; secrets injected runtime (not image).
- TLS cert renewal automation, Nginx reload, monitor cert expiry; restrictive firewall; backup strategy.
- Structured JSON logs `timestamp,service,request_id,run_id,station_id,model_id,job_status`; avoid sensitive payload/token.
- Metrics: ingestion lag, NOAA request 5xx, GCS upload errors, DB pool saturation, inference failure rate, job duration, data coverage, latest successful snapshot.
- Test: pytest unit/integration/contract/e2e + Ruff/typing; migrations applied in CI test DB; `docker compose config`, lint `nginx -t` in container.

## L. Nhiệm vụ giao nhau cần phân công rõ

- Cùng trường `WVHT` nhưng mỗi stage phải có **definition** và mapping thống nhất; `target_metric='WVHT'` của SQL v2 (không phải `wave_height_m` trong FK measurement_definitions).
- API pagination/cursor code chỉ thuộc Database API; worker không tự thiết kế format tùy ý.
- Models tải GCS theo exact generation, tuyệt đối không tự lấy file version mới nhất chỉ dựa trên object name.
- Các bảng `evaluation_samples` là lịch sử kiểm thử **offline**; `predictions` là dự báo đã phát hành **online**.
