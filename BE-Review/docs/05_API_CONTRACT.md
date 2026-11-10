# 05 — REST API Contract v1 (đề xuất chưa lập trình)

**Public base:** `https://apiwaimea.liems.io.vn/api/v1`; JSON UTF-8; FastAPI OpenAPI. Frontend `https://waimea.liems.io.vn` là allowed origin duy nhất trong production. **Tất cả endpoints dưới đây là thiết kế dự kiến; scaffold hiện mới có `/health/live` và `/health/ready` (readiness chưa triển khai thật).**

## 1. Quy tắc dùng chung

- `observed_at`, `issued_at`, `created_at` là RFC3339 UTC có `Z`; `date_hst`/`target_date_hst` là ISO date với timezone `Pacific/Honolulu` được ghi chú tại field.
- Tất cả số đo kèm đơn vị trong tên trường: `wave_height_m`, `dominant_period_s`, v.v.; giá trị thiếu là `null` (không phải `0`).
- Query pagination theo cursor (keyset `observed_at,station_id`), `limit` mặc định 100, max 1000, trả `next_cursor`, `has_more`.
- `metric` là mã NOAA (`WVHT`, `DPD`, ...); mapping sang field Silver do API quản lý.
- Lỗi chuẩn: `{ "error": {"code": "INVALID_PARAMETER", "message": "...", "request_id":"...", "details": {}}}`; không phát password/traceback.
- Versioning theo `/api/v1`; **idempotency_key** qua header `Idempotency-Key` cho job/bulk writes; `X-Request-ID` log.
- `200` thành công đồng bộ; `202` job được nhận (không có nghĩa hoàn thành); `400` cú pháp; `401/403` quyền; `404` chưa có; `409` job đang chạy/conflict; `422` validation; `429` rate limit; `503` dependency unavailable.
- Giới hạn bộ lọc thời gian để tránh quét toàn bộ NOAA 2005→nay trong một HTTP request; trả 422 nếu window quá rộng (chính sách đặt config).
- Cache-Control ngắn cho historical stats, không cache `latest` quá lâu; ETag nếu có.

## 2. Public: chỉ expose qua Nginx

| Method | Endpoint | Query/body | Bảng/nguồn | Nội dung |
|---|---|---|---|---|
| GET | `/stations/51201` | — | `bronze.stations` | trạm, tọa độ, timezone, update_at |
| GET | `/observations` | `station_id=51201&start=UTC&end=UTC&limit&cursor` | `silver.wave_observations` | sensor series có QC, source kind |
| GET | `/observations/latest` | `station_id=51201` | `gold.v_latest_observation` | quan trắc NOAA mới nhất, `staleness_min` |
| GET | `/metrics/summary` | `metric=WVHT&group_by=day\|week\|month&start_hst&end_hst` | `gold.time_series_metrics` | count/mean/median/min/max/p95 |
| GET | `/analytics/trend-seasonal` | `metric=WVHT&period=month&start_hst&end_hst` | `gold.trend_seasonal_components` | observed/trend/seasonal/residual |
| GET | `/analytics/correlations` | `method=pearson&start_hst&end_hst` | `gold.correlations` | matrix/cells + paired_count |
| GET | `/analytics/histogram` | `metric=WVHT&bins=20&...` | read Silver or precompute | bin edges/counts; **không có bảng histogram riêng ở SQL v2** |
| GET | `/analytics/scatter` | `x=WVHT&y=DPD&...` | read Silver | paired valid samples, bounded points |
| GET | `/clusters` | `start_hst&end_hst&model_version` | `gold.cluster_assignments`, centroids | label per day + cluster profile |
| GET | `/predictions/latest` | `target_metric=WVHT` | `gold.v_latest_predictions` | T+1, issued_at, model, uncertainty if valid |
| GET | `/predictions/history` | `start_hst&end_hst` | `gold.predictions` | predictions + actual/error (if known) |
| GET | `/classifications/latest` | — | `gold.v_latest_classifications` | label, scheme, model, no safety claim |
| GET | `/classifications/schemes` | — | `gold.classification_schemes` | threshold/rules + rationale |
| GET | `/models` | `task_type&status` | `gold.model_registry` | versions + metadata (not GCS signed URL) |
| GET | `/models/{model_id}/evaluations` | `stage=test` | `gold.v_model_evaluation_summary` | metrics samples, training cutoff |
| GET | `/data-quality/overview` | `start_hst&end_hst` | Silver QC/gaps/daily | missing coverage, unresolved issues |
| GET | `/jobs/{processing_run_id}` | — | `gold.processing_runs` | progress/status (auth if internal data) |

**HTTP Response example (MINH HỌA CẤU TRÚC, không phải dữ liệu quan trắc thực)**:

```json
{
  "station_id": "51201",
  "target_metric": "WVHT",
  "target_date_hst": "2026-11-08",
  "feature_cutoff_date_hst": "2026-11-07",
  "predicted_value_m": 2.1,
  "issued_at": "2026-11-08T12:00:00Z",
  "model": {"model_id": 3, "version": "illustrative-v1"},
  "quality_status": "complete",
  "actual_value_m": null
}
```

## 3. Nội bộ: KHÔNG expose qua Nginx

**Chuẩn:** `http://database-api:8000/internal/v1/...` qua private Compose network; require internal auth; không chỉ tin private network. Worker trigger endpoints của từng service chỉ available internal.

| Method | Service/Endpoint | Input | Output / side effects |
|---|---|---|---|
| POST | ingestion `/internal/v1/ingestion/poll` | source, station, idempotency | `202 job_id`; asynchronously fetch NOAA |
| POST | ingestion `/internal/v1/ingestion/backfill` | start_year, end_year, force=FALSE | 202, admin token, block repeat |
| POST | DB API `/internal/v1/source-files` | sha, URI, generation, source period, run_id | source_file_id dedup |
| POST | DB API `/internal/v1/raw-observations/bulk` | `{file_id, rows:[]}` | inserted/duplicate/failed counts |
| POST | DB API `/internal/v1/silver/observations/bulk-upsert` | normalized rows+source priority | insert/update/revision counts transaction |
| GET | DB API `/internal/v1/silver/observations` | bounded range/cursor | normalized measurements |
| PUT | DB API `/internal/v1/silver/daily-features` | station,date,features,QCs | upsert daily features |
| POST | preprocess `/internal/v1/preprocessing/rebuild` | affected date range | `202 job_id` |
| POST | analysis `/internal/v1/analysis/rebuild` | metric,period/window,version | `202 job_id` |
| POST | clustering `/internal/v1/clustering/assign` | date,model_id | assignments |
| POST | prediction `/internal/v1/prediction/run` | cutoff date, active model | prediction ID / 202 |
| POST | classification `/internal/v1/classification/run` | cutoff date, scheme,model | classification ID / 202 |
| POST | DB API `/internal/v1/gold/results/bulk` | named table, rows, replace scope/version | transactional save |
| POST | DB API `/internal/v1/ml/training-runs` | dataset/model info, config, outcome | training_run_id |
| POST | DB API `/internal/v1/ml/evaluations` | metrics+sample refs | evaluation_id |
| PATCH | DB API `/internal/v1/jobs/{id}` | status/counts/error | state transition |
| POST | DB API `/internal/v1/models/promote` | candidate model version | atomic approval/active switch |
| POST | DB API `/internal/v1/demo/rebuild-patterns` | scoped modules+dates | authenticated 202, never train |

**Mọi POST nhận data phải có:** pydantic body schema, auth, max batch size, idempotency, transaction or saga, error logging, test 401/422/409/503. **Cấm xây một generic SQL runner API** để worker gửi arbitrary SQL.

## 4. Contract dữ liệu từ Frontend

```ts
// TypeScript contract đề xuất — cần generate từ OpenAPI khi code thực tế.
type NoaaObservation = {
  station_id: '51201';
  observed_at: string;         // UTC RFC3339
  wave_height_m: number | null;
  dominant_period_s: number | null;
  mean_wave_direction_deg: number | null;
  quality_status: string;
  source_kind: string;
};

type DailyPrediction = {
  station_id: '51201';
  target_date_hst: string;     // Local Hawaii YYYY-MM-DD
  feature_cutoff_date_hst: string;
  predicted_value_m: number;
  issued_at: string;
  model: {model_id: number; version: string};
  actual_value_m: number | null;
};
```

Frontend phải hỗ trợ: `loading`, `empty`, `insufficient_data`, `stale`, `error`, `ok`, chart range selection, timezone caption, model version/issued_at; không tự suy đoán `null` là 0.

## 5. Security policy

- Public GET read-only nếu nhóm chọn công khai Dashboard; không public các endpoint gây chạy training, xóa Gold hay backfill tốn tài nguyên.
- Internal token không lộ ra browser hoặc source frontend; dùng secret ở runtime; authorization theo scope (read, ingest, model-admin, demo-admin) khi triển khai.
- CORS chỉ `https://waimea.liems.io.vn` và localhost theo env DEV; không dùng `*` nếu gửi credentials.
- Docs `/docs` chỉ enabled trong dev/admin có kiểm soát; có thể trình diễn qua SSH tunnel/VPN thay vì public.
- Export signed GCS URLs chỉ nếu nghiệp vụ thực sự cần; mặc định không gửi raw/model object URLs ra client.
