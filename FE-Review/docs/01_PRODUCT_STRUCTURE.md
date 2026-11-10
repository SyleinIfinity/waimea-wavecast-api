# 01 — Cấu trúc sản phẩm, Information Architecture và giao diện chung

## 1. Product goal và nhóm người dùng

**Goal:** biến dữ liệu quan trắc NOAA 51201 và kết quả Data Mining thành giao diện có thể hiểu, có thể tương tác, chứng minh được dữ liệu lấy qua API, theo dõi được lần xử lý gần nhất và trình diễn tái tạo pattern theo yêu cầu cuối kỳ.

- **Viewer (công khai):** xem thông số sóng đã đo, lịch sử, pattern phân tích, dự báo do nhóm tự huấn luyện và chất lượng mô hình. Chỉ được gọi GET; không thấy chức năng xóa/training/backfill.
- **Operator/Admin (đăng nhập):** xem trạng thái job, kiểm tra nguồn và service, kích hoạt poll/reprocess/rebuild, duyệt kết quả, thực hiện demo có giới hạn.
- **Giảng viên khi demo:** theo dõi rõ API nào được gọi và dữ liệu xuất hiện/mất đi; có thể xem màn hình monitor và F12 Network.

## 2. Sitemap và routes

```text
/
├── /overview                        Dashboard tổng quan ★ landing
├── /observations                    Quan trắc gần hiện tại + lịch sử chi tiết
├── /analytics
│   ├── /time-series                 3 chart ngày / tuần / tháng + line/scatter/histogram
│   ├── /trend-seasonality           Decomposition: observed/trend/seasonal/residual
│   └── /correlations                Correlation heatmap + scatter theo cặp biến
├── /clustering                      K-means, assignment, centroid, cluster profile
├── /forecast                        Dự đoán hồi quy + phân loại cho ngày mai HST
├── /models                          Model versions / evaluation / test evidence
├── /data-quality                    Completeness / gaps / field nulls / provenance
├── /system (admin)                  Pipeline status / runs / logs / service health
└── /admin/demo (admin)              Demo xóa scoped pattern / rebuild / theo dõi job
```

**Navigation:** Public 8 mục lớn (Overview, Observations, Time Series, Trend & Seasonality, Correlations, Clustering, Forecast, Model Evaluation) và Data Quality; nhóm Admin gói dưới accordion `System` để tránh sidebar quá dài. Có thể gom ba màn Analytics dưới mục cha.

## 3. Desktop App Shell

```text
┌────────────────────────────── HEADER (sticky ~64px) ───────────────────────────────────────┐
│ [logo] Waimea WaveCast     [NOAA 51201 · Waimea Bay] [UTC/HST] [Last sync] [theme] [admin] │
├───────── SIDEBAR (~240px) ─┬────────────────────────────────────────────────────────────────┤
│ Overview                   │ Breadcrumb / Title / Description / Source status                 │
│ Observations               │                                                                  │
│ Analytics ▾                │ ┌──────────────────────── GLOBAL CONTROLS ──────────────────────┐ │
│   Time Series              │ │ Station (fixed) | Period | Start/End | Metric | Apply | Reset  │ │
│   Trends & Seasonal        │ └────────────────────────────────────────────────────────────────┘ │
│   Correlations             │                                                                  │
│ Clustering                 │ ┌───────────── CONTENT: responsive 12-column grid ──────────────┐ │
│ Forecast                   │ │ KPI / charts / tables / interpretation / quality notes          │ │
│ Model Evaluation           │ └────────────────────────────────────────────────────────────────┘ │
│ Data Quality               │                                                                  │
│ ─── admin only ───          │ Source, method, timezone, generated time, disclaimer              │
│ Pipeline Monitor           │                                                                  │
│ Demo Control               │                                                                  │
├────────────────────────────┴────────────────────────────────────────────────────────────────┤
│ FOOTER: NOAA NDBC 51201 | model not authoritative forecast | API/Last processed | project  │
└──────────────────────────────────────────────────────────────────────────────────────────────┘
```

## 4. Vai trò và nội dung của từng vùng

### Header (64–72 px desktop, sticky)

| UI element | Nội dung | Hành vi/API |
|---|---|---|
| Logo + product | Waimea WaveCast, Home link | route `/overview` |
| Station indicator | `NOAA / NDBC 51201 — Waimea Bay` | `/stations/51201`; chỉ một trạm nên không cho chọn station khác giả |
| Live freshness | `Latest observation: YYYY-MM-DD HH:mm UTC` + `Độ trễ ...` | `/observations/latest`, cần `last_fetched_at` thêm từ backend nếu hiện chưa có |
| HST/UTC display | User chọn timezone hiển thị tooltip; **ngày tổng hợp cố định HST** | FE state; không thay nghĩa target day |
| Update UI button | `Làm mới màn hình` (refetch GET) | TanStack Query invalidate/refetch, **KHÔNG** gọi NOAA ingestion |
| Theme / accessibility | Light / dark, reduced motion | FE preference |
| User menu | Viewer hoặc Operator; đăng xuất | cơ chế auth khi đã triển khai |

### Sidebar (224–260 px, sticky dưới header)

- Active item, icons, collapse trạng thái, current page, keyboard nav; admin section không render nếu chưa authenticated/authorized.
- Mỗi mục mở đúng route, menu Analytics mở/thu gọn; không render vô số chart trong sidebar.
- Có thể có status dot tổng quan, nhưng không dùng `success` giả nếu API health 200 liveness mà DB chưa ready.

### Main content (max-width 1600 px, padding 24–32 desktop)

1. **Page heading:** tên trang, một câu hỏi khoa học trang đó trả lời; caption nguồn/cutoff/timezone.
2. **Global filter bar:** date range, granularity, metric, chart-specific controls; URL query params để share lại view.
3. **Data status/banner:** `loading`, `ready`, `partial`, `stale`, `empty-pattern`, `source-unavailable`, `model-unavailable`; không đồng nhất.
4. **Key content:** cards/charts/table/interpretation. Mỗi chart có title, unit, methodology, date range, sample_count, last_generated_at, zoom/export.
5. **Details section:** methodology/data lineage, QC notes và link sang Model Evaluation nếu chart dự báo.

### Footer (32–64 px)

- NOAA NDBC 51201 source credit, `Data observed_at`, `Data processed_at` khi có; GitHub/docs optional; disclaimer: mô hình giáo dục, không phải dự báo/safety warning chính thức.
- Không ghi “Real-time” nếu chỉ là kiểm tra 30 phút với dữ liệu nguồn chậm cập nhật.

## 5. Breakpoints/responsive (quy định UI nhóm, không phải từ slide)

| Viewport | Layout | Charts |
|---|---|---|
| >= 1440 px | Sidebar ~248, 12-col main | 2 cột chart, một số chart full width |
| 1024–1439 px | Sidebar ~224 hoặc collapse | 2-col tùy chart |
| 768–1023 px | Sidebar drawer, header gọn | 1–2 cột |
| < 768 px | Top bar + drawer, single column | Chart scroll theo chiều dọc, không giấu pattern bắt buộc |

Minimum target viewport 360 px; table scroll ngang và có `Column visibility` để chọn trường; chart tooltip đủ touch targets ≥44px.

## 6. Thiết kế thị giác và hệ thống tokens đề xuất

- Visual direction: **Scientific Ocean Observatory**, nghiêm túc, rõ ràng, tránh trang dashboard tài chính stock template; biển, phao NOAA, lưới đơn giản, whitespace.
- Colors (gợi ý): `--ocean-950:#082033`; `--ocean-700:#155478`; `--teal-500:#13A0A5`; `--sea-100:#E8F4F5`; `--surface:#FFFFFF`; `--text:#193241`; status success/warning/error riêng; contrast WCAG AA cần kiểm tra.
- Typography: `Inter` hoặc system sans; numeric tabular; 28–32 heading, 20 section, 14–16 body, 12–13 metadata.
- Layout: 8px spacing grid, card radius 12–16 px, 1px borders, shadow nhẹ; đồ thị dùng màu nhất quán qua trang (WVHT = teal, DPD = indigo, WTMP = orange chẳng hạn).
- Legends luôn kèm đơn vị và không dùng màu là cách duy nhất diễn đạt cluster/status (có icon/text/pattern).

## 7. Các nút thao tác — phân nhóm an toàn

| Nhóm | Nút | Ai thấy | Ý nghĩa |
|---|---|---|---|
| Read-only | Làm mới giao diện | Viewer | refetch các GET; không ingest |
| Read-only | Zoom, reset zoom, export PNG/CSV | Viewer | làm việc trên dữ liệu đã nhận; export không bao gồm secrets |
| Read-only | Xem chi tiết, chọn biến, đổi phạm vi | Viewer | cập nhật bộ lọc/query |
| Data operation | Thu thập NOAA ngay | Admin | trigger **Ingestion**; có thể 0 bản ghi mới |
| Data operation | Xử lý lại dữ liệu Silver/Gold | Admin | jobs với phạm vi, 202 + progress |
| ML operation | Chạy lại dự đoán / phân loại | Admin | inference từ model đã lưu, **không train** |
| ML operation | Huấn luyện phiên bản mới | Admin chuyên trách; không nằm trang demo mặc định | one-off training, cần phê duyệt và đánh giá |
| Demo | Xem số dòng Gold sẽ xóa (dry run) | Admin | xác nhận phạm vi/backup trước deletion |
| Demo | Xóa **pattern Gold (demo)** | Admin được cấp riêng | **không** xóa DB, Bronze/Silver/GCS/models/evaluation |
| Demo | Tái tạo pattern | Admin | trigger từng service và theo dõi jobs |

**Không thiết kế nút có tên `Xóa DB` hay `Reset All Database`** vì nguy cơ phá hủy quan trắc và model. Nếu giảng viên yêu cầu *xóa bằng tay* trong DBeaver, Demo Control chỉ hiển thị checklist/state và nút chạy lại sau thao tác thủ công.
