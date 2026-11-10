# TL01 — DATABASE & DATABASE API

**Người phụ trách:** Thành viên 1 — Database. **Repo:** `waimea-wavecast-backend`. **Nhánh:** `feature/tl01-db-api`.

**Đọc:** `Waimea_WaveCast_DB_GCS_v2.0/{README_HUONG_DAN.md,DATA_DICTIONARY.md,01_init_full.sql,02_upgrade_from_v1.sql,03_verify.sql}`; Backend Blueprint `02_IMPLEMENTATION_BACKLOG.md` phần Database API, `05_API_CONTRACT.md`, `06_DATABASE_GCS_MAPPING.md`.

**Độc lập với người khác:** tự tạo PostgreSQL 17 local hoặc Cloud SQL test, seed `51201`, mock `source_files`, `wave_observations`, daily features, model/evaluation; worker thật chưa tồn tại vẫn test API bằng `pytest+httpx` và sample payload.

## TL01-01 — Khóa schema và lớp truy cập dữ liệu

- [ ] Đưa **SQL v2.0 (25 bảng, 3 schema)** vào `database/migrations/`; viết `database/README.md` hướng dẫn cài mới/nâng cấp và lệnh verify, **không DROP dữ liệu**. Không chạy cả init và upgrade khi không cần.
- [ ] Code `apps/database_api/app/db/{connection,session}.py`, `repositories/{bronze,silver,gold,models}.py`; quản lý transaction, connection pool, unique/upsert, FK; phép đo thời gian UTC/HST.
- [ ] Code `apps/database_api/app/schemas/{common,observations,analytics,ml}.py`: Pydantic DTO có type/unit/null, page/cursor. Khóa bản DTO bằng contract fixture để TL02–05 mock cùng kiểu.
- [ ] `tests/integration/test_migrations.py`: 25 tables + 5 views + 14 NOAA definitions; `tests/integration/test_upsert.py`: duplicate, revisions, canonical priority.

## TL01-02 — Database API + bảo vệ điều khiển

- [ ] Code `apps/database_api/app/api/public.py` các GET cần cho dashboard: station, observations, metrics, trend, correlations, hist/scatter, clustering, forecast/classification, models/evaluations, quality. Dữ liệu chưa có phải trả empty/null/status đúng, không bịa.
- [ ] Code `apps/database_api/app/api/internal.py` nhận batch Bronze/Silver, daily features, Gold results, model registry/training metrics và tracking jobs theo interface trong `00_HOP_DONG_CHUNG.md`. Hỗ trợ idempotency key, auth nội bộ, giới hạn batch.
- [ ] Code `apps/database_api/app/api/admin.py` **facade**: status/join workers, poll/rebuild jobs, demo **preflight + preview + guarded delete scoped Gold patterns**; quyền admin kiểm tra ở Backend, môi trường staging allowlist; không xóa dữ liệu model/GCS/Bronze/Silver.
- [ ] Code `apps/database_api/Dockerfile` và health/readiness kiểm tra DB thật. Hướng dẫn Compose/Nginx hợp nhất (owner shared deploy) nhưng **không bắt người khác chờ deploy**.

## TL01-03 — Độc lập kiểm thử và bàn giao

- [ ] Test SQL injection, empty, pagination, 401/403, 409, 422, 503; kiểm tra API response contract với mock client; đối chiếu `status` model `draft/ready/retired`.
- [ ] Kiểm tra trên PostgreSQL 17 riêng; gửi PR kèm migration có thể lặp lại an toàn.
- [ ] Tạo `docs/contributions/TL01_STATUS.md` và `docs/contributions/TL01_FUNCTIONS.md` theo mẫu; ghi endpoint nào **đã chạy thật**, endpoint nào mới stub.

**Bàn giao:** migrations + repository, FastAPI Database API, DTO, tests, Dockerfile của service, OpenAPI xuất từ FastAPI, hai báo cáo.

**Nghiệm thu TL01:** DDL verify pass; raw/silver upsert không trùng và giữ revision; 3 endpoint đại diện GET NOAA/GET Gold/POST batch hoạt động với fixture; admin destructive action bị chặn ở production; test chạy không cần TL04/TL05.

**Không sở hữu:** NOAA HTTP client/parser/GCS upload, training/inference, React UI.
