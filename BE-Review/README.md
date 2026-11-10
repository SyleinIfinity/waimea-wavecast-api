# Waimea WaveCast — Backend Blueprint v1.0

> **Tài liệu thiết kế + bộ khung hạ tầng, KHÔNG PHẢI backend đầy đủ tính năng.** Sản phẩm gồm hướng dẫn chi tiết để nhóm lập trình tự triển khai, Dockerfile/Compose/Nginx và một FastAPI scaffold chạy được với health check. Endpoint nghiệp vụ trong tài liệu là **API contract dự kiến**; chúng chưa được triển khai trong scaffold.

## Quyết định đã chốt

- Python **3.13** / FastAPI / PostgreSQL **17** (Cloud SQL) / GCS / scikit-learn.
- Nguồn **quan trắc duy nhất**: **NOAA NDBC 51201** qua NDBC Standard Meteorological (historical annual/monthly + realtime). Không nạp Open-Meteo/CDIP như nguồn độc lập.
- Backend repo riêng: `waimea-wavecast-backend`; frontend repo riêng.
- Ba schema hiện hữu: **bronze (6 bảng), silver (5 bảng), gold (14 bảng)** theo `Waimea_WaveCast_DB_GCS_v2.0/01_init_full.sql` (25 bảng).
- Phát hành API thông qua Nginx: `https://apiwaimea.liems.io.vn/api/v1`.
- GCS bucket cấu hình qua `GCS_BUCKET` (ví dụ `waimea-noaa`, **xác minh lại tên bucket đã tạo**).
- Lưu model trên GCS, metadata/metrics trên Cloud SQL; hoạt động inference KHÔNG train lại.
- Dữ liệu ngày theo `Pacific/Honolulu` (HST, UTC-10), timestamp lưu DB `TIMESTAMPTZ` UTC.

## Danh mục tài liệu

| Tệp | Nội dung |
|---|---|
| [`docs/01_REQUIREMENTS_ARCHITECTURE.md`](docs/01_REQUIREMENTS_ARCHITECTURE.md) | Phạm vi, sơ đồ luồng, quyết định kiến trúc, phân quyền |
| [`docs/02_IMPLEMENTATION_BACKLOG.md`](docs/02_IMPLEMENTATION_BACKLOG.md) | Từng service, **file/module cần code**, input/output, API nội bộ, acceptance criteria |
| [`docs/03_NOAA_PIPELINE_QUALITY.md`](docs/03_NOAA_PIPELINE_QUALITY.md) | Backfill, incremental, GCS, parsing NOAA, quality gates, Bronze/Silver/Gold |
| [`docs/04_MACHINE_LEARNING.md`](docs/04_MACHINE_LEARNING.md) | **Hồi quy**, baseline, feature lag, split, metrics, phân loại, K-means, artifacts |
| [`docs/05_API_CONTRACT.md`](docs/05_API_CONTRACT.md) | API public/nội bộ, JSON schema, HTTP status, quyền truy cập |
| [`docs/06_DATABASE_GCS_MAPPING.md`](docs/06_DATABASE_GCS_MAPPING.md) | Ánh xạ **25 bảng SQL v2.0** và thư mục GCS, giao dịch/retry |
| [`docs/07_DEPLOYMENT.md`](docs/07_DEPLOYMENT.md) | Dockerfile, Compose, Nginx, TLS, Cloud SQL proxy, IAM, vận hành |
| [`docs/08_TESTING_DEMO.md`](docs/08_TESTING_DEMO.md) | Test plan, observability, script demo kết quả mất → tái tạo |
| [`docs/09_ROADMAP.md`](docs/09_ROADMAP.md) | Thứ tự triển khai, ưu tiên, phân công 5 người, Definition of Done |
| [`docs/API_OPENAPI_DRAFT.yaml`](docs/API_OPENAPI_DRAFT.yaml) | **OpenAPI 3.1 dự kiến**, chưa triển khai; cho Frontend tạo mock/client |
| [`docs/10_VALIDATION_REPORT.md`](docs/10_VALIDATION_REPORT.md) | Đã kiểm tra những gì, phần nào **chưa chạy thật** |

## Cây thư mục đề xuất khi triển khai thực tế

```text
waimea-wavecast-backend/
├── apps/
│   ├── database_api/app/{main.py,api/v1/,api/internal/,repositories/,schemas/,services/}
│   ├── ingestion/app/{main.py,clients/,parser/,jobs/,services/}
│   ├── preprocessing/app/{main.py,quality/,features/,jobs/}
│   ├── analysis/app/{main.py,statistics/,timeseries/,correlation/}
│   ├── clustering/app/{main.py,inference/,jobs/}
│   ├── prediction/app/{main.py,inference/,jobs/}
│   ├── classification/app/{main.py,inference/,jobs/}
│   └── scheduler/app/{main.py,schedules/,locks/}
├── packages/
│   ├── common/{config,logging,http,exceptions,security,schemas}/
│   ├── noaa/{client,parser,normalization,checksum}/
│   ├── ml/{features,labels,artifacts,validation}/
│   └── gcs/{client,paths,manifest}/
├── training/{train_regression.py,train_classification.py,train_clustering.py,evaluate.py}
├── database/migrations/                    # Copy SQL v2.0 vào đây
├── tests/{unit,integration,contract,e2e}/
├── docs/
├── deploy/{nginx,compose.yaml,compose.prod.yaml,certbot/}
├── pyproject.toml
├── .env.example
└── README.md
```

**Lưu ý:** `apps/` ở trên là **mục tiêu triển khai**. Scaffold chạy thử trong gói này tối giản dưới `app/` và chỉ chứng minh FastAPI+Docker+Nginx liveness; chưa có các thư mục nghiệp vụ trong `apps/`.

## SQL reference kèm theo

Thư mục `database/schema_reference_v2/` chứa **bản sao** script SQL v2.0 đã chốt (full, upgrade, verify, dictionary) để đối chiếu khi code. Chỉ chạy **một trong full hoặc upgrade** theo trạng thái DB thật; không tự chạy SQL chỉ vì `docker compose up`.

## Chạy thử scaffold (không đụng Cloud SQL/GCS)

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-dev.txt
uvicorn app.main:app --host 127.0.0.1 --port 8000
curl http://127.0.0.1:8000/health/live
pytest -q
```

Chạy thử Docker với cấu hình **có Cloud SQL Proxy thực** cần `.env`, IAM của VM và Cloud SQL instance connection name. Xem `docs/07_DEPLOYMENT.md`; `docker compose up` không được xem là kết nối thành công nếu chưa cấp quyền và điền cấu hình. Có thể kiểm tra tĩnh bằng `docker compose config`.

## Cần đọc trước khi code

1. Các tệp SQL v2.0 thực tế là **source of truth cho tên bảng/cột**. Không suy diễn lại từ sơ đồ.
2. Hai notebook NOAA hiện có là **mẫu hành vi** cho parsing/backfill, không copy nguyên cell notebook vào production.
3. Không sử dụng dữ liệu tương lai làm đặc trưng dự đoán. Không tự gán tên nhãn `safe/danger` nếu chưa có định nghĩa.
4. Các jobs phải idempotent và có trạng thái có thể retry; GCS và SQL không cùng một transaction.
5. Chỉ xóa **các bảng pattern Gold được phép** khi demo; giữ model registry, training/evaluation và GCS artifacts.
