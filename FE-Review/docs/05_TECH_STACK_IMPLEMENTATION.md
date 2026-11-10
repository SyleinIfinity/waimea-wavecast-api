# 05 — Frontend Engineering Specification (React / TypeScript / API / charts)

## 1. Tech Stack đề xuất

| Nhóm | Công nghệ | Vai trò | Chú ý |
|---|---|---|---|
| Runtime/build | React + TypeScript + Vite | SPA triển khai Cloud Run dạng static server image | khóa phiên bản qua lockfile, không hard-code package major |
| Routing | React Router | nested routes + protected admin routes | auth chỉ ở FE không đủ; BE luôn kiểm tra quyền |
| Styling | Tailwind CSS + CSS variables | design tokens & responsive | hỗ trợ light/dark/contrast |
| Components | tự viết primitives hoặc shadcn/ui | Button, Card, Dialog, Tabs, Drawer, DataTable | WCAG keyboard và focus management |
| Charts | Apache ECharts + React wrapper | line/scatter/hist/heatmap/radar/dataZoom | đồng bộ filters và tooltips; chart datasets không nhúng data giả |
| Server state | TanStack Query | caching, invalidate, status polling | `refetchInterval` khác với Ingestion schedule |
| HTTP | fetch hoặc Axios + typed OpenAPI client | REST JSON, abort, timeout, errors | chỉ gọi VITE_API_BASE_URL, không DB/GCS |
| Forms/validation | React Hook Form + Zod | dates, numeric limits, admin confirmation | server phải validate lại |
| Date/time | date-fns + TZ support hoặc Luxon | format HST/UTC, date ranges | HST UTC-10 không đổi DST |
| Testing | Vitest, React Testing Library, Playwright | unit/component/e2e | MSW để mock API lỗi/trễ/rỗng |
| Quality | ESLint, Prettier, TypeScript strict | consistency | CI check |

Docs công khai về chart/dataZoom/heatmap: https://echarts.apache.org/en/feature.html. TanStack Query refetchInterval docs: https://tanstack.com/query/latest/docs/framework/react/guides/polling.

## 2. Cây thư mục Frontend repo độc lập

```text
waimea-wavecast-frontend/
├── public/
│   └── images/                     # logo và biểu tượng local (không chứa chart giả)
├── src/
│   ├── app/
│   │   ├── main.tsx                # createRoot, QueryClientProvider, RouterProvider
│   │   ├── router.tsx              # nested public routes + protected Admin
│   │   ├── providers.tsx           # Theme, Auth, Query, Toast
│   │   └── query-client.ts         # stale/cache/retry policy
│   ├── api/
│   │   ├── client.ts               # baseURL, JSON, retry/abort, auth headers
│   │   ├── generated/              # generate typed client from OpenAPI when stable
│   │   ├── endpoints/
│   │   │   ├── observations.ts
│   │   │   ├── analytics.ts
│   │   │   ├── clusters.ts
│   │   │   ├── forecasts.ts
│   │   │   ├── models.ts
│   │   │   ├── quality.ts
│   │   │   └── admin.ts
│   │   ├── schemas/                # Zod runtime validation selected contracts
│   │   ├── errors.ts
│   │   └── query-keys.ts
│   ├── components/
│   │   ├── layout/{AppShell,Header,Sidebar,PageHeader,Footer,FilterToolbar}.tsx
│   │   ├── ui/{Card,Button,Select,DateRangePicker,Dialog,Badge,DataTable,Pagination}.tsx
│   │   ├── charts/{TimeSeriesChart,HistogramChart,ScatterChart,CorrelationHeatmap,ClusterRadar,ChartToolbar}.tsx
│   │   ├── data/{MetricCard,FreshnessIndicator,QualityStatus,SourceCaption,ObservationTable}.tsx
│   │   ├── feedback/{LoadingSkeleton,EmptyState,ErrorState,StaleNotice,RetryBanner,JobProgress}.tsx
│   │   └── security/{RequireAdmin,ConfirmGoldDelete}.tsx
│   ├── features/
│   │   ├── overview/{OverviewPage,overview.queries,OverviewStats}.tsx
│   │   ├── observations/{ObservationsPage,ObservationDrawer}.tsx
│   │   ├── time-series/{TimeSeriesPage,granularity-utils}.tsx
│   │   ├── trend-seasonality/{TrendPage,DecompositionCharts}.tsx
│   │   ├── correlations/{CorrelationPage,PairDetail}.tsx
│   │   ├── clustering/{ClusteringPage,ClusterProfiles}.tsx
│   │   ├── forecast/{ForecastPage,PredictionPanel,ClassificationPanel}.tsx
│   │   ├── models/{ModelsPage,ModelComparison,EvaluationTabs}.tsx
│   │   ├── quality/{QualityPage,CoverageCalendar,GapTable}.tsx
│   │   ├── system/{PipelinePage,ServiceStatus,JobTable}.tsx
│   │   └── admin-demo/{DemoControlPage,Preflight,DeletePreview,RebuildJobs}.tsx
│   ├── hooks/{useStation,useDateFilters,useHstFormat,usePagination,useJobPoll}.ts
│   ├── utils/{time,number,units,download,chart-series,quality}.ts
│   ├── types/{api,model,shared}.ts
│   ├── styles/{globals.css,tokens.css}
│   └── tests/{unit,components,contract,e2e}/
├── docs/                            # copy spec docs + decision records
├── nginx/default.conf               # static app container server, optional
├── Dockerfile
├── .env.example
├── index.html
├── package.json
├── tsconfig.json
├── vite.config.ts
└── README.md
```

**Ghi chú:** React source chỉ chạy các HTTP GET/POST có quyền được server cho phép. Không import SDK GCS/admin credentials vào bundle và không viết SQL trong `src/api`.

## 3. Tổ chức component theo chức năng

| Component | Props/State quan trọng | Trách nhiệm |
|---|---|---|
| `AppShell` | active route, role, theme | Header + Sidebar + main outlet + Footer |
| `PageHeader` | title, subtitle, source, last_updated | standardized metadata every screen |
| `FilterToolbar` | metric, range, granularity, timezoneDisplay | sync URL query params; validate range |
| `MetricCard` | label,value,unit,observedAt,qualityStatus | N/A/partial/stale/updated states |
| `TimeSeriesChart` | points[], yUnit, timeBasis, aggregation, gaps[] | line, zoom, export, accessible description |
| `HistogramChart` | bins[], counts[], total | histogram, indicate source method |
| `CorrelationHeatmap` | variables[], matrix[], pairedCount[] | heatmap -1..1, click cell -> PairDetail |
| `ClusterProfiles` | profile[] scaled?, clusterLabels | radar if standardized, grouped bars otherwise |
| `ForecastPanel` | predicted, actual?, model, cutoff, quality | differentiates observed/forecast, guards missing |
| `JobProgress` | jobId,status,progress?,error? | polling via `GET /admin/jobs/{id}` |
| `ConfirmGoldDelete` | dryRunId, scope, rows, confirmation | destructive admin operation guarded |

## 4. URL state và query policies

- Route query sample: `/analytics/time-series?metric=WVHT&start=2026-01-01&end=2026-10-31&aggregation=median`.
- Render filter defaults realistically: end day = latest complete HST day from backend, not Vietnamese calendar day; date range selects local **HST** dates for aggregated charts.
- Query key: `['metrics', stationId, metric, granularity, stat, startHst, endHst]`, `['observations',station,startUTC,endUTC,cursor]`, `['job',jobId]`.
- Suggested refetch (design values, not backend schedule): `latest 60s`, `ingestion status 30s`, `job status 2–5s while active`, `history 5–15min` or invalidated on job success, `model evaluations rarely (manual invalidation)`.
- Loading skeleton on first query, subtle refreshing indicator for background refetch; never replace last valid data with zero while refetching.
- Cancel prior query on rapid date filter change; guard stale results from overwriting current filter.
- For time series high volume: backend aggregated API and bounded raw windows; ECharts large-series support/data sampling to keep <10000 points rendered when appropriate, clearly label downsampling.

## 5. Trạng thái giao diện thống nhất

| State | Cách nhận biết | UX |
|---|---|---|
| `loading` | first request pending | skeleton / aria-busy |
| `refreshing` | refetch pending while old data exists | retain chart + small spinner |
| `ready` | valid data, valid QC | chart and metadata |
| `partial` | missing values or limited coverage | warning banner + gaps / sample_count |
| `stale-source` | last_observed_at older policy | show source age, do not call it live |
| `empty-filter` | 200 [] from a valid range | empty icon + change range CTA |
| `empty-pattern` | Gold deleted/not computed but Silver exists | `Chưa có kết quả phân tích`, admin link rebuild |
| `insufficient-data` | decompositions/ML cannot compute | why (min points/coverage), no fake outputs |
| `model-unavailable` | no ready model / artifact failure | observation pages stay functional; forecast panel error |
| `api-error` | 429/5xx/network | error with safe retry, cached badge if old data |
| `unauthorized` | 401/403 on admin | login/forbidden, not bypass in JS |
| `job-running` | 202/job active | progress & job ID, do not toast success yet |

## 6. UTC/Hawaii: không được xử lý sai

- Stored observed timestamps are UTC. For sensor tooltip show `YYYY-MM-DD HH:mm UTC` and `YYYY-MM-DD HH:mm HST`, both refer to same instant.
- Charts day/week/month use **Hawaii local calendar**. Forecast `target_date_hst=T+1` is relative to available complete day T in HST and model cutoff, not relative to the browser's Vietnamese time.
- Download CSV include explicit timezone in field names, e.g. `observed_at_utc`, `date_hst`, `target_date_hst`; never strip `Z` and re-parse as local time accidentally.
- No DST in `Pacific/Honolulu`, but still use proper timezone conversion library rather than hardcoding `-10` across all calculations in UI; backend authoritative for day grouping.

## 7. Chart metadata/accessibility contract

Every chart has `title`, `metric_code`, `metric_unit`, `source`, `period`, `aggregation`, `sample_count`, `data_quality`, `computed_at`, `data_time_basis`, `model_version` (if ML). Provide downloadable tabular alternative and text summary or table for keyboard/screen reader, not only hover tooltip.

- Color coding consistent, legend selectable; correlation heatmap use diverging colors with `N/A` pattern; cluster colors stable per model version.
- No fake confidence intervals, fake zero for NULL, fake trend extrapolation, or safety flags without clinical/environmental support.
- For print/report: chart title+data range+source+calculation method inside export (not just raw canvas).

## 8. Deployment Frontend Cloud Run

- Frontend repo separate. Build Vite `dist/`, serve via Nginx or static server container with SPA fallback `try_files $uri $uri/ /index.html`.
- `VITE_API_BASE_URL=https://apiwaimea.liems.io.vn/api/v1` is public address (non-secret); never include DB credentials, internal API token, or GCS keys as Vite env (Vite `VITE_*` vars are exposed to browser bundle).
- Set CORS backend allowlist to `https://waimea.liems.io.vn`; handle HTTPS and cookies/session securely if admin auth is cookie based. Public frontend can use read-only APIs.
- Cloud Run domain mapping, custom DNS, build/deploy process should be independently tested and versioned; source frontend repo and backend repo don't need to be a mono-repo.

## 9. Component-level tests

- MetricCard receives `null` -> prints N/A; never 0; source timestamp stale -> warning badge.
- TimeSeries monthly/weekly paths use correct API group_by; no silent conversion of month to UTC dates.
- Correlation heatmap `null` coefficients -> N/A, not color 0; `n_pairs` present.
- Cluster radar disabled when features have different units and backend no standardized centroids.
- Forecast day HST +1 and model version present; no predict when source insufficient.
- Admin routes hidden for viewer **and** protected server-side; forced navigation forbidden.
- 202 POST shows job running; doesn't show success until job GET status success.
- Query cache invalidated after rebuild; charts render again when Gold patterns repopulate.
