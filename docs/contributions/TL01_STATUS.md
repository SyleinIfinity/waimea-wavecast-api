# TL01 — STATUS REPORT | Database / Database API

**Người thực hiện:** Trần Văn Thọ Khang

**Nhánh Git:** `feature/tl05-be-ml-analysis` (TL01 transferred here; create separate TL01 PR)

**Commit / Pull Request:** Chưa tạo PR cho phần code mới.
**Ngày cập nhật:** 12/11/2026

| Task ID | Trạng thái | Code path | Test command/result | Ghi chú/blocker |
|---|---|---|---|---|
| TL01-01 | DONE (độc lập) | `database/migrations/`; `database/README.md`; `services/database_api/app/db/`; `services/database_api/app/repositories/`; `services/database_api/app/schemas/`; `services/database_api/contracts/` | `scripts.inspect_db`: PostgreSQL 17.11, Bronze 6/Silver 5/Gold 14, 5 views, 2 revision triggers. SHA-256 migration trùng SQL đã dùng. `scripts.rollback_integration`: PASS, ROLLBACK. | Đã có pool, transaction, repository, DTO, OpenAPI và fixture. |
| TL01-02 | DONE (độc lập với mock worker) | `services/database_api/app/api/{public,internal,admin}.py`; `services/database_api/app/main.py`; `Dockerfile`; `deploy/nginx/apiwaimea.conf`; `compose.yaml` | `scripts.smoke_read`: 19 GET routes/health PASS trên DB thật; `scripts.http_rollback`: POST Bronze/Silver/Gold, GET NOAA/Gold, cursor và ML promotion PASS, ROLLBACK; `pytest`: 9 PASS; `docker-compose config -q`: PASS. | Admin xóa Gold có staging/backup flag, preview 1 lần, xác thực, giới hạn bảng/ngày; production bị chặn. Worker rebuild đã test bằng mock, chưa có TL04/TL05 thật để thử liên dịch vụ. |
| TL01-03 | PARTIAL | `tests/tl01/`; `services/database_api/scripts/`; hai báo cáo TL01 | Backend pytest: 110 PASS, 2 SKIPPED; TL01 HTTP rollback and smoke GET: PASS on PostgreSQL 17.11. | Chưa chạy Docker build (Docker daemon không hoạt động trong môi trường này), chưa có PR và chưa chạy tích hợp thật với TL04/TL05/Frontend. |

## Kết quả dữ liệu và tích hợp

- Database hiện có schema/metadata của NOAA 51201; chưa thấy quan trắc, Gold patterns hay model trong các smoke GET. Không ghi dữ liệu fixture vào DB: mọi kiểm thử ghi dùng transaction `ROLLBACK`.
- Giao diện `/internal/v1` đã có các thao tác cho worker; TL04/TL05 cần dùng token và `Idempotency-Key`, sau đó chạy checklist I-01/I-02 với worker thật.
- Admin rebuild chuyển yêu cầu đến worker qua HTTP và chỉ trả `202` khi worker chấp nhận; chưa chứng minh job thật hoàn thành.
- Không chạy thao tác xóa Gold thật. Kiểm thử guard/preview/delete chỉ dùng fake DB; trước demo cần staging, backup và đối chiếu quyền DELETE.
- `D:\kdl\PhanCong\docs\STATUS_REPORT.md` là bản tổng hợp mẫu cũ, cần sinh lại sau khi báo cáo của các thành viên được chốt.

## Kết luận bàn giao

- TL01 độc lập: `DONE` 2/3 task; `PARTIAL` 1/3 do Docker/PR/tích hợp cuối.
- Không có secret thật hoặc fixture NOAA giả được ghi vào source code; credential chỉ được truyền qua môi trường khi kiểm thử.
- Các bước còn lại để nghiệm thu cuối: Docker build/run, kiểm thử với TL04/TL05 và frontend, chạy demo staging, tạo PR và xác nhận log tích hợp.
