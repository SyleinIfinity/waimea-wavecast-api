# 07 — Docker, Nginx, Google Cloud Deployment (VM Compute Engine)

## 1. Mục tiêu mạng

```text
Internet → :443 Nginx → database-api:8000 → Cloud SQL Auth Proxy:5432 → Cloud SQL
                                 └─ internal API ⇄ worker services
Workers (ingestion/training/prediction) → NOAA/GCS (HTTPS outbound)
Frontend Cloud Run (separate repo) → HTTPS domain apiwaimea.liems.io.vn
```

- Chỉ publish `80` + `443` ở VM firewall; SSH riêng theo key và CIDR; tuyệt đối không mở 8000/5432 của containers trên public interface.
- Docker network dành cho services **phải cho outbound HTTPS**: ingestion cần NOAA, GCS; Cloud SQL Proxy cần Google APIs. Network không publish port đủ để bảo vệ ingress, nhưng **không** chọn `internal: true` nếu không có egress proxy khác.
- Nginx phải enforce TLS trước khi có traffic real; HTTP bootstrap chỉ để khởi tạo/kiểm thử.

## 2. File có sẵn trong gói blueprint

```text
Dockerfile
requirements.txt
compose.yaml                # HTTP bootstrap + 8 worker role stubs + cloud-sql-proxy
compose.prod.yaml           # HTTPS override khi certificate đã tồn tại
nginx/nginx.http.conf       # port 80
nginx/nginx.https.conf      # port 80 redirect, 443 TLS
.env.example                # không commit mật khẩu thật
app/main.py                # 200 /health/live; 503 /health/ready; business API chưa code
```

**Quan trọng:** Chỉ `app/main.py` tối giản đã chạy được. Chưa có `NOAA Client`, DB repository, scheduler jobs, training hay API business. Compose dùng một image với biến `SERVICE_NAME` để tạo 8 process khác nhau **phục vụ thử Docker networking**, chưa đáp ứng yêu cầu service độc lập về nghiệp vụ khi triển khai đầy đủ. Khi code xong thay build context/entrypoint cho từng service.

## 3. Trên VM Google Cloud

1. Dùng Ubuntu, cài Docker Engine/Compose plugin theo tài liệu Docker chính thức, không dùng Dockerfile từ nguồn không tin cậy.
2. Cloud SQL instance `waimea-postgres` có instance connection name `PROJECT:asia-southeast1:waimea-postgres` lấy từ Cloud SQL Overview (**đừng nhầm với IP public hoặc hostname dbwaimea**).
3. VM attached service account có **Cloud SQL Client** và quyền GCS cần thiết (ít nhất bucket-scoped Storage Object User cho upload + đọc, có thể tách quyền theo job khi production).
4. VM service account phải có đúng Compute Engine OAuth access scope hoặc cloud-platform scope; IAM grants không khắc phục scope bị giới hạn.
5. GCS bucket `waimea-noaa` trong README chỉ là tên dự kiến; thay tên thật trong `.env`.
6. PostgreSQL `waimea_app` có quyền SQL phù hợp; proxy không thay database username/password.
7. Tạo `.env` bằng `cp .env.example .env` và điền giá trị, `chmod 600 .env`. **Không commit `.env`.**
8. Trên VM, `docker compose config` xác thực YAML/interpolation, `docker compose up -d --build` để kiểm tra lớp network/process.
9. `curl -i http://127.0.0.1/health/live` kiểm tra chỉ process; `/health/ready` 503 đến khi dependencies được code.

## 4. TLS cho apiwaimea.liems.io.vn

- DNS A của `apiwaimea.liems.io.vn` trỏ về **IP public VM** (không phải IP Cloud SQL). Nếu dùng Cloudflare, cấu hình phù hợp với ACME issuance; `DNS only` đơn giản nhất cho HTTP-01.
- Để cấp cert lần đầu bằng Certbot standalone: tạm dừng Nginx container đang chiếm :80; bảo đảm Google Cloud firewall cho 80/443; chạy trên host VM:

```bash
# Trên Ubuntu host, cài certbot bằng phương pháp chính thức phù hợp distro.
# Chỉ ví dụ lệnh chạy khi certbot đã cài:
cd /path/to/waimea-wavecast-backend-blueprint
docker compose stop nginx
sudo certbot certonly --standalone \
  --preferred-challenges http \
  -d apiwaimea.liems.io.vn

# Đảm bảo fullchain.pem và privkey.pem có tại:
# /etc/letsencrypt/live/apiwaimea.liems.io.vn/
docker compose -f compose.yaml -f compose.prod.yaml up -d --build
```

- Nginx container mount `/etc/letsencrypt` read-only; kiểm tra `nginx -t`, `curl -I https://apiwaimea.liems.io.vn/health/live`.
- **Gia hạn chứng chỉ**: dùng certbot renew timer + post-renew hook `docker compose ... exec -T nginx nginx -s reload` (lưu ý path project và quyền gọi docker). Với **standalone** khi renewal, phải giải phóng port 80 hoặc chuyển sang webroot/DNS-01; đừng cấu hình timer giả định renew tự làm được khi Nginx đang giữ port 80.
- Không enable prod compose khi chưa có cert hợp lệ: Nginx sẽ lỗi start do file missing.
- Khi dùng HTTPS proxy, FastAPI ứng dụng cần tin `X-Forwarded-Proto` **chỉ từ proxy trusted**; không cho app trực tiếp exposed trên host.

## 5. Cloud SQL Auth Proxy

`compose.yaml` minh họa container `gcr.io/cloud-sql-connectors/cloud-sql-proxy:2.27.0`, dùng VM metadata credentials và port 5432 **chỉ trên Docker network**. Không map `5432:5432` ra host. Phương pháp này được Google Cloud khuyến nghị để kết nối Cloud SQL bằng TLS/IAM; database password vẫn cần.

- Cấu hình database-api URL qua `DB_HOST=cloud-sql-proxy`, `DB_NAME=waimea_wavecast`, `DB_USER=waimea_app`, DB_PASSWORD protected; SQLAlchemy connection pool nhỏ, liveness/readiness timeout.
- Nếu local Arch muốn test Compose thật, cần `gcloud auth application-default login` và truyền ADC vào **proxy container** bằng mount được bảo vệ; **không** nhét `gcloud` token/keys vào image/repo. Đơn giản hơn, chạy `cloud-sql-proxy` trên host local và connect backend dev qua localhost khi thử DB, tách local compose profile.
- Cloud SQL public IP có thể vẫn bật cho DBeaver (authorized networks/TLS). Container production không cần truy cập qua hostname `dbwaimea` khi đã dùng proxy.

## 6. Dockerfile khi triển khai thực tế

Bộ gói có Dockerfile tối giản để chạy scaffold. Khi chia services, có thể dùng một base image và nhiều targets độc lập, mỗi service có package-specific dependencies. Các yếu tố bắt buộc:

- `python:3.13-slim` hoặc base pin digest, user non-root, `.dockerignore`, build dependencies trước code, no secrets in Docker build, exec form CMD.
- Ingestion cần `httpx`, `google-cloud-storage`; database-api cần `fastapi`, `sqlalchemy`, `psycopg[binary]`/asyncpg, Pydantic; Training cần `scikit-learn`, pandas, numpy, joblib, pyarrow; analysis cần scipy/statsmodels; pin requirements/uv.lock.
- Mỗi image có `/health/live` + readiness dependencies thực tế. Không dùng `--reload` khi deploy.
- CPU/bộ nhớ phân bổ: train chạy one-off, không tranh CPU khi cần API phản hồi; tránh mở hàng chục Uvicorn workers đồng thời trên VM nhỏ.

## 7. Secret/IAM, logging và giới hạn mạng

- DB password qua protected `.env` trên VM hoặc Secret Manager; key `INTERNAL_API_TOKEN`, `ADMIN_API_TOKEN` phải khác nhau, random, không đưa vào browser.
- Thư viện GCS `storage.Client()` ADC; bucket private, Public Access Prevention + Uniform. Pin object generation khi download ML; upload with `if_generation_match=0`.
- Phân quyền bucket tối thiểu: ingestion write raw, training write models/datasets, inference read artifacts. Giai đoạn demo có thể dùng 1 VM service account với quyền bucket-scoped nếu ưu tiên đơn giản, nhưng giảm quyền khi triển khai lâu dài.
- CORS chỉ allow `https://waimea.liems.io.vn`. Nginx không bao giờ route `/internal/*`, `/docs` hoặc `openapi.json` công khai khi prod.
- Logs JSON stdout, docker restart, healthcheck, rotate Docker logs/metrics. DB backups, GCS soft delete/retention; kiểm thử restore.

## 8. Các kiểm tra sau deploy

```bash
docker compose config
docker compose ps
docker compose logs --tail=100 nginx database-api cloud-sql-proxy
curl -i http://127.0.0.1/health/live
curl -i https://apiwaimea.liems.io.vn/health/live
# Khi đã viết business API:
curl -i https://apiwaimea.liems.io.vn/api/v1/stations/51201
```

`/health/live` 200 không chứng minh DB/GCS đã được kết nối. Khi bắt đầu ingestion thực, **chỉ `/health/ready` được code kiểm tra DB/GCS mới là chứng cứ readiness**.

## 9. References và phiên bản

- FastAPI Containers: https://fastapi.tiangolo.com/deployment/docker/
- Cloud SQL Auth Proxy PostgreSQL: https://cloud.google.com/sql/docs/postgres/connect-auth-proxy
- GCS request preconditions: https://cloud.google.com/storage/docs/request-preconditions
- Nginx proxy module: https://nginx.org/en/docs/http/ngx_http_proxy_module.html
- Phiên bản Docker images/library cần pin/review lại trước production release; mẫu là scaffold minh họa, không phải BOM bảo mật đã audit.
