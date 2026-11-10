# 02 — Screen specification: từng trang, từng vùng, chart, nút, trạng thái

**Quy ước:** Mọi số giá trị dưới đây là cấu trúc/giao diện đề xuất, không phải số liệu NOAA thực. Trong dữ liệu production, số liệu đến từ API, không hard-code. Độ cao sóng theo `WVHT` (significant wave height) không đồng nghĩa với sóng cao nhất. Dự đoán ngày HST T+1 là mô hình của nhóm, không phải dự báo NOAA.

## S01 — Overview Dashboard `/overview` (P0)

**Câu hỏi đáp ứng:** Waimea Bay hiện ghi nhận gì, có số liệu mới đến thời điểm nào, mô hình dự đoán ngày mai ra sao, hệ thống có đáng tin không?

| Vùng | Nội dung cụ thể | Hành vi/đầu vào | Dữ liệu/API |
|---|---|---|---|
| Hero/banner | “Waimea Bay — Ocean Wave Observatory” + Station 51201, tọa độ, NOAA source | link Source Details | `GET /stations/51201` |
| Freshness strip | `Latest observed`, `Last NOAA check`, `Last Gold processing`, stale/available badges | tooltip UTC/HST/latency; click Monitor nếu admin | observation latest + **API freshness mới cần bổ sung** |
| 4 KPI cards | `WVHT m`, `DPD s`, `MWD °`, `WTMP °C` nếu có; `N/A` nếu thiếu | click card -> Observations filtered | `GET /observations/latest` |
| Main history card | Line chart wave height gần 72h/7d, valid samples & gaps | 24h/72h/7d; hover; zoom; reset; CSV export | `GET /observations` |
| Forecast card | Dự đoán **median WVHT của ngày HST tiếp theo**, model version, cutoff, issued_at | `Xem chi tiết` (không tự chạy inference) | `GET /predictions/latest` |
| Classification card | Nhãn sóng ngày mai, `scheme_version`, class rules | `Tìm hiểu ngưỡng` | `GET /classifications/latest`, `/classifications/schemes` |
| Data quality card | missing ratio/coverage, số gaps, recent anomalies | link `/data-quality` | `GET /data-quality/overview` |
| Quick charts | 2 preview: daily trend last 30d và các nhóm K-means gần đây | click mở Analytics/Clusters | `metrics/summary`, `clusters` |
| Footnote | “Source NOAA NDBC; model predictions educational, not safety guidance” | readonly | static + metadata |

**Nút:** `[24 giờ] [72 giờ] [7 ngày]`, `[Làm mới giao diện]`, `[Phóng to]`, `[Xuất biểu đồ PNG]`, `[Xem dữ liệu]`. **Không có** nút nhập dữ liệu NOAA/xóa Gold ở public Overview.

**UI khi chưa có model:** Hiển thị quan trắc thực tế bình thường; forecast card thông báo `Chưa có mô hình sẵn sàng`, không thay bằng số giả. Nếu NOAA cũ: `Quan trắc gần nhất: ...`, không dùng chữ `LIVE` vô điều kiện.

## S02 — Observations `/observations` (P0)

**Vùng:** Toolbar cố định Station 51201, ngày bắt đầu/kết thúc UTC/HST, chọn các biến `WVHT`, `DPD`, `APD`, `MWD`, `WTMP`, `WSPD`; nút `Áp dụng`, `Đặt lại`, `Tải CSV`; checkbox hiện điểm thiếu/mã chất lượng.

**Chart chính:** Line series multi-select, **trục y riêng** cho biến khác đơn vị (hoặc chỉ một metric mỗi lúc để tránh đánh lừa); có Brush/Zoom; đánh dấu khoảng gap, hover timestamp UTC/HST và raw vs normalized distinction. Nếu chọn hướng MWD, dùng circular/directional display, không nối qua 359°→1° theo đường thẳng gây hiểu nhầm.

**Data table:** `Observed At (UTC)`, `Observed At (HST)`, `WVHT`, `DPD`, `APD`, `MWD`, `WTMP`, `Source kind`, `Quality`, `Revision`; phân trang server, sort thời gian desc, click row mở **Observation Details drawer**: các cột hợp lệ/missing_fields, source_file_id, revision_no, raw provenance (không trực tiếp file riêng tư GCS). Chỉ hiện các cột thực sự có trong response.

**Nút:** `Chọn biến`, `Thu phóng`, `Đặt lại`, `Xuất CSV`, `Sao chép timestamp`, `Xem QC`; **Làm mới** chỉ GET.

**API:** `GET /observations/latest`, `GET /observations`, `GET /data-quality/overview`; **chưa có** public `GET /observations/{station}/{timestamp}/revisions` và `GET /source-files/{id}/metadata`: đề nghị bổ sung nếu triển khai drawer provenance. Không expose raw GCS signed URL tùy tiện.

## S03 — Time Series Analytics `/analytics/time-series` (P0, bắt buộc từ slide)

**Vùng chung:** Title `Time-Series Analysis`, metric selector (mặc định WVHT), date range theo Hawaii, toggle `mean/median/max/p95`, nút `Refresh`, `Export` và phần giải thích aggregation.

**Ba chart riêng để thầy thấy rõ:**

1. **Daily Wave Height:** line chart theo từng ngày HST, x-axis ISO date, y-axis mét; tooltip count, valid_count, period start.
2. **Weekly Wave Height:** line/bar theo tuần bắt đầu thứ Hai HST; cùng metric/aggregation, gắn chú giải “Week starts Monday HST”.
3. **Monthly Wave Height:** line/bar theo tháng, so sánh nhiều năm/zoom chọn năm; không dùng 1 giá trị tháng làm quan trắc thời điểm thực.

**Biểu đồ bổ sung theo yêu cầu:** một **Histogram** phân phối WVHT theo khoảng, một **Scatter** WVHT vs DPD (paired measurements), có chế độ chọn cặp biến; do đó không hiểu sai là yêu cầu 9 biểu đồ (mỗi thời gian đủ line/scatter/hist). Biểu đồ Daily/Weekly/Monthly là **ba khu riêng**, trên desktop xếp Daily full-width, Weekly/Monthly cạnh nhau; mobile xếp dọc.

**KPI nhỏ:** `Records`, `Valid samples`, `Min/Max`, `Median`, `P95`, `Missing rate`, `coverage` nếu có. Data contract phải định nghĩa thống kê trên quan trắc hay trên daily aggregate (một nguồn số chính, không trộn kiểu tính).

**API:** `GET /metrics/summary?metric=WVHT&group_by=day|week|month&start_hst=...&end_hst=...`, `GET /analytics/histogram`, `GET /analytics/scatter` (đã nằm trong Blueprint, cần code).

## S04 — Trend & Seasonality `/analytics/trend-seasonality` (P0)

**Vùng:** metric WVHT mặc định; granularity mặc định month; range dài (cần ≥2 chu kỳ đầy đủ theo method nếu seasonal period=12 tháng), method selector chỉ hiện các method/backend đã thực hiện.

**4 biểu đồ trên cùng miền thời gian:**

- `Observed` (chuỗi gốc resampled); `Trend` (xu hướng dài hạn); `Seasonal` (biến thiên lặp theo kỳ); `Residual` (phần dư).
- Các biểu đồ share x-axis/time-zoom và dùng cùng sampling; chú thích decomposition version và kỳ seasonal (ví dụ 12 tháng cho monthly data, nếu dữ liệu đủ).

**Controls:** `Theo tháng/tuần` (chỉ cho phép backend hỗ trợ), `Metric`, `Phạm vi`, `Hiện/ẩn Residual`, `Export PNG`, `Giải thích phương pháp`.

**Warning:** Khi không đủ kỳ seasonal, trả `insufficient_data` với lý do rõ; không tạo seasonal phẳng =0 để cho đẹp. Không gọi trend là dự báo tương lai.

**API:** `GET /analytics/trend-seasonal`, `GET /metrics/summary` và method metadata bổ sung trong response; Gold `trend_seasonal_components`.

## S05 — Correlation Explorer `/analytics/correlations` (P0)

**Vùng:** date window HST, phương pháp Pearson/Spearman, dropdown variables (chỉ field có dữ liệu đủ và có ý nghĩa), min paired samples, `Tính lại` (đọc stored Gold hoặc trigger job admin riêng; viewer chỉ GET).

**Heatmap:** Ma trận đối xứng -1..1; màu hai phía, ô hiện hệ số và `n_pairs` khi hover; constant series hay sample_count quá ít -> ô `N/A`. Click ô `WVHT–DPD` mở detail bên phải: Scatter x/y + trendline tùy chọn (trendline phải có giải thích; correlation != causation).

**Controls:** `Pearson / Spearman`, `Chọn biến`, `Khoảng ngày`, `Xem cặp biến`, `Xuất CSV/PNG`, `Hiển thị số mẫu`.

**API:** `/analytics/correlations`, `/analytics/scatter`, `/data-quality/overview`. **Cần Backend** trả `paired_sample_count`, missing reason/undefined cases; không cần lưu ảnh heatmap trong DB.

## S06 — Clustering `/clustering` (P0)

**Vùng:** Model version/algorithm (`K-means`), số cụm `k`, feature set, last trained, silhouette/DBI, và giải thích mỗi cluster.

**Đồ thị:**

1. **Timeline/Calendar Heatmap:** mỗi ngày HST được tô màu theo cluster_id; hover ngày/centroid/quality, click sang ngày ở Observations.
2. **Cluster distribution:** horizontal bar/stacked: số ngày theo cluster, filter năm; không gọi “tỉ lệ dự đoán” nếu chỉ là số ngày lịch sử.
3. **Centroid Profile Radar hoặc grouped bars:** feature tiêu biểu **đã chuẩn hóa** (z-score theo scaler model) mới so sánh tương đối được; nếu API chỉ có feature nguyên đơn vị, dùng grouped bar từng metric để tránh radar nhiều đơn vị.
4. (Optional) Scatter 2D projection PCA/t-SNE với disclaimer projection; **chưa có API/projection trong backend**, không mặc định sẵn.

**Controls:** `Model version`, `Year`, `Cluster`, `Hiện centroid`, `Xuất kết quả`. Nhãn cluster 0/1/2 chỉ là ID vô hướng; hiển thị diễn giải sau hậu kiểm, không gọi tự động “mùa hè” chỉ vì màu.

**API:** `GET /clusters` (assignments), `GET /models` (registry), `GET /models/{id}/evaluations`; **thiếu** centroid profile API `GET /clusters/profiles` nếu backend trả assignments riêng.

## S07 — Forecast & Classification `/forecast` (P0)

**Hai panel rõ ràng để thầy phân biệt hồi quy/phân loại**:

**Panel A — Regression Forecast:**
- `Target: median WVHT of next Hawaii day`, con số dự đoán **mét**, `target_date_hst`, `feature_cutoff_date_hst`, `issued_at`, `model_name/version`.
- Line Actual vs Predicted theo ngày (lịch sử predictions), marks forecast chưa actual, toggle `Show errors`, tooltip `actual_value`, `absolute_error`.
- Metrics mini: MAE, RMSE, R² của test set và baseline, kèm `n_test`, dataset version; không trộn test metrics với online errors.
- Uncertainty band **chỉ** nếu backend có model/calibrated bounds; không tự vẽ ±10%.

**Panel B — Classification:**
- Nhãn trạng thái sóng ngày mai (ví dụ `low/medium/high` **nếu scheme đã được duyệt**), scheme version & ngưỡng, issued_at.
- Probability bars chỉ nếu model thực sự trả class_probabilities; ghi chú uncalibrated confidence khi chưa calibration.
- Link xem confusion matrix/metrics trong Model Evaluation.

**Controls:** `Xem 7/30/90 ngày`, `Chọn model version` nếu các bản forecast lưu nhiều model, `Xem ngưỡng`, `Xem đánh giá`; **không có nút train hoặc chạy lại inference cho public**.

**API:** `/predictions/latest`, `/predictions/history`, `/classifications/latest`, `/classifications/schemes`, `/models/{id}/evaluations`; **thiếu public classifications history** nếu muốn vẽ accuracy theo thời gian.

**Cảnh báo:** `Forecast next day` phải dựa trên ngày HST T **đã đủ dữ liệu và cutoff hợp lệ**; nếu NOAA chậm, hiển thị “chưa thể tạo dự báo mới” chứ không suy luận bằng ngày chưa đầy đủ rồi không thông báo.

## S08 — Model Evaluation / Model Lab `/models` (P1, quan trọng cho báo cáo)

**Vùng:** Filter Task type Regression/Classification/Clustering, model version, status ready/draft/retired theo SQL v2, training period, dataset version, evaluated_at, feature list, algorithm, param snippets, n_train/n_val/n_test.

**Các tab:**

- `Compare models`: table model vs baseline, MAE/RMSE/R²; macro F1/accuracy; Silhouette/DBI tùy task. Chỉ so trên dataset/evaluation stage cùng điều kiện.
- `Regression`: chart scatter y_true vs y_pred (held-out samples), residual distribution vs target; MAE table.
- `Classification`: confusion matrix (đã lưu JSONB), class precision/recall/F1; hiển thị support từng nhãn.
- `Clustering`: k selection diagnostics, centroids, model fit parameters.
- `Lineage`: dataset snapshot metadata, GCS URI **chỉ hiển thị cho admin và không cung cấp signed download mặc định**, code version và model version.

**Nút:** `So sánh`, `Chọn model`, `Xem features`, `Xuất bảng đánh giá` (viewer); `Phê duyệt model` chỉ admin ML, không ở trang công khai.

**API:** `/models`, `/models/{model_id}/evaluations`; **cần bổ sung** `/models/{model_id}/evaluation-samples` (pagination, no raw leak) và `/models/{model_id}/details` cho feature/split/training metadata nếu hiện endpoint list chưa đủ.

## S09 — Data Quality & Provenance `/data-quality` (P1)

**Vùng:** source availability banner, coverage theo năm, missing counts của từng trường NOAA, gaps theo ngày/năm, mới nhất fetch/observation, input parsers và bản hiệu chỉnh.

**Charts/table:**
- Heatmap `Years × Months` hiển thị số dòng/coverage; 2005–2006 không có thì `unavailable/no data`, không thay bằng zero-wave.
- Bar missing % theo field NOAA (đúng denominator, chỉ đếm trên thời kỳ có dữ liệu thật).
- Table `quality issues`: severities, field, observed_at, source_file, status; table `gaps` start/end, estimated missing; metadata/link không cho truy cập bucket trực tiếp.
- Source lineage panel: `NOAA 51201 → source file hashed/GCS → Silver revision → Gold processing job → model version`.

**Controls:** `Chọn năm`, `Lọc severity`, `Chọn field`, `Xuất báo cáo`, `Xem phiên bản đã cập nhật`.

**API:** `/data-quality/overview`; **thiếu** `/data-quality/gaps`, `/data-quality/issues`, `/data-quality/coverage`, `/source-files/{id}` nếu muốn detail, phải thiết kế thêm.

## S10 — Pipeline Monitor `/system` (ADMIN P1)

**Mục đích:** chứng minh services tách biệt, theo dõi ingestion/processing/update mà không lộ secrets.

**Vùng:**
- Pipeline DAG tiles: NOAA → Ingestion → GCS/Bronze → Preprocessing → Silver → Analysis/Clustering/Prediction/Classification → Gold → API → Frontend.
- Mỗi tile: `last_run_at`, `status`, `duration`, `affected_rows`, `errors`, source `observed_at`, last successful job ID.
- Job activity table: job ID, service, type, range, queued/running/succeeded/failed, duration, summary counts, retry CTA.
- Storage/capacity panel: file count/raw byte totals, Cloud SQL connection health, GCS object status; backend chỉ trả aggregated metadata, không truyền credentials.

**Controls:** `Làm mới trạng thái`, `Lọc service`, `Xem log`, `Thử lại job thất bại` (admin protected), `Chạy thu thập NOAA` (admin protected). Nút `Làm mới trạng thái` chỉ GET, không poll NOAA.

**API:** **Cần bổ sung** `/admin/system/status`, `/admin/ingestion-runs`, `/admin/jobs`, `/admin/jobs/{id}`, `/admin/jobs/{id}/retry`; nội bộ worker API đã được phác thảo nhưng chưa nên expose trực tiếp ra public gateway.

## S11 — Demo Control `/admin/demo` (ADMIN P0 cho demo cuối kỳ)

**Bố cục đề xuất:**

- Left: **Checklist bảo vệ dữ liệu** (Cloud SQL staging? backup? model ready? GCS ready? auth? selected scope), các dòng `bronze`, `silver`, `model_registry`, `training_runs`, `evaluations`, `GCS` gắn icon **PRESERVE**.
- Middle: chọn các pattern được phép xóa **chỉ trong Gold** (`metrics`, `trend/seasonal`, `correlations`, `cluster assignments`, `predictions`, `classifications`), time range/station, preview rows affected.
- Right: **Job progress** và trạng thái pattern có/không; danh sách endpoint rebuild tương ứng, button `Chạy lại xử lý`, `Xem lại Dashboard`.
- Bottom: log chứng cứ demo `before_count`, `deleted_count`, `after_rebuild_count`, `training_run_count_before/after`, timestamps, error details, audit event id.

**Nút và cấp quyền:** `Kiểm tra điều kiện`, `Xem trước phạm vi xóa` → (xác nhận 2 bước admin) `Xóa kết quả Gold (demo)` → `Chạy lại Analysis` / `Chạy lại Clustering` / `Chạy lại Prediction` / `Chạy lại Classification` → `Làm mới biểu đồ`. **Không có `Xóa DB`**.

**Chế độ demo chuẩn khi thầy yêu cầu xóa bằng tay:** mô tả SQL cần chạy trong DBeaver trên **staging**, dùng UI read-only hiển thị counts trước/sau, rồi bấm `Tái tạo kết quả` qua API. Nút xóa trên UI là tùy chọn hỗ trợ, không phải yêu cầu gốc từ slide.

Chi tiết authorization, confirmation, job state machine và endpoint missing xem `04_ADMIN_DEMO_SAFETY.md`.
