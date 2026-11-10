# TL05 — BACKEND 2: ANALYSIS & MACHINE LEARNING

**Người phụ trách:** Thành viên 5 — Backend 2. **Repo:** `waimea-wavecast-backend`. **Nhánh:** `feature/tl05-be-ml-analysis`.

**Đọc:** Backend Blueprint `02_IMPLEMENTATION_BACKLOG.md` phần analysis, clustering, prediction, classification, training, scheduler; `04_MACHINE_LEARNING.md`, `05_API_CONTRACT.md`, `08_TESTING_DEMO.md`; DB v2.0 `gold.*` dictionary.

**Độc lập với TL01/TL04:** dùng fixture `daily_wave_features` theo HST từ CSV/Parquet test; đọc/ghi qua `FakeDatabaseGateway`; GCS model adapter fake/local filesystem trong test. Không cần chờ NOAA ingestion hoặc DB API có dữ liệu thật.

## TL05-01 — Analysis (Gold patterns)

- [ ] Code `apps/analysis/app/{aggregation,decomposition,correlation}.py`: day/week/month với HST, thống kê mean/median/max/p95; histogram/scatter (tính bounded); trend/seasonal/residual khi đủ chu kỳ; Pearson/Spearman với paired_count, missing case.
- [ ] Save-to-Gold qua DatabaseGateway interface (không viết SQL trực tiếp); worker `POST /internal/v1/analysis/rebuild`; tách bước tính và bước lưu để mock test.
- [ ] Test: dữ liệu thiếu, irregular cadence, direction circular, series không đủ mùa vụ, correlation undefined.

## TL05-02 — ML & scheduler

- [ ] Code `training/{build_dataset,train_regression,train_classification,train_clustering,evaluate}.py`; target đề xuất **daily median WVHT(T+1)**, lag T−6..T; train/validation/test theo thời gian, không leakage; baseline vs RandomForestRegressor; MAE/RMSE/R².
- [ ] Code classification scheme versioned (không gắn nhãn “an toàn” tùy tiện), confusion matrix/F1; K-means + scaler, chọn k và lưu centroid/evaluation.
- [ ] Code `packages/ml/{features,artifacts,metrics}.py`, `packages/gcs/models/` để lưu dataset/model version bất biến, manifest, hash; `apps/{prediction,classification,clustering}/` chỉ inference bằng model đã train, không fit trên request.
- [ ] Code `apps/scheduler/`: orchestration tối thiểu chạy poll/rebuild/infer qua internal HTTP theo idempotency key và job_id; lịch poll NOAA do TL04 định nghĩa, không nhúng NOAA client ở scheduler.
- [ ] Mỗi worker có Dockerfile riêng, root Compose do TL01 ghép; training CLI chạy one-off, không expose public.

## TL05-03 — Test và bàn giao

- [ ] `pytest` dataset: T+1 tuyệt đối không xuất hiện trong X_T; split thời gian; feature order; GCS model manifest/hash; artifact v2 không ghi đè v1.
- [ ] Demo độc lập: fake Silver → Gold statistics; train mô hình **trên fixture chỉ để test** → lưu local artifact → inference và evaluation → fake Gold output; không báo metrics fixture như kết quả NOAA thật.
- [ ] Test xóa kết quả pattern giả lập → rebuild không gọi train; tạo `docs/contributions/TL05_STATUS.md`, `docs/contributions/TL05_FUNCTIONS.md`.

**Bàn giao:** analysis, 3 job ML + evaluation, inference services, scheduler, artifact adapter, tests, Dockerfiles, hai báo cáo.

**Nghiệm thu TL05:** 3 pattern Gold và forecast/class/cluster có thể tạo từ fixture; có baseline + held-out metrics; inference tái chạy không thêm training run; không phụ thuộc TL01/TL04 để unit/integration fake.

**Không sở hữu:** Database API, NOAA parser/ingestion, Frontend.
