# 06 — PostgreSQL v2.0 (25 bảng) ↔ GCS: mapping và business transactions

**Source of truth:** `Waimea_WaveCast_DB_GCS_v2.0/01_init_full.sql`, đã tạo trong trao đổi trước. Bản blueprint không tự thay schema, không chạy DDL trên Cloud SQL và không tự tạo bucket. Trước code thực tế, copy SQL vào repo `database/migrations/` và xác nhận `03_verify.sql` trên Cloud SQL.

## A. Bronze — 6 bảng

| Bảng thật | Writer | Reader | Trường/chức năng quan trọng |
|---|---|---|---|
| `bronze.stations` | migration/admin | all | `station_id='51201'`, local_timezone |
| `bronze.measurement_definitions` | migration/admin | parser/API | `noaa_code`, `silver_column`, missing_codes |
| `bronze.source_feeds` | migration/admin | ingestion | feed_key, url_template, data_priority, poll_interval |
| `bronze.ingestion_runs` | ingestion via database-api | ops | request_url, status, counts, min/max_observed_at |
| `bronze.source_files` | ingestion via database-api | preprocess/audit | `gcs_uri`, `gcs_generation`, `content_sha256`, `parse_status`, `gcs_upload_status` |
| `bronze.raw_observations` | ingestion via database-api | preprocess | source line/raw_values/row_sha256; optional large raw payload |

## B. Silver — 5 bảng

| Bảng | Writer | Reader | Contract |
|---|---|---|---|
| `silver.wave_observations` | preprocessing/database-api | analysis,ml,public-api | 1 canonical row per `(station_id,observed_at)`; 14 NOAA columns; `source_file_id`, measurement_sha256, quality_status |
| `silver.wave_observation_revisions` | triggers via updates | audit | historical diff snapshots, never truncate in demo |
| `silver.data_quality_issues` | preprocessing | monitoring | rule, severity, error context and status |
| `silver.observation_gaps` | preprocessing | monitoring | start/end gap; depends on validated cadence |
| `silver.daily_wave_features` | preprocessing | analysis/training/inference | HST day, sample_count, valid_wave_count, mean/median/max/p95/period/SST, quality metadata |

## C. Gold — 14 bảng (phân loại theo mục đích demo)

| Bảng | Category | Demo policy |
|---|---|---|
| `gold.processing_runs` | processing audit | GIỮ |
| `gold.time_series_metrics` | regenerable pattern | XÓA CÓ PHẠM VI + rebuild |
| `gold.trend_seasonal_components` | regenerable pattern | XÓA CÓ PHẠM VI + rebuild |
| `gold.correlations` | regenerable pattern | XÓA CÓ PHẠM VI + rebuild |
| `gold.model_registry` | model metadata | GIỮ |
| `gold.cluster_centroids` | artifact-derived metadata | GIỮ hoặc rebuild nếu logic đã kiểm chứng; mặc định GIỮ |
| `gold.cluster_assignments` | regenerable pattern | XÓA CÓ PHẠM VI + inference lại |
| `gold.classification_schemes` | scheme metadata | GIỮ |
| `gold.predictions` | regenerable inference output | XÓA bản demo có phạm vi; giữ forecast issuance thực nếu production |
| `gold.classifications` | regenerable inference output | XÓA bản demo có phạm vi; giữ historical issuance thực nếu production |
| `gold.dataset_splits` | experiment lineage | GIỮ |
| `gold.training_runs` | experiment lineage | GIỮ |
| `gold.model_evaluations` | offline evaluation | GIỮ |
| `gold.evaluation_samples` | offline test samples | GIỮ |

**Cảnh báo:** Không gọi `TRUNCATE gold.* CASCADE`; thay vào đó chỉ dùng SQL DELETE theo scope trên môi trường test/demo, kèm dry-run SELECT COUNT. Nếu database production chứa issuance dự báo theo lịch, việc xóa có thể phá lịch sử audit: tốt nhất một demo dataset/range riêng hoặc snapshot backup và restore.

## D. GCS object prefixes — chỉ giữ file, không quan trắc canonical

```text
gs://$GCS_BUCKET/
  noaa/raw/historical/YYYY/<sha256>-51201hYYYY.txt.gz
  noaa/raw/monthly/YYYY/MM/<sha256>-source.txt.gz
  noaa/raw/realtime/YYYY/MM/DD/<sha256>-snapshot.txt
  datasets/daily-features/<dataset-version>.parquet
  datasets/training/<task>/<dataset-version>/dataset.parquet
  models/<task>/<model-version>/model.joblib
  models/<task>/<model-version>/manifest.json
  training-logs/<training-run-id>/train.json
```

- `bronze.source_files.gcs_uri` liên kết raw object; `gcs_generation` và `content_sha256` liên kết **phiên bản bytes**.
- `gold.dataset_splits.dataset_gcs_uri` liên kết snapshot train, thêm generation và SHA256.
- `gold.model_registry.artifact_gcs_uri` liên kết model artifact, thêm generation và artifact hash.
- `gold.training_runs.log_gcs_uri` liên kết log; không nhét log lớn vào database.
- GCS bucket riêng tư với Uniform + Public Access Prevention; upload `if_generation_match=0`; không dùng tên `latest.joblib` để ghi đè model.

## E. Ghi dữ liệu bảo đảm nhất quán

**SAGA / retry**:

```text
1. NOAA download → SHA-256 gốc
2. GCS object upload (if-generation-match=0)
3. Database API transaction: source_file metadata (gcs_uri,generation,hash,status)
4. Parse + raw rows / Silver canonical upsert (transaction per batch)
5. Mark ingestion run success; queue downstream affected dates
```

Nếu bước 2 thành công, bước 3 lỗi, retry đọc object xác thực cùng SHA/generation rồi tạo metadata; không upload bằng tên khác tùy tiện. Tránh `NULL` source_file_id khi insert Silver vì FK NOT NULL. Nếu bước 4 fail halfway, batch retries dựa unique constraints và measurement hash, không nhân bản rows.

### Policy chọn canonical Silver

- `PRIMARY KEY (station_id, observed_at)` bảo đảm một canonical row/timestamp.
- Nếu cùng measurement hash và quality/source như cũ, chỉ update `last_seen_at` nếu yêu cầu, **không tạo revision mới**.
- Nếu khác hash, so source priority, QC, source published date và correction policy; update canonical trong transaction để trigger ghi snapshot cũ. Không để realtime ghi đè annual đã chốt.
- Trong tình huống NOAA annual có một số giá trị `NULL` mà realtime từng có, phải định nghĩa **replace toàn-record** hay **merge theo trường**. Đề xuất mặc định replace theo authoritative source nhưng **ghi revision**, không trộn trường từ nguồn khác không truy vết; ngoại lệ phải có đặc tả rõ.

## F. Xử lý schema security và quyền

- User PostgreSQL `waimea_app` không mặc định được grant mọi quyền nếu script chạy bởi `postgres` admin; cần quyền USAGE schema, SELECT/INSERT/UPDATE trên các bảng tương ứng, sequence usage. Production nên tách user migration/owner khỏi app nếu khả thi.
- Không lưu mật khẩu DB trong `.env` commit; ứng dụng lấy từ VM secret manager/host protected env; chỉ Cloud SQL Proxy cần Cloud SQL Client IAM, DB user/password vẫn cần.
- Kiểm tra `max_connections` / connection pool khi chạy nhiều FastAPI services: 1 DB API với pool nhỏ tốt hơn mỗi worker tự mở pool.

## G. Hạn chế của thiết kế SQL v2 hiện hữu

1. Không có `gold.histogram_bins` riêng; histogram API phải tính từ Silver hoặc thêm migration rõ ràng.
2. `gold.model_registry` chưa có unique partial constraint chỉ cho 1 active model theo target/task. Cần transaction guard hoặc migration khi chốt policy.
3. `gold.predictions` có thể lưu cùng target nhiều issuance; cần xác định idempotency key per `(model_id, station_id, target_date_hst, feature_cutoff_date_hst, run version)` trước khi thêm unique index.
4. Dataset split đang lưu khoảng thời gian, nhưng có thể cần object manifest chứa exact row IDs/ hash để tái lập khi Silver được NOAA hiệu chỉnh.
5. Thống kê `coverage_ratio` cần cadence NOAA thực tế, không hard-code 48/24h nếu không chắc.
6. `Gold processing_runs` trạng thái job chưa là full job queue: scheduler phải triển khai lock/retry ngoài DB và không tự cho rằng table làm sẵn orchestration.
