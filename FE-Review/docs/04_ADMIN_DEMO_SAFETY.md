# 04 — Admin Demo: xóa kết quả pattern và khôi phục qua API, an toàn với NOAA & model

## 1. Chính xác thầy yêu cầu gì?

Theo bản tổng hợp slide tuần 9, demo cần dựng Docker, mở web/F12 kiểm tra API, **xóa bằng tay các kết quả pattern trong Database**, web mất kết quả, gọi API xử lý để tạo lại (dùng mô hình đã build sẵn, không train lại), kiểm chứng DB/web và chứng minh cập nhật. **Không có yêu cầu bắt buộc tạo nút xóa toàn bộ Database trên Frontend.**

## 2. Quy tắc an toàn tuyệt đối

**Không bao giờ có nút `XÓA DATABASE`, `DROP SCHEMA`, `TRUNCATE gold.* CASCADE`, `XÓA BRONZE/SILVER`, `RESET GCS`.** Giao diện Admin có thể hỗ trợ "Xóa kết quả Gold trong phạm vi demo, nhưng **không cho thao tác tùy ý câu SQL** và phải có authorization server-side. Nghiêm cấm thao tác với production thật nếu chưa có bản sao/snapshot và quy trình khôi phục.

### Bảng bảo tồn

| Được phép xóa chọn lọc theo phạm vi staging | PHẢI GIỮ |
|---|---|
| `gold.time_series_metrics` | Toàn bộ `bronze.*`, `silver.*` |
| `gold.trend_seasonal_components` | `gold.model_registry` và artifact `.joblib` trên GCS |
| `gold.correlations` | `gold.dataset_splits`, `gold.training_runs` |
| `gold.cluster_assignments` | `gold.model_evaluations`, `gold.evaluation_samples` |
| `gold.predictions` (chỉ issuance demo) | `gold.cluster_centroids`, `gold.classification_schemes` |
| `gold.classifications` (chỉ issuance demo) | GCS `noaa/raw`, `datasets`, `models` |

Mỗi bảng thực tế phải được chọn riêng, theo station 51201 và date_hst/target_date_hst phạm vi. `gold.predictions` có nhiều `issued_at` cho một ngày; không xóa lịch sử dự báo production nếu muốn đánh giá drift. `gold.processing_runs` có FKs: mặc định giữ.

## 3. Ba phương án demo và khuyến nghị

### A. Đáp ứng đúng lời thầy — xóa thủ công DBeaver (khuyến nghị)

1. Admin mở Demo Control **chế độ read-only** và chụp counts trước.
2. Trong DBeaver trên **bản staging/sandbox đã backup**, giảng viên hoặc nhóm chạy SQL `DELETE FROM` có điều kiện theo phạm vi Gold **sau khi xem `SELECT COUNT(*)`**, không xóa toàn DB.
3. Quay lại web: Gold pattern trống; Bronze/Silver và model metadata không đổi.
4. Trong Admin UI nhấn `Chạy lại Analysis`, `Chạy lại K-means Assignment`, `Chạy lại Prediction`, `Chạy lại Classification` (gọi API workers qua facade).
5. UI hiển thị `202 Accepted / job_id`; kiểm tra hoàn thành, counts và refresh chart.

### B. Nút xóa pattern có guardrails — tùy chọn, KHÔNG thay thế A nếu thầy yêu cầu bằng tay

1. Chọn `Pattern types`, `station_id=51201`, `date_from`, `date_to`. Giới hạn time window cho demo (ví dụ tối đa 30 ngày) **chỉ là policy nhóm**, không tự mở all years.
2. `POST /admin/demo/patterns/preview-delete` trả `dry_run_id`, `affected_tables`, `row_count`, `protected_tables`, expiry, `safe_to_delete`. Backend reject nếu scope sai hoặc prod.
3. Confirmation dialog hiển thị các bảng/row count, phải nhập `DELETE GOLD DEMO` và nhấn xác nhận. **Không dùng chỉ confirm() JavaScript đơn giản.**
4. Backend xác thực người dùng + session + CSRF (nếu cookie), rate-limit, môi trường staging, kiểm tra one-time dry-run token, audit who/what/when/rows.
5. Thực hiện transaction DELETE theo thứ tự FK với server-side allowlist; trả job ID/audit ID, không cho FE gửi table name tùy ý.
6. Frontend invalidate query keys liên quan, hiện empty state các pattern, không động Bronze/Silver/GCS.
7. Nhấn `Tái tạo Gold`, backend điều phối internal worker jobs, trả progress và counts, đối chiếu không tăng training_runs.

### C. Không có Admin UI nút xóa — chỉ Panel kiểm thử

Nếu không đủ thời gian hoặc thầy thực sự chỉ muốn xem DBeaver, chỉ làm panel `Trạng thái kết quả` + `Chạy lại từng pattern` + `Xem jobs` + `Giải thích quy trình`. Cách này đủ trình diễn, ít rủi ro hơn.

## 4. Thiết kế giao diện thao tác

```text
┌── DEMO CONTROL (ADMIN ONLY) ──────────────────────────────────────────────────────┐
│ Scope: Station 51201    From [2026-10-01]    To [2026-10-07]   [Check status]     │
│ Preconditions:   [✓ Staging] [✓ DB connected] [✓ Model ready] [✓ GCS verified] │
├───────────────────────────────────────────────────────────────────────────────────┤
│ PATTERNS TO REBUILD:                                                               │
│ [x] Time series  [x] Trend/Seasonal  [x] Correlations  [x] Clustering           │
│ [x] Regression   [x] Classification                                             │
│ [Preview affected rows]  [Delete scoped Gold for DEMO] (disabled until safe)      │
├───────────────────────────────────────────────────────────────────────────────────┤
│ REBUILD: [Run Analysis] [Assign Clusters] [Run Regression] [Run Classification]  │
│ JOB: 2345 [RUNNING]  completed 2/4 modules  |  [View log] [Refresh patterns]     │
│ CHECKSUM: bronze rows SAME | silver rows SAME | model GCS SAME | train runs SAME │
└───────────────────────────────────────────────────────────────────────────────────┘
```

## 5. API admin facade phải xây mới

| Endpoint (all `/api/v1`) | Input/Output | An toàn |
|---|---|---|
| `GET /admin/demo/preflight` | env, ready-model, backups, count summaries, allow_delete | admin only, no secrets |
| `POST /admin/demo/patterns/preview-delete` | `{station_id,from_hst,to_hst,pattern_types}` → dry_run token/counts | read-only, allowlist |
| `POST /admin/demo/patterns/delete` | dry_run token + confirmation → job/audit | staging guard, CSRF, 2-step |
| `POST /admin/demo/patterns/rebuild` | patterns+range, `retrain:false` → 202 jobs | no training endpoint |
| `GET /admin/jobs/{job_id}` | status/progress/counts/errors | admin scope |
| `GET /admin/demo/audit` | when/what/who/rows, request IDs | audit read-only |

**Backend Blueprint cũ chỉ có các internal worker trigger**; những endpoint `admin/*` **chưa có**. Không bật nút trong FE cho đến khi Backend hoàn thành, test bảo mật, có môi trường staging và migration phù hợp.

## 6. Job UX state machine

```text
Not started -> Requesting (POST) -> Accepted (202) -> Running -> Success
                                              |                  -> Partial
                                              |                  -> Failed
                                              -> Conflict (409; attach running job)
```

- Sau POST, hiển thị **job_id** (không hiển thị Toast “thành công” chỉ vì 202).
- Poll GET status mỗi 2–5 giây **chỉ khi job active**, dừng khi terminal status; hỗ trợ browser reload bằng job_id trong route (không lưu token trong query).
- Không hiển thị progress % nếu Backend chưa trả `progress_completed/total`; thay bằng trạng thái `Đang xử lý...` và timestamps.
- Sau success: invalidate affected query keys rồi refetch; kiểm tra counts sau > 0 hoặc logic no-data hợp lệ.
- Sau failure: giữ state đã xóa để thể hiện lỗi; nút retry an toàn với idempotency key, không tự xóa/training lại.

## 7. Audit / quyền và chứng cứ

- Viewer: không thấy Admin sidebar và Backend kiểm tra unauthorized request; 403/401 trên mọi POST admin.
- Admin demo: chỉ environment allowlisted, phạm vi ngày/trạm hạn chế, không được truyền tên bảng tự do.
- Audit: `actor_id`, `timestamp_utc`, `request_id`, `operation`, `scope`, `table_counts_before/after`, `dry_run_id`, `result`, `job_ids`; hiện SQL v2 chưa có bảng `admin_audit`, có thể ghi Cloud Logging với retention hoặc thêm migration tạo audit table chuyên biệt.
- Sau demo kiểm tra `bronze.source_files`, `silver.wave_observations`, `gold.training_runs`, GCS checksum không thay đổi.
- Dùng demo staging/isolated dataset; dữ liệu trong production không có chức năng tự xóa từ website công khai.
