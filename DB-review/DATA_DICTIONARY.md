# Waimea WaveCast Database v2.0 — Từ điển dữ liệu đầy đủ

> Các bảng dưới đây được trích xuất từ `01_init_full.sql`. Gồm cột gốc v1 và các cột thêm bằng `ALTER TABLE` ở v2. Tên cột và kiểu PostgreSQL giữ nguyên.

## Quy ước

- `PK`: primary key; `FK`: foreign key; `NOT NULL`: bắt buộc; `CHECK`: kiểm tra miền giá trị; `Default`: có giá trị mặc định; `Generated`: cột tự tính.
- Các ràng buộc nhiều cột (`UNIQUE`, `CHECK`, FK ghép) nằm trong file SQL gốc; ở đây thể hiện cấu trúc field và các cờ chính, không thay cho DDL.
- Cột thời gian quan trắc dùng `TIMESTAMPTZ` và dữ liệu local theo Hawaii dùng `DATE`.

## BRONZE
### `bronze.stations`

Thông tin trạm NOAA, tọa độ, timezone, nguồn dữ liệu.

| Trường | Kiểu PostgreSQL | Ràng buộc cấp cột |
|---|---|---|
| `station_id` | `VARCHAR(12)` | NOT NULL / PK |
| `station_name` | `TEXT` | NOT NULL / PK |
| `location_name` | `TEXT` | NOT NULL / PK |
| `provider_name` | `TEXT` | NOT NULL / PK; Default |
| `provider_url` | `TEXT` | NOT NULL / PK |
| `latitude` | `NUMERIC(9,6)` | NOT NULL / PK; CHECK |
| `longitude` | `NUMERIC(9,6)` | NOT NULL / PK; CHECK |
| `local_timezone` | `TEXT` | NOT NULL / PK; Default |
| `expected_interval_min` | `INTEGER` | CHECK |
| `measurement_start_date` | `DATE` | Nullable |
| `measurement_end_date` | `DATE` | Nullable |
| `active` | `BOOLEAN` | NOT NULL / PK; Default |
| `notes` | `TEXT` | Nullable |
| `metadata` | `JSONB` | NOT NULL / PK; Default |
| `created_at` | `TIMESTAMPTZ` | NOT NULL / PK; Default |
| `updated_at` | `TIMESTAMPTZ` | NOT NULL / PK; Default |

**Số cột: 16**.

### `bronze.measurement_definitions`

Danh mục mã chỉ số NOAA và cách ánh xạ sang Silver.

| Trường | Kiểu PostgreSQL | Ràng buộc cấp cột |
|---|---|---|
| `noaa_code` | `VARCHAR(12)` | NOT NULL / PK |
| `silver_column` | `TEXT` | NOT NULL / PK |
| `display_name` | `TEXT` | NOT NULL / PK |
| `meaning` | `TEXT` | NOT NULL / PK |
| `unit` | `TEXT` | NOT NULL / PK |
| `missing_codes` | `JSONB` | NOT NULL / PK; Default |
| `is_wave_feature` | `BOOLEAN` | NOT NULL / PK; Default |
| `description` | `TEXT` | Nullable |
| `created_at` | `TIMESTAMPTZ` | NOT NULL / PK; Default |

**Số cột: 9**.

### `bronze.source_feeds`

Kênh ingest annual/monthly/realtime của cùng trạm.

| Trường | Kiểu PostgreSQL | Ràng buộc cấp cột |
|---|---|---|
| `feed_id` | `BIGINT` | NOT NULL / PK; Generated; Default |
| `station_id` | `VARCHAR(12)` | NOT NULL / PK; FK |
| `feed_key` | `VARCHAR(32)` | NOT NULL / PK |
| `url_template` | `TEXT` | NOT NULL / PK |
| `source_format` | `VARCHAR(32)` | NOT NULL / PK; Default |
| `data_priority` | `SMALLINT` | NOT NULL / PK; CHECK |
| `poll_interval_minutes` | `INTEGER` | CHECK |
| `enabled` | `BOOLEAN` | NOT NULL / PK; Default |
| `notes` | `TEXT` | Nullable |
| `created_at` | `TIMESTAMPTZ` | NOT NULL / PK; Default |

**Số cột: 10**.

### `bronze.ingestion_runs`

Lịch sử một lần tải và nạp dữ liệu NOAA.

| Trường | Kiểu PostgreSQL | Ràng buộc cấp cột |
|---|---|---|
| `run_id` | `BIGINT` | NOT NULL / PK; Generated; Default |
| `feed_id` | `BIGINT` | NOT NULL / PK; FK |
| `scheduled_at` | `TIMESTAMPTZ` | Nullable |
| `started_at` | `TIMESTAMPTZ` | NOT NULL / PK; Default |
| `finished_at` | `TIMESTAMPTZ` | Nullable |
| `status` | `VARCHAR(16)` | NOT NULL / PK; Default |
| `request_url` | `TEXT` | Nullable |
| `http_status` | `INTEGER` | CHECK |
| `response_time_ms` | `INTEGER` | CHECK |
| `received_rows` | `INTEGER` | NOT NULL / PK; Default; CHECK |
| `inserted_rows` | `INTEGER` | NOT NULL / PK; Default; CHECK |
| `revised_rows` | `INTEGER` | NOT NULL / PK; Default; CHECK |
| `duplicate_rows` | `INTEGER` | NOT NULL / PK; Default; CHECK |
| `rejected_rows` | `INTEGER` | NOT NULL / PK; Default; CHECK |
| `min_observed_at` | `TIMESTAMPTZ` | Nullable |
| `max_observed_at` | `TIMESTAMPTZ` | Nullable |
| `error_type` | `TEXT` | Nullable |
| `error_message` | `TEXT` | Nullable |
| `details` | `JSONB` | NOT NULL / PK; Default |

**Số cột: 19**.

### `bronze.source_files`

Metadata tệp và object GCS, checksum và parse status.

| Trường | Kiểu PostgreSQL | Ràng buộc cấp cột |
|---|---|---|
| `file_id` | `BIGINT` | NOT NULL / PK; Generated; Default |
| `station_id` | `VARCHAR(12)` | NOT NULL / PK; FK |
| `feed_id` | `BIGINT` | NOT NULL / PK; FK |
| `ingestion_run_id` | `BIGINT` | FK |
| `original_filename` | `TEXT` | NOT NULL / PK |
| `source_url` | `TEXT` | NOT NULL / PK |
| `storage_format` | `VARCHAR(16)` | NOT NULL / PK |
| `gcs_uri` | `TEXT` | Nullable |
| `local_path` | `TEXT` | Nullable |
| `content_sha256` | `VARCHAR(64)` | NOT NULL / PK |
| `size_bytes` | `BIGINT` | CHECK |
| `compression_type` | `VARCHAR(16)` | NOT NULL / PK; Default |
| `source_period_start` | `DATE` | Nullable |
| `source_period_end` | `DATE` | Nullable |
| `fetched_at` | `TIMESTAMPTZ` | NOT NULL / PK; Default |
| `parsed_at` | `TIMESTAMPTZ` | Nullable |
| `parser_version` | `TEXT` | Nullable |
| `columns_in_source` | `JSONB` | NOT NULL / PK; Default |
| `source_row_count` | `INTEGER` | CHECK |
| `parse_error_count` | `INTEGER` | NOT NULL / PK; Default; CHECK |
| `parse_status` | `VARCHAR(16)` | NOT NULL / PK; Default |
| `extra_metadata` | `JSONB` | NOT NULL / PK; Default |
| `gcs_bucket` | `TEXT` | Nullable |
| `gcs_object_name` | `TEXT` | Nullable |
| `gcs_generation` | `TEXT` | Nullable, CHECK |
| `gcs_crc32c` | `TEXT` | Nullable |
| `gcs_uploaded_at` | `TIMESTAMPTZ` | Nullable |
| `gcs_last_verified_at` | `TIMESTAMPTZ` | Nullable |
| `source_etag` | `TEXT` | Nullable |
| `source_last_modified_at` | `TIMESTAMPTZ` | Nullable |
| `content_type` | `TEXT` | Nullable |
| `gcs_upload_status` | `VARCHAR(16)` | NOT NULL, DEFAULT, CHECK |

**Số cột: 32**.

### `bronze.raw_observations`

Dòng dữ liệu raw, trace và lỗi parse.

| Trường | Kiểu PostgreSQL | Ràng buộc cấp cột |
|---|---|---|
| `raw_id` | `BIGINT` | NOT NULL / PK; Generated; Default |
| `file_id` | `BIGINT` | NOT NULL / PK; FK |
| `station_id` | `VARCHAR(12)` | NOT NULL / PK; FK |
| `source_line_number` | `INTEGER` | CHECK |
| `observed_at` | `TIMESTAMPTZ` | Nullable |
| `raw_line` | `TEXT` | Nullable |
| `raw_values` | `JSONB` | NOT NULL / PK; Default |
| `row_sha256` | `VARCHAR(64)` | NOT NULL / PK |
| `parse_ok` | `BOOLEAN` | NOT NULL / PK; Default |
| `parse_error` | `TEXT` | Nullable |
| `imported_at` | `TIMESTAMPTZ` | NOT NULL / PK; Default |

**Số cột: 11**.

## SILVER
### `silver.wave_observations`

Bản ghi quan trắc hiện hành ở UTC, 14 trường NOAA.

| Trường | Kiểu PostgreSQL | Ràng buộc cấp cột |
|---|---|---|
| `station_id` | `VARCHAR(12)` | NOT NULL / PK; FK |
| `observed_at` | `TIMESTAMPTZ` | NOT NULL / PK |
| `wind_direction_deg` | `DOUBLE PRECISION` | Nullable |
| `wind_speed_ms` | `DOUBLE PRECISION` | Nullable |
| `wind_gust_ms` | `DOUBLE PRECISION` | Nullable |
| `wave_height_m` | `DOUBLE PRECISION` | Nullable |
| `dominant_period_s` | `DOUBLE PRECISION` | Nullable |
| `average_period_s` | `DOUBLE PRECISION` | Nullable |
| `mean_wave_direction_deg` | `DOUBLE PRECISION` | Nullable |
| `sea_level_pressure_hpa` | `DOUBLE PRECISION` | Nullable |
| `air_temperature_c` | `DOUBLE PRECISION` | Nullable |
| `sea_surface_temp_c` | `DOUBLE PRECISION` | Nullable |
| `dew_point_c` | `DOUBLE PRECISION` | Nullable |
| `visibility_nmi` | `DOUBLE PRECISION` | Nullable |
| `pressure_tendency_hpa` | `DOUBLE PRECISION` | Nullable |
| `tide_ft` | `DOUBLE PRECISION` | Nullable |
| `source_kind` | `VARCHAR(24)` | NOT NULL / PK |
| `source_dataset` | `TEXT` | NOT NULL / PK |
| `source_file_id` | `BIGINT` | NOT NULL / PK; FK |
| `raw_id` | `BIGINT` | FK |
| `measurement_sha256` | `VARCHAR(64)` | NOT NULL / PK |
| `quality_status` | `VARCHAR(16)` | NOT NULL / PK; Default |
| `quality_flags` | `JSONB` | NOT NULL / PK; Default |
| `missing_fields` | `TEXT[]` | NOT NULL / PK; Default |
| `revision_no` | `INTEGER` | NOT NULL / PK; Default; CHECK |
| `first_seen_at` | `TIMESTAMPTZ` | NOT NULL / PK; Default |
| `last_seen_at` | `TIMESTAMPTZ` | NOT NULL / PK; Default |
| `updated_at` | `TIMESTAMPTZ` | NOT NULL / PK; Default |

**Số cột: 28**.

### `silver.wave_observation_revisions`

Các revision bất biến của quan trắc.

| Trường | Kiểu PostgreSQL | Ràng buộc cấp cột |
|---|---|---|
| `revision_id` | `BIGINT` | NOT NULL / PK; Generated; Default |
| `station_id` | `VARCHAR(12)` | NOT NULL / PK |
| `observed_at` | `TIMESTAMPTZ` | NOT NULL / PK |
| `revision_no` | `INTEGER` | NOT NULL / PK; CHECK |
| `source_kind` | `VARCHAR(24)` | NOT NULL / PK |
| `source_file_id` | `BIGINT` | NOT NULL / PK; FK |
| `raw_id` | `BIGINT` | FK |
| `measurement_sha256` | `VARCHAR(64)` | NOT NULL / PK |
| `change_type` | `VARCHAR(16)` | NOT NULL / PK |
| `snapshot` | `JSONB` | NOT NULL / PK |
| `recorded_at` | `TIMESTAMPTZ` | NOT NULL / PK; Default |

**Số cột: 11**.

### `silver.data_quality_issues`

Sự cố chất lượng dữ liệu.

| Trường | Kiểu PostgreSQL | Ràng buộc cấp cột |
|---|---|---|
| `issue_id` | `BIGINT` | NOT NULL / PK; Generated; Default |
| `station_id` | `VARCHAR(12)` | NOT NULL / PK; FK |
| `observed_at` | `TIMESTAMPTZ` | Nullable |
| `raw_id` | `BIGINT` | FK |
| `file_id` | `BIGINT` | FK |
| `field_code` | `VARCHAR(32)` | Nullable |
| `issue_type` | `VARCHAR(48)` | NOT NULL / PK |
| `severity` | `VARCHAR(12)` | NOT NULL / PK; Default |
| `original_value` | `TEXT` | Nullable |
| `explanation` | `TEXT` | Nullable |
| `metadata` | `JSONB` | NOT NULL / PK; Default |
| `detected_at` | `TIMESTAMPTZ` | NOT NULL / PK; Default |
| `resolved_at` | `TIMESTAMPTZ` | Nullable |
| `status` | `VARCHAR(12)` | NOT NULL / PK; Default |

**Số cột: 14**.

### `silver.observation_gaps`

Khoảng trống quan trắc theo trạm.

| Trường | Kiểu PostgreSQL | Ràng buộc cấp cột |
|---|---|---|
| `gap_id` | `BIGINT` | NOT NULL / PK; Generated; Default |
| `station_id` | `VARCHAR(12)` | NOT NULL / PK; FK |
| `gap_start_at` | `TIMESTAMPTZ` | NOT NULL / PK |
| `gap_end_at` | `TIMESTAMPTZ` | NOT NULL / PK |
| `assumed_interval_min` | `INTEGER` | CHECK |
| `estimated_missing_slots` | `INTEGER` | CHECK |
| `detected_at` | `TIMESTAMPTZ` | NOT NULL / PK; Default |
| `resolved_at` | `TIMESTAMPTZ` | Nullable |
| `notes` | `TEXT` | Nullable |

**Số cột: 9**.

### `silver.daily_wave_features`

Đặc trưng tổng hợp từng ngày theo Hawaii.

| Trường | Kiểu PostgreSQL | Ràng buộc cấp cột |
|---|---|---|
| `station_id` | `VARCHAR(12)` | NOT NULL / PK; FK |
| `date_hst` | `DATE` | NOT NULL / PK |
| `sample_count` | `INTEGER` | NOT NULL / PK; Default; CHECK |
| `valid_wave_count` | `INTEGER` | NOT NULL / PK; Default; CHECK |
| `expected_sample_count` | `INTEGER` | CHECK |
| `coverage_ratio` | `DOUBLE PRECISION` | CHECK |
| `mean_wave_height_m` | `DOUBLE PRECISION` | Nullable |
| `median_wave_height_m` | `DOUBLE PRECISION` | Nullable |
| `max_wave_height_m` | `DOUBLE PRECISION` | Nullable |
| `min_wave_height_m` | `DOUBLE PRECISION` | Nullable |
| `p95_wave_height_m` | `DOUBLE PRECISION` | Nullable |
| `stddev_wave_height_m` | `DOUBLE PRECISION` | Nullable |
| `mean_dominant_period_s` | `DOUBLE PRECISION` | Nullable |
| `circular_mean_direction_deg` | `DOUBLE PRECISION` | Nullable |
| `mean_sea_surface_temp_c` | `DOUBLE PRECISION` | Nullable |
| `max_wind_speed_ms` | `DOUBLE PRECISION` | Nullable |
| `good_quality_count` | `INTEGER` | NOT NULL / PK; Default; CHECK |
| `calculation_version` | `TEXT` | NOT NULL / PK; Default |
| `calculated_at` | `TIMESTAMPTZ` | NOT NULL / PK; Default |
| `mean_average_period_s` | `DOUBLE PRECISION` | Nullable |
| `max_dominant_period_s` | `DOUBLE PRECISION` | Nullable |
| `mean_wind_speed_ms` | `DOUBLE PRECISION` | Nullable |
| `mean_air_temperature_c` | `DOUBLE PRECISION` | Nullable |
| `mean_sea_level_pressure_hpa` | `DOUBLE PRECISION` | Nullable |
| `valid_dominant_period_count` | `INTEGER` | Nullable, CHECK |
| `source_data_max_updated_at` | `TIMESTAMPTZ` | Nullable |

**Số cột: 26**.

## GOLD
### `gold.processing_runs`

Lịch sử chạy job xử lý Gold.

| Trường | Kiểu PostgreSQL | Ràng buộc cấp cột |
|---|---|---|
| `processing_run_id` | `BIGINT` | NOT NULL / PK; Generated; Default |
| `job_type` | `VARCHAR(24)` | NOT NULL / PK |
| `status` | `VARCHAR(16)` | NOT NULL / PK; Default |
| `started_at` | `TIMESTAMPTZ` | NOT NULL / PK; Default |
| `finished_at` | `TIMESTAMPTZ` | Nullable |
| `input_period_start` | `DATE` | Nullable |
| `input_period_end` | `DATE` | Nullable |
| `input_record_count` | `INTEGER` | CHECK |
| `output_record_count` | `INTEGER` | CHECK |
| `algorithm_version` | `TEXT` | Nullable |
| `parameters` | `JSONB` | NOT NULL / PK; Default |
| `error_message` | `TEXT` | Nullable |

**Số cột: 12**.

### `gold.time_series_metrics`

Thống kê ngày/tuần/tháng của một biến.

| Trường | Kiểu PostgreSQL | Ràng buộc cấp cột |
|---|---|---|
| `station_id` | `VARCHAR(12)` | NOT NULL / PK; FK |
| `period_type` | `VARCHAR(8)` | NOT NULL / PK |
| `period_start_hst` | `DATE` | NOT NULL / PK |
| `metric_code` | `VARCHAR(32)` | NOT NULL / PK; FK |
| `sample_count` | `INTEGER` | NOT NULL / PK; Default; CHECK |
| `valid_sample_count` | `INTEGER` | NOT NULL / PK; Default; CHECK |
| `mean_value` | `DOUBLE PRECISION` | Nullable |
| `median_value` | `DOUBLE PRECISION` | Nullable |
| `min_value` | `DOUBLE PRECISION` | Nullable |
| `max_value` | `DOUBLE PRECISION` | Nullable |
| `stddev_value` | `DOUBLE PRECISION` | Nullable |
| `p95_value` | `DOUBLE PRECISION` | Nullable |
| `method` | `TEXT` | NOT NULL / PK; Default |
| `processing_run_id` | `BIGINT` | FK |
| `computed_at` | `TIMESTAMPTZ` | NOT NULL / PK; Default |

**Số cột: 15**.

### `gold.trend_seasonal_components`

Trend/seasonal/residual.

| Trường | Kiểu PostgreSQL | Ràng buộc cấp cột |
|---|---|---|
| `station_id` | `VARCHAR(12)` | NOT NULL / PK; FK |
| `metric_code` | `VARCHAR(32)` | NOT NULL / PK; FK |
| `period_type` | `VARCHAR(8)` | NOT NULL / PK |
| `period_start_hst` | `DATE` | NOT NULL / PK |
| `observed_value` | `DOUBLE PRECISION` | Nullable |
| `trend_value` | `DOUBLE PRECISION` | Nullable |
| `seasonal_value` | `DOUBLE PRECISION` | Nullable |
| `residual_value` | `DOUBLE PRECISION` | Nullable |
| `decomposition_method` | `VARCHAR(32)` | NOT NULL / PK |
| `seasonal_periods` | `INTEGER` | CHECK |
| `analysis_version` | `TEXT` | NOT NULL / PK; Default |
| `processing_run_id` | `BIGINT` | FK |
| `computed_at` | `TIMESTAMPTZ` | NOT NULL / PK; Default |

**Số cột: 13**.

### `gold.correlations`

Hệ số tương quan trên hai biến.

| Trường | Kiểu PostgreSQL | Ràng buộc cấp cột |
|---|---|---|
| `correlation_id` | `BIGINT` | NOT NULL / PK; Generated; Default |
| `station_id` | `VARCHAR(12)` | NOT NULL / PK; FK |
| `metric_x` | `VARCHAR(32)` | NOT NULL / PK; FK |
| `metric_y` | `VARCHAR(32)` | NOT NULL / PK; FK |
| `method` | `VARCHAR(12)` | NOT NULL / PK; Default |
| `date_start_hst` | `DATE` | NOT NULL / PK |
| `date_end_hst` | `DATE` | NOT NULL / PK |
| `paired_sample_count` | `INTEGER` | NOT NULL / PK; CHECK |
| `coefficient` | `DOUBLE PRECISION` | Nullable |
| `processing_run_id` | `BIGINT` | FK |
| `computed_at` | `TIMESTAMPTZ` | NOT NULL / PK; Default |

**Số cột: 11**.

### `gold.model_registry`

Mô hình đã lưu trên GCS và metadata phiên bản.

| Trường | Kiểu PostgreSQL | Ràng buộc cấp cột |
|---|---|---|
| `model_id` | `BIGINT` | NOT NULL / PK; Generated; Default |
| `model_name` | `TEXT` | NOT NULL / PK |
| `model_version` | `TEXT` | NOT NULL / PK |
| `task_type` | `VARCHAR(16)` | NOT NULL / PK |
| `algorithm` | `TEXT` | NOT NULL / PK |
| `artifact_gcs_uri` | `TEXT` | NOT NULL / PK |
| `scaler_gcs_uri` | `TEXT` | Nullable |
| `training_code_version` | `TEXT` | Nullable |
| `training_data_hash` | `TEXT` | Nullable |
| `feature_names` | `JSONB` | NOT NULL / PK; Default |
| `hyperparameters` | `JSONB` | NOT NULL / PK; Default |
| `evaluation_metrics` | `JSONB` | NOT NULL / PK; Default |
| `train_start_hst` | `DATE` | Nullable |
| `train_end_hst` | `DATE` | Nullable |
| `test_start_hst` | `DATE` | Nullable |
| `test_end_hst` | `DATE` | Nullable |
| `train_sample_count` | `INTEGER` | CHECK |
| `test_sample_count` | `INTEGER` | CHECK |
| `status` | `VARCHAR(12)` | NOT NULL / PK; Default |
| `trained_at` | `TIMESTAMPTZ` | Nullable |
| `created_at` | `TIMESTAMPTZ` | NOT NULL / PK; Default |
| `updated_at` | `TIMESTAMPTZ` | NOT NULL / PK; Default |
| `artifact_generation` | `TEXT` | Nullable, CHECK |
| `artifact_sha256` | `VARCHAR(64)` | Nullable, CHECK |
| `artifact_size_bytes` | `BIGINT` | Nullable, CHECK |
| `preprocessor_gcs_uri` | `TEXT` | Nullable |
| `preprocessor_generation` | `TEXT` | Nullable |
| `requirements_gcs_uri` | `TEXT` | Nullable |
| `model_card_gcs_uri` | `TEXT` | Nullable |
| `feature_schema_sha256` | `VARCHAR(64)` | Nullable, CHECK |
| `python_version` | `TEXT` | Nullable |
| `promoted_at` | `TIMESTAMPTZ` | Nullable |

**Số cột: 32**.

### `gold.cluster_centroids`

Tâm cụm và mô tả cluster.

| Trường | Kiểu PostgreSQL | Ràng buộc cấp cột |
|---|---|---|
| `model_id` | `BIGINT` | NOT NULL / PK; FK |
| `cluster_label` | `INTEGER` | NOT NULL / PK |
| `cluster_name` | `TEXT` | Nullable |
| `centroid_features` | `JSONB` | NOT NULL / PK |
| `description` | `TEXT` | Nullable |
| `sample_count` | `INTEGER` | CHECK |
| `created_at` | `TIMESTAMPTZ` | NOT NULL / PK; Default |

**Số cột: 7**.

### `gold.cluster_assignments`

Gán cụm cho một ngày Hawaii.

| Trường | Kiểu PostgreSQL | Ràng buộc cấp cột |
|---|---|---|
| `model_id` | `BIGINT` | NOT NULL / PK |
| `station_id` | `VARCHAR(12)` | NOT NULL / PK |
| `date_hst` | `DATE` | NOT NULL / PK |
| `cluster_label` | `INTEGER` | NOT NULL / PK |
| `distance_to_centroid` | `DOUBLE PRECISION` | CHECK |
| `assigned_at` | `TIMESTAMPTZ` | NOT NULL / PK; Default |
| `processing_run_id` | `BIGINT` | FK |

**Số cột: 7**.

### `gold.classification_schemes`

Bộ quy tắc nhãn phân loại sóng.

| Trường | Kiểu PostgreSQL | Ràng buộc cấp cột |
|---|---|---|
| `scheme_id` | `BIGINT` | NOT NULL / PK; Generated; Default |
| `scheme_name` | `TEXT` | NOT NULL / PK |
| `scheme_version` | `TEXT` | NOT NULL / PK |
| `target_metric` | `VARCHAR(32)` | NOT NULL / PK; FK |
| `label_rules` | `JSONB` | NOT NULL / PK |
| `rationale` | `TEXT` | NOT NULL / PK |
| `created_at` | `TIMESTAMPTZ` | NOT NULL / PK; Default |

**Số cột: 7**.

### `gold.predictions`

Dự đoán định kỳ và actual sau phát hành.

| Trường | Kiểu PostgreSQL | Ràng buộc cấp cột |
|---|---|---|
| `prediction_id` | `BIGINT` | NOT NULL / PK; Generated; Default |
| `model_id` | `BIGINT` | NOT NULL / PK; FK |
| `station_id` | `VARCHAR(12)` | NOT NULL / PK; FK |
| `issued_at` | `TIMESTAMPTZ` | NOT NULL / PK; Default |
| `target_date_hst` | `DATE` | NOT NULL / PK |
| `feature_cutoff_date_hst` | `DATE` | NOT NULL / PK |
| `target_metric` | `VARCHAR(32)` | NOT NULL / PK; FK |
| `horizon_days` | `SMALLINT` | NOT NULL / PK; Default; CHECK |
| `predicted_value` | `DOUBLE PRECISION` | NOT NULL / PK |
| `predicted_lower` | `DOUBLE PRECISION` | Nullable |
| `predicted_upper` | `DOUBLE PRECISION` | Nullable |
| `actual_value` | `DOUBLE PRECISION` | Nullable |
| `absolute_error` | `DOUBLE PRECISION` | CHECK |
| `evaluated_at` | `TIMESTAMPTZ` | Nullable |
| `processing_run_id` | `BIGINT` | FK |
| `features_snapshot` | `JSONB` | NOT NULL / PK; Default |
| `created_at` | `TIMESTAMPTZ` | NOT NULL / PK; Default |

**Số cột: 17**.

### `gold.classifications`

Nhãn dự đoán và đánh giá actual.

| Trường | Kiểu PostgreSQL | Ràng buộc cấp cột |
|---|---|---|
| `classification_id` | `BIGINT` | NOT NULL / PK; Generated; Default |
| `model_id` | `BIGINT` | NOT NULL / PK; FK |
| `scheme_id` | `BIGINT` | NOT NULL / PK; FK |
| `station_id` | `VARCHAR(12)` | NOT NULL / PK; FK |
| `issued_at` | `TIMESTAMPTZ` | NOT NULL / PK; Default |
| `target_date_hst` | `DATE` | NOT NULL / PK |
| `feature_cutoff_date_hst` | `DATE` | NOT NULL / PK |
| `predicted_label` | `TEXT` | NOT NULL / PK |
| `confidence` | `DOUBLE PRECISION` | CHECK |
| `class_probabilities` | `JSONB` | NOT NULL / PK; Default |
| `actual_label` | `TEXT` | Nullable |
| `is_correct` | `BOOLEAN` | Nullable |
| `evaluated_at` | `TIMESTAMPTZ` | Nullable |
| `processing_run_id` | `BIGINT` | FK |
| `features_snapshot` | `JSONB` | NOT NULL / PK; Default |
| `created_at` | `TIMESTAMPTZ` | NOT NULL / PK; Default |

**Số cột: 16**.

### `gold.dataset_splits`

Snapshot dataset, train/val/test và đối soát GCS.

| Trường | Kiểu PostgreSQL | Ràng buộc cấp cột |
|---|---|---|
| `dataset_split_id` | `BIGINT` | NOT NULL / PK; Generated; Default |
| `dataset_name` | `TEXT` | NOT NULL / PK |
| `dataset_version` | `TEXT` | NOT NULL / PK |
| `station_id` | `VARCHAR(12)` | NOT NULL / PK; FK |
| `task_type` | `VARCHAR(16)` | NOT NULL / PK |
| `target_metric` | `VARCHAR(32)` | FK |
| `target_horizon_days` | `SMALLINT` | CHECK |
| `observation_granularity` | `VARCHAR(12)` | NOT NULL / PK; Default |
| `feature_window_days` | `INTEGER` | CHECK |
| `feature_columns` | `JSONB` | NOT NULL / PK; Default |
| `label_definition` | `JSONB` | NOT NULL / PK; Default |
| `preprocessing_parameters` | `JSONB` | NOT NULL / PK; Default |
| `split_method` | `VARCHAR(32)` | NOT NULL / PK; Default |
| `train_start_hst` | `DATE` | NOT NULL / PK |
| `train_end_hst` | `DATE` | NOT NULL / PK |
| `validation_start_hst` | `DATE` | Nullable |
| `validation_end_hst` | `DATE` | Nullable |
| `test_start_hst` | `DATE` | Nullable |
| `test_end_hst` | `DATE` | Nullable |
| `train_sample_count` | `INTEGER` | CHECK |
| `validation_sample_count` | `INTEGER` | CHECK |
| `test_sample_count` | `INTEGER` | CHECK |
| `data_cutoff_at` | `TIMESTAMPTZ` | Nullable |
| `dataset_gcs_uri` | `TEXT` | Nullable |
| `dataset_gcs_generation` | `TEXT` | Nullable |
| `dataset_sha256` | `VARCHAR(64)` | Nullable |
| `code_commit_sha` | `TEXT` | Nullable |
| `created_at` | `TIMESTAMPTZ` | NOT NULL / PK; Default |
| `notes` | `TEXT` | Nullable |

**Số cột: 29**.

### `gold.training_runs`

Lịch sử chạy train model.

| Trường | Kiểu PostgreSQL | Ràng buộc cấp cột |
|---|---|---|
| `training_run_id` | `BIGINT` | NOT NULL / PK; Generated; Default |
| `dataset_split_id` | `BIGINT` | NOT NULL / PK; FK |
| `model_id` | `BIGINT` | FK |
| `processing_run_id` | `BIGINT` | FK |
| `run_name` | `TEXT` | Nullable |
| `task_type` | `VARCHAR(16)` | NOT NULL / PK |
| `algorithm` | `TEXT` | NOT NULL / PK |
| `status` | `VARCHAR(16)` | NOT NULL / PK; Default |
| `random_seed` | `INTEGER` | Nullable |
| `hyperparameters` | `JSONB` | NOT NULL / PK; Default |
| `software_versions` | `JSONB` | NOT NULL / PK; Default |
| `feature_pipeline_version` | `TEXT` | Nullable |
| `code_commit_sha` | `TEXT` | Nullable |
| `log_gcs_uri` | `TEXT` | Nullable |
| `host_identifier` | `TEXT` | Nullable |
| `started_at` | `TIMESTAMPTZ` | NOT NULL / PK; Default |
| `finished_at` | `TIMESTAMPTZ` | Nullable |
| `input_sample_count` | `INTEGER` | CHECK |
| `training_duration_seconds` | `DOUBLE PRECISION` | CHECK |
| `error_type` | `TEXT` | Nullable |
| `error_message` | `TEXT` | Nullable |
| `notes` | `TEXT` | Nullable |

**Số cột: 22**.

### `gold.model_evaluations`

Các metrics theo model/run/stage.

| Trường | Kiểu PostgreSQL | Ràng buộc cấp cột |
|---|---|---|
| `evaluation_id` | `BIGINT` | NOT NULL / PK; Generated; Default |
| `training_run_id` | `BIGINT` | NOT NULL / PK; FK |
| `model_id` | `BIGINT` | FK |
| `dataset_split_id` | `BIGINT` | NOT NULL / PK; FK |
| `evaluation_name` | `TEXT` | NOT NULL / PK; Default |
| `evaluation_stage` | `VARCHAR(16)` | NOT NULL / PK |
| `task_type` | `VARCHAR(16)` | NOT NULL / PK |
| `sample_count` | `INTEGER` | NOT NULL / PK; CHECK |
| `mae` | `DOUBLE PRECISION` | CHECK |
| `mse` | `DOUBLE PRECISION` | CHECK |
| `rmse` | `DOUBLE PRECISION` | CHECK |
| `r2_score` | `DOUBLE PRECISION` | Nullable |
| `accuracy` | `DOUBLE PRECISION` | CHECK |
| `precision_macro` | `DOUBLE PRECISION` | CHECK |
| `recall_macro` | `DOUBLE PRECISION` | CHECK |
| `f1_macro` | `DOUBLE PRECISION` | CHECK |
| `silhouette_score` | `DOUBLE PRECISION` | CHECK |
| `davies_bouldin_score` | `DOUBLE PRECISION` | CHECK |
| `calinski_harabasz_score` | `DOUBLE PRECISION` | CHECK |
| `confusion_matrix` | `JSONB` | Nullable |
| `additional_metrics` | `JSONB` | NOT NULL / PK; Default |
| `baseline_name` | `TEXT` | Nullable |
| `baseline_metrics` | `JSONB` | NOT NULL / PK; Default |
| `evaluation_parameters` | `JSONB` | NOT NULL / PK; Default |
| `details_gcs_uri` | `TEXT` | Nullable |
| `evaluated_at` | `TIMESTAMPTZ` | NOT NULL / PK; Default |
| `notes` | `TEXT` | Nullable |

**Số cột: 27**.

### `gold.evaluation_samples`

Các cặp actual/prediction/label trên tập kiểm thử.

| Trường | Kiểu PostgreSQL | Ràng buộc cấp cột |
|---|---|---|
| `evaluation_sample_id` | `BIGINT` | NOT NULL / PK; Generated; Default |
| `evaluation_id` | `BIGINT` | NOT NULL / PK; FK |
| `station_id` | `VARCHAR(12)` | NOT NULL / PK; FK |
| `target_date_hst` | `DATE` | NOT NULL / PK |
| `target_name` | `TEXT` | NOT NULL / PK |
| `horizon_days` | `SMALLINT` | NOT NULL / PK; Default; CHECK |
| `actual_value` | `DOUBLE PRECISION` | Nullable |
| `predicted_value` | `DOUBLE PRECISION` | Nullable |
| `residual` | `DOUBLE PRECISION` | NOT NULL / PK; Generated |
| `absolute_error` | `DOUBLE PRECISION` | NOT NULL / PK; Generated |
| `actual_label` | `TEXT` | Nullable |
| `predicted_label` | `TEXT` | Nullable |
| `is_correct` | `BOOLEAN` | NOT NULL / PK; Generated |
| `prediction_probabilities` | `JSONB` | NOT NULL / PK; Default |
| `feature_cutoff_date_hst` | `DATE` | Nullable |
| `feature_snapshot` | `JSONB` | NOT NULL / PK; Default |
| `created_at` | `TIMESTAMPTZ` | NOT NULL / PK; Default |

**Số cột: 17**.

