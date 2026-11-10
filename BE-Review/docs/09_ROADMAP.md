# 09 — Kế hoạch thực hiện Backend, thứ tự ưu tiên và phân công 5 người

## Phase 0 — Khóa contract và xác minh hạ tầng (P0)

- [ ] Bản SQL v2.0 (25 tables) đã chạy và `03_verify.sql` pass trên Cloud SQL `waimea_wavecast`.
- [ ] Bucket private đã tạo; ghi lại tên thực; test object precondition, generation, SHA và role VM.
- [ ] Chốt `WVHT` target: **median significant wave height ngày Hawaii T+1**; chốt classification thresholds **có giải thích rõ**.
- [ ] Từ NOAA notebook lập test fixtures từng header/year variant (2007/current/realtime), don't fake missing 2005/06.
- [ ] Chốt OpenAPI/JSON contract public để Frontend phát triển mock song song.

**Done:** ER diagram, OpenAPI draft, `.env.example`, infra smoke, schema report, code owners.

## Phase 1 — Database API + NOAA ingestion (P0)

- [ ] Implement DB access, Bronze metadata, batch endpoints, transactions, health/ready.
- [ ] Implement NOAA HTTP client, parser, GCS upload + dedup/idempotent register.
- [ ] Backfill on demand từ 2005 đến hiện tại; poll increment; instrument logs/gaps.
- [ ] Build Postgres integration tests and GCS fault-injection tests.

**Done:** NOAA data real into Bronze and GCS, rerun no duplicate; DB API can read sources.

## Phase 2 — Preprocessing + first charts (P0)

- [ ] Canonical Silver, source priority, revisions, quality flags; accurate UTC/HST.
- [ ] Daily features valid wave count, median, coverage, circular direction.
- [ ] Gold daily/weekly/monthly metrics and API JSON for line/scatter/histogram.
- [ ] Analysis decomposition and correlation with sample checks.

**Done:** Frontend can render 3 time scopes, trend, seasonal, correlation through API; no direct DB.

## Phase 3 — Offline training, evaluation, GCS artifacts (P0)

- [ ] Snapshot dataset Parquet + manifest to GCS, chronological splits/lag 7 + quality gates.
- [ ] Regression baseline and RandomForestRegressor, MAE/RMSE/R² against test.
- [ ] Classification scheme fixed on train + classifier + macro-F1/confusion matrix.
- [ ] K-means training, scale, k selection and centroid audit.
- [ ] Persist dataset_splits/training_runs/model_registry/model_evaluations/evaluation_samples; promote one active model.

**Done:** Model can be loaded by generation/sha from GCS, results can be reproduced, holdout metrics real.

## Phase 4 — Inference services + scheduler (P0)

- [ ] Prediction/classification/cluster assignment and Gold API; latest feature cutoff and no-leakage tests.
- [ ] Worker POST returns 202, job ID/status; scheduler poll 30 min, job locks and retries.
- [ ] Idempotent Gold writes and reconciliation actual labels/values when new NOAA data arrives.

**Done:** `DELETE` scoped Gold pattern + API regenerate, zero training jobs triggered.

## Phase 5 — Deployment, security, demo and reports (P0)

- [ ] Multi-container docker build, Nginx TLS, Cloud SQL proxy IAM/permissions, private GCS.
- [ ] Monitoring dashboards/logs, cert renewal, backups, crash/recovery test.
- [ ] End-to-end slide evidence: Docker up, F12 API, delete pattern, 202 job, recover results, new NOAA poll.
- [ ] System architecture diagram + network diagram + data flow + ML algorithm/evaluation explanation.

**Done:** Deployment reproducible from repo and runbook with redacted secrets, demo rehearsed.

## 5-member parallel work allocation (đề xuất, có thể đổi)

| Thành viên | Scope chính | Phối hợp |
|---|---|---|
| A — Data infrastructure | Cloud SQL migration, DB API, GCS auth, Nginx/Compose | API design & security |
| B — Ingestion / Data Quality | NOAA HTTP, parser, backfill, incremental, QC Bronze/Silver | A, C |
| C — Analysis / Gold | daily features, day/week/month, trend/seasonal, correlation, API semantics | B, D |
| D — ML | feature splits, regression, classification, K-means, evaluation, GCS artifacts | C, E |
| E — Orchestration/QA/API frontend integration | scheduler, job idempotency, contract tests, dashboard wiring, demo pipeline | Everyone |

## Definition of Done cho mọi Pull Request

- Code chạy được + test, không phải chỉ notebook screenshot.
- Tên bảng/cột map SQL v2.0 đã được review, không tạo bảng tùy ý.
- Type hints/Pydantic, input validation, xử lý null/timezone, logs, retry có giới hạn.
- Có API contract/README usage cập nhật, migration tương thích và rollback plan.
- Không commit `.env`, API keys, service account JSON, model lớn chưa cần thiết.
- CI pass: `ruff`, `pytest`, DB integration, Compose config, Nginx config; test end-to-end nếu PR chạm data flow.
- Nhánh `main` có migration version và release tag. Model release có dataset snapshot hash + code commit.

## Open decisions cần chốt trước khi viết production code

1. Tên bucket thực tế và Cloud SQL instance connection name.
2. Cadence NOAA theo các giai đoạn và quy tắc tính expected_sample_count (không lấy 48 cứng).
3. Quality gate cho daily features (min samples, accepted qc states, missing policy).
4. Target prediction là daily **median WVHT**, không phải maximum 30-min hay next observation.
5. Definition classification scheme; có được phép gọi "sóng nguy hiểm" hay chỉ mức thống kê.
6. Cách chịu trách nhiệm DB API: strict all service DB via internal API (đề xuất) hay optimize bulk streaming; cần chốt contract để tránh 7 kết nối SQL tự phát.
7. Model promotion policy và nếu cần partial unique index cho active model.
8. Quy tắc lưu lịch sử forecast issuance khi demo (cần hạn chế destructive delete trên prod).
9. Frontend API shape và filter limits; deployment TLS/ACME challenge.
10. Số CPU/RAM VM thực tế, giới hạn memory/train schedule trước khi chỉnh worker count.
