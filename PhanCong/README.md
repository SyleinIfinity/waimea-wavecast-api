# WAIMEA WAVECAST — KẾ HOẠCH CODE SONG SONG CHO 5 NGƯỜI (v1.0)

**Mục tiêu:** mỗi thành viên tự triển khai, tự kiểm thử bằng dữ liệu giả lập/hợp đồng API cố định, không phụ thuộc việc đồng đội hoàn thành trước; chỉ hợp nhất khi cả 5 nhánh đã đạt điều kiện nghiệm thu. Kế hoạch chia **3 bước ngắn/người**, không quy định timeline ngày cứng. Dữ liệu quan trắc duy nhất là NOAA 51201; Cloud SQL PostgreSQL 17 với `bronze/silver/gold`; GCS lưu raw/dataset/model.

## Bảng phân công (mỗi người một file tài liệu)

| Mã | Người | Phạm vi chính | Repo/nhánh đề xuất | Tài liệu |
|---|---|---|---|---|
| **TL01** | Database 1 | Schema 25 bảng, migration, Database API + DTO + admin demo facade | `waimea-wavecast-backend` / `feature/tl01-db-api` | [TL01-Database.md](TL01-Database.md) |
| **TL02** | Frontend 1 | App shell, Overview, Observations, Time Series, Trend/Seasonal, Data Quality | `waimea-wavecast-frontend` / `feature/tl02-fe-core` | [TL02-Frontend-Core.md](TL02-Frontend-Core.md) |
| **TL03** | Frontend 2 | Correlation, Clustering, Forecast/Classification, Model Evaluation, Pipeline Monitor, Demo Control | `waimea-wavecast-frontend` / `feature/tl03-fe-ml-admin` | [TL03-Frontend-ML-Admin.md](TL03-Frontend-ML-Admin.md) |
| **TL04** | Backend 1 | NOAA client, GCS raw, Bronze Ingestion, Silver Preprocessing/QC | `waimea-wavecast-backend` / `feature/tl04-be-ingestion` | [TL04-Backend-Ingestion.md](TL04-Backend-Ingestion.md) |
| **TL05** | Backend 2 | Analysis, Training/Evaluation, K-means, Regression, Classification, Scheduler | `waimea-wavecast-backend` / `feature/tl05-be-ml-analysis` | [TL05-Backend-Analytics-ML.md](TL05-Backend-Analytics-ML.md) |

**TL01 là chủ sở hữu Database API; TL04/TL05 không chờ TL01**: cả hai xây dựng worker với `FakeDatabaseGateway` theo hợp đồng chung, chuyển sang HTTP adapter khi tích hợp. **TL02/TL03 không chờ Backend**: phát triển từng màn hình qua mock API và data fixture; chuyển sang API thật bằng biến môi trường khi ghép.

## 3 bước tiến hành toàn nhóm — ai cũng bắt đầu ngay

1. **Bước 1 — Khóa interface + dựng phần việc riêng:** mỗi người đọc tài liệu TLxx và `00_HOP_DONG_CHUNG.md`, tạo branch riêng, viết interface/fixtures/tests/mock. Không sửa code thuộc người khác.
2. **Bước 2 — Code phần sở hữu:** triển khai thực tế chức năng theo checklist trong TLxx; unit test + contract test; vẫn chạy độc lập với mock local.
3. **Bước 3 — Nghiệm thu nhánh + báo cáo:** chạy test; ghi đủ hai file báo cáo cá nhân ở `docs/contributions/`; tạo PR. Sau khi hợp nhất, sinh hai tài liệu tổng `docs/STATUS_REPORT.md`, `docs/COMPLETED_FUNCTIONS.md` và chạy smoke/e2e chung.

### Quy tắc độc lập tuyệt đối trong giai đoạn code

- Không đợi người khác tạo bảng API: làm việc trên adapter contract + fixtures. Không đợi người khác hoàn thiện UI shell: TL03 export page component riêng, TL02 mới gắn route khi tích hợp.
- Không sửa cùng file. Nếu cần thêm API/DB field, ghi vào phần **Yêu cầu tích hợp** trong hai file báo cáo, không tự ý đổi contract của nhóm.
- Không dùng dữ liệu NOAA giả như dữ liệu thực khi triển khai production. Fixture phải gắn `mock` và chỉ dành cho test/demo giao diện.
- Khi người phụ trách hoàn thành, **không tự tuyên bố phần tích hợp chung đã xong** nếu chưa kiểm thử với thành phần thật.
- Phân biệt kỹ: lệnh xóa cho demo chỉ xóa các **Gold pattern được phép** trong **staging** và có xác nhận; không xóa schema, Bronze, Silver, model registry, training/evaluation hay GCS model.

## Bàn giao bắt buộc (mỗi người 2 file, cuối cùng hệ thống có 2 file tổng)

Trong nhánh của từng người, tạo hoặc cập nhật:

- `docs/contributions/TLxx_STATUS.md` — danh sách báo cáo trạng thái: task, PASS/FAIL/BLOCKED/NOT_STARTED, đường dẫn code/test, blocker, API phụ thuộc.
- `docs/contributions/TLxx_FUNCTIONS.md` — từng **function/endpoint/component đã hoàn thiện**: tên thực tế, file, input/output, test, bằng chứng. Chỉ ghi “hoàn thiện” khi đã chạy test tương ứng.

Sau khi merge hai repository, copy thư mục `scripts/` từ bộ kế hoạch vào workspace quản lý nhóm và chạy:

```bash
python scripts/aggregate_reports.py \
  --backend-repo /path/waimea-wavecast-backend \
  --frontend-repo /path/waimea-wavecast-frontend \
  --output /path/waimea-final-delivery/docs
```

Nếu chỉ muốn xem **hai bản báo cáo mẫu** ban đầu trong ZIP, chạy `python scripts/aggregate_reports.py` không tham số. Kết quả sinh:

1. `docs/STATUS_REPORT.md` — tình trạng toàn bộ 5 nhóm việc.
2. `docs/COMPLETED_FUNCTIONS.md` — tất cả chức năng **được khai báo hoàn thiện**, theo người.

**Ghi chú:** Trong ZIP này đã có hai bản tổng ban đầu thể hiện “chưa triển khai” và 10 file mẫu để điền; script tổng hợp **không kiểm chứng code thay cho kiểm thử**, người reviewer phải xem log/PR thật.

## Tiêu chí nhận bàn giao

Mỗi nhánh: build/test được độc lập; giữ API contract; có chức năng/đầu ra kiểm chứng được; không commit secrets; có 2 báo cáo trong `docs/contributions`; mô tả lệnh chạy; PR không xung đột file do nhóm khác sở hữu. Chỉ sau bước tích hợp mới kiểm tra luồng đầy đủ NOAA → GCS/Bronze → Silver → Gold → REST API → Frontend và demo Gold xóa/tái tạo.

**Tài liệu gốc để đọc:** `Waimea_WaveCast_DB_GCS_v2.0` (SQL v2.0), `Waimea_WaveCast_Backend_Blueprint_v1` (Backend docs), `Waimea_WaveCast_Frontend_Spec_v1` (Frontend docs), `TONG_HOP_KIEN_THUC_VA_DO_AN_CUOI_KY.md` (yêu cầu học phần).
