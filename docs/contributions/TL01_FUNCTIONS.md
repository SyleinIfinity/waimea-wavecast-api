# TL01 — COMPLETED FUNCTIONS | Database / Database API

> Chỉ liệt kê phần đã có bằng chứng kiểm thử. Fixture ghi vào PostgreSQL đều nằm trong transaction `ROLLBACK`; không phải quan trắc NOAA thật.

| ID | Function / Endpoint | File path | Input → Output / tác dụng | Bằng chứng |
|---|---|---|---|---|
| TL01-F01 | `Database.connection`, `Database.ready` | `services/database_api/app/db/session.py` | Env DB → pooled transaction / `SELECT 1` | `scripts.smoke_read`: readiness 200 trên PostgreSQL 17.11 |
| TL01-F02 | `register_source_file` | `services/database_api/app/repositories/bronze.py` | NOAA file metadata → `file_id`, dedup theo SHA | `scripts.rollback_integration`, `scripts.http_rollback`: PASS |
| TL01-F03 | `upsert_raw_batch` | `services/database_api/app/repositories/bronze.py` | Raw rows → inserted/duplicate | `scripts.rollback_integration`, `scripts.http_rollback`: PASS |
| TL01-F04 | `upsert_silver_batch` | `services/database_api/app/repositories/silver.py` | NOAA normalized rows → insert/duplicate/update/skip; trigger revision | `scripts.rollback_integration`: 2 revision; `scripts.http_rollback`: POST PASS |
| TL01-F05 | `upsert_daily_features` | `services/database_api/app/repositories/silver.py` | HST daily features → upsert | `scripts.rollback_integration`: PASS |
| TL01-F06 | `save_gold_results` | `services/database_api/app/repositories/gold.py` | Allowlisted Gold rows → upsert | `scripts.rollback_integration`, `scripts.http_rollback`: PASS; `pytest` chặn cột ngoài allowlist |
| TL01-F07 | `save_dataset_split`, `save_model_metadata`, `save_training_run`, `save_evaluation`, `save_evaluation_sample`, `save_cluster_centroid`, `save_classification_scheme` | `services/database_api/app/repositories/models.py` | ML metadata/evaluation → ID/record | `scripts.rollback_integration`: PASS, ROLLBACK |
| TL01-F08 | `GET /api/v1/stations/51201` | `services/database_api/app/api/public.py` | Station ID → NOAA station metadata | `scripts.smoke_read`: 200 `ok` |
| TL01-F09 | `GET /api/v1/observations/latest` | `services/database_api/app/api/public.py` | — → latest NOAA observation hoặc empty | `scripts.smoke_read`: 200 `empty`; `scripts.http_rollback`: fixture 200 `ok`; `pytest`: empty DTO |
| TL01-F10 | `GET /api/v1/observations` | `services/database_api/app/api/public.py` | UTC range/cursor/limit → Silver page | `scripts.smoke_read`: 200; `scripts.http_rollback`: data + cursor PASS |
| TL01-F11 | `GET /api/v1/metrics/summary` | `services/database_api/app/api/public.py` | Metric/HST period → Gold metrics page | `scripts.smoke_read`: 200; `scripts.http_rollback`: fixture value PASS |
| TL01-F12 | `GET /api/v1/analytics/trend-seasonal` | `services/database_api/app/api/public.py` | Metric/HST range → decomposition rows | `scripts.smoke_read`: 200 `empty` |
| TL01-F13 | `GET /api/v1/analytics/correlations` | `services/database_api/app/api/public.py` | Method/HST range → correlation rows | `scripts.smoke_read`: 200 `empty` |
| TL01-F14 | `GET /api/v1/analytics/histogram` | `services/database_api/app/api/public.py` | Metric/UTC range/bins → bounded bins từ Silver | `scripts.smoke_read`: 200 `empty`; `scripts.http_rollback`: 2 bins có dữ liệu PASS |
| TL01-F15 | `GET /api/v1/analytics/scatter` | `services/database_api/app/api/public.py` | X/Y/UTC range → bounded paired samples | `scripts.smoke_read`: 200 `empty` |
| TL01-F16 | `GET /api/v1/clusters`, `/clusters/profiles` | `services/database_api/app/api/public.py` | HST range/model → assignments/centroids | `scripts.smoke_read`: 200 `empty` |
| TL01-F17 | `GET /api/v1/predictions/latest`, `/predictions/history` | `services/database_api/app/api/public.py` | Target/range → forecast/history | `scripts.smoke_read`: 200 `empty` |
| TL01-F18 | `GET /api/v1/classifications/latest`, `/classifications/schemes` | `services/database_api/app/api/public.py` | — → labels/schemes | `scripts.smoke_read`: 200 `empty` |
| TL01-F19 | `GET /api/v1/models`, `/models/{id}/evaluations`, `/models/{id}/evaluation-samples` | `services/database_api/app/api/public.py` | Model filters/ID → metadata/evaluations | `scripts.smoke_read`: 200 `empty` |
| TL01-F20 | `GET /api/v1/data-quality/overview`, `/data-freshness` | `services/database_api/app/api/public.py` | HST range → QC/freshness | `scripts.smoke_read`: 200; freshness `source_status=unknown` khi chưa có NOAA |
| TL01-F21 | `POST /internal/v1/source-files`, `/raw-observations/bulk`, `/silver/observations/bulk-upsert`, `/gold/results/bulk` | `services/database_api/app/api/internal.py` | Token + idempotency + payload → persisted rows/counts | `scripts.http_rollback`: 4 POST PASS và ROLLBACK |
| TL01-F22 | `POST /internal/v1/ml/dataset-splits`, `/models`, `/ml/training-runs`, `/ml/evaluations`, `/models/{id}/promote` | `services/database_api/app/api/internal.py` | Dataset/model/test evaluation → model `ready` | `scripts.http_rollback`: toàn bộ chuỗi HTTP/DB PASS, ROLLBACK |
| TL01-F23 | Admin preflight/preview/delete/rebuild guard | `services/database_api/app/api/admin.py` | Admin token + staging scope → preview/delete hoặc 202 worker jobs | `pytest`: production block, scoped SQL + one-time preview, worker mock `retrain=false`, 503 dependency |

## Database đã có

- `01_init_full.sql` và `03_verify.sql` đã được chạy bằng DBeaver theo báo cáo trước; `database/migrations/` là bản sao SHA-256 tương ứng của SQL trong `D:\kdl\BE-Review\database\schema_reference_v2\`.
- Kết nối chỉ đọc xác nhận PostgreSQL 17.11, Bronze 6/Silver 5/Gold 14 bảng, 5 views và 2 trigger revision. Dữ liệu NOAA/Gold/model thực hiện vẫn trống trong smoke test.

## Chưa ghi nhận là nghiệm thu tích hợp

- Các internal endpoint metadata/QC/ML khác đã có code và OpenAPI nhưng chưa kiểm tra HTTP với worker TL04/TL05 thật.
- Admin delete mới được thử bằng fake DB; không chạy xóa trên PostgreSQL đang dùng. Rebuild đã thử với mock worker, chưa có job thật.
- Docker build/run, PR và e2e NOAA → GCS → Bronze → Silver → Gold → Frontend còn chờ tích hợp nhóm.

**Contract:** `D:\kdl\PhanCong\00_HOP_DONG_CHUNG.md` v1; OpenAPI thực tế ở `services/database_api/contracts/openapi.json`, fixture ở `services/database_api/contracts/examples.json`.
