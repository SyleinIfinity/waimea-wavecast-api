# 10 — Báo cáo kiểm tra bản thiết kế/Scaffold

## Đã kiểm tra trong môi trường tạo bộ tài liệu

- `pytest -q`: 3/3 test pass (health liveness trả 200, readiness chưa triển khai trả 503, business stub trả 501).
- `python -m compileall`: code scaffold biên dịch được.
- `PyYAML`: parse thành công `compose.yaml`, `compose.prod.yaml` và `API_OPENAPI_DRAFT.yaml` (OpenAPI 3.1; **17 endpoint public dự kiến**).
- Nginx `nginx -t`: HTTP/HTTPS cấu hình có cú pháp hợp lệ **trong môi trường kiểm tra thay upstream giả lập và chứng chỉ thử nghiệm**; không chứng minh chứng chỉ Google Cloud/production đã được cấp.
- Đã đối chiếu tên **25 bảng Bronze 6 / Silver 5 / Gold 14** và tên cột với `01_init_full.sql` v2.0, sao chép để tra cứu.

## KHÔNG được coi là đã kiểm tra/thực thi

- `docker compose up` (môi trường kiểm tra không cài Docker Engine); chưa kiểm tra quá trình build/chạy container thực tế.
- Chưa truy cập Cloud SQL/GCS trên dự án Google Cloud của người dùng, chưa có chứng cứ IAM/bucket/TLS chạy thật.
- Chưa code business endpoints, NOAA ingestion, preprocessing, analysis, training, inference hoặc scheduler; mọi endpoint trong OpenAPI draft chưa hoạt động.
- Chưa chạy backfill NOAA 2005→nay; chưa chứng minh hiệu năng, correctness của models hoặc metrics thực tế.
- Chưa audit production security, dependency lock, SSL renewal, secrets lifecycle, data migrations ở hệ thống thật.

## Cần kiểm tra tiếp trong repo thật

1. `docker compose config`; `docker compose build`; `docker compose ps`.
2. `docker compose exec nginx nginx -t` khi DNS/Compose network thật hoạt động.
3. Cloud SQL Proxy IAM + DB 17 `SELECT 1`; GCS upload/read precondition `if-generation-match`.
4. Implement `/health/ready` kiểm tra dependency thật, unit/integration tests cho mỗi microservice.
5. End-to-end `NOAA→GCS→Bronze→Silver→Gold→API`, sau đó test xóa scoped Gold và tái tạo không train.
