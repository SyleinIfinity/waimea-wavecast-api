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
9. Confirm a restorable Cloud SQL backup. Apply additive migrations `04_tl07b_quality_gold.sql` through `10_tl08a_publications.sql` in numeric order with `psql -v ON_ERROR_STOP=1` before deploying images. `sudo bash deploy/scripts/deploy.sh` validates Compose, builds Database API, Ingestion, Preprocessing and the scheduler, starts them, then checks readiness.
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
- TL07 onward follows the production-first validation decision in `docs/implementation-roadmap.md`: test code before deployment, then verify actual Cloud SQL, GCS and API results. Bulk backfill needs an explicit task authorization and impact review; TL08-A's request supplied that authorization. Destructive migrations, deletion, IAM/firewall changes and VM reboot require separate approval.

## TL08-A historical backfill and GCS data lake

`historical-backfill` is a one-off Compose profile with a 0.5 CPU and 1.5 GiB
memory limit. Its only source inventory is the live NOAA 51201 station-history
listing. It imports one listed archive at a time; Raw/Silver DB writes are
chunked into 500 rows and checkpointed in `bronze.historical_backfill_jobs`.
It runs QC, HST Daily Features and six-resolution descriptive Gold before
writing immutable monthly GCS exports. The export manifest is
`gold.dataset_exports`. Only rows whose GCS generation, compressed SHA-256,
decompressed content and second DB read agree become active `VERIFIED` exports.

Before a bulk run, take and validate a PostgreSQL 17 custom-format backup.
The TL08-A schema addition is `database/migrations/09_tl08a_exports.sql`; it
creates the job and export manifest tables without changing existing rows.
Migration `10_tl08a_publications.sql` tracks pending year and coverage
publication across failed runs and VM reboots.
After the migration and Database API deployment, run one archive first:

```bash
sudo docker compose --env-file .env.production -f compose.yaml -f deploy/compose.prod.yaml --profile historical run --rm --no-deps historical-backfill python -m services.ingestion.scripts.historical_backfill --year 2004
```

The same command with `--all` processes all archives currently listed by
NOAA, preserving completed jobs if an individual archive fails. Run it again
to resume. `--year YYYY` and `--month M` target one source. `--force-check`
downloads the listed source and compares its SHA-256 even if the last metadata
check is recent. A verified unchanged
source is skipped. Scheduled single-source runs check recent archives first.
The defaults are `HISTORY_METADATA_CHECK_HOURS=24`,
`HISTORY_FULL_CHECK_DAYS=30` and `HISTORY_MAX_FILES_PER_RUN=1`. Old archives
are periodically downloaded for SHA-256 reconciliation even when metadata is
unchanged. Do not schedule `--all` every 30 minutes.

Inspect progress through the internal `GET /internal/v1/datalake/jobs` route
with the protected `X-Internal-Token`, or query
`bronze.historical_backfill_jobs` from Cloud SQL. `last_batch_offset`,
`error_stage`, GCS source URI/generation and the last exported partition show
the recovery point. A failed or killed worker can be run again; a five-minute
lease with heartbeat prevents parallel claims. Stop only the one-off
historical container to pause the backfill; do not stop the four production
services or the VM. The container checks available VM memory and disk before
each source/partition.

Run the read-only/recovery reconciliation and publish immutable yearly GCS
manifests after import:

```bash
sudo docker compose --env-file .env.production -f compose.yaml -f deploy/compose.prod.yaml --profile historical run --rm --no-deps historical-backfill python -m services.ingestion.scripts.publish_history_manifest --all --repair --rebuild-gold
```

`--repair` recreates missing or stale monthly exports from PostgreSQL with
content-derived object names; it never overwrites original NOAA source
objects. The publisher independently downloads each pinned GCS generation,
compares its SHA-256 and canonical contents with current DB rows, then writes
an immutable manifest under `gs://waimea-noaa/manifests/station=51201/historical/`.
It publishes all Silver months plus next January's Silver before Gold so HST
December and other cross-month Gold dependencies reference the final Silver
version for that pass.
`--rebuild-gold` runs one post-import adaptive-cadence Gold rebuild per year
before exporting. Early annual archives with mostly hourly data would
otherwise appear artificially incomplete under a fixed 30-minute denominator.
Use the internal `GET /internal/v1/datalake/coverage` route for per-month
Raw/Silver/Gold status and active export versions. If verification fails,
resolve the source or DB/GCS mismatch and rerun the publisher. Never mark a
manifest verified merely because an upload returned success.

For later archive reconciliation, install
`deploy/systemd/waimea-history-reconcile.service` and `.timer` under
`/etc/systemd/system/`, run `sudo systemctl daemon-reload` and
`sudo systemctl enable --now waimea-history-reconcile.timer`. The timer starts
one bounded source reconciliation each day at about 03:15 UTC, independently
of the 30-minute realtime scheduler (about 10:15 Vietnam time).
`Persistent=true` runs a missed check
after reboot. Inspect `systemctl status waimea-history-reconcile.timer` and
`journalctl -u waimea-history-reconcile.service`. Keep the main
`waimea-wavecast-api.service` enabled so the four core containers return
after reboot. ML model training and prediction stay disabled.
The service runs `scheduled_history_reconcile`: it checks at most one source
requiring a download, publishes at most two pending years, then uploads a
versioned coverage report to `manifests/station=51201/coverage/{sha256}.json`.
`gold.history_publications` stores the verified manifest and coverage URI,
SHA-256 and generation. An error leaves the year `PENDING` for the next run.
Use the private `GET /internal/v1/datalake/publications` API to inspect state.

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

## TL08-B model training checkpoint

The `historical-backfill` Compose profile also provides a bounded one-off ML
training container (`0.5` CPU, `1536m` memory). It runs explicitly, never as
part of `waimea-wavecast-api.service`. TL08-B models are stored as DRAFT with
private GCS artifacts and Cloud SQL registry rows. Database API promotion
requires an explicit READY quality gate and an existing successful test
evaluation. Verify `/health/live`, `/health/ready`, and the registry after
any Database API rollout. Do not enable `prediction`, `classification`, or
`clustering` worker services while all model readiness statuses are NOT_READY.
The evidence, holdout values and current blockers are in the TL08-B and TL08-C
contribution reports. The existing Compose/systemd core stack restores after
reboot; no TL08-C inference timer has been installed.
- Security hardening, job authentication, rate limiting, monitoring, backups and rollbacks required before public production deployment.
