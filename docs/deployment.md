# Deploy Backend to Compute Engine

**Public HTTPS API:** `https://apiwaimea.liems.io.vn`  
**Cloud SQL:** PostgreSQL at `dbwaimea.liems.io.vn:5432`
**Storage:** one private GCS bucket, `gs://waimea-noaa/`, with object prefixes documented in `storage/gcs_layout.md`.

## Provisioning order
1. GCP project / APIs / service accounts / billing budget alerts.
2. VPC, subnet, Private Services Access, firewall and backend regional static external IP.
3. Cloud Storage bucket `waimea-noaa` in `asia-southeast1`, scoped IAM for the VM service account and public access prevention.
4. Cloud SQL PostgreSQL instance with private IP, automated backups and PITR; create DB/user.
5. Ubuntu Compute Engine VM attached to the VPC and workload service account.
6. Docker Engine + Compose plugin; clone backend repo onto VM.
7. `cp .env.production.example .env.production`, fill `DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER`, `DB_PASSWORD`, `DB_SSLMODE=require`; keep every `GCS_*_BUCKET` and `GCS_BUCKET` set to `waimea-noaa`. Protect the file with `chmod 600 .env.production`. Keep `DATABASE_API_URL=http://database-api:8000` for workers.
8. Confirm the VM resolves `dbwaimea.liems.io.vn` and can reach TCP 5432. Configure private routing or restricted authorized networks for the address actually used. Test a read-only PostgreSQL connection with TLS before deployment.
9. Confirm a restorable Cloud SQL backup. Apply additive migrations `04_tl07b_quality_gold.sql` through `08_tl07b_gold_signals.sql` in numeric order with `psql -v ON_ERROR_STOP=1` before deploying TL07-B images. `sudo bash deploy/scripts/deploy.sh` validates Compose, builds Database API, Ingestion, Preprocessing and the TL07-B scheduler, starts them, then checks their readiness.
10. Host Nginx: use `deploy/nginx/apiwaimea.conf`, DNS A to static VM IP; issue TLS with Certbot after HTTP DNS works.
11. Verify the configured public `/health/ready` endpoint and frontend CORS. Check the private `GET /internal/v1/pipeline/status` using `X-Internal-Token` from inside a container; it reports poll outcome, QC, gaps and Gold freshness. HTTP readiness alone does not prove recent NOAA data.

## Notes
- Compose `postgres-local` uses profile **devdb**; do not run it in production. The production overlay passes `.env.production` to each service; Database API connects directly over TLS.
- Ingestion has a real NOAA-to-GCS-to-Bronze/Silver pipeline when `INGESTION_ADAPTER_MODE=real`. Preprocessing performs QC and computes HST Daily Features and six-resolution descriptive Gold. `pipeline-scheduler` runs the durable 00/30 UTC polling loop and bounded retries. TL08 training, prediction and automatic imputation remain disabled.
- `dbwaimea.liems.io.vn` must resolve to an address reachable from the VM. Check DNS, routing, firewall and Cloud SQL network authorization for that address. `DB_SSLMODE=require` encrypts the connection; use a stricter verification mode and trusted CA if certificate identity verification is required.
- Google Cloud Firewall does not control Cloud Run's ingress; frontend runs in a separate project service.
- `docker compose down -v` deletes volumes in dev; it does not delete a managed Cloud SQL database.
- VM containers use Application Default Credentials from the attached Compute Engine service account through the metadata server. Do not set `GOOGLE_APPLICATION_CREDENTIALS` or mount a JSON key. Check access from inside a container, not with a personal `gcloud` login.
- The tracked systemd unit is `deploy/systemd/waimea-wavecast-api.service`. Install it as `/etc/systemd/system/waimea-wavecast-api.service`, run `sudo systemctl daemon-reload` and `sudo systemctl enable waimea-wavecast-api.service`. It starts Database API, Ingestion, Preprocessing and `pipeline-scheduler` with the production Compose overlay after VM reboot. The old `tl05-scheduler` profile must stay off; it invokes prediction workers.
- Keep `AUTO_IMPUTATION_ENABLED=false` and `NOAA_MAX_RETRIES=0` while the publication-delay and imputation policies are unapproved. One scheduler cycle makes at most ten separate NOAA fetches: one primary, up to five one-minute fast retries and four five-minute fallback retries before the next slot. The ingestion client honors `Retry-After` and reuses ETag/Last-Modified responses in process memory when offered by NOAA.
- To inspect state without exposing the token, run `sudo docker compose --env-file .env.production -f compose.yaml -f deploy/compose.prod.yaml exec -T pipeline-scheduler python -c 'import os,httpx; print(httpx.get(os.environ["DATABASE_API_URL"]+"/internal/v1/pipeline/status",headers={"X-Internal-Token":os.environ["INTERNAL_API_TOKEN"]}).json())'`. Avoid pasting the full output into public logs because cycle metadata may include operational details.
- If deployment fails, stop only `pipeline-scheduler`, restore the previous tagged images or Compose files and re-run health checks. Migrations 04 and 05 are additive; keep their tables/columns during image rollback. Migration 06 can be reverted with the prior view definition from `01_init_full.sql` after assessing whether unverified values should be visible. The pre-TL07-B `pg_dump` file is private and should be restored only into an isolated database for a recovery drill, not blindly over live production data.
- TL07 onward follows the production-first validation decision in `docs/implementation-roadmap.md`: test code before deployment, then verify actual Cloud SQL, GCS and API results. Require impact review and approval for bulk backfill, destructive migrations, deletion, IAM/firewall changes or VM reboot.

## Private Swagger UI on the current VM

Production Swagger UI is at `/private/docs` and its schema at
`/private/openapi.json`. The installed Nginx configuration allows these exact
paths only from VM loopback and requires HTTP Basic Auth. The public IP returns
403 for them, even with valid credentials. `/docs` remains disabled.

On your own computer, start an SSH tunnel and leave that terminal open:

```bash
ssh -L 8088:127.0.0.1:80 mcadmin@136.85.0.193
```

Open `http://localhost:8088/private/docs` in your browser. Enter the username
and password stored in the VM file
`/home/mcadmin/.config/waimea/swagger-initial-credentials.txt` (mode `0600`).
Read that file only over SSH and move the password to a password manager; do
not paste it into tickets, logs, or repository files. The Nginx password hash
is in `/etc/nginx/waimea-swagger.htpasswd`, which must remain readable by the
Nginx worker after reboot. The SSH tunnel encrypts the browser traffic between
your computer and VM; the loopback HTTP hop stays on the VM.

The tracked Nginx template is `deploy/nginx/apiwaimea.conf`. The current VM
uses `/etc/nginx/sites-enabled/waimea-wavecast`; its previous version is saved
at `/etc/nginx/waimea-wavecast.pre-swagger.bak`. Reinstall the two protected
locations from the tracked template if the host Nginx configuration is later
replaced. Do not open these Basic Auth routes on public HTTP.

## Local frontend connection

The current public API base URL is `http://136.85.0.193/api/v1`. A Vite frontend
running at `http://localhost:5173` can set
`VITE_API_BASE_URL=http://136.85.0.193/api/v1` and request
`${import.meta.env.VITE_API_BASE_URL}/observations/latest`. Production CORS
permits that exact localhost origin alongside `https://waimea.liems.io.vn`;
other origins are rejected. `ALLOWED_ORIGINS` is a comma-separated list in the
protected production environment file. Remove the localhost entry when local
browser development is no longer needed. A future HTTPS frontend needs an
HTTPS API URL to avoid browser mixed-content blocking.
- Security hardening, job authentication, rate limiting, monitoring, backups and rollbacks required before public production deployment.
