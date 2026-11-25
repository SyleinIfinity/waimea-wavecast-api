# TL05 — Backend Analytics / ML status

**Ngày cập nhật:** 12/11/2026  
**Nhánh:** `feature/tl05-be-ml-analysis`  
**Tình trạng:** triển khai độc lập bằng fake gateway; chưa nghiệm thu cloud/e2e.

| Task | Trạng thái | Bằng chứng | Còn thiếu |
|---|---|---|---|
| TL05-01 Gold analytics | DONE độc lập | `tests/tl05/test_tl05.py`: HST aggregation, circular direction, correlation undefined, rebuild không train | Chạy I-02 với Silver NOAA thật |
| TL05-02 ML và scheduler | DONE độc lập | Chronological split, persistence baseline, RF regression/classification, K-means, Parquet snapshot, immutable artifact, inference, auth jobs, scheduler | GCS thực, model review/promotion, TL04 poll còn fake DB |
| TL05-03 Test và bàn giao | PARTIAL | Test fake artifact/hash/version, inference không tăng training run, worker 401/422/202 | Docker build/run và I-02 trên staging |

**Kiểm chứng SQL:** payload Gold metrics/trend/correlations đã thử trên PostgreSQL 17 bằng giao dịch `ROLLBACK`, đạt. Tại thời điểm kiểm tra, `silver.daily_wave_features=0` và `gold.model_registry=0`; không có dữ liệu NOAA daily hay model đã duyệt để train/infer thật.

**Kiểm thử hiện tại:** `python -m pytest -q -p no:cacheprovider` → 119 PASS, 2 SKIPPED (hai kiểm thử DB của TL01 cần biến môi trường); `pip check` và `docker compose config -q` PASS. Docker daemon không chạy nên chưa build image.

## Hợp đồng tích hợp

- Workers dùng `X-Internal-Token` và `Idempotency-Key`; TL01 nhận Gold/model/evaluation/job qua `/internal/v1`.
- Model registry dùng `draft/ready/retired`; worker chỉ suy luận từ model `ready` có generation và SHA-256.
- Dataset test là synthetic fixture và không được công bố như benchmark NOAA.
- Scheduler chạy một replica và chỉ bật bằng Compose profile `tl05-scheduler`; TL04 poll hiện vẫn dùng `FakeDatabaseGateway`, vì vậy chưa có pipeline NOAA → Silver thật.
- Không chạy xóa Gold hoặc train model trên database đang dùng trong kiểm thử này.

**HTTP TL05 → TL01:** adapter đọc `/internal/v1/silver/daily-features`, `/internal/v1/silver/observations` và `/api/v1/models` qua server localhost với token thật: 200 PASS; hiện trả 0 daily, 0 observations và chưa có model `ready`.

**Idempotency TL01:** adapter nhận biết phản hồi job đã tồn tại và không chạy nền lần hai với cùng key.
