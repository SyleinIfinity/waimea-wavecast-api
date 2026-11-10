# Waimea WaveCast — Frontend Product & Technical Specification v1.0

**Phạm vi:** Frontend React cho hệ thống NOAA NDBC Station 51201, backend FastAPI, Cloud SQL PostgreSQL 17 (Bronze/Silver/Gold), GCS lưu raw/model. Domain dự kiến: `https://waimea.liems.io.vn` → `https://apiwaimea.liems.io.vn/api/v1`.

**Đây là tài liệu đặc tả/thiết kế để nhóm code, không phải Frontend đã hoàn thiện.** Tất cả endpoint nghiệp vụ đều là contract dự kiến của Backend Blueprint trước đó; chưa chứng minh chúng đã được lập trình hoặc hoạt động trên Google Cloud. Không dùng dữ liệu minh họa làm dữ liệu NOAA thực tế.

## Tóm tắt quyết định thiết kế

- **Public:** Dashboard, Observations, Historical Analytics, Trend/Seasonality, Correlation, Clustering, Forecast & Classification, Model Evaluation, Data Quality.
- **Admin-only:** Pipeline Monitor + Demo Control. Không đưa nút `DELETE DATABASE` lên giao diện công khai.
- **Demo:** Chỉ xóa pattern tái tạo được trong Gold, có dry-run/confirm/scope/audit; giữ Bronze, Silver, models và GCS. Giảng viên yêu cầu **xóa bằng tay dữ liệu pattern**, nên kịch bản chuẩn dùng DBeaver/manual SQL; Admin Demo UI là phương án hỗ trợ, không thay thế yêu cầu nếu thầy kiểm tra thao tác thủ công.
- **Các dữ liệu thời gian:** sensor `observed_at` UTC, ngày phân tích `date_hst` và target forecast theo Hawaii (UTC−10, không đổi DST). `T+1` là ngày **Hawaii**, không phải ngày theo máy người xem.
- **Đồ thị:** 3 biểu đồ ngày/tuần/tháng; line/scatter/histogram; trend + seasonal + residual; correlation heatmap; clustering profiles; forecast và classification.
- **Không giả real-time:** trình bày `last_observed_at`, `last_fetched_at`, `last_processed_at`, trạng thái cập nhật và cảnh báo độ trễ tách biệt; polling Frontend ≠ thu thập NOAA.

## Danh sách tài liệu

| Tài liệu | Dùng để làm gì |
|---|---|
| `docs/01_PRODUCT_STRUCTURE.md` | Sitemap, bố cục Header/Sidebar/Main/Footer, vai trò, menu và đề xuất kích thước |
| `docs/02_SCREEN_BY_SCREEN.md` | Chi tiết **từng màn hình, component, nút, chart, tooltip, bảng, trạng thái, responsive** |
| `docs/03_FRONTEND_BACKEND_API_MAPPING.md` | Ánh xạ UI ↔ endpoint ↔ bảng PostgreSQL; những endpoint Backend còn phải bổ sung |
| `docs/04_ADMIN_DEMO_SAFETY.md` | Nút Admin, luồng Demo xóa/tái tạo pattern, phân quyền, xác nhận, audit, idempotency |
| `docs/05_TECH_STACK_IMPLEMENTATION.md` | Stack React/TypeScript, cây thư mục, state/query/chart, data contracts, performance/UX |
| `docs/06_ACCEPTANCE_ROADMAP.md` | User stories, tiêu chí chấp nhận, ma trận pattern cuối kỳ, phân việc, phases |
| `contracts/ui_response_examples.json` | Ví dụ **giả lập về cấu trúc phản hồi**, KHÔNG phải số đo NOAA thật |

## Cách sử dụng tài liệu

1. Nhóm Backend và Frontend thống nhất **tên endpoint và dữ liệu trả về** ở `03_...` trước khi code.
2. Nhóm UI/UX dựng trang theo `01_...` và `02_...`; mỗi chart phải hiển thị metadata đơn vị, nguồn và thời gian.
3. Backend triển khai missing endpoint **được liệt kê rõ** ở `03_...`, không để Frontend tự gọi SQL/GCS.
4. Frontend viết mock API theo hợp đồng, sau đó chuyển sang Backend thật bằng cấu hình `VITE_API_BASE_URL`.
5. Test `06_...`, diễn tập demo `04_...`; chỉ sử dụng dữ liệu thật cho các minh chứng báo cáo.

## Các căn cứ nguồn

- Tài liệu môn học `TONG_HOP_KIEN_THUC_VA_DO_AN_CUOI_KY.md` (tuần 6–10), đặc biệt các phần pattern tuần 9 và demo xóa/tái tạo kết quả.
- Backend Blueprint v1.0 và Database v2.0 (25 bảng) đã thảo luận trong hội thoại. Vì bản mã nguồn Backend hiện chưa được truy cập trực tiếp trong lượt này, các endpoint được xem là **hợp đồng dự kiến**, phải đồng bộ lại với OpenAPI thật khi Backend được triển khai.
- Hỗ trợ thư viện: React + TypeScript, TanStack Query, Apache ECharts, React Router, Tailwind CSS (đề xuất, không phải yêu cầu của giảng viên).
