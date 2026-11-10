# 00 — HỢP ĐỒNG CHUNG & QUY TẮC KHÔNG CHỜ NHAU

## A. Những điều KHÓA từ đầu (mọi thành viên đọc trước khi code)

| Mục | Giá trị/hợp đồng | Trách nhiệm |
|---|---|---|
| Dữ liệu nguồn | NOAA NDBC `51201`, feeds historical annual/monthly/realtime | TL04 |
| Database | PostgreSQL 17, DB `waimea_wavecast`, `bronze` 6 + `silver` 5 + `gold` 14 bảng | TL01 |
| GCS | bucket lấy từ `GCS_BUCKET`; URI `gs://...` lưu trong metadata DB | TL04/TL05 |
| Metric | NOAA `WVHT` → `silver.wave_observations.wave_height_m` (m); không nhầm với `DPD` | Tất cả |
| Time | `observed_at` ISO UTC; ngày analytics/target theo `Pacific/Honolulu`, `YYYY-MM-DD` | Tất cả |
| Regression target dự kiến | **Trung vị độ cao sóng WVHT của ngày HST T+1**; phải khóa lại trước train thật | TL05/FE03 |
| Endpoint public | Prefix `/api/v1`; các endpoint nghiệp vụ của Backend blueprint | TL01 |
| Internal worker | Prefix `/internal/v1`; không expose qua Nginx | TL01/TL04/TL05 |
| API admin (mới) | Prefix `/api/v1/admin`; chỉ tài khoản admin, không có chức năng public DELETE | TL01/FE03 |
| UI | React + TypeScript/Vite, ECharts, TanStack Query; `VITE_API_BASE_URL` | TL02/TL03 |

### B. Interface ấn định trước để giảm tranh chấp

**Frontend↔Backend:** giữ tối thiểu các GET `stations/51201`, `observations/latest`, `observations`, `metrics/summary`, `analytics/trend-seasonal`, `analytics/correlations`, `analytics/histogram`, `analytics/scatter`, `clusters`, `predictions/latest`, `predictions/history`, `classifications/latest`, `classifications/schemes`, `models`, `models/{id}/evaluations`, `data-quality/overview`. Những endpoint bổ sung (`clusters/profiles`, `models/{id}/evaluation-samples`, `data-freshness`, admin) là **API gap**, đánh dấu `proposed` cho đến khi TL01 thông qua và implement. Không tự gọi là API đã chạy thật.

**Worker↔Database API:** TL04/TL05 chỉ dùng **giao diện** `DatabaseGateway` gồm `register_source_file`, `upsert_raw_batch`, `upsert_silver_batch`, `upsert_daily_features`, `get_observations`, `get_daily_features`, `save_gold_results`, `save_model_metadata`, `get_model_metadata`, `save_job_status`, `get_job_status`; trao đổi JSON/Pydantic theo DTO. TL01 code HTTP implementation. Mock dùng in-memory/fixture cùng methods, **không đợi HTTP server**.

**Schema v2.0:** file SQL gốc là authoritative. Nếu thiếu trường (như `target_aggregation` chưa có cột riêng), đưa vào JSON metadata/versioned manifest hoặc đề xuất migration, **không tự đổi SQL mỗi người một kiểu**. `gold.model_registry.status` có giá trị `draft/ready/retired` theo schema, không tự query `status='active'`; model được chọn qua `model_id` đã duyệt/chính sách triển khai. Kiểm tra SQL thật trước khi triển khai.

### C. Ownership file — tránh conflict Git

| Chủ sở hữu | Được code tại | Không sửa trực tiếp |
|---|---|---|
| TL01 | `database/**`, `apps/database_api/**`, `deploy/integration/**` | `apps/ingestion/**`, `training/**`, `src/features/**` |
| TL02 | `src/app/**`, `src/layout/**`, `src/components/ui/**`, `src/features/{overview,observations,time-series,trend,data-quality}/**` | feature của TL03 |
| TL03 | `src/features/{correlations,clustering,forecast,models,pipeline-monitor,demo-control}/**` | shell của TL02 |
| TL04 | `apps/{ingestion,preprocessing}/**`, `packages/noaa/**`, `packages/gcs/raw/**` | Database API, ML, scheduler |
| TL05 | `apps/{analysis,clustering,prediction,classification,scheduler}/**`, `training/**`, `packages/ml/**`, `packages/gcs/models/**` | Ingestion, SQL/migrations |

**Shared config quan trọng** (`package.json`, `src/app/router.tsx`, root `compose.yaml`, `pyproject.toml`): TL02 là owner đối với FE, TL01 là owner đối với BE. Các thành viên khác ghi hướng dẫn bổ sung trong tài liệu/PR, không tự sửa file chung; khi ghép owner áp dụng. Mỗi service Backend tự có Dockerfile, TL01 ghép Docker Compose/Nginx cuối.

### D. Quy tắc fake/stub để bắt đầu ngay

- TL01: PostgreSQL local hoặc temp DB dùng SQL v2.0; fake NOAA/GCS payload để test repository/API.
- TL02 và TL03: file `*.mock.ts` riêng từng feature, đáp ứng DTO; phát triển hoàn chỉnh loading/empty/error/partial. Không đưa mock vào build production.
- TL04: `FakeDatabaseGateway`, NOAA sample fixture, GCS fake/in-memory adapter; test chuyển đổi dữ liệu không cần Cloud SQL thật.
- TL05: fixture daily features 30–100 ngày, DB gateway fake và GCS artifact adapter; train model test trên synthetic data **chỉ cho unit/integration test**, train thật sau khi có Silver thật.

### E. Tiêu chí API/UX thống nhất

- HTTP `GET` đọc, `POST` admin job; `202 Accepted` = đã nhận việc, **chưa hoàn thành**; `409` đang có job; `401/403` quyền; `422` validation; `503` dependency.
- `null` không phải số 0; `predicted_value_m` khác `actual_value_m`; HST target date khác UTC observed timestamp.
- Không hardcode threshold khoa học/quality như “NOAA bắt buộc 80%”; thresholds chỉ là cấu hình đề xuất và cần văn bản quyết định.
- Trạng thái UI: `loading`, `ready`, `empty`, `partial`, `stale`, `error`, `processing`, `success`. Một mock POST không có nghĩa job thật đã chạy.
- Kiểm thử đơn vị độc lập và nghiệm thu tích hợp là hai lớp khác nhau: mỗi người tự test bằng mock, sau merge mới kiểm thử API thực và Docker.

### F. Quy ước báo cáo và PR

Tên branch trong README. Commit theo `TL01: ...` / `TL02: ...`. Mỗi người thêm **đúng 2 tệp** `docs/contributions/TLxx_STATUS.md` và `docs/contributions/TLxx_FUNCTIONS.md`. Không ghi “DONE” khi chưa chạy kiểm thử. Cùng mẫu minh chứng `test_command`, `test_result`, `file_path`, `evidence`, `blocked_by`. Trước merge phải có PR không đổi code của đồng đội, không để lộ khóa DB/GCS và có checklist hoàn thành.
