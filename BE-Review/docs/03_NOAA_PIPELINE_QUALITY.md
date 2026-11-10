# 03 — NOAA Data Ingestion, incremental, QC và Data Contracts

## 1. Nguồn và quy tắc ưu tiên

Trạm duy nhất `51201`; luồng `historical_annual`, `historical_monthly`, `realtime` đều thuộc NOAA NDBC Standard Meteorological. Dựa theo **notebook NOAA hiện có**:

```text
annual:   https://www.ndbc.noaa.gov/data/historical/stdmet/51201hYYYY.txt.gz
monthly:  https://www.ndbc.noaa.gov/data/stdmet/{Jan|Feb|...}/51201{M}{YYYY}.txt.gz
realtime: kiểm tra URL nguồn trong notebook 30 phút trước khi chốt API client
```

- Bắt đầu tìm kiếm từ 2005 nhưng chỉ lưu các năm/record NOAA thực sự cung cấp. Không lấp dữ liệu 2005/2006 bằng bản ghi giả.
- Annual thường được xem là authoritative cho năm đã đóng, monthly có ưu tiên tiếp theo, realtime là tạm thời. Tuy nhiên, **ưu tiên file không tự chứng minh tất cả giá trị đều hợp lệ**: QC tốt hơn, timestamp, provenance, parser version đều phải xem xét trước khi thay canonical row.
- `NOAA 51201` là **observed significant wave height (`WVHT`)**; không phải API dự báo tương lai.

## 2. Chu trình backfill lịch sử

1. Admin gọi `POST /internal/v1/ingestion/backfill` với `start_year: 2005`, `end_year` không vượt năm hiện tại.
2. Tạo ingestion_run; lập các annual tasks. Với năm hiện tại ưu tiên monthly/realtime; không đợi annual chưa xuất bản.
3. GET source kèm timeout/limited retries/backoff; HTTP 404 -> `unavailable` từng period, không dừng toàn bộ backfill.
4. Tính SHA-256 byte gốc; tạo `gs://bucket/noaa/raw/historical/YYYY/<sha>-51201hYYYY.txt.gz`.
5. Upload GCS `if_generation_match=0`; nếu 412 `PreconditionFailed`, kiểm tra object tồn tại và checksum, sau đó dùng object cũ nếu đúng hash.
6. POST metadata source_file vào Database API và parse raw lines; nếu parse lỗi, ghi file state `partial/failed` nhưng không xóa GCS object.
7. Batch upsert Bronze/raw rồi Silver, write run counters; tháng/ngày bị ảnh hưởng queue lại preprocessing / analytics.
8. Ghi checkpoint đúng cấp **(feed, period, source content hash)**, để chạy lại không tải vô hạn.

## 3. Poll realtime mỗi 30 phút

1. Tải một vùng overlap 2–7 ngày (config) vì NOAA có thể bổ sung/sửa bản ghi cũ.
2. Checkpoint theo `observed_at` **không đủ** nếu NOAA sửa một timestamp đã tải; phải so hash của giá trị.
3. Số liệu không đổi → `duplicate_rows`; timestamp mới → inserted; timestamp cũ nhưng khác → cân nhắc revision với source priority.
4. Nếu NOAA chưa có dữ liệu mới, run `success` với `inserted_rows=0`, không tạo records giả.
5. Nếu lỗi 429/5xx → backoff; 4xx do URL sai → log và fail rõ ràng, không liên tục hammer NOAA.
6. Bảo vệ trùng job qua `idempotency_key`, advisory lock/checkpoint; luôn có retry an toàn.

## 4. Quy ước parse Standard Meteorological

Notebook đang dùng các trường:

```text
WDIR WSPD GST WVHT DPD APD MWD PRES ATMP WTMP DEWP VIS PTDY TIDE
```

| NOAA | Silver column | Đơn vị | Quy tắc quan trọng |
|---|---|---|---|
| WDIR | wind_direction_deg | degree true | 0–360; sentinel 999 -> NULL |
| WSPD | wind_speed_ms | m/s | >=0; 99 -> NULL khi đúng tài liệu/header |
| GST | wind_gust_ms | m/s | >=0; 99 -> NULL |
| WVHT | wave_height_m | m | >=0; 99 -> NULL; output hồi quy mặc định |
| DPD | dominant_period_s | s | >0; 99 -> NULL |
| APD | average_period_s | s | >0; 99 -> NULL |
| MWD | mean_wave_direction_deg | degree true | 0–360; circular mean, sentinel 999 -> NULL |
| PRES/BAR | sea_level_pressure_hpa | hPa | >0; 9999 -> NULL |
| ATMP | air_temperature_c | °C | có thể âm; 999 -> NULL |
| WTMP | sea_surface_temp_c | °C | có thể thiếu; phân biệt 99/999 theo nguồn |
| DEWP | dew_point_c | °C | 999 -> NULL |
| VIS | visibility_nmi | nautical mile | NOAA có thể ghi mi/nmi tùy header, chuyển khi cần |
| PTDY | pressure_tendency_hpa | hPa/3h | có thể âm; thường thiếu trong historical |
| TIDE | tide_ft | ft relative MLLW | có thể âm |

**Header-driven parser là bắt buộc**: dòng `#` có thể biểu thị header với mã cũ, số cột khác nhau, đơn vị; không giả định mọi file năm 2005–nay cùng header. Trường cột không tồn tại → `NULL`, không tạo giá trị đo giả. Mã sentinel phải xét **theo trường** và ngữ cảnh; giá trị hợp lệ cần kiểm tra vật lý bằng unit tests.

## 5. Quality gates tại Silver và Gold

| Gate | Silver | Gold |
|---|---|---|
| Station/UTC timestamp | bắt buộc | kế thừa |
| Chính sách duplicate / source priority | bắt buộc | kế thừa |
| Các cột numeric parse / sentinel -> NULL | bắt buộc | kế thừa |
| Domain checks (wave>=0, direction 0..360) | status/issue | có thể loại khỏi metric tương ứng |
| Missing WSPD khi có WVHT | vẫn lưu Silver với NULL | feature filter tùy model |
| Sample coverage theo ngày | ghi sample_count, valid_count, expected nếu hợp lệ | công bố statistic theo chính sách coverage |
| Vùng thời gian phân tích | giữ UTC instant | ngày HST và cutoff rõ |
| Reproducibility | parser version, source hash | processing version, run ID/model ID |

**Không có ngưỡng coverage chính thức do thầy/NOAA áp đặt cho đồ án.** Gợi ý policy để đánh giá: `valid_wave_count >= 12` và `coverage_ratio >= 0.8` **nếu** biết chính xác `expected_sample_count`; nếu không biết cadence thì không tính ratio và chỉ dùng min-count + gap diagnostics; threshold là cấu hình phiên bản (`features_v1`), không hard-code trong nhiều file. Đặc trưng Silver có thể lưu dù chưa đạt gate; việc training/publication cần filter theo contract.

## 6. Tạo daily features đúng theo Hawaii

- Thời điểm quan trắc lưu UTC aware; `date_hst = (observed_at AT TIME ZONE 'Pacific/Honolulu')::date`.
- Aggregation daily độc lập với ngày UTC: một đêm Hawaii có thể qua ngày UTC khác.
- Tính median, mean, min/max, p95 WVHT, stddev, mean DPD, mean SST, max wind; **mean MWD bằng atan2(mean(sin angle), mean(cos angle))**, xử lý trường hợp vector length≈0 → NULL.
- Khi nguồn năm cũ được cập nhật: chỉ rebuild **các ngày HST bị ảnh hưởng**, rồi window tuần/tháng tương ứng và đầu vào evaluation/prediction bị ảnh hưởng nếu cần; không xóa cả bảng.
- `sample_count` không đồng nhất `valid_wave_count`; metric phải hiển thị sample_count và `data_completeness` để tránh đánh lừa.

## 7. Idempotency / transaction boundary

GCS và Cloud SQL không có distributed transaction chung. Thực hiện pattern *saga / replayable state*:

```text
DISCOVERED
  -> DOWNLOADED
  -> GCS_UPLOADED (uri + generation + sha)
  -> SOURCE_FILE_REGISTERED (bronze.source_files)
  -> PARSED (bronze.raw_observations)
  -> SILVER_APPLIED (upsert + revisions)
  -> GOLD_INVALIDATED/REBUILT
```

Trong lần retry, từng bước `get-or-create` hoặc `ON CONFLICT` xác nhận cùng checksum; tuyệt đối không trộn một file khác cùng đường dẫn. Không đánh dấu run hoàn thành nếu đã upload GCS nhưng metadata chưa được lưu. Có bước reconciliation định kỳ tìm orphan/partial states.

## 8. Ví dụ mã giả cần hiện thực (không phải code đã chạy)

```python
async def ingest_source(feed, period):
    raw_bytes = await noaa_client.download(feed.url(period))
    source_hash = sha256(raw_bytes).hexdigest()
    object_info = gcs.upload_if_absent(content=raw_bytes, key=immutable_path(feed, period, source_hash))
    file_id = await database_api.register_file(feed, period, source_hash, object_info)
    for batch in parse_stdmet_batches(raw_bytes):
        await database_api.upsert_raw_and_silver(file_id, batch)  # transactional per batch
    await database_api.finish_file(file_id)
    await orchestrator.enqueue_rebuild_for_changed_days()
```

## 9. Bảng test phải có

- Header `#YY`/`#YYYY`; BAR/PRES; có/không có PTDY; năm 2-digit.
- NOAA 2005 hoặc 2006 không có file: không tạo 0 wave hoặc rows giả.
- Trùng row cùng file và trùng timestamp giữa feeds.
- Annual sửa dữ liệu realtime; annual không được bị realtime stale ghi đè.
- Fault injection: HTTP timeout, GCS 412, GCS 503, DB 503 giữa upload và register, kill/restart giữa batch.
- Boundary HST 23:59→00:00, direction 359°/1°, missing codes vs valid 99 where ambiguous.
