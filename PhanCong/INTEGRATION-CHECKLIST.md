# Checklist tích hợp — chỉ chạy SAU khi 5 nhánh độc lập được nghiệm thu

| Thứ tự | Việc ghép | Kiểm tra |
|---|---|---|
| I-01 | TL01 Database API + TL04 ingestion/preprocessing | NOAA sample → GCS → Bronze/Silver, 2 lần ingest không trùng, correction có revision |
| I-02 | TL01 Database API + TL05 Analytics/ML | Silver → Gold day/week/month/trend/correlation; model registry/generation; inference không train lại |
| I-03 | TL02 App Shell + TL03 page exports | Không conflict router/style; public/admin routes đúng; frontend build |
| I-04 | Frontend ↔ Backend REST | Bỏ mock bằng env; contract response null/timezone/unit/status; F12 Network; CORS |
| I-05 | Docker Compose + Nginx TLS + Cloud SQL Proxy + GCS IAM | Chỉ Nginx mở 80/443, auth internal/admin, health/readiness thực tế |
| I-06 | Demo staging | Xóa **chọn lọc Gold patterns**, không xóa Bronze/Silver/model/GCS → gọi API rebuild → Dashboard phục hồi |
| I-07 | Báo cáo | Mỗi PR có 2 báo cáo; sinh hai file tổng dưới `docs/`; reviewer xác nhận test bằng chứng |

**Chặn merge** nếu contract đổi không thông báo, code sửa vùng sở hữu của người khác, fixture/mock lọt production, secrets được commit, chức năng xóa DB dùng `DROP/TRUNCATE CASCADE`, hoặc script báo xóa model/training.

**Kết quả cuối:** một backend repo + một frontend repo, bộ migrations và GCS cấu hình, 2 file tổng báo cáo cho mỗi repo hoặc một workspace chung theo quy trình nhóm. Cần phân biệt build/test từng module với test tích hợp thực sự.
