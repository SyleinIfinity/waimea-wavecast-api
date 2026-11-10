# TL04_FUNCTIONS.md — Danh sách chức năng đã hoàn thiện (Backend 1)

**Người phụ trách:** Komeme  
**Nhánh:** `feature/tl04-be-ingestion`

> Chỉ ghi "hoàn thiện" khi đã chạy test tương ứng thành công.

---

## packages/noaa/urls.py

| Function | Input | Output | Test |
|---|---|---|---|
| `annual_url(year, station_id)` | `year=2023` | `https://www.ndbc.noaa.gov/data/historical/stdmet/51201h2023.txt.gz` | `test_priority_hash_gcs.py` (via imports) |
| `monthly_url(year, month, station_id)` | `year=2023, month=6` | monthly URL | unit |
| `realtime_url(station_id)` | — | realtime URL | unit |

## packages/noaa/parser.py

| Function | Input | Output | Test |
|---|---|---|---|
| `detect_header(lines)` | list of text lines | `(columns, header_index)` | `test_noaa_parser.py::TestDetectHeader` |
| `parse_stdmet(raw_bytes, filename)` | raw bytes (gzip or plain) | `ParseResult(rows, columns, errors)` | `test_noaa_parser.py` — 10+ cases |
| `iter_stdmet_batches(raw_bytes, batch_size)` | raw bytes | generator of `list[ParsedRow]` | via `test_ingest_pipeline.py` |

**Evidence:** `test_noaa_parser.py` — old #YY header, new #YYYY header, BAR→PRES alias, all-MM row, short row, gzip decompression — all PASS

## packages/noaa/normalize.py

| Function | Input | Output | Test |
|---|---|---|---|
| `normalize_field(field, raw_value)` | `"WVHT", "99"` | `NormalizedMeasurement(value=None, is_sentinel=True)` | `test_noaa_normalize.py` — 20+ cases |
| `normalize_row(raw_values)` | dict of raw strings | dict of float\|None | `test_noaa_normalize.py::TestNormalizeRow` |
| `normalize_visibility(raw_value, header_unit)` | `"10.0", "mi"` | `8.68976` nmi | `test_noaa_normalize.py::TestVisibilityConversion` |

**Evidence:** MM sentinel, per-field numeric sentinels (99/999/9999), domain violations (wave<0, direction>360), negative PTDY/TIDE allowed — all PASS

## packages/noaa/hash.py

| Function | Input | Output | Test |
|---|---|---|---|
| `file_sha256(raw_bytes)` | bytes | 64-char hex string | `test_priority_hash_gcs.py::TestHash` |
| `measurement_sha256(station_id, observed_at, values)` | station, datetime, dict | 64-char hex | same |
| `row_sha256(raw_line)` | string | 64-char hex | same |

**Evidence:** stability and source_kind exclusion confirmed — PASS

## packages/noaa/priority.py

| Function | Input | Output | Test |
|---|---|---|---|
| `source_priority(source_kind)` | `"historical_annual"` | `3` | `test_priority_hash_gcs.py::TestSourcePriority` |
| `should_replace(existing, candidate, hash_a, hash_b)` | two source kinds + hashes | bool | 7 cases — PASS |
| `arbitrate(...)` | same | `(bool, reason_str)` | via `FakeDatabaseGateway` |

## packages/noaa/client.py

| Class/Method | Behaviour | Test |
|---|---|---|
| `NOAAClient.download(url)` | retry backoff, 404→SourceUnavailableError | mocked in integration tests |

## packages/gcs/paths.py

| Function | Output | Test |
|---|---|---|
| `annual_object_path(station_id, year, sha256)` | `noaa/raw/historical/{year}/{sha}-51201h{year}.txt.gz` | `test_priority_hash_gcs.py` (implicit) |
| `monthly_object_path(...)` | monthly path | unit |
| `realtime_object_path(...)` | realtime path | unit |
| `full_gcs_uri(bucket, path)` | `gs://{bucket}/{path}` | unit |

## packages/gcs/fake.py

| Method | Behaviour | Test |
|---|---|---|
| `upload_if_absent(bucket, path, data)` | if_generation_match=0 semantics; reuse on same hash | `test_priority_hash_gcs.py::TestFakeGCSClient` — 6 cases PASS |
| `download(bucket, path, generation)` | returns stored data; raises FileNotFoundError | same |
| `object_info(...)` | returns GCSObjectInfo or None | same |

## packages/db/gateway.py + fake.py

| Class/Method | Behaviour | Test |
|---|---|---|
| `FakeDatabaseGateway.register_source_file(dto)` | dedup by (station_id, sha256) | `test_ingest_pipeline.py` |
| `FakeDatabaseGateway.upsert_silver_batch(rows)` | source priority arbitration | `test_ingest_pipeline.py::TestSourcePriority` — PASS |
| `FakeDatabaseGateway.upsert_daily_features(rows)` | upsert by (station_id, date_hst) | `test_rebuild_days.py` |
| `FakeDatabaseGateway.get_observations(...)` | filter by station + UTC range | `test_rebuild_days.py` |

## services/ingestion/app/services/ingest_pipeline.py

| Function | Behaviour | Test |
|---|---|---|
| `run_ingest(feed_key, year, month, ...)` | SAGA: download→GCS→register→parse→raw upsert→silver upsert | `test_ingest_pipeline.py` — 9 cases PASS |

**Evidence:** idempotent (2× same file), 404 no fabricated data, annual blocks realtime, realtime upgraded by annual, MM sentinel → NULL silver row

## services/preprocessing/app/features/daily.py

| Function | Behaviour | Test |
|---|---|---|
| `compute_daily_features(date_hst, observations, ...)` | mean/median/max/p95/std, circular MWD, coverage_ratio=None | `test_daily_features.py` — 10+ cases PASS |
| `group_observations_by_hst_date(observations)` | correct HST midnight crossing | `test_daily_features.py::TestHSTMidnightCrossing` PASS |

## services/preprocessing/app/jobs/rebuild_days.py

| Function | Behaviour | Test |
|---|---|---|
| `rebuild_daily_features(start_hst, end_hst, db_gateway, ...)` | idempotent, empty day → skipped | `test_rebuild_days.py` — 4 cases PASS |

## FastAPI Endpoints

| Endpoint | Service | Status |
|---|---|---|
| `GET /health` | ingestion | ✅ implemented |
| `POST /internal/v1/ingestion/backfill` | ingestion | ✅ implemented (fake adapters) |
| `POST /internal/v1/ingestion/poll` | ingestion | ✅ implemented (fake adapters) |
| `POST /internal/v1/ingestion/reconcile` | ingestion | ✅ implemented (fake adapters) |
| `GET /health` | preprocessing | ✅ implemented |
| `POST /internal/v1/preprocessing/daily/rebuild` | preprocessing | ✅ implemented (fake adapters) |

---

## Chưa test với dữ liệu thực

- Kết nối Cloud SQL qua TL01 HTTP DatabaseGateway implementation
- Upload GCS thực (`RealGCSClient`) với bucket `waimea-noaa`
- NOAA network thực (mọi test dùng mock/fixture)
- Docker image build và compose integration (do TL01 ghép)
