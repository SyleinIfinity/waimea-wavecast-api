# 08 — Test strategy, Acceptance Tests, monitoring và demo đồ án

## 1. Các cấp độ test

| Cấp | Bộ test | Không phụ thuộc external cloud? |
|---|---|---|
| Unit | NOAA parsing, sentinel, units, HST, lag features, labels, thresholds | Có |
| Property-based | hash deterministic, parser roundtrip, pagination order, date cutoff | Có |
| Integration | DB API↔PostgreSQL test instance, migrations, transactions & triggers | Dùng PostgreSQL test container |
| Integration GCS | GCS upload+generation, 412, retries (mock/real isolated bucket) | Có mock, cloud test riêng |
| Contract | OpenAPI schemas, docs, validation errors, frontend generated TS types | Có |
| End-to-end | NOAA sample→Bronze→Silver→Gold→HTTP API | Fixture + docker compose |
| Cloud smoke | Proxy Cloud SQL, private GCS, TLS Nginx, IAM scopes | Cần tài nguyên GCP có thật |
| Demo rehearsal | delete pattern subset→call jobs→check rehydrate, unchanged artifacts | DB staging/test |

## 2. Bộ test case bắt buộc

| Test ID | Điều kiện/đầu vào | Kết quả mong đợi |
|---|---|---|
| ING-01 | tải annual 2007 đúng cấu trúc | SHA/source_file/row_count đúng, status success |
| ING-02 | annual 2005 unavailable | ghi unavailable, không bịa row |
| ING-03 | cùng realtime payload hai lần | không nhân đôi `silver.wave_observations` |
| ING-04 | cùng timestamp NOAA cập nhật giá trị | 1 canonical + revision cũ |
| ING-05 | GCS upload OK, DB lỗi | retry đăng ký metadata, không tạo object trùng |
| ING-06 | NOAA trả 429/503 | backoff, theo dõi errors, không xóa dữ liệu cũ |
| PAR-01 | `MM`, sentinel field-specific | NULL; không chuyển toàn bộ `99` của mọi field |
| PAR-02 | `#YY`, `#YYYY`, header BAR | observed_at đúng, PRES mapping đúng |
| PAR-03 | `TIDE=-0.4`, `PTDY=-1.2` | không loại nhầm là invalid |
| PAR-04 | VIS unit source `mi` | đổi sang nmi có unit test |
| QC-01 | thiếu WSPD nhưng có WVHT | Silver được lưu với NULL+flag |
| QC-02 | 359° và 1° | circular mean gần 0°, không gần 180° |
| QC-03 | chưa biết expected interval | coverage_ratio NULL, không tự chia cho 48 |
| DAT-01 | UTC đúng lúc Hawaii đổi ngày | date_hst đúng (UTC-10) |
| ML-01 | feature dùng target T+1 | pipeline từ chối/leakage test fail |
| ML-02 | Imputer fit trước khi split | test tự phát hiện leakage trong review |
| ML-03 | duplicate model artifact bytes | reuse pinned GCS URI; không overwrite |
| ML-04 | GCS model hash mismatch | inference fail closed, không phát prediction |
| ML-05 | Test MAE baseline vs RF | metrics được lưu và model được chọn theo rule |
| ML-06 | Target day chưa đủ quality | no prediction hoặc explicitly insufficient status |
| ML-07 | classification threshold boundary | nhãn nhất quán theo scheme version |
| ML-08 | Silhouette không áp dụng (k=1) | metric NULL + giải thích, không giả 0 |
| API-01 | time range > allowed | HTTP 422 đúng schema error |
| API-02 | invalid token POST internal | 401/403, không chạy job |
| API-03 | Nginx request `/internal/v1` | HTTP 404, không tới worker |
| API-04 | public `GET` latest | đúng timezone, version, nulls và units |
| SYS-01 | stop all containers / start again | nguồn SQL/GCS không mất |
| DEMO-01 | delete scoped Gold results | dashboard báo empty; NOAA và model registry giữ nguyên |
| DEMO-02 | gọi API rebuild và chờ job success | pattern Gold được tái tạo, dashboard trở lại |
| DEMO-03 | so training_runs count before/after | không tăng do inference/rebuild |

## 3. Rehearsal demo (CHỈ môi trường staging/test; backup trước)

**Mục tiêu của thầy:** tái tạo pattern mà không train lại. **KHÔNG** `DROP SCHEMA`, không `TRUNCATE gold.* CASCADE`, không xóa dataset GCS/training metadata. Quy trình:

1. Trước demo xác nhận đã chạy migrations, có data NOAA và model `active` trên GCS, ghi số dòng Bronze/Silver và số lượng training runs.
2. Mở dashboard; dùng Developer Tools xem API GET để chứng minh giao diện không đọc DB trực tiếp.
3. Backup DB/snapshot staging. Chỉ DELETE **bảng pattern** cho một station/range đã xác định; thứ tự tôn trọng foreign keys. Preflight đếm số hàng bị ảnh hưởng rồi mới xác nhận delete.
4. Reload dashboard: chart thiếu/missing results (HTTP 200 empty array hoặc HTTP 404 đã quy ước).
5. Gọi các internal/admin API được bảo vệ cho analysis, clustering assignment, prediction, classification (**không gọi training**).
6. Poll `GET /jobs/{id}` đến success; kiểm tra số row Gold được tái tạo, log processing_run, GCS model checksum unchanged.
7. Reload dashboard: chart và kết quả prediction trở lại; kiểm tra `gold.training_runs` và `gold.model_evaluations` bất biến.
8. Chứng minh ingestion realtime có lần thu thập gần đây, hoặc chạy poll một lần cho kết quả `0 new` hợp lệ nếu NOAA chưa cập nhật.
9. Báo cáo số lượng dữ liệu thật theo năm, nguồn missing years, completeness và đánh giá mô hình trên holdout.

## 4. SQL kiểm chứng minh họa (read-only)

```sql
SELECT 'bronze.source_files' AS item, COUNT(*) FROM bronze.source_files
UNION ALL SELECT 'silver.wave_observations', COUNT(*) FROM silver.wave_observations
UNION ALL SELECT 'silver.daily_wave_features', COUNT(*) FROM silver.daily_wave_features
UNION ALL SELECT 'gold.model_registry', COUNT(*) FROM gold.model_registry
UNION ALL SELECT 'gold.training_runs', COUNT(*) FROM gold.training_runs
UNION ALL SELECT 'gold.predictions', COUNT(*) FROM gold.predictions;

SELECT station_id, observed_at, wave_height_m, source_kind, quality_status
FROM silver.wave_observations ORDER BY observed_at DESC LIMIT 10;

SELECT model_name, model_version, task_type, algorithm, status, artifact_gcs_uri
FROM gold.model_registry ORDER BY created_at DESC LIMIT 10;

SELECT e.evaluation_id, e.task_type, e.evaluation_stage,
       e.sample_count, e.mae, e.rmse, e.r2_score, e.accuracy, e.f1_macro
FROM gold.model_evaluations e
ORDER BY evaluated_at DESC LIMIT 10;
```

## 5. Monitoring/alerts

- `ingestion_last_success_timestamp` / NOAA observation lag; `ingestion_revised_rows`.
- `ingestion_failures_total` by HTTP status/type; GCS failures and uploaded bytes.
- `silver_quality_missing_ratio`, coverage distribution, gaps count.
- `processing_job_duration_seconds`, backlogs and errors.
- `api_latency_seconds`, HTTP 4xx/5xx by route, DB connection errors, GCS permission denied.
- `model_inference_duration_seconds`, stale model/status, online MAE by forecast issuance and cohort.
- `gcs_artifact_checksum_mismatch_total` and SSL cert expiry.

Mỗi job cần request_id/run_id để tra log xuyên suốt và tránh log credentials. Cần biểu đồ giám sát/trạng thái lỗi ngay cả khi NOAA chưa có dữ liệu mới.
