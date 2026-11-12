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
9. `bash deploy/scripts/deploy.sh`; it validates Compose, starts services, and checks `/health/ready`. Apply SQL migrations only when separately planned and approved.
10. Host Nginx: use `deploy/nginx/apiwaimea.conf`, DNS A to static VM IP; issue TLS with Certbot after HTTP DNS works.
11. Verify `https://apiwaimea.liems.io.vn/health/ready`, frontend CORS, then wire Ingestion -> Silver -> Gold after coding pipelines.

## Notes
- Compose `postgres-local` uses profile **devdb**; do not run it in production. The production overlay passes `.env.production` to each service; Database API connects directly over TLS.
- All worker services are **health endpoint skeletons**, not an implemented pipeline. A running container != finished data mining service.
- `dbwaimea.liems.io.vn` must resolve to an address reachable from the VM. Check DNS, routing, firewall and Cloud SQL network authorization for that address. `DB_SSLMODE=require` encrypts the connection; use a stricter verification mode and trusted CA if certificate identity verification is required.
- Google Cloud Firewall does not control Cloud Run's ingress; frontend runs in a separate project service.
- `docker compose down -v` deletes volumes in dev; it does not delete a managed Cloud SQL database.
- VM containers use Application Default Credentials from the attached Compute Engine service account through the metadata server. Do not set `GOOGLE_APPLICATION_CREDENTIALS` or mount a JSON key. Check access from inside a container, not with a personal `gcloud` login.
- The tracked systemd unit is `deploy/systemd/waimea-wavecast-api.service`. Install it as `/etc/systemd/system/waimea-wavecast-api.service`, run `sudo systemctl daemon-reload` and `sudo systemctl enable waimea-wavecast-api.service`. It starts the Database API with the production Compose overlay after VM reboot; other workers remain managed by the deployment script.
- Security hardening, job authentication, rate limiting, monitoring, backups and rollbacks required before public production deployment.
