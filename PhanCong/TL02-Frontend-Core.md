# TL02 — FRONTEND 1: CORE UI & DATA ANALYTICS

**Người phụ trách:** Thành viên 2 — Frontend 1. **Repo:** `waimea-wavecast-frontend`. **Nhánh:** `feature/tl02-fe-core`.

**Đọc:** `Waimea_WaveCast_Frontend_Spec_v1/docs/{01_PRODUCT_STRUCTURE.md,02_SCREEN_BY_SCREEN.md}` phần S01–S04 + S09, `03_FRONTEND_BACKEND_API_MAPPING.md`, `05_TECH_STACK_IMPLEMENTATION.md`.

**Độc lập với Backend:** tất cả màn hình gọi `src/features/<tên>/api.ts` qua interface từ `00_HOP_DONG_CHUNG.md`; mỗi feature có `*.mock.ts` và `*.test.tsx`. Không cần DB API tồn tại mới lập trình.

## TL02-01 — Layout + nền tảng giao diện

- [ ] Code `src/app/{App,router,providers}.tsx`, `src/layout/{Header,Sidebar,Footer,MainLayout}.tsx`, `src/components/ui/` và `src/styles/`: desktop/mobile, responsive, color tokens, loading/empty/error/status badges, filter bar.
- [ ] Code `src/lib/{apiClient,dateTime,units}.ts`: `VITE_API_BASE_URL`, fetch/React Query, UTC ↔ HST, NOAA units, không nhúng DB credentials; phân biệt `null` và `0`.
- [ ] Dựng route placeholder cho TL03 (`/analytics/correlations`, `/clustering`, `/forecast`, `/models`, `/system`, `/admin/demo`) bằng page registration/import contract để TL03 tự export component, chưa import file chưa tồn tại.

## TL02-02 — Các trang thuộc TL02

- [ ] `src/features/overview/`: Station hero, freshness, 4 KPI NOAA, line chart WVHT, dự đoán tóm tắt, trạng thái thiếu dữ liệu. API: station, latest observations/prediction/quality.
- [ ] `src/features/observations/`: bảng quan trắc phân trang, lựa chọn metric, line chart, lọc ngày UTC/HST, export CSV, gaps/QC; API: observations.
- [ ] `src/features/time-series/`: **ba biểu đồ riêng Daily / Weekly / Monthly**, kèm histogram WVHT và scatter WVHT–DPD; chọn metric/date range/mean-median; API metrics/summary, histogram, scatter.
- [ ] `src/features/trend/`: bốn phần observed/trend/seasonal/residual, phương pháp/thời đoạn; không hiển thị pattern giả khi data chưa đủ; API trend-seasonal.
- [ ] `src/features/data-quality/`: coverage/missing/gaps theo dữ liệu API có thật; màn hình chi tiết mới chỉ hiện nếu Backend có endpoint; fallback overview-only.

## TL02-03 — Test và bàn giao

- [ ] Vitest + React Testing Library: loading/empty/stale/error, `null -> N/A`, date HST, 3 charts day/week/month, zoom/filter; mobile 360px smoke.
- [ ] Mở trình duyệt F12 xác minh request qua mocked API; khi ghép không gọi DB/GCS trực tiếp.
- [ ] Tạo `docs/contributions/TL02_STATUS.md`, `docs/contributions/TL02_FUNCTIONS.md` ghi tên component/hook/api adapter đã test.

**Bàn giao:** Shell, UI kit, 5 feature pages, mock fixtures, route registration, tests, hai báo cáo.

**Nghiệm thu TL02:** `npm run build` + tests pass chỉ với mock; 3 biểu đồ thời gian, histogram và scatter có đúng input/units; route của TL03 không bị chặn; màn hình khi mất API không crash.

**Không sở hữu:** Clustering/Forecast/Models/Admin pages của TL03; Backend API/SQL.
