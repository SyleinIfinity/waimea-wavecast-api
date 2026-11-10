# 06 — Roadmap, traceability requirements và UI Acceptance Criteria

## 1. Đối chiếu yêu cầu cuối kỳ ↔ UI ↔ API

| Yêu cầu môn học (từ bản tổng hợp) | Màn hình/hiển thị | API phải có | Test chứng minh |
|---|---|---|---|
| Hệ thống có cập nhật theo thời gian | Overview Freshness + Pipeline Monitor | observations/latest, ingestion/status | new NOAA records hoặc 0 new records và log poll rõ |
| 3 chart ngày / tuần / tháng | Time Series (3 chart riêng) | metrics/summary group_by | 3 HTTP GET, chart đúng aggregation |
| Line chart | Observations, Time Series | observations/metrics | line real NOAA, zoom/filter |
| Scatter chart | Time Series, Correlation | analytics/scatter | paired values QC pass |
| Histogram | Time Series | analytics/histogram | bins/counts, correct units |
| Trend + Seasonal | Trend & Seasonality | analytics/trend-seasonal | show observed/trend/seasonal/residual |
| Correlation matrix | Correlations | analytics/correlations | heatmap with coeff and paired_count |
| Clustering | Clustering | clusters/profiles | visual groups, explain feature/centroid, model eval |
| Prediction ngày mai | Forecast regression | predictions/latest/history | target HST T+1, model version, MAE on test |
| Classification ngày mai | Forecast classification | classifications/latest/schemes | label scheme version, model version, accuracy/F1 |
| Giao diện dùng API | all pages | FastAPI JSON | F12 Network demonstrates responses |
| Docker Compose/microservices | Pipeline Monitor + demo external terminal | systems status/jobs | screenshot containers & service relations |
| Xóa kết quả pattern rồi tái tạo | Admin Demo Control / DBeaver | admin/demo/... | patterns missing → rebuild → shown again |
| Model không train lại trong demo | Model Evaluation + Demo evidence | model registry/training logs | before/after training_runs same |
| Báo cáo kiến trúc và hoạt động | Data lineage, Model Lab, Monitoring | metadata/provenance | screenshots + source and diagrams |

**Nội dung slide**: liệt kê day/week/month, line/scatter/hist, trend/seasonal/correlation, next-day forecast/class, kết quả phân cụm; kịch bản demo xóa Gold pattern bằng tay và gọi API để tái tạo. Bố cục Header/Sidebar và chart layout bên dưới là **lựa chọn thiết kế của nhóm**.

## 2. Phân giai đoạn triển khai theo dependency

| Phase | Công việc | Definition of Done |
|---|---|---|
| F0 Design foundations | route, shell, tokens, reusable charts, date/time utility, OpenAPI types, mocked states | /overview responsive, theme, loading/empty/error |
| F1 Main NOAA pages | Overview, Observations, Time Series chart day/week/month, histogram/scatter | API mock→real; correct units, HST/UTC; data real |
| F2 Patterns | Trend/Seasonal, Correlations, Clusters | chart method metadata, filters, missing data handled |
| F3 ML results | Forecast, Classification, Model Evaluation | model provenance, metrics, baseline, null actual, test vs live separated |
| F4 Operational | Data Quality, Pipeline Monitor, admin guard | source and job states, authenticated route, 401/403 |
| F5 Demo & production | Demo Control, job polling, deletion status, Cloud Run, F12, responsive/a11y | staged demo xóa/tạo lại, no retraining, no data loss |

**Ưu tiên thực tế:** F0 → F1 → F2 → F3 → F4 → F5; có thể song song FE mock và BE OpenAPI, nhưng trước khi nghiệm thu phải có API thật.

## 3. Acceptance criteria theo màn hình

| ID | Scenario | Expected results |
|---|---|---|
| UI-01 | Mở `/overview` desktop | Header, Sidebar, 4 metrics nếu available, chart, forecast, QC, unit/source/time |
| UI-02 | API latest NOAA trả partial/null | Card null = N/A, không thành 0; tooltip reason |
| UI-03 | latest observed đã cũ nhưng UI vừa refetch | status shows stale NOAA; không “LIVE” xanh chỉ vì request 200 |
| UI-04 | Mở `/analytics/time-series` | 3 chart riêng Day/Week/Month cùng metric và date HST |
| UI-05 | Chọn `WVHT` và đổi date range | URL/search params cập nhật, GET đúng range, chart không lẫn kết quả cũ |
| UI-06 | Scatter và histogram | axes/units/sample counts/bin labels đúng, không vẽ synthetic points |
| UI-07 | Trend insufficient data | banner insufficient_data; không hiển thị seasonal giả |
| UI-08 | Correlation pair thiếu dữ liệu | ô N/A, n_pairs hiện; không đồng nhất coefficient null=0 |
| UI-09 | K-means cluster IDs thay giữa model | label/color keyed by model_version, centroid đúng |
| UI-10 | Forecast hiện ngày mai | target day Hawaii đúng; model_id/version và cutoff rõ |
| UI-11 | Forecast actual chưa có | chỉ predicted, actual/error N/A; không plot 0 hoặc kẻ đường truth |
| UI-12 | Model metrics | phân biệt offline test vs online performance, baseline visible |
| UI-13 | Viewer mở `/admin/demo` | bị điều hướng 403/login; API POST vẫn 403 nếu trực tiếp gọi |
| UI-14 | Admin nhấn Preview Delete | hiển thị đúng bảng/row count, không xóa ngay |
| UI-15 | Admin chọn protected table | server reject, UI không thể tiếp tục |
| UI-16 | Demo DELETE staging Gold pattern | Bronze/Silver/GCS/training_runs giữ nguyên; frontend empty pattern |
| UI-17 | Admin gọi rebuild API | 202 job ID, UI không báo success sớm |
| UI-18 | Job success và re-fetch | Gold pattern được render lại, counts reconcile |
| UI-19 | Job failed | error info, retry safe, không phát chart dự đoán giả |
| UI-20 | 360px viewport | drawer nav, vertical charts, touch targets, no horizontal page overflow |
| UI-21 | Keyboard/screen reader | focus, labels, chart data table, contrast, reduced motion |
| UI-22 | Cloud Run + CORS | frontend URL HTTPS truy vấn API URL HTTPS, không mixed content |
| UI-23 | DevTools/F12 | chỉ thấy JSON API; không lộ DB password, admin token, GCS credentials |
| UI-24 | Cloud SQL/GCS không sẵn sàng | hiển thị unavailable/stale phù hợp, không gửi nút destructive qua public |

## 4. UI states theo các service trạng thái

| Service | Data empty | Service down | Job running |
|---|---|---|---|
| NOAA Ingestion | “Chưa có bản ghi mới” khi poll ok 0 records | “Không thể kiểm tra nguồn”, last observed vẫn hiện | hiển thị processing/fetching |
| Preprocessing | raw tồn tại nhưng chưa có Silver: “Đang chuẩn hóa” | QC unavailable | job status |
| Analysis | Silver có nhưng Gold trống: “Chưa tạo pattern” | analytics failed | analysis processing |
| Clustering | model absent -> “Chưa có mô hình” | model unavailable | assignment processing |
| Prediction | missing feature/model -> no forecast | inference error | inference processing |
| Classification | missing scheme/model -> no label | classifier error | classification processing |
| Database API | stale cache with warning if available | 503 screen | query in flight |

## 5. Mini backlog và phân công gợi ý cho 5 người

- **Member A**: App shell/Responsive/design system/router/auth/role guards.
- **Member B**: Overview, Observations, Time Series charts, export/data table.
- **Member C**: Trend/Seasonality, Correlation, Clustering dashboards.
- **Member D**: Forecast/Classification, Model Evaluation, ML chart contracts.
- **Member E**: Data Quality, Pipeline Monitor, Demo Control, API integration/e2e/Cloud Run deployment.

Dù chia theo trang, **API client, design tokens, chart primitives và date conversion phải có người làm chủ thống nhất**. Mỗi PR cần contract test với Backend trước khi merge.

## 6. Khi nào đủ để giảng viên chấm?

MVP để trình bày cần: 3 chart ngày/tuần/tháng, line/scatter/hist, trend/seasonal, correlation, clustering, regression prediction ngày mai, classification ngày mai, API Network hoạt động, Docker Compose service logs, xóa pattern Gold qua DBeaver/staging và tái tạo bằng API từ model GCS (no retraining). Các trang Model Evaluation & Data Quality giúp báo cáo thuyết phục, nhưng không được dùng chúng thay cho các pattern bắt buộc.

## 7. Quyết định chưa khóa — không giả là đã chốt

1. Chọn tên/tên miền frontend thật và bucket thật (trong kiến trúc đang dùng `waimea.liems.io.vn`, nhưng cần DNS deploy).
2. Auth admin (OIDC/session/Basic behind VPN) và chính sách người dùng public.
3. Metric target là median WVHT ngày HST T+1 (đề xuất từ Backend Blueprint, cần nhóm xác nhận và train thực).
4. Threshold classification scheme phải có tài liệu nguồn/logic, không dùng quy tắc cảnh báo an toàn tự chế.
5. Stale threshold/noaa polling và min quality gates phải dựa dữ liệu thật, versioned config.
6. Backend bổ sung các endpoint `admin/*`, quality detail, model evaluation-samples, centroid profiles và freshness như API gap matrix.
7. Nếu giảng viên yêu cầu xóa bằng tay, thực hiện DBeaver, **UI delete button chỉ optional**.
