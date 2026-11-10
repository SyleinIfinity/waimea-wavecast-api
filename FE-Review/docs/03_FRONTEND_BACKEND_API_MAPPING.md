# 03 — Ma trận đồng bộ Frontend ↔ Backend API ↔ Cloud SQL / GCS

**Trạng thái quan trọng:** Các endpoint bên dưới xuất phát từ **Backend Blueprint đã đề xuất trong hội thoại**, hiện chưa được xác minh là có mã nguồn hoạt động. `EXISTING CONTRACT` = đã được nêu trong tài liệu thiết kế trước; **không đồng nghĩa endpoint đã code**. `NEW` = Frontend cần Backend bổ sung contract/code. Tất cả dữ liệu frontend phải qua REST API, không dùng trực tiếp PostgreSQL, Cloud SQL connection string hay GCS credentials.

## 1. Map màn hình / widget / endpoint / database

| Trang | Widget | Endpoint | Tables/Views | Cần Backend làm thêm? |
|---|---|---|---|---|
| Overview | station hero | `GET /api/v1/stations/51201` | `bronze.stations` | hiện contract có |
| Overview | latest NOAA cards | `GET /api/v1/observations/latest` | `gold.v_latest_observation` (Silver) | bổ sung freshness metadata |
| Overview | recent WVHT chart | `GET /api/v1/observations` | `silver.wave_observations` | có contract, streaming pagination |
| Overview | latest prediction | `GET /api/v1/predictions/latest` | `gold.v_latest_predictions` | map target metric + model version |
| Overview | latest classification | `GET /api/v1/classifications/latest` | `gold.v_latest_classifications` | kèm scheme metadata |
| Overview | QC banner | `GET /api/v1/data-quality/overview` | Silver QC+gaps/daily | có contract; chốt fields |
| Observations | raw/silver time series table | `GET /api/v1/observations` | `silver.wave_observations` | có contract; cursor, date units |
| Observations | revisions drawer | `GET /api/v1/observations/{observed_at}/revisions` | `silver.wave_observation_revisions` | **NEW** |
| Time Series | daily/weekly/monthly | `GET /api/v1/metrics/summary` | `gold.time_series_metrics` | có contract; weekly boundary HST |
| Time Series | histogram | `GET /api/v1/analytics/histogram` | Silver query/custom result | contract đã có, không có bảng histogram |
| Time Series | scatter | `GET /api/v1/analytics/scatter` | Silver paired samples | contract đã có, bounds/limit |
| Trend/Seasonal | decomposition | `GET /api/v1/analytics/trend-seasonal` | `gold.trend_seasonal_components` | có contract; method/seasonal_period |
| Correlation | heatmap | `GET /api/v1/analytics/correlations` | `gold.correlations` | có contract; n_pairs, missing reason |
| Correlation | detail scatter | `GET /api/v1/analytics/scatter` | Silver | có contract |
| Clustering | assignments/calendar | `GET /api/v1/clusters` | `gold.cluster_assignments` | có contract; count/labels |
| Clustering | centroid/radar | `GET /api/v1/clusters/profiles` | `gold.cluster_centroids`+model | **NEW** nếu `/clusters` không trả profiles |
| Forecast | latest WVHT | `GET /api/v1/predictions/latest` | `gold.predictions`/view | có contract |
| Forecast | actual vs predicted | `GET /api/v1/predictions/history` | `gold.predictions` | có contract |
| Forecast | latest class | `GET /api/v1/classifications/latest` | `gold.classifications`/view | có contract |
| Forecast | class thresholds | `GET /api/v1/classifications/schemes` | `gold.classification_schemes` | có contract |
| Forecast | label history/online accuracy | `GET /api/v1/classifications/history` | `gold.classifications` | **NEW** nếu cần chart |
| Model Evaluation | model version list | `GET /api/v1/models` | `gold.model_registry` | có contract; không lộ signed GCS URI |
| Model Evaluation | metrics/test vs baseline | `GET /api/v1/models/{id}/evaluations` | `gold.model_evaluations`+training | có contract |
| Model Evaluation | held-out actual vs predict | `GET /api/v1/models/{id}/evaluation-samples` | `gold.evaluation_samples` | **NEW** paginated |
| Model Evaluation | feature list, train dates | `GET /api/v1/models/{id}/details` | `gold.dataset_splits`,`training_runs`,`model_registry` | **NEW** nếu list thiếu |
| Data Quality | high-level overview | `GET /api/v1/data-quality/overview` | Silver issues/gaps/daily | có contract |
| Data Quality | coverage calendar | `GET /api/v1/data-quality/coverage` | Silver obs/features | **NEW** |
| Data Quality | issues/gaps table | `GET /api/v1/data-quality/issues`, `/gaps` | Silver issue/gap | **NEW** |
| Data Quality | source_file details | `GET /api/v1/source-files/{id}` | `bronze.source_files` | **NEW** optional, no secrets |
| Pipeline Monitor | service health/dependencies | `GET /api/v1/admin/system/status` | service health + gold processing log | **NEW**, admin auth |
| Pipeline Monitor | ingestion/job history | `GET /api/v1/admin/ingestion-runs`, `/jobs` | `bronze.ingestion_runs`, `gold.processing_runs` | **NEW**, admin auth |
| Pipeline Monitor | poll now / retry job | `POST /api/v1/admin/ingestion/poll`, `/jobs/{id}/retry` | backend orchestration | **NEW**, admin auth |
| Demo Control | preflight/checks | `GET /api/v1/admin/demo/preflight` | source & model counts/service state | **NEW**, admin auth |
| Demo Control | preview scoped DELETE | `POST /api/v1/admin/demo/patterns/preview-delete` | selected Gold patterns | **NEW**, admin auth |
| Demo Control | delete selected patterns | `POST /api/v1/admin/demo/patterns/delete` | selected Gold patterns ONLY | **NEW**, admin+guardrails |
| Demo Control | regenerate pattern | `POST /api/v1/admin/demo/patterns/rebuild` | worker jobs | **NEW**, admin auth |
| Demo Control | progress | `GET /api/v1/admin/jobs/{id}` | processing_runs/job state | **NEW**, admin auth |

## 2. API response contracts cần chuẩn hóa

### Observation DTO (Silver)

```ts
type Observation = {
  station_id: '51201';
  observed_at: string;              // ISO UTC, e.g. 2026-11-06T10:26:00Z
  wave_height_m: number | null;     // NOAA WVHT
  dominant_period_s: number | null;
  average_period_s: number | null;
  mean_wave_direction_deg: number | null;
  sea_surface_temp_c: number | null;
  wind_speed_ms?: number | null;
  quality_status: 'unverified'|'valid'|'partial'|'suspect'|'rejected';
  source_kind: 'realtime'|'historical_monthly'|'historical_annual';
  revision_no?: number;
};
```

### Metric point DTO (Gold)

```ts
type MetricPoint = {
  station_id: '51201';
  period_type: 'day'|'week'|'month';
  period_start_hst: string;         // YYYY-MM-DD; Monday for week, 1st for month
  metric_code: 'WVHT'|'DPD'|'APD'|'MWD'|'WTMP'|string;
  mean_value: number|null;
  median_value: number|null;
  min_value: number|null;
  max_value: number|null;
  p95_value: number|null;
  sample_count: number;
  valid_sample_count: number;
  computed_at: string;             // UTC
  calculation_version?: string;
};
```

### Latest observation freshness DTO — **cần đề nghị Backend bổ sung**

```ts
type DataFreshness = {
  station_id: '51201';
  last_observed_at: string|null;    // NOAA measurement timestamp
  last_fetched_at: string|null;     // our ingestion request succeeded
  last_silver_updated_at: string|null;
  last_gold_processed_at: string|null;
  ingestion_poll_interval_min: number;
  source_age_minutes: number|null; // backend-computed or FE computed from UTC now
  is_stale: boolean;               // server policy value, not only UI judgement
  source_status: 'ok'|'delayed'|'unavailable'|'unknown';
};
```

### Forecast DTO — phân biệt observed vs predicted

```ts
type WaveForecast = {
  prediction_id: number;
  station_id: '51201';
  target_metric: 'WVHT';
  target_aggregation: 'daily_median';
  target_date_hst: string;
  feature_cutoff_date_hst: string;
  issued_at: string;
  predicted_value_m: number;
  lower_m?: number|null;           // only if validated uncertainty method
  upper_m?: number|null;
  actual_value_m: number|null;
  absolute_error_m: number|null;
  model: {model_id:number; name:string; version:string};
  data_quality_status: 'ready'|'partial'|'insufficient_data';
};
```

### Job DTO — cần server hỗ trợ ngoài gold.processing_runs tối thiểu

```ts
type Job = {
  job_id: string;
  job_type: 'ingestion'|'preprocessing'|'analysis'|'clustering'|'prediction'|'classification';
  status: 'queued'|'running'|'success'|'partial'|'failed'|'cancelled';
  started_at: string|null;
  finished_at: string|null;
  progress_completed?: number;
  progress_total?: number;
  inserted_rows?: number;
  revised_rows?: number;
  deleted_rows?: number;
  error?: {code:string;message:string}|null;
  affected_patterns?: string[];
};
```

**Backend delta:** `gold.processing_runs` hiện có `running/success/partial/failed/skipped` và không có `queued/cancelled` hoặc progress counters. Có thể (a) giữ enum hiện tại và map frontend, (b) thêm queue/job table hoặc migration. **Đừng cho UI nhận `queued` khi DB không hỗ trợ**, cũng không giả progress % khi backend chưa đo được.

## 3. Thiếu sót/khác biệt cần chốt giữa SQL v2 và Blueprint

| Mục | Hiện trạng | Quyết định trước khi code |
|---|---|---|
| Model status | SQL v2 `gold.model_registry.status` giới hạn **`draft`, `ready`, `retired`** | Blueprint từng nhắc `status='active'` không khớp. Chọn model `ready` qua cấu hình `model_id` đã duyệt hoặc tạo migration `model_deployments/current_model`; không query `active` khi chưa đổi SQL. |
| Frontend freshness | `gold.v_latest_observation` có timestamp đo nhưng không bảo đảm `last_fetch` | Bổ sung `GET /data-freshness` hoặc summary DTO. |
| Job statuses | `processing_runs` không có hàng đợi/percent | Code job store + progress hoặc UI dùng indeterminate progress. |
| Evaluation samples | SQL có `gold.evaluation_samples`, public API chưa có | GET phân trang để scatter actual-vs-predicted. |
| Centroid profile | SQL có bảng centroid nhưng API draft chưa có | `/clusters/profiles` hoặc gộp vào `/clusters`. |
| Quality calendars | SQL có issues/gaps, API draft chỉ overview | Coverage/gaps/issues endpoints cần code nếu UI yêu cầu detail. |
| Admin operations | Worker `/internal/v1` có nhưng Nginx không expose | Backend cần **admin facade** `/api/v1/admin/*` với xác thực; không mở `/internal` ra ngoài. |
| Gold demo delete | Slide nói **xóa bằng tay dữ liệu pattern** | Không bắt buộc nút xóa UI; nếu có, tạo guarded scoped action và audit. |
| Classification history | SQL có bảng classifications | API history nếu muốn accuracy over time. |
| GCS artifact URL | Có metadata `artifact_gcs_uri` | **Không trả GCS signed/private URL/model binaries cho public browser**. |

## 4. Các hành động — phân biệt rõ bốn loại “refresh”

| CTA hiển thị | Method | Quyền | Kết quả thực |
|---|---|---|---|
| `Làm mới giao diện` | Refetch GET | Viewer | chỉ cập nhật cache FE; không gọi NOAA |
| `Thu thập NOAA ngay` | POST admin ingestion/poll → 202 | Admin | tải NOAA, có thể không có record mới |
| `Xử lý lại dữ liệu` | POST admin preprocessing/rebuild → 202 | Admin | cập nhật Silver/Gold theo phạm vi, giữ raw |
| `Tái tạo pattern` | POST admin demo/rebuild → 202 | Admin | phân tích/inference từ Silver + GCS model, không training |
| `Huấn luyện lại` | job one-off riêng, không ở Demo page | Model admin | tạo phiên bản model mới, có khả năng tốn CPU |

## 5. Backend error → UI presentation

| Response | Giao diện |
|---|---|
| `200` với data | render chart/table, annotate timestamp/method |
| `200` với `items=[]` | empty state: thật sự không có dữ liệu trong filter |
| `202` | processing state + job_id, poll đến terminal status |
| `404` forecast chưa có | no model/no forecast available, không hiện 0 m |
| `409` job trùng | hiển thị job đang chạy, cho link theo dõi, không retry vô hạn |
| `422` | thông báo bộ lọc sai và highlight input |
| `429` | throttled, giữ data cũ kèm `stale`, cho retry sau |
| `500/503` | service unavailable, cho retry, không tuyên bố NOAA down nếu lỗi là DB/API |
| Model artifact invalid | model-unavailable, không tạo prediction giả |

## 6. Cache/filter contract

- Query key gợi ý: `['observations', stationId, startISO, endISO, metrics]`; `['metrics', metric, aggregation, startHST, endHST]`; `['job', id]`.
- Global filter lưu trong URL query params để khi F5/share không mất phạm vi; riêng admin destructive draft không lưu vào localStorage/query string.
- Chỉ auto-refetch GET status/near-live data theo tần suất hợp lý (ví dụ 60s); historical Gold stats cache lâu hơn (ví dụ 5–15 phút) và invalidation sau success job. Không dùng FE interval 30 phút để kích hoạt NOAA ingestion.
- Pagination keyset cho 2005→nay, không tải tất cả 300k+ rows trong một request; chart server aggregation/decimation, detail table lazy load.
