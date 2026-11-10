# TL04 — BACKEND 1: NOAA INGESTION → BRONZE/SILVER

**Người phụ trách:** Thành viên 4 — Backend 1. **Repo:** `waimea-wavecast-backend`. **Nhánh:** `feature/tl04-be-ingestion`.

**Đọc:** Backend Blueprint `02_IMPLEMENTATION_BACKLOG.md` phần shared/ingestion/preprocessing, `03_NOAA_PIPELINE_QUALITY.md`, `06_DATABASE_GCS_MAPPING.md`; Notebook NOAA 51201 là reference để kiểm tra header và URL (không copy nguyên notebook thành service).

**Độc lập với Database API:** code `DatabaseGateway` protocol + `FakeDatabaseGateway` lưu in-memory; hợp đồng dữ liệu ở `00_HOP_DONG_CHUNG.md`. Không sửa `apps/database_api`, không chờ Cloud SQL/GCS thật để unit test.

## TL04-01 — NOAA parser và đầu vào

- [ ] Code `packages/noaa/{client,urls,parser,normalize,hash,priority}.py`: annual/monthly/realtime; timeout/retry; parse `#YY`/`#YYYY`, alias BAR/PRES, 14 NOAA fields; sentinel → NULL **theo từng field**; utc timestamps.
- [ ] Code `apps/ingestion/app/jobs/{backfill,poll,reconcile}.py`: backfill 2005→hiện tại khi thực sự có nguồn, poll 30 phút, dedup theo timestamp/hash, annual>monthly>realtime; file thiếu ghi log, không tạo dữ liệu giả.
- [ ] Fixtures/test sample gồm: available years, thiếu 2005–2006, duplicate, correction, header old/current, NOAA rate limit, retry.

## TL04-02 — GCS + Silver processing

- [ ] Code `packages/gcs/raw/{client,paths}.py`: lưu bytes NOAA gốc vào private bucket path bất biến, SHA-256/generation, upload `if_generation_match=0`, xử lý tình huống upload thành công nhưng DB thất bại.
- [ ] Code `apps/preprocessing/app/{quality,features,jobs}/`: chuẩn hóa 14 fields, kiểm tra thiếu/trùng/domain; source priority; tính daily features theo `Pacific/Honolulu`; mean direction circular, coverage chỉ khi cadence có căn cứ.
- [ ] Worker command/API nội bộ: poll/backfill/rebuild-days, nhưng phản hồi job thật/đăng ký status qua adapter; local fake chạy được khi không có TL01.
- [ ] Tạo `apps/ingestion/Dockerfile`, `apps/preprocessing/Dockerfile` độc lập; không sửa root Compose của TL01.

## TL04-03 — Test và bàn giao

- [ ] `pytest` fixtures: parse đúng WVHT/DPD/WTMP; `MM` và sentinel; HST crossing midnight; idempotent 2 lần; corrected source tạo revision proposal; GCS 412/retry.
- [ ] Demo local: sample NOAA bytes → fake bucket + fake gateway → canonical Silver + daily features, không cần API người khác.
- [ ] Tạo `docs/contributions/TL04_STATUS.md` và `docs/contributions/TL04_FUNCTIONS.md` với kết quả unit/integration fake và những kết nối cloud chưa test.

**Bàn giao:** 2 workers, NOAA parser/client, GCS raw adapter, fixtures, tests, 2 Dockerfiles, hai báo cáo.

**Nghiệm thu TL04:** chạy lại một input không nhân số đo; source lỗi có log; dữ liệu HST đúng; luôn phân biệt real NOAA với fixture; retry sau partial failure an toàn.

**Không sở hữu:** PostgreSQL DDL/Database API, Analysis/ML service, Frontend.
