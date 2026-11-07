# Deploy Backend to Compute Engine

**Public HTTPS API:** `https://apiwaimea.liems.io.vn`  
**Cloud SQL:** PostgreSQL private IP  
**Storage:** private GCS raw, model and backup buckets.

## Provisioning order
1. GCP project / APIs / service accounts / billing budget alerts.
2. VPC, subnet, Private Services Access, firewall and backend regional static external IP.
3. Cloud Storage buckets, scoped IAM.
4. Cloud SQL PostgreSQL instance with private IP, automated backups and PITR; create DB/user.
5. Ubuntu Compute Engine VM attached to the VPC and workload service account.
6. Docker Engine + Compose plugin; clone backend repo onto VM.
7. `cp .env.production.example .env.production`, fill DB URL, Cloud SQL connection name and bucket names; protect file with chmod 600.
8. `bash deploy/scripts/deploy.sh`. `docker compose ... ps`. Then apply SQL migration using a trusted PostgreSQL client through Cloud SQL Auth Proxy.
9. Host Nginx: use `deploy/nginx/apiwaimea.conf`, DNS A to static VM IP; issue TLS with Certbot after HTTP DNS works.
10. Verify `https://apiwaimea.liems.io.vn/health`, frontend CORS, then wire Ingestion -> Silver -> Gold after coding pipelines.

## Notes
- Compose `postgres-local` uses profile **devdb**; do not run it in production. The production compose overlay adds a Cloud SQL Auth Proxy container.
- All worker services are **health endpoint skeletons**, not an implemented pipeline. A running container != finished data mining service.
- `dbwaimea.liems.io.vn` may be configured as **private** DNS for direct private DB access, but an Auth Proxy connection uses service name `cloud-sql-proxy:5432`, not the DNS alias.
- Google Cloud Firewall does not control Cloud Run's ingress; frontend runs in a separate project service.
- `docker compose down -v` deletes volumes in dev; it does not delete a managed Cloud SQL database.
- Security hardening, job authentication, rate limiting, monitoring, backups and rollbacks required before public production deployment.
