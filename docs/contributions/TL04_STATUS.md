# TL04_STATUS.md — Báo cáo trạng thái bàn giao (Backend 1: Ingestion)

**Người phụ trách:** Komeme  
**Nhánh:** `feature/tl04-be-ingestion`  
**Cập nhật lần cuối:** 2026-11-10

---

## Tổng quan

| Hạng mục | Trạng thái |
|---|---|
| `packages/noaa/{urls,parser,normalize,hash,priority,client}.py` | ✅ PASS |
| `packages/gcs/{paths,client,fake}.py` | ✅ PASS |
| `packages/db/{gateway,fake}.py` | ✅ PASS |
| `packages/common/{time,errors}.py` | ✅ PASS |
| `services/ingestion/app/services/ingest_pipeline.py` | ✅ PASS |
| `services/ingestion/app/jobs/{backfill,poll,reconcile}.py` | ✅ PASS |
| `services/ingestion/app/main.py` (FastAPI) | ✅ PASS |
| `services/preprocessing/app/features/daily.py` | ✅ PASS |
| `services/preprocessing/app/quality/checks.py` | ✅ PASS |
| `services/preprocessing/app/jobs/rebuild_days.py` | ✅ PASS |
| `services/preprocessing/app/main.py` (FastAPI) | ✅ PASS |
| `services/ingestion/Dockerfile` | ✅ PASS |
| `services/preprocessing/Dockerfile` | ✅ PASS |
| `tests/unit/test_noaa_parser.py` | ✅ PASS (unit, no NOAA network) |
| `tests/unit/test_noaa_normalize.py` | ✅ PASS (unit) |
| `tests/unit/test_daily_features.py` | ✅ PASS (unit) |
| `tests/unit/test_priority_hash_gcs.py` | ✅ PASS (unit) |
| `tests/integration/test_ingest_pipeline.py` | ✅ PASS (fake GCS + fake DB) |
| `tests/integration/test_rebuild_days.py` | ✅ PASS (fake DB) |
| Kết nối Cloud SQL thực | ⚠️ NOT_STARTED (cần TL01 deploy + IAM) |
| Kết nối GCS thực | ⚠️ NOT_STARTED (cần `GCS_BUCKET` env + SA) |
| NOAA network thực (không mock) | ⚠️ NOT_STARTED (sandbox/CI chưa cấu hình) |

---

## Chi tiết theo checklist TL04-01

| Task | Trạng thái | File |
|---|---|---|
| NOAA client (timeout/retry/backoff) | ✅ PASS | `packages/noaa/client.py` |
| URL builder (annual/monthly/realtime) | ✅ PASS | `packages/noaa/urls.py` |
| Header-driven parser (#YY/#YYYY, BAR→PRES) | ✅ PASS | `packages/noaa/parser.py` |
| Sentinel → NULL per-field (14 fields) | ✅ PASS | `packages/noaa/normalize.py` |
| SHA-256 file hash + measurement hash | ✅ PASS | `packages/noaa/hash.py` |
| Source priority arbitration | ✅ PASS | `packages/noaa/priority.py` |
| Jobs: backfill 2005→present | ✅ PASS (fake) | `services/ingestion/app/jobs/backfill.py` |
| Jobs: poll realtime 30 min | ✅ PASS (fake) | `services/ingestion/app/jobs/poll.py` |
| Jobs: reconcile archive | ✅ PASS (fake) | `services/ingestion/app/jobs/reconcile.py` |

## Chi tiết theo checklist TL04-02

| Task | Trạng thái | File |
|---|---|---|
| GCS client (upload_if_absent, if_generation_match=0) | ✅ PASS | `packages/gcs/client.py` |
| GCS fake adapter (412-equivalent) | ✅ PASS | `packages/gcs/fake.py` |
| GCS immutable paths (content-addressed) | ✅ PASS | `packages/gcs/paths.py` |
| Silver preprocessing / quality gates | ✅ PASS | `services/preprocessing/app/quality/checks.py` |
| Daily features (HST, circular MWD, coverage=None khi không rõ cadence) | ✅ PASS | `services/preprocessing/app/features/daily.py` |
| DatabaseGateway protocol + FakeDatabaseGateway | ✅ PASS | `packages/db/{gateway,fake}.py` |
| Worker FastAPI endpoints (backfill/poll/rebuild) | ✅ PASS | `services/ingestion/app/main.py`, `services/preprocessing/app/main.py` |
| Ingestion Dockerfile | ✅ PASS | `services/ingestion/Dockerfile` |
| Preprocessing Dockerfile | ✅ PASS | `services/preprocessing/Dockerfile` |

---

## Blockers / Yêu cầu tích hợp

1. **TL01** cần cung cấp HTTP implementation của `DatabaseGateway` (hiện dùng `FakeDatabaseGateway`)
2. **TL01** cần cung cấp `feed_id` thực từ `bronze.source_feeds` (hiện để `feed_id=0` trong `SourceFileDTO`)
3. **GCS bucket name** cần xác nhận từ `GCS_BUCKET` env var trước khi switch sang `RealGCSClient`
4. **Cloud SQL IAM** (Cloud SQL Client role) cần cấp cho Service Account của VM trước khi kết nối
5. Không tự sửa `services/database_api/` hay `database/migrations/` — đó là phạm vi TL01

---

## Lệnh chạy test

```bash
# Từ thư mục gốc be/
cd d:\College\year4\khodulieu\be
python -m pytest tests/unit/ tests/integration/ -v
```

**Tất cả test chạy offline không cần Cloud SQL hay GCS thực.**
